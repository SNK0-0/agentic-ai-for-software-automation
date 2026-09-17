"""
GRAFT-CKG Clean Light-Mode Workbench & N-Hop Visualizer
Features:
  1. Pure White / Clean Slate Background: Crisp, modern light aesthetic.
  2. Circular Nodes: All nodes are clean circular dots with high-fidelity layer coloring.
  3. Directed Edges: Clean, thin directed arrows showing call and dependency flows.
  4. Static Topology: Physics stabilizes once and locks completely (zero drifting).
  5. Bottom-Right Pop-up Window: Clicking any node pops up a dedicated floating inspector in the bottom right.
  6. Visual Shading & Isolation: Deeply dims or isolates everything outside the N-hop query path.
"""

import os
import sys
import json
import webbrowser
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from graft_ckg import CKGBuilder

GLOBAL_BUILDER = None
REPO_NAME = ""


def nhop_retrieval(builder, root_id, max_hops=1, layers=None, direction="both"):
    """
    Performs BFS n-hop adjacency retrieval starting from root_id.
    """
    if root_id not in builder.graph:
        candidates = [n for n in builder.graph.nodes if root_id in n]
        if not candidates:
            return {"error": f"Symbol '{root_id}' not found in graph."}
        root_id = candidates[0]

    visited_nodes = {root_id: 0}
    retrieved_edges = []
    queue = [(root_id, 0)]

    while queue:
        curr, depth = queue.pop(0)
        if depth >= max_hops:
            continue

        # Outward edges
        if direction in ("out", "both"):
            for _, target, key, data in builder.graph.out_edges(curr, keys=True, data=True):
                l = data.get("layer")
                if layers and l not in layers:
                    continue
                retrieved_edges.append({
                    "id": str(key),
                    "source": curr,
                    "target": target,
                    "relation": data.get("relation"),
                    "layer": l,
                    "direction": "out",
                    "hop": depth + 1,
                    "scope": data.get("d_scope", 0),
                    "confidence": data.get("c_type", 1.0)
                })
                if target not in visited_nodes or visited_nodes[target] > depth + 1:
                    visited_nodes[target] = depth + 1
                    queue.append((target, depth + 1))

        # Inward edges
        if direction in ("in", "both"):
            for source, _, key, data in builder.graph.in_edges(curr, keys=True, data=True):
                l = data.get("layer")
                if layers and l not in layers:
                    continue
                retrieved_edges.append({
                    "id": str(key),
                    "source": source,
                    "target": curr,
                    "relation": data.get("relation"),
                    "layer": l,
                    "direction": "in",
                    "hop": depth + 1,
                    "scope": data.get("d_scope", 0),
                    "confidence": data.get("c_type", 1.0)
                })
                if source not in visited_nodes or visited_nodes[source] > depth + 1:
                    visited_nodes[source] = depth + 1
                    queue.append((source, depth + 1))

    nodes_payload = []
    for nid, hop in visited_nodes.items():
        ndata = builder.graph.nodes[nid]
        nodes_payload.append({
            "id": nid,
            "name": ndata.get("name") or nid.split(":")[-1],
            "type": ndata.get("type", "unknown"),
            "layer": ndata.get("layer", "Lsyn"),
            "file": ndata.get("file", ""),
            "line": ndata.get("line_no", 0),
            "hop": hop,
            "docstring": ndata.get("docstring", ""),
            "code": ndata.get("code", "")
        })

    return {
        "root_id": root_id,
        "max_hops": max_hops,
        "direction": direction,
        "total_nodes": len(nodes_payload),
        "total_edges": len(retrieved_edges),
        "nodes": sorted(nodes_payload, key=lambda x: (x["hop"], x["name"])),
        "edges": retrieved_edges
    }


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>GRAFT-CKG Explorer</title>
  <script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
  <style>
    :root {
      --bg: #ffffff;
      --sidebar-bg: #f8fafc;
      --panel-bg: #ffffff;
      --border: #e2e8f0;
      --border-focus: #cbd5e1;
      --text-main: #0f172a;
      --text-muted: #64748b;
      --text-dim: #94a3b8;
      --accent: #2563eb;
      --accent-hover: #1d4ed8;

      --lsyn: #0284c7;
      --ldep: #e11d48;
      --lflow: #059669;
      --lsem: #9333ea;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", sans-serif; }
    body { background: var(--bg); color: var(--text-main); display: flex; height: 100vh; overflow: hidden; font-size: 13px; }

    /* Left Control Sidebar */
    #sidebar {
      width: 400px;
      background: var(--sidebar-bg);
      border-right: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      z-index: 10;
    }

    .top-brand {
      padding: 14px 18px;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
      background: #ffffff;
    }
    .brand-title { font-size: 13px; font-weight: 700; letter-spacing: 0.5px; color: var(--text-main); font-family: ui-monospace, monospace; }
    .brand-repo { font-size: 11px; color: var(--text-muted); font-family: ui-monospace, monospace; background: #f1f5f9; padding: 3px 8px; border-radius: 4px; border: 1px solid var(--border); }

    /* Query Config Section */
    .control-section { padding: 14px 18px; border-bottom: 1px solid var(--border); display: flex; flex-direction: column; gap: 12px; background: #ffffff; }

    .segmented-tabs {
      display: flex;
      background: #f1f5f9;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 2px;
    }
    .segment-btn {
      flex: 1;
      padding: 6px 10px;
      background: transparent;
      border: none;
      color: var(--text-muted);
      font-size: 12px;
      font-weight: 500;
      cursor: pointer;
      border-radius: 4px;
      transition: all 0.15s ease;
    }
    .segment-btn.active {
      background: #ffffff;
      color: var(--text-main);
      font-weight: 600;
      box-shadow: 0 1px 2px rgba(0,0,0,0.05);
    }

    .search-row { display: flex; gap: 8px; }
    .input-field {
      flex: 1;
      background: #ffffff;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 8px 12px;
      color: var(--text-main);
      font-size: 12px;
      font-family: ui-monospace, monospace;
      outline: none;
    }
    .input-field:focus { border-color: var(--accent); box-shadow: 0 0 0 2px rgba(37,99,235,0.1); }
    .primary-btn {
      background: var(--accent);
      color: #fff;
      border: none;
      border-radius: 6px;
      padding: 8px 15px;
      font-weight: 600;
      font-size: 12px;
      cursor: pointer;
      transition: background 0.15s;
    }
    .primary-btn:hover { background: var(--accent-hover); }

    /* Filters & Options Grid */
    .filter-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; font-size: 11px; color: var(--text-muted); }
    .filter-item { display: flex; align-items: center; gap: 6px; }
    .filter-item select {
      background: #ffffff;
      border: 1px solid var(--border);
      color: var(--text-main);
      padding: 4px 6px;
      border-radius: 4px;
      font-size: 11px;
      outline: none;
    }

    .layer-toggles { display: flex; gap: 6px; flex-wrap: wrap; }
    .layer-chip {
      display: flex;
      align-items: center;
      gap: 5px;
      font-size: 11px;
      padding: 3px 8px;
      border-radius: 4px;
      background: #f1f5f9;
      border: 1px solid var(--border);
      color: var(--text-muted);
      cursor: pointer;
      user-select: none;
    }
    .layer-chip.active { color: var(--text-main); border-color: var(--text-muted); background: #ffffff; }

    .sample-row { display: flex; gap: 6px; flex-wrap: wrap; }
    .quick-chip {
      font-size: 11px;
      font-family: ui-monospace, monospace;
      background: #f1f5f9;
      border: 1px solid var(--border);
      padding: 2px 7px;
      border-radius: 4px;
      color: var(--text-muted);
      cursor: pointer;
    }
    .quick-chip:hover { border-color: var(--accent); color: var(--accent); background: #ffffff; }

    /* Results Inspector Panel */
    #inspector-panel {
      flex: 1;
      overflow-y: auto;
      padding: 14px 18px;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .empty-state { text-align: center; color: var(--text-dim); margin-top: 60px; font-size: 12px; line-height: 1.6; }

    .summary-card {
      background: #ffffff;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 12px;
      box-shadow: 0 1px 3px rgba(0,0,0,0.03);
    }
    .summary-title { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-muted); margin-bottom: 6px; display: flex; justify-content: space-between; }
    .node-title-lg { font-size: 13px; font-weight: 700; color: var(--text-main); font-family: ui-monospace, monospace; word-break: break-all; }
    .node-subtext { font-size: 11px; color: var(--text-muted); margin-top: 4px; }

    .hop-table { width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 11px; }
    .hop-table th { text-align: left; padding: 6px 8px; color: var(--text-muted); border-bottom: 1px solid var(--border); font-weight: 600; }
    .hop-table td { padding: 6px 8px; border-bottom: 1px solid #f1f5f9; font-family: ui-monospace, monospace; }
    .hop-table tr:hover { background: #f8fafc; cursor: pointer; }

    .badge-pill { display: inline-block; font-size: 9px; padding: 1px 6px; border-radius: 3px; font-weight: 600; text-transform: uppercase; font-family: ui-monospace, monospace; }
    .badge-lsyn { background: rgba(2,132,199,0.1); color: var(--lsyn); border: 1px solid var(--lsyn); }
    .badge-ldep { background: rgba(225,29,72,0.1); color: var(--ldep); border: 1px solid var(--ldep); }
    .badge-lflow { background: rgba(5,150,105,0.1); color: var(--lflow); border: 1px solid var(--lflow); }
    .badge-lsem { background: rgba(147,51,234,0.1); color: var(--lsem); border: 1px solid var(--lsem); }

    /* Main Graph Area */
    #canvas-view { flex: 1; position: relative; height: 100vh; background: #ffffff; }
    #network-canvas { width: 100%; height: 100%; }

    /* Top Floating Controls Bar */
    .floating-toolbar {
      position: absolute;
      top: 14px;
      right: 18px;
      display: flex;
      gap: 8px;
      background: rgba(255, 255, 255, 0.92);
      backdrop-filter: blur(8px);
      border: 1px solid var(--border);
      padding: 6px 10px;
      border-radius: 6px;
      box-shadow: 0 2px 6px rgba(0,0,0,0.06);
      z-index: 5;
    }
    .tool-btn {
      background: #f8fafc;
      border: 1px solid var(--border);
      color: var(--text-muted);
      padding: 5px 10px;
      border-radius: 4px;
      font-size: 11px;
      font-weight: 500;
      cursor: pointer;
      transition: all 0.15s;
    }
    .tool-btn:hover { color: var(--text-main); border-color: var(--border-focus); background: #ffffff; }
    .tool-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); }

    .floating-status {
      position: absolute;
      bottom: 14px;
      left: 18px;
      background: rgba(255, 255, 255, 0.92);
      border: 1px solid var(--border);
      padding: 6px 12px;
      border-radius: 4px;
      font-size: 11px;
      color: var(--text-muted);
      font-family: ui-monospace, monospace;
      pointer-events: none;
      box-shadow: 0 2px 5px rgba(0,0,0,0.04);
    }

    /* BOTTOM RIGHT FLOATING NODE INSPECTOR WINDOW */
    #node-popup {
      position: absolute;
      bottom: 20px;
      right: 20px;
      width: 360px;
      max-height: 480px;
      background: #ffffff;
      border: 1px solid var(--border);
      border-radius: 8px;
      box-shadow: 0 10px 30px rgba(0,0,0,0.1), 0 4px 6px rgba(0,0,0,0.05);
      display: none;
      flex-direction: column;
      z-index: 20;
      overflow: hidden;
      animation: popIn 0.2s ease-out;
    }
    @keyframes popIn {
      from { opacity: 0; transform: translateY(10px) scale(0.98); }
      to { opacity: 1; transform: translateY(0) scale(1); }
    }

    .popup-header {
      padding: 12px 14px;
      background: #f8fafc;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .popup-title { font-size: 12px; font-weight: 700; color: var(--text-main); font-family: ui-monospace, monospace; word-break: break-all; }
    .popup-close { background: transparent; border: none; font-size: 14px; color: var(--text-muted); cursor: pointer; padding: 2px 6px; border-radius: 4px; }
    .popup-close:hover { background: #e2e8f0; color: var(--text-main); }

    .popup-body {
      padding: 14px;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 10px;
      font-size: 12px;
    }
    .popup-row { display: flex; justify-content: space-between; font-size: 11px; color: var(--text-muted); }
    .popup-code {
      background: #f8fafc;
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 8px;
      font-size: 11px;
      font-family: ui-monospace, monospace;
      color: #0f172a;
      overflow-x: auto;
      max-height: 160px;
      white-space: pre;
    }
  </style>
</head>
<body>

  <!-- Sidebar -->
  <div id="sidebar">
    <div class="top-brand">
      <div class="brand-title">GRAFT-CKG WORKBENCH</div>
      <div class="brand-repo" id="repo-badge">Loading...</div>
    </div>

    <!-- Query Inputs -->
    <div class="control-section">
      <div class="segmented-tabs">
        <button class="segment-btn active" id="mode-nhop" onclick="setMode('nhop')">N-Hop Adjacency</button>
        <button class="segment-btn" id="mode-rag" onclick="setMode('rag')">Natural RAG</button>
      </div>

      <div class="search-row">
        <input type="text" id="query-input" class="input-field" placeholder="Target Symbol (e.g. Session.request)" onkeydown="if(event.key==='Enter') executeQuery()">
        <button class="primary-btn" onclick="executeQuery()">Retrieve</button>
      </div>

      <div class="sample-row" id="sample-row">
        <span class="quick-chip" onclick="quickSearch('Session.request')">Session.request</span>
        <span class="quick-chip" onclick="quickSearch('Session.send')">Session.send</span>
        <span class="quick-chip" onclick="quickSearch('PreparedRequest.prepare')">PreparedRequest</span>
      </div>

      <!-- Filters -->
      <div class="filter-grid">
        <div class="filter-item">
          <label>Hop Depth:</label>
          <select id="hop-depth" onchange="if(lastQuery) executeQuery()">
            <option value="1" selected>1 Hop (Direct)</option>
            <option value="2">2 Hops (Transitive)</option>
            <option value="3">3 Hops (Extended)</option>
          </select>
        </div>
        <div class="filter-item">
          <label>Direction:</label>
          <select id="direction" onchange="if(lastQuery) executeQuery()">
            <option value="both" selected>Both (In + Out)</option>
            <option value="out">Outgoing (Callees)</option>
            <option value="in">Incoming (Callers)</option>
          </select>
        </div>
      </div>

      <!-- Layer Filters -->
      <div style="font-size:11px; color:var(--text-muted); margin-top:2px;">Filter Layers:</div>
      <div class="layer-toggles">
        <label class="layer-chip active"><input type="checkbox" id="layer-lsyn" checked onchange="executeQuery()"> <span style="color:var(--lsyn)">●</span> Lsyn</label>
        <label class="layer-chip active"><input type="checkbox" id="layer-ldep" checked onchange="executeQuery()"> <span style="color:var(--ldep)">●</span> Ldep</label>
        <label class="layer-chip active"><input type="checkbox" id="layer-lflow" checked onchange="executeQuery()"> <span style="color:var(--lflow)">●</span> Lflow</label>
        <label class="layer-chip active"><input type="checkbox" id="layer-lsem" checked onchange="executeQuery()"> <span style="color:var(--lsem)">●</span> Lsem</label>
      </div>
    </div>

    <!-- Inspector Results Area -->
    <div id="inspector-panel">
      <div class="empty-state">
        Select a circular node or enter a symbol query above to retrieve its N-hop execution slice.
      </div>
    </div>
  </div>

  <!-- Graph Canvas Area -->
  <div id="canvas-view">
    <div class="floating-toolbar">
      <button class="tool-btn" id="physics-btn" onclick="togglePhysics()">Static (Locked)</button>
      <button class="tool-btn" id="isolate-btn" onclick="toggleIsolateMode()">Mode: Shaded</button>
      <button class="tool-btn" onclick="resetView()">Reset View</button>
    </div>

    <div id="network-canvas"></div>
    <div class="floating-status" id="status-display">Initializing graph topology...</div>

    <!-- BOTTOM RIGHT POPUP NODE INFO WINDOW -->
    <div id="node-popup">
      <div class="popup-header">
        <div class="popup-title" id="popup-title">Node Inspector</div>
        <button class="popup-close" onclick="closePopup()">✕</button>
      </div>
      <div class="popup-body" id="popup-content">
        <!-- Content injected dynamically on node click -->
      </div>
    </div>
  </div>

  <script>
    let network = null;
    let nodesDataSet = null;
    let edgesDataSet = null;
    let rawNodes = [];
    let rawEdges = [];
    let currentMode = 'nhop';
    let lastQuery = null;
    let isolateOnly = false;
    let physicsActive = false;

    function setMode(mode) {
      currentMode = mode;
      document.getElementById('mode-nhop').classList.toggle('active', mode === 'nhop');
      document.getElementById('mode-rag').classList.toggle('active', mode === 'rag');
      document.getElementById('query-input').placeholder = (mode === 'nhop')
        ? "Target Symbol (e.g. Session.request)"
        : "Natural language query (e.g. send http request)";
    }

    function quickSearch(sym) {
      document.getElementById('query-input').value = sym;
      executeQuery();
    }

    function getSelectedLayers() {
      const layers = [];
      if (document.getElementById('layer-lsyn').checked) layers.push('Lsyn');
      if (document.getElementById('layer-ldep').checked) layers.push('Ldep');
      if (document.getElementById('layer-lflow').checked) layers.push('Lflow');
      if (document.getElementById('layer-lsem').checked) layers.push('Lsem');
      return layers;
    }

    // Initialize clean graph layout on white background
    async function initGraph() {
      try {
        let data;
        if (window.STANDALONE_DATA) {
          data = window.STANDALONE_DATA;
        } else {
          const res = await fetch('/api/graph');
          data = await res.json();
        }
        document.getElementById('repo-badge').innerText = data.repo_name;

        if (data.repo_name.toLowerCase().includes('numpy')) {
          document.getElementById('sample-row').innerHTML = `
            <span class="quick-chip" onclick="quickSearch('inv')">inv</span>
            <span class="quick-chip" onclick="quickSearch('fft')">fft</span>
            <span class="quick-chip" onclick="quickSearch('zeros')">zeros</span>
            <span class="quick-chip" onclick="quickSearch('matmul')">matmul</span>
          `;
        }

        rawNodes = data.nodes;
        rawEdges = data.edges;

        nodesDataSet = new vis.DataSet(rawNodes);
        edgesDataSet = new vis.DataSet(rawEdges);

        const container = document.getElementById('network-canvas');
        const graphData = { nodes: nodesDataSet, edges: edgesDataSet };

        const options = {
          nodes: {
            shape: 'dot',       // ALL NODES ARE CIRCULAR DOTS
            borderWidth: 1,
            borderWidthSelected: 2,
            font: { size: 11, color: '#1e293b', face: 'ui-monospace, monospace' }
          },
          edges: {
            smooth: false, // Straight directed lines = 10x faster canvas rendering, crisp arrows
            arrows: {
              to: { enabled: true, scaleFactor: 0.65 }
            },
            width: 0.8
          },
          physics: {
            enabled: true,
            forceAtlas2Based: {
              gravitationalConstant: -700,
              centralGravity: 0.25,
              springLength: 70,
              springConstant: 0.08
            },
            solver: 'forceAtlas2Based',
            stabilization: {
              enabled: true,
              iterations: 45,
              updateInterval: 15
            }
          },
          interaction: {
            hover: false,
            tooltipDelay: 150,
            hideEdgesOnDrag: true,
            hideEdgesOnZoom: true
          }
        };

        network = new vis.Network(container, graphData, options);

        // STOP CONTINUOUS MOVEMENT: Lock physics as soon as layout stabilizes
        network.once("stabilizationIterationsDone", function () {
          freezePhysics();
          document.getElementById('status-display').innerText = `Ready: ${rawNodes.length} circular nodes, ${rawEdges.length} directed edges (Locked)`;
        });

        // Click any node: Open bottom-right floating window + retrieve neighborhood
        network.on("click", function (params) {
          if (params.nodes.length > 0) {
            const clickedId = params.nodes[0];
            showNodePopup(clickedId);
            document.getElementById('query-input').value = clickedId;
            setMode('nhop');
            executeQuery();
          } else {
            closePopup();
          }
        });

      } catch (err) {
        document.getElementById('status-display').innerText = "Init error: " + err;
      }
    }

    function freezePhysics() {
      if (network) {
        network.setOptions({ physics: { enabled: false } });
        physicsActive = false;
        document.getElementById('physics-btn').innerText = "Static (Locked)";
        document.getElementById('physics-btn').classList.remove('active');
      }
    }

    function togglePhysics() {
      physicsActive = !physicsActive;
      network.setOptions({ physics: { enabled: physicsActive } });
      document.getElementById('physics-btn').innerText = physicsActive ? "Physics: Running" : "Static (Locked)";
      document.getElementById('physics-btn').classList.toggle('active', physicsActive);
    }

    function toggleIsolateMode() {
      isolateOnly = !isolateOnly;
      document.getElementById('isolate-btn').innerText = isolateOnly ? "Mode: Isolated Only" : "Mode: Shaded";
      document.getElementById('isolate-btn').classList.toggle('active', isolateOnly);
      if (lastQuery) applyVisualShading(lastQuery.result, lastQuery.root);
    }

    function clientNhop(rootId, maxHops, direction, layersStr) {
      const allowedLayers = layersStr ? new Set(layersStr.split(',').map(s => s.trim()).filter(Boolean)) : null;
      let rootNode = rawNodes.find(n => n.id === rootId || n.id.toLowerCase() === rootId.toLowerCase() || (n.label && n.label.toLowerCase() === rootId.toLowerCase()) || n.id.includes(rootId));
      if (!rootNode) return { error: "Symbol '" + rootId + "' not found in graph." };

      const actualRoot = rootNode.id;
      const visited = { [actualRoot]: 0 };
      const queue = [[actualRoot, 0]];
      const retrievedEdges = [];

      while (queue.length > 0) {
        const [curr, depth] = queue.shift();
        if (depth >= maxHops) continue;

        rawEdges.forEach(e => {
          if (allowedLayers && !allowedLayers.has(e.layer)) return;
          if ((direction === 'out' || direction === 'both') && e.from === curr) {
            retrievedEdges.push({ id: e.id, source: e.from, target: e.to, relation: e.relation, layer: e.layer, direction: 'out', hop: depth + 1 });
            if (!(e.to in visited) || visited[e.to] > depth + 1) {
              visited[e.to] = depth + 1;
              queue.push([e.to, depth + 1]);
            }
          }
          if ((direction === 'in' || direction === 'both') && e.to === curr) {
            retrievedEdges.push({ id: e.id, source: e.from, target: e.to, relation: e.relation, layer: e.layer, direction: 'in', hop: depth + 1 });
            if (!(e.from in visited) || visited[e.from] > depth + 1) {
              visited[e.from] = depth + 1;
              queue.push([e.from, depth + 1]);
            }
          }
        });
      }

      const nodesPayload = Object.keys(visited).map(nid => {
        const n = rawNodes.find(item => item.id === nid) || { id: nid, label: nid };
        return {
          id: nid,
          name: n.label || nid.split(':').pop(),
          type: n.type || 'unknown',
          layer: n.layer || 'Lsyn',
          file: n.file || '',
          line: n.line || 0,
          hop: visited[nid],
          docstring: n.docstring || '',
          code: n.code || ''
        };
      });

      return {
        root_id: actualRoot,
        max_hops: maxHops,
        direction: direction,
        total_nodes: nodesPayload.length,
        total_edges: retrievedEdges.length,
        nodes: nodesPayload,
        edges: retrievedEdges
      };
    }

    function clientRagNhop(queryText, maxHops, direction, layersStr) {
      const qTokens = queryText.toLowerCase().split(/\\s+/).filter(Boolean);
      let bestNode = null;
      let bestScore = -1;

      rawNodes.forEach(n => {
        const text = `${n.id} ${n.label || ''} ${n.docstring || ''} ${n.code || ''}`.toLowerCase();
        let score = 0;
        qTokens.forEach(t => {
          if (text.includes(t)) score += 1;
        });
        if (score > bestScore) {
          bestScore = score;
          bestNode = n;
        }
      });

      if (!bestNode || bestScore <= 0) {
        return { error: "No relevant symbols found for: '" + queryText + "'" };
      }
      const res = clientNhop(bestNode.id, maxHops, direction, layersStr);
      res.rag_score = bestScore;
      return res;
    }

    // Execute Retrieval
    async function executeQuery() {
      const q = document.getElementById('query-input').value.trim();
      if (!q) return;

      const hops = document.getElementById('hop-depth').value;
      const direction = document.getElementById('direction').value;
      const layers = getSelectedLayers().join(',');

      document.getElementById('status-display').innerText = `Retrieving: ${q}...`;

      if (window.STANDALONE_DATA) {
        const data = (currentMode === 'nhop')
          ? clientNhop(q, parseInt(hops), direction, layers)
          : clientRagNhop(q, parseInt(hops), direction, layers);

        if (data.error) {
          renderError(data.error);
          return;
        }
        lastQuery = { result: data, root: data.root_id };
        applyVisualShading(data, data.root_id);
        renderInspector(data);
        showNodePopup(data.root_id, data);
        document.getElementById('status-display').innerText = `Retrieved: ${data.root_id} (${data.total_nodes} nodes within ${data.max_hops} hop${data.max_hops>1?'s':''})`;
        return;
      }

      let endpoint = (currentMode === 'nhop')
        ? `/api/nhop?node=${encodeURIComponent(q)}&hops=${hops}&direction=${direction}&layers=${layers}`
        : `/api/rag_nhop?q=${encodeURIComponent(q)}&hops=${hops}&direction=${direction}&layers=${layers}`;

      try {
        const res = await fetch(endpoint);
        const data = await res.json();
        if (data.error) {
          renderError(data.error);
          return;
        }

        lastQuery = { result: data, root: data.root_id };
        applyVisualShading(data, data.root_id);
        renderInspector(data);
        showNodePopup(data.root_id, data);

        document.getElementById('status-display').innerText = `Retrieved: ${data.root_id} (${data.total_nodes} nodes within ${data.max_hops} hop${data.max_hops>1?'s':''})`;
      } catch (err) {
        renderError(err.toString());
      }
    }

    // Visual Shading on White Background
    function applyVisualShading(data, rootId) {
      const activeNodeMap = new Map();
      data.nodes.forEach(n => activeNodeMap.set(n.id, n.hop));

      const activeEdgeSet = new Set();
      data.edges.forEach(e => activeEdgeSet.add(e.id));

      // Dynamically add any queried nodes/edges that were not in the initial canvas
      data.nodes.forEach(n => {
        if (!nodesDataSet.get(n.id)) {
          const defaultColor = (n.type === 'file') ? '#ea580c' : (n.type === 'class' ? '#16a34a' : '#2563eb');
          const newNode = {
            id: n.id,
            label: n.name,
            shape: 'dot',
            size: 14,
            color: { background: defaultColor, border: '#ffffff' },
            font: { color: '#0f172a', size: 10 }
          };
          nodesDataSet.add(newNode);
          rawNodes.push(newNode);
        }
      });
      data.edges.forEach(e => {
        if (!edgesDataSet.get(e.id)) {
          let edgeColor = '#0284c7';
          if (e.layer === 'Ldep') edgeColor = '#e11d48';
          else if (e.layer === 'Lflow') edgeColor = '#059669';
          else if (e.layer === 'Lsem') edgeColor = '#9333ea';
          const newEdge = {
            id: e.id,
            from: e.source,
            to: e.target,
            color: { color: edgeColor },
            width: 1.5,
            arrows: { to: { enabled: true, scaleFactor: 0.65 } }
          };
          edgesDataSet.add(newEdge);
          rawEdges.push(newEdge);
        }
      });

      const nodeUpdates = [];
      const edgeUpdates = [];

      rawNodes.forEach(node => {
        const isRoot = (node.id === rootId);
        const inNeighborhood = activeNodeMap.has(node.id);

        if (inNeighborhood) {
          const hop = activeNodeMap.get(node.id);
          nodeUpdates.push({
            id: node.id,
            hidden: false,
            shape: 'dot',
            color: isRoot
              ? { background: '#ef4444', border: '#991b1b' } // Root: Red circle
              : { background: node.color.background, border: '#334155' },
            size: isRoot ? 26 : (hop === 1 ? 18 : 13),
            font: { color: '#0f172a', size: isRoot ? 13 : 10 },
            opacity: 1.0
          });
        } else {
          // Shaded out on white canvas: faint grey dot with hidden label
          nodeUpdates.push({
            id: node.id,
            hidden: isolateOnly,
            shape: 'dot',
            color: { background: '#e2e8f0', border: '#cbd5e1' },
            size: 6,
            font: { color: 'transparent', size: 0 },
            opacity: 0.18
          });
        }
      });

      rawEdges.forEach(edge => {
        const isActive = activeEdgeSet.has(edge.id);
        if (isActive) {
          edgeUpdates.push({
            id: edge.id,
            hidden: false,
            width: 2.0,
            opacity: 1.0
          });
        } else {
          edgeUpdates.push({
            id: edge.id,
            hidden: isolateOnly,
            width: 0.4,
            color: { color: '#e2e8f0' },
            opacity: 0.12
          });
        }
      });

      nodesDataSet.update(nodeUpdates);
      edgesDataSet.update(edgeUpdates);

      if (network && rootId) {
        network.focus(rootId, {
          scale: 1.2,
          animation: { duration: 500, easingFunction: 'easeInOutQuad' }
        });
      }
    }

    // BOTTOM RIGHT FLOATING WINDOW: Shows Node Details
    async function showNodePopup(nodeId, cachedData=null) {
      const popup = document.getElementById('node-popup');
      const title = document.getElementById('popup-title');
      const content = document.getElementById('popup-content');

      title.innerText = nodeId.split(':').pop();

      // Find node info
      let nodeData = null;
      if (cachedData && cachedData.nodes) {
        nodeData = cachedData.nodes.find(n => n.id === nodeId);
      }
      if (!nodeData) {
        nodeData = rawNodes.find(n => n.id === nodeId);
      }

      if (!nodeData) {
        content.innerHTML = `<div style="color:var(--text-muted)">Loading node information...</div>`;
        popup.style.display = 'flex';
        return;
      }

      let html = `
        <div class="popup-row">
          <span>Type: <b style="color:var(--text-main)">${nodeData.type || 'Symbol'}</b></span>
          <span class="badge-pill badge-${(nodeData.layer||'Lsyn').toLowerCase()}">${nodeData.layer || 'Lsyn'}</span>
        </div>
        <div class="popup-row">
          <span>File:</span> <span style="font-family:ui-monospace">${nodeData.file || 'N/A'}</span>
        </div>
        <div class="popup-row">
          <span>Line:</span> <span style="font-family:ui-monospace">${nodeData.line || '1'}</span>
        </div>
      `;

      if (nodeData.docstring) {
        html += `
          <div style="font-size:11px; color:var(--text-muted); background:#f8fafc; border:1px solid var(--border); padding:8px; border-radius:4px; font-style:italic;">
            "${escapeHtml(nodeData.docstring)}"
          </div>
        `;
      }

      if (nodeData.code) {
        html += `
          <div style="font-size:11px; font-weight:600; color:var(--text-muted); margin-top:4px;">Source Slice:</div>
          <div class="popup-code">${escapeHtml(nodeData.code)}</div>
        `;
      }

      content.innerHTML = html;
      popup.style.display = 'flex';
    }

    function closePopup() {
      document.getElementById('node-popup').style.display = 'none';
    }

    // Left Inspector
    function renderInspector(data) {
      const panel = document.getElementById('inspector-panel');
      const rootNode = data.nodes.find(n => n.id === data.root_id) || data.nodes[0];

      let html = `
        <div class="summary-card">
          <div class="summary-title">
            <span>RETRIEVED ROOT</span>
            <span class="badge-pill badge-${rootNode.layer.toLowerCase()}">${rootNode.type}</span>
          </div>
          <div class="node-title-lg">${rootNode.name}</div>
          <div class="node-subtext">${rootNode.file} : line ${rootNode.line}</div>
        </div>

        <div class="summary-card">
          <div class="summary-title">
            <span>N-HOP NEIGHBORS</span>
            <span style="font-family:ui-monospace;">${data.total_nodes} NODES / ${data.total_edges} EDGES</span>
          </div>

          <table class="hop-table">
            <thead>
              <tr>
                <th>Hop</th>
                <th>Symbol</th>
                <th>Type</th>
                <th>Layer</th>
              </tr>
            </thead>
            <tbody>
              ${data.nodes.filter(n => n.hop > 0).map(n => `
                <tr onclick="showNodePopup('${n.id}'); document.getElementById('query-input').value='${n.id}'; executeQuery();">
                  <td style="color:${n.hop===1?'#0284c7':'#6366f1'}; font-weight:600;">H${n.hop}</td>
                  <td style="color:var(--text-main);">${n.name}</td>
                  <td style="color:var(--text-muted);">${n.type}</td>
                  <td><span class="badge-pill badge-${n.layer.toLowerCase()}">${n.layer}</span></td>
                </tr>
              `).join('') || '<tr><td colspan="4" style="color:var(--text-muted); text-align:center;">No neighbors found</td></tr>'}
            </tbody>
          </table>
        </div>
      `;

      panel.innerHTML = html;
    }

    function renderError(msg) {
      const panel = document.getElementById('inspector-panel');
      panel.innerHTML = `
        <div class="summary-card" style="border-color:#e11d48;">
          <div class="summary-title" style="color:#e11d48;">QUERY ERROR</div>
          <div style="font-size:12px; color:var(--text-main);">${escapeHtml(msg)}</div>
        </div>
      `;
      document.getElementById('status-display').innerText = "Error: " + msg;
    }

    function resetView() {
      closePopup();
      const nodeUpdates = rawNodes.map(n => ({
        id: n.id,
        hidden: false,
        shape: 'dot',
        color: n.color,
        size: n.size,
        font: { color: '#1e293b', size: 11 },
        opacity: 1.0
      }));
      const edgeUpdates = rawEdges.map(e => ({
        id: e.id,
        hidden: false,
        width: 0.8,
        color: e.color,
        opacity: 1.0
      }));
      nodesDataSet.update(nodeUpdates);
      edgesDataSet.update(edgeUpdates);
      network.fit({ animation: { duration: 500 } });
      document.getElementById('status-display').innerText = `Reset: Viewing full graph (${rawNodes.length} nodes)`;
    }

    function escapeHtml(str) {
      if (!str) return '';
      return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }

    window.onload = initGraph;
  </script>
</body>
</html>
"""


from socketserver import ThreadingMixIn


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class WorkbenchRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)

        if path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(DASHBOARD_HTML.encode("utf-8"))

        elif path == "/api/graph":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()

            G = GLOBAL_BUILDER.graph
            sub_nodes = [n for n, d in G.nodes(data=True) if d.get("type") != "variable"] if G.number_of_nodes() > 600 else list(G.nodes)
            
            # For massive repositories like NumPy (60k+ nodes), limit overview canvas to core architectural symbols (~450)
            # All other symbols are retrieved and grafted on-demand during search/N-hop queries at 60 FPS
            max_overview_nodes = 450
            if len(sub_nodes) > max_overview_nodes:
                priority_nodes = [n for n in sub_nodes if G.nodes[n].get("type") in ("file", "class", "module")]
                if len(priority_nodes) > max_overview_nodes:
                    priority_nodes = sorted(priority_nodes, key=lambda n: G.degree(n), reverse=True)[:max_overview_nodes]
                remaining = [n for n in sub_nodes if n not in priority_nodes]
                remaining.sort(key=lambda n: G.degree(n), reverse=True)
                target_fill = max(0, max_overview_nodes - len(priority_nodes))
                sub_nodes = priority_nodes + remaining[:target_fill]
                
            subG = G.subgraph(sub_nodes)

            nodes_payload = []
            for nid in sub_nodes:
                d = G.nodes[nid]
                ntype = d.get("type", "unknown")
                color = "#0284c7"
                size = 14
                if ntype == "file": color = "#ea580c"; size = 20
                elif ntype == "class": color = "#16a34a"; size = 16
                elif ntype == "function": color = "#2563eb"; size = 12
                elif ntype == "module": color = "#0891b2"; size = 18

                clean_title = f"{nid} [{d.get('layer', 'Lsyn')}]"

                nodes_payload.append({
                    "id": str(nid),
                    "label": d.get("name") or str(nid).split(":")[-1],
                    "title": clean_title,
                    "type": ntype,
                    "layer": d.get("layer", "Lsyn"),
                    "file": d.get("file", ""),
                    "line": d.get("line_no", 0),
                    "shape": "dot",
                    "color": {"background": color, "border": "#ffffff"},
                    "size": size,
                    "docstring": d.get("docstring", ""),
                    "code": d.get("code", "")
                })

            edges_payload = []
            edge_list = list(subG.edges(keys=True, data=True))
            if len(edge_list) > 800:
                edge_list = edge_list[:800]

            for u, v, k, d in edge_list:
                l = d.get("layer", "Lsyn")
                edge_color = "#94a3b8"
                if l == "Lsyn": edge_color = "#0284c7"
                elif l == "Ldep": edge_color = "#e11d48"
                elif l == "Lflow": edge_color = "#059669"
                elif l == "Lsem": edge_color = "#9333ea"

                clean_edge_title = f"{d.get('relation')} ({l})"

                edges_payload.append({
                    "id": str(k),
                    "from": str(u),
                    "to": str(v),
                    "color": {"color": edge_color},
                    "layer": l,
                    "relation": d.get("relation"),
                    "title": clean_edge_title
                })

            payload = {
                "repo_name": REPO_NAME,
                "nodes": nodes_payload,
                "edges": edges_payload,
            }
            self.wfile.write(json.dumps(payload).encode("utf-8"))

        elif path == "/api/nhop":
            node_id = params.get("node", [""])[0]
            hops = int(params.get("hops", ["1"])[0])
            direction = params.get("direction", ["both"])[0]
            layers_param = params.get("layers", [""])[0]
            layers = [l.strip() for l in layers_param.split(",") if l.strip()] if layers_param else None

            res = nhop_retrieval(GLOBAL_BUILDER, node_id, max_hops=hops, layers=layers, direction=direction)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(res).encode("utf-8"))

        elif path == "/api/rag_nhop":
            query_text = params.get("q", [""])[0]
            hops = int(params.get("hops", ["1"])[0])
            direction = params.get("direction", ["both"])[0]
            layers_param = params.get("layers", [""])[0]
            layers = [l.strip() for l in layers_param.split(",") if l.strip()] if layers_param else None

            rag_match = GLOBAL_BUILDER.rag_query(query_text, top_k=1, graft_subgraph=False)
            matches = rag_match.get("top_matches", [])
            if not matches:
                res = {"error": f"No relevant code found for query: '{query_text}'"}
            else:
                top_id = matches[0]["node_id"]
                res = nhop_retrieval(GLOBAL_BUILDER, top_id, max_hops=hops, layers=layers, direction=direction)
                res["rag_score"] = matches[0].get("score", 0.0)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(res).encode("utf-8"))

        else:
            self.send_response(404)
            self.end_headers()


def start_server(repo_path, port=8080, open_browser=True, force_rebuild=False):
    global GLOBAL_BUILDER, REPO_NAME
    import pickle
    if repo_path.lower() == "numpy" and not os.path.exists(repo_path):
        try:
            import numpy
            repo_path = os.path.dirname(numpy.__file__)
            print(f"[*] Resolved 'numpy' alias to installed package: {repo_path}")
        except Exception:
            pass

    abs_repo = os.path.abspath(repo_path)
    if not os.path.exists(abs_repo):
        print(f"[!] Error: Repository path '{abs_repo}' does not exist.")
        sys.exit(1)

    REPO_NAME = os.path.basename(abs_repo)
    cache_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), f".{REPO_NAME}_ckg_cache.pickle")

    if not force_rebuild and os.path.exists(cache_file):
        print(f"[*] Loading cached CKG from: {cache_file}...")
        try:
            with open(cache_file, "rb") as f:
                GLOBAL_BUILDER = pickle.load(f)
            print(f"[*] Loaded in 0.5s: {GLOBAL_BUILDER.graph.number_of_nodes()} nodes, {GLOBAL_BUILDER.graph.number_of_edges()} edges.")
        except Exception as e:
            print(f"[!] Cache read failed ({e}), rebuilding...")
            GLOBAL_BUILDER = CKGBuilder(repo_path)
            GLOBAL_BUILDER.build()
    else:
        print(f"[*] Constructing GRAFT-CKG for: {REPO_NAME}...")
        GLOBAL_BUILDER = CKGBuilder(repo_path)
        GLOBAL_BUILDER.build()
        try:
            with open(cache_file, "wb") as f:
                pickle.dump(GLOBAL_BUILDER, f)
            print(f"[*] Cached CKG to: {cache_file}")
        except Exception as e:
            print(f"[!] Cache write skipped: {e}")

    server = ThreadedHTTPServer(("localhost", port), WorkbenchRequestHandler)
    url = f"http://localhost:{port}"
    print(f"\n" + "="*70)
    print(f"GRAFT-CKG Clean Light Workbench running at: {url}")
    print("="*70)
    print("Features:")
    print("  - Pure white background / clean typography")
    print("  - Circular nodes ('dot' shape) & directed arrows")
    print("  - Bottom-right pop-up inspector on node click")
    print("  - N-Hop adjacency retrieval with background shading")
    print("Press Ctrl+C to stop.")

    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] Workbench stopped.")


def export_standalone_html(repo_path, output_path="polyglot_graph.html"):
    import pickle
    if repo_path.lower() == "numpy" and not os.path.exists(repo_path):
        try:
            import numpy
            repo_path = os.path.dirname(numpy.__file__)
            print(f"[*] Resolved 'numpy' alias to installed package: {repo_path}")
        except Exception:
            pass

    abs_repo = os.path.abspath(repo_path)
    if not os.path.exists(abs_repo):
        print(f"[!] Error: Repository path '{abs_repo}' does not exist.")
        sys.exit(1)

    repo_name = os.path.basename(abs_repo)
    cache_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), f".{repo_name}_ckg_cache.pickle")
    builder = None
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "rb") as f:
                builder = pickle.load(f)
            print(f"[*] Loaded cached CKG from: {cache_file}")
        except Exception:
            builder = None

    if builder is None:
        print(f"[*] Constructing GRAFT-CKG for: {repo_name}...")
        builder = CKGBuilder(repo_path)
        builder.build()

    G = builder.graph
    sub_nodes = [n for n, d in G.nodes(data=True) if d.get("type") != "variable"] if G.number_of_nodes() > 600 else list(G.nodes)
    max_overview_nodes = 450
    if len(sub_nodes) > max_overview_nodes:
        priority_nodes = [n for n in sub_nodes if G.nodes[n].get("type") in ("file", "class", "module", "service_contract")]
        if len(priority_nodes) > max_overview_nodes:
            priority_nodes = sorted(priority_nodes, key=lambda n: G.degree(n), reverse=True)[:max_overview_nodes]
        remaining = [n for n in sub_nodes if n not in priority_nodes]
        remaining.sort(key=lambda n: G.degree(n), reverse=True)
        target_fill = max(0, max_overview_nodes - len(priority_nodes))
        sub_nodes = priority_nodes + remaining[:target_fill]

    subG = G.subgraph(sub_nodes)
    nodes_payload = []
    for nid in sub_nodes:
        d = G.nodes[nid]
        ntype = d.get("type", "unknown")
        color = "#0284c7"
        size = 14
        if ntype == "file": color = "#ea580c"; size = 20
        elif ntype == "class": color = "#16a34a"; size = 16
        elif ntype == "function": color = "#2563eb"; size = 12
        elif ntype == "module": color = "#0891b2"; size = 18
        elif ntype in ("service_contract", "rpc_contract"): color = "#9333ea"; size = 18

        nodes_payload.append({
            "id": str(nid),
            "label": d.get("name") or str(nid).split(":")[-1],
            "title": f"{nid} [{d.get('layer', 'Lsyn')}]",
            "type": ntype,
            "layer": d.get("layer", "Lsyn"),
            "file": d.get("file", ""),
            "line": d.get("line_no", 0),
            "shape": "dot",
            "color": {"background": color, "border": "#ffffff"},
            "size": size,
            "docstring": d.get("docstring", ""),
            "code": d.get("code", "")
        })

    edges_payload = []
    edge_list = list(subG.edges(keys=True, data=True))
    if len(edge_list) > 800:
        edge_list = edge_list[:800]

    for u, v, k, d in edge_list:
        l = d.get("layer", "Lsyn")
        edge_color = "#94a3b8"
        if l == "Lsyn": edge_color = "#0284c7"
        elif l == "Ldep": edge_color = "#e11d48"
        elif l == "Lflow": edge_color = "#059669"
        elif l == "Lsem": edge_color = "#9333ea"
        elif l == "Lcontract": edge_color = "#a855f7"

        edges_payload.append({
            "id": str(k),
            "from": str(u),
            "to": str(v),
            "color": {"color": edge_color},
            "layer": l,
            "relation": d.get("relation"),
            "title": f"{d.get('relation')} ({l})"
        })

    payload = {
        "repo_name": repo_name,
        "nodes": nodes_payload,
        "edges": edges_payload,
    }

    injection = f"<script>window.STANDALONE_DATA = {json.dumps(payload)};</script>\n"
    final_html = DASHBOARD_HTML.replace("<head>", f"<head>\n  {injection}")
    out_abs = os.path.abspath(output_path)
    with open(out_abs, "w", encoding="utf-8") as f:
        f.write(final_html)
    print(f"\n[+] Successfully exported Standalone Interactive Graph to:\n    {out_abs}")
    print("[*] This file is completely self-contained (zero Python server required).")
    print("    You can double-click it, email it, or host it on GitHub Pages directly!\n")
    return out_abs


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="GRAFT-CKG Clean Light-Mode Workbench & N-Hop Visualizer")
    parser.add_argument(
        "repo",
        nargs="?",
        default="examples/mixed_service",
        help="Path to the repository to index and visualize (default: examples/mixed_service)"
    )
    parser.add_argument("--port", "-p", type=int, default=8080, help="Server port (default: 8080)")
    parser.add_argument("--no-browser", action="store_true", help="Do not automatically open browser")
    parser.add_argument("--rebuild", action="store_true", help="Force rebuild CKG (ignore cached pickle)")
    parser.add_argument("--export", type=str, default="", help="Export a standalone zero-server HTML file (e.g. polyglot.html)")
    args = parser.parse_args()

    if args.export:
        export_standalone_html(args.repo, output_path=args.export)
    else:
        start_server(args.repo, port=args.port, open_browser=not args.no_browser, force_rebuild=args.rebuild)
