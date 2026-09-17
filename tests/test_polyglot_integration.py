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


if __name__ == "__main__":
    unittest.main()
