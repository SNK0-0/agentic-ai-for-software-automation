# Implementation status

## v2.1 (Single-Repo Polyglot Layer Complete) — All Gaps Closed

All architectural requirements and capabilities for single-repository polyglot CKG construction are fully implemented and verified:
- **JS/TS Completeness**:
  - Arrow functions (`arrow_function`) and function expressions in variable declarators registered as first-class function nodes.
  - Constructor calls (`new Store()`) extracted via `new_expression` into `Ldep` `CALLS` edges.
  - Multi-line ES import statements (`import {\n  helper,\n} from './util'`) extracted into `Ldep` `IMPORTS` edges.
  - TypeScript declarations (`interface`, `abstract class`, `enum`, `type`) registered as syntactic `Lsyn` nodes.
  - JS gRPC client consumer detection (`client.getQuote(...)` via `grpc_client_vars`) linked to protobuf RPC contracts (`Lcontract`).
- **Go AST & Dataflow Completeness**:
  - Package/local `var` specifications (`var x = 1`) registered as `DEFINES` edges.
  - Assignment statements (`x = 2`) registered as `MUTATES` edges.
- **Python AST & Dataflow Completeness**:
  - Parameter extraction for `posonlyargs`, `kwonlyargs`, `*args` (`vararg`), and `**kwargs` (`kwarg`) as `PASSES_ARG`.
  - Recursive tuple and list assignment target unpacking (`x, y = 1, 2`) as `DEFINES` edges.
  - Loop variable extraction (`for i in ...`) and context manager extraction (`with ... as f`) as `DEFINES` edges.
  - Augmented assignment (`x += 1`) as `MUTATES` edges.
  - Return statements mapped to `RETURNS` and intra-procedural `FLOWS_TO` edges.
- **Class-Hierarchy Analysis (CHA), Receiver-Type Inference & Eq. (4) Polymorphic Dispatch**:
  - Local constructor type tracking (`s = Svc()`) enables receiver-type inference (`s.run()` resolves directly to `Svc.run`).
  - Hierarchical subclass traversal (`_find_all_implementations`) finds all overriding method targets.
  - Softmax polymorphic confidence computation with temperature $\tau=0.2$ ($c_{type}$ metadata) according to Eq. (4).
- **Cross-Service Resolution Scoping**:
  - Resolution units scoped by service/package directory; prevents false name-based cross-service fallback leaks.
- **Ignore Policy & Cleanliness**:
  - Default ignore patterns and `.ckgignore` file parsing filter generated protobuf/gRPC code (`*_pb2*.py`, `*.pb.go`, `genproto/`, `vendor/`, `node_modules/`, `venv/`).
  - Deterministic sorted `os.walk` ensures consistent graph construction across runs and environments.
- **Incremental Streaming Updates**:
  - `update_files(changed_rel_paths, deleted_rel_paths)` selectively removes stale nodes/edges and re-parses modified files.
- **Full-Text RAG Retrieval Index**:
  - Indexes all functions, classes, modules, and contracts (tokens + docstrings + code snippets) for hybrid TF-IDF and BM25 retrieval.
- **Verification**:
  - 24/24 unit tests passing (`python -m unittest discover -s tests -v`).
  - Definition-of-Done probe `bench/probe_polyglot_gaps.py` fully verified against all expected keys.
  - End-to-end polyglot integration `eval_polyglot.py` passes (2/2 HTTP bindings recovered).

## v2 (2026-09-11 audit) — what changed

- **Go adapter** (tree-sitter-go): functions, receiver methods (`Type.Method` ids), struct/interface types, short-var defs, imports.
- **Contract Layer `Lcontract`** (Inter-Service Contract Layer): `.proto` files are parsed into canonical
  `contract::Service` / `contract::Service.Rpc` nodes (`DEFINES_RPC`, request/response types). Producers bind via
  `IMPLEMENTS` (Python `*Servicer` subclasses incl. template subclasses, Go `Register<Svc>Server` + impl type incl.
  `new(T)` / `&T{}` / cross-file types, JS `addService` pair + shorthand handlers). Consumers bind via `CONSUMES`
  (Python `*Stub` variables bound anywhere in the file, Go `New<Svc>Client(...).<Rpc>` incl. multi-line chains and
  client variables). Measured **P = R = 1.00** on 21 labelled consumer + 7 producer bindings in Online Boutique.
