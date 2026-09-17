"""
GRAFT-CKG Visualizer & Multi-Format Exporter
Supports:
  1. Local Interactive Browser Viewer (Pyvis HTML) with physics & search
  2. Web-based Visualizers (Gephi Lite, Cosmograph, Graphistry):
     - Exports Cytoscape/Cosmograph Node-Link JSON (repo_graph.json)
     - Exports CSV tables (repo_nodes.csv, repo_edges.csv)
     - Reads standard GraphML (repo_graph.graphml)
"""

import os
import sys
import json
import csv
import argparse
import networkx as nx
from pyvis.network import Network


def export_and_visualize(graphml_path="repo_graph.graphml", output_html="graph_viewer.html", include_vars=False):
    if not os.path.exists(graphml_path):
        print(f"[!] Error: Graph file '{graphml_path}' not found. Run graft_ckg.py first.")
        return

    print(f"[*] Loading CKG from: {graphml_path}...")
    G = nx.read_graphml(graphml_path)
    total_nodes = G.number_of_nodes()
    total_edges = G.number_of_edges()
    print(f"[*] Loaded Graph: {total_nodes} nodes, {total_edges} edges.")

    # -------------------------------------------------------------
    # 1. EXPORT FOR WEB VISUALIZERS (Cosmograph, Graphistry, Gephi Lite)
    # -------------------------------------------------------------
    base_name = os.path.splitext(graphml_path)[0]
    json_path = f"{base_name}.json"
    nodes_csv = f"{base_name}_nodes.csv"
    edges_csv = f"{base_name}_edges.csv"

    # Export Node-Link JSON
    node_data_list = []
    for nid, d in G.nodes(data=True):
        node_data_list.append({
            "id": str(nid),
            "label": d.get("name") or str(nid).split(":")[-1],
            "type": d.get("type", "unknown"),
            "layer": d.get("layer", "Lsyn"),
            "file": d.get("file", ""),
            "line": d.get("line_no", 0),
        })

    edge_data_list = []
    for u, v, d in G.edges(data=True):
        edge_data_list.append({
            "source": str(u),
            "target": str(v),
            "relation": d.get("relation", "UNKNOWN"),
            "layer": d.get("layer", "Lsyn"),
            "scope": d.get("d_scope", 0),
            "confidence": d.get("c_type", 1.0),
        })

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"nodes": node_data_list, "edges": edge_data_list}, f, indent=2)
    print(f"[*] Exported Web-Ready JSON: {json_path}")

    # Export CSVs (Standard for Cosmograph & Graphistry)
    with open(nodes_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "label", "type", "layer", "file", "line"])
        writer.writeheader()
        writer.writerows(node_data_list)

    with open(edges_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["source", "target", "relation", "layer", "scope", "confidence"])
        writer.writeheader()
        writer.writerows(edge_data_list)
    print(f"[*] Exported CSV Tables: {nodes_csv}, {edges_csv}")

    # -------------------------------------------------------------
    # 2. GENERATE LOCAL INTERACTIVE HTML VIEWER (Pyvis)
    # -------------------------------------------------------------
    # For large graphs (>1000 nodes), filter out granular local variable nodes
    # to maintain 60 FPS in standard browser DOM, unless explicitly asked.
    display_G = G
    if total_nodes > 1000 and not include_vars:
        print(f"[*] Large repository detected ({total_nodes} nodes).")
        print(f"    Filtering out local variable nodes for high-speed browser rendering...")
        sub_nodes = [n for n, d in G.nodes(data=True) if d.get("type") != "variable"]
        display_G = G.subgraph(sub_nodes)
        print(f"    Browser view focused on {display_G.number_of_nodes()} core architectural symbols.")
    
    net = Network(height="850px", width="100%", bgcolor="#1a1a1a", font_color="white", directed=True)
    net.force_atlas_2based()

    # Add Nodes
    for node_id, data in display_G.nodes(data=True):
        ntype = data.get("type", "unknown")
        name = data.get("name") or node_id.split(":")[-1]
        
        # Color palette by entity type
        color = "#97c2fc"
        size = 15
        if ntype == "file":
            color = "#ff9f43"   # Orange
            size = 28
        elif ntype == "class":
            color = "#2ecc71"   # Emerald Green
            size = 22
        elif ntype == "function":
            color = "#a29bfe"   # Purple-Blue
            size = 18
        elif ntype == "variable":
            color = "#feca57"   # Yellow
            size = 10
        elif ntype == "module":
            color = "#00cec9"   # Cyan
            size = 25

        tooltip = f"<b>{node_id}</b><br>Type: {ntype}<br>Layer: {data.get('layer', '')}<br>File: {data.get('file', '')}"
        net.add_node(node_id, label=name, title=tooltip, color=color, size=size)

    # Add Edges
    for u, v, data in display_G.edges(data=True):
        rel = data.get("relation", "REL")
        layer = data.get("layer", "Lsyn")
        scope = data.get("d_scope", 0)
        conf = data.get("c_type", 1.0)

        # Color palette by topological layer
        edge_color = "#57606f"
        if layer == "Lsyn":
            edge_color = "#54a0ff"  # Blue (Syntactic Containment)
        elif layer == "Ldep":
            edge_color = "#ff6b6b"  # Coral Red (Call Graph & Imports)
        elif layer == "Lflow":
            edge_color = "#1dd1a1"  # Mint Green (Dataflow Def-Use)
        elif layer == "Lsem":
            edge_color = "#f368e0"  # Magenta (Semantic Similarity)

        tooltip = f"Layer: {layer}<br>Relation: {rel}<br>Scope: {scope}<br>Conf: {conf}"
        net.add_edge(u, v, title=tooltip, label=rel if total_nodes < 200 else "", color=edge_color)

    # Enable interaction & physics buttons
    net.set_options("""
    var options = {
      "nodes": {
        "font": { "size": 14, "face": "tahoma", "color": "#ffffff" }
      },
      "edges": {
        "smooth": { "type": "continuous", "forceDirection": "none" }
      },
      "physics": {
        "barnesHut": {
          "gravitationalConstant": -3000,
          "centralGravity": 0.3,
          "springLength": 95,
          "springConstant": 0.04
        },
        "minVelocity": 0.75
      }
    }
    """)

    net.save_graph(output_html)
    print(f"[*] Saved Interactive Local Viewer: {output_html}")
    print("\n" + "="*70)
    print("HOW TO VIEW YOUR CODE KNOWLEDGE GRAPH:")
    print("="*70)
    print("1. Local Browser: Open 'graph_viewer.html' in Chrome/Edge/Firefox.")
    print("2. Gephi Lite (Web): Go to https://gephi.org/gephi-lite/ and drag & drop 'repo_graph.graphml'.")
    print("3. Cosmograph (Web GPU): Go to https://cosmograph.app/run and drag & drop 'repo_graph.json'.")
    print("4. Graphistry: Upload 'repo_nodes.csv' and 'repo_edges.csv' to Graphistry Cloud.")
    print("="*70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GRAFT-CKG Visualizer & Exporter")
    parser.add_argument("graphml", nargs="?", default="repo_graph.graphml", help="Path to input .graphml file")
    parser.add_argument("--output", default="graph_viewer.html", help="Path to output .html file")
    parser.add_argument("--full", action="store_true", help="Include all low-level variable nodes in browser HTML view")
    args = parser.parse_args()

    export_and_visualize(args.graphml, args.output, include_vars=args.full)
