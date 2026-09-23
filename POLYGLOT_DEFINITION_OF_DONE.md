# Polyglot single-repo layer — Definition of Done (before multi-repo)

Status as of 2026-09-11 (v2). Every ❌ below was **reproduced** with `python bench/probe_polyglot_gaps.py`
(fixtures are inside the script; re-run it after each fix — the item is closed when the probe output changes as noted).

## A. Per-language extraction matrix

| Capability | Python | JS/TS | Go | Java / C# | Probe key |
|---|---|---|---|---|---|
| Files / classes / functions / methods | ✅ | ⚠️ `function_declaration`, `method_definition`, class only — **arrow functions (`const f = () => {}`) are not nodes**; TS `interface`, `abstract class`, `enum`, `type` not nodes | ✅ funcs, receiver methods, struct/interface types | ❌ not parsed (Online Boutique: adservice, cartservice invisible) | `js_function_or_class_nodes` (expect `handler` present), `tsx_nodes` (expect `Props`, `Base`) |
| Imports | ✅ absolute/relative/alias | ⚠️ regex-based; **multi-line `import {\n a,\n} from` is missed**; re-exports / dynamic `import()` missed | ⚠️ regex on quoted lines | ❌ | `js_imports_of_app` (expect `a.util.helper`) |
| Calls: same-file / imported / `self` / `super` | ✅ | ✅ (`this.`→`self.`) | ✅ | ❌ | — |
| Calls: constructor (`Foo()` / `new Foo()` / `&T{}`) | ✅ (`CALLS`→class) | ❌ `new_expression` ignored | ❌ composite literal not linked | ❌ | `js_calls` (expect `plain→Store`) |
| Calls: `obj.method()` with **inferred receiver type** | ❌ resolves only if the method name is unique in the whole repo (measured disambiguation acc. 2–10 %) | ❌ same | ❌ same; Go interface dispatch not modelled | ❌ | `py_flow_dep_of_f` (expect `f→Svc.run`, not `:s.run`) |
| Calls at module level (`if __name__…`, top-level scripts, Node entrypoints) | ❌ dropped | ❌ dropped (incl. bodies of module-level arrow functions) | n/a | — | — |
| Parameters → `PASSES_ARG` | ⚠️ only `args.args`; **posonly, kw-only, `*args`, `**kw` missing** | ⚠️ destructured `({a,b})` and `...rest` missing | ✅ | ❌ | `py_flow_dep_of_f` (expect vars `a`,`c`,`kw`) |
| Local defs → `DEFINES` | ⚠️ `Assign`/`AnnAssign` only; **tuple-unpacking, `for`, `with`, walrus missing** | ✅ `const/let/var` in functions | ⚠️ `:=` only; **`var x =` missing** | ❌ | `py_flow_dep_of_f` (expect `x`,`y`,`i`) |
| `USES` | ⚠️ same scope only (no closures) | ✅ | ✅ | ❌ | — |
| `MUTATES` | ❌ none (no `AugAssign`, `self.x =`, `list.append`) | ✅ identifier targets | ❌ `assignment_statement` not handled | ❌ | `go_flow_dep` (expect `MUTATES main→x,y`) |
| `RETURNS` / `FLOWS_TO` (intra-procedural) | ❌ none | ✅ | ✅ | ❌ | `py_flow_dep_of_f` (expect `RETURNS`) |
| Inter-procedural flow (arg→param, return→assignment) | ❌ | ❌ | ❌ | ❌ | — |
| Docstrings → `Lsem` | ✅ | ❌ JSDoc / Go doc comments not captured | ❌ | ❌ | — |
| HTTP route indexing (server side) | ✅ Flask-style literal decorators | ❌ Express `app.get('/x', h)` not indexed | ❌ `http.HandleFunc`, gin/mux not indexed | ❌ | — |
| HTTP call sites (client side) | ❌ `requests.get(...)` not indexed | ✅ literal `fetch`/`axios` | ❌ `http.Get` not indexed | ❌ | — |
| gRPC producers → `IMPLEMENTS` | ✅ servicer + subclasses | ✅ `addService` pair + shorthand | ✅ `Register*Server` + `new()`/`&T{}`/cross-file | ❌ | Online Boutique P=R=1.0 |
| gRPC consumers → `CONSUMES` | ✅ stub vars anywhere in file | ❌ `client.getQuote()` on `new pkg.Service(...)` not bound | ✅ chained + client vars | ❌ | `js_grpc_consumes` (expect 1 edge) |
| REST/OpenAPI contracts (Train-Ticket, Sock Shop) | ❌ | ❌ | ❌ | ❌ | — |

## B. Cross-cutting single-repo blockers (independent of language)

