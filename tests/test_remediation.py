"""Unit tests for SCKG Self-Healing & Automated Remediation Engine (Objective 3 / Phase 4).

Tests the 5-Stage Closed Loop:
  1. Diagnose: contract breaking skews and dangling consumers
  2. Attribute: causal root attribution to contract divergence
  3. Generate Patch: unified diff synthesis for call sites
  4. Validate: AST syntax check + CKG graph verification
  5. Propose: human-in-the-loop remediation proposal
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from remediation import DefectType, SCKGSelfHealingEngine


class SCKGSelfHealingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        # Copy examples/polyglot_system to temp_dir
        src_repo = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "examples",
            "polyglot_system",
        )
        self.repo_copy = os.path.join(self.temp_dir, "polyglot_system")
        shutil.copytree(src_repo, self.repo_copy)

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_clean_repo_has_zero_defects(self):
        engine = SCKGSelfHealingEngine(self.repo_copy)
        defects = engine.diagnose()
        # Clean repo should have 0 breaking defects
        critical_defects = [d for d in defects if d.get("severity") == "CRITICAL"]
        self.assertEqual(len(critical_defects), 0)

    def test_5_stage_remediation_on_contract_skew(self):
        # 1. Mutate proto contract: rename GetQuote -> GetQuoteV2
        proto_path = os.path.join(self.repo_copy, "protos", "shipping.proto")
        with open(proto_path, "r", encoding="utf-8") as f:
            proto_content = f.read()

        mutated_proto = proto_content.replace("rpc GetQuote(", "rpc GetQuoteV2(")
        with open(proto_path, "w", encoding="utf-8") as f:
            f.write(mutated_proto)

        # 2. Stage 1: Diagnose
        engine = SCKGSelfHealingEngine(self.repo_copy)
        defects = engine.diagnose()
        self.assertGreaterEqual(len(defects), 1)

        skew = next((d for d in defects if d["type"] == DefectType.RENAMED_RPC), None)
        self.assertIsNotNone(skew)
        self.assertEqual(skew["called_method"], "GetQuote")
        self.assertIn("GetQuoteV2", skew["valid_rpcs"])

        # 3. Stage 2: Attribute Causal Root
        attributed = engine.attribute_causal_root(skew)
        c_root = attributed["causal_root"]
        self.assertEqual(c_root["target_contract_rpc"], "GetQuoteV2")
        self.assertGreaterEqual(c_root["confidence"], 0.85)

        # 4. Stage 3: Generate Patch
        patch = engine.generate_patch(attributed)
        self.assertIsNotNone(patch)
        self.assertIn("order_service.py", patch["file"])
        self.assertIn("GetQuoteV2", patch["patched_code"])
        self.assertIn("-    stub.GetQuote(None)", patch["unified_diff"])
        self.assertIn("+    stub.GetQuoteV2(None)", patch["unified_diff"])

        # 5. Stage 4: Validate
        val = engine.validate(patch)
        self.assertTrue(val["valid"])
        self.assertEqual(val["ast_syntax_status"], "PASS")
        self.assertEqual(val["ckg_consistency"], "VERIFIED")

        # 6. Stage 5: Propose
        proposal = engine.propose(attributed, patch, val)
        self.assertEqual(proposal["status"], "READY_FOR_HUMAN_REVIEW")
        self.assertEqual(proposal["defect_type"], DefectType.RENAMED_RPC)
        self.assertIn("order_service.py", proposal["file"])
        self.assertIn("GetQuoteV2", proposal["unified_diff"])

        # 7. Apply patch and verify self-healing resolution
        res = engine.remediate_all(apply=True)
        self.assertTrue(res["applied"])
        self.assertGreaterEqual(len(res["applied_files"]), 1)

        # Re-build graph on repaired repository
        healed_engine = SCKGSelfHealingEngine(self.repo_copy)
        remaining_defects = healed_engine.diagnose()
        remaining_critical = [d for d in remaining_defects if d.get("severity") == "CRITICAL"]
        self.assertEqual(len(remaining_critical), 0)

        # Verify the repaired call now successfully binds to GetQuoteV2 via CONSUMES
        consumers = healed_engine.traversal.downstream_consumers("ShippingService.GetQuoteV2")
        self.assertGreaterEqual(consumers["consumer_count"], 1)


if __name__ == "__main__":
    unittest.main()
