/* One private conversation: history, sending, receipts, typing and presence.
 * All message text is inserted with textContent, never as HTML. */
(function () {
  "use strict";

  var chat = document.getElementById("chat");
  if (!chat) return;

  var F = window.ChatFormat;
  var socket = window.ChatApp.socket;
  var conversationId = Number(chat.dataset.conversationId);
  var meId = Number(chat.dataset.meId);
  var peerId = Number(chat.dataset.peerId);

  var messagesEl = document.getElementById("messages");
  var listEl = document.getElementById("message-list");
  var loadEarlierBtn = document.getElementById("load-earlier");
  var historyError = document.getElementById("history-error");
  var typingEl = document.getElementById("typing-indicator");
  var form = document.getElementById("composer");
  var input = document.getElementById("composer-input");
  var sendError = document.getElementById("send-error");
  var presenceDot = document.getElementById("presence-dot");
  var presenceText = document.getElementById("presence-text");

  var RANK = { pending: 0, failed: 0, sent: 1, delivered: 2, read: 3 };
  var nodesById = {};
  var nodesByClient = {};
  var pending = {}; // client_id -> { body, node }
  var oldestId = null;

  /* ---------------------------------------------------------------- helpers */

  function nearBottom() {
    return messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 120;
  }

  function scrollToBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function showSendError(text) {
    sendError.textContent = text || "";
    sendError.hidden = !text;
  }

  /* ------------------------------------------------------------- message UI */

  var STATUS_TEXT = {
    pending: ["\u2026", "Sending"],
    failed: ["!", "Not sent. Tap to retry"],
    sent: ["\u2713", "Sent"],
    delivered: ["\u2713\u2713", "Delivered"],
    read: ["\u2713\u2713", "Read"]
  };

  function applyStatus(node, state, force) {
    if (!node._status) return; // received messages have no status
    if (!force && RANK[state] < RANK[node._state]) return; // never go backwards
    node._state = state;
    node._status.textContent = STATUS_TEXT[state][0];
    node._status.title = STATUS_TEXT[state][1];
    node._status.className = "msg-status msg-status-" + state;
    node.classList.toggle("msg-pending", state === "pending");
    node.classList.toggle("msg-failed", state === "failed");
  }

  function stateOf(message) {
    if (message.read_at) return "read";
    if (message.delivered_at) return "delivered";
    return "sent";
  }

  function buildNode(message, state) {
    var mine = message.sender_id === meId;
    var node = F.el("div", "msg " + (mine ? "msg-out" : "msg-in"));
    var bubble = F.el("div", "msg-bubble");
    bubble.appendChild(F.el("div", "msg-body",
      message.deleted ? "This message was deleted" : message.body));
    var meta = F.el("div", "msg-meta");
    meta.appendChild(F.el("span", "msg-time", F.clock(message.created_at)));
    if (mine) {
      node._status = F.el("span", "msg-status");
      meta.appendChild(node._status);
      node._state = "pending";
      applyStatus(node, state || stateOf(message), true);
    }
    bubble.appendChild(meta);
    node.appendChild(bubble);
    return node;
  }

  // Server messages are ordered by id; unsent (pending) ones stay at the end.
  function insertSorted(node, id) {
    var cursor = listEl.lastElementChild;
    while (cursor && (!cursor.dataset.id || Number(cursor.dataset.id) > id)) {
      cursor = cursor.previousElementSibling;
    }
    listEl.insertBefore(node, cursor ? cursor.nextElementSibling : listEl.firstChild);
  }

  function track(node, message) {
    node.dataset.id = message.id;
    nodesById[message.id] = node;
    if (message.client_id) nodesByClient[message.client_id] = node;
    if (oldestId === null || message.id < oldestId) oldestId = message.id;
  }

  // Insert or update a message the server confirmed. Idempotent: the same
  // message can arrive via the ack, the broadcast and a history reload.
  function upsert(message) {
    var node = nodesById[message.id] ||
      (message.client_id && nodesByClient[message.client_id]);
    if (node) {
      var wasPending = !node.dataset.id;
      track(node, message);
      if (wasPending) {
        delete pending[message.client_id];
        var time = node.querySelector(".msg-time");
        if (time) time.textContent = F.clock(message.created_at);
        insertSorted(node, message.id);
      }
      applyStatus(node, stateOf(message), wasPending);
      return node;
    }
    node = buildNode(message);
    track(node, message);
    insertSorted(node, message.id);
    return node;
  }

  /* ---------------------------------------------------------------- history */

  function applyHistory(data, prepend) {
    var anchor = messagesEl.scrollHeight - messagesEl.scrollTop;
    var wasNear = nearBottom();
    data.messages.forEach(upsert);
    loadEarlierBtn.hidden = !data.has_more;
    if (prepend) {
      messagesEl.scrollTop = messagesEl.scrollHeight - anchor; // keep the view steady
    } else if (wasNear) {
      scrollToBottom();
    }
  }

  function fetchHistory(before) {
    var url = "/api/conversations/" + conversationId + "/messages" +
      (before ? "?before=" + before : "");
    return fetch(url, { credentials: "same-origin" }).then(function (response) {
      if (response.status === 401) { window.location.href = "/login"; return null; }
      if (!response.ok) throw new Error("status " + response.status);
      return response.json();
    });
  }

  function loadLatest(initial) {
    return fetchHistory(null)
      .then(function (data) {
        if (!data) return;
        historyError.hidden = true;
        applyHistory(data, false);
        if (initial) scrollToBottom();
        markReadSoon();
      })
      .catch(function () { historyError.hidden = false; });
  }

  loadEarlierBtn.addEventListener("click", function () {
    loadEarlierBtn.disabled = true;
    fetchHistory(oldestId)
      .then(function (data) { if (data) applyHistory(data, true); })
      .catch(function () { historyError.hidden = false; })
      .then(function () { loadEarlierBtn.disabled = false; });
  });

  /* ---------------------------------------------------------------- sending */

  function emitSend(clientId) {
    var entry = pending[clientId];
    if (!entry) return;
    applyStatus(entry.node, "pending", true);
    socket.timeout(8000).emit("send_message", {
      conversation_id: conversationId,
      body: entry.body,
      client_id: clientId
    }, function (error, response) {
      if (!pending[clientId]) return; // already reconciled via the broadcast
      if (error) {
        // No answer in time. The server may still have it; retrying with the
        // same client_id can never create a duplicate.
        applyStatus(entry.node, "failed", true);
        return;
      }
      if (!response || !response.ok) {
        // Rejected (too long, rate limited, ...): take it back and say why.
        entry.node.remove();
        delete pending[clientId];
        delete nodesByClient[clientId];
        if (!input.value) input.value = entry.body;
        showSendError((response && response.error) || "Message could not be sent.");
        return;
      }
      showSendError("");
      upsert(response.message);
      scrollToBottom();
    });
  }

  function sendFromInput() {
    var body = input.value.trim();
    if (!body) return;
    showSendError("");
    stopTyping();

    var clientId = F.uuid();
    var optimistic = {
      id: null, client_id: clientId, sender_id: meId, body: body, deleted: false,
      created_at: new Date().toISOString(), delivered_at: null, read_at: null
    };
    var node = buildNode(optimistic, "pending");
    node._status.addEventListener("click", function () {
      if (node._state === "failed") emitSend(clientId);
    });
    nodesByClient[clientId] = node;
    pending[clientId] = { body: body, node: node };
    listEl.appendChild(node);
    scrollToBottom();

    input.value = "";
    autosize();
    emitSend(clientId);
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    sendFromInput();
  });

  input.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      sendFromInput();
    }
  });

  function autosize() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 140) + "px";
  }

  /* ----------------------------------------------------------------- typing */

  var typingSent = false;
  var lastTypingEmit = 0;
  var typingTimer = null;
  var peerTypingTimer = null;

  function stopTyping() {
    clearTimeout(typingTimer);
    if (typingSent) {
      socket.emit("typing", { conversation_id: conversationId, typing: false });
      typingSent = false;
    }
  }

  input.addEventListener("input", function () {
    autosize();
    if (!input.value.trim()) { stopTyping(); return; }
    var now = Date.now();
    if (!typingSent || now - lastTypingEmit > 2500) {
      socket.emit("typing", { conversation_id: conversationId, typing: true });
      typingSent = true;
      lastTypingEmit = now;
    }
    clearTimeout(typingTimer);
    typingTimer = setTimeout(stopTyping, 3000);
  });

  socket.on("typing", function (data) {
    if (data.conversation_id !== conversationId || data.user_id !== peerId) return;
    clearTimeout(peerTypingTimer);
    typingEl.hidden = !data.typing;
    if (data.typing) {
      peerTypingTimer = setTimeout(function () { typingEl.hidden = true; }, 5000);
    }
  });

  /* -------------------------------------------------------- live events/state */

  var readTimer = null;
  function markReadSoon() {
    if (document.visibilityState !== "visible") return;
    clearTimeout(readTimer);
    readTimer = setTimeout(function () {
      socket.emit("mark_read", { conversation_id: conversationId });
    }, 300);
  }

  socket.on("message_new", function (message) {
    if (message.conversation_id !== conversationId) return;
    var stick = nearBottom() || message.sender_id === meId;
    upsert(message);
    if (message.sender_id === peerId) {
      typingEl.hidden = true;
      markReadSoon();
    }
    if (stick) scrollToBottom();
  });

  socket.on("messages_delivered", function (data) {
    if (data.conversation_id !== conversationId) return;
    data.message_ids.forEach(function (id) {
      if (nodesById[id]) applyStatus(nodesById[id], "delivered", false);
    });
  });

  socket.on("messages_read", function (data) {
    if (data.conversation_id !== conversationId || data.reader_id !== peerId) return;
    data.message_ids.forEach(function (id) {
      if (nodesById[id]) applyStatus(nodesById[id], "read", false);
    });
  });

  function updatePresence() {
    var app = window.ChatApp;
    var online = app.presenceReady ? app.isOnline(peerId) : chat.dataset.peerOnline === "1";
    presenceDot.className = "presence-dot" + (online ? " online" : "");
    presenceText.textContent = online ? "Online"
      : F.lastSeenText(app.lastSeen(peerId) || chat.dataset.peerLastSeen || null);
  }

  document.addEventListener("chat:presence", updatePresence);
  document.addEventListener("visibilitychange", markReadSoon);
  window.addEventListener("focus", markReadSoon);

  var firstConnect = true;
  socket.on("connect", function () {
    if (firstConnect) { firstConnect = false; return; }
    loadLatest(false); // catch up on anything missed while disconnected
  });

  updatePresence();
  autosize();
  loadLatest(true);
})();
