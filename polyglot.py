"""Tree-sitter extraction for JavaScript, TypeScript, and Go adapters.

The adapters deliberately emit the same node IDs and edge vocabulary used by
the Python builder. That keeps query, export, and evaluation code language
agnostic while language-specific parsing remains isolated here.
"""

from __future__ import annotations

import os
import re

from tree_sitter import Language, Parser
import tree_sitter_javascript
import tree_sitter_typescript

try:  # Go adapter is optional: the engine degrades to JS/TS without it.
    import tree_sitter_go

    _GO_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on environment
    _GO_AVAILABLE = False


class TreeSitterExtractor:
    EXTENSIONS = {".js", ".jsx", ".ts", ".tsx", ".go"}

    def __init__(self, extension):
        if extension == ".go":
            if not _GO_AVAILABLE:
                raise RuntimeError("tree-sitter-go is not installed")
            grammar = tree_sitter_go.language()
        elif extension in {".ts", ".tsx"}:
            grammar = tree_sitter_typescript.language_typescript()
        else:
            grammar = tree_sitter_javascript.language()
        self.parser = Parser(Language(grammar))
        self.is_go = extension == ".go"

    @staticmethod
    def _text(node, source):
        return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    @staticmethod
    def _line(node):
        return node.start_point.row + 1

    @staticmethod
    def _extract_doc_comment(node, source):
        """Extract preceding JSDoc (/** ... */) or Go (// ...) doc comments."""
        def get_comment_node(n):
            curr = n.prev_sibling
            while curr and curr.type in {"comment", "line_comment", "block_comment"}:
                return curr
            if n.parent and n.parent.type in {
                "export_statement", "lexical_declaration", "variable_declaration", "variable_declarator"
            }:
                return get_comment_node(n.parent)
            return None

        c_node = get_comment_node(node)
        if not c_node:
            return None

        comments = []
        curr = c_node
        while curr and curr.type in {"comment", "line_comment", "block_comment"}:
            raw_c = source[curr.start_byte:curr.end_byte].decode("utf-8", errors="replace").strip()
            comments.insert(0, raw_c)
            curr = curr.prev_sibling

        if not comments:
            return None

        full_comment = "\n".join(comments)
        cleaned = re.sub(r"^/\*\*|\*/$", "", full_comment)
        cleaned = re.sub(r"(?m)^\s*\*\s?", "", cleaned)
        cleaned = re.sub(r"(?m)^\s*//\s?", "", cleaned)
        return cleaned.strip() or None

    def extract(self, builder, rel_path, full_path):
        with open(full_path, "rb") as source_file:
            source = source_file.read()
        text = source.decode("utf-8", errors="replace")
        builder.current_file = rel_path
        builder.current_scope = []
        builder.scope_depth = 0
        builder.imports_by_file[rel_path] = {}
        builder.raw_file_lines[rel_path] = text.splitlines()
        builder.total_loc += len(builder.raw_file_lines[rel_path])
        file_id = getattr(builder, "current_file_node_id", f"file::{rel_path}")
        builder.add_ckg_node(
            file_id, "file", "Lsyn", os.path.basename(rel_path), 1,
            code=f"// File: {rel_path}\n// Lines: {len(builder.raw_file_lines[rel_path])}",
        )
        self._register_imports(builder, rel_path, text, file_id)
        self._visit(builder, self.parser.parse(source).root_node, source)

    # ------------------------------------------------------------------
    # IMPORTS
    # ------------------------------------------------------------------
    def _register_imports(self, builder, rel_path, text, file_id):
        for names, specifier in re.findall(
            r"import\s+([\s\S]+?)\s+from\s+[\"']([^\"']+)[\"']", text
        ):
            target_module = builder.resolve_js_module_specifier(rel_path, specifier)
            aliases = []
            clean_names = names.strip()
            if clean_names.startswith("{"):
                for item in clean_names.strip(" {} \r\n\t").split(","):
                    item = item.strip()
                    if not item:
                        continue
                    source_name, _, alias = item.partition(" as ")
                    aliases.append(((alias or source_name).strip(), f"{target_module}.{source_name.strip()}"))
            elif clean_names.startswith("*"):
                alias = clean_names.split(" as ")[-1].strip()
                aliases.append((alias, target_module))
            else:
                aliases.append((clean_names.split(",")[0].strip(), target_module))
            for alias, target in aliases:
                self._add_import(builder, rel_path, file_id, alias, target)

        # CommonJS: const { original: local } = require("./module")
        for names, specifier in re.findall(
            r"(?:const|let|var)\s+\{([^}]+)\}\s*=\s*require\(\s*[\"']([^\"']+)[\"']\s*\)",
            text,
        ):
            target_module = builder.resolve_js_module_specifier(rel_path, specifier)
            for item in names.split(","):
                source_name, _, alias = item.strip().partition(":")
                self._add_import(
                    builder, rel_path, file_id, (alias or source_name).strip(),
                    f"{target_module}.{source_name.strip()}",
                )

        # CommonJS: const helpers = require("./helpers")
        for alias, specifier in re.findall(
            r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*require\(\s*[\"']([^\"']+)[\"']\s*\)",
            text,
        ):
            target_module = builder.resolve_js_module_specifier(rel_path, specifier)
            self._add_import(builder, rel_path, file_id, alias, target_module)

        # Go: import "path" / import alias "path" / import ( "path" ... )
        for alias, specifier in re.findall(
            r"(?m)^\s*(?:([A-Za-z_][\w.]*)\s+)?\"([a-zA-Z0-9_./-]+)\"\s*$", text
        ):
            if "import" not in text:
                continue
            if not re.search(r"(?m)^\s*import\b", text):
                continue
            module_key = builder._module_key_for_path(specifier)
            local_alias = alias or specifier.rsplit("/", 1)[-1]
            builder.imports_by_file[rel_path][local_alias] = module_key
            symbol = f"module::{specifier}"
            if symbol not in builder.graph:
                builder.add_ckg_node(symbol, "module", "Ldep", specifier)
            builder.add_ckg_edge(file_id, symbol, "IMPORTS", "Ldep")

    @staticmethod
    def _add_import(builder, rel_path, file_id, alias, target):
        builder.imports_by_file[rel_path][alias] = target
        symbol = f"symbol::{target}"
        if symbol not in builder.graph:
            builder.add_ckg_node(symbol, "symbol_ref", "Ldep", alias)
        builder.add_ckg_edge(file_id, symbol, "IMPORTS", "Ldep")

    # ------------------------------------------------------------------
    # AST WALK
    # ------------------------------------------------------------------
    def _visit(self, builder, node, source):
        if node.type == "class_declaration":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = self._text(name_node, source)
                class_id = builder.get_node_id(name)
                builder.add_ckg_node(class_id, "class", "Lsyn", name, self._line(node))
                builder.register_symbol(class_id, name)
                docstring = self._extract_doc_comment(node, source)
                if docstring:
                    builder.docstrings[class_id] = docstring
                    builder.graph.nodes[class_id]["docstring"] = docstring
                builder.add_ckg_edge(builder.get_current_scope_id(), class_id, "CONTAINS", "Lsyn")
                heritage = next((c for c in node.named_children if c.type == "class_heritage"), None)
                if heritage:
                    base = re.sub(r"^extends\s+", "", self._text(heritage, source)).strip()
                    if base:
                        builder.class_base_records.append((class_id, base, builder.current_file, builder.scope_depth))
                self._with_scope(builder, name, lambda: self._visit_children(builder, node, source))
                return

        if node.type == "type_declaration" and self.is_go:
            self._go_type_declaration(builder, node, source)
            return

        if node.type in {"interface_declaration", "abstract_class_declaration", "type_alias_declaration", "enum_declaration"}:
            name_node = node.child_by_field_name("name")
            if name_node:
                name = self._text(name_node, source)
                type_kind = "class" if "class" in node.type or "interface" in node.type else "type"
                node_id = builder.get_node_id(name)
                builder.add_ckg_node(node_id, type_kind, "Lsyn", name, self._line(node))
                builder.register_symbol(node_id, name)
                docstring = self._extract_doc_comment(node, source)
                if docstring:
                    builder.docstrings[node_id] = docstring
                    builder.graph.nodes[node_id]["docstring"] = docstring
                builder.add_ckg_edge(builder.get_current_scope_id(), node_id, "CONTAINS", "Lsyn")
                self._with_scope(builder, name, lambda: self._visit_children(builder, node, source))
                return

        if node.type in {"function_declaration", "method_definition", "method_declaration"}:
            name_node = node.child_by_field_name("name")
            if name_node:
                receiver = None
                if node.type == "method_declaration":
                    receiver = self._go_receiver_type(node, source)
                self._add_function(builder, node, source, self._text(name_node, source), receiver=receiver)
                return

        # Arrow function or function expression assigned to a variable / export
        if node.type == "variable_declarator":
            name_node = node.child_by_field_name("name")
            value_node = node.child_by_field_name("value")
            if name_node and value_node and value_node.type in {"arrow_function", "function_expression"}:
                name = self._text(name_node, source)
                self._add_function(builder, value_node, source, name)
                return

        # Constructor call: new Store(...)
        if node.type == "new_expression":
            constructor_node = node.child_by_field_name("constructor")
            if constructor_node:
                raw_callee = self._text(constructor_node, source)
                match = re.search(r"(\w+Service)$", raw_callee)
                if match:
                    curr_p = node.parent
                    while curr_p and curr_p.type != "variable_declarator":
                        curr_p = curr_p.parent
                    if curr_p and curr_p.type == "variable_declarator":
                        var_ident = curr_p.child_by_field_name("name")
                        if var_ident:
                            var_name = self._text(var_ident, source)
                            builder.grpc_client_vars.setdefault(builder.current_file, {})[var_name] = match.group(1)
                if builder.current_scope:
                    scope_id = builder.get_current_scope_id()
                    target_name = raw_callee.rsplit(".", 1)[-1]
                    builder.calls_to_resolve.append(
                        (scope_id, target_name, builder.scope_depth, builder.current_file)
                    )

        if node.type == "call_expression":
            function = node.child_by_field_name("function")
            if function:
                raw_callee = self._text(function, source)
                # Express route registration: app.get("/path", handler)
                express_match = re.match(r"^(?:app|router)\.(get|post|put|delete|patch)$", raw_callee)
                if express_match:
                    method = express_match.group(1).upper()
                    arguments = node.child_by_field_name("arguments")
                    if arguments and len(arguments.named_children) >= 2:
                        path_str = self._text(arguments.named_children[0], source).strip("'\"")
                        if path_str.startswith("/"):
                            h_node = arguments.named_children[1]
                            h_name = self._text(h_node, source)
                            h_id = builder.get_node_id(h_name) if h_node.type == "identifier" else builder.get_current_scope_id()
                            builder.http_endpoints.setdefault((method, path_str), []).append(h_id)

                if builder.current_scope:
                    if self.is_go:
                        self._go_call(builder, node, raw_callee, source)
                    else:
                        http_call = self._literal_http_call(node, raw_callee, source)
                        if http_call:
                            path, method = http_call
                            builder.http_calls_to_resolve.append(
                                (builder.get_current_scope_id(), path, method, builder.current_file)
                            )
                        callee = raw_callee.replace("this.", "self.")
                        if callee.startswith("super."):
                            callee = "super()." + callee[len("super."):]
                        builder.calls_to_resolve.append(
                            (builder.get_current_scope_id(), callee, builder.scope_depth, builder.current_file)
                        )
                        # JS gRPC client call: client.getQuote(...)
                        if "." in raw_callee:
                            var_name, _, method = raw_callee.partition(".")
                            service = builder.grpc_client_vars.get(builder.current_file, {}).get(var_name)
                            if service:
                                builder.grpc_calls_to_resolve.append(
                                    (builder.get_current_scope_id(), service, method, builder.current_file)
                                )
                        # JS gRPC server: server.addService(proto.XService.service, {rpc: handler})
                        add_service = re.match(r"^(?:\w+\.)*addService$", raw_callee)
                        if add_service:
                            self._js_add_service(builder, node, source)

        if node.type == "return_statement" and builder.current_scope:
            scope_id = builder.get_current_scope_id()
            return_id = f"{scope_id}.return::{self._line(node)}"
            builder.add_ckg_node(return_id, "return", "Lflow", "return", self._line(node))
            builder.add_ckg_edge(scope_id, return_id, "RETURNS", "Lflow")
            variable_id = self._first_local_variable(builder, node, source, scope_id)
            if variable_id:
                builder.add_ckg_edge(variable_id, return_id, "FLOWS_TO", "Lflow")

        if node.type in {"assignment_expression", "augmented_assignment_expression", "update_expression"} and builder.current_scope:
            scope_id = builder.get_current_scope_id()
            target = node.child_by_field_name("left") or node.child_by_field_name("argument")
            if target and target.type == "identifier":
                variable_id = builder.var_defs.get(scope_id, {}).get(self._text(target, source))
                if variable_id:
                    builder.add_ckg_edge(scope_id, variable_id, "MUTATES", "Lflow")

        if node.type == "assignment_statement" and self.is_go and builder.current_scope:
            scope_id = builder.get_current_scope_id()
            left = node.child_by_field_name("left")
            if left:
                for ident in (left.named_children if left.named_children else [left]):
                    if ident.type == "identifier":
                        name = self._text(ident, source)
                        variable_id = builder.var_defs.get(scope_id, {}).get(name)
                        if variable_id:
                            builder.add_ckg_edge(scope_id, variable_id, "MUTATES", "Lflow")

        if node.type == "var_spec" and builder.current_scope:
            scope_id = builder.get_current_scope_id()
            name_node = node.child_by_field_name("name")
            if name_node:
                name = self._text(name_node, source)
                variable_id = f"{scope_id}.var::{name}"
                if variable_id not in builder.graph:
                    builder.add_ckg_node(variable_id, "variable", "Lflow", name, self._line(name_node))
                builder.var_defs.setdefault(scope_id, {})[name] = variable_id
                builder.add_ckg_edge(scope_id, variable_id, "DEFINES", "Lflow")

        if node.type == "short_var_declaration" and builder.current_scope:
            scope_id = builder.get_current_scope_id()
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            if left:
                for ident in left.named_children:
                    if ident.type == "identifier":
                        name = self._text(ident, source)
                        variable_id = f"{scope_id}.var::{name}"
                        if variable_id not in builder.graph:
                            builder.add_ckg_node(variable_id, "variable", "Lflow", name, self._line(ident))
                        builder.var_defs.setdefault(scope_id, {})[name] = variable_id
                        builder.add_ckg_edge(scope_id, variable_id, "DEFINES", "Lflow")
            # gRPC client var: cl := pb.NewXServiceClient(conn)
            if left and right and right.named_children:
                call = right.named_children[0]
                if call.type == "call_expression":
                    fn = call.child_by_field_name("function")
                    if fn:
                        fn_text = self._text(fn, source)
                        match = re.match(r"^(\w+)\.New(\w+)Client$", fn_text)
                        if match:
                            for ident in left.named_children:
                                if ident.type == "identifier":
                                    builder.grpc_client_vars.setdefault(builder.current_file, {})[
                                        self._text(ident, source)
                                    ] = match.group(2)
                        # impl var: svc := new(checkoutService)  or  svc := &checkoutService{}
                        if fn_text == "new":
                            arg = self._call_argument_text(call, source, index=0)
                            if arg:
                                for ident in left.named_children:
                                    if ident.type == "identifier":
                                        builder.go_type_vars.setdefault(builder.current_file, {})[
                                            self._text(ident, source)
                                        ] = arg.strip()
                if call.type == "unary_expression":
                    # svc := &checkoutService{}
                    inner = call.named_children[0] if call.named_children else None
                    if inner is not None and inner.type == "composite_literal":
                        type_node = inner.child_by_field_name("type")
                        if type_node:
                            for ident in left.named_children:
                                if ident.type == "identifier":
                                    builder.go_type_vars.setdefault(builder.current_file, {})[
                                        self._text(ident, source)
                                    ] = self._text(type_node, source)

        if node.type == "variable_declarator" and builder.current_scope:
            scope_id = builder.get_current_scope_id()
            if builder.graph.nodes.get(scope_id, {}).get("type") == "function":
                name_node = node.child_by_field_name("name")
                if name_node and name_node.type == "identifier":
                    name = self._text(name_node, source)
                    variable_id = f"{scope_id}.var::{name}"
                    if variable_id not in builder.graph:
                        builder.add_ckg_node(variable_id, "variable", "Lflow", name, self._line(name_node))
                    builder.var_defs.setdefault(scope_id, {})[name] = variable_id
                    builder.add_ckg_edge(scope_id, variable_id, "DEFINES", "Lflow")
                    self._visit_children(builder, node, source, skip_nodes=(name_node,))
                    return

        if node.type == "identifier" and builder.current_scope:
            scope_id = builder.get_current_scope_id()
            variable_id = builder.var_defs.get(scope_id, {}).get(self._text(node, source))
            if variable_id:
                builder.add_ckg_edge(scope_id, variable_id, "USES", "Lflow")

        self._visit_children(builder, node, source)

    def _js_add_service(self, builder, node, source):
        """Capture server.addService(pkg.<Service>.service, {handler...}) bindings."""
        arguments = node.child_by_field_name("arguments")
        if not arguments:
            return
        positional = [c for c in arguments.named_children]
        if len(positional) < 2:
            return
        service_expr = self._text(positional[0], source)
        match = re.search(r"\.(\w+)\.service\s*$", service_expr)
        if not match:
            return
        service = match.group(1)
        handler_map = positional[1]
        handlers = {}
        if handler_map.type == "object":
            for pair in handler_map.named_children:
                if pair.type in {"shorthand_property_identifier", "shorthand_property_identifier_pattern"}:
                    name = self._text(pair, source)
                    handlers[name] = name
                    continue
                if pair.type == "pair":
                    key_node = pair.child_by_field_name("key")
                    value_node = pair.child_by_field_name("value")
                    if key_node and value_node:
                        key = self._text(key_node, source).strip("'\"")
                        value = self._text(value_node, source)
                        value = re.sub(r"\.bind\(.*\)$", "", value)
                        handlers[key] = value
        builder.js_add_service_records.append(
            (service, handlers, builder.current_file, builder.get_current_scope_id())
        )

    # ------------------------------------------------------------------
    # GO-SPECIFIC HANDLING
    # ------------------------------------------------------------------
    def _go_type_declaration(self, builder, node, source):
        """Register Go struct/interface type declarations as class-like nodes."""
        for spec in node.named_children:
            if spec.type not in {"type_spec", "type_alias"}:
                continue
            name_node = spec.child_by_field_name("name")
            if not name_node:
                continue
            name = self._text(name_node, source)
            type_id = builder.get_node_id(name)
            builder.add_ckg_node(type_id, "class", "Lsyn", name, self._line(spec))
            builder.register_symbol(type_id, name)
            docstring = self._extract_doc_comment(spec, source) or self._extract_doc_comment(node, source)
            if docstring:
                builder.docstrings[type_id] = docstring
                builder.graph.nodes[type_id]["docstring"] = docstring
            builder.add_ckg_edge(builder.get_current_scope_id(), type_id, "CONTAINS", "Lsyn")

    @staticmethod
    def _go_receiver_type(node, source):
        receiver = node.child_by_field_name("receiver")
        if not receiver:
            return None
        text = TreeSitterExtractor._text(receiver, source)
        match = re.search(r"[\w$]+\s+\*?([A-Za-z_][\w]*)\s*\)", text)
        if match:
            return match.group(1)
        match = re.search(r"\(\s*\*?([A-Za-z_][\w]*)", text)
        return match.group(1) if match else None

    def _go_call(self, builder, node, raw_callee, source):
        """Handle Go call expressions, including gRPC client and server patterns."""
        scope_id = builder.get_current_scope_id()
        callee = raw_callee

        # gRPC client: pb.NewXServiceClient(conn).Method(...)  (chained call)
        match = re.match(r"^(\w+)\.New(\w+)Client\(([^)]*)\)\.\s*(\w+)$", raw_callee.strip())
        if match:
            _, service, _, method = match.groups()
            builder.grpc_calls_to_resolve.append((scope_id, service, method, builder.current_file))
            callee = f"grpc.{service}.{method}"
        else:
            # gRPC client: cl.Method(...) where cl was bound via NewXServiceClient
            sel = re.match(r"^(\w+)\.(\w+)$", raw_callee)
            if sel:
                var, method = sel.groups()
                service = builder.grpc_client_vars.get(builder.current_file, {}).get(var)
                if service:
                    builder.grpc_calls_to_resolve.append((scope_id, service, method, builder.current_file))
                    callee = f"grpc.{service}.{method}"

        # gRPC server registration: pb.RegisterXServiceServer(srv, impl)
        match = re.match(r"^(\w+)\.Register(\w+)Server$", raw_callee)
        if match:
            _, service = match.groups()
            impl_arg = self._call_argument_text(node, source, index=1)
            builder.grpc_server_registrations.append(
                (service, impl_arg, builder.current_file, scope_id)
            )

        # HTTP route registration: http.HandleFunc("/path", handler)
        if raw_callee == "http.HandleFunc":
            path_arg = self._call_argument_text(node, source, index=0)
            handler_arg = self._call_argument_text(node, source, index=1)
            if path_arg and handler_arg:
                path = path_arg.strip("'\"`")
                if path.startswith("/"):
                    h_name = handler_arg.rsplit(".", 1)[-1]
                    h_id = builder.get_node_id(h_name)
                    builder.http_endpoints.setdefault(("GET", path), []).append(h_id)
                    builder.http_endpoints.setdefault(("POST", path), []).append(h_id)

        # Go client HTTP call: http.Get("http://...") or http.Post("...")
        if raw_callee in ("http.Get", "http.Post"):
            method = "GET" if raw_callee == "http.Get" else "POST"
            url_arg = self._call_argument_text(node, source, index=0)
            if url_arg:
                path_match = re.match(r"['\"`](?:https?://[^/'\"`]+)?(/[^'\"`]+)['\"`]", url_arg)
                if path_match:
                    path = path_match.group(1)
                    builder.http_calls_to_resolve.append((scope_id, path, method, builder.current_file))

        builder.calls_to_resolve.append(
            (scope_id, callee, builder.scope_depth, builder.current_file)
        )

    def _call_argument_text(self, node, source, index):
        arguments = node.child_by_field_name("arguments")
        if not arguments:
            return None
        positional = [c for c in arguments.named_children]
        if index < len(positional):
            return self._text(positional[index], source)
        return None

    # ------------------------------------------------------------------
    # JS/TS HTTP LITERALS
    # ------------------------------------------------------------------
    @staticmethod
    def _literal_http_call(node, callee, source):
        """Return a literal (path, method) for fetch/axios calls, else None."""
        method = None
        if callee == "fetch":
            method = "GET"
        else:
            match = re.fullmatch(r"axios\.(get|post|put|patch|delete)", callee)
            if match:
                method = match.group(1).upper()
        if not method:
            return None
        arguments = node.child_by_field_name("arguments")
        if not arguments:
            return None
        argument_text = TreeSitterExtractor._text(arguments, source)
        path_match = re.match(r"\(\s*['\"]([^'\"]+)['\"]", argument_text)
        if not path_match:
            return None
        if callee == "fetch":
            explicit_method = re.search(
                r"\bmethod\s*:\s*['\"]([A-Za-z]+)['\"]", argument_text
            )
            if explicit_method:
                method = explicit_method.group(1).upper()
        return path_match.group(1), method

    def _first_local_variable(self, builder, node, source, scope_id):
        """Find the first local identifier read by a return expression."""
        for child in node.named_children:
            if child.type == "identifier":
                variable_id = builder.var_defs.get(scope_id, {}).get(self._text(child, source))
                if variable_id:
                    return variable_id
            variable_id = self._first_local_variable(builder, child, source, scope_id)
            if variable_id:
                return variable_id
        return None

    def _add_function(self, builder, node, source, name, receiver=None):
        if receiver:
            # Go method: scope under its receiver type so IDs read Type.Method.
            type_id = builder.get_node_id(receiver)
            if type_id not in builder.graph:
                builder.add_ckg_node(type_id, "class", "Lsyn", receiver, self._line(node))
                builder.register_symbol(type_id, receiver)
                builder.add_ckg_edge(builder.get_current_scope_id(), type_id, "CONTAINS", "Lsyn")
            builder.current_scope.append(receiver)
            builder.scope_depth += 1
        function_id = builder.get_node_id(name)
        builder.add_ckg_node(function_id, "function", "Lsyn", name, self._line(node))
        builder.register_symbol(function_id, name)
        docstring = self._extract_doc_comment(node, source)
        if docstring:
            builder.docstrings[function_id] = docstring
            builder.graph.nodes[function_id]["docstring"] = docstring
        builder.add_ckg_edge(builder.get_current_scope_id(), function_id, "CONTAINS", "Lsyn")
        builder.var_defs.setdefault(function_id, {})
        parameters = node.child_by_field_name("parameters") or node.child_by_field_name("parameter")
        if parameters:
            parameter_text = self._text(parameters, source).strip("()")
            for entry in parameter_text.split(","):
                match = re.match(r"\s*([A-Za-z_$][\w$]*)", entry)
                if not match:
                    continue
                parameter = match.group(1)
                var_id = f"{function_id}.var::{parameter}"
                builder.add_ckg_node(var_id, "variable", "Lflow", parameter, self._line(parameters))
                builder.var_defs[function_id][parameter] = var_id
                builder.add_ckg_edge(function_id, var_id, "PASSES_ARG", "Lflow")
        self._with_scope(
            builder, name,
            lambda: self._visit_children(builder, node, source, skip_nodes=(parameters,)),
        )
        if receiver:
            builder.current_scope.pop()
            builder.scope_depth -= 1

    @staticmethod
    def _with_scope(builder, name, callback):
        builder.current_scope.append(name)
        builder.scope_depth += 1
        try:
            callback()
        finally:
            builder.scope_depth -= 1
            builder.current_scope.pop()

    def _visit_children(self, builder, node, source, skip_nodes=()):
        for child in node.named_children:
            if child in skip_nodes:
                continue
            self._visit(builder, child, source)
