(function () {
  function clear(el) {
    if (el) el.innerHTML = "";
  }

  function safeData(data) {
    return data && typeof data === "object" ? data : { nodes: [], edges: [], meta: {} };
  }

  function panel(root, html) {
    root.innerHTML = html;
  }

  function renderForce(root, graph) {
    const nodes = graph.nodes || [];
    const edges = graph.edges || [];
    const width = root.clientWidth || 920;
    const height = Math.max(420, root.clientHeight || 520);
    const r = Math.min(width, height) * 0.38;
    const cx = width / 2;
    const cy = height / 2;
    const pos = {};
    nodes.forEach((n, i) => {
      const a = (i / Math.max(1, nodes.length)) * Math.PI * 2;
      pos[n.id] = { x: cx + Math.cos(a) * r, y: cy + Math.sin(a) * r };
    });
    const edgeSvg = edges.map((e) => {
      const a = pos[e.source];
      const b = pos[e.target];
      if (!a || !b) return "";
      const w = Math.max(1, Math.min(6, Number(e.weight_period || e.weight || 1)));
      return `<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="rgba(155,176,207,.55)" stroke-width="${w * 0.35}" />`;
    }).join("");
    const nodeSvg = nodes.map((n) => {
      const p = pos[n.id];
      if (!p) return "";
      const rad = 7 + Math.min(18, Number(n.degree || 1) * 0.9);
      return `
        <g>
          <circle cx="${p.x}" cy="${p.y}" r="${rad}" fill="rgba(233,69,96,.85)"></circle>
          <text x="${p.x}" y="${p.y + rad + 12}" text-anchor="middle" fill="#dbe7ff" font-size="11">${(n.label || n.id)}</text>
        </g>
      `;
    }).join("");

    panel(root, `
      <svg width="100%" height="${height}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet">
        ${edgeSvg}
        ${nodeSvg}
      </svg>
    `);
  }

  function renderRadial(root, graph) {
    const nodes = graph.nodes || [];
    const byComm = {};
    nodes.forEach((n) => {
      const c = String(n.community_id ?? "none");
      if (!byComm[c]) byComm[c] = [];
      byComm[c].push(n);
    });
    const comms = Object.keys(byComm);
    const width = root.clientWidth || 920;
    const height = Math.max(420, root.clientHeight || 520);
    const cx = width / 2;
    const cy = height / 2;
    const groupR = Math.min(width, height) * 0.32;
    let svg = `<svg width="100%" height="${height}" viewBox="0 0 ${width} ${height}">`;
    comms.forEach((cid, ci) => {
      const ga = (ci / Math.max(1, comms.length)) * Math.PI * 2;
      const gcx = cx + Math.cos(ga) * groupR;
      const gcy = cy + Math.sin(ga) * groupR;
      const members = byComm[cid];
      members.forEach((n, i) => {
        const a = (i / Math.max(1, members.length)) * Math.PI * 2;
        const x = gcx + Math.cos(a) * 56;
        const y = gcy + Math.sin(a) * 56;
        svg += `<circle cx="${x}" cy="${y}" r="8" fill="rgba(79,70,229,.86)"></circle>`;
      });
      svg += `<text x="${gcx}" y="${gcy - 68}" text-anchor="middle" fill="#b6cae6" font-size="12">community ${cid}</text>`;
    });
    svg += "</svg>";
    panel(root, svg);
  }

  function renderCommunity(root, graph) {
    const nodes = graph.nodes || [];
    const zones = {};
    nodes.forEach((n) => {
      const z = String(n.tier || "secondary");
      if (!zones[z]) zones[z] = [];
      zones[z].push(n);
    });
    const colors = { core: "#22c55e", secondary: "#38bdf8", periphery: "#a78bfa" };
    let html = `<div class="row" style="margin-bottom:8px;">`;
    Object.keys(zones).forEach((z) => {
      html += `<span class="badge" style="background:${colors[z] || "#1a3b65"}22;border:1px solid ${colors[z] || "#1a3b65"}">${z}: ${zones[z].length}</span>`;
    });
    html += `</div><div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:8px;">`;
    nodes.forEach((n) => {
      const tier = n.tier || "secondary";
      html += `<div class="card" style="margin:0;border-color:${colors[tier] || "#1a3b65"}"><div class="title">${n.label || n.id}</div><div class="text-sm">tier: ${tier}</div><div class="text-sm">degree: ${n.degree || 0}</div></div>`;
    });
    html += "</div>";
    panel(root, html);
  }

  function renderMatrix(root, graph) {
    const nodes = graph.nodes || [];
    const edges = graph.edges || [];
    const idToIdx = {};
    nodes.forEach((n, i) => { idToIdx[n.id] = i; });
    const n = nodes.length;
    const size = Math.min(680, Math.max(300, n * 22));
    let cells = "";
    edges.forEach((e) => {
      const i = idToIdx[e.source];
      const j = idToIdx[e.target];
      if (i == null || j == null) return;
      const w = Math.max(0.15, Math.min(1, Number(e.weight_period || e.weight || 1) / 10));
      cells += `<rect x="${i * 20}" y="${j * 20}" width="18" height="18" fill="rgba(233,69,96,${w})"></rect>`;
      cells += `<rect x="${j * 20}" y="${i * 20}" width="18" height="18" fill="rgba(233,69,96,${w})"></rect>`;
    });
    panel(root, `
      <div class="text-sm text-muted mb-1">Adjacency matrix (${n} x ${n})</div>
      <svg width="${size}" height="${size}" viewBox="0 0 ${Math.max(1, n * 20)} ${Math.max(1, n * 20)}">${cells}</svg>
    `);
  }

  const handlers = {
    force: renderForce,
    radial: renderRadial,
    community: renderCommunity,
    matrix: renderMatrix,
  };

  window.GraphVisualizations = {
    modes: Object.keys(handlers),
    render(ctx) {
      const root = ctx && ctx.root ? ctx.root : document.getElementById("graph-root");
      if (!root) return;
      clear(root);
      const graph = safeData(ctx && ctx.data);
      const mode = (ctx && ctx.mode) || "force";
      const fn = handlers[mode] || handlers.force;
      fn(root, graph);
    },
  };
})();
