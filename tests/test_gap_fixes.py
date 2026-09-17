"""Regression and compliance tests for all single-repo polyglot gap fixes.

Validates the capabilities specified in POLYGLOT_DEFINITION_OF_DONE.md:
  - JS/TS arrow functions, new_expression constructor calls, multi-line imports, TS declarations
  - Go var_spec declarations and assignment mutations
  - Python posonly/kwonly/vararg/kwarg parameters, tuple unpacking, for/with/augassign, returns
  - Receiver-type inference & Equation (4) polymorphic dispatch
  - Resolution scoping (cross-service isolation)
  - Ignore policy (.ckgignore + defaults)
  - Incremental update_files equivalence
"""

from __future__ import annotations

import io
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from graft_ckg import CKGBuilder


class PolyglotGapFixesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_files(self, files: dict[str, str]) -> str:
        for rel_path, content in files.items():
            full_path = os.path.join(self.temp_dir, rel_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
        return self.temp_dir

    def test_javascript_arrow_functions_and_constructors(self) -> None:
        files = {
            "util.js": "export class Store { save() {} }\nexport function helper(x) { return x; }\n",
            "app.js": (
                "import {\n  helper,\n} from './util';\n"
                "import { Store } from './util';\n"
                "export const handler = async (req, res) => {\n"
                "  const s = new Store();\n"
                "  s.save();\n"
                "  return helper(req);\n"
                "};\n"
                "function plain() {\n"
                "  const s2 = new Store();\n"
                "  return helper(s2);\n"
                "}\n"
            ),
        }
        root = self._write_files(files)
        builder = CKGBuilder(root)
        graph = builder.build()

        self.assertIn("app.js:handler", graph.nodes)
        self.assertEqual(graph.nodes["app.js:handler"]["type"], "function")

        import_edges = [
            v for u, v, d in graph.edges(data=True)
            if u == "file::app.js" and d.get("relation") == "IMPORTS"
        ]
        self.assertTrue(any("helper" in v for v in import_edges))

        calls = [
            (u, v) for u, v, d in graph.edges(data=True)
            if d.get("relation") == "CALLS" and "Store" in v
        ]
        self.assertTrue(any("handler" in u and "Store" in v for u, v in calls))
        self.assertTrue(any("plain" in u and "Store" in v for u, v in calls))

    def test_typescript_declarations_indexed(self) -> None:
        files = {
            "types.ts": (
                "export interface UserProfile {\n  id: string;\n  name: string;\n}\n"
                "export abstract class BaseService {\n  abstract execute(): void;\n}\n"
                "export enum Status { ACTIVE, INACTIVE }\n"
                "export type UserID = string;\n"
            ),
        }
        root = self._write_files(files)
        builder = CKGBuilder(root)
        graph = builder.build()

        self.assertIn("types.ts:UserProfile", graph.nodes)
        self.assertIn(graph.nodes["types.ts:UserProfile"]["type"], {"class", "interface"})
        self.assertIn("types.ts:BaseService", graph.nodes)
        self.assertEqual(graph.nodes["types.ts:BaseService"]["type"], "class")
        self.assertIn("types.ts:Status", graph.nodes)
        self.assertIn(graph.nodes["types.ts:Status"]["type"], {"enum", "type"})
        self.assertIn("types.ts:UserID", graph.nodes)
        self.assertIn(graph.nodes["types.ts:UserID"]["type"], {"type", "class"})

    def test_go_var_declarations_and_mutations(self) -> None:
        files = {
            "main.go": (
                "package main\n\n"
                "func compute() {\n"
                "  var a = 10\n"
                "  a = 20\n"
                "  b := 30\n"
                "  b = 40\n"
                "}\n"
            ),
        }
        root = self._write_files(files)
        builder = CKGBuilder(root)
        graph = builder.build()

        defines = [
            v for u, v, d in graph.edges(data=True)
            if d.get("relation") == "DEFINES" and "compute" in u
        ]
        self.assertTrue(any("var::a" in v for v in defines))
        self.assertTrue(any("var::b" in v for v in defines))

        mutates = [
            v for u, v, d in graph.edges(data=True)
            if d.get("relation") == "MUTATES" and "compute" in u
        ]
        self.assertTrue(any("var::a" in v for v in mutates))
        self.assertTrue(any("var::b" in v for v in mutates))

    def test_python_full_parameter_and_dataflow_extraction(self) -> None:
        files = {
            "proc.py": (
                "def process(pos_only, /, standard, *varargs, kw_only=1, **kwargs):\n"
                "    a, b = (10, 20)\n"
                "    for idx in range(3):\n"
                "        a += idx\n"
                "    with open('dummy') as f:\n"
                "        pass\n"
                "    return a + b\n"
            ),
        }
        root = self._write_files(files)
        builder = CKGBuilder(root)
        graph = builder.build()

        func_id = "proc.py:process"
        args_passed = [
            v.split("::")[-1] for _, v, d in graph.out_edges(func_id, data=True)
            if d.get("relation") == "PASSES_ARG"
        ]
        self.assertIn("pos_only", args_passed)
        self.assertIn("standard", args_passed)
        self.assertIn("varargs", args_passed)
        self.assertIn("kw_only", args_passed)
        self.assertIn("kwargs", args_passed)

        defs = [
            v.split("::")[-1] for _, v, d in graph.out_edges(func_id, data=True)
            if d.get("relation") == "DEFINES"
        ]
        self.assertIn("a", defs)
        self.assertIn("b", defs)
        self.assertIn("idx", defs)
        self.assertIn("f", defs)

        mutates = [
            v.split("::")[-1] for _, v, d in graph.out_edges(func_id, data=True)
            if d.get("relation") == "MUTATES"
        ]
        self.assertIn("a", mutates)

        returns = [
            v for _, v, d in graph.out_edges(func_id, data=True)
            if d.get("relation") == "RETURNS"
        ]
        self.assertGreaterEqual(len(returns), 1)

    def test_receiver_type_inference_and_polymorphic_dispatch(self) -> None:
        files = {
            "app.py": (
                "class BaseProcessor:\n"
                "    def run(self):\n"
                "        return 0\n\n"
                "class ImageProcessor(BaseProcessor):\n"
                "    def run(self):\n"
                "        return 1\n\n"
                "class AudioProcessor(BaseProcessor):\n"
                "    def run(self):\n"
                "        return 2\n\n"
                "def process_image():\n"
                "    p = ImageProcessor()\n"
                "    return p.run()\n\n"
                "def generic_dispatch(p: BaseProcessor):\n"
                "    return p.run()\n"
            ),
        }
        root = self._write_files(files)
        builder = CKGBuilder(root)
        graph = builder.build()

        img_calls = [
            v for _, v, d in graph.out_edges("app.py:process_image", data=True)
            if d.get("relation") == "CALLS" and "run" in v
        ]
        self.assertEqual(img_calls, ["app.py:ImageProcessor.run"])

        poly_calls = [
            (v, d.get("c_type", 0.0))
            for _, v, d in graph.out_edges("app.py:generic_dispatch", data=True)
            if d.get("relation") == "POLYMORPHIC_CALL"
        ]
        self.assertGreaterEqual(len(poly_calls), 2)
        total_conf = sum(conf for _, conf in poly_calls)
        self.assertAlmostEqual(total_conf, 1.0, delta=0.05)

    def test_cross_service_isolation_prevents_name_leak(self) -> None:
        files = {
            "svcA/service.py": "def handle():\n    return 'svcA'\n",
            "svcB/worker.py": "def do_work():\n    return handle()\n",
        }
        root = self._write_files(files)
        builder = CKGBuilder(root)
        graph = builder.build()

        calls = [
            v for _, v, d in graph.out_edges("svcB/worker.py:do_work", data=True)
            if d.get("relation") == "CALLS"
        ]
        self.assertNotIn("svcA/service.py:handle", calls)
        self.assertIn("external::handle", calls)

    def test_ignore_policy_filters_generated_code(self) -> None:
        files = {
            ".ckgignore": "custom_generated/*\n",
            "custom_generated/foo.py": "def gen_func(): pass\n",
            "genproto/demo.pb.go": "package genproto\n",
            "api_pb2.py": "class ApiProto: pass\n",
            "src/main.py": "def normal(): pass\n",
        }
        root = self._write_files(files)
        builder = CKGBuilder(root)
        graph = builder.build()

        node_ids = set(graph.nodes)
        self.assertTrue(any("src/main.py" in n for n in node_ids))
        self.assertFalse(any("custom_generated" in n for n in node_ids))
        self.assertFalse(any("genproto" in n for n in node_ids))
        self.assertFalse(any("api_pb2.py" in n for n in node_ids))

    def test_incremental_update_files_replaces_modified_code(self) -> None:
        files = {
            "a.py": "def calculate():\n    return 42\n",
            "b.py": "import a\ndef run():\n    return a.calculate()\n",
        }
        root = self._write_files(files)
        builder = CKGBuilder(root)
        graph = builder.build()

        self.assertIn("a.py:calculate", graph.nodes)
        b_calls = [
            v for _, v, d in graph.out_edges("b.py:run", data=True)
            if d.get("relation") == "CALLS"
        ]
        self.assertIn("a.py:calculate", b_calls)

        a_path = os.path.join(root, "a.py")
        with open(a_path, "w", encoding="utf-8") as f:
            f.write("def compute():\n    return 100\n")

        builder.update_files(["a.py"])

        self.assertNotIn("a.py:calculate", builder.graph.nodes)
        self.assertIn("a.py:compute", builder.graph.nodes)


if __name__ == "__main__":
    unittest.main()
