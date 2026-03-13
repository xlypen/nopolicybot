window.GraphRenderPipeline = (function () {
  const _pendingByRoot = new WeakMap();
  const _DEFAULT_LARGE_THRESHOLDS = { nodes: 260, edges: 1100 };

  function _isAdvancedMode(mode) {
    const m = String(mode || "");
    const adv = window.AdvancedGraphViz;
    return !!(adv && Array.isArray(adv.modes) && adv.modes.includes(m));
  }

  function _metaOriginalCount(meta, key, fallback) {
    const ds = meta && typeof meta === "object" ? meta.downsample_meta : null;
    const v = ds && Object.prototype.hasOwnProperty.call(ds, key) ? Number(ds[key]) : Number(fallback);
    return Number.isFinite(v) ? v : Number(fallback || 0);
  }

  function _shouldUseLargeEngine(mode, data, payload) {
    const graph = data && typeof data === "object" ? data : {};
    const meta = graph.meta && typeof graph.meta === "object" ? graph.meta : {};
    const nodeCount = _metaOriginalCount(meta, "original_nodes", Array.isArray(graph.nodes) ? graph.nodes.length : 0);
    const edgeCount = _metaOriginalCount(meta, "original_edges", Array.isArray(graph.edges) ? graph.edges.length : 0);
    const forcedMode = String(mode || "") === "webgl";
    const preferredRenderer = String(meta.preferred_renderer || "").toLowerCase() === "webgl";
    const limits = (payload && payload.largeGraphThresholds) || {};
    const nodeLimit = Number.isFinite(Number(limits.nodes)) ? Number(limits.nodes) : _DEFAULT_LARGE_THRESHOLDS.nodes;
    const edgeLimit = Number.isFinite(Number(limits.edges)) ? Number(limits.edges) : _DEFAULT_LARGE_THRESHOLDS.edges;
    return !!(forcedMode || preferredRenderer || nodeCount >= nodeLimit || edgeCount >= edgeLimit);
  }

  function _pickEngine(mode, data, payload) {
    const webgl = window.GraphWebGLViz;
    if (webgl && typeof webgl.render === "function" && _shouldUseLargeEngine(mode, data, payload)) {
      return {
        engine: webgl,
        engineName: "webgl",
        effectiveMode: String(mode || "force"),
      };
    }
    const advanced = window.AdvancedGraphViz;
    const basic = window.GraphVisualizations;
    const needAdvanced = _isAdvancedMode(mode);
    if (needAdvanced) {
      if (advanced && typeof advanced.render === "function") {
        return { engine: advanced, engineName: "advanced", effectiveMode: String(mode || "force") };
      }
      return null;
    }
    if (basic && typeof basic.render === "function") {
      return { engine: basic, engineName: "basic", effectiveMode: String(mode || "force") };
    }
    return null;
  }

  function _setPending(root, token) {
    if (!root) return;
    _pendingByRoot.set(root, token);
  }

  function _isPending(root, token) {
    if (!root) return false;
    return _pendingByRoot.get(root) === token;
  }

  function _clearPending(root) {
    if (!root) return;
    _pendingByRoot.delete(root);
  }

  function render(ctx) {
    const payload = ctx || {};
    const root = payload.root || document.getElementById("graph-root");
    const mode = payload.mode || "force";
    const data = payload.data || {};
    const defer = payload.defer !== false;
    const onUnavailable = typeof payload.onUnavailable === "function" ? payload.onUnavailable : function () {};
    const onEngineSelected = typeof payload.onEngineSelected === "function" ? payload.onEngineSelected : null;

    const picked = _pickEngine(mode, data, payload);
    if (picked && picked.engine) {
      _clearPending(root);
      const nextPayload = { ...payload, mode: picked.effectiveMode };
      picked.engine.render(nextPayload);
      if (onEngineSelected) {
        onEngineSelected({
          engine: picked.engineName,
          mode: picked.effectiveMode,
        });
      }
      return true;
    }
    if (!defer) {
      onUnavailable();
      return false;
    }

    const token = { at: Date.now(), mode: String(mode || "") };
    _setPending(root, token);
    let attempts = 0;
    const maxAttempts = 25;

    function retry() {
      if (!_isPending(root, token)) return;
      const e = _pickEngine(mode, data, payload);
      if (e && e.engine) {
        _clearPending(root);
        const nextPayload = { ...payload, mode: e.effectiveMode };
        e.engine.render(nextPayload);
        if (onEngineSelected) {
          onEngineSelected({
            engine: e.engineName,
            mode: e.effectiveMode,
          });
        }
        return;
      }
      attempts += 1;
      if (attempts >= maxAttempts) {
        _clearPending(root);
        onUnavailable();
        return;
      }
      window.setTimeout(retry, 60);
    }

    window.setTimeout(retry, 30);
    return false;
  }

  return {
    render: render,
    isAdvancedMode: _isAdvancedMode,
    supportsMode(mode) {
      return !!_pickEngine(mode, {}, {});
    },
    zoomBy() {},
    reset() {},
  };
})();
