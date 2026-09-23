"""
GRAFT-CKG Evaluation & Benchmark Suite
Implements the experimental protocol and metrics defined in:
  1. GRAFT-CKG Paper (Table I & Table II):
     - Extraction Latency (s/kLOC)
     - MRR@5, Hits@1, Hits@5, Context Recall@10
     - Token Redundancy / Prompt Bloat Reduction
     - Multi-hop & Layer Ablation Analysis
  2. Multi-Layer Extraction & Traversal Verification:
     - Layer-wise node & edge extraction validation (Lsyn, Ldep, Lflow, Lsem)
     - Adjacency & Traversal API verification (Blast radius, callers, def-use)
"""

import os
import sys
import time
import math
import json
import unittest
from graft_ckg import CKGBuilder, HyperEdgeMetadata


# =====================================================================
# 1. SYNTHETIC GROUND-TRUTH TESTBENCH REPOSITORY
# =====================================================================
def create_ground_truth_testbench(base_dir):
    """
    Creates a controlled multi-file benchmark repository with known
    syntactic hierarchy, cross-file calls, dataflow chains, and docstrings.
    """
    test_dir = os.path.join(base_dir, "benchmark_repo")
    os.makedirs(test_dir, exist_ok=True)

    # File 1: core_service.py
    code_core = '''"""Core service handling transaction lifecycle and calculations."""

def validate_amount(amount):
    """Validates if payment amount is positive and within limit."""
    if amount <= 0:
        return False
    is_valid = amount < 10000
    return is_valid

def process_transaction(user_id, amount):
    """Processes financial transaction and computes tax."""
    valid = validate_amount(amount)
    if not valid:
        return None
    tax_rate = 0.05
    final_amount = amount + (amount * tax_rate)
    return final_amount
'''

    # File 2: payment_gateway.py
    code_payment = '''"""Payment gateway integrating transactions with bank settlement."""
import core_service

class PaymentGateway:
    """Enterprise payment gateway client."""

    def __init__(self, gateway_id):
        """Initializes the payment gateway client."""
        self.gateway_id = gateway_id
        self.log = []

    def execute_payment(self, customer_id, raw_amount):
        """Executes payment by coordinating with the core service."""
        total = core_service.process_transaction(customer_id, raw_amount)
        if total is not None:
            self.log.append(total)
        return total

    def settle_batch(self, batch_items):
        """Settles transaction batch and computes total revenue."""
        total_payout = 0.0
        for item in batch_items:
            total_payout = total_payout + item
        return total_payout
'''

    # File 3: reporting.py
    code_report = '''"""Audit reporting module for financial transactions."""
import payment_gateway

def generate_audit_report(gateway, items):
    """Generates financial transaction report for auditing."""
    settlement = gateway.settle_batch(items)
    summary = f"Settled: {settlement}"
    return summary
'''

    with open(os.path.join(test_dir, "core_service.py"), "w", encoding="utf-8") as f:
        f.write(code_core)
    with open(os.path.join(test_dir, "payment_gateway.py"), "w", encoding="utf-8") as f:
        f.write(code_payment)
    with open(os.path.join(test_dir, "reporting.py"), "w", encoding="utf-8") as f:
        f.write(code_report)

    return test_dir


