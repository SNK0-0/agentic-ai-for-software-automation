"""Regression coverage for cross-file Python dependency resolution."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from graft_ckg import CKGBuilder


class DependencyResolutionTests(unittest.TestCase):
    def _build_fixture(self) -> CKGBuilder:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        package = root / "pkg"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "base.py").write_text(
            "class Parent:\n"
            "    def inherited(self):\n"
            "        return 'ok'\n",
            encoding="utf-8",
        )
        (package / "helpers.py").write_text(
            "def transform(value):\n"
            "    return value\n",
            encoding="utf-8",
        )
        (package / "child.py").write_text(
            "from .base import Parent\n"
            "from .helpers import transform\n\n"
            "class Child(Parent):\n"
            "    def run(self, value):\n"
            "        self.inherited()\n"
            "        super().inherited()\n"
            "        return transform(value)\n",
            encoding="utf-8",
        )
        return CKGBuilder(root)

    def tearDown(self) -> None:
        if hasattr(self, "tempdir"):
            self.tempdir.cleanup()

    def test_relative_import_and_inherited_method_calls_resolve(self) -> None:
        builder = self._build_fixture()
        graph = builder.build()

        caller = "pkg/child.py:Child.run"
        targets = [
            target
            for _, target, data in graph.out_edges(caller, data=True)
            if data.get("relation") == "CALLS"
        ]
        self.assertGreaterEqual(targets.count("pkg/base.py:Parent.inherited"), 2)
        self.assertIn("pkg/helpers.py:transform", targets)

        inheritance = [
            target
            for _, target, data in graph.out_edges("pkg/child.py:Child", data=True)
            if data.get("relation") == "EXTENDS"
        ]
        self.assertIn("pkg/base.py:Parent", inheritance)

    def test_javascript_relative_import_and_inherited_method_calls_resolve(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            web = root / "web"
            web.mkdir()
            (web / "base.js").write_text(
                "export class Parent { inherited() { return 'ok'; } }\n", encoding="utf-8"
            )
            (web / "helpers.js").write_text(
                "export function transform(value) { return value; }\n", encoding="utf-8"
            )
            (web / "child.js").write_text(
                "import { Parent } from './base';\n"
                "import { transform } from './helpers';\n"
                "class Child extends Parent {\n"
                "  run(value) { this.inherited(); super.inherited(); return transform(value); }\n"
                "}\n",
                encoding="utf-8",
            )
            graph = CKGBuilder(root).build()

        caller = "web/child.js:Child.run"
        targets = [
            target
            for _, target, data in graph.out_edges(caller, data=True)
            if data.get("relation") == "CALLS"
        ]
        self.assertGreaterEqual(targets.count("web/base.js:Parent.inherited"), 2)
        self.assertIn("web/helpers.js:transform", targets)

    def test_typescript_relative_import_resolves(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            web = root / "web"
            web.mkdir()
            (web / "helpers.ts").write_text(
                "export function transform(value: string): string { return value; }\n",
                encoding="utf-8",
            )
            (web / "use.ts").write_text(
                "import { transform } from './helpers';\n"
                "export function run(value: string): string { return transform(value); }\n",
                encoding="utf-8",
            )
            graph = CKGBuilder(root).build()

        targets = [
            target
            for _, target, data in graph.out_edges("web/use.ts:run", data=True)
            if data.get("relation") == "CALLS"
        ]
        self.assertIn("web/helpers.ts:transform", targets)

    def test_commonjs_relative_import_and_alias_resolve(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            web = root / "web"
            web.mkdir()
            (web / "base.js").write_text(
                "class Parent { inherited() { return 'ok'; } }\n", encoding="utf-8"
            )
            (web / "helpers.js").write_text(
                "function transform(value) { return value; }\n", encoding="utf-8"
            )
            (web / "child.js").write_text(
                "const { Parent } = require('./base');\n"
                "const { transform: adapt } = require('./helpers');\n"
                "class Child extends Parent {\n"
                "  run(value) { this.inherited(); return adapt(value); }\n"
                "}\n",
                encoding="utf-8",
            )
            graph = CKGBuilder(root).build()

        targets = [
            target
            for _, target, data in graph.out_edges("web/child.js:Child.run", data=True)
            if data.get("relation") == "CALLS"
        ]
        self.assertIn("web/base.js:Parent.inherited", targets)
        self.assertIn("web/helpers.js:transform", targets)

    def test_javascript_parameter_and_local_def_use_are_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "flow.js").write_text(
                "function run(value) { const normalized = value; return normalized; }\n",
                encoding="utf-8",
            )
            graph = CKGBuilder(root).build()

        function_id = "flow.js:run"
        flow_edges = [
            (data.get("relation"), target)
            for _, target, data in graph.out_edges(function_id, data=True)
            if data.get("layer") == "Lflow"
        ]
        self.assertIn(("PASSES_ARG", f"{function_id}.var::value"), flow_edges)
        self.assertIn(("DEFINES", f"{function_id}.var::normalized"), flow_edges)
        self.assertIn(("USES", f"{function_id}.var::value"), flow_edges)
        self.assertIn(("USES", f"{function_id}.var::normalized"), flow_edges)

    def test_javascript_return_and_mutation_flow_are_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "flow.js").write_text(
                "function run(value) { let normalized = value; normalized = normalized.trim(); return normalized; }\n",
                encoding="utf-8",
            )
            graph = CKGBuilder(root).build()

        function_id = "flow.js:run"
        variable_id = f"{function_id}.var::normalized"
        return_id = f"{function_id}.return::1"
        self.assertTrue(any(
            data.get("relation") == "MUTATES" and target == variable_id
            for _, target, data in graph.out_edges(function_id, data=True)
        ))
        self.assertTrue(any(
            data.get("relation") == "RETURNS" and target == return_id
            for _, target, data in graph.out_edges(function_id, data=True)
        ))
        self.assertTrue(any(
            data.get("relation") == "FLOWS_TO" and target == return_id
            for _, target, data in graph.out_edges(variable_id, data=True)
        ))

    def test_typescript_path_alias_resolves(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tsconfig.json").write_text(
                '{"compilerOptions": {"paths": {"@lib/*": ["lib/*"]}}}',
                encoding="utf-8",
            )
            library = root / "lib"
            library.mkdir()
            (library / "helpers.ts").write_text(
                "export function transform(value: string): string { return value; }\n",
                encoding="utf-8",
            )
            (root / "use.ts").write_text(
                "import { transform } from '@lib/helpers';\n"
                "export function run(value: string): string { return transform(value); }\n",
                encoding="utf-8",
            )
            graph = CKGBuilder(root).build()

        targets = [
            target
            for _, target, data in graph.out_edges("use.ts:run", data=True)
            if data.get("relation") == "CALLS"
        ]
        self.assertIn("lib/helpers.ts:transform", targets)

    def test_local_workspace_package_main_entry_resolves(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shared = root / "packages" / "shared" / "src"
            shared.mkdir(parents=True)
            (root / "packages" / "shared" / "package.json").write_text(
                '{"name": "@demo/shared", "main": "./src/index.js"}',
                encoding="utf-8",
            )
            (shared / "index.js").write_text(
                "export function transform(value) { return value; }\n", encoding="utf-8"
            )
            app = root / "app"
            app.mkdir()
            (app / "use.js").write_text(
                "import { transform } from '@demo/shared';\n"
                "export function run(value) { return transform(value); }\n",
                encoding="utf-8",
            )
            graph = CKGBuilder(root).build()

        targets = [
            target
            for _, target, data in graph.out_edges("app/use.js:run", data=True)
            if data.get("relation") == "CALLS"
        ]
        self.assertIn("packages/shared/src/index.js:transform", targets)


if __name__ == "__main__":
    unittest.main()
