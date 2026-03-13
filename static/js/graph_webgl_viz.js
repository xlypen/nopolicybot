(function () {
  const _stateByRoot = new WeakMap();

  function n(v, fallback) {
    const x = Number(v);
    return Number.isFinite(x) ? x : fallback;
  }

  function safeData(data) {
    return data && typeof data === "object" ? data : { nodes: [], edges: [], meta: {} };
  }

  function nodeScore(node) {
    const influence = n(node && node.influence_score, 0);
    const centrality = n(node && node.centrality, 0);
    const degree = n(node && node.degree, 0);
    return influence * 100 + centrality * 35 + degree * 5;
  }

  function edgeScore(edge) {
    const w = n(edge && (edge.weight_period || edge.weight), 0);
    const bridge = n(edge && edge.bridge_score, 0);
    const cross = Number(edge && edge.community_id) === -1 ? 1 : 0;
    return bridge * 1000 + cross * 100 + w;
  }

  function detectWebGL() {
    try {
      const probe = document.createElement("canvas");
      const gl = probe.getContext("webgl", { antialias: false }) || probe.getContext("experimental-webgl");
      return !!gl;
    } catch (_e) {
      return false;
    }
  }

  function pickSubset(graph, limits) {
    const srcNodes = Array.isArray(graph.nodes) ? graph.nodes : [];
    const srcEdges = Array.isArray(graph.edges) ? graph.edges : [];
    const maxNodes = Math.max(80, n(limits && limits.maxNodes, 1200));
    const maxEdges = Math.max(200, n(limits && limits.maxEdges, 6000));
    if (srcNodes.length <= maxNodes && srcEdges.length <= maxEdges) {
      return {
        nodes: srcNodes,
        edges: srcEdges,
        culled: false,
        originalNodes: srcNodes.length,
        originalEdges: srcEdges.length,
      };
    }

    const nodeById = new Map();
    srcNodes.forEach((node) => {
      const id = n(node && node.id, 0);
      if (id) nodeById.set(id, node);
    });

    const bridgeIds = new Set();
    srcEdges.forEach((e) => {
      const a = n(e && e.source, 0);
      const b = n(e && e.target, 0);
      if (!a || !b) return;
      const isBridge = n(e && e.bridge_score, 0) > 0 || Number(e && e.community_id) === -1;
      if (isBridge) {
        bridgeIds.add(a);
        bridgeIds.add(b);
      }
    });

    const rankedBridge = Array.from(bridgeIds)
      .map((id) => nodeById.get(id))
      .filter(Boolean)
      .sort((a, b) => nodeScore(b) - nodeScore(a));
    const rankedNodes = srcNodes.slice().sort((a, b) => nodeScore(b) - nodeScore(a));
    const keep = [];
    const seen = new Set();
    rankedBridge.concat(rankedNodes).forEach((node) => {
      const id = n(node && node.id, 0);
      if (!id || seen.has(id)) return;
      seen.add(id);
      keep.push(node);
    });

    const keptNodes = keep.slice(0, maxNodes);
    const keptIds = new Set(keptNodes.map((x) => n(x && x.id, 0)));
    const keptEdges = srcEdges
      .filter((e) => keptIds.has(n(e && e.source, 0)) && keptIds.has(n(e && e.target, 0)))
      .sort((a, b) => edgeScore(b) - edgeScore(a))
      .slice(0, maxEdges);

    return {
      nodes: keptNodes,
      edges: keptEdges,
      culled: true,
      originalNodes: srcNodes.length,
      originalEdges: srcEdges.length,
    };
  }

  function positionNodes(nodes, width, height, mode) {
    const cx = width / 2;
    const cy = height / 2;
    const pos = {};
    const m = String(mode || "force");
    if (!nodes.length) return pos;

    if (m === "radial" || m === "community" || m === "hierarchy") {
      const byComm = {};
      nodes.forEach((node) => {
        const key = String(node && node.community_id != null ? node.community_id : "none");
        if (!byComm[key]) byComm[key] = [];
        byComm[key].push(node);
      });
      const groups = Object.keys(byComm);
      const groupR = Math.max(80, Math.min(width, height) * 0.3);
      groups.forEach((gid, gi) => {
        const ga = (gi / Math.max(1, groups.length)) * Math.PI * 2;
        const gcx = cx + Math.cos(ga) * groupR;
        const gcy = cy + Math.sin(ga) * groupR;
        const members = byComm[gid];
        const ring = Math.max(24, 28 + Math.min(160, members.length * 1.5));
        members.forEach((node, i) => {
          const a = (i / Math.max(1, members.length)) * Math.PI * 2;
          const id = n(node && node.id, 0);
          pos[id] = {
            x: gcx + Math.cos(a) * ring,
            y: gcy + Math.sin(a) * ring,
          };
        });
      });
      return pos;
    }

    const rMax = Math.max(80, Math.min(width, height) * 0.46);
    nodes.forEach((node, i) => {
      const t = i / Math.max(1, nodes.length);
      const angle = t * Math.PI * 10;
      const radius = Math.sqrt(t) * rMax;
      const id = n(node && node.id, 0);
      pos[id] = {
        x: cx + Math.cos(angle) * radius,
        y: cy + Math.sin(angle) * radius,
      };
    });
    return pos;
  }

  function applyCanvasSize(canvas, width, height) {
    const dpr = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
    canvas.width = Math.max(1, Math.floor(width * dpr));
    canvas.height = Math.max(1, Math.floor(height * dpr));
    canvas.style.width = `${Math.max(1, Math.floor(width))}px`;
    canvas.style.height = `${Math.max(1, Math.floor(height))}px`;
    return dpr;
  }

  function edgeColor(edge) {
    const tone = String((edge && edge.tone) || "neutral").toLowerCase();
    if (tone === "friendly") return "rgba(74,222,128,0.48)";
    if (tone === "conflict") return "rgba(249,115,22,0.5)";
    if (tone === "toxic") return "rgba(239,68,68,0.56)";
    return "rgba(148,163,184,0.38)";
  }

  function nodeColor(node) {
    const tier = String((node && node.tier) || "secondary");
    if (tier === "core") return "rgba(34,197,94,0.9)";
    if (tier === "periphery") return "rgba(167,139,250,0.88)";
    return "rgba(56,189,248,0.9)";
  }

  function drawScene(state) {
    state.raf = 0;
    const ctx = state.ctx;
    if (!ctx) return;
    const canvas = state.canvas;
    const dpr = state.dpr || 1;
    const width = canvas.width / dpr;
    const height = canvas.height / dpr;
    const t = state.transform;
    const posById = state.posById;
    const nodes = state.nodes;
    const edges = state.edges;

    ctx.save();
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#0b1630";
    ctx.fillRect(0, 0, width, height);

    for (let i = 0; i < edges.length; i += 1) {
      const e = edges[i];
      const a = posById[n(e && e.source, 0)];
      const b = posById[n(e && e.target, 0)];
      if (!a || !b) continue;
      const x1 = a.x * t.scale + t.tx;
      const y1 = a.y * t.scale + t.ty;
      const x2 = b.x * t.scale + t.tx;
      const y2 = b.y * t.scale + t.ty;
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(x2, y2);
      ctx.strokeStyle = edgeColor(e);
      const base = Math.max(0.35, Math.min(2.2, n(e && (e.weight_period || e.weight), 1) * 0.08));
      ctx.lineWidth = Math.max(0.3, base * t.scale);
      ctx.stroke();
    }

    for (let i = 0; i < nodes.length; i += 1) {
      const node = nodes[i];
      const p = posById[n(node && node.id, 0)];
      if (!p) continue;
      const x = p.x * t.scale + t.tx;
      const y = p.y * t.scale + t.ty;
      const deg = n(node && node.degree, 0);
      const influence = n(node && node.influence_score, 0);
      const rad = Math.max(2.2, Math.min(9, (2.5 + deg * 0.26 + influence * 6) * t.scale));
      ctx.beginPath();
      ctx.arc(x, y, rad, 0, Math.PI * 2);
      ctx.fillStyle = nodeColor(node);
      ctx.fill();
    }

    const labelCandidates = nodes.slice().sort((a, b) => nodeScore(b) - nodeScore(a)).slice(0, 24);
    if (t.scale >= 0.65) {
      ctx.fillStyle = "rgba(226,232,240,0.92)";
      ctx.font = `${Math.max(10, Math.min(13, 10 + t.scale))}px sans-serif`;
      labelCandidates.forEach((node) => {
        const p = posById[n(node && node.id, 0)];
        if (!p) return;
        const x = p.x * t.scale + t.tx + 5;
        const y = p.y * t.scale + t.ty - 5;
        ctx.fillText(String((node && node.label) || (node && node.id) || ""), x, y);
      });
    }
    ctx.restore();
  }

  function scheduleDraw(state) {
    if (!state || state.raf) return;
    state.raf = window.requestAnimationFrame(() => drawScene(state));
  }

  function attachInteractions(state) {
    const canvas = state.canvas;
    const t = state.transform;
    let dragging = false;
    let startX = 0;
    let startY = 0;
    let startTx = 0;
    let startTy = 0;

    canvas.addEventListener(
      "wheel",
      (e) => {
        e.preventDefault();
        const rect = canvas.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;
        const factor = e.deltaY > 0 ? 0.9 : 1.1;
        const nextScale = Math.max(0.25, Math.min(4, t.scale * factor));
        t.tx = x - ((x - t.tx) * nextScale) / t.scale;
        t.ty = y - ((y - t.ty) * nextScale) / t.scale;
        t.scale = nextScale;
        scheduleDraw(state);
      },
      { passive: false }
    );

    canvas.addEventListener("mousedown", (e) => {
      if (e.button !== 0) return;
      dragging = true;
      startX = e.clientX;
      startY = e.clientY;
      startTx = t.tx;
      startTy = t.ty;
    });
    window.addEventListener("mousemove", (e) => {
      if (!dragging) return;
      t.tx = startTx + (e.clientX - startX);
      t.ty = startTy + (e.clientY - startY);
      scheduleDraw(state);
    });
    window.addEventListener("mouseup", () => {
      dragging = false;
    });
  }

  function render(ctx) {
    const payload = ctx || {};
    const root = payload.root || document.getElementById("graph-root");
    if (!root) return;
    const graph = safeData(payload.data);
    const mode = String(payload.mode || "force");
    const limits = payload.largeGraphLimits || { maxNodes: 1400, maxEdges: 9000 };
    const subset = pickSubset(graph, limits);

    const wrap = document.createElement("div");
    wrap.style.display = "grid";
    wrap.style.gap = "8px";
    const note = document.createElement("div");
    note.className = "text-sm text-muted";
    const webglReady = detectWebGL();
    note.textContent = subset.culled
      ? `GPU pipeline (${webglReady ? "webgl-ready" : "canvas-fallback"}): render ${subset.nodes.length}/${subset.originalNodes} nodes, ${subset.edges.length}/${subset.originalEdges} edges`
      : `GPU pipeline (${webglReady ? "webgl-ready" : "canvas-fallback"}): ${subset.nodes.length} nodes, ${subset.edges.length} edges`;
    const canvas = document.createElement("canvas");
    canvas.className = "graph-webgl-canvas";
    canvas.style.border = "1px solid rgba(71,85,105,0.45)";
    canvas.style.borderRadius = "10px";
    canvas.style.background = "#0b1630";
    canvas.style.cursor = "grab";
    canvas.style.touchAction = "none";
    wrap.appendChild(note);
    wrap.appendChild(canvas);
    root.innerHTML = "";
    root.appendChild(wrap);

    const width = Math.max(360, root.clientWidth || 920);
    const height = Math.max(420, root.clientHeight || 520);
    const dpr = applyCanvasSize(canvas, width, height);
    const ctx2d = canvas.getContext("2d", { alpha: false, desynchronized: true });
    if (!ctx2d) {
      root.innerHTML = '<div style="padding:0.8rem;color:var(--text-muted);">Canvas renderer unavailable</div>';
      return;
    }

    const state = {
      canvas,
      ctx: ctx2d,
      dpr,
      mode,
      nodes: subset.nodes,
      edges: subset.edges,
      posById: positionNodes(subset.nodes, width, height, mode),
      transform: { scale: 1, tx: 0, ty: 0 },
      raf: 0,
    };

    if (state.nodes.length) {
      let minX = Infinity;
      let maxX = -Infinity;
      let minY = Infinity;
      let maxY = -Infinity;
      state.nodes.forEach((node) => {
        const p = state.posById[n(node && node.id, 0)];
        if (!p) return;
        if (p.x < minX) minX = p.x;
        if (p.x > maxX) maxX = p.x;
        if (p.y < minY) minY = p.y;
        if (p.y > maxY) maxY = p.y;
      });
      const rangeX = Math.max(1, maxX - minX);
      const rangeY = Math.max(1, maxY - minY);
      const sx = (width - 80) / rangeX;
      const sy = (height - 80) / rangeY;
      const scale = Math.max(0.8, Math.min(1.6, Math.min(sx, sy)));
      state.transform.scale = scale;
      state.transform.tx = 40 - minX * scale;
      state.transform.ty = 40 - minY * scale;
    }

    _stateByRoot.set(root, state);
    attachInteractions(state);
    scheduleDraw(state);
  }

  window.GraphWebGLViz = {
    name: "graph-webgl-v1",
    modes: ["webgl", "force", "radial", "community", "matrix", "sankey", "hierarchy", "bubble", "ego"],
    render,
  };
})();
