/* Shared real-time connection for every signed-in page.
 * Exposes window.ChatApp = { socket, isOnline(id), lastSeen(id) } and fires
 * "chat:presence" on document when someone's online state changes. */
(function () {
  "use strict";

  var socket = io();
  var online = new Set();
  var lastSeen = {};
  var presenceReady = false;
  var banner = document.getElementById("connection-banner");

  window.ChatApp = {
    socket: socket,
    // True once the server has told us who is online (after each (re)connect).
    get presenceReady() { return presenceReady; },
    isOnline: function (userId) { return online.has(userId); },
    lastSeen: function (userId) { return lastSeen[userId] || null; }
  };

  function announce(detail) {
    document.dispatchEvent(new CustomEvent("chat:presence", { detail: detail }));
  }

  socket.on("connect", function () {
    if (banner) banner.hidden = true;
  });

  socket.on("disconnect", function (reason) {
    if (banner) banner.hidden = false;
    // The server closes our connection when a session ends (logout or password
    // change). Reconnect: a still-valid session gets back in, a revoked one is
    // refused below and sent to the sign-in page.
    if (reason === "io server disconnect") socket.connect();
  });

  socket.on("connect_error", function (error) {
    if (banner) banner.hidden = false;
    if (error && /rejected/i.test(error.message || "")) {
      window.location.href = "/login";
    }
  });

  socket.on("presence_state", function (data) {
    presenceReady = true;
    online.clear();
    (data.online || []).forEach(function (id) { online.add(id); });
    announce({ all: true });
  });

  socket.on("presence", function (data) {
    if (data.online) {
      online.add(data.user_id);
    } else {
      online.delete(data.user_id);
      if (data.last_seen) lastSeen[data.user_id] = data.last_seen;
    }
    announce({ userId: data.user_id });
  });
})();
