"""End-to-end checks for the frozen mixed Python + TypeScript fixture."""

from __future__ import annotations

import unittest
from pathlib import Path

from graft_ckg import CKGBuilder


class PolyglotIntegrationTests(unittest.TestCase):
    def test_literal_frontend_calls_bind_to_python_handlers(self) -> None:
        fixture = Path(__file__).resolve().parents[1] / "examples" / "mixed_service"
        graph = CKGBuilder(fixture).build()
        bindings = {
            source: target
            for source, target, data in graph.edges(data=True)
            if data.get("relation") == "HTTP_CALLS"
        }
        self.assertEqual(
            bindings,
            {
                "frontend/client.ts:loadGreeting": "backend/api.py:get_greeting",
                "frontend/client.ts:sendEcho": "backend/api.py:echo",
            },
        )

    def test_jsdoc_and_godoc_comment_extraction_into_lsem(self) -> None:
        import tempfile, shutil, os
        tmp = tempfile.mkdtemp()
        try:
            js_code = (
                "/**\n"
                " * Computes shipping rate for cart.\n"
                " */\n"
                "export function calcShipping(weight) { return weight * 2; }\n"
                "/**\n"
                " * Represents payment checkout entity.\n"
                " */\n"
                "export class CheckoutService {}\n"
            )
            go_code = (
                "package main\n\n"
                "// ProcessOrder handles financial transaction settlement\n"
                "func ProcessOrder() {}\n"
            )
            with open(os.path.join(tmp, "service.js"), "w") as f:
                f.write(js_code)
            with open(os.path.join(tmp, "main.go"), "w") as f:
                f.write(go_code)

            builder = CKGBuilder(tmp)
            builder.build()

            # Verify JS function docstring
            fn_id = "service.js:calcShipping"
            self.assertIn(fn_id, builder.docstrings)
            self.assertIn("Computes shipping rate", builder.docstrings[fn_id])

            # Verify JS class docstring
            cls_id = "service.js:CheckoutService"
            self.assertIn(cls_id, builder.docstrings)
            self.assertIn("Represents payment checkout", builder.docstrings[cls_id])

            # Verify Go function docstring
            go_id = "main.go:ProcessOrder"
            self.assertIn(go_id, builder.docstrings)
            self.assertIn("ProcessOrder handles financial", builder.docstrings[go_id])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_python_requests_and_express_http_bindings(self) -> None:
        import tempfile, shutil, os
        tmp = tempfile.mkdtemp()
        try:
            # Express server in JS
            js_server = (
                "const express = require('express');\n"
                "const app = express();\n"
                "function handleAuth(req, res) { res.send('ok'); }\n"
                "app.post('/api/auth/login', handleAuth);\n"
            )
            # Python client calling Express endpoint via requests
            py_client = (
                "import requests\n"
                "def login_user(user, pwd):\n"
                "    return requests.post('/api/auth/login', json={'u': user, 'p': pwd})\n"
            )
            with open(os.path.join(tmp, "server.js"), "w") as f:
                f.write(js_server)
            with open(os.path.join(tmp, "client.py"), "w") as f:
                f.write(py_client)

            builder = CKGBuilder(tmp)
            graph = builder.build()

            # Verify HTTP_CALLS edge from Python client.py:login_user to JS server.js:handleAuth
            http_edges = [
                (u, v) for u, v, d in graph.edges(data=True)
                if d.get("relation") == "HTTP_CALLS"
            ]
            self.assertIn(("client.py:login_user", "server.js:handleAuth"), http_edges)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
