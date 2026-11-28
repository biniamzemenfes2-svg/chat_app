/* One private conversation: history, sending, receipts, typing, presence,
 * replies, edits, deletes, reactions, emoji, and in-conversation search.
 * All message text is inserted with textContent, never as HTML. */
(function () {
  "use strict";

  var chat = document.getElementById("chat");
  if (!chat) return;

  var F = window.ChatFormat;
  var Emoji = window.ChatEmoji;
  var socket = window.ChatApp.socket;
  var conversationId = Number(chat.dataset.conversationId);
  var meId = Number(chat.dataset.meId);
  var peerId = Number(chat.dataset.peerId);
  var peerName = chat.dataset.peerName;

  function $(id) { return document.getElementById(id); }
  var messagesEl = $("messages"), listEl = $("message-list");
  var loadEarlierBtn = $("load-earlier"), loadNewerBtn = $("load-newer");
  var historyError = $("history-error"), emptyEl = $("chat-empty");
  var jumpBtn = $("jump-latest"), jumpLabel = $("jump-label"), toastEl = $("chat-toast");
  var typingEl = $("typing-indicator");
  var contextBar = $("context-bar"), contextIcon = $("context-icon");
  var contextTitle = $("context-title"), contextPreview = $("context-preview");
  var form = $("composer"), input = $("composer-input"), emojiBtn = $("emoji-button");
  var sendError = $("send-error");
  var presenceDot = $("presence-dot"), presenceText = $("presence-text");
  var searchBar = $("chat-search"), searchInput = $("chat-search-input");
  var searchResults = $("chat-search-results");

  var RANK = { pending: 0, failed: 0, sent: 1, delivered: 2, read: 3 };
  var STATUS = {
    pending: ["clock", "Sending"],
    failed: ["exclamation-circle", "Not sent. Tap to retry"],
    sent: ["check2", "Sent"],
    delivered: ["check2-all", "Delivered"],
    read: ["check2-all", "Read"]
  };

  var nodesById = {};
  var nodesByClient = {};
  var pending = {}; // client_id -> { body, node, replyToId }
  var state = {
    hasOlder: false, hasNewer: false,
    windowed: false,   // the list shows a window in the middle of history, not the newest messages
    unseen: 0,         // messages that arrived out of view
    context: null,     // { kind: "reply" | "edit", message }
    loaded: false
  };
  var openMenuState = null;
  var pendingDelete = null;

  /* ---------------------------------------------------------------- helpers */

  function oneLine(text, limit) {
    text = (text || "").replace(/\s+/g, " ").trim();
    return text.length > limit ? text.slice(0, limit - 1) + "\u2026" : text;
  }

  function nearBottom(slack) {
    return messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < (slack || 120);
  }

  function scrollToBottom() { messagesEl.scrollTop = messagesEl.scrollHeight; }

  var toastTimer = null;
  function toast(text) {
    toastEl.textContent = text;
    toastEl.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toastEl.hidden = true; }, 2200);
  }

  function showSendError(text) {
    sendError.textContent = text || "";
    sendError.hidden = !text;
  }

  function nameOf(userId) { return userId === meId ? "You" : peerName; }

  function ids() {
    return Object.keys(nodesById).map(Number);
  }
  function oldestId() { var all = ids(); return all.length ? Math.min.apply(null, all) : null; }
  function newestId() { var all = ids(); return all.length ? Math.max.apply(null, all) : null; }

  /* ------------------------------------------------------------ message nodes */

  function stateOf(message) {
    if (message.read_at) return "read";
    if (message.delivered_at) return "delivered";
    return "sent";
  }

  function applyStatus(node, next, force) {
    if (!node._status) return; // received messages have no status
    if (!force && RANK[next] < RANK[node._state]) return; // never go backwards
    node._state = next;
    node._status.textContent = "";
    node._status.appendChild(F.icon(STATUS[next][0]));
    node._status.title = STATUS[next][1];
    node._status.className = "msg-status msg-status-" + next;
    node.classList.toggle("msg-pending", next === "pending");
    node.classList.toggle("msg-failed", next === "failed");
  }

  function myReaction(message) {
    var mine = (message.reactions || []).filter(function (r) { return r.user_ids.indexOf(meId) !== -1; })[0];
    return mine ? mine.emoji : null;
  }

  function quoteNode(quote) {
    var button = F.el("button", "msg-quote" + (quote.deleted ? " msg-quote-deleted" : ""));
    button.type = "button";
    button.appendChild(F.el("span", "msg-quote-name", nameOf(quote.sender_id)));
    button.appendChild(F.el("span", "msg-quote-text", quote.deleted ? "Message deleted" : quote.text));
    button.addEventListener("click", function () { jumpTo(quote.id); });
    return button;
  }

  function reactionsNode(message) {
    var wrap = F.el("div", "msg-reactions");
    message.reactions.forEach(function (reaction) {
      var mine = reaction.user_ids.indexOf(meId) !== -1;
      var chip = F.el("button", "reaction-chip" + (mine ? " mine" : ""));
      chip.type = "button";
      chip.appendChild(F.el("span", "emoji", reaction.emoji));
      if (reaction.user_ids.length > 1) chip.appendChild(F.el("span", "count", String(reaction.user_ids.length)));
      var who = reaction.user_ids.map(nameOf).join(", ");
      chip.title = who;
      chip.setAttribute("aria-label", reaction.emoji + " from " + who + (mine ? ". Tap to remove." : ""));
      chip.addEventListener("click", function () { react(message.id, reaction.emoji); });
      wrap.appendChild(chip);
    });
    return wrap;
  }

  // (Re)render a message node's contents from its data.
  function fill(node, message) {
    node._msg = message;
    node.textContent = "";
    node.classList.toggle("msg-deleted", !!message.deleted);

    var col = F.el("div", "msg-col");
    var bubble = F.el("div", "msg-bubble");
    if (message.reply_to && !message.deleted) bubble.appendChild(quoteNode(message.reply_to));
    bubble.appendChild(F.el("div", "msg-body", message.deleted ? "This message was deleted" : message.body));

    var meta = F.el("div", "msg-meta");
    if (message.edited_at && !message.deleted) meta.appendChild(F.el("span", "msg-edited", "edited"));
    meta.appendChild(F.el("span", "msg-time", F.clock(message.created_at)));
    node._status = null;
    if (message.sender_id === meId) {
      node._status = F.el("span", "msg-status");
      node._status.addEventListener("click", function () {
        if (node._state === "failed" && node._clientId) emitSend(node._clientId);
      });
      meta.appendChild(node._status);
    }
    bubble.appendChild(meta);
    col.appendChild(bubble);
    if (message.reactions && message.reactions.length && !message.deleted) {
      col.appendChild(reactionsNode(message));
    }
    node.appendChild(col);

    if (message.id && !message.deleted) {
      var button = F.el("button", "icon-btn msg-menu-btn");
      button.type = "button";
      button.setAttribute("aria-label", "Message actions");
      button.setAttribute("aria-haspopup", "menu");
      button.appendChild(F.icon("chevron-down"));
      button.addEventListener("click", function (event) {
        event.stopPropagation();
        openMenu(node, button);
      });
      node.appendChild(button);
    }
    if (node._status) applyStatus(node, node._state || stateOf(message), true);
  }

  function buildNode(message, initialState) {
    var node = F.el("div", "msg " + (message.sender_id === meId ? "msg-out" : "msg-in"));
    node._state = initialState || stateOf(message);
    fill(node, message);
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
  }

  // Insert or update a message the server confirmed. Idempotent: the same
  // message can arrive via the ack, the broadcast and a history reload.
  function upsert(message) {
    var node = nodesById[message.id] ||
      (message.client_id && nodesByClient[message.client_id]);
    if (node) {
      var wasPending = !node.dataset.id;
      track(node, message);
      fill(node, message);
      if (wasPending) {
        delete pending[message.client_id];
        if (!state.windowed) insertSorted(node, message.id);
      }
      applyStatus(node, stateOf(message), wasPending);
      return node;
    }
    node = buildNode(message);
    track(node, message);
    insertSorted(node, message.id);
    return node;
  }

  function refreshSeparators() {
    Array.prototype.slice.call(listEl.querySelectorAll(".date-sep")).forEach(function (n) { n.remove(); });
    var previous = null;
    Array.prototype.slice.call(listEl.children).forEach(function (node) {
      var iso = node._msg && node._msg.created_at;
      if (!iso) return;
      var day = new Date(iso).toDateString();
      if (day !== previous) {
        var sep = F.el("div", "date-sep");
        sep.appendChild(F.el("span", null, F.dayLabel(iso)));
        listEl.insertBefore(sep, node);
        previous = day;
      }
    });
    emptyEl.hidden = !(state.loaded && !listEl.querySelector(".msg"));
  }

  function updateEdges() {
    loadEarlierBtn.hidden = !state.hasOlder;
    loadNewerBtn.hidden = !state.hasNewer;
    updateJump();
  }

  function updateJump() {
    var show = state.windowed || !nearBottom(400);
    jumpBtn.hidden = !show;
    jumpLabel.textContent = state.unseen > 0
      ? state.unseen + (state.unseen === 1 ? " new message" : " new messages")
      : "Latest messages";
  }

  messagesEl.addEventListener("scroll", function () {
    if (nearBottom()) state.unseen = 0;
    updateJump();
  });

  /* ------------------------------------------------------------ history/windows */

  function fetchHistory(params) {
    var query = Object.keys(params).map(function (key) {
      return key + "=" + encodeURIComponent(params[key]);
    }).join("&");
    var url = "/api/conversations/" + conversationId + "/messages" + (query ? "?" + query : "");
    return fetch(url, { credentials: "same-origin" }).then(function (response) {
      if (response.status === 401) { window.location.href = "/login"; return null; }
      if (!response.ok) throw new Error("status " + response.status);
      return response.json();
    });
  }

  function resetList() {
    listEl.textContent = "";
    nodesById = {};
    nodesByClient = {};
    Object.keys(pending).forEach(function (cid) { nodesByClient[cid] = pending[cid].node; });
  }

  function reattachPending() {
    Object.keys(pending).forEach(function (cid) { listEl.appendChild(pending[cid].node); });
  }

  // opts.initial: first load; opts.reset: replace the list with the newest page.
  function loadLatest(opts) {
    opts = opts || {};
    return fetchHistory({})
      .then(function (data) {
        if (!data) return;
        historyError.hidden = true;
        var wasNear = nearBottom();
        if (opts.reset) resetList();
        data.messages.forEach(upsert);
        if (opts.reset) reattachPending();
        if (opts.initial || opts.reset) state.hasOlder = data.has_more;
        state.hasNewer = false;
        state.windowed = false;
        state.unseen = 0;
        state.loaded = true;
        refreshSeparators();
        updateEdges();
        if (opts.initial || opts.reset || wasNear) scrollToBottom();
        markReadSoon();
      })
      .catch(function () { historyError.hidden = false; });
  }

  loadEarlierBtn.addEventListener("click", function () {
    var before = oldestId();
    if (before === null) return;
    loadEarlierBtn.disabled = true;
    fetchHistory({ before: before })
      .then(function (data) {
        if (!data) return;
        var anchor = messagesEl.scrollHeight - messagesEl.scrollTop;
        data.messages.forEach(upsert);
        state.hasOlder = data.has_more;
        refreshSeparators();
        updateEdges();
        messagesEl.scrollTop = messagesEl.scrollHeight - anchor; // keep the view steady
      })
      .catch(function () { historyError.hidden = false; })
      .then(function () { loadEarlierBtn.disabled = false; });
  });

  loadNewerBtn.addEventListener("click", function () {
    var after = newestId();
    if (after === null) return;
    loadNewerBtn.disabled = true;
    fetchHistory({ after: after })
      .then(function (data) {
        if (!data) return;
        data.messages.forEach(upsert);
        state.hasNewer = data.has_newer;
        if (!data.has_newer) { state.windowed = false; state.unseen = 0; markReadSoon(); }
        refreshSeparators();
        updateEdges();
      })
      .catch(function () { historyError.hidden = false; })
      .then(function () { loadNewerBtn.disabled = false; });
  });

  jumpBtn.addEventListener("click", function () {
    if (state.windowed) loadLatest({ reset: true });
    else { scrollToBottom(); state.unseen = 0; updateJump(); }
  });

  function focusNode(node) {
    var top = node.getBoundingClientRect().top - messagesEl.getBoundingClientRect().top +
      messagesEl.scrollTop - messagesEl.clientHeight / 3;
    messagesEl.scrollTop = Math.max(0, top);
    node.classList.remove("flash");
    void node.offsetWidth; // restart the animation
    node.classList.add("flash");
    setTimeout(function () { node.classList.remove("flash"); }, 1800);
  }

  // Scroll to a message, loading the surrounding window first if it isn't on screen.
  function jumpTo(id) {
    var node = nodesById[id];
    if (node && node.parentNode === listEl) { focusNode(node); return; }
    fetchHistory({ around: id })
      .then(function (data) {
        if (!data) return;
        resetList();
        data.messages.forEach(upsert);
        state.hasOlder = data.has_more;
        state.hasNewer = data.has_newer;
        state.windowed = data.has_newer;
        state.unseen = 0;
        refreshSeparators();
        updateEdges();
        if (nodesById[id]) focusNode(nodesById[id]);
        if (!state.windowed) markReadSoon();
      })
      .catch(function () { toast("Couldn't load that message."); });
  }

  /* ---------------------------------------------------------------- sending */

  function emitSend(clientId) {
    var entry = pending[clientId];
    if (!entry) return;
    applyStatus(entry.node, "pending", true);
    var payload = { conversation_id: conversationId, body: entry.body, client_id: clientId };
    if (entry.replyToId) payload.reply_to_id = entry.replyToId;
    socket.timeout(8000).emit("send_message", payload, function (error, response) {
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
        autosize();
        showSendError((response && response.error) || "Message could not be sent.");
        refreshSeparators();
        return;
      }
      showSendError("");
      upsert(response.message);
      scrollToBottom();
      refreshSeparators();
    });
  }

  function doSend(body, replyTo) {
    var clientId = F.uuid();
    var optimistic = {
      id: null, client_id: clientId, sender_id: meId, body: body, deleted: false,
      created_at: new Date().toISOString(), delivered_at: null, read_at: null,
      edited_at: null, reactions: [],
      reply_to: replyTo ? {
        id: replyTo.id, sender_id: replyTo.sender_id,
        text: oneLine(replyTo.body, 120), deleted: false
      } : null
    };
    var node = buildNode(optimistic, "pending");
    node._clientId = clientId;
    nodesByClient[clientId] = node;
    pending[clientId] = { body: body, node: node, replyToId: replyTo ? replyTo.id : null };
    listEl.appendChild(node);
    state.loaded = true;
    refreshSeparators();
    scrollToBottom();
    emitSend(clientId);
  }

  function sendFromInput() {
    var body = input.value.trim();
    if (!body) return;
    var context = state.context;
    if (context && context.kind === "edit") { submitEdit(context.message, body); return; }

    var replyTo = context && context.kind === "reply" ? context.message : null;
    showSendError("");
    stopTyping();
    input.value = "";
    autosize();
    setContext(null);
    if (state.windowed) loadLatest({ reset: true }).then(function () { doSend(body, replyTo); });
    else doSend(body, replyTo);
  }

  form.addEventListener("submit", function (event) { event.preventDefault(); sendFromInput(); });

  input.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      sendFromInput();
    } else if (event.key === "Escape" && state.context) {
      setContext(null);
    }
  });

  function autosize() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 140) + "px";
  }

  /* --------------------------------------------------- reply / edit / delete */

  function setContext(kind, message) {
    var wasEdit = state.context && state.context.kind === "edit";
    state.context = kind ? { kind: kind, message: message } : null;
    contextBar.hidden = !kind;
    if (!kind) {
      if (wasEdit) { input.value = ""; autosize(); } // the box held the text being edited
      return;
    }
    contextIcon.className = "bi " + (kind === "reply" ? "bi-reply" : "bi-pencil");
    contextTitle.textContent = kind === "reply"
      ? "Replying to " + (message.sender_id === meId ? "yourself" : peerName)
      : "Editing message";
    contextPreview.textContent = oneLine(message.body, 120);
    if (kind === "edit") { input.value = message.body; autosize(); }
    input.focus();
  }

  $("context-cancel").addEventListener("click", function () { setContext(null); });

  function submitEdit(message, body) {
    if (body === message.body) { setContext(null); return; }
    socket.timeout(8000).emit("edit_message", { message_id: message.id, body: body }, function (error, response) {
      if (error) { showSendError("Couldn't save the edit. Check your connection."); return; }
      if (!response || !response.ok) { showSendError((response && response.error) || "Couldn't save the edit."); return; }
      showSendError("");
      setContext(null);
      upsert(response.message);
    });
  }

  function askDelete(message) {
    pendingDelete = message;
    var modalEl = $("delete-modal");
    if (window.bootstrap && window.bootstrap.Modal) {
      window.bootstrap.Modal.getOrCreateInstance(modalEl).show();
    } else if (window.confirm("Delete this message for everyone?")) {
      confirmDelete();
    }
  }

  function confirmDelete() {
    var message = pendingDelete;
    pendingDelete = null;
    var modalEl = $("delete-modal");
    if (window.bootstrap && window.bootstrap.Modal) {
      var instance = window.bootstrap.Modal.getInstance(modalEl);
      if (instance) instance.hide();
    }
    if (!message) return;
    socket.timeout(8000).emit("delete_message", { message_id: message.id }, function (error, response) {
      if (error) { toast("Couldn't delete. Check your connection."); return; }
      if (!response || !response.ok) { toast((response && response.error) || "Couldn't delete."); return; }
      upsert(response.message);
      refreshQuotesOf(response.message);
    });
  }

  $("delete-confirm").addEventListener("click", confirmDelete);

  // A quote shows the *current* state of the message it points at.
  function refreshQuotesOf(message) {
    Object.keys(nodesById).forEach(function (key) {
      var node = nodesById[key];
      var data = node._msg;
      if (!data || !data.reply_to || data.reply_to.id !== message.id) return;
      data.reply_to.deleted = !!message.deleted;
      data.reply_to.text = message.deleted ? "" : oneLine(message.body, 120);
      fill(node, data);
    });
  }

  function react(messageId, emoji) {
    socket.timeout(8000).emit("react", { message_id: messageId, emoji: emoji }, function (error, response) {
      if (error) { toast("Couldn't react. Check your connection."); return; }
      if (!response || !response.ok) { toast((response && response.error) || "Couldn't react."); return; }
      upsert(response.message);
    });
  }

  /* ------------------------------------------------------------ action menu */

  function closeMenu() {
    if (!openMenuState) return;
    openMenuState.el.remove();
    openMenuState.node.classList.remove("menu-open");
    openMenuState = null;
  }

  function menuItem(iconName, label, onClick, danger) {
    var item = F.el("button", "msg-menu-item" + (danger ? " danger" : ""));
    item.type = "button";
    item.setAttribute("role", "menuitem");
    item.appendChild(F.icon(iconName));
    item.appendChild(F.el("span", null, label));
    item.addEventListener("click", function () { closeMenu(); onClick(); });
    return item;
  }

  function openMenu(node, anchor) {
    closeMenu();
    if (Emoji.isOpen()) Emoji.close();
    var message = node._msg;
    var mine = message.sender_id === meId;
    var menu = F.el("div", "msg-menu");
    menu.setAttribute("role", "menu");

    var row = F.el("div", "msg-menu-react");
    var current = myReaction(message);
    Emoji.QUICK.forEach(function (emoji) {
      var button = F.el("button", current === emoji ? "picked" : null, emoji);
      button.type = "button";
      button.setAttribute("aria-label", "React with " + emoji);
      button.addEventListener("click", function () { closeMenu(); react(message.id, emoji); });
      row.appendChild(button);
    });
    var more = F.el("button", null);
    more.type = "button";
    more.setAttribute("aria-label", "More reactions");
    more.appendChild(F.icon("plus-lg"));
    more.addEventListener("click", function () {
      closeMenu();
      Emoji.open({ anchor: anchor, onPick: function (emoji) { react(message.id, emoji); } });
    });
    row.appendChild(more);
    menu.appendChild(row);

    menu.appendChild(menuItem("reply", "Reply", function () { setContext("reply", message); }));
    menu.appendChild(menuItem("clipboard", "Copy text", function () {
      F.copyText(message.body).then(function (ok) { toast(ok ? "Copied" : "Couldn't copy"); });
    }));
    if (mine && message.type === "text") {
      menu.appendChild(menuItem("pencil", "Edit", function () { setContext("edit", message); }));
    }
    if (mine) {
      menu.appendChild(menuItem("trash", "Delete", function () { askDelete(message); }, true));
    }

    document.body.appendChild(menu);
    var rect = anchor.getBoundingClientRect();
    var left = mine ? rect.right - menu.offsetWidth : rect.left;
    left = Math.min(Math.max(8, left), window.innerWidth - menu.offsetWidth - 8);
    var top = rect.bottom + 4;
    if (top + menu.offsetHeight > window.innerHeight - 8) top = Math.max(8, rect.top - menu.offsetHeight - 4);
    menu.style.left = left + "px";
    menu.style.top = top + "px";
    node.classList.add("menu-open");
    openMenuState = { el: menu, node: node };
    var first = menu.querySelector("button");
    if (first) first.focus();
  }

  document.addEventListener("mousedown", function (event) {
    if (openMenuState && !openMenuState.el.contains(event.target)) closeMenu();
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closeMenu();
  });
  messagesEl.addEventListener("scroll", closeMenu);

  /* ------------------------------------------------------------------ emoji */

  function insertAtCursor(emoji) {
    var start = input.selectionStart;
    var end = input.selectionEnd;
    if (typeof input.setRangeText === "function" && typeof start === "number") {
      input.setRangeText(emoji, start, end, "end");
    } else {
      input.value += emoji;
    }
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.focus();
  }

  emojiBtn.addEventListener("click", function () {
    if (Emoji.isOpen()) { Emoji.close(); return; }
    Emoji.open({ anchor: emojiBtn, keepOpen: true, onPick: insertAtCursor })
      .catch(function () { toast("Couldn't load emoji."); });
  });

  /* -------------------------------------------------- search in conversation */

  var searchTimer = null;
  var searchSeq = 0;

  function openSearch() {
    searchBar.hidden = false;
    searchInput.focus();
  }

  function closeSearch() {
    searchBar.hidden = true;
    searchInput.value = "";
    searchResults.hidden = true;
    searchResults.textContent = "";
    searchSeq++;
  }

  function highlighted(text, query) {
    var wrap = F.el("span", "search-hit-text");
    var lower = text.toLowerCase();
    var needle = query.toLowerCase();
    var at = lower.indexOf(needle);
    if (at === -1) { wrap.textContent = text; return wrap; }
    var from = Math.max(0, at - 30);
    var snippet = text.slice(from, at + needle.length + 70);
    var offset = at - from;
    if (from > 0) wrap.appendChild(document.createTextNode("\u2026"));
    wrap.appendChild(document.createTextNode(snippet.slice(0, offset)));
    wrap.appendChild(F.el("mark", null, snippet.slice(offset, offset + needle.length)));
    wrap.appendChild(document.createTextNode(snippet.slice(offset + needle.length)));
    return wrap;
  }

  function renderHits(messages, query) {
    searchResults.textContent = "";
    searchResults.hidden = false;
    if (!messages.length) {
      searchResults.appendChild(F.el("div", "search-empty", "No messages found"));
      return;
    }
    messages.forEach(function (message) {
      var hit = F.el("button", "search-hit");
      hit.type = "button";
      var meta = F.el("span", "search-hit-meta");
      meta.appendChild(F.el("span", null, nameOf(message.sender_id)));
      meta.appendChild(F.el("span", null, F.shortDate(message.created_at)));
      hit.appendChild(meta);
      hit.appendChild(highlighted(message.body || "", query));
      hit.addEventListener("click", function () {
        searchResults.hidden = true;
        jumpTo(message.id);
      });
      searchResults.appendChild(hit);
    });
  }

  function runSearch() {
    var query = searchInput.value.trim();
    if (query.length < 2) { searchResults.hidden = true; searchResults.textContent = ""; return; }
    var seq = ++searchSeq;
    fetch("/api/conversations/" + conversationId + "/search?q=" + encodeURIComponent(query),
      { credentials: "same-origin" })
      .then(function (response) {
        if (seq !== searchSeq) return null;
        if (response.status === 429) { toast("Too many searches. Wait a moment."); return null; }
        if (response.status === 401) { window.location.href = "/login"; return null; }
        if (!response.ok) throw new Error("status " + response.status);
        return response.json();
      })
      .then(function (data) { if (data && seq === searchSeq) renderHits(data.messages, query); })
      .catch(function () { if (seq === searchSeq) toast("Search failed. Try again."); });
  }

  $("chat-search-toggle").addEventListener("click", function () {
    if (searchBar.hidden) openSearch(); else closeSearch();
  });
  $("chat-search-open").addEventListener("click", openSearch);
  $("chat-search-close").addEventListener("click", closeSearch);
  searchInput.addEventListener("input", function () {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(runSearch, 300);
  });
  searchInput.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closeSearch();
  });

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
    if (!input.value.trim() || (state.context && state.context.kind === "edit")) { stopTyping(); return; }
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
    if (document.visibilityState !== "visible" || state.windowed) return;
    clearTimeout(readTimer);
    readTimer = setTimeout(function () {
      socket.emit("mark_read", { conversation_id: conversationId });
    }, 300);
  }

  socket.on("message_new", function (message) {
    if (message.conversation_id !== conversationId) return;
    if (state.windowed) {
      // We're looking at old history; don't splice a new message into it.
      if (message.sender_id !== meId) state.unseen += 1;
      updateJump();
      return;
    }
    var stick = nearBottom() || message.sender_id === meId;
    upsert(message);
    state.loaded = true;
    refreshSeparators();
    if (message.sender_id === peerId) {
      typingEl.hidden = true;
      if (stick) markReadSoon(); else state.unseen += 1;
    }
    if (stick) scrollToBottom();
    updateJump();
  });

  // Edits, deletes and reactions.
  socket.on("message_updated", function (message) {
    if (message.conversation_id !== conversationId) return;
    if (nodesById[message.id]) upsert(message);
    refreshQuotesOf(message);
    var context = state.context;
    if (context && context.message.id === message.id) {
      if (message.deleted) setContext(null); else context.message = message;
    }
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
    presenceDot.className = "presence-dot presence-dot-corner" + (online ? " online" : "");
    presenceText.textContent = online ? "Online"
      : F.lastSeenText(app.lastSeen(peerId) || chat.dataset.peerLastSeen || null);
  }

  document.addEventListener("chat:presence", updatePresence);
  document.addEventListener("visibilitychange", markReadSoon);
  window.addEventListener("focus", markReadSoon);

  var firstConnect = true;
  socket.on("connect", function () {
    if (firstConnect) { firstConnect = false; return; }
    // Catch up on anything missed while disconnected (also refreshes edits/reactions).
    if (!state.windowed) loadLatest({});
  });

  updatePresence();
  autosize();
  loadLatest({ initial: true });
})();
