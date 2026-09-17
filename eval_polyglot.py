"""Reproducible structural evaluation for the bundled mixed-service fixture.

This is a deterministic fixture check, not a claim of real-world accuracy.
It verifies that literal TypeScript fetch calls bind to literal Python
Flask-style route decorators in one repository-wide graph build.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from graft_ckg import CKGBuilder


EXPECTED_BINDINGS = {
    "frontend/client.ts:loadGreeting": "backend/api.py:get_greeting",
    "frontend/client.ts:sendEcho": "backend/api.py:echo",
}


def collect_http_bindings(graph):
    return {
        source: target
        for source, target, data in graph.edges(data=True)
        if data.get("relation") == "HTTP_CALLS"
    }


def main():
    parser = argparse.ArgumentParser(description="Evaluate mixed Python/TypeScript graph extraction.")
    default_fixture = Path(__file__).parent / "examples" / "mixed_service"
    parser.add_argument("--repo", type=Path, default=default_fixture)
    args = parser.parse_args()

    graph = CKGBuilder(args.repo).build()
    bindings = collect_http_bindings(graph)
    print("\nPolyglot fixture result")
    print(f"  Repository: {args.repo}")
    print(f"  HTTP bindings found: {len(bindings)}")
    for source, target in sorted(bindings.items()):
        print(f"  {source} -> {target}")

    if args.repo.resolve() == default_fixture.resolve():
        missing = {source: target for source, target in EXPECTED_BINDINGS.items()
                   if bindings.get(source) != target}
        if missing:
            print(f"  FAILED: missing or incorrect bindings: {missing}")
            raise SystemExit(1)
        print("  PASSED: all labelled literal endpoint bindings were recovered.")


if __name__ == "__main__":
    main()
