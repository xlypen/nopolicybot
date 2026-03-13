(function () {
  const _renderEpochByRoot = new WeakMap();

  function nextEpoch(root) {
    const next = Number(_renderEpochByRoot.get(root) || 0) + 1;
    _renderEpochByRoot.set(root, next);
    return next;
  }

  function epochAlive(root, epoch) {
    return Number(_renderEpochByRoot.get(root) || 0) === Number(epoch);
  }

  function clear(el) {
    if (el) el.innerHTML = "";
  }

  function safeData(data) {
    return data && typeof data === "object" ? data : { nodes: [], edges: [], meta: {} };
  }

  function n(v, fallback) {
    const x = Number(v);
    return Number.isFinite(x) ? x : fallback;
  }

  function edgeScore(edge) {
    const w = n(edge && (edge.weight_period || edge.weight), 0);
    const bridge = n(edge && edge.bridge_score, 0);
    const cross = Number(edge && edge.community_id) === -1 ? 1 : 0;
    return bridge * 1000 + cross * 100 + w;
  }

  function nodeScore(node) {
    const influence = n(node && node.influence_score, 0);
    const degree = n(node && node.degree, 0);
    const centrality = n(node && node.centrality, 0);
    return influence * 100 + degree * 5 + centrality * 50;
  }

  function pickSubset(graph, limits) {
    const srcNodes = Array.isArray(graph.nodes) ? graph.nodes : [];
    const srcEdges = Array.isArray(graph.edges) ? graph.edges : [];
    const maxNodes = Math.max(20, n(limits && limits.maxNodes, 220));
    const maxEdges = Math.max(30, n(limits && limits.maxEdges, 300));
    const nodes = srcNodes.slice().sort((a, b) => nodeScore(b) - nodeScore(a)).slice(0, maxNodes);
    const keptIds = new Set(nodes.map((x) => n(x && x.id, 0)).filter(Boolean));
    const edges = srcEdges
      .filter((e) => keptIds.has(n(e && e.source, 0)) && keptIds.has(n(e && e.target, 0)))
      .sort((a, b) => edgeScore(b) - edgeScore(a))
      .slice(0, maxEdges);
    return {
      nodes,
      edges,
      culled: srcNodes.length > nodes.length || srcEdges.length > edges.length,
      originalNodes: srcNodes.length,
      originalEdges: srcEdges.length,
    };
  }

  function renderTitle(root, title, subset) {
    const note = document.createElement("div");
    note.className = "title";
    note.textContent = title;
    root.appendChild(note);
    const hint = document.createElement("div");
    hint.className = "text-sm text-muted mb-1";
    hint.textContent = subset && subset.culled
      ? `render ${subset.nodes.length}/${subset.originalNodes} nodes, ${subset.edges.length}/${subset.originalEdges} edges`
      : "";
    root.appendChild(hint);
  }

  function appendProgressive(root, epoch, target, rows, chunkSize, drawRow) {
    let idx = 0;
    const chunk = Math.max(20, n(chunkSize, 120));
    function frame() {
      if (!epochAlive(root, epoch)) return;
      const end = Math.min(rows.length, idx + chunk);
      for (; idx < end; idx++) drawRow(rows[idx], idx);
      if (idx < rows.length) {
        window.requestAnimationFrame(frame);
      }
    }
    window.requestAnimationFrame(frame);
  }

  function cards(root, title, rows, subset) {
    clear(root);
    const epoch = nextEpoch(root);
    renderTitle(root, title, subset);
    const grid = document.createElement("div");
    grid.style.display = "grid";
    grid.style.gridTemplateColumns = "repeat(auto-fill,minmax(220px,1fr))";
    grid.style.gap = "8px";
    root.appendChild(grid);
    appendProgressive(root, epoch, grid, rows, 120, (r) => {
      const card = document.createElement("div");
      card.className = "card";
      card.style.margin = "0";
      card.innerHTML = `<div style="font-weight:700">${r.h}</div><div class="text-sm">${r.b}</div>`;
      grid.appendChild(card);
    });
  }

  function renderSankey(root, graph) {
    const subset = pickSubset(graph, { maxNodes: 180, maxEdges: 180 });
    const rows = (subset.edges || []).slice(0, 18).map((e) => ({
      h: `${e.source} -> ${e.target}`,
      b: `flow: ${e.weight_period || e.weight || 0}`,
    }));
    cards(root, "Sankey-style flow (simplified)", rows.length ? rows : [{ h: "No data", b: "Нет рёбер для отображения" }], subset);
  }

  function renderHierarchy(root, graph) {
    const subset = pickSubset(graph, { maxNodes: 320, maxEdges: 360 });
    const byComm = {};
    (subset.nodes || []).forEach((n) => {
      const c = String(n.community_id ?? "none");
      if (!byComm[c]) byComm[c] = [];
      byComm[c].push(n);
    });
    const rows = Object.keys(byComm).map((cid) => ({
      h: `Community ${cid}`,
      b: `nodes: ${byComm[cid].length}`,
    }));
    cards(root, "Hierarchy by communities", rows.length ? rows : [{ h: "No data", b: "Нет узлов для иерархии" }], subset);
  }

  function renderBubble(root, graph) {
    const subset = pickSubset(graph, { maxNodes: 260, maxEdges: 180 });
    const nodes = (subset.nodes || []).slice().sort((a, b) => Number(b.influence_score || 0) - Number(a.influence_score || 0)).slice(0, 40);
    clear(root);
    const epoch = nextEpoch(root);
    renderTitle(root, "Bubble (activity vs influence)", subset);
    const wrap = document.createElement("div");
    wrap.style.display = "flex";
    wrap.style.flexWrap = "wrap";
    wrap.style.gap = "10px";
    root.appendChild(wrap);
    appendProgressive(root, epoch, wrap, nodes, 60, (n) => {
      const r = 18 + Math.min(38, Number(n.influence_score || n.degree || 1) * 1.4);
      const bubble = document.createElement("div");
      bubble.title = String(n.label || n.id);
      bubble.style.width = `${r * 2}px`;
      bubble.style.height = `${r * 2}px`;
      bubble.style.borderRadius = "50%";
      bubble.style.background = "radial-gradient(circle at 30% 30%, rgba(56,189,248,.9), rgba(79,70,229,.9))";
      bubble.style.display = "flex";
      bubble.style.alignItems = "center";
      bubble.style.justifyContent = "center";
      bubble.style.color = "white";
      bubble.style.fontSize = "11px";
      bubble.style.textAlign = "center";
      bubble.style.padding = "6px";
      bubble.textContent = String(n.label || n.id);
      wrap.appendChild(bubble);
    });
  }

  function renderEgo(root, graph) {
    const subset = pickSubset(graph, { maxNodes: 200, maxEdges: 300 });
    const nodes = subset.nodes || [];
    const edges = subset.edges || [];
    const center = nodes[0];
    if (!center) {
      root.innerHTML = `<div class="card">Нет данных для ego-view</div>`;
      return;
    }
    const linked = edges.filter((e) => e.source === center.id || e.target === center.id).slice(0, 24);
    const rows = linked.map((e) => {
      const peer = e.source === center.id ? e.target : e.source;
      return { h: `${center.label || center.id} <-> ${peer}`, b: `weight: ${e.weight_period || e.weight || 0}` };
    });
    cards(root, `Ego network: ${center.label || center.id}`, rows.length ? rows : [{ h: "No edges", b: "У центрального узла нет связей" }], subset);
  }

  const handlers = {
    sankey: renderSankey,
    hierarchy: renderHierarchy,
    bubble: renderBubble,
    ego: renderEgo,
  };

  window.AdvancedGraphViz = {
    modes: Object.keys(handlers),
    render(ctx) {
      const root = ctx && ctx.root ? ctx.root : document.getElementById("graph-root");
      if (!root) return;
      clear(root);
      const graph = safeData(ctx && ctx.data);
      const mode = (ctx && ctx.mode) || "sankey";
      const fn = handlers[mode] || handlers.sankey;
      fn(root, graph);
    },
  };
})();