- **Queries**: `who_consumes(Service[.Rpc])`, `who_implements(...)`, CLI `--who-consumes/--who-implements`.
- **Benchmarks** (`bench/`): real-repo throughput + contract P/R (`run_benchmark.py`), dynamic-trace construction
  accuracy oracle (`trace_oracle.py`), contract fault injection + blast radius (`inject_contract_fault.py`),
  hand labels (`labels/`), all measured outputs (`results/`, summarised in `RESULTS.md`).
- **Tests**: 16/16 (7 new in `tests/test_contract_layer.py`).
- BM25 lexical index kept alongside TF-IDF as a retrieval baseline.

## Known limits after v2 (see GRAFT-CKG_Assessment_and_Gap_Report.md §3, §7)

- No receiver-type inference / class-hierarchy analysis; Eq. (4) confidence not implemented → measured
  disambiguation accuracy 2–10 %, call-edge recall 0.13–0.82 depending on repo.
- Retrieval index covers only docstring'd nodes; token-redundancy metric is not a redundancy measure.
- No incremental (streaming) construction; no Layer 3 lineage; no Layer 4 telemetry; no Neo4j persistence;
  no Java / C# adapters (AdService, CartService unparsed); OpenAPI/REST contracts not modelled.
- Table II of Paper1 has not been measured on real data.

---

## v1 (upstream) status

Audited against upstream commit `1c566173e06d7e9a43cdfb247d15ba4c2acceda3`.

## Completed correctness fixes

- Relative Python imports are normalized before call resolution.
- Import aliases are resolved before any global name fallback.
- `self.method()` and `super().method()` follow explicitly declared base classes.
- GraphML/GEXF export creates its output directory automatically.
- Regression tests cover Python inheritance, JavaScript inheritance/imports, and
  TypeScript imports, JavaScript return/mutation flow, and literal local API
  links.

The stricter resolver may report fewer internal call bindings than the previous
implementation. This is intentional: ambiguous name matches are now left
unresolved rather than linked to an unrelated function.

## First polyglot slice

JavaScript, JSX, TypeScript, and TSX are parsed with Tree-sitter and emitted
into the same NetworkX graph schema as Python. The supported common vocabulary
is `CONTAINS`, `IMPORTS`, `EXTENDS`, `CALLS`, `PASSES_ARG`, `DEFINES`, `USES`,
`MUTATES`, `RETURNS`, `FLOWS_TO`, and `HTTP_CALLS`. Relative ES-module imports,
destructured/aliased CommonJS `require()` imports, TypeScript `paths` aliases,
and local workspace package entrypoints are resolved. Literal JS/TS `fetch()`
or Axios calls are linked to literal Flask-style Python handlers when their
HTTP method and path agree. `examples/mixed_service` plus `eval_polyglot.py`
is a frozen, reproducible end-to-end check for those bindings.

## Remaining limits

- JavaScript/TypeScript resolve local alias and workspace configuration only.
  External `node_modules`, package-manager workspaces, wildcard/conditional
  exports, and non-relative CommonJS packages are intentionally left external.
- JavaScript/TypeScript flow covers parameters, local definitions/uses,
  assignments/updates, and direct returns. Closure, alias, object-property,
  and inter-procedural flow remain future work.
- API linking is intentionally limited to literal paths and methods. Dynamic
  URLs, templates, router composition, proxies, authentication middleware, and
  runtime route registration are not inferred.
- The bundled retrieval evaluation uses a generated toy repository. It is not a
  real-repository benchmark and should not be presented as one.
- Semantic links use TF-IDF over Python docstrings. They are lexical similarity,
  not neural embeddings or language-model reasoning.

## Next priority order

1. Build a frozen, independently labelled real-repository evaluation set.
2. Expand API linking to configurable router frameworks and route prefixes.
3. Add external package and conditional-export resolution where project policy
   allows dependency traversal.
4. Add a third language through the same adapter contract.
