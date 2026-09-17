"""Polyglot definition-of-done probe. Each key in the output is a known gap; re-run after fixes.
See POLYGLOT_DEFINITION_OF_DONE.md for the expected values once each item is closed."""
import os, sys, io, contextlib, tempfile, json, inspect
import networkx as nx
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from graft_ckg import CKGBuilder
def build(root):
    with contextlib.redirect_stdout(io.StringIO()):
        b = CKGBuilder(root); b.build()
    return b
def fixture(files):
    d = tempfile.mkdtemp()
    for p, c in files.items():
        fp = os.path.join(d, p); os.makedirs(os.path.dirname(fp), exist_ok=True); open(fp, 'w').write(c)
    return d
def rels(b, prefix, layers=None):
    return sorted(set((d['relation'], u.split(':',1)[-1], v.split(':',1)[-1]) for u,v,d in b.graph.edges(data=True) if u.startswith(prefix) and (layers is None or d.get('layer') in layers)))
R = {}
js = fixture({
 'protos/s.proto': 'syntax = "proto3";\nservice ShippingService { rpc GetQuote(Q) returns (Q) {} }\nmessage Q {}\n',
 'a/util.js': "export function helper(x){ return x; }\nexport class Store { save(){ } }\n",
 'a/app.js': ("import {\n  helper,\n} from './util';\nimport { Store } from './util';\nconst grpc = require('@grpc/grpc-js');\n"
              "export const handler = async (req, res) => {\n  const s = new Store();\n  s.save();\n  return helper(req);\n};\n"
              "const obj = { run() { return helper(1); } };\n"
              "function plain(){ const s2 = new Store(); s2.save(); return helper(2); }\n"
              "const client = new shopProto.ShippingService('addr', grpc.credentials.createInsecure());\n"
              "function quote(){ client.getQuote({}, (err, r) => {}); }\n")})
b = build(js)
R['js_function_or_class_nodes'] = sorted(n.split(':',1)[-1] for n in b.graph.nodes if n.startswith('a/app.js') and b.graph.nodes[n]['type'] in ('function','class'))
R['js_imports_of_app'] = sorted(v for u,v,d in b.graph.edges(data=True) if d.get('relation')=='IMPORTS' and u=='file::a/app.js')
R['js_calls'] = rels(b, 'a/app.js', {'Ldep'})
R['js_grpc_consumes'] = [(u,v) for u,v,d in b.graph.edges(data=True) if d.get('relation')=='CONSUMES']
tsx = fixture({'c.tsx': "export function View(props: {n: number}) {\n  const x = props.n;\n  return <div className=\"a\">{x}</div>;\n}\nexport function helper2(a: number): number { return a; }\nexport interface Props { n: number }\nexport abstract class Base { abstract go(): void; }\n"})
b = build(tsx)
R['tsx_nodes'] = sorted(n.split(':',1)[-1] for n in b.graph.nodes if n.startswith('c.tsx') and 'var::' not in n and 'return::' not in n)
go = fixture({'m/main.go': 'package main\n\ntype Runner interface { Run() }\ntype impl struct{ n int }\nfunc (i *impl) Run() { i.n = 2 }\nfunc use(r Runner) { r.Run() }\nfunc main() {\n\tvar x = 1\n\tx = 2\n\ty := 3\n\ty = 4\n\tuse(&impl{})\n}\n',
              'm/other.go': 'package main\n\nfunc (i *impl) Run2() { i.Run() }\n'})
b = build(go)
R['go_flow_dep'] = rels(b, 'm/', {'Lflow','Ldep'})
py = fixture({'p/a.py': 'class Svc:\n    def run(self):\n        return 1\n\nclass Other:\n    def run(self):\n        return 2\n\ndef f(a, /, b, *, c=1, **kw):\n    s = Svc()\n    s.run()\n    x, y = 1, 2\n    for i in range(3):\n        pass\n    total = x + y + i\n    return total\n\nf(1, 2)\n'})
b = build(py)
R['py_flow_dep_of_f'] = rels(b, 'p/a.py:f', {'Lflow','Ldep'})
ob_path = '/home/user/work/repos/online-boutique'
if not os.path.isdir(ob_path):
    ob_path = fixture({
        'genproto/demo.pb.go': 'package genproto\n',
        'demo_pb2.py': '"""Generated pb2 docstring."""\nclass Demo:\n    pass\n',
        'server.go': 'package main\nfunc Serve() {}\n',
        'main.py': '"""Main service entry."""\ndef main():\n    pass\n',
    })
b = build(ob_path)
gen = [n for n in b.graph.nodes if any(t in n for t in ('_pb2','.pb.go','genproto','_grpc_pb','_pb.js'))]
lsem_total = sum(1 for u,v,d in b.graph.edges(data=True) if d.get('layer')=='Lsem')
lsem_gen = sum(1 for u,v,d in b.graph.edges(data=True) if d.get('layer')=='Lsem' and any(t in u for t in ('_pb2','genproto')))
pct = round(100*len(gen)/b.graph.number_of_nodes(), 1) if b.graph.number_of_nodes() else 0.0
R['ob_generated_code'] = dict(generated_nodes=len(gen), total_nodes=b.graph.number_of_nodes(), pct=pct, lsem_edges_from_generated=lsem_gen, lsem_total=lsem_total)
tomli_path = '/home/user/work/repos/tomli'
if not os.path.isdir(tomli_path):
    tomli_path = fixture({
        'a.py': 'def foo():\n    return 1\n',
        'b.py': 'import a\ndef bar():\n    return a.foo()\n',
    })
b1 = build(tomli_path); b2 = build(tomli_path)
R['deterministic_edge_ids_same_fs'] = {k:(u,v,d['relation']) for u,v,k,d in b1.graph.edges(keys=True,data=True)} == {k:(u,v,d['relation']) for u,v,k,d in b2.graph.edges(keys=True,data=True)}
R['os_walk_sorted_in_build'] = 'sorted(' in inspect.getsource(CKGBuilder.build)
r1 = fixture({'src/main.go': 'package main\nfunc main(){ helper() }\nfunc helper(){}\n'})
r2 = fixture({'src/main.go': 'package main\nfunc main(){ other() }\nfunc other(){}\n'})
g1, g2 = build(r1).graph, build(r2).graph
R['multi_repo_id_collision'] = dict(repo1_nodes=g1.number_of_nodes(), repo2_nodes=g2.number_of_nodes(), naive_union=nx.compose(g1,g2).number_of_nodes(), colliding_ids=sorted(set(g1.nodes)&set(g2.nodes)))
r3 = fixture({'svcA/handlers.py': 'def process(x):\n    return x\n', 'svcB/worker.py': 'def run():\n    return process(1)\n'})
b = build(r3)
R['cross_service_name_leak'] = [(u,v) for u,v,d in b.graph.edges(data=True) if d.get('relation')=='CALLS' and 'svcB' in u and 'svcA' in v]
print(json.dumps(R, indent=1))
