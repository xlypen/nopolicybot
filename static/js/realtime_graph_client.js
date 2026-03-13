(function () {
  function normalizeChatIds(ids) {
    if (!Array.isArray(ids)) return [];
    var out = [];
    ids.forEach(function (v) {
      var n = Number(v);
      if (Number.isFinite(n) && n !== 0) out.push(String(Math.trunc(n)));
    });
    var seen = {};
    return out.filter(function (x) {
      if (seen[x]) return false;
      seen[x] = true;
      return true;
    });
  }

  function buildWsUrl(basePath, chatId) {
    var proto = window.location.protocol === "https:" ? "wss" : "ws";
    var host = window.location.host;
    return proto + "://" + host + String(basePath || "/api/v2/realtime/ws/") + encodeURIComponent(chatId);
  }

  window.createGraphRealtimeClient = function createGraphRealtimeClient(opts) {
    opts = opts || {};
    var chatIds = normalizeChatIds(opts.chatIds || []);
    var basePath = String(opts.basePath || "/api/v2/realtime/ws/");
    var pollUrl = String(opts.pollUrl || "");
    var pollIntervalMs = Math.max(1000, Number(opts.pollIntervalMs || 5000));
    var onStatus = typeof opts.onStatus === "function" ? opts.onStatus : function () {};
    var onDelta = typeof opts.onDelta === "function" ? opts.onDelta : function () {};
    var throttleMs = Math.max(300, Number(opts.throttleMs || 1200));

    var active = false;
    var sockets = {};
    var reconnectTimers = {};
    var pendingDelta = null;
    var deltaTimer = null;
    var pollTimer = null;
    var pollVersion = null;

    function status(s) {
      try {
        onStatus(String(s || ""));
      } catch (_e) {}
    }

    function clearReconnect(chatId) {
      if (reconnectTimers[chatId]) {
        clearTimeout(reconnectTimers[chatId]);
        reconnectTimers[chatId] = null;
      }
    }

    function scheduleDelta(msg) {
      pendingDelta = msg || {};
      if (deltaTimer) return;
      deltaTimer = setTimeout(function () {
        var data = pendingDelta || {};
        pendingDelta = null;
        deltaTimer = null;
        try {
          onDelta(data);
        } catch (_e) {}
      }, throttleMs);
    }

    function connectOne(chatId) {
      if (!active || sockets[chatId]) return;
      var ws = null;
      try {
        ws = new WebSocket(buildWsUrl(basePath, chatId));
      } catch (_e) {
        status("ws unavailable, poll fallback");
        return;
      }
      sockets[chatId] = ws;
      ws.onopen = function () {
        status("ws connected");
      };
      ws.onmessage = function (evt) {
        var msg = null;
        try {
          msg = JSON.parse(String(evt.data || "{}"));
        } catch (_e) {
          return;
        }
        if (!msg || typeof msg !== "object") return;
        var t = String(msg.type || "").toLowerCase();
        if (t === "connected" || t === "heartbeat" || t === "pong") return;
        if (t === "graph_delta") {
          scheduleDelta(msg);
        }
      };
      ws.onclose = function () {
        sockets[chatId] = null;
        if (!active) return;
        status("ws reconnecting...");
        clearReconnect(chatId);
        reconnectTimers[chatId] = setTimeout(function () {
          reconnectTimers[chatId] = null;
          connectOne(chatId);
        }, 2000);
      };
      ws.onerror = function () {
        status("ws error, poll fallback");
      };
    }

    function closeOne(chatId) {
      clearReconnect(chatId);
      var ws = sockets[chatId];
      sockets[chatId] = null;
      if (!ws) return;
      try {
        ws.close();
      } catch (_e) {}
    }

    function readVersion(body) {
      if (!body || typeof body !== "object") return null;
      if (body.version != null) return String(body.version);
      if (body.graph_version != null) return String(body.graph_version);
      if (body.meta && body.meta.version != null) return String(body.meta.version);
      return null;
    }

    function schedulePoll() {
      if (!active || !pollUrl) return;
      if (pollTimer) clearTimeout(pollTimer);
      pollTimer = setTimeout(pollTick, pollIntervalMs);
    }

    function pollTick() {
      if (!active || !pollUrl) return;
      fetch(pollUrl, { cache: "no-store", credentials: "same-origin" })
        .then(function (resp) { return resp.json(); })
        .then(function (body) {
          var nextVersion = readVersion(body);
          if (!nextVersion) return;
          if (pollVersion === null) {
            pollVersion = nextVersion;
            return;
          }
          if (nextVersion !== pollVersion) {
            pollVersion = nextVersion;
            status("poll delta");
            scheduleDelta({ type: "graph_delta", source: "poll", version: nextVersion });
          }
        })
        .catch(function () {
          status("poll error");
        })
        .finally(function () {
          schedulePoll();
        });
    }

    function start() {
      if (active) return;
      active = true;
      if (chatIds.length) {
        status("connecting...");
        chatIds.forEach(connectOne);
      } else if (!pollUrl) {
        status("no chats");
        return;
      }
      if (pollUrl) {
        status(chatIds.length ? "ws + poll" : "poll only");
        schedulePoll();
      }
    }

    function stop() {
      active = false;
      chatIds.forEach(closeOne);
      if (pollTimer) {
        clearTimeout(pollTimer);
        pollTimer = null;
      }
      pollVersion = null;
      if (deltaTimer) {
        clearTimeout(deltaTimer);
        deltaTimer = null;
      }
      pendingDelta = null;
      status("stopped");
    }

    return {
      start: start,
      stop: stop,
      isActive: function () {
        return !!active;
      },
    };
  };
})();
