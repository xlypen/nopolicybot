window.GraphRenderPipeline = (function () {
  const _pendingByRoot = new WeakMap();

  function _isAdvancedMode(mode) {
    const m = String(mode || "");
    const adv = window.AdvancedGraphViz;
    return !!(adv && Array.isArray(adv.modes) && adv.modes.includes(m));
  }

  function _pickEngine(mode) {
    const advanced = window.AdvancedGraphViz;
    const basic = window.GraphVisualizations;
    const needAdvanced = _isAdvancedMode(mode);
    if (needAdvanced) {
      if (advanced && typeof advanced.render === "function") return advanced;
      return null;
    }
    if (basic && typeof basic.render === "function") return basic;
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
    const defer = payload.defer !== false;
    const onUnavailable = typeof payload.onUnavailable === "function" ? payload.onUnavailable : function () {};

    const engine = _pickEngine(mode);
    if (engine) {
      _clearPending(root);
      engine.render(payload);
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
      const e = _pickEngine(mode);
      if (e) {
        _clearPending(root);
        e.render(payload);
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
      return !!_pickEngine(mode);
    },
    zoomBy() {},
    reset() {},
  };
})();
