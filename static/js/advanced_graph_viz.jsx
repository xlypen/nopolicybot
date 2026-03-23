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

  function appendProgressive(root, epoch, target, rows, chunkSize, drawRow, done) {
    let idx = 0;
    const chunk = Math.max(20, n(chunkSize, 120));
    function frame() {
      if (!epochAlive(root, epoch)) return;
      const end = Math.min(rows.length, idx + chunk);
      for (; idx < end; idx++) drawRow(rows[idx], idx);
      if (idx < rows.length) {
        window.requestAnimationFrame(frame);
      } else if (typeof done === "function") {
        done();
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

  function renderSankey(root, graph, _payload) {
    const subset = pickSubset(graph, { maxNodes: 180, maxEdges: 180 });
    const rows = (subset.edges || []).slice(0, 18).map((e) => ({
      h: `${e.source} -> ${e.target}`,
      b: `flow: ${e.weight_period || e.weight || 0}`,
    }));
    cards(root, "Sankey-style flow (simplified)", rows.length ? rows : [{ h: "No data", b: "Нет рёбер для отображения" }], subset);
  }

  function renderHierarchy(root, graph, _payload) {
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

  function activityValue(node) {
    const m7 = n(node && node.messages_7d, 0);
    if (m7 > 0) return m7;
    const m30 = n(node && node.messages_30d, 0);
    if (m30 > 0) return m30;
    return Math.max(0, n(node && node.degree, 0));
  }

  function activityAxisCaption(nodes) {
    const has7 = (nodes || []).some((x) => n(x && x.messages_7d, 0) > 0);
    if (has7) return "Сообщения за 7 дн.";
    const has30 = (nodes || []).some((x) => n(x && x.messages_30d, 0) > 0);
    if (has30) return "Сообщения за 30 дн.";
    return "Число связей (degree)";
  }

  function _hash01(seed) {
    const x = Math.abs(Math.sin(seed * 9999) * 10000);
    return x - Math.floor(x);
  }

  function renderBubble(root, graph, payload) {
    const subset = pickSubset(graph, { maxNodes: 260, maxEdges: 520 });
    const nodes = subset.nodes || [];
    const edges = subset.edges || [];
    if (!nodes.length) {
      clear(root);
      renderTitle(root, "Bubble (activity vs influence)", subset);
      const empty = document.createElement("div");
      empty.className = "text-sm text-muted";
      empty.textContent = "Нет узлов для отображения.";
      root.appendChild(empty);
      return;
    }

    const selectedNodeId = n(payload && payload.selectedNodeId, 0);
    const onNodeSelect = payload && typeof payload.onNodeSelect === "function" ? payload.onNodeSelect : null;
    const epoch = nextEpoch(root);
    const neighborMap = (function buildNeighbors() {
      const map = new Map();
      edges.forEach((e) => {
        const a = n(e && e.source, 0);
        const b = n(e && e.target, 0);
        if (!a || !b) return;
        if (!map.has(a)) map.set(a, new Set());
        if (!map.has(b)) map.set(b, new Set());
        map.get(a).add(b);
        map.get(b).add(a);
      });
      return map;
    })();
    const selectedNeighbors = selectedNodeId && neighborMap.has(selectedNodeId) ? neighborMap.get(selectedNodeId) : new Set();

    let xmin = Infinity;
    let xmax = -Infinity;
    let ymin = Infinity;
    let ymax = -Infinity;
    nodes.forEach((node) => {
      const x = activityValue(node);
      const y = n(node && node.influence_score, 0);
      if (x < xmin) xmin = x;
      if (x > xmax) xmax = x;
      if (y < ymin) ymin = y;
      if (y > ymax) ymax = y;
    });
    if (!Number.isFinite(xmin) || xmin === xmax) {
      xmin = 0;
      xmax = Math.max(1, xmax + 1, xmin + 1);
    } else {
      const padX = Math.max(1, (xmax - xmin) * 0.06);
      xmin -= padX;
      xmax += padX;
    }
    if (!Number.isFinite(ymin) || ymin === ymax) {
      ymin = 0;
      ymax = Math.max(0.08, ymax + 0.02, 0.01);
    } else {
      const padY = Math.max(0.02, (ymax - ymin) * 0.08);
      ymin = Math.max(0, ymin - padY);
      ymax += padY;
    }

    const width = Math.max(480, root.clientWidth || 920);
    const height = Math.max(440, Math.min(640, root.clientHeight || 520));
    const margin = { t: 36, r: 28, b: 52, l: 56 };
    const plotW = width - margin.l - margin.r;
    const plotH = height - margin.t - margin.b;
    const sx = (v) => margin.l + ((v - xmin) / Math.max(1e-9, xmax - xmin)) * plotW;
    const sy = (v) => margin.t + (1 - (v - ymin) / Math.max(1e-9, ymax - ymin)) * plotH;

    const posById = {};
    nodes.forEach((node) => {
      const id = n(node.id, 0);
      if (!id) return;
      let px = sx(activityValue(node));
      let py = sy(n(node.influence_score, 0));
      const jx = (_hash01(id) - 0.5) * 14;
      const jy = (_hash01(id + 31) - 0.5) * 14;
      posById[id] = { x: px + jx, y: py + jy, node };
    });

    clear(root);
    renderTitle(root, "Bubble (activity vs influence)", subset);
    if (window.getComputedStyle(root).position === "static") {
      root.style.position = "relative";
    }
    const caption = document.createElement("div");
    caption.className = "text-sm text-muted mb-1";
    caption.textContent = `Оси: X — ${activityAxisCaption(nodes)}, Y — влияние. Линии связей — только после выбора узла (к соседям).`;
    root.appendChild(caption);

    const tooltip = document.createElement("div");
    tooltip.style.cssText =
      "position:absolute;pointer-events:none;padding:4px 8px;border-radius:6px;background:rgba(15,23,42,0.94);color:#e2e8f0;font-size:12px;display:none;z-index:6;";
    root.appendChild(tooltip);

    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("width", "100%");
    svg.setAttribute("height", String(height));
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("preserveAspectRatio", "xMidYMid meet");

    const edgeG = document.createElementNS("http://www.w3.org/2000/svg", "g");
    const axisG = document.createElementNS("http://www.w3.org/2000/svg", "g");
    const nodeG = document.createElementNS("http://www.w3.org/2000/svg", "g");
    svg.appendChild(edgeG);
    svg.appendChild(axisG);
    svg.appendChild(nodeG);

    const axisStroke = "rgba(148,163,184,0.45)";
    const axisText = "#94a3b8";
    const x0 = margin.l;
    const x1 = margin.l + plotW;
    const y0 = margin.t;
    const y1 = margin.t + plotH;
    [["M", x0, y1, "L", x1, y1], ["M", x0, y0, "L", x0, y1]].forEach((parts) => {
      const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
      p.setAttribute("d", `M ${parts[1]} ${parts[2]} L ${parts[4]} ${parts[5]}`);
      p.setAttribute("fill", "none");
      p.setAttribute("stroke", axisStroke);
      p.setAttribute("stroke-width", "1");
      axisG.appendChild(p);
    });
    const xl = document.createElementNS("http://www.w3.org/2000/svg", "text");
    xl.setAttribute("x", String((x0 + x1) / 2));
    xl.setAttribute("y", String(height - 14));
    xl.setAttribute("text-anchor", "middle");
    xl.setAttribute("fill", axisText);
    xl.setAttribute("font-size", "11");
    xl.textContent = activityAxisCaption(nodes);
    axisG.appendChild(xl);
    const yl = document.createElementNS("http://www.w3.org/2000/svg", "text");
    yl.setAttribute("x", "14");
    yl.setAttribute("y", String(margin.t + plotH / 2));
    yl.setAttribute("text-anchor", "middle");
    yl.setAttribute("fill", axisText);
    yl.setAttribute("font-size", "11");
    yl.setAttribute("transform", `rotate(-90 14 ${margin.t + plotH / 2})`);
    yl.textContent = "Влияние";
    axisG.appendChild(yl);

    root.appendChild(svg);

    // Рёбра только при выбранном узле — линии к соседям (без «паутины» на весь граф).
    const edgesToDraw = selectedNodeId
      ? edges.filter(
          (e) => n(e.source, 0) === selectedNodeId || n(e.target, 0) === selectedNodeId
        )
      : [];

    function drawNodes() {
      appendProgressive(root, epoch, nodeG, nodes, 50, (node) => {
        const id = n(node.id, 0);
        const p = posById[id];
        if (!p) return;
        const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
        const c = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        const av = activityValue(node);
        const baseR = Math.max(
          11,
          8 + Math.min(26, Math.log(1 + av) * 4.2 + n(node.degree, 0) * 0.55)
        );
        const isSel = selectedNodeId && id === selectedNodeId;
        const isNbr = selectedNodeId && selectedNeighbors.has(id);
        let fill = "rgba(56,189,248,0.92)";
        let stroke = "rgba(15,23,42,0.35)";
        if (selectedNodeId) {
          if (isSel) {
            fill = "rgba(34,197,94,0.95)";
            stroke = "rgba(15,23,42,0.55)";
          } else if (isNbr) {
            fill = "rgba(129,140,248,0.92)";
            stroke = "rgba(15,23,42,0.45)";
          } else {
            fill = "rgba(148,163,184,0.55)";
            stroke = "rgba(148,163,184,0.35)";
          }
        } else if (String(node.tier || "") === "core") {
          fill = "rgba(34,197,94,0.9)";
        } else if (String(node.tier || "") === "periphery") {
          fill = "rgba(167,139,250,0.9)";
        }
        c.setAttribute("cx", String(p.x));
        c.setAttribute("cy", String(p.y));
        c.setAttribute("r", String(isSel ? baseR + 2.5 : baseR));
        c.setAttribute("fill", fill);
        c.setAttribute("stroke", stroke);
        c.setAttribute("stroke-width", isSel ? "2" : "1.25");
        g.appendChild(c);
        const shortLabel = String(node.label || node.username || id).slice(0, 12);
        const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
        t.setAttribute("x", String(p.x));
        t.setAttribute("y", String(p.y + (baseR >= 14 ? 4 : 3)));
        t.setAttribute("text-anchor", "middle");
        t.setAttribute("fill", "rgba(248,250,252,0.98)");
        t.setAttribute("font-size", baseR >= 15 ? "10" : "9");
        t.setAttribute("font-weight", "600");
        t.setAttribute("pointer-events", "none");
        t.setAttribute("style", "text-shadow:0 0 3px rgba(15,23,42,0.9);");
        t.textContent = shortLabel;
        g.appendChild(t);
        g.style.cursor = "pointer";
        g.addEventListener("mouseenter", () => {
          tooltip.textContent = `${node.label || id} · активн.≈${activityValue(node)} · влияние ${n(node.influence_score, 0).toFixed(3)}`;
          tooltip.style.display = "block";
        });
        g.addEventListener("mousemove", (evt) => {
          const r = root.getBoundingClientRect();
          tooltip.style.left = `${evt.clientX - r.left + 10}px`;
          tooltip.style.top = `${evt.clientY - r.top + 10}px`;
        });
        g.addEventListener("mouseleave", () => {
          tooltip.style.display = "none";
        });
        g.addEventListener("click", (evt) => {
          evt.stopPropagation();
          if (!onNodeSelect) return;
          const neighbors = neighborMap.get(id) || new Set();
          onNodeSelect({
            id,
            label: String(node.label || node.username || node.id),
            username: String(node.username || ""),
            rank: String(node.rank || node.tier || ""),
            influence_score: n(node.influence_score, 0),
            degree: n(node.degree, 0),
            neighbor_count: neighbors.size,
            last_activity: String(node.last_activity || node.last_active || node.last_seen || ""),
          });
        });
        nodeG.appendChild(g);
      });
    }

    appendProgressive(
      root,
      epoch,
      edgeG,
      edgesToDraw,
      100,
      (e) => {
        const pa = posById[n(e.source, 0)];
        const pb = posById[n(e.target, 0)];
        if (!pa || !pb) return;
        const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
        line.setAttribute("x1", String(pa.x));
        line.setAttribute("y1", String(pa.y));
        line.setAttribute("x2", String(pb.x));
        line.setAttribute("y2", String(pb.y));
        line.setAttribute("stroke", "rgba(56,189,248,0.85)");
        line.setAttribute("stroke-opacity", "0.9");
        const sw = Math.max(0.7, Math.min(2.8, n(e.weight_period || e.weight, 1) * 0.14));
        line.setAttribute("stroke-width", String(sw));
        edgeG.appendChild(line);
      },
      drawNodes
    );

    svg.addEventListener("click", () => {
      if (onNodeSelect) onNodeSelect(null);
    });
  }

  function renderEgo(root, graph, _payload) {
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
      fn(root, graph, ctx || {});
    },
  };
})();
