(function () {
  function clear(el) {
    if (el) el.innerHTML = "";
  }

  function safeData(data) {
    return data && typeof data === "object" ? data : { nodes: [], edges: [], meta: {} };
  }

  function cards(root, title, rows) {
    let html = `<div class="title">${title}</div><div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:8px;">`;
    rows.forEach((r) => {
      html += `<div class="card" style="margin:0"><div style="font-weight:700">${r.h}</div><div class="text-sm">${r.b}</div></div>`;
    });
    html += "</div>";
    root.innerHTML = html;
  }

  function renderSankey(root, graph) {
    const rows = (graph.edges || []).slice(0, 18).map((e) => ({
      h: `${e.source} -> ${e.target}`,
      b: `flow: ${e.weight_period || e.weight || 0}`,
    }));
    cards(root, "Sankey-style flow (simplified)", rows.length ? rows : [{ h: "No data", b: "Нет рёбер для отображения" }]);
  }

  function renderHierarchy(root, graph) {
    const byComm = {};
    (graph.nodes || []).forEach((n) => {
      const c = String(n.community_id ?? "none");
      if (!byComm[c]) byComm[c] = [];
      byComm[c].push(n);
    });
    const rows = Object.keys(byComm).map((cid) => ({
      h: `Community ${cid}`,
      b: `nodes: ${byComm[cid].length}`,
    }));
    cards(root, "Hierarchy by communities", rows.length ? rows : [{ h: "No data", b: "Нет узлов для иерархии" }]);
  }

  function renderBubble(root, graph) {
    const nodes = (graph.nodes || []).slice().sort((a, b) => Number(b.influence_score || 0) - Number(a.influence_score || 0)).slice(0, 32);
    let html = `<div class="title">Bubble (activity vs influence)</div><div style="display:flex;flex-wrap:wrap;gap:10px;">`;
    nodes.forEach((n) => {
      const r = 18 + Math.min(38, Number(n.influence_score || n.degree || 1) * 1.4);
      html += `<div title="${n.label || n.id}" style="width:${r * 2}px;height:${r * 2}px;border-radius:50%;background:radial-gradient(circle at 30% 30%, rgba(56,189,248,.9), rgba(79,70,229,.9));display:flex;align-items:center;justify-content:center;color:white;font-size:11px;text-align:center;padding:6px;">${(n.label || n.id)}</div>`;
    });
    html += "</div>";
    root.innerHTML = html;
  }

  function renderEgo(root, graph) {
    const nodes = graph.nodes || [];
    const edges = graph.edges || [];
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
    cards(root, `Ego network: ${center.label || center.id}`, rows.length ? rows : [{ h: "No edges", b: "У центрального узла нет связей" }]);
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
