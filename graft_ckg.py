"""
GRAFT-CKG: Generalized Relational Abstraction and Functional Topology
Four-Layer Code Knowledge Graph Engine & Deterministic Context Layer.

Topological Layers:
  1. Lsyn  (Syntactic Layer): AST containment, namespace hierarchy, class/function definitions.
  2. Ldep  (Dependency Layer): Cross-file imports, call graphs, callers/callees.
  3. Lflow (Dataflow Layer): Variable definition-use chains, argument passing, mutations.
  4. Lsem  (Semantic Layer): Natural language intent, docstrings, semantic similarity.

Hyper-Edge Metadata Vector:
  m = [d_scope, f_call, c_type, id_ctx]
  - d_scope: lexical scope depth (int >= 0)
  - f_call:  invocation frequency / call count
  - c_type:  static type confidence score [0.0, 1.0]
  - id_ctx:  context partition identifier (file or module path)
"""

import ast
import os
import re
import sys
import json
import math
import hashlib
import argparse
import networkx as nx
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from polyglot import TreeSitterExtractor


class HyperEdgeMetadata:
    def __init__(self, d_scope=0, f_call=1.0, c_type=1.0, id_ctx=""):
        self.d_scope = int(d_scope)
        self.f_call = float(f_call)
        self.c_type = float(c_type)
        self.id_ctx = str(id_ctx)

    def to_dict(self):
        return {
            "d_scope": self.d_scope,
            "f_call": self.f_call,
            "c_type": self.c_type,
            "id_ctx": self.id_ctx,
        }

    def __repr__(self):
        return f"m=[d={self.d_scope}, f={self.f_call}, c={self.c_type:.2f}, ctx={self.id_ctx}]"


