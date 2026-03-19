self.onmessage = function (e) {
  const nodes = (e.data && e.data.nodes) || [];
  self.postMessage({ type: "force_done", positions: nodes.map((n, i) => ({ id: n.id, x: i * 10, y: i * 10 })) });
};
