/* Emoji picker: categories, search by name, and a "frequently used" row that is
 * remembered per user in localStorage. One shared popup; call
 * ChatEmoji.open({ anchor, onPick, keepOpen }).
 * The catalog (static/data/emoji.json) is the same file the server uses to
 * validate reactions. */
(function () {
  "use strict";

  var QUICK = ["\uD83D\uDC4D", "\u2764\uFE0F", "\uD83D\uDE02", "\uD83D\uDE2E", "\uD83D\uDE22", "\uD83D\uDE4F"];
  var RECENT_LIMIT = 24;

  var catalogPromise = null;
  var picker = null;          // popup element, built on first open
  var current = null;         // { anchor, onPick, keepOpen }
  var sections = [];          // [{ id, title, node }]
  var allEmoji = [];          // [[char, name, groupId]]
  var searchInput, scroller, resultsBox, tabsBox, recentGrid, recentSection;

  function el(tag, className, text) { return window.ChatFormat.el(tag, className, text); }

  /* ----------------------------------------------------------- frequently used */

  function storageKey() {
    var shell = document.getElementById("app-shell");
    return "chat.emoji.recent." + (shell ? shell.dataset.meId : "anon");
  }

  function readRecents() {
    try {
      var data = JSON.parse(localStorage.getItem(storageKey()) || "[]");
      return Array.isArray(data) ? data.filter(function (r) { return r && typeof r.e === "string"; }) : [];
    } catch (e) { return []; }
  }

  function topRecents() {
    return readRecents().sort(function (a, b) { return (b.n - a.n) || (b.t - a.t); })
      .slice(0, RECENT_LIMIT).map(function (r) { return r.e; });
  }

  function pushRecent(emoji) {
    try {
      var list = readRecents();
      var hit = list.filter(function (r) { return r.e === emoji; })[0];
      if (hit) { hit.n += 1; hit.t = Date.now(); } else { list.push({ e: emoji, n: 1, t: Date.now() }); }
      list.sort(function (a, b) { return (b.n - a.n) || (b.t - a.t); });
      localStorage.setItem(storageKey(), JSON.stringify(list.slice(0, 60)));
    } catch (e) { /* storage unavailable: recents just won't persist */ }
  }

  /* ------------------------------------------------------------------ building */

  function load() {
    if (!catalogPromise) {
      var url = document.body.dataset.emojiUrl;
      catalogPromise = fetch(url, { credentials: "same-origin" })
        .then(function (r) { if (!r.ok) throw new Error("emoji catalog " + r.status); return r.json(); })
        .catch(function (error) { catalogPromise = null; throw error; });
    }
    return catalogPromise;
  }

  function emojiButton(emoji, name) {
    var button = el("button", "emoji-btn", emoji);
    button.type = "button";
    button.setAttribute("aria-label", name);
    button.title = name;
    button.dataset.emoji = emoji;
    return button;
  }

  function build(catalog) {
    picker = el("div", "emoji-picker");
    picker.setAttribute("role", "dialog");
    picker.setAttribute("aria-label", "Emoji picker");
    picker.hidden = true;

    var searchWrap = el("div", "emoji-search");
    searchInput = el("input", "form-control");
    searchInput.type = "search";
    searchInput.placeholder = "Search emoji";
    searchInput.setAttribute("aria-label", "Search emoji");
    searchInput.autocomplete = "off";
    searchWrap.appendChild(searchInput);
    picker.appendChild(searchWrap);

    tabsBox = el("div", "emoji-tabs");
    tabsBox.setAttribute("role", "tablist");
    picker.appendChild(tabsBox);

    scroller = el("div", "emoji-scroll");
    picker.appendChild(scroller);

    resultsBox = el("div", "emoji-results");
    resultsBox.hidden = true;
    scroller.appendChild(resultsBox);

    function addSection(id, title, tabIcon) {
      var section = el("section");
      section.id = "emoji-section-" + id;
      section.appendChild(el("h3", "emoji-section-title", title));
      var grid = el("div", "emoji-grid");
      section.appendChild(grid);
      scroller.appendChild(section);

      var tab = el("button", "emoji-tab", tabIcon);
      tab.type = "button";
      tab.setAttribute("role", "tab");
      tab.setAttribute("aria-label", title);
      tab.title = title;
      tab.addEventListener("click", function () {
        searchInput.value = "";
        showSearch("");
        scroller.scrollTop = section.offsetTop - scroller.offsetTop;
      });
      tabsBox.appendChild(tab);
      sections.push({ id: id, node: section, tab: tab });
      return grid;
    }

    recentGrid = addSection("recent", "Frequently used", "\uD83D\uDD52");
    recentSection = sections[0];

    catalog.groups.forEach(function (group) {
      var grid = addSection(group.id, group.name, group.icon);
      var fragment = document.createDocumentFragment();
      group.emojis.forEach(function (item) {
        fragment.appendChild(emojiButton(item[0], item[1]));
        allEmoji.push([item[0], item[1]]);
      });
      grid.appendChild(fragment);
    });

    picker.addEventListener("click", function (event) {
      var button = event.target.closest(".emoji-btn");
      if (!button || !current) return;
      var emoji = button.dataset.emoji;
      pushRecent(emoji);
      var handler = current.onPick;
      var keep = current.keepOpen;
      if (!keep) close();
      handler(emoji);
    });

    searchInput.addEventListener("input", function () { showSearch(searchInput.value); });
    scroller.addEventListener("scroll", highlightTab);
    document.body.appendChild(picker);
  }

  function showSearch(query) {
    query = query.trim().toLowerCase();
    var searching = query.length > 0;
    resultsBox.hidden = !searching;
    sections.forEach(function (s) { s.node.hidden = searching; });
    if (!searching) { renderRecents(); highlightTab(); return; }
    resultsBox.textContent = "";
    var words = query.split(/\s+/);
    var matches = allEmoji.filter(function (item) {
      return words.every(function (w) { return item[1].indexOf(w) !== -1; });
    }).slice(0, 120);
    if (!matches.length) {
      resultsBox.appendChild(el("div", "emoji-empty", "No emoji found"));
      return;
    }
    var grid = el("div", "emoji-grid");
    matches.forEach(function (item) { grid.appendChild(emojiButton(item[0], item[1])); });
    resultsBox.appendChild(grid);
  }

  function renderRecents() {
    var recents = topRecents();
    recentGrid.textContent = "";
    recentSection.node.hidden = recents.length === 0;
    recentSection.tab.hidden = recents.length === 0;
    var names = {};
    allEmoji.forEach(function (item) { names[item[0]] = item[1]; });
    recents.forEach(function (emoji) { recentGrid.appendChild(emojiButton(emoji, names[emoji] || emoji)); });
  }

  function highlightTab() {
    var top = scroller.scrollTop + 8;
    var active = null;
    sections.forEach(function (s) {
      if (!s.node.hidden && s.node.offsetTop - scroller.offsetTop <= top) active = s;
    });
    sections.forEach(function (s) { s.tab.classList.toggle("active", s === active); });
  }

  /* ----------------------------------------------------------- open / close */

  function position(anchor) {
    var rect = anchor.getBoundingClientRect();
    var width = picker.offsetWidth || 352;
    var height = picker.offsetHeight || 400;
    var left = Math.min(Math.max(8, rect.left), window.innerWidth - width - 8);
    var above = rect.top - height - 8;
    picker.style.left = left + "px";
    picker.style.top = (above >= 8 ? above : Math.min(rect.bottom + 8, window.innerHeight - height - 8)) + "px";
  }

  function onDocumentPointer(event) {
    if (!picker || picker.hidden) return;
    if (picker.contains(event.target)) return;
    if (current && current.anchor && current.anchor.contains(event.target)) return;
    close();
  }

  function onKey(event) {
    if (event.key === "Escape" && picker && !picker.hidden) {
      var anchor = current && current.anchor;
      close();
      if (anchor && anchor.focus && document.contains(anchor)) anchor.focus();
    }
  }

  function open(options) {
    return load().then(function (catalog) {
      if (!picker) build(catalog);
      current = options;
      searchInput.value = "";
      showSearch("");
      picker.hidden = false;
      position(options.anchor);
      scroller.scrollTop = 0;
      highlightTab();
      if (window.matchMedia && !window.matchMedia("(pointer: coarse)").matches) searchInput.focus();
      return true;
    });
  }

  function close() {
    if (picker) picker.hidden = true;
    current = null;
  }

  function isOpen() { return !!picker && !picker.hidden; }

  document.addEventListener("mousedown", onDocumentPointer);
  document.addEventListener("touchstart", onDocumentPointer, { passive: true });
  document.addEventListener("keydown", onKey);
  window.addEventListener("resize", function () { if (isOpen() && current) position(current.anchor); });

  window.ChatEmoji = { QUICK: QUICK, open: open, close: close, isOpen: isOpen, load: load };
})();
