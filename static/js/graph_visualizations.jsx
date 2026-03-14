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

  function panel(root, html) {
    root.innerHTML = html;
  }

  function n(v, fallback) {
    const x = Number(v);
    return Number.isFinite(x) ? x : fallback;
  }

  function nodeScore(node) {
    const influence = n(node && node.influence_score, 0);
    const degree = n(node && node.degree, 0);
    const centrality = n(node && node.centrality, 0);
    return influence * 100 + degree * 5 + centrality * 50;
  }

  function edgeScore(edge) {
    const w = n(edge && (edge.weight_period || edge.weight), 0);
    const bridge = n(edge && edge.bridge_score, 0);
    const cross = Number(edge && edge.community_id) === -1 ? 1 : 0;
    return bridge * 1000 + cross * 100 + w;
  }

  function pickSubset(graph, limits) {
    const srcNodes = Array.isArray(graph.nodes) ? graph.nodes : [];
    const srcEdges = Array.isArray(graph.edges) ? graph.edges : [];
    const maxNodes = Math.max(20, n(limits && limits.maxNodes, 220));
    const maxEdges = Math.max(40, n(limits && limits.maxEdges, 800));
    if (srcNodes.length <= maxNodes && srcEdges.length <= maxEdges) {
      return { nodes: srcNodes, edges: srcEdges, culled: false };
    }

    const byId = new Map();
    srcNodes.forEach((x) => {
      const id = n(x && x.id, 0);
      if (id) byId.set(id, x);
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

    const bridgeNodes = Array.from(bridgeIds)
      .map((id) => byId.get(id))
      .filter(Boolean)
      .sort((a, b) => nodeScore(b) - nodeScore(a));
    const ranked = srcNodes.slice().sort((a, b) => nodeScore(b) - nodeScore(a));
    const keep = [];
    const seen = new Set();
    bridgeNodes.concat(ranked).forEach((node) => {
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
    return { nodes: keptNodes, edges: keptEdges, culled: true, originalNodes: srcNodes.length, originalEdges: srcEdges.length };
  }

  function appendProgressive(root, epoch, target, items, chunkSize, drawItem, done) {
    let idx = 0;
    const chunk = Math.max(20, n(chunkSize, 180));
    function frame() {
      if (!epochAlive(root, epoch)) return;
      const end = Math.min(items.length, idx + chunk);
      for (; idx < end; idx++) drawItem(items[idx], idx);
      if (idx < items.length) {
        window.requestAnimationFrame(frame);
      } else if (typeof done === "function") {
        done();
      }
    }
    window.requestAnimationFrame(frame);
  }

  function mountRenderHeader(root, title, subset) {
    const note = document.createElement("div");
    note.className = "text-sm text-muted mb-1";
    const totalNodes = subset.culled ? subset.originalNodes : subset.nodes.length;
    const totalEdges = subset.culled ? subset.originalEdges : subset.edges.length;
    note.textContent = subset.culled
      ? `${title} (отрисовано ${subset.nodes.length}/${totalNodes} узлов, ${subset.edges.length}/${totalEdges} связей)`
      : `${title} (${subset.nodes.length} узлов, ${subset.edges.length} связей)`;
    root.appendChild(note);
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

  function computeForcePositions(nodes, edges, width, height) {
    const cx = width / 2;
    const cy = height / 2;
    const pos = {};
    const vel = {};
    const nodeRows = (nodes || []).slice();
    const idToNode = new Map();
    nodeRows.forEach((node, idx) => {
      const id = n(node && node.id, 0);
      if (!id) return;
      idToNode.set(id, node);
      const t = idx / Math.max(1, nodeRows.length);
      const angle = t * Math.PI * 8;
      const radius = Math.sqrt(t) * Math.min(width, height) * 0.35;
      pos[id] = {
        x: cx + Math.cos(angle) * radius,
        y: cy + Math.sin(angle) * radius,
      };
      vel[id] = { x: 0, y: 0 };
    });

    const edgeRows = (edges || []).map((e) => {
      const a = n(e && e.source, 0);
      const b = n(e && e.target, 0);
      if (!a || !b || !idToNode.has(a) || !idToNode.has(b)) return null;
      const weight = Math.max(0.2, n(e && (e.weight_period || e.weight), 1));
      return { a, b, weight };
    }).filter(Boolean);

    const ids = Array.from(idToNode.keys());
    const repulsion = 12000;
    const springK = 0.018;
    const centerPull = 0.006;
    const damping = 0.86;
    const minDist = 10;
    const maxIter = Math.max(120, Math.min(260, 120 + ids.length));
    for (let iter = 0; iter < maxIter; iter += 1) {
      const fx = {};
      const fy = {};
      ids.forEach((id) => { fx[id] = 0; fy[id] = 0; });

      for (let i = 0; i < ids.length; i += 1) {
        const idA = ids[i];
        const a = pos[idA];
        for (let j = i + 1; j < ids.length; j += 1) {
          const idB = ids[j];
          const b = pos[idB];
          const dx = b.x - a.x;
          const dy = b.y - a.y;
          const dist2 = Math.max(minDist * minDist, dx * dx + dy * dy);
          const dist = Math.sqrt(dist2);
          const f = repulsion / dist2;
          const nx = dx / dist;
          const ny = dy / dist;
          fx[idA] -= nx * f;
          fy[idA] -= ny * f;
          fx[idB] += nx * f;
          fy[idB] += ny * f;
        }
      }

      edgeRows.forEach((e) => {
        const a = pos[e.a];
        const b = pos[e.b];
        if (!a || !b) return;
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const dist = Math.max(minDist, Math.sqrt(dx * dx + dy * dy));
        const desired = Math.max(28, Math.min(165, 145 - Math.min(100, e.weight * 13)));
        const spring = (dist - desired) * springK;
        const nx = dx / dist;
        const ny = dy / dist;
        fx[e.a] += nx * spring;
        fy[e.a] += ny * spring;
        fx[e.b] -= nx * spring;
        fy[e.b] -= ny * spring;
      });

      ids.forEach((id) => {
        const p = pos[id];
        fx[id] += (cx - p.x) * centerPull;
        fy[id] += (cy - p.y) * centerPull;
        vel[id].x = (vel[id].x + fx[id]) * damping;
        vel[id].y = (vel[id].y + fy[id]) * damping;
        p.x += vel[id].x;
        p.y += vel[id].y;
        p.x = Math.max(22, Math.min(width - 22, p.x));
        p.y = Math.max(22, Math.min(height - 22, p.y));
      });
    }
    return pos;
  }

  function renderForce(root, graph, payload) {
    const subset = pickSubset(graph, { maxNodes: 220, maxEdges: 900 });
    const nodes = subset.nodes;
    const edges = subset.edges;
    const width = root.clientWidth || 920;
    const height = Math.max(420, root.clientHeight || 520);
    const epoch = nextEpoch(root);
    const pos = computeForcePositions(nodes, edges, width, height);
    const selectedNodeId = n(payload && payload.selectedNodeId, 0);
    const onNodeSelect = payload && typeof payload.onNodeSelect === "function" ? payload.onNodeSelect : null;
    const neighborsMap = buildNeighborMap(edges);
    const selectedNeighbors = selectedNodeId && neighborsMap.has(selectedNodeId) ? neighborsMap.get(selectedNodeId) : new Set();

    clear(root);
    mountRenderHeader(root, "Силовой граф", subset);
    if (window.getComputedStyle(root).position === "static") {
      root.style.position = "relative";
    }
    const tooltip = document.createElement("div");
    tooltip.style.position = "absolute";
    tooltip.style.pointerEvents = "none";
    tooltip.style.padding = "4px 7px";
    tooltip.style.borderRadius = "6px";
    tooltip.style.background = "rgba(15,23,42,0.92)";
    tooltip.style.color = "#e2e8f0";
    tooltip.style.fontSize = "12px";
    tooltip.style.display = "none";
    root.appendChild(tooltip);

    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("width", "100%");
    svg.setAttribute("height", String(height));
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    const edgeGroup = document.createElementNS("http://www.w3.org/2000/svg", "g");
    const nodeGroup = document.createElementNS("http://www.w3.org/2000/svg", "g");
    svg.appendChild(edgeGroup);
    svg.appendChild(nodeGroup);
    root.appendChild(svg);

    appendProgressive(
      root,
      epoch,
      edgeGroup,
      edges,
      260,
      (e) => {
        const a = pos[e.source];
        const b = pos[e.target];
        if (!a || !b) return;
        const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
        line.setAttribute("x1", String(a.x));
        line.setAttribute("y1", String(a.y));
        line.setAttribute("x2", String(b.x));
        line.setAttribute("y2", String(b.y));
        let stroke = "rgba(155,176,207,.55)";
        let alpha = 0.55;
        if (selectedNodeId) {
          const connected = n(e && e.source, 0) === selectedNodeId || n(e && e.target, 0) === selectedNodeId;
          stroke = connected ? "rgba(56,189,248,0.95)" : "rgba(148,163,184,0.14)";
          alpha = connected ? 0.95 : 0.14;
        }
        line.setAttribute("stroke", stroke);
        const w = Math.max(1, Math.min(6, n(e.weight_period || e.weight, 1)));
        line.setAttribute("stroke-opacity", String(alpha));
        line.setAttribute("stroke-width", String((selectedNodeId ? w * 0.55 : w * 0.35)));
        edgeGroup.appendChild(line);
      },
      () => {
        appendProgressive(root, epoch, nodeGroup, nodes, 180, (node) => {
          const p = pos[node.id];
          if (!p) return;
          const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
          const c = document.createElementNS("http://www.w3.org/2000/svg", "circle");
          const rad = 7 + Math.min(18, n(node.degree, 1) * 0.9);
          const id = n(node && node.id, 0);
          const isSelected = selectedNodeId && id === selectedNodeId;
          const isNeighbor = selectedNodeId && selectedNeighbors.has(id);
          let fill = "rgba(233,69,96,.85)";
          let stroke = "rgba(255,255,255,0.18)";
          let opacity = 0.92;
          if (selectedNodeId) {
            if (isSelected) {
              fill = "rgba(34,197,94,0.95)";
              stroke = "rgba(226,232,240,0.95)";
              opacity = 1;
            } else if (isNeighbor) {
              fill = "rgba(56,189,248,0.92)";
              stroke = "rgba(226,232,240,0.75)";
              opacity = 0.96;
            } else {
              fill = "rgba(148,163,184,0.35)";
              stroke = "rgba(148,163,184,0.18)";
              opacity = 0.45;
            }
          }
          c.setAttribute("cx", String(p.x));
          c.setAttribute("cy", String(p.y));
          c.setAttribute("r", String(isSelected ? rad + 1.4 : rad));
          c.setAttribute("fill", fill);
          c.setAttribute("stroke", stroke);
          c.setAttribute("stroke-width", isSelected ? "1.4" : "0.9");
          c.setAttribute("fill-opacity", String(opacity));
          g.appendChild(c);
          g.style.cursor = "pointer";
          g.addEventListener("mouseenter", () => {
            tooltip.textContent = String(node.label || node.username || node.id);
            tooltip.style.display = "block";
          });
          g.addEventListener("mousemove", (evt) => {
            const rect = root.getBoundingClientRect();
            tooltip.style.left = `${evt.clientX - rect.left + 12}px`;
            tooltip.style.top = `${evt.clientY - rect.top + 12}px`;
          });
          g.addEventListener("mouseleave", () => {
            tooltip.style.display = "none";
          });
          g.addEventListener("click", (evt) => {
            evt.stopPropagation();
            if (!onNodeSelect) return;
            const neighbors = neighborsMap.get(id) || new Set();
            onNodeSelect({
              id,
              label: String(node.label || node.username || node.id),
              username: String(node.username || ""),
              rank: String(node.rank || node.tier || ""),
              influence_score: n(node && node.influence_score, 0),
              degree: n(node && node.degree, 0),
              neighbor_count: neighbors.size,
              last_activity: String(node.last_activity || node.last_active || node.last_seen || ""),
            });
          });
          nodeGroup.appendChild(g);
        });
      }
    );
    svg.addEventListener("click", () => {
      if (onNodeSelect) onNodeSelect(null);
    });
  }

  function renderRadial(root, graph) {
    const subset = pickSubset(graph, { maxNodes: 260, maxEdges: 800 });
    const nodes = subset.nodes;
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
    const epoch = nextEpoch(root);

    clear(root);
    mountRenderHeader(root, "Radial", subset);
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("width", "100%");
    svg.setAttribute("height", String(height));
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    const nodeGroup = document.createElementNS("http://www.w3.org/2000/svg", "g");
    const labelGroup = document.createElementNS("http://www.w3.org/2000/svg", "g");
    svg.appendChild(nodeGroup);
    svg.appendChild(labelGroup);
    root.appendChild(svg);

    const points = [];
    comms.forEach((cid, ci) => {
      const ga = (ci / Math.max(1, comms.length)) * Math.PI * 2;
      const gcx = cx + Math.cos(ga) * groupR;
      const gcy = cy + Math.sin(ga) * groupR;
      const members = byComm[cid];
      members.forEach((node, i) => {
        const a = (i / Math.max(1, members.length)) * Math.PI * 2;
        points.push({
          x: gcx + Math.cos(a) * 56,
          y: gcy + Math.sin(a) * 56,
          cid,
        });
      });
      const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
      t.setAttribute("x", String(gcx));
      t.setAttribute("y", String(gcy - 68));
      t.setAttribute("text-anchor", "middle");
      t.setAttribute("fill", "#b6cae6");
      t.setAttribute("font-size", "12");
      t.textContent = `сообщество ${cid}`;
      labelGroup.appendChild(t);
    });
    appendProgressive(root, epoch, nodeGroup, points, 220, (p) => {
      const c = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      c.setAttribute("cx", String(p.x));
      c.setAttribute("cy", String(p.y));
      c.setAttribute("r", "8");
      c.setAttribute("fill", "rgba(79,70,229,.86)");
      nodeGroup.appendChild(c);
    });
  }

  function renderCommunity(root, graph) {
    const subset = pickSubset(graph, { maxNodes: 360, maxEdges: 1200 });
    const nodes = subset.nodes || [];
    const zones = {};
    nodes.forEach((n) => {
      const z = String(n.tier || "secondary");
      if (!zones[z]) zones[z] = [];
      zones[z].push(n);
    });
    const colors = { core: "#22c55e", secondary: "#38bdf8", periphery: "#a78bfa" };
    let html = `<div class="text-sm text-muted mb-1">${subset.culled ? `отрисовано ${subset.nodes.length}/${subset.originalNodes} узлов` : `${nodes.length} узлов`}</div>`;
    html += `<div class="row" style="margin-bottom:8px;">`;
    Object.keys(zones).forEach((z) => {
      html += `<span class="badge" style="background:${colors[z] || "#1a3b65"}22;border:1px solid ${colors[z] || "#1a3b65"}">${z}: ${zones[z].length}</span>`;
    });
    html += `</div><div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:8px;">`;
    nodes.forEach((n) => {
      const tier = n.tier || "secondary";
      html += `<div class="card" style="margin:0;border-color:${colors[tier] || "#1a3b65"}"><div class="title">${n.label || n.id}</div><div class="text-sm">уровень: ${tier}</div><div class="text-sm">степень: ${n.degree || 0}</div></div>`;
    });
    html += "</div>";
    panel(root, html);
  }

  function renderMatrix(root, graph) {
    const subset = pickSubset(graph, { maxNodes: 120, maxEdges: 2000 });
    const nodes = subset.nodes || [];
    const edges = subset.edges || [];
    const idToIdx = {};
    nodes.forEach((n, i) => { idToIdx[n.id] = i; });
    const count = nodes.length;
    const size = Math.min(680, Math.max(300, count * 22));
    const epoch = nextEpoch(root);
    clear(root);
    const note = document.createElement("div");
    note.className = "text-sm text-muted mb-1";
    note.textContent = subset.culled
      ? `Матрица связей (${count} x ${count}, отрисовано ${subset.edges.length}/${subset.originalEdges} связей)`
      : `Матрица связей (${count} x ${count})`;
    root.appendChild(note);
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("width", String(size));
    svg.setAttribute("height", String(size));
    svg.setAttribute("viewBox", `0 0 ${Math.max(1, count * 20)} ${Math.max(1, count * 20)}`);
    root.appendChild(svg);

    const cells = [];
    edges.forEach((e) => {
      const i = idToIdx[e.source];
      const j = idToIdx[e.target];
      if (i == null || j == null) return;
      const w = Math.max(0.15, Math.min(1, n(e.weight_period || e.weight, 1) / 10));
      cells.push({ x: i * 20, y: j * 20, w });
      cells.push({ x: j * 20, y: i * 20, w });
    });
    appendProgressive(root, epoch, svg, cells, 400, (c) => {
      const r = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      r.setAttribute("x", String(c.x));
      r.setAttribute("y", String(c.y));
      r.setAttribute("width", "18");
      r.setAttribute("height", "18");
      r.setAttribute("fill", `rgba(233,69,96,${c.w})`);
      svg.appendChild(r);
    });
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
      fn(root, graph, ctx || {});
    },
  };
})();
