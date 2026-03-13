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
    var onStatus = typeof opts.onStatus === "function" ? opts.onStatus : function () {};
    var onDelta = typeof opts.onDelta === "function" ? opts.onDelta : function () {};
    var throttleMs = Math.max(300, Number(opts.throttleMs || 1200));

    var active = false;
    var sockets = {};
    var reconnectTimers = {};
    var pendingDelta = null;
    var deltaTimer = null;

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
        status("ws unavailable");
        return;
      }
      sockets[chatId] = ws;
      ws.onopen = function () {
        status("connected");
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
        status("reconnecting...");
        clearReconnect(chatId);
        reconnectTimers[chatId] = setTimeout(function () {
          reconnectTimers[chatId] = null;
          connectOne(chatId);
        }, 2000);
      };
      ws.onerror = function () {
        status("ws error");
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

    function start() {
      if (active) return;
      active = true;
      if (!chatIds.length) {
        status("no chats");
        return;
      }
      status("connecting...");
      chatIds.forEach(connectOne);
    }

    function stop() {
      active = false;
      chatIds.forEach(closeOne);
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
