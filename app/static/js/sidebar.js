/* Sidebar (every app page): live conversation list, user search, and the
 * small actions on the welcome pane. */
(function () {
  "use strict";

  var shell = document.getElementById("app-shell");
  if (!shell) return;

  var F = window.ChatFormat;
  var socket = window.ChatApp.socket;
  var meId = Number(shell.dataset.meId);
  var activeId = Number(shell.dataset.activeConversation) || null;

  var listEl = document.getElementById("conversation-list");
  var emptyEl = document.getElementById("conversations-empty");
  var errorEl = document.getElementById("conversations-error");
  var searchInput = document.getElementById("user-search");
  var searchStatus = document.getElementById("search-status");
  var resultsEl = document.getElementById("search-results");
  var welcomeEmpty = document.getElementById("welcome-empty");

  var conversations = [];

  /* ---------------------------------------------------------- conversations */

  function previewText(conversation) {
    var last = conversation.last_message;
    if (!last) return "No messages yet";
    return (last.sender_id === meId ? "You: " : "") + last.text;
  }

  function render() {
    listEl.textContent = "";
    var none = conversations.length === 0;
    emptyEl.hidden = !none;
    if (welcomeEmpty) welcomeEmpty.hidden = !none;

    conversations.forEach(function (conversation) {
      var peer = conversation.peer;
      var online = window.ChatApp.isOnline(peer.id);
      var link = F.el("a", "conv-item" +
        (conversation.id === activeId ? " active" : "") +
        (conversation.unread > 0 ? " has-unread" : ""));
      link.href = conversation.url;
      link.dataset.id = conversation.id;
      if (conversation.id === activeId) link.setAttribute("aria-current", "page");

      var avatarWrap = F.el("span", "conv-avatar");
      avatarWrap.appendChild(F.avatarNode(peer, "md"));
      var dot = F.el("span", "presence-dot presence-dot-corner" + (online ? " online" : ""));
      dot.title = online ? "Online" : "Offline";
      avatarWrap.appendChild(dot);
      link.appendChild(avatarWrap);

      var body = F.el("span", "conv-body");
      var top = F.el("span", "conv-top");
      top.appendChild(F.el("span", "conv-name", peer.display_name));
      if (conversation.last_message) {
        top.appendChild(F.el("span", "conv-time text-muted",
          F.shortDate(conversation.last_message.created_at)));
      }
      body.appendChild(top);

      var bottom = F.el("span", "conv-bottom");
      bottom.appendChild(F.el("span", "conv-preview text-muted", previewText(conversation)));
      if (conversation.unread > 0) {
        var badge = F.el("span", "badge rounded-pill bg-primary conv-unread",
          conversation.unread > 99 ? "99+" : String(conversation.unread));
        badge.setAttribute("aria-label", conversation.unread + " unread messages");
        bottom.appendChild(badge);
      }
      body.appendChild(bottom);
      link.appendChild(body);
      listEl.appendChild(link);
    });
  }

  function load() {
    return fetch("/api/conversations", { credentials: "same-origin" })
      .then(function (response) {
        if (response.status === 401) { window.location.href = "/login"; return null; }
        if (!response.ok) throw new Error("status " + response.status);
        return response.json();
      })
      .then(function (data) {
        if (!data) return;
        conversations = data.conversations;
        errorEl.hidden = true;
        render();
      })
      .catch(function () { errorEl.hidden = false; });
  }

  function findIndex(id) {
    for (var i = 0; i < conversations.length; i++) {
      if (conversations[i].id === id) return i;
    }
    return -1;
  }

  function textOf(message) {
    if (message.deleted) return "This message was deleted";
    return (message.body || "").replace(/\s+/g, " ").slice(0, 80);
  }

  socket.on("message_new", function (message) {
    var index = findIndex(message.conversation_id);
    if (index === -1) { load(); return; } // a conversation we haven't listed yet
    var conversation = conversations.splice(index, 1)[0];
    conversation.last_message = {
      id: message.id, sender_id: message.sender_id, type: message.type,
      text: textOf(message), created_at: message.created_at
    };
    var viewing = message.conversation_id === activeId && document.visibilityState === "visible";
    if (message.sender_id !== meId && !viewing) conversation.unread += 1;
    conversations.unshift(conversation);
    render();
  });

  // Edits and deletes of the newest message change the preview line.
  socket.on("message_updated", function (message) {
    var index = findIndex(message.conversation_id);
    if (index === -1) return;
    var last = conversations[index].last_message;
    if (!last || last.id !== message.id) return;
    last.text = textOf(message);
    render();
  });

  socket.on("conversation_read", function (data) {
    var index = findIndex(data.conversation_id);
    if (index !== -1) { conversations[index].unread = 0; render(); }
  });

  document.addEventListener("chat:presence", render);

  var firstConnect = true;
  socket.on("connect", function () {
    // After a reconnect we may have missed events; reload to catch up.
    if (firstConnect) { firstConnect = false; return; }
    load();
  });

  /* ------------------------------------------------------------ user search */

  var searchTimer = null;
  var searchSeq = 0;

  function hideResults() {
    resultsEl.hidden = true;
    resultsEl.textContent = "";
    searchStatus.hidden = true;
  }

  function showStatus(text) {
    searchStatus.textContent = text;
    searchStatus.hidden = false;
  }

  function renderResults(users) {
    resultsEl.textContent = "";
    if (!users.length) {
      resultsEl.hidden = true;
      showStatus("No one found with that name.");
      return;
    }
    searchStatus.hidden = true;
    users.forEach(function (user) {
      var link = F.el("a", "search-result");
      link.href = user.profile_url;
      link.appendChild(F.avatarNode(user, "sm"));
      var text = F.el("span", "search-result-text");
      text.appendChild(F.el("span", "fw-semibold", user.display_name));
      text.appendChild(F.el("span", "text-muted small", "@" + user.username));
      link.appendChild(text);
      resultsEl.appendChild(link);
    });
    resultsEl.hidden = false;
  }

  function search() {
    var query = searchInput.value.trim();
    if (query.replace(/^@/, "").length < 2) { hideResults(); return; }
    var seq = ++searchSeq;
    fetch("/api/users/search?q=" + encodeURIComponent(query), { credentials: "same-origin" })
      .then(function (response) {
        if (seq !== searchSeq) return null;
        if (response.status === 429) { showStatus("Too many searches. Wait a moment."); return null; }
        if (response.status === 401) { window.location.href = "/login"; return null; }
        if (!response.ok) throw new Error("status " + response.status);
        return response.json();
      })
      .then(function (data) { if (data && seq === searchSeq) renderResults(data.users); })
      .catch(function () { if (seq === searchSeq) showStatus("Search failed. Try again."); });
  }

  searchInput.addEventListener("input", function () {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(search, 250);
  });

  /* ------------------------------------------------- welcome-pane actions */

  document.addEventListener("click", function (event) {
    var focusBtn = event.target.closest("[data-action='focus-search']");
    if (focusBtn) {
      // On small screens the sidebar is the page; on the chat view go home first.
      if (activeId && window.matchMedia("(max-width: 991.98px)").matches) {
        window.location.href = "/";
        return;
      }
      searchInput.focus();
      searchInput.select();
      return;
    }
    var copyBtn = event.target.closest("[data-copy]");
    if (copyBtn) {
      F.copyText(copyBtn.dataset.copy).then(function (ok) {
        var label = copyBtn.querySelector("strong");
        if (!label) return;
        var original = label.dataset.original || label.textContent;
        label.dataset.original = original;
        label.textContent = ok ? "Copied!" : "Couldn't copy";
        setTimeout(function () { label.textContent = original; }, 1500);
      });
    }
  });

  load();
})();
