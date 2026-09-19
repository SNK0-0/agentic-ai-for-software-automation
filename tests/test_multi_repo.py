"""
Comprehensive Unit & Integration Test Suite for GRAFT-CKG Multi-Repository Phase.
"""

import os
import sys
import tempfile
import unittest
import networkx as nx

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from graft_ckg import CKGBuilder
from multi_repo import WorkspaceConfig, WorkspaceCKGBuilder, RepoConfig
from traversal import SCKGTraversal
from remediation import SCKGSelfHealingEngine, DefectType


class MultiRepoCKGTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def _create_file(self, rel_path: str, content: str) -> str:
        full_path = os.path.join(self.temp_dir, rel_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        return full_path

    def test_workspace_config_parsing(self):
        """Test parsing workspace manifest from YAML string and file."""
        yaml_content = """name: "test_workspace"
version: "2.1.0"
repositories:
  - id: "auth_svc"
    path: "./services/auth"
    language: "go"
  - id: "billing_svc"
    path: "./services/billing"
    language: "python"
shared_contracts:
  - "./contracts"
ignore_patterns:
  - "*.test.ts"
"""
        manifest_path = self._create_file("workspace.yaml", yaml_content)
        config = WorkspaceConfig.from_file(manifest_path)
        self.assertEqual(config.name, "test_workspace")
        self.assertEqual(config.version, "2.1.0")
        self.assertEqual(len(config.repositories), 2)
        self.assertEqual(config.repositories[0].id, "auth_svc")
        self.assertEqual(config.repositories[1].id, "billing_svc")
        self.assertEqual(config.shared_contracts, ["./contracts"])
        self.assertEqual(config.ignore_patterns, ["*.test.ts"])

    def test_multi_repo_zero_id_collisions(self):
        """Requirement C1: Two repositories with identical file names must produce 0 collisions."""
        repo1_dir = os.path.join(self.temp_dir, "repo1")
        repo2_dir = os.path.join(self.temp_dir, "repo2")

        # Create identical files in both repos
        for r_dir in (repo1_dir, repo2_dir):
            main_go = os.path.join(r_dir, "src", "main.go")
            os.makedirs(os.path.dirname(main_go), exist_ok=True)
            with open(main_go, "w", encoding="utf-8") as f:
                f.write("""package main
func main(){ helper() }
func helper(){}
""")

        builder1 = CKGBuilder(repo1_dir, repo_id="repo1")
        builder1.build()

        builder2 = CKGBuilder(repo2_dir, repo_id="repo2")
        builder2.build()

        colliding_nodes = set(builder1.graph.nodes) & set(builder2.graph.nodes)
        self.assertEqual(
            colliding_nodes, set(),
            f"Expected 0 node ID collisions between namespaced repositories, got: {colliding_nodes}"
        )

        # Confirm exact node union count without loss
        n1 = builder1.graph.number_of_nodes()
        n2 = builder2.graph.number_of_nodes()
        union_graph = nx.compose(builder1.graph, builder2.graph)
        self.assertEqual(union_graph.number_of_nodes(), n1 + n2)

        # Confirm node IDs contain repo_id prefix
        self.assertIn("file::repo1/src/main.go", builder1.graph.nodes)
        self.assertIn("repo1/src/main.go:main", builder1.graph.nodes)
        self.assertIn("file::repo2/src/main.go", builder2.graph.nodes)
        self.assertIn("repo2/src/main.go:main", builder2.graph.nodes)

    def test_canonical_contract_unification(self):
        """Requirement C2: Protobuf contracts vendored across repos unify into single canonical nodes."""
        proto_content = """syntax = "proto3";
package payment;
service PaymentService {
  rpc Charge(ChargeReq) returns (ChargeResp);
}
"""
        self._create_file("services/billing/protos/payment.proto", proto_content)
        self._create_file("services/billing/billing.py", """import payment_pb2_grpc
class BillingService(payment_pb2_grpc.PaymentServiceServicer):
    def Charge(self, req):
        return {"status": "ok"}
""")

        self._create_file("services/checkout/protos/payment.proto", proto_content)
        self._create_file("services/checkout/checkout.py", """import payment_pb2_grpc
stub = payment_pb2_grpc.PaymentServiceStub(None)
def execute():
    return stub.Charge(None)
""")

        ws_yaml = """name: "payment_system"
version: "1.0"
repositories:
  - id: "billing_service"
    path: "./services/billing"
  - id: "checkout_service"
    path: "./services/checkout"
"""
        ws_path = self._create_file("workspace.yaml", ws_yaml)
        ws_builder = WorkspaceCKGBuilder(ws_path)
        ws_builder.build()

        # Check canonical contract nodes
        self.assertIn("contract::PaymentService", ws_builder.graph.nodes)
        self.assertIn("contract::PaymentService.Charge", ws_builder.graph.nodes)

        # Only ONE canonical service node should exist in graph
        service_nodes = [n for n, d in ws_builder.graph.nodes(data=True) if d.get("type") == "service_contract"]
        self.assertEqual(service_nodes, ["contract::PaymentService"])

        # Definitions list contains both repositories
        service_data = ws_builder.graph.nodes["contract::PaymentService"]
        definitions = service_data.get("definitions", [])
        repos_found = {d["repo_id"] for d in definitions}
        self.assertEqual(repos_found, {"billing_service", "checkout_service"})

        # Check bindings
        implements_edges = [(u, v) for u, v, d in ws_builder.graph.edges(data=True) if d.get("relation") == "IMPLEMENTS"]
        consumes_edges = [(u, v) for u, v, d in ws_builder.graph.edges(data=True) if d.get("relation") == "CONSUMES"]
        self.assertTrue(any(v == "contract::PaymentService.Charge" for _, v in implements_edges))
        self.assertTrue(any(v == "contract::PaymentService.Charge" for _, v in consumes_edges))

        # No contract skew detected
        self.assertEqual(len(ws_builder.contract_skews), 0)

    def test_contract_skew_detection_across_repos(self):
        """Requirement C2: Divergent vendored .proto copies trigger CONTRACT_SKEW."""
        proto_v1 = """syntax = "proto3";
package order;
service OrderService {
  rpc CreateOrder(OrderReqV1) returns (OrderResp);
}
"""
        proto_v2 = """syntax = "proto3";
package order;
service OrderService {
  rpc CreateOrder(OrderReqV2) returns (OrderResp);
}
"""
        self._create_file("services/order_backend/protos/order.proto", proto_v1)
        self._create_file("services/order_backend/main.py", """import order_pb2_grpc
class OrderHandler(order_pb2_grpc.OrderServiceServicer):
    def CreateOrder(self, req):
        return {}
""")

        self._create_file("services/api_gateway/protos/order.proto", proto_v2)
        self._create_file("services/api_gateway/main.py", """import order_pb2_grpc
stub = order_pb2_grpc.OrderServiceStub(None)
def route():
    return stub.CreateOrder(None)
""")

        ws_yaml = """name: "skewed_system"
version: "1.0"
repositories:
  - id: "order_backend"
    path: "./services/order_backend"
  - id: "api_gateway"
    path: "./services/api_gateway"
"""
        ws_path = self._create_file("workspace.yaml", ws_yaml)
        ws_builder = WorkspaceCKGBuilder(ws_path)
        ws_builder.build()

        # Assert contract skew was detected
        self.assertGreaterEqual(len(ws_builder.contract_skews), 1)
        skew = ws_builder.contract_skews[0]
        self.assertEqual(skew["type"], "CONTRACT_SKEW")
        self.assertEqual(skew["service"], "OrderService")
        self.assertIn("order_backend", skew["versions"])
        self.assertIn("api_gateway", skew["versions"])
        # Hashes must be different
        self.assertNotEqual(skew["versions"]["order_backend"], skew["versions"]["api_gateway"])

        # Also verify that Self-Healing Engine diagnose detects this skew
        healing_engine = SCKGSelfHealingEngine(ws_builder)
        diagnosed_defects = healing_engine.diagnose()
        skew_defects = [d for d in diagnosed_defects if d["type"] == DefectType.CONTRACT_SKEW]
        self.assertEqual(len(skew_defects), 1)
        self.assertEqual(skew_defects[0]["severity"], "CRITICAL")

    def test_cross_repo_http_resolution(self):
        """Requirement C3: Frontend TypeScript calls Backend Python REST routes across repos."""
        self._create_file("services/py_api/api.py", """class App:
    def post(self, path):
        return lambda f: f

app = App()

@app.post("/api/v1/auth")
def authenticate():
    return {"token": "secret"}
""")

        self._create_file("services/ts_ui/src/login.ts", """export async function login(creds: any) {
  const res = await fetch("/api/v1/auth", { method: "POST" });
  return res.json();
}
""")

        ws_yaml = """name: "http_system"
version: "1.0"
repositories:
  - id: "py_api"
    path: "./services/py_api"
  - id: "ts_ui"
    path: "./services/ts_ui"
"""
        ws_path = self._create_file("workspace.yaml", ws_yaml)
        ws_builder = WorkspaceCKGBuilder(ws_path)
        ws_builder.build()

        http_edges = [
            (u, v) for u, v, d in ws_builder.graph.edges(data=True)
            if d.get("relation") == "HTTP_CALLS"
        ]
        self.assertGreaterEqual(len(http_edges), 1)
        caller, target = http_edges[0]
        self.assertIn("ts_ui", caller)
        self.assertIn("py_api", target)
        self.assertIn("authenticate", target)

    def test_cross_repo_traversal_blast_radius(self):
        """Requirement C3: Blast radius traverses across service boundaries."""
        ws_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "examples", "multi_repo_system", "workspace.yaml"
        )
        if not os.path.isfile(ws_path):
            self.skipTest("Showcase fixture workspace.yaml not found.")

        traversal = SCKGTraversal(ws_path)
        res = traversal.blast_radius("contract::ShippingService.GetQuote", max_depth=3)
        self.assertNotIn("error", res)
        self.assertGreater(res["impact_score"], 0)
        self.assertGreater(res["total_impacted_nodes"], 5)
        # Should affect multiple services
        self.assertGreaterEqual(len(res["affected_services"]), 2)


if __name__ == "__main__":
    unittest.main()
