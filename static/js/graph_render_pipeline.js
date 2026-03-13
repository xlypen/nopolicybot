window.GraphRenderPipeline = {
  render(ctx) {
    if (window.GraphVisualizations && typeof window.GraphVisualizations.render === "function") {
      return window.GraphVisualizations.render(ctx);
    }
    return null;
  },
  zoomBy() {},
  reset() {},
};