| # | Blocker | Evidence (probe key) | Fix |
|---|---|---|---|
| B1 | **Generated code is indexed as first-class code.** In Online Boutique 5 851 / 7 639 nodes (76.6 %) come from `*_pb2*.py`, `*.pb.go`, `genproto/`; **100 % of the 1 450 Lsem edges** come from generated docstrings. Table-I node/edge counts are therefore noise. | `ob_generated_code` | Ignore policy: `.ckgignore` globs + defaults (`*_pb2*.py`, `*.pb.go`, `*_grpc_pb.js`, `genproto/`, `vendor/`, `dist/`, `build/`, `node_modules/`, `venv/`); mark generated nodes `generated=True` instead of dropping if needed for binding |
| B2 | **Name-based fallback creates false cross-service edges.** `svcB/worker.py:run → svcA/handlers.py:process` was linked purely because `process` is unique in the tree. In a microservice monorepo this fabricates cross-service `CALLS` that do not exist (only contracts cross services). | `cross_service_name_leak` | Scope the unique-name fallback to a *resolution unit* (Python package / Go package / JS package.json dir / service dir); cross-unit edges only via imports or `Lcontract` |
| B3 | **No receiver-type inference / class-hierarchy analysis; Eq. (4) not implemented** (`c_type` = 1/k). Measured disambiguation 2–10 %, recall 0.13–0.48 on OO repos. | `py_flow_dep_of_f` | Report §7.1 design: type facts from constructors/annotations/`self.attr`, `_find_method` + overriding subclasses, softmax(cos/τ) |
| B4 | Retrieval index covers only docstring'd nodes (16–30 % of functions) | report D1 | Index name tokens + docstring + code for every function/class |
| B5 | No incremental update; full rebuild only | report D8 | `update_files()` with per-file fact partitioning; verify equality vs. from-scratch build |
| B6 | `os.walk` order not sorted → edge ids / tie-breaks may differ across machines | `os_walk_sorted_in_build=false` | `sorted()` dirs and files; golden-file test on `examples/` |
| B7 | No golden-file regression for real repos (only unit fixtures) | — | Snapshot node/edge counts per layer for `examples/mixed_service` and a pinned Online Boutique commit; fail CI on drift > 0 |
| B8 | No packaging / CI | — | `pyproject.toml`, `pip install -e .`, GitHub Actions running `unittest` + probe + `eval_polyglot.py` |
| B9 | Memory: full source stored on every node (`code` attr) | — | Store line ranges; slice on demand |

## C. Multi-repo prerequisites that must be designed **now** (they change node identity)

| # | Requirement | Evidence | Design |
|---|---|---|---|
| C1 | **Repo-namespaced node ids.** Two repos with `src/main.go:main` collapse when graphs are unioned (6 nodes → 4). | `multi_repo_id_collision` | id = `<repo_id>/<rel_path>:<qualname>`; `id_ctx` = `<repo_id>/<rel_path>`; `file::<repo_id>/<rel_path>`. Do this in single-repo mode with `repo_id` = repo directory name so nothing changes later |
| C2 | **Canonical contract nodes across repos.** The same `.proto` is vendored in every service repo; `contract::Service.Rpc` must be one node keyed by `<proto package>.<Service>.<Rpc>`, with `DEFINED_IN` edges to each copy and a content hash per copy → contract-skew detection (fault class: schema divergence) | design | key by proto `package` + name; store `sha256(proto text)`; emit `CONTRACT_SKEW` when copies differ |
| C3 | **Resolution scope = repo (or package).** Name-based/unique fallbacks must never cross a repo; cross-repo edges only via `Lcontract` (gRPC/REST) and explicit package imports | `cross_service_name_leak` | same mechanism as B2 |
| C4 | **Workspace manifest** listing repos, commit, language hints, proto dirs, ignore globs | — | `workspace.yaml`; `graft_ckg.py --workspace workspace.yaml` builds each repo and unions |
| C5 | **Persistence + merge** (per-repo GraphML/JSON → Neo4j loader), incremental per-repo refresh | Tracker Wk 5/14 | build → serialise → `MERGE` by id; contract nodes shared |
| C6 | **Contract versioning across commits** (Layer 3 lineage hook) | Phase 2 | contract node ↔ commit sha of the proto copy |

## D. Recommended order (single repo first, as requested)

1. **B1 ignore policy + B6 sorted walk + B7 golden files** (½ day) — makes every later number trustworthy.
2. **C1 repo-namespaced ids + B2/C3 resolution scoping** (1 day) — cheap now, painful after multi-repo.
3. **JS/TS completeness**: arrow functions, `new`, multi-line imports, TS declarations, Express routes, JS gRPC consumers (2 days). Re-run probe until `js_*` keys flip.
4. **Python/Go flow completeness**: params, tuple/for/with defs, `MUTATES`, `RETURNS`, Go `var`/assignment (1 day).
5. **B3 receiver-type inference + CHA + Eq. (4)** (3–4 days) — re-run `bench/trace_oracle.py`; target disambiguation ≥ 0.6 and recall ≥ 0.6 on click/jinja.
6. **B4 retrieval index + B5 incremental build** (2–3 days).
7. Java adapter (tree-sitter-java) — required for adservice and for Train-Ticket before multi-service evaluation; C# for cartservice is optional.
8. Then multi-repo: C2 canonical contracts + skew detection, C4 manifest, C5 persistence.

Exit criterion for "polyglot single-repo done": probe keys all at expected values; `bench/trace_oracle.py` recall ≥ 0.6 and precision ≥ 0.8 on ≥ 3 OO repos; contract P/R ≥ 0.85/0.80 on Online Boutique **with generated code excluded**; golden-file tests green; `python -m unittest` green.
