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

  function buildNeighborMap(edges) {
    const map = new Map();
    (edges || []).forEach((e) => {
      const a = n(e && e.source, 0);
      const b = n(e && e.target, 0);
      if (!a || !b) return;
      if (!map.has(a)) map.set(a, new Set());
      if (!map.has(b)) map.set(b, new Set());
      map.get(a).add(b);
      map.get(b).add(a);
    });
    return map;
  }

  function nodeScreenPos(state, nodeId) {
    const p = state.posById[n(nodeId, 0)];
    if (!p) return null;
    const t = state.transform;
    return {
      x: p.x * t.scale + t.tx,
      y: p.y * t.scale + t.ty,
    };
  }

  function nodeRadiusOnScreen(node, scale) {
    const deg = n(node && node.degree, 0);
    const influence = n(node && node.influence_score, 0);
    return Math.max(2.2, Math.min(9, (2.5 + deg * 0.26 + influence * 6) * scale));
  }

  function findNearestNode(state, x, y, tolerance) {
    const nodes = state.nodes || [];
    let bestNode = null;
    let bestDist = Number.POSITIVE_INFINITY;
    for (let i = 0; i < nodes.length; i += 1) {
      const node = nodes[i];
      const id = n(node && node.id, 0);
      if (!id) continue;
      const screen = nodeScreenPos(state, id);
      if (!screen) continue;
      const dx = screen.x - x;
      const dy = screen.y - y;
      const dist = Math.sqrt(dx * dx + dy * dy);
      const limit = Math.max(6, nodeRadiusOnScreen(node, state.transform.scale) + Math.max(4, tolerance || 0));
      if (dist <= limit && dist < bestDist) {
        bestDist = dist;
        bestNode = node;
      }
    }
    return bestNode;
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
    const selectedId = n(state.selectedNodeId, 0);
    const selectedNeighbors = selectedId && state.neighborMap.has(selectedId) ? state.neighborMap.get(selectedId) : new Set();

    ctx.save();
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#0b1630";
    ctx.fillRect(0, 0, width, height);

    for (let i = 0; i < edges.length; i += 1) {
      const e = edges[i];
      const sourceId = n(e && e.source, 0);
      const targetId = n(e && e.target, 0);
      const a = posById[sourceId];
      const b = posById[targetId];
      if (!a || !b) continue;
      const x1 = a.x * t.scale + t.tx;
      const y1 = a.y * t.scale + t.ty;
      const x2 = b.x * t.scale + t.tx;
      const y2 = b.y * t.scale + t.ty;
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(x2, y2);
      let stroke = edgeColor(e);
      if (selectedId) {
        const connected = sourceId === selectedId || targetId === selectedId;
        stroke = connected ? "rgba(56,189,248,0.98)" : "rgba(148,163,184,0.12)";
      }
      ctx.strokeStyle = stroke;
      const base = Math.max(0.35, Math.min(2.2, n(e && (e.weight_period || e.weight), 1) * 0.08));
      ctx.lineWidth = Math.max(0.3, (selectedId ? base * 1.25 : base) * t.scale);
      ctx.stroke();
    }

    for (let i = 0; i < nodes.length; i += 1) {
      const node = nodes[i];
      const p = posById[n(node && node.id, 0)];
      if (!p) continue;
      const x = p.x * t.scale + t.tx;
      const y = p.y * t.scale + t.ty;
      const id = n(node && node.id, 0);
      const isSelected = selectedId && id === selectedId;
      const isNeighbor = selectedId && selectedNeighbors.has(id);
      const rad = nodeRadiusOnScreen(node, t.scale) + (isSelected ? 1.1 : 0);
      ctx.beginPath();
      ctx.arc(x, y, rad, 0, Math.PI * 2);
      let fill = nodeColor(node);
      if (selectedId) {
        if (isSelected) fill = "rgba(34,197,94,0.95)";
        else if (isNeighbor) fill = "rgba(56,189,248,0.92)";
        else fill = "rgba(148,163,184,0.33)";
      }
      ctx.fillStyle = fill;
      ctx.fill();
    }

    if (state.hoverNodeId) {
      const hoverNode = state.nodeById.get(n(state.hoverNodeId, 0));
      const screen = hoverNode ? nodeScreenPos(state, n(state.hoverNodeId, 0)) : null;
      if (hoverNode && screen) {
        const label = String(hoverNode.label || hoverNode.username || hoverNode.id || "");
        if (label) {
          ctx.fillStyle = "rgba(226,232,240,0.95)";
          ctx.font = `${Math.max(10, Math.min(13, 10 + t.scale * 0.7))}px sans-serif`;
          ctx.fillText(label, screen.x + 6, screen.y - 8);
        }
      }
    } else if (!selectedId && t.scale >= 1.6) {
      // Only show sparse labels when zoomed in and nothing selected.
      const labelCandidates = nodes.slice().sort((a, b) => nodeScore(b) - nodeScore(a)).slice(0, 8);
      ctx.fillStyle = "rgba(226,232,240,0.92)";
      ctx.font = `${Math.max(10, Math.min(13, 10 + t.scale))}px sans-serif`;
      labelCandidates.forEach((node) => {
        const p = posById[n(node && node.id, 0)];
        if (!p) return;
        const x = p.x * t.scale + t.tx + 5;
        const y = p.y * t.scale + t.ty - 6;
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
    let moved = false;
    let downX = 0;
    let downY = 0;

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
      moved = false;
      startX = e.clientX;
      startY = e.clientY;
      downX = e.clientX;
      downY = e.clientY;
      startTx = t.tx;
      startTy = t.ty;
    });
    window.addEventListener("mousemove", (e) => {
      if (!dragging) return;
      const travel = Math.abs(e.clientX - downX) + Math.abs(e.clientY - downY);
      if (travel > 4) moved = true;
      t.tx = startTx + (e.clientX - startX);
      t.ty = startTy + (e.clientY - startY);
      scheduleDraw(state);
    });
    window.addEventListener("mouseup", () => {
      dragging = false;
    });

    canvas.addEventListener("mousemove", (e) => {
      if (dragging) return;
      const rect = canvas.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      const nearest = findNearestNode(state, x, y, 4);
      const nextHover = n(nearest && nearest.id, 0) || 0;
      if (nextHover !== n(state.hoverNodeId, 0)) {
        state.hoverNodeId = nextHover || null;
        canvas.style.cursor = nearest ? "pointer" : "grab";
        scheduleDraw(state);
      }
    });

    canvas.addEventListener("mouseleave", () => {
      if (state.hoverNodeId) {
        state.hoverNodeId = null;
        canvas.style.cursor = "grab";
        scheduleDraw(state);
      }
    });

    canvas.addEventListener("click", (e) => {
      if (moved) return;
      const rect = canvas.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      const nearest = findNearestNode(state, x, y, 5);
      if (!nearest) {
        state.selectedNodeId = 0;
        if (typeof state.onNodeSelect === "function") state.onNodeSelect(null);
        scheduleDraw(state);
        return;
      }
      const id = n(nearest && nearest.id, 0);
      state.selectedNodeId = n(state.selectedNodeId, 0) === id ? 0 : id;
      if (typeof state.onNodeSelect === "function") {
        if (state.selectedNodeId) {
          const neighbors = state.neighborMap.get(id) || new Set();
          state.onNodeSelect({
            id,
            label: String(nearest.label || nearest.username || nearest.id),
            username: String(nearest.username || ""),
            rank: String(nearest.rank || nearest.tier || ""),
            influence_score: n(nearest && nearest.influence_score, 0),
            degree: n(nearest && nearest.degree, 0),
            neighbor_count: neighbors.size,
            last_activity: String(nearest.last_activity || nearest.last_active || nearest.last_seen || ""),
          });
        } else {
          state.onNodeSelect(null);
        }
      }
      scheduleDraw(state);
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
      ? `GPU-рендер (${webglReady ? "webgl готов" : "canvas резерв"}): отрисовано ${subset.nodes.length}/${subset.originalNodes} узлов, ${subset.edges.length}/${subset.originalEdges} связей`
      : `GPU-рендер (${webglReady ? "webgl готов" : "canvas резерв"}): ${subset.nodes.length} узлов, ${subset.edges.length} связей`;
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
      root.innerHTML = '<div style="padding:0.8rem;color:var(--text-muted);">Canvas-рендер недоступен</div>';
      return;
    }

    const state = {
      canvas,
      ctx: ctx2d,
      dpr,
      mode,
      nodes: subset.nodes,
      edges: subset.edges,
      nodeById: new Map(subset.nodes.map((node) => [n(node && node.id, 0), node])),
      neighborMap: buildNeighborMap(subset.edges),
      posById: positionNodes(subset.nodes, width, height, mode),
      transform: { scale: 1, tx: 0, ty: 0 },
      selectedNodeId: n(payload.selectedNodeId, 0) || 0,
      hoverNodeId: null,
      onNodeSelect: typeof payload.onNodeSelect === "function" ? payload.onNodeSelect : null,
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