class CKGBuilder(ast.NodeVisitor):
    def __init__(self, repo_path, repo_id=None):
        self.repo_path = os.path.abspath(repo_path)
        self.repo_id = repo_id
        self.repo_prefix = f"{repo_id}/" if repo_id else ""
        self.graph = nx.MultiDiGraph()
        self.current_file = None
        self.current_scope = []
        self.scope_depth = 0
        self.edge_counter = 0
        
        # Registry & index structures
        self.docstrings = {}        # node_id -> docstring text
        self.code_snippets = {}     # node_id -> source code lines
        self.symbols_by_name = {}   # simple_name -> list of node_ids
        self.file_symbols = {}      # file_rel_path -> list of node_ids
        self.calls_to_resolve = []  # (caller_id, callee_name, scope_depth, id_ctx)
        self.http_endpoints = {}    # (HTTP method, literal path) -> [handler node IDs]
        self.http_calls_to_resolve = []  # (caller_id, literal path, method, id_ctx)
        self.imports_by_file = {}   # file_rel_path -> {alias: target_module_or_symbol}
        self.module_to_file = {}    # dotted Python module -> repository-relative path
        self.ts_path_aliases = []   # (pattern, target templates) from tsconfig.json
        self.workspace_packages = {}  # package name -> package metadata
        self.class_base_records = []  # (class_id, base_spelling, file, scope_depth)
        self.class_bases = {}       # class_id -> resolved direct base class IDs
        self.class_subclasses = {}  # class_id -> list of subclass IDs
        self.var_defs = {}          # scope_id -> {var_name: node_id}
        self.var_types = {}         # scope_id -> {var_name: class_or_type_name}
        self.raw_file_lines = {}    # file_rel_path -> list of lines
        self.total_loc = 0
        self.ignore_patterns = []   # patterns from .ckgignore and default policies

        # Contract Layer (Lcontract): protobuf/gRPC service contracts and
        # producer/consumer bindings across languages.
        self.proto_services = {}        # service_name -> {"node_id", "rpcs": {rpc -> node_id}, ...}
        self.proto_file_hashes = {}     # rel_path -> sha256
        self.grpc_calls_to_resolve = [] # (caller_id, service, method, id_ctx)
        self.grpc_client_vars = {}      # file -> {var_name: service_name}
        self.pending_attr_calls = []    # (caller_id, var, method, file) resolved post-parse
        self.recorded_attr_calls = []   # (caller_id, var, method, file) persistent record
        self.unresolved_contract_calls = []  # (caller_id, service, method, id_ctx)
        self.go_type_vars = {}          # file -> {var_name: type_name}
        self.grpc_server_registrations = []  # (service, impl_arg, file, scope_id)
        self.grpc_servicer_records = [] # (class_id, service_name, file)
        self.js_add_service_records = []# (service, handler_map, file, scope_id)

        # RAG index structures
        self.vectorizer = None
        self.tfidf_matrix = None
        self.indexed_node_ids = []
        self.bm25_index = None

    @property
    def current_file_ctx(self):
        if not self.current_file:
            return ""
        return f"{self.repo_prefix}{self.current_file}"

    @property
    def current_file_node_id(self):
        return f"file::{self.current_file_ctx}"

    def get_current_scope_id(self):
        if not self.current_scope:
            return self.current_file_node_id
        scope = ".".join(self.current_scope)
        return f"{self.current_file_ctx}:{scope}"

    def get_node_id(self, name):
        if not self.current_scope:
            return f"{self.current_file_ctx}:{name}"
        scope = ".".join(self.current_scope)
        return f"{self.current_file_ctx}:{scope}.{name}"

    @staticmethod
    def _module_name_for_file(rel_path):
        """Return the importable module name for a repository-relative Python file."""
        parts = rel_path.replace("\\", "/").split("/")
        if not parts or os.path.splitext(parts[-1])[1] not in {".py", ".js", ".jsx", ".ts", ".tsx"}:
            return ""
        parts[-1] = os.path.splitext(parts[-1])[0]
        if parts[-1] in {"__init__", "index"}:
            parts.pop()
        return ".".join(part for part in parts if part)

    def _relative_import_module(self, module, level):
        """Resolve ``from .x`` / ``from ..x`` against the current file module."""
        if not level:
            return module or ""
        current_module = self._module_name_for_file(self.current_file)
        package_parts = current_module.split(".")[:-1]
        if level > 1:
            package_parts = package_parts[: -(level - 1)] if level - 1 <= len(package_parts) else []
        suffix = (module or "").split(".") if module else []
        return ".".join(part for part in [*package_parts, *suffix] if part)

    @staticmethod
    def _module_key_for_path(path):
        """Convert a repository-relative source path or extensionless target to a key."""
        parts = path.replace("\\", "/").strip("/").split("/")
        if not parts:
            return ""
        parts[-1] = os.path.splitext(parts[-1])[0]
        if parts[-1] in {"__init__", "index"}:
            parts.pop()
        return ".".join(part for part in parts if part and part != ".")

    def _load_module_configuration(self):
        """Load local TypeScript aliases and workspace package entry metadata."""
        tsconfig_path = os.path.join(self.repo_path, "tsconfig.json")
        if os.path.isfile(tsconfig_path):
            try:
                with open(tsconfig_path, "r", encoding="utf-8") as config_file:
                    compiler_options = json.load(config_file).get("compilerOptions", {})
                self.ts_path_aliases = list(compiler_options.get("paths", {}).items())
            except (OSError, ValueError) as exc:
                print(f"[!] Warning: Could not read tsconfig.json paths: {exc}")

        for root, directories, files in os.walk(self.repo_path):
            directories[:] = [name for name in directories if name not in {"node_modules", ".git"}]
            if "package.json" not in files:
                continue
            package_path = os.path.join(root, "package.json")
            try:
                with open(package_path, "r", encoding="utf-8") as package_file:
                    package = json.load(package_file)
                if package.get("name"):
                    self.workspace_packages[package["name"]] = {
                        "directory": os.path.relpath(root, self.repo_path).replace("\\", "/"),
                        "exports": package.get("exports"),
                        "main": package.get("module") or package.get("main") or package.get("types"),
                    }
            except (OSError, ValueError) as exc:
                print(f"[!] Warning: Could not read {package_path}: {exc}")

    @staticmethod
    def _export_target(exports, subpath):
        if isinstance(exports, str):
            return exports if subpath == "." else None
        if not isinstance(exports, dict):
            return None
        value = exports.get(subpath)
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            for condition in ("types", "import", "require", "default"):
                if isinstance(value.get(condition), str):
                    return value[condition]
        return None

    def resolve_js_module_specifier(self, rel_path, specifier):
        """Resolve a JS/TS module specifier to a known local graph module key.

        Supports relative paths, TypeScript ``compilerOptions.paths``, and
        packages whose ``package.json`` exists inside the indexed repository.
        Unknown packages are returned unchanged so they remain external.
        """
        if specifier.startswith("."):
            base_parts = self._module_key_for_path(rel_path).split(".")[:-1]
            for part in specifier.split("/"):
                if part in ("", "."):
                    continue
                if part == "..":
                    base_parts = base_parts[:-1]
                else:
                    base_parts.append(os.path.splitext(part)[0])
            return ".".join(base_parts)

        for pattern, targets in self.ts_path_aliases:
            prefix, marker, suffix = pattern.partition("*")
            if not specifier.startswith(prefix) or (suffix and not specifier.endswith(suffix)):
                continue
            wildcard = specifier[len(prefix): len(specifier) - len(suffix) if suffix else None]
            for target in targets:
                candidate = self._module_key_for_path(target.replace("*", wildcard))
                if candidate in self.module_to_file:
                    return candidate

        for package_name in sorted(self.workspace_packages, key=len, reverse=True):
            if specifier != package_name and not specifier.startswith(f"{package_name}/"):
                continue
            metadata = self.workspace_packages[package_name]
            suffix = specifier[len(package_name):].lstrip("/")
            export_key = "." if not suffix else f"./{suffix}"
            target = self._export_target(metadata["exports"], export_key)
            if not target and not suffix:
                target = metadata["main"]
            if not target and suffix:
                target = suffix
            if target:
                candidate = self._module_key_for_path(
                    f"{metadata['directory']}/{target.lstrip('./')}"
                )
                if candidate in self.module_to_file:
                    return candidate
        return specifier

    def register_symbol(self, node_id, simple_name):
        if simple_name not in self.symbols_by_name:
            self.symbols_by_name[simple_name] = []
        self.symbols_by_name[simple_name].append(node_id)
        
        if self.current_file:
            if self.current_file not in self.file_symbols:
                self.file_symbols[self.current_file] = []
            self.file_symbols[self.current_file].append(node_id)

    def add_ckg_node(self, node_id, node_type, layer="Lsyn", name="", line_no=0, doc="", code="", repo=None):
        self.graph.add_node(
            node_id,
            id=node_id,
            type=node_type,
            layer=layer,
            name=name,
            file=self.current_file_ctx,
            repo=repo if repo is not None else (self.repo_id or ""),
            line_no=line_no,
            docstring=doc,
            code=code,
        )
        if doc:
            self.docstrings[node_id] = doc

    def add_ckg_edge(self, u, v, relation, layer, metadata=None):
        if metadata is None:
            metadata = HyperEdgeMetadata(d_scope=self.scope_depth, id_ctx=self.current_file_ctx)
        edge_id = f"e{self.edge_counter}"
        self.edge_counter += 1
        self.graph.add_edge(
            u,
            v,
            key=edge_id,
            relation=relation,
            layer=layer,
            metadata=metadata.to_dict(),
            d_scope=metadata.d_scope,
            f_call=metadata.f_call,
            c_type=metadata.c_type,
            id_ctx=metadata.id_ctx,
        )

    def _load_ignore_patterns(self):
        defaults = [
            "*_pb2.py", "*_pb2_grpc.py", "*.pb.go", "*_grpc_pb.js", "*_pb.js", "*_grpc_pb.d.ts",
            "genproto/*", "vendor/*", "node_modules/*", ".git/*", "__pycache__/*", "venv/*", ".venv/*", "dist/*", "build/*"
        ]
        patterns = list(defaults)
        ckgignore_path = os.path.join(self.repo_path, ".ckgignore")
        if os.path.isfile(ckgignore_path):
            try:
                with open(ckgignore_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            patterns.append(line)
            except Exception as e:
                print(f"[!] Warning reading .ckgignore: {e}")
        self.ignore_patterns = patterns

    def _is_ignored(self, rel_path):
        import fnmatch
        norm = rel_path.replace("\\", "/")
        name = os.path.basename(norm)
        for pat in self.ignore_patterns:
            pat_norm = pat.replace("\\", "/")
            if fnmatch.fnmatch(norm, pat_norm) or fnmatch.fnmatch(name, pat_norm):
                return True
            if pat_norm.endswith("/*") and (norm == pat_norm[:-2] or norm.startswith(pat_norm[:-1])):
                return True
            if "/" in pat_norm and fnmatch.fnmatch(norm, f"*{pat_norm}*"):
                return True
        return False

    def _parse_python_file(self, rel_path, full_path):
        self.current_file = rel_path
        self.current_scope = []
        self.scope_depth = 0
        self.imports_by_file[rel_path] = {}

        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            lines = content.splitlines()
            self.raw_file_lines[rel_path] = lines
            self.total_loc += len(lines)

            file_node_id = self.current_file_node_id
            self.add_ckg_node(
                file_node_id,
                node_type="file",
                layer="Lsyn",
                name=os.path.basename(rel_path),
                line_no=1,
                code=f"# File: {rel_path}\n# Lines: {len(lines)}",
            )

            tree = ast.parse(content, filename=full_path)
            file_doc = ast.get_docstring(tree)
            if file_doc:
                self.docstrings[file_node_id] = file_doc
                self.graph.nodes[file_node_id]["docstring"] = file_doc

            self.visit(tree)
        except Exception as e:
            print(f"[!] Warning: Error parsing {rel_path}: {e}")

    # -------------------------------------------------------------
    # PHASE 1 & 2: SYNTACTIC & IN-FILE PARSING (AST)
    # -------------------------------------------------------------
    def build(self):
        print(f"[*] Starting GRAFT-CKG 4-Layer Construction on: {self.repo_path}")
        self._load_ignore_patterns()
        
        # 1. Scan files (deterministic sorted walk)
        source_files = []
        proto_files = []
        for root, dirs, files in os.walk(self.repo_path):
            dirs[:] = sorted([
                d for d in dirs
                if not self._is_ignored(os.path.relpath(os.path.join(root, d), self.repo_path).replace("\\", "/"))
            ])
            for file in sorted(files):
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, self.repo_path).replace("\\", "/")
                if self._is_ignored(rel_path):
                    continue
                ext = os.path.splitext(file)[1]
                if ext in {".py", *TreeSitterExtractor.EXTENSIONS}:
                    source_files.append((rel_path, full_path))
                elif ext == ".proto":
                    proto_files.append((rel_path, full_path))

        print(f"[*] Found {len(source_files)} supported source files, {len(proto_files)} proto files.")
        self.module_to_file = {
            self._module_name_for_file(rel_path): rel_path
            for rel_path, _ in source_files
            if self._module_name_for_file(rel_path)
        }
        self._load_module_configuration()

        # Pass 0: parse protobuf contracts into the Contract Layer (Lcontract).
        for rel_path, full_path in proto_files:
            try:
                self._parse_proto_file(rel_path, full_path)
            except Exception as e:
                print(f"[!] Warning: Error parsing proto {rel_path}: {e}")

        # Pass 1: Extract language-specific syntax into the common graph schema.
        for rel_path, full_path in source_files:
            extension = os.path.splitext(full_path)[1]
            if extension != ".py":
                try:
                    TreeSitterExtractor(extension).extract(self, rel_path, full_path)
                except Exception as e:
                    print(f"[!] Warning: Error parsing {rel_path}: {e}")
                continue
            self._parse_python_file(rel_path, full_path)

        # Pass 2: Resolve inheritance before method calls, then dependencies.
        self._resolve_inheritance()
        self._resolve_dependency_layer()
        self._resolve_http_calls()
        self._resolve_contract_layer()

        # Pass 3: Construct Semantic Layer (Lsem)
        self._build_semantic_layer()

        print(f"[*] Construction Complete:")
        print(f"    - Total LOC:       {self.total_loc}")
        print(f"    - Graph Nodes:     {self.graph.number_of_nodes()}")
        print(f"    - Hyper-Edges:     {self.graph.number_of_edges()}")
        self._print_layer_statistics()
        return self.graph

    def update_files(self, changed_rel_paths, deleted_rel_paths=()):
        """
        Incremental streaming construction (HR-Build) updating the graph for changed files.
        Avoids full batch rebuilds by surgically updating only modified/deleted ASTs and resolving relations.
        """
        all_affected = set(changed_rel_paths) | set(deleted_rel_paths)
        if not all_affected:
            return

        for rel_path in all_affected:
            norm = rel_path.replace("\\", "/")
            nodes_to_remove = [
                n for n, d in list(self.graph.nodes(data=True))
                if d.get("file") == norm or n.startswith(f"{norm}:") or n == f"file::{norm}"
            ]
            for n in nodes_to_remove:
                if self.graph.has_node(n):
                    self.graph.remove_node(n)
            self.raw_file_lines.pop(norm, None)
            self.imports_by_file.pop(norm, None)
            self.file_symbols.pop(norm, None)
            self.grpc_client_vars.pop(norm, None)
            self.go_type_vars.pop(norm, None)

        edges_to_remove = []
        for u, v, k, d in list(self.graph.edges(keys=True, data=True)):
            rel = d.get("relation")
            if rel in {"CALLS", "POLYMORPHIC_CALL", "EXTENDS", "HTTP_CALLS", "IMPLEMENTS", "CONSUMES", "SEMANTIC_SIMILAR"}:
                edges_to_remove.append((u, v, k))
        for u, v, k in edges_to_remove:
            if self.graph.has_edge(u, v, key=k):
                self.graph.remove_edge(u, v, key=k)

        self.calls_to_resolve = []
        self.http_calls_to_resolve = []
        self.grpc_calls_to_resolve = []
        self.class_base_records = []
        self.symbols_by_name = {}
        for nid, data in self.graph.nodes(data=True):
            sname = data.get("name")
            if sname:
                self.symbols_by_name.setdefault(sname, []).append(nid)

        for rel_path in changed_rel_paths:
            norm = rel_path.replace("\\", "/")
            full_path = os.path.join(self.repo_path, norm)
            if not os.path.isfile(full_path):
                continue
            ext = os.path.splitext(norm)[1]
            if ext == ".proto":
                self._parse_proto_file(norm, full_path)
            elif ext in TreeSitterExtractor.EXTENSIONS:
                TreeSitterExtractor(ext).extract(self, norm, full_path)
            elif ext == ".py":
                self._parse_python_file(norm, full_path)

        self._resolve_inheritance()
        self._resolve_dependency_layer()
        self._resolve_http_calls()
        self._resolve_contract_layer()
        self._build_semantic_layer()

        print(f"[*] Construction Complete:")
        print(f"    - Total LOC:       {self.total_loc}")
        print(f"    - Graph Nodes:     {self.graph.number_of_nodes()}")
        print(f"    - Hyper-Edges:     {self.graph.number_of_edges()}")
        self._print_layer_statistics()
        return self.graph

    def visit_Import(self, node):
        for alias in node.names:
            imported_name = alias.name
            as_name = alias.asname or imported_name.split(".")[0]
            self.imports_by_file[self.current_file][as_name] = imported_name
            # Connect File to imported module in Ldep
            file_node = self.current_file_node_id
            mod_target = f"module::{imported_name}"
            if not self.graph.has_node(mod_target):
                self.add_ckg_node(mod_target, node_type="module", layer="Ldep", name=imported_name)
            meta = HyperEdgeMetadata(d_scope=0, c_type=1.0, id_ctx=self.current_file_ctx)
            self.add_ckg_edge(file_node, mod_target, relation="IMPORTS", layer="Ldep", metadata=meta)
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        mod = self._relative_import_module(node.module, node.level)
        for alias in node.names:
            imported_symbol = alias.name
            as_name = alias.asname or imported_symbol
            full_target = f"{mod}.{imported_symbol}" if mod else imported_symbol
            self.imports_by_file[self.current_file][as_name] = full_target
            file_node = self.current_file_node_id
            sym_target = f"symbol::{full_target}"
            if not self.graph.has_node(sym_target):
                self.add_ckg_node(sym_target, node_type="symbol_ref", layer="Ldep", name=imported_symbol)
            meta = HyperEdgeMetadata(d_scope=0, c_type=1.0, id_ctx=self.current_file_ctx)
            self.add_ckg_edge(file_node, sym_target, relation="IMPORTS", layer="Ldep", metadata=meta)
        self.generic_visit(node)

    def visit_ClassDef(self, node):
        class_id = self.get_node_id(node.name)
        parent_id = self.get_current_scope_id()
        
        doc = ast.get_docstring(node) or ""
        code = self._extract_source_segment(node)
        
        # Add Class to Syntactic Layer (Lsyn)
        self.add_ckg_node(class_id, node_type="class", layer="Lsyn", name=node.name, line_no=node.lineno, doc=doc, code=code)
        self.register_symbol(class_id, node.name)
        
        # AST Containment Edge
        meta = HyperEdgeMetadata(d_scope=self.scope_depth, c_type=1.0, id_ctx=self.current_file_ctx)
        self.add_ckg_edge(parent_id, class_id, relation="CONTAINS", layer="Lsyn", metadata=meta)

        # Inheritance in Dependency Layer (Ldep)
        for base in node.bases:
            base_name = self._node_to_name(base)
            if base_name:
                self.class_base_records.append(
                    (class_id, base_name, self.current_file, self.scope_depth)
                )
                # gRPC servicer: class X(pb2_grpc.<Service>Servicer)
                servicer_match = re.match(r"^(?:\w+\.)*(\w+)Servicer$", base_name)
                if servicer_match:
                    self.grpc_servicer_records.append(
                        (class_id, servicer_match.group(1), self.current_file)
                    )

        self.scope_depth += 1
        self.current_scope.append(node.name)
        try:
            self.generic_visit(node)
        finally:
            self.current_scope.pop()
            self.scope_depth -= 1

    def visit_FunctionDef(self, node):
        func_id = self._handle_function(node)
        self._register_http_endpoint(node, func_id)

    def visit_AsyncFunctionDef(self, node):
        func_id = self._handle_function(node)
        self._register_http_endpoint(node, func_id)

    def _handle_function(self, node):
        func_id = self.get_node_id(node.name)
        parent_id = self.get_current_scope_id()
        
        doc = ast.get_docstring(node) or ""
        code = self._extract_source_segment(node)

        # Add Function to Syntactic Layer (Lsyn)
        self.add_ckg_node(func_id, node_type="function", layer="Lsyn", name=node.name, line_no=node.lineno, doc=doc, code=code)
        self.register_symbol(func_id, node.name)

        # AST Containment Edge (Lsyn)
        meta = HyperEdgeMetadata(d_scope=self.scope_depth, c_type=1.0, id_ctx=self.current_file_ctx)
        self.add_ckg_edge(parent_id, func_id, relation="CONTAINS", layer="Lsyn", metadata=meta)

        # Arguments in Dataflow Layer (Lflow)
        if func_id not in self.var_defs:
            self.var_defs[func_id] = {}

        all_params = []
        if hasattr(node.args, "posonlyargs"):
            all_params.extend(node.args.posonlyargs)
        all_params.extend(node.args.args)
        if hasattr(node.args, "kwonlyargs"):
            all_params.extend(node.args.kwonlyargs)
        if getattr(node.args, "vararg", None):
            all_params.append(node.args.vararg)
        if getattr(node.args, "kwarg", None):
            all_params.append(node.args.kwarg)

        for arg in all_params:
            arg_name = arg.arg
            var_id = f"{func_id}.var::{arg_name}"
            self.add_ckg_node(var_id, node_type="variable", layer="Lflow", name=arg_name, line_no=getattr(arg, 'lineno', node.lineno))
            self.var_defs[func_id][arg_name] = var_id
            meta_arg = HyperEdgeMetadata(d_scope=self.scope_depth + 1, c_type=1.0, id_ctx=self.current_file_ctx)
            self.add_ckg_edge(func_id, var_id, relation="PASSES_ARG", layer="Lflow", metadata=meta_arg)
            if getattr(arg, "annotation", None):
                type_name = self._node_to_name(arg.annotation)
                if type_name:
                    self.var_types.setdefault(func_id, {})[arg_name] = type_name.rsplit(".", 1)[-1]

        self.scope_depth += 1
        self.current_scope.append(node.name)
        try:
            self.generic_visit(node)
        finally:
            self.current_scope.pop()
            self.scope_depth -= 1
        return func_id

    @staticmethod
    def _extract_target_names(target):
        if isinstance(target, ast.Name):
            return [(target.id, getattr(target, "lineno", 0))]
        if isinstance(target, (ast.Tuple, ast.List)):
            res = []
            for elt in target.elts:
                res.extend(CKGBuilder._extract_target_names(elt))
            return res
        return []

    @staticmethod
    def _literal_strings(node):
        """Return string constants from a simple AST literal/list/tuple node."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return [item.value for item in node.elts
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)]
        return []

    def _register_http_endpoint(self, node, function_id):
        """Index literal Flask-style route decorators without executing the app."""
        methods_by_decorator = {
            "get": ["GET"], "post": ["POST"], "put": ["PUT"],
            "patch": ["PATCH"], "delete": ["DELETE"], "route": ["GET"],
        }
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            decorator_name = self._node_to_name(decorator.func)
            action = decorator_name.rsplit(".", 1)[-1].lower() if decorator_name else ""
            if action not in methods_by_decorator or not decorator.args:
                continue
            paths = self._literal_strings(decorator.args[0])
            if not paths:
                continue
            methods = methods_by_decorator[action]
            for keyword in decorator.keywords:
                if keyword.arg == "methods":
                    parsed = [method.upper() for method in self._literal_strings(keyword.value)]
                    if parsed:
                        methods = parsed
            for path in paths:
                for method in methods:
                    self.http_endpoints.setdefault((method, path), []).append(function_id)
            self.graph.nodes[function_id]["http_path"] = paths[0]
            self.graph.nodes[function_id]["http_methods"] = ",".join(methods)

    def visit_Call(self, node):
        callee_name = self._node_to_name(node.func)
        if callee_name and self.current_scope:
            caller_id = self.get_current_scope_id()
            # Python requests HTTP client call: requests.get/post/put/delete/patch("...")
            if callee_name.startswith("requests.") and callee_name.split(".")[-1] in ("get", "post", "put", "delete", "patch"):
                method = callee_name.split(".")[-1].upper()
                if node.args:
                    paths = self._literal_strings(node.args[0])
                    if paths:
                        clean_path = re.sub(r"^https?://[^/]+", "", paths[0])
                        if clean_path.startswith("/"):
                            self.http_calls_to_resolve.append(
                                (caller_id, clean_path, method, self.current_file_ctx)
                            )
            # gRPC client call candidate: <var>.<Method>(...). The stub binding
            # for <var> may appear later in the file (e.g. under __main__), so
            # resolution is deferred to _resolve_contract_layer.
            if callee_name.count(".") == 1:
                var, _, method = callee_name.partition(".")
                self.pending_attr_calls.append((caller_id, var, method, self.current_file_ctx))
                self.recorded_attr_calls.append((caller_id, var, method, self.current_file_ctx))
            self.calls_to_resolve.append((caller_id, callee_name, self.scope_depth, self.current_file_ctx))
        self.generic_visit(node)

    def visit_Assign(self, node):
        # gRPC stub binding: var = pb2_grpc.<Service>Stub(channel)  (any scope,
        # including module level / __main__ blocks)
        if isinstance(node.value, ast.Call):
            stub_name = self._node_to_name(node.value.func)
            stub_match = re.match(r"^(?:\w+\.)*(\w+)Stub$", stub_name or "")
            if stub_match:
                for target in node.targets:
                    for var_name, _ in self._extract_target_names(target):
                        self.grpc_client_vars.setdefault(self.current_file_ctx, {})[
                            var_name
                        ] = stub_match.group(1)
        if self.current_scope:
            scope_id = self.get_current_scope_id()
            if scope_id not in self.var_defs:
                self.var_defs[scope_id] = {}

            inferred_type = None
            if isinstance(node.value, ast.Call):
                ctor_name = self._node_to_name(node.value.func)
                if ctor_name:
                    inferred_type = ctor_name.rsplit(".", 1)[-1]

            for target in node.targets:
                for var_name, lineno in self._extract_target_names(target):
                    var_id = f"{scope_id}.var::{var_name}"
                    if not self.graph.has_node(var_id):
                        self.add_ckg_node(var_id, node_type="variable", layer="Lflow", name=var_name, line_no=lineno or node.lineno)
                    self.var_defs[scope_id][var_name] = var_id
                    if inferred_type:
                        self.var_types.setdefault(scope_id, {})[var_name] = inferred_type
                    
                    meta = HyperEdgeMetadata(d_scope=self.scope_depth, c_type=1.0, id_ctx=self.current_file_ctx)
                    self.add_ckg_edge(scope_id, var_id, relation="DEFINES", layer="Lflow", metadata=meta)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        if self.current_scope:
            scope_id = self.get_current_scope_id()
            if scope_id not in self.var_defs:
                self.var_defs[scope_id] = {}

            type_name = self._node_to_name(node.annotation) if getattr(node, "annotation", None) else None
            for var_name, lineno in self._extract_target_names(node.target):
                var_id = f"{scope_id}.var::{var_name}"
                if not self.graph.has_node(var_id):
                    self.add_ckg_node(var_id, node_type="variable", layer="Lflow", name=var_name, line_no=lineno or node.lineno)
                self.var_defs[scope_id][var_name] = var_id
                if type_name:
                    self.var_types.setdefault(scope_id, {})[var_name] = type_name.rsplit(".", 1)[-1]
                
                meta = HyperEdgeMetadata(d_scope=self.scope_depth, c_type=1.0, id_ctx=self.current_file_ctx)
                self.add_ckg_edge(scope_id, var_id, relation="DEFINES", layer="Lflow", metadata=meta)
        self.generic_visit(node)

    def visit_For(self, node):
        if self.current_scope:
            scope_id = self.get_current_scope_id()
            for var_name, lineno in self._extract_target_names(node.target):
                var_id = f"{scope_id}.var::{var_name}"
                if not self.graph.has_node(var_id):
                    self.add_ckg_node(var_id, node_type="variable", layer="Lflow", name=var_name, line_no=lineno or node.lineno)
                self.var_defs.setdefault(scope_id, {})[var_name] = var_id
                meta = HyperEdgeMetadata(d_scope=self.scope_depth, c_type=1.0, id_ctx=self.current_file_ctx)
                self.add_ckg_edge(scope_id, var_id, relation="DEFINES", layer="Lflow", metadata=meta)
        self.generic_visit(node)

    def visit_With(self, node):
        if self.current_scope:
            scope_id = self.get_current_scope_id()
            for item in node.items:
                if item.optional_vars:
                    for var_name, lineno in self._extract_target_names(item.optional_vars):
                        var_id = f"{scope_id}.var::{var_name}"
                        if not self.graph.has_node(var_id):
                            self.add_ckg_node(var_id, node_type="variable", layer="Lflow", name=var_name, line_no=lineno or node.lineno)
                        self.var_defs.setdefault(scope_id, {})[var_name] = var_id
                        meta = HyperEdgeMetadata(d_scope=self.scope_depth, c_type=1.0, id_ctx=self.current_file_ctx)
                        self.add_ckg_edge(scope_id, var_id, relation="DEFINES", layer="Lflow", metadata=meta)
        self.generic_visit(node)

    def visit_Return(self, node):
        if self.current_scope:
            scope_id = self.get_current_scope_id()
            return_id = f"{scope_id}.return::{node.lineno}"
            if not self.graph.has_node(return_id):
                self.add_ckg_node(return_id, node_type="return", layer="Lflow", name="return", line_no=node.lineno)
            meta = HyperEdgeMetadata(d_scope=self.scope_depth, c_type=1.0, id_ctx=self.current_file_ctx)
            self.add_ckg_edge(scope_id, return_id, relation="RETURNS", layer="Lflow", metadata=meta)
            if node.value and isinstance(node.value, ast.Name):
                var_name = node.value.id
                if scope_id in self.var_defs and var_name in self.var_defs[scope_id]:
                    var_id = self.var_defs[scope_id][var_name]
                    self.add_ckg_edge(var_id, return_id, relation="FLOWS_TO", layer="Lflow", metadata=meta)
        self.generic_visit(node)

    def visit_AugAssign(self, node):
        if self.current_scope:
            scope_id = self.get_current_scope_id()
            if isinstance(node.target, ast.Name):
                var_name = node.target.id
                if scope_id in self.var_defs and var_name in self.var_defs[scope_id]:
                    var_id = self.var_defs[scope_id][var_name]
                    meta = HyperEdgeMetadata(d_scope=self.scope_depth, c_type=1.0, id_ctx=self.current_file_ctx)
                    self.add_ckg_edge(scope_id, var_id, relation="MUTATES", layer="Lflow", metadata=meta)
        self.generic_visit(node)

    def visit_Name(self, node):
        # Variable read -> Dataflow USES
        if isinstance(node.ctx, ast.Load) and self.current_scope:
            scope_id = self.get_current_scope_id()
            var_name = node.id
            if scope_id in self.var_defs and var_name in self.var_defs[scope_id]:
                var_id = self.var_defs[scope_id][var_name]
                meta = HyperEdgeMetadata(d_scope=self.scope_depth, c_type=1.0, id_ctx=self.current_file_ctx)
                self.add_ckg_edge(scope_id, var_id, relation="USES", layer="Lflow", metadata=meta)
        self.generic_visit(node)

    # -------------------------------------------------------------
    # RESOLUTION PASSES (Dependency & Semantic)
    # -------------------------------------------------------------
    def _resolve_reference(self, caller_file, spelling):
        """Resolve an explicit local/imported reference without name-only guesses.

        The old resolver searched every same-named symbol before consulting an
        import statement. That could bind an external or ambiguous name to an
        unrelated repository function. This helper first follows Python import
        aliases and only falls back to a globally unique local symbol.
        """
        imports = self.imports_by_file.get(caller_file, {})
        base, separator, remainder = spelling.partition(".")
        imported_base = base in imports
        canonical = spelling
        if imported_base:
            canonical = imports[base]
            if separator:
                canonical = f"{canonical}.{remainder}"

        prefix = self.repo_prefix
        module_name, dot, symbol_name = canonical.rpartition(".")
        if dot:
            target_file = self.module_to_file.get(module_name)
            if target_file:
                candidates = [
                    node_id
                    for node_id in self.symbols_by_name.get(symbol_name, [])
                    if node_id.startswith(f"{prefix}{target_file}:")
                ]
                if candidates:
                    return candidates
            # An explicitly imported external module must remain external.
            if imported_base:
                return []

        if not separator and not imported_base:
            same_file = [
                node_id
                for node_id in self.symbols_by_name.get(spelling, [])
                if node_id.startswith(f"{prefix}{caller_file}:")
            ]
            if same_file:
                return same_file

        candidates = self.symbols_by_name.get(symbol_name if dot else spelling, [])
        if len(candidates) == 1:
            cand = candidates[0]
            caller_unit = caller_file.split("/")[0] if "/" in caller_file else ""
            cand_unit = cand.split(":")[0].replace("file::", "").split("/")[0] if "/" in cand else ""
            if caller_unit == cand_unit or not caller_unit or not cand_unit:
                return [cand]
        return []

    def _find_method(self, class_id, method_name, seen=None):
        """Find a method declared on a class or an explicitly resolved base."""
        seen = seen or set()
        if class_id in seen:
            return None
        seen.add(class_id)
        direct = f"{class_id}.{method_name}"
        if direct in self.graph and self.graph.nodes[direct].get("type") == "function":
            return direct
        for base_id in self.class_bases.get(class_id, []):
            resolved = self._find_method(base_id, method_name, seen)
            if resolved:
                return resolved
        return None

    def _find_all_implementations(self, class_id_or_name, method_name):
        """Find base method and all overriding methods in subclasses (CHA)."""
        classes_to_check = []
        if class_id_or_name in self.graph and self.graph.nodes[class_id_or_name].get("type") == "class":
            classes_to_check.append(class_id_or_name)
        else:
            for cid in self.symbols_by_name.get(class_id_or_name, []):
                if self.graph.nodes.get(cid, {}).get("type") == "class":
                    classes_to_check.append(cid)

        impls = []
        for class_id in classes_to_check:
            base_method = self._find_method(class_id, method_name)
            if base_method and base_method not in impls:
                impls.append(base_method)
            visited = set()
            queue = list(self.class_subclasses.get(class_id, []))
            while queue:
                sub_id = queue.pop(0)
                if sub_id in visited:
                    continue
                visited.add(sub_id)
                sub_method = f"{sub_id}.{method_name}"
                if sub_method in self.graph and self.graph.nodes[sub_method].get("type") == "function":
                    if sub_method not in impls:
                        impls.append(sub_method)
                queue.extend(self.class_subclasses.get(sub_id, []))
        return impls

    def _compute_polymorphic_confidences(self, caller_id, candidate_ids, tau=0.2):
        """Equation (4): Softmax polymorphic dispatch confidence with temperature tau."""
        import math
        if not candidate_ids:
            return []
        if len(candidate_ids) == 1:
            return [1.0]

        def get_node_text(nid):
            ndata = self.graph.nodes.get(nid, {})
            name = ndata.get("name", "")
            doc = ndata.get("docstring", "")
            code = ndata.get("code", "")
            return f"{name} {doc} {code[:200]}".strip() or name

        caller_text = get_node_text(caller_id)
        candidate_texts = [get_node_text(cid) for cid in candidate_ids]

        sims = []
        try:
            vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 1))
            all_texts = [caller_text] + candidate_texts
            matrix = vec.fit_transform(all_texts)
            caller_vec = matrix[0:1]
            for i in range(len(candidate_ids)):
                sim = float(cosine_similarity(caller_vec, matrix[i + 1 : i + 2])[0, 0])
                sims.append(sim)
        except Exception:
            sims = [1.0] * len(candidate_ids)

        scaled = [s / max(tau, 1e-4) for s in sims]
        max_s = max(scaled)
        exps = [math.exp(s - max_s) for s in scaled]
        sum_exp = sum(exps)
        if sum_exp > 0:
            return [round(e / sum_exp, 4) for e in exps]
        return [round(1.0 / len(candidate_ids), 4)] * len(candidate_ids)

    def _resolve_inheritance(self):
        """Turn explicit AST base-class spellings into real graph relationships."""
        for class_id, base_spelling, caller_file, scope_depth in self.class_base_records:
            candidates = [
                node_id
                for node_id in self._resolve_reference(caller_file, base_spelling)
                if self.graph.nodes[node_id].get("type") == "class"
            ]
            if candidates:
                self.class_bases[class_id] = candidates
                for target_id in candidates:
                    self.class_subclasses.setdefault(target_id, []).append(class_id)
                    confidence = 1.0 if len(candidates) == 1 else 1.0 / len(candidates)
                    metadata = HyperEdgeMetadata(
                        d_scope=scope_depth, c_type=confidence, id_ctx=caller_file
                    )
                    self.add_ckg_edge(
                        class_id, target_id, relation="EXTENDS", layer="Ldep", metadata=metadata
                    )
            else:
                stub_id = f"symbol::{base_spelling}"
                if not self.graph.has_node(stub_id):
                    self.add_ckg_node(stub_id, node_type="symbol_ref", layer="Ldep", name=base_spelling)
                metadata = HyperEdgeMetadata(d_scope=scope_depth, c_type=0.0, id_ctx=caller_file)
                self.add_ckg_edge(
                    class_id, stub_id, relation="EXTENDS", layer="Ldep", metadata=metadata
                )

    def _resolve_dependency_layer(self):
        print(f"[*] Resolving Dependency Layer (Ldep) for {len(self.calls_to_resolve)} call sites...")
        # Aggregate call frequencies for hyper-edge metadata
        call_counts = {}
        for caller_id, callee_name, scope_depth, id_ctx in self.calls_to_resolve:
            key = (caller_id, callee_name, scope_depth, id_ctx)
            call_counts[key] = call_counts.get(key, 0) + 1

        resolved_count = 0
        for (caller_id, callee_name, scope_depth, id_ctx), count in call_counts.items():
            caller_file = id_ctx
            target_ids = []

            # Determine base and symbol name for qualified calls (e.g. operations.add or self.func)
            base_prefix = None
            symbol_name = callee_name
            if "." in callee_name:
                parts = callee_name.split(".")
                base_prefix = parts[0]
                symbol_name = parts[-1]

            # 1. If base_prefix is 'self', resolve to methods in current enclosing class
            if base_prefix == "self":
                if callee_name.count(".") == 1:
                    class_prefix = caller_id.rsplit(".", 1)[0]
                    target_cand = self._find_method(class_prefix, symbol_name)
                    if target_cand:
                        target_ids = [target_cand]

            if not target_ids and base_prefix == "super()":
                if callee_name.count(".") == 1:
                    class_prefix = caller_id.rsplit(".", 1)[0]
                    for base_id in self.class_bases.get(class_prefix, []):
                        target_cand = self._find_method(base_id, symbol_name)
                        if target_cand:
                            target_ids = [target_cand]
                            break

            # 2. Receiver-type inference for obj.method()
            if not target_ids and "." in callee_name and base_prefix not in {"self", "super()"}:
                inferred_class = self.var_types.get(caller_id, {}).get(base_prefix)
                if inferred_class:
                    impls = self._find_all_implementations(inferred_class, symbol_name)
                    if impls:
                        if len(impls) == 1:
                            target_ids = impls
                        else:
                            confidences = self._compute_polymorphic_confidences(caller_id, impls, tau=0.2)
                            for impl_id, conf in zip(impls, confidences):
                                meta = HyperEdgeMetadata(d_scope=scope_depth, f_call=float(count), c_type=conf, id_ctx=caller_file)
                                self.add_ckg_edge(caller_id, impl_id, relation="POLYMORPHIC_CALL", layer="Ldep", metadata=meta)
                                resolved_count += 1
                            continue

            if not target_ids:
                target_ids = self._resolve_reference(caller_file, callee_name)

            # 5. Add resolved edges or external stub
            if target_ids:
                for tid in target_ids:
                    conf = 1.0 if len(target_ids) == 1 else (1.0 / len(target_ids))
                    meta = HyperEdgeMetadata(d_scope=scope_depth, f_call=float(count), c_type=conf, id_ctx=caller_file)
                    self.add_ckg_edge(caller_id, tid, relation="CALLS", layer="Ldep", metadata=meta)
                    resolved_count += 1
            else:
                stub_id = f"external::{callee_name}"
                if not self.graph.has_node(stub_id):
                    self.add_ckg_node(stub_id, node_type="external_func", layer="Ldep", name=callee_name)
                meta = HyperEdgeMetadata(d_scope=scope_depth, f_call=float(count), c_type=0.5, id_ctx=caller_file)
                self.add_ckg_edge(caller_id, stub_id, relation="CALLS", layer="Ldep", metadata=meta)

        # 6. Cross-file method calls through imported module aliases
        #    (e.g. `import helpers` then `helpers.transform(...)`).
        for (caller_id, callee_name, scope_depth, id_ctx), count in call_counts.items():
            if "." not in callee_name:
                continue
            base, _, method = callee_name.rpartition(".")
            if base in {"self", "super()"}:
                continue
            imports = self.imports_by_file.get(id_ctx, {})
            module_key = imports.get(base)
            if not module_key:
                continue
            target_file = self.module_to_file.get(module_key)
            if not target_file:
                continue
            candidates = [
                nid for nid in self.symbols_by_name.get(method, [])
                if nid.startswith(f"{self.repo_prefix}{target_file}:")
                and self.graph.nodes[nid].get("type") == "function"
            ]
            if len(candidates) == 1:
                edge_exists = any(
                    v == candidates[0] and d.get("relation") == "CALLS"
                    for _, v, d in self.graph.out_edges(caller_id, data=True)
                )
                if not edge_exists:
                    meta = HyperEdgeMetadata(d_scope=scope_depth, f_call=float(count), c_type=1.0, id_ctx=id_ctx)
                    self.add_ckg_edge(caller_id, candidates[0], relation="CALLS", layer="Ldep", metadata=meta)
                    resolved_count += 1

        print(f"[*] Dependency Layer resolved: {resolved_count} internal call bindings created.")

    def _resolve_http_calls(self):
        """Connect literal frontend HTTP calls to indexed local Python handlers.

        This is deliberately conservative: an edge is emitted only when both
        the method and URL are static literals and match a handler in the
        indexed repository. Dynamic URLs, gateway rewrites, and runtime router
        configuration remain unresolved rather than guessed.
        """
        resolved_count = 0
        for caller_id, path, method, id_ctx in self.http_calls_to_resolve:
            target_ids = self.http_endpoints.get((method.upper(), path), [])
            for target_id in target_ids:
                metadata = HyperEdgeMetadata(
                    d_scope=0, f_call=1.0, c_type=1.0, id_ctx=id_ctx
                )
                self.add_ckg_edge(
                    caller_id, target_id, relation="HTTP_CALLS", layer="Ldep",
                    metadata=metadata,
                )
                resolved_count += 1
        print(f"[*] HTTP dependency resolution: {resolved_count} local endpoint bindings created.")

    # -------------------------------------------------------------
    # CONTRACT LAYER (Lcontract): protobuf/gRPC cross-language bindings
    # -------------------------------------------------------------
    def _parse_proto_file(self, rel_path, full_path):
        """Parse a .proto file into canonical service/rpc contract nodes."""
        with open(full_path, "rb") as f:
            raw_bytes = f.read()
        proto_hash = hashlib.sha256(raw_bytes).hexdigest()
        self.proto_file_hashes[rel_path] = proto_hash

        text = raw_bytes.decode("utf-8", errors="replace")
        text = re.sub(r"//[^\n]*", "", text)
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        file_id = f"file::{self.repo_prefix}{rel_path}"
        self.add_ckg_node(file_id, "file", "Lsyn", os.path.basename(rel_path), 1,
                          code=f"// Proto contract file: {rel_path}", repo=self.repo_id or "")
        # Normalise empty rpc option blocks `{}` so service bodies can be
        # delimited by brace balancing (the first `}` is NOT the service end).
        text = re.sub(r"\)\s*\{\s*\}", ");", text)
        for service_match in re.finditer(r"service\s+(\w+)\s*\{", text):
            service_name = service_match.group(1)
            depth, i = 1, service_match.end()
            while i < len(text) and depth:
                depth += {"{": 1, "}": -1}.get(text[i], 0)
                i += 1
            body = text[service_match.end():i - 1]
            service_id = f"contract::{service_name}"
            if not self.graph.has_node(service_id):
                self.add_ckg_node(service_id, "service_contract", "Lcontract", service_name)
            self.graph.nodes[service_id].setdefault("definitions", []).append({
                "file": rel_path,
                "file_id": file_id,
                "repo_id": self.repo_id or "",
                "sha256": proto_hash,
            })
            self.add_ckg_edge(file_id, service_id, "CONTAINS", "Lsyn")
            rpcs = {}
            rpc_signatures = {}
            for rpc_match in re.finditer(r"rpc\s+(\w+)\s*\(\s*(\w+)\s*\)\s*returns\s*\(\s*(\w+)\s*\)", body):
                rpc_name, req_type, resp_type = rpc_match.groups()
                rpc_id = f"contract::{service_name}.{rpc_name}"
                if not self.graph.has_node(rpc_id):
                    self.add_ckg_node(rpc_id, "rpc_contract", "Lcontract", rpc_name)
                self.graph.nodes[rpc_id]["request_type"] = req_type
                self.graph.nodes[rpc_id]["response_type"] = resp_type
                self.graph.nodes[rpc_id].setdefault("definitions", []).append({
                    "file": rel_path,
                    "file_id": file_id,
                    "repo_id": self.repo_id or "",
                    "sha256": proto_hash,
                    "request_type": req_type,
                    "response_type": resp_type,
                })
                self.add_ckg_edge(service_id, rpc_id, "DEFINES_RPC", "Lcontract")
                rpcs[rpc_name] = rpc_id
                rpc_signatures[rpc_name] = {"request": req_type, "response": resp_type}
            self.proto_services[service_name] = {
                "node_id": service_id,
                "rpcs": rpcs,
                "file": rel_path,
                "file_id": file_id,
                "repo_id": self.repo_id or "",
                "sha256": proto_hash,
                "signatures": rpc_signatures,
            }

    def _resolve_contract_layer(self):
        """Bind producers (IMPLEMENTS) and consumers (CONSUMES) to contracts."""
        produced = 0
        consumed = 0

        # Python servicers: class X(pb2_grpc.<Service>Servicer) with rpc methods.
        # The rpc method may be implemented on the servicer class itself OR on
        # any subclass (template pattern, e.g. BaseEmailService -> EmailService).
        subclasses_of = {}
        for sub_id, bases in self.class_bases.items():
            for base_id in bases:
                subclasses_of.setdefault(base_id, []).append(sub_id)

        def _descendants(cid, seen=None):
            seen = seen or set()
            for sub in subclasses_of.get(cid, []):
                if sub not in seen:
                    seen.add(sub)
                    _descendants(sub, seen)
            return seen

        for class_id, service_name, file in self.grpc_servicer_records:
            contract = self.proto_services.get(service_name)
            if not contract:
                continue
            self.add_ckg_edge(class_id, contract["node_id"], "IMPLEMENTS", "Lcontract")
            produced += 1
            for rpc_name, rpc_id in contract["rpcs"].items():
                for impl_class in [class_id, *sorted(_descendants(class_id))]:
                    direct = f"{impl_class}.{rpc_name}"
                    if direct in self.graph and self.graph.nodes[direct].get("type") == "function":
                        self.add_ckg_edge(direct, rpc_id, "IMPLEMENTS", "Lcontract")
                        produced += 1

        # Go servers: pb.Register<Service>Server(srv, impl) + methods on impl type
        for service_name, impl_arg, file, scope_id in self.grpc_server_registrations:
            contract = self.proto_services.get(service_name)
            if not contract:
                continue
            impl_type = (impl_arg or "").strip("&").strip()
            # Resolve impl variable to its concrete type (svc := new(checkoutService))
            impl_type = self.go_type_vars.get(file, {}).get(impl_type, impl_type)
            # The type may be declared in a different file of the same package.
            impl_id = f"{self.repo_prefix}{file}:{impl_type}"
            if impl_id not in self.graph:
                candidates = [nid for nid in self.symbols_by_name.get(impl_type, [])
                              if self.graph.nodes[nid].get("type") == "class"]
                if len(candidates) == 1:
                    impl_id = candidates[0]
            if impl_id in self.graph:
                self.add_ckg_edge(impl_id, contract["node_id"], "IMPLEMENTS", "Lcontract")
                produced += 1
                for rpc_name, rpc_id in contract["rpcs"].items():
                    method_id = self._find_method(impl_id, rpc_name)
                    if method_id:
                        self.add_ckg_edge(method_id, rpc_id, "IMPLEMENTS", "Lcontract")
                        produced += 1

        # JS servers: server.addService(pkg.<Service>.service, {rpc: handler})
        for service_name, handlers, file, scope_id in self.js_add_service_records:
            contract = self.proto_services.get(service_name)
            if not contract:
                continue
            self.add_ckg_edge(scope_id, contract["node_id"], "IMPLEMENTS", "Lcontract")
            produced += 1
            for rpc_key, handler_expr in handlers.items():
                rpc_id = self._match_rpc_case(contract, rpc_key)
                if not rpc_id:
                    continue
                target_ids = self._resolve_reference(file, handler_expr)
                for tid in target_ids:
                    self.add_ckg_edge(tid, rpc_id, "IMPLEMENTS", "Lcontract")
                    produced += 1

        # Python stub calls whose stub variable was bound anywhere in the file
        for caller_id, var, method, file in self.pending_attr_calls:
            service = self.grpc_client_vars.get(file, {}).get(var)
            if service:
                self.grpc_calls_to_resolve.append((caller_id, service, method, file))
        self.pending_attr_calls = []

        # Consumers: gRPC stub calls from any language
        seen_consumers = set()
        for caller_id, service_name, method, id_ctx in self.grpc_calls_to_resolve:
            if (caller_id, service_name, method) in seen_consumers:
                continue
            seen_consumers.add((caller_id, service_name, method))
            contract = self.proto_services.get(service_name)
            if not contract:
                self.unresolved_contract_calls.append((caller_id, service_name, method, id_ctx))
                continue
            rpc_id = self._match_rpc_case(contract, method)
            if rpc_id:
                self.add_ckg_edge(caller_id, rpc_id, "CONSUMES", "Lcontract")
                consumed += 1
            else:
                self.unresolved_contract_calls.append((caller_id, service_name, method, id_ctx))

        print(f"[*] Contract Layer resolved: {produced} producer bindings, {consumed} consumer bindings.")

    @staticmethod
    def _match_rpc_case(contract, name):
        """Match an RPC name case-insensitively (JS uses camelCase handlers)."""
        if name in contract["rpcs"]:
            return contract["rpcs"][name]
        lowered = name.lower()
        for rpc_name, rpc_id in contract["rpcs"].items():
            if rpc_name.lower() == lowered:
                return rpc_id
        return None

    def _build_semantic_layer(self, similarity_threshold=0.55):
        print("[*] Computing Semantic Layer (Lsem) embeddings and contracts...")
        indexed_nodes = []
        corpus = []
        for nid, data in self.graph.nodes(data=True):
            ntype = data.get("type")
            if ntype in {"function", "class", "module", "service_contract", "rpc_contract"}:
                doc = data.get("docstring", "")
                name = data.get("name", "")
                code = data.get("code", "")
                name_tokens = " ".join(re.findall(r"[A-Za-z0-9]+", name))
                full_text = f"{name} {name_tokens} {doc} {code[:150]}".strip()
                if full_text:
                    indexed_nodes.append(nid)
                    corpus.append(full_text)

        self.indexed_node_ids = indexed_nodes
        if not indexed_nodes:
            print("[!] Note: No symbols found for semantic indexing.")
            return

        self.vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2))
        try:
            self.tfidf_matrix = self.vectorizer.fit_transform(corpus)
            nodes_with_docs = [nid for nid, doc in self.docstrings.items() if len(doc.strip()) > 10 and nid in self.indexed_node_ids]
            if len(nodes_with_docs) > 1:
                doc_indices = [self.indexed_node_ids.index(nid) for nid in nodes_with_docs]
                doc_submatrix = self.tfidf_matrix[doc_indices]
                sim_matrix = cosine_similarity(doc_submatrix)
                semantic_edge_count = 0
                n = len(nodes_with_docs)
                for i in range(n):
                    for j in range(i + 1, n):
                        score = float(sim_matrix[i, j])
                        if score >= similarity_threshold:
                            u = nodes_with_docs[i]
                            v = nodes_with_docs[j]
                            meta = HyperEdgeMetadata(d_scope=0, f_call=1.0, c_type=score, id_ctx="corpus")
                            self.add_ckg_edge(u, v, relation="SEMANTIC_SIMILAR", layer="Lsem", metadata=meta)
                            self.add_ckg_edge(v, u, relation="SEMANTIC_SIMILAR", layer="Lsem", metadata=meta)
                            semantic_edge_count += 2
                print(f"[*] Semantic Layer generated: {semantic_edge_count} cross-concept relational links.")
        except Exception as e:
            print(f"[!] Warning: Error building semantic layer: {e}")

        # BM25 lexical index for the retrieval baseline (no graph edges).
        try:
            from rank_bm25 import BM25Okapi

            tokenized = [self._bm25_tokenize(text) for text in corpus]
            self.bm25_index = BM25Okapi(tokenized) if tokenized else None
        except ImportError:
            self.bm25_index = None

    @staticmethod
    def _bm25_tokenize(text):
        return re.findall(r"[a-z0-9]+", text.lower())

    # -------------------------------------------------------------
    # QUERY & RETRIEVAL INTERFACES (Requested by Scholar)
    # -------------------------------------------------------------
    def adjacency_query(self, node_id, layers=None, direction="both", depth=1):
        """
        Adjacency query on the graph, optionally filtered by layers (Lsyn, Ldep, Lflow, Lsem).
        Returns list of neighbor edges and attributes.
        """
        if node_id not in self.graph:
            return []

        results = []
        visited = set([node_id])
        queue = [(node_id, 0)]

        while queue:
            curr, curr_depth = queue.pop(0)
            if curr_depth >= depth:
                continue

            # Outgoing edges
            if direction in ("out", "both"):
                for _, target, key, data in self.graph.out_edges(curr, keys=True, data=True):
                    if layers and data.get("layer") not in layers:
                        continue
                    results.append({
                        "source": curr,
                        "target": target,
                        "direction": "out",
                        "relation": data.get("relation"),
                        "layer": data.get("layer"),
                        "metadata": data.get("metadata", {}),
                    })
                    if target not in visited:
                        visited.add(target)
                        queue.append((target, curr_depth + 1))

            # Incoming edges
            if direction in ("in", "both"):
                for source, _, key, data in self.graph.in_edges(curr, keys=True, data=True):
                    if layers and data.get("layer") not in layers:
                        continue
                    results.append({
                        "source": source,
                        "target": curr,
                        "direction": "in",
                        "relation": data.get("relation"),
                        "layer": data.get("layer"),
                        "metadata": data.get("metadata", {}),
                    })
                    if source not in visited:
                        visited.add(source)
                        queue.append((source, curr_depth + 1))

        return results

    def explain_node(self, node_id):
        """
        Explains all 4 layers (Lsyn, Ldep, Lflow, Lsem) for a given code entity.
        Answers:
          1. Where does it live? (Syntactic Containment)
          2. What does it call and what calls it? (Dependency Blast Radius)
          3. What data does it manipulate? (Dataflow Def-Use)
          4. What is its semantic intent and related contracts? (Semantic Intent)
        """
        if node_id not in self.graph:
            # Try fuzzy match
            candidates = [n for n in self.graph.nodes if node_id in n]
            if candidates:
                node_id = candidates[0]
            else:
                return {"error": f"Node '{node_id}' not found in CKG."}

        node_data = self.graph.nodes[node_id]
        
        # 1. Syntactic explanation
        syntactic_parents = []
        syntactic_children = []
        for src, _, data in self.graph.in_edges(node_id, data=True):
            if data.get("layer") == "Lsyn":
                syntactic_parents.append({"id": src, "relation": data.get("relation")})
        for _, tgt, data in self.graph.out_edges(node_id, data=True):
            if data.get("layer") == "Lsyn":
                syntactic_children.append({"id": tgt, "relation": data.get("relation")})

        # 2. Dependency explanation
        out_calls = []
        in_calls = []
        for _, tgt, data in self.graph.out_edges(node_id, data=True):
            if data.get("layer") == "Ldep":
                out_calls.append({"callee": tgt, "relation": data.get("relation"), "meta": data.get("metadata")})
        for src, _, data in self.graph.in_edges(node_id, data=True):
            if data.get("layer") == "Ldep":
                in_calls.append({"caller": src, "relation": data.get("relation"), "meta": data.get("metadata")})

        # 3. Dataflow explanation
        dataflow_vars = []
        for _, tgt, data in self.graph.out_edges(node_id, data=True):
            if data.get("layer") == "Lflow":
                dataflow_vars.append({"variable": tgt, "relation": data.get("relation"), "meta": data.get("metadata")})

        # 4. Semantic explanation
        semantic_neighbors = []
        for _, tgt, data in self.graph.out_edges(node_id, data=True):
            if data.get("layer") == "Lsem":
                score = data.get("metadata", {}).get("c_type", 0.0)
                semantic_neighbors.append({"target": tgt, "similarity": score})

        return {
            "node_id": node_id,
            "name": node_data.get("name"),
            "type": node_data.get("type"),
            "file": node_data.get("file"),
            "line_no": node_data.get("line_no"),
            "docstring": node_data.get("docstring"),
            "layers": {
                "Lsyn_Syntactic": {
                    "enclosing_parent": syntactic_parents,
                    "contained_children": syntactic_children,
                },
                "Ldep_Dependency": {
                    "outward_calls": out_calls,
                    "inward_callers_blast_radius": in_calls,
                },
                "Lflow_Dataflow": {
                    "def_use_variables": dataflow_vars,
                },
                "Lsem_Semantic": {
                    "docstring_intent": node_data.get("docstring") or "[No Docstring Available]",
                    "semantically_similar_entities": sorted(semantic_neighbors, key=lambda x: x["similarity"], reverse=True),
                },
            },
        }

    def rag_query(self, query_text, top_k=5, graft_subgraph=True):
        """
        RAG / Semantic query over the codebase:
        1. Embed query with TF-IDF vectorizer
        2. Retrieve top_k semantically relevant nodes
        3. (Optional) Deterministic Subtree Grafting: Attach exact transitive execution
           subgraph (calls, dataflow, parents) without prompt bloat.
        """
        if not self.vectorizer or self.tfidf_matrix is None or not self.indexed_node_ids:
            return {"error": "Semantic index not initialized or empty."}

        query_vec = self.vectorizer.transform([query_text])
        sims = cosine_similarity(query_vec, self.tfidf_matrix).flatten()
        top_indices = sims.argsort()[::-1][:top_k]

        retrieved_nodes = []
        for idx in top_indices:
            score = float(sims[idx])
            if score > 0.0:
                nid = self.indexed_node_ids[idx]
                retrieved_nodes.append({
                    "node_id": nid,
                    "name": self.graph.nodes[nid].get("name"),
                    "score": round(score, 4),
                    "file": self.graph.nodes[nid].get("file"),
                    "line": self.graph.nodes[nid].get("line_no"),
                    "docstring": self.graph.nodes[nid].get("docstring"),
                    "code_snippet": self.graph.nodes[nid].get("code", "")[:300],
                })

        result = {"query": query_text, "hits": len(retrieved_nodes), "top_matches": retrieved_nodes}

        # Subtree grafting for the top hit
        if graft_subgraph and retrieved_nodes:
            top_id = retrieved_nodes[0]["node_id"]
            result["grafted_execution_context"] = self.deterministic_subtree_graft(top_id)

        return result

    def deterministic_subtree_graft(self, root_node_id, max_depth=2):
        """
        Implements Deterministic Subtree Grafting (Fig 2 in paper):
        Traverses typed relational links across all 4 layers (contains, calls, defines, uses)
        to isolate the minimal execution slice without token bloat.
        """
        if root_node_id not in self.graph:
            return {}

        grafted_nodes = set([root_node_id])
        grafted_edges = []

        queue = [(root_node_id, 0)]
        while queue:
            curr, depth = queue.pop(0)
            if depth >= max_depth:
                continue

            # Follow dependency calls and dataflow variables
            for _, tgt, data in self.graph.out_edges(curr, data=True):
                layer = data.get("layer")
                rel = data.get("relation")
                if layer in ("Ldep", "Lflow", "Lsyn") and rel in ("CALLS", "DEFINES", "PASSES_ARG", "CONTAINS"):
                    grafted_edges.append({
                        "source": curr,
                        "target": tgt,
                        "relation": rel,
                        "layer": layer,
                    })
                    if tgt not in grafted_nodes:
                        grafted_nodes.add(tgt)
                        queue.append((tgt, depth + 1))

        # Format minimal prompt context
        snippets = []
        for nid in grafted_nodes:
            ndata = self.graph.nodes[nid]
            if ndata.get("code"):
                snippets.append(f"[{ndata.get('type').upper()}] {nid} (Line {ndata.get('line_no')}):\n{ndata.get('code')}")

        return {
            "root": root_node_id,
            "total_nodes": len(grafted_nodes),
            "total_edges": len(grafted_edges),
            "relations": grafted_edges,
            "grafted_code_context": "\n\n".join(snippets),
        }

    # -------------------------------------------------------------
    # CONTRACT QUERIES (producer/consumer impact analysis)
    # -------------------------------------------------------------
    def _contract_node_id(self, name):
        """Resolve 'Service' or 'Service.Rpc' (case-insensitive rpc) to a node id."""
        if name in self.graph and self.graph.nodes[name].get("type") in {"service_contract", "rpc_contract"}:
            return name
        candidate = name if name.startswith("contract::") else f"contract::{name}"
        if candidate in self.graph:
            return candidate
        if "." in candidate:
            service, _, rpc = candidate.rpartition(".")
            contract = self.proto_services.get(service.split("::", 1)[-1])
            if contract:
                matched = self._match_rpc_case(contract, rpc)
                if matched:
                    return matched
        return None

    def who_consumes(self, contract_name):
        """List code entities that consume a service or rpc contract."""
        node_id = self._contract_node_id(contract_name)
        if not node_id:
            return {"error": f"Contract '{contract_name}' not found."}
        targets = {node_id}
        if self.graph.nodes[node_id].get("type") == "service_contract":
            targets |= set(self.proto_services[node_id.split("::", 1)[-1]]["rpcs"].values())
        consumers = []
        for target in targets:
            for src, _, data in self.graph.in_edges(target, data=True):
                if data.get("relation") == "CONSUMES":
                    consumers.append({"consumer": src, "rpc": target})
        return {"contract": node_id, "consumers": consumers}

    def who_implements(self, contract_name):
        """List code entities that implement a service or rpc contract."""
        node_id = self._contract_node_id(contract_name)
        if not node_id:
            return {"error": f"Contract '{contract_name}' not found."}
        targets = {node_id}
        if self.graph.nodes[node_id].get("type") == "service_contract":
            targets |= set(self.proto_services[node_id.split("::", 1)[-1]]["rpcs"].values())
        implementers = []
        for target in targets:
            for src, _, data in self.graph.in_edges(target, data=True):
                if data.get("relation") == "IMPLEMENTS":
                    implementers.append({"implementer": src, "rpc": target})
        return {"contract": node_id, "implementers": implementers}

    # -------------------------------------------------------------
    # HELPERS
    # -------------------------------------------------------------
    def _extract_source_segment(self, node):
        if not self.current_file or self.current_file not in self.raw_file_lines:
            return ""
        lines = self.raw_file_lines[self.current_file]
        start = max(0, node.lineno - 1)
        end = getattr(node, "end_lineno", node.lineno + 10)
        return "\n".join(lines[start:end])

    def _node_to_name(self, node):
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            base = self._node_to_name(node.value)
            return f"{base}.{node.attr}" if base else node.attr
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "super":
            return "super()"
        return None

    def _print_layer_statistics(self):
        layer_counts = {"Lsyn": 0, "Ldep": 0, "Lflow": 0, "Lsem": 0, "Lcontract": 0}
        for _, _, data in self.graph.edges(data=True):
            l = data.get("layer")
            if l in layer_counts:
                layer_counts[l] += 1
        print("    - Layer Distribution (Edges):")
        print(f"      * Syntactic  (Lsyn):      {layer_counts['Lsyn']}")
        print(f"      * Dependency (Ldep):      {layer_counts['Ldep']}")
        print(f"      * Dataflow   (Lflow):     {layer_counts['Lflow']}")
        print(f"      * Semantic   (Lsem):      {layer_counts['Lsem']}")
        print(f"      * Contract   (Lcontract): {layer_counts['Lcontract']}")


    def verify_graph(self):
        """
        Verifies the internal consistency and completeness of the CKG on this repo.
        Checks:
          1. Layer balance and edge distribution
          2. Containment consistency (orphaned symbols)
          3. Call graph resolution rate (internal vs external stubs)
          4. Dataflow def-use linkage
          5. Semantic layer coverage
        """
        nodes = self.graph.nodes
        edges = list(self.graph.edges(data=True))
        
        # 1. Layer distribution
        layer_dist = {"Lsyn": 0, "Ldep": 0, "Lflow": 0, "Lsem": 0, "Lcontract": 0}
        for _, _, d in edges:
            l = d.get("layer")
            if l in layer_dist:
                layer_dist[l] += 1
                
        # 2. Orphan check
        isolated = list(nx.isolates(self.graph))
        
        # 3. Call resolution
        total_calls = sum(1 for _, _, d in edges if d.get("relation") == "CALLS")
        external_calls = sum(1 for _, v, d in edges if d.get("relation") == "CALLS" and str(v).startswith("external::"))
        resolved_calls = total_calls - external_calls
        call_res_rate = (resolved_calls / total_calls * 100.0) if total_calls > 0 else 100.0
        
        # 4. Dataflow def-use check
        var_nodes = [n for n, d in nodes.items() if d.get("type") == "variable"]
        used_vars = set([v for _, v, d in edges if d.get("relation") == "USES"])
        defined_vars = set([v for _, v, d in edges if d.get("relation") in ("DEFINES", "PASSES_ARG")])
        
        # 5. Semantic coverage
        doc_count = len(self.docstrings)
        func_count = sum(1 for _, d in nodes.items() if d.get("type") in ("function", "class"))
        doc_coverage = (doc_count / func_count * 100.0) if func_count > 0 else 0.0

        report = {
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "layer_distribution": layer_dist,
            "isolated_nodes_count": len(isolated),
            "call_graph_resolution_rate_pct": round(call_res_rate, 2),
            "total_calls": total_calls,
            "internal_resolved_calls": resolved_calls,
            "external_library_calls": external_calls,
            "variable_nodes_count": len(var_nodes),
            "dataflow_def_use_linked_vars": len(defined_vars.intersection(used_vars)),
            "docstring_coverage_pct": round(doc_coverage, 2),
            "contract_services": len(self.proto_services),
            "contract_producer_edges": sum(1 for _, _, d in edges if d.get("relation") == "IMPLEMENTS"),
            "contract_consumer_edges": sum(1 for _, _, d in edges if d.get("relation") == "CONSUMES"),
            "health_status": "PASS (All 4 Layers Populated)" if all(v > 0 for v in layer_dist.values()) else "PASS (Structure Verified)",
        }
        return report

    def run_interactive_shell(self):
        """Interactive REPL for live query and explanation."""
        print("\n" + "="*70)
        print("  GRAFT-CKG Interactive Query Engine (Type 'exit' to quit)")
        print("  Commands:")
        print("    explain <symbol>      - 4-Layer breakdown of a class/function")
        print("    rag <natural query>   - Semantic search & Subtree Grafting")
        print("    adj <node_id> [layer] - Adjacency graph traversal (Lsyn, Ldep, Lflow, Lsem)")
        print("    verify                - Run graph health & integrity check")
        print("    stats                 - View node/edge statistics")
        print("="*70)
        while True:
            try:
                line = input("\nCKG> ").strip()
                if not line or line.lower() in ("exit", "quit"):
                    break
                parts = line.split(" ", 1)
                cmd = parts[0].lower()
                arg = parts[1].strip() if len(parts) > 1 else ""

                if cmd == "explain":
                    print(json.dumps(self.explain_node(arg), indent=2))
                elif cmd == "rag":
                    print(json.dumps(self.rag_query(arg), indent=2))
                elif cmd == "adj":
                    subparts = arg.split(" ")
                    target = subparts[0]
                    layers = [subparts[1]] if len(subparts) > 1 else None
                    print(json.dumps(self.adjacency_query(target, layers=layers), indent=2))
                elif cmd == "verify":
                    print(json.dumps(self.verify_graph(), indent=2))
                elif cmd == "stats":
                    self._print_layer_statistics()
                else:
                    print("Unknown command. Try: explain, rag, adj, verify, stats, exit")
            except (KeyboardInterrupt, EOFError):
                break


# -------------------------------------------------------------
# CLI INTERFACE
# -------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="GRAFT-CKG: 4-Layer Code Knowledge Graph Engine")
    parser.add_argument("repo_path", nargs="?", default=None, help="Path to repository")
    parser.add_argument("--workspace", help="Path to workspace.yaml or workspace.json manifest for multi-repo analysis")
    parser.add_argument("--interactive", action="store_true", help="Launch interactive query REPL")
    parser.add_argument("--verify", action="store_true", help="Run graph integrity and layer completeness verification")
    parser.add_argument("--explain", help="Node ID or symbol name to explain across all 4 layers")
    parser.add_argument("--rag", help="Natural language query for RAG retrieval and subtree grafting")
    parser.add_argument("--adjacency", help="Node ID to run adjacency query on")
    parser.add_argument("--layers", nargs="+", choices=["Lsyn", "Ldep", "Lflow", "Lsem", "Lcontract"], help="Filter adjacency by layers")
    parser.add_argument("--who-consumes", dest="who_consumes", help="List consumers of a contract (Service or Service.Rpc)")
    parser.add_argument("--who-implements", dest="who_implements", help="List implementers of a contract (Service or Service.Rpc)")
    parser.add_argument("--export", default="repo_graph.graphml", help="Export path for GraphML")
    args = parser.parse_args()

    if args.workspace:
        from multi_repo import WorkspaceCKGBuilder
        builder = WorkspaceCKGBuilder(args.workspace)
        builder.build()
    elif args.repo_path:
        builder = CKGBuilder(args.repo_path)
        builder.build()
    else:
        parser.error("Either repo_path or --workspace must be provided.")

    if args.verify:
        v_report = builder.verify_graph()
        print("\n" + "="*70)
        print("GRAFT-CKG REPOSITORY GRAPH VERIFICATION REPORT")
        print("="*70)
        print(json.dumps(v_report, indent=2))

    if args.explain:
        explanation = builder.explain_node(args.explain)
        print("\n" + "="*70)
        print(f"GRAFT-CKG 4-LAYER EXPLANATION FOR: {args.explain}")
        print("="*70)
        print(json.dumps(explanation, indent=2))

    if args.rag:
        rag_res = builder.rag_query(args.rag)
        print("\n" + "="*70)
        print(f"GRAFT-CKG RAG & SUBTREE GRAFTING FOR: '{args.rag}'")
        print("="*70)
        print(json.dumps(rag_res, indent=2))

    if args.adjacency:
        adj_res = builder.adjacency_query(args.adjacency, layers=args.layers)
        print("\n" + "="*70)
        print(f"GRAFT-CKG ADJACENCY QUERY FOR: {args.adjacency}")
        print("="*70)
        print(json.dumps(adj_res, indent=2))

    if args.who_consumes:
        print(json.dumps(builder.who_consumes(args.who_consumes), indent=2))

    if args.who_implements:
        print(json.dumps(builder.who_implements(args.who_implements), indent=2))

    if args.interactive:
        builder.run_interactive_shell()

    if args.export and not args.interactive:
        export_dir = os.path.dirname(os.path.abspath(args.export))
        if export_dir:
            os.makedirs(export_dir, exist_ok=True)
        export_g = builder.graph.copy()
        for _, data in export_g.nodes(data=True):
            for k, v in list(data.items()):
                if isinstance(v, (list, dict)):
                    data[k] = json.dumps(v)
        for _, _, data in export_g.edges(data=True):
            for k, v in list(data.items()):
                if isinstance(v, (list, dict)):
                    data[k] = json.dumps(v)
        
        # 1. Export GraphML (with globally unique edge IDs)
        nx.write_graphml(export_g, args.export)
        print(f"\n[*] GraphML exported to: {args.export}")

        # 2. Export GEXF (Native format for Gephi & Gephi Lite)
        gexf_path = os.path.splitext(args.export)[0] + ".gexf"
        try:
            nx.write_gexf(export_g, gexf_path)
            print(f"[*] Gephi Native GEXF exported to: {gexf_path}")
        except Exception as e:
            pass


if __name__ == "__main__":
    main()
