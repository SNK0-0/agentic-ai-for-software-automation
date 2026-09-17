"""Dynamic call-trace oracle for Layer-1/Ldep construction accuracy (Gap 1.3).

Runs a repository's own pytest suite under sys.setprofile, records every
(caller -> callee) pair between functions defined in the repository, then
compares the static GRAFT-CKG call graph against the observed calls.

Metrics (all defined, reproducible, and independent of our own graph):
  * edge_recall        = |static ∩ dynamic| / |dynamic|
  * edge_precision*    = |static ∩ dynamic| / |static edges whose caller executed|
                         (*lower bound: tests may not exercise every path)
  * disambiguation_acc = among dynamic calls whose callee NAME has >=2 definitions
                         in the repo (true polymorphic/ambiguous sites), fraction
                         where the static graph links caller -> the observed callee
  * multihop_f1        = F1 over 2-hop reachability pairs (static vs dynamic)

Usage:
  python bench/trace_oracle.py --repo /path/to/repo [--pytest-args "tests -x -q"]
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from graft_ckg import CKGBuilder  # noqa: E402

SITECUSTOMIZE = textwrap.dedent(
    """
    import sys, os, atexit, json, threading
    ROOT = os.path.realpath(os.environ["CKG_TRACE_ROOT"]) + os.sep
    OUT = os.environ["CKG_TRACE_OUT"]
    edges = {}
    def _prof(frame, event, arg):
        if event != "call":
            return
        co = frame.f_code
        fn = co.co_filename
        if not fn.startswith(ROOT):
            return
        back = frame.f_back
        if back is None:
            return
        bco = back.f_code
        if not bco.co_filename.startswith(ROOT):
            return
        key = (bco.co_filename[len(ROOT):], bco.co_qualname, fn[len(ROOT):], co.co_qualname)
        edges[key] = edges.get(key, 0) + 1
    def _dump():
        sys.setprofile(None)
        with open(OUT, "w") as f:
            json.dump([list(k) + [v] for k, v in edges.items()], f)
    sys.setprofile(_prof)
    threading.setprofile(_prof)
    atexit.register(_dump)
    """
)


def normalize(rel_file, qualname):
    """Map a runtime (file, co_qualname) to a GRAFT-CKG node id."""
    q = qualname.replace(".<locals>", "")
    if any(part.startswith("<") for part in q.split(".")):
        return None  # <module>, <lambda>, <genexpr>, ...
    parts = q.split(".")
    if parts[-1] in {"__init__", "__new__"} and len(parts) > 1:
        parts = parts[:-1]  # Foo() static call == Foo.__init__ at runtime
    return f"{rel_file.replace(os.sep, '/')}:{'.'.join(parts)}"


def run_trace(repo, pytest_args):
    tmp = tempfile.mkdtemp(prefix="ckg_trace_")
    with open(os.path.join(tmp, "sitecustomize.py"), "w") as f:
        f.write(SITECUSTOMIZE)
    out = os.path.join(tmp, "trace.json")
    env = dict(os.environ)
    src_dir = os.path.join(repo, "src")
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in [tmp, src_dir if os.path.isdir(src_dir) else None, repo, env.get("PYTHONPATH", "")] if p
    )
    env["CKG_TRACE_ROOT"] = repo
    env["CKG_TRACE_OUT"] = out
    cmd = [sys.executable, "-m", "pytest", *pytest_args.split(), "-p", "no:cacheprovider", "-q", "-x", "--no-header"]
    proc = subprocess.run(cmd, cwd=repo, env=env, capture_output=True, text=True, timeout=900)
    if not os.path.exists(out):
        raise RuntimeError(f"trace not produced; pytest output:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    raw = json.load(open(out))
    dynamic = {}
    for cf, cq, tf, tq, n in raw:
        u, v = normalize(cf, cq), normalize(tf, tq)
        if u and v and u != v:
            dynamic[(u, v)] = dynamic.get((u, v), 0) + n
    return dynamic, proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""


def static_edges(builder):
    g = builder.graph
    out = set()
    for u, v, d in g.edges(data=True):
        if d.get("relation") in {"CALLS", "POLYMORPHIC_CALL"} and not str(v).startswith(("external::", "symbol::", "module::")):
            if g.nodes[u].get("type") == "function" and g.nodes[v].get("type") in {"function", "class"}:
                out.add((u, v))
    return out


def two_hop(edges, sources):
    adj = {}
    for u, v in edges:
        adj.setdefault(u, set()).add(v)
    pairs = set()
    for s in sources:
        for m in adj.get(s, ()):
            pairs.add((s, m))
            for t in adj.get(m, ()):
                pairs.add((s, t))
    return pairs


def evaluate(repo, pytest_args):
    with contextlib.redirect_stdout(io.StringIO()):
        b = CKGBuilder(repo)
        b.build()
    S = static_edges(b)
    D, pytest_line = run_trace(repo, pytest_args)
    Dset = set(D)
    # only compare dynamic edges whose endpoints exist as nodes in the static graph
    Dset = {(u, v) for u, v in Dset if u in b.graph and v in b.graph}
    executed_callers = {u for u, _ in Dset}
    S_cov = {(u, v) for u, v in S if u in executed_callers}
    tp = S_cov & Dset
    recall = len(S & Dset) / len(Dset) if Dset else 0.0
    precision = len(tp) / len(S_cov) if S_cov else 0.0

    # polymorphic / ambiguous disambiguation accuracy
    name_defs = {}
    for n, d in b.graph.nodes(data=True):
        if d.get("type") == "function":
            name_defs.setdefault(d.get("name"), []).append(n)
    ambiguous = [(u, v) for u, v in Dset if len(name_defs.get(b.graph.nodes[v].get("name"), [])) >= 2]
    amb_correct = sum(1 for e in ambiguous if e in S)
    amb_attempted = sum(1 for u, v in ambiguous if any(
        b.graph.nodes[t].get("name") == b.graph.nodes[v].get("name") for s, t in S if s == u))

    # 2-hop relation F1
    S2 = two_hop(S, executed_callers)
    D2 = two_hop(Dset, executed_callers)
    S2 = {(u, v) for u, v in S2 if u in executed_callers}
    inter = S2 & D2
    p2 = len(inter) / len(S2) if S2 else 0.0
    r2 = len(inter) / len(D2) if D2 else 0.0
    f1 = 2 * p2 * r2 / (p2 + r2) if (p2 + r2) else 0.0

    return {
        "repo": os.path.basename(repo.rstrip("/")),
        "pytest": pytest_line,
        "loc": b.total_loc,
        "static_internal_call_edges": len(S),
        "dynamic_call_edges": len(Dset),
        "executed_callers": len(executed_callers),
        "edge_recall": round(recall, 4),
        "edge_precision_lower_bound": round(precision, 4),
        "ambiguous_dynamic_calls": len(ambiguous),
        "disambiguation_accuracy": round(amb_correct / len(ambiguous), 4) if ambiguous else None,
        "disambiguation_attempted_pct": round(amb_attempted / len(ambiguous), 4) if ambiguous else None,
        "multihop2_precision": round(p2, 4),
        "multihop2_recall": round(r2, 4),
        "multihop2_f1": round(f1, 4),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pytest-args", default="tests")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    report = evaluate(os.path.abspath(args.repo), args.pytest_args)
    print(json.dumps(report, indent=2))
    if args.out:
        json.dump(report, open(args.out, "w"), indent=2)


if __name__ == "__main__":
    main()
