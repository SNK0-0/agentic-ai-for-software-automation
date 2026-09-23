"""Contract-violation fault injection & detection benchmark.

Injects a breaking change into a copy of a repository's .proto contract WITHOUT
updating consumers or producers, rebuilds the graph, and reports every code
entity whose binding is now dangling.  This is the ground-truth generator for
Layer-2 construction accuracy and the first "blast radius" demo.

Fault classes:
  rename-rpc     : rename an rpc (e.g. GetQuote -> GetQuoteV2)
  remove-rpc     : delete an rpc from its service
  rename-service : rename a whole service

Usage:
  python bench/inject_contract_fault.py --repo PATH --service ShippingService --rpc GetQuote --fault rename-rpc
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from graft_ckg import CKGBuilder  # noqa: E402


def build_quiet(path):
    with contextlib.redirect_stdout(io.StringIO()):
        b = CKGBuilder(path)
        b.build()
    return b


def contract_bindings(builder):
    """Return {(relation, source, rpc_or_service)} for every contract edge."""
    out = set()
    for u, v, d in builder.graph.edges(data=True):
        if d.get("relation") in {"IMPLEMENTS", "CONSUMES"}:
            out.add((d["relation"], u, v))
    return out


def inject(repo_copy, service, rpc, fault):
    """Mutate the .proto in repo_copy. Returns the list of touched proto files."""
    touched = []
    for root, _, files in os.walk(repo_copy):
        for f in files:
            if not f.endswith(".proto"):
                continue
            path = os.path.join(root, f)
            text = open(path, encoding="utf-8").read()
            new = text
            if fault == "rename-rpc":
                new = re.sub(rf"\brpc\s+{rpc}\s*\(", f"rpc {rpc}V2(", text)
            elif fault == "remove-rpc":
                new = re.sub(rf"\n\s*rpc\s+{rpc}\s*\([^\n]*\n", "\n", text)
            elif fault == "rename-service":
                new = re.sub(rf"\bservice\s+{service}\b", f"service {service}V2", text)
            if new != text:
                open(path, "w", encoding="utf-8").write(new)
                touched.append(os.path.relpath(path, repo_copy))
    return touched


def detect(before, after):
    """Bindings that existed before the fault but are gone after it = blast radius."""
    lost = before - after
    gained = after - before
    return sorted(lost), sorted(gained)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--service", required=True)
    ap.add_argument("--rpc", default=None)
    ap.add_argument("--fault", choices=["rename-rpc", "remove-rpc", "rename-service"], default="rename-rpc")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    base = build_quiet(args.repo)
    before = contract_bindings(base)

    tmp = tempfile.mkdtemp(prefix="ckg_fault_")
    copy = os.path.join(tmp, "repo")
    shutil.copytree(args.repo, copy, ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__"))
    touched = inject(copy, args.service, args.rpc, args.fault)
    mutated = build_quiet(copy)
    after = contract_bindings(mutated)
    lost, gained = detect(before, after)

    # Dangling entities: code that still references the OLD contract name
    dangling_consumers = sorted({u for rel, u, v in lost if rel == "CONSUMES"})
    dangling_producers = sorted({u for rel, u, v in lost if rel == "IMPLEMENTS"})

    report = {
        "fault": args.fault,
        "service": args.service,
        "rpc": args.rpc,
        "proto_files_mutated": touched,
        "bindings_before": len(before),
        "bindings_after": len(after),
        "bindings_lost": len(lost),
        "detected": len(lost) > 0,
        "blast_radius": {
            "dangling_consumers": dangling_consumers,
            "dangling_producers": dangling_producers,
            "services_affected": sorted({u.split(":")[0].split("/")[1] if "/" in u else u for u in dangling_consumers + dangling_producers}),
        },
        "lost_edges": [f"{rel}: {u} -> {v}" for rel, u, v in lost],
    }
    shutil.rmtree(tmp, ignore_errors=True)
    print(json.dumps(report, indent=2))
    if args.out:
        with open(args.out, "w") as f:
            json.dump(report, f, indent=2)


if __name__ == "__main__":
    main()