# =====================================================================
# 2. UNIT VERIFICATION SUITE (Structural & Layer Testing)
# =====================================================================
class TestCKGLayers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch_dir = os.path.dirname(os.path.abspath(__file__))
        cls.test_repo = create_ground_truth_testbench(cls.scratch_dir)
        cls.builder = CKGBuilder(cls.test_repo)
        cls.graph = cls.builder.build()

    def test_layer1_syntactic_containment(self):
        """Checks Lsyn: Files contain Classes, Classes contain Methods."""
        # Check files exist
        self.assertTrue(self.graph.has_node("file::core_service.py"))
        self.assertTrue(self.graph.has_node("file::payment_gateway.py"))

        # Check class containment
        class_node = "payment_gateway.py:PaymentGateway"
        self.assertTrue(self.graph.has_node(class_node))
        in_edges = [u for u, v, d in self.graph.in_edges(class_node, data=True) if d.get("layer") == "Lsyn"]
        self.assertIn("file::payment_gateway.py", in_edges)

        # Check method containment
        method_node = "payment_gateway.py:PaymentGateway.execute_payment"
        self.assertTrue(self.graph.has_node(method_node))
        method_parents = [u for u, v, d in self.graph.in_edges(method_node, data=True) if d.get("layer") == "Lsyn"]
        self.assertIn(class_node, method_parents)

    def test_layer2_cross_file_dependencies(self):
        """Checks Ldep: Cross-file imports and call resolutions."""
        # execute_payment calls core_service.process_transaction
        caller = "payment_gateway.py:PaymentGateway.execute_payment"
        callee = "core_service.py:process_transaction"
        
        calls = [v for u, v, d in self.graph.out_edges(caller, data=True) if d.get("layer") == "Ldep" and d.get("relation") == "CALLS"]
        self.assertIn(callee, calls, f"Expected {caller} to CALL {callee} across files!")

        # process_transaction calls validate_amount (intra-file)
        intra_calls = [v for u, v, d in self.graph.out_edges(callee, data=True) if d.get("layer") == "Ldep"]
        self.assertIn("core_service.py:validate_amount", intra_calls)

    def test_layer3_dataflow_def_use(self):
        """Checks Lflow: Variable definitions and load uses."""
        func_node = "core_service.py:validate_amount"
        dataflow_edges = [(d.get("relation"), v) for u, v, d in self.graph.out_edges(func_node, data=True) if d.get("layer") == "Lflow"]
        
        relations = [r for r, v in dataflow_edges]
        # Must have PASSES_ARG for amount
        self.assertIn("PASSES_ARG", relations)
        # Must have DEFINES for is_valid
        self.assertIn("DEFINES", relations)
        # Must have USES for is_valid
        self.assertIn("USES", relations)

    def test_layer4_semantic_docstrings(self):
        """Checks Lsem: Docstrings indexed and semantic similarity edges formed."""
        func1 = "core_service.py:process_transaction"
        self.assertIn(func1, self.builder.docstrings)
        self.assertIn("financial transaction", self.builder.docstrings[func1].lower())

    def test_hyper_edge_metadata(self):
        """Checks that edges carry 4D hyper-edge vector: m = [d_scope, f_call, c_type, id_ctx]."""
        for u, v, d in self.graph.edges(data=True):
            self.assertIn("d_scope", d)
            self.assertIn("f_call", d)
            self.assertIn("c_type", d)
            self.assertIn("id_ctx", d)
            self.assertGreaterEqual(d["d_scope"], 0)
            self.assertGreaterEqual(d["c_type"], 0.0)
            self.assertLessEqual(d["c_type"], 1.0)


