"""Unit tests for SCKG Graph-Guided Traversal Engine (Objective 2).

Tests:
  - blast_radius calculation across microservices and layers
  - downstream_consumers contract query
  - contract_of reverse symbol resolution
  - deterministic_subtree_graft context budgeting and formatting
"""

from __future__ import annotations

import os
import unittest

from traversal import SCKGTraversal


class SCKGTraversalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Use existing polyglot microservice showcase repo
        cls.repo_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples", "polyglot_system")
        cls.traversal = SCKGTraversal(cls.repo_dir)

    def test_blast_radius_computation(self):
        # Blast radius from handle_checkout should reach frontend and shipping_go
        res = self.traversal.blast_radius("handle_checkout", max_depth=2, direction="both")
        self.assertNotIn("error", res)
        self.assertGreater(res["total_impacted_nodes"], 5)
        self.assertGreater(res["total_traversed_edges"], 5)
        self.assertGreater(res["impact_score"], 10.0)

        # Verify affected services include Python order service and Go shipping service
        services = set(res["affected_services"])
        self.assertTrue(any("order" in s for s in services))
        self.assertTrue(any("shipping" in s.lower() for s in services))

        # Verify affected files
        files = res["affected_files"]
        self.assertTrue(any("order_service.py" in f for f in files))

    def test_downstream_consumers_query(self):
        # Query consumers of ShippingService
        res = self.traversal.downstream_consumers("ShippingService")
        self.assertNotIn("error", res)
        self.assertEqual(res["resolved_contract_node"], "contract::ShippingService")
        self.assertGreaterEqual(res["consumer_count"], 1)

        consumer = res["consumers"][0]
        self.assertEqual(consumer["caller_name"], "handle_checkout")
        self.assertIn("order_service.py", consumer["file"])
        self.assertEqual(consumer["target_contract"], "contract::ShippingService.GetQuote")

    def test_downstream_consumers_specific_rpc(self):
        # Query consumers of specific RPC GetQuote
        res = self.traversal.downstream_consumers("ShippingService.GetQuote")
        self.assertNotIn("error", res)
        self.assertGreaterEqual(res["consumer_count"], 1)
        self.assertIn("order_service.py", res["consumers"][0]["file"])

    def test_contract_of_mapping(self):
        # Reverse mapping: What contract does handle_checkout touch?
        res = self.traversal.contract_of("handle_checkout")
        self.assertNotIn("error", res)
        self.assertTrue(res["is_contract_bound"])
        self.assertGreaterEqual(len(res["consumes"]), 1)
        self.assertEqual(res["consumes"][0]["contract_node"], "contract::ShippingService.GetQuote")

    def test_deterministic_subtree_grafting(self):
        # Test LLM prompt context extraction
        graft = self.traversal.deterministic_subtree_graft("handle_checkout", max_tokens=1024)
        self.assertNotIn("error", graft)
        self.assertGreater(graft["node_count"], 2)
        self.assertLessEqual(graft["approx_tokens"], 1024)

        context_text = graft["grafted_context"]
        self.assertIn("TARGET FOCUS", context_text)
        self.assertIn("handle_checkout", context_text)
        self.assertIn("contract::ShippingService.GetQuote", context_text)


if __name__ == "__main__":
    unittest.main()
