"""Regression tests for the Contract Layer (Lcontract): protobuf/gRPC bindings.

Fixture: a tiny polyglot repo with one .proto, a Python servicer + stub caller,
a Go server registration + client call, and a JS addService handler map.
"""

from __future__ import annotations

import io
import contextlib
import tempfile
import unittest
from pathlib import Path

from graft_ckg import CKGBuilder


PROTO = """
syntax = "proto3";
package shop;
message Empty {}
message Quote { int32 cost = 1; }
message ShipReq { string id = 1; }
message ShipResp { string tracking = 1; }
service ShippingService {
    rpc GetQuote(Empty) returns (Quote) {}
    rpc ShipOrder(ShipReq) returns (ShipResp) {}
}
service EmailService {
    rpc SendOrderConfirmation(Empty) returns (Empty) {}
}
"""

PY_SERVER = """
import demo_pb2_grpc

class EmailService(demo_pb2_grpc.EmailServiceServicer):
    def SendOrderConfirmation(self, request, context):
        return request
"""

PY_CLIENT = """
import demo_pb2_grpc

def notify(channel):
    stub.SendOrderConfirmation(None)

if __name__ == "__main__":
    stub = demo_pb2_grpc.EmailServiceStub(None)
"""

GO_SERVER = """
package main

import (
\t"context"
\tpb "example.com/genproto"
)

type server struct{}

func (s *server) GetQuote(ctx context.Context, in *pb.Empty) (*pb.Quote, error) {
\treturn nil, nil
}

func (s *server) ShipOrder(ctx context.Context, in *pb.ShipReq) (*pb.ShipResp, error) {
\treturn nil, nil
}

func main() {
\tsvc := &server{}
\tpb.RegisterShippingServiceServer(nil, svc)
}

func (s *server) quote(ctx context.Context) {
\tq, _ := pb.NewShippingServiceClient(nil).
\t\tGetQuote(ctx, &pb.Empty{})
\t_ = q
}
"""

JS_SERVER = """
const grpc = require('@grpc/grpc-js');
function shipOrder(call, callback) { callback(null, {}); }
function main() {
  const server = new grpc.Server();
  server.addService(shopProto.ShippingService.service, {shipOrder});
}
"""


class ContractLayerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "protos").mkdir()
        (root / "protos" / "demo.proto").write_text(PROTO, encoding="utf-8")
        (root / "email").mkdir()
        (root / "email" / "server.py").write_text(PY_SERVER, encoding="utf-8")
        (root / "email" / "client.py").write_text(PY_CLIENT, encoding="utf-8")
        (root / "shipping").mkdir()
        (root / "shipping" / "main.go").write_text(GO_SERVER, encoding="utf-8")
        (root / "ship_js").mkdir()
        (root / "ship_js" / "server.js").write_text(JS_SERVER, encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            self.builder = CKGBuilder(root)
            self.graph = self.builder.build()

    def tearDown(self):
        self.tmp.cleanup()

    def _edges(self, relation):
        return {(u, v) for u, v, d in self.graph.edges(data=True) if d.get("relation") == relation}

    def test_proto_parsed_into_canonical_contract_nodes(self):
        self.assertIn("contract::ShippingService", self.graph)
        self.assertIn("contract::ShippingService.GetQuote", self.graph)
        # brace-balanced parsing must capture BOTH rpcs, not only the first
        self.assertIn("contract::ShippingService.ShipOrder", self.graph)
        self.assertEqual(self.graph.nodes["contract::ShippingService.GetQuote"]["request_type"], "Empty")

    def test_python_servicer_implements_rpc(self):
        impl = self._edges("IMPLEMENTS")
        self.assertIn(("email/server.py:EmailService.SendOrderConfirmation",
                       "contract::EmailService.SendOrderConfirmation"), impl)

    def test_python_stub_bound_after_use_is_resolved(self):
        cons = self._edges("CONSUMES")
        self.assertIn(("email/client.py:notify", "contract::EmailService.SendOrderConfirmation"), cons)

    def test_go_register_server_binds_all_rpc_methods(self):
        impl = self._edges("IMPLEMENTS")
        self.assertIn(("shipping/main.go:server.GetQuote", "contract::ShippingService.GetQuote"), impl)
        self.assertIn(("shipping/main.go:server.ShipOrder", "contract::ShippingService.ShipOrder"), impl)

    def test_go_multiline_chained_client_call_is_consumer(self):
        cons = self._edges("CONSUMES")
        self.assertIn(("shipping/main.go:server.quote", "contract::ShippingService.GetQuote"), cons)

    def test_js_add_service_shorthand_handler_binds(self):
        impl = self._edges("IMPLEMENTS")
        self.assertIn(("ship_js/server.js:shipOrder", "contract::ShippingService.ShipOrder"), impl)

    def test_who_consumes_and_who_implements_queries(self):
        c = self.builder.who_consumes("ShippingService.GetQuote")
        self.assertEqual([x["consumer"] for x in c["consumers"]], ["shipping/main.go:server.quote"])
        i = self.builder.who_implements("EmailService")
        self.assertTrue(any("EmailService.SendOrderConfirmation" in x["implementer"] for x in i["implementers"]))


if __name__ == "__main__":
    unittest.main()