# =====================================================================
# 3. BENCHMARK & EVALUATION RUNNER (Metrics as per Table I & II)
# =====================================================================
def run_benchmark_evaluation(repo_path):
    print("\n" + "="*75)
    print("           GRAFT-CKG DOWNSTREAM RETRIEVAL & GRAPH BENCHMARK")
    print("="*75)

    # 1. Extraction Throughput Metric (Table I)
    t0 = time.time()
    builder = CKGBuilder(repo_path)
    graph = builder.build()
    duration = time.time() - t0

    kloc = builder.total_loc / 1000.0 if builder.total_loc > 0 else 0.001
    extraction_latency_per_kloc = duration / kloc

    print(f"\n[METRIC 1] Graph Construction Throughput (Table I):")
    print(f"  * Total LOC:                  {builder.total_loc}")
    print(f"  * Elapsed Time:               {duration:.4f} seconds")
    print(f"  * Throughput Latency:         {extraction_latency_per_kloc:.4f} s/kLOC")

    # 2. Define Ground-Truth Queries for Downstream RAG Evaluation (Table II)
    test_queries = [
        {
            "query": "validate payment amount within limit",
            "expected_target": "core_service.py:validate_amount",
            "required_dependencies": ["core_service.py:validate_amount.var::amount"],
        },
        {
            "query": "process financial transaction and compute tax",
            "expected_target": "core_service.py:process_transaction",
            "required_dependencies": ["core_service.py:validate_amount"],
        },
        {
            "query": "execute payment with payment gateway",
            "expected_target": "payment_gateway.py:PaymentGateway.execute_payment",
            "required_dependencies": ["core_service.py:process_transaction"],
        },
        {
            "query": "settle transaction batch and calculate total payout",
            "expected_target": "payment_gateway.py:PaymentGateway.settle_batch",
            "required_dependencies": ["payment_gateway.py:PaymentGateway.settle_batch.var::batch_items"],
        },
        {
            "query": "generate audit report for financial settlement",
            "expected_target": "reporting.py:generate_audit_report",
            "required_dependencies": ["payment_gateway.py:PaymentGateway.settle_batch"],
        },
    ]

    hits_1 = 0
    hits_5 = 0
    mrr_5 = 0.0
    context_recalls = []
    total_raw_tokens = 0
    total_grafted_tokens = 0

    print(f"\n[METRIC 2] Downstream Retrieval & Subtree Grafting Quality (Table II):")
    for item in test_queries:
        q = item["query"]
        expected = item["expected_target"]
        required_deps = item["required_dependencies"]

        rag_res = builder.rag_query(q, top_k=5, graft_subgraph=True)
        retrieved_ids = [m["node_id"] for m in rag_res.get("top_matches", [])]

        # Rank of target
        rank = None
        for i, rid in enumerate(retrieved_ids):
            if rid == expected or expected in rid:
                rank = i + 1
                break

        if rank == 1:
            hits_1 += 1
        if rank is not None and rank <= 5:
            hits_5 += 1
            mrr_5 += 1.0 / rank

        # Context Recall: Check if grafted execution subgraph contains required dependencies
        grafted = rag_res.get("grafted_execution_context", {})
        grafted_nodes = set([rel["target"] for rel in grafted.get("relations", [])] + [grafted.get("root", "")])
        
        found_deps = sum(1 for dep in required_deps if any(dep in gn for gn in grafted_nodes))
        recall = found_deps / len(required_deps) if required_deps else 1.0
        context_recalls.append(recall)

        # Token Redundancy comparison: Full file size vs grafted slice size (char len / 4 approx)
        full_file_tokens = builder.total_loc * 8  # approx 8 tokens per line of code
        grafted_tokens = len(grafted.get("grafted_code_context", "")) // 4
        total_raw_tokens += full_file_tokens
        total_grafted_tokens += grafted_tokens

        print(f"  * Query: '{q}'")
        print(f"    - Target: {expected} | Retrieved Rank: {rank if rank else 'Not in Top-5'}")
        print(f"    - Context Recall: {recall * 100:.1f}%")

    num_q = len(test_queries)
    hits_1_pct = (hits_1 / num_q) * 100.0
    hits_5_pct = (hits_5 / num_q) * 100.0
    final_mrr = mrr_5 / num_q
    avg_recall = (sum(context_recalls) / num_q) * 100.0
    token_redundancy = (total_grafted_tokens / total_raw_tokens) * 100.0 if total_raw_tokens > 0 else 0.0

    print("\n" + "-"*75)
    print("                    FINAL BENCHMARK SCORECARD")
    print("-"*75)
    print(f"  {'Metric':<35} | {'Measured Score':<15} | {'Paper Reference'}")
    print(f"  {'-'*35}-+-{'-'*15}-+-{'-'*20}")
    print(f"  {'MRR@5 (Mean Reciprocal Rank)':<35} | {final_mrr:<15.3f} | Table II (0.894)")
    print(f"  {'Hits@1 Accuracy (%)':<35} | {hits_1_pct:<14.1f}% | Table II (84.7%)")
    print(f"  {'Hits@5 Accuracy (%)':<35} | {hits_5_pct:<14.1f}% | Table II (96.5%)")
    print(f"  {'Context Recall@10 (%)':<35} | {avg_recall:<14.1f}% | Table II (95.2%)")
    print(f"  {'Token Context Ratio (%)':<35} | {token_redundancy:<14.1f}% | Table II (14.8% Redundancy)")
    print(f"  {'Extraction Latency (s/kLOC)':<35} | {extraction_latency_per_kloc:<15.4f} | Table I  (0.042-0.055)")
    print("-"*75)

    return {
        "mrr_5": final_mrr,
        "hits_1": hits_1_pct,
        "hits_5": hits_5_pct,
        "context_recall": avg_recall,
        "extraction_latency_per_kloc": extraction_latency_per_kloc,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="GRAFT-CKG Evaluation & Benchmark Suite")
    parser.add_argument("--repo", help="Path to a custom Python repository to evaluate")
    args = parser.parse_args()

    scratch_dir = os.path.dirname(os.path.abspath(__file__))

    if args.repo:
        target_repo = os.path.abspath(args.repo)
        print(f"\n[*] Evaluating Custom Repository: {target_repo}")
        
        # 1. Health Verification
        builder = CKGBuilder(target_repo)
        builder.build()
        v_report = builder.verify_graph()
        print("\n" + "="*75)
        print("          REPOSITORY GRAPH HEALTH & LAYER VERIFICATION")
        print("="*75)
        print(json.dumps(v_report, indent=2))
        
        # 2. Performance Metric
        kloc = builder.total_loc / 1000.0 if builder.total_loc > 0 else 0.001
        print("\n" + "="*75)
        print("                 REPOSITORY PERFORMANCE METRICS")
        print("="*75)
        print(f"  * Total LOC:                  {builder.total_loc}")
        print(f"  * Total CKG Nodes:            {builder.graph.number_of_nodes()}")
        print(f"  * Total Hyper-Edges:          {builder.graph.number_of_edges()}")
        print(f"  * Call Resolution Rate:       {v_report['call_graph_resolution_rate_pct']}%")
        print(f"  * Docstring Coverage:         {v_report['docstring_coverage_pct']}%")
        print(f"  * Status:                     {v_report['health_status']}")
        print("="*75)
    else:
        bench_repo = create_ground_truth_testbench(scratch_dir)

        print("\n[STEP 1] Running Automated Unit & Layer Verification...")
        suite = unittest.TestLoader().loadTestsFromTestCase(TestCKGLayers)
        runner = unittest.TextTestRunner(verbosity=2)
        test_result = runner.run(suite)

        if test_result.wasSuccessful():
            print("\n[STEP 2] Running Downstream Retrieval & Grafting Benchmark...")
            run_benchmark_evaluation(bench_repo)
        else:
            print("\n[!] Unit tests failed. Skipping downstream benchmark.")
