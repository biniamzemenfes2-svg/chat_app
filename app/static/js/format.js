/* Small formatting and DOM helpers shared by the page scripts. */
(function () {
  "use strict";

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text; // never innerHTML
    return node;
  }

  function avatarNode(user, size) {
    if (user.avatar_url) {
      var img = el("img", "avatar avatar-" + size);
      img.src = user.avatar_url;
      img.alt = "";
      return img;
    }
    var initial = el("span", "avatar avatar-" + size + " avatar-initial",
      (user.display_name || user.username || "?").charAt(0).toUpperCase());
    initial.setAttribute("aria-hidden", "true");
    return initial;
  }

  function clock(iso) {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  function shortDate(iso) {
    var date = new Date(iso);
    var now = new Date();
    if (date.toDateString() === now.toDateString()) return clock(iso);
    var yesterday = new Date(now.getTime() - 86400000);
    if (date.toDateString() === yesterday.toDateString()) return "Yesterday";
    return date.toLocaleDateString([], { day: "numeric", month: "short" });
  }

  function lastSeenText(iso) {
    if (!iso) return "Offline";
    var date = new Date(iso);
    var now = new Date();
    if (date.toDateString() === now.toDateString()) return "Last seen today at " + clock(iso);
    return "Last seen " + date.toLocaleDateString([], { day: "numeric", month: "short" }) +
      " at " + clock(iso);
  }

  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    var bytes = new Uint8Array(16);
    (window.crypto || window.msCrypto).getRandomValues(bytes);
    return Array.prototype.map.call(bytes, function (b) {
      return ("0" + b.toString(16)).slice(-2);
    }).join("");
  }

  window.ChatFormat = {
    el: el, avatarNode: avatarNode, clock: clock, shortDate: shortDate,
    lastSeenText: lastSeenText, uuid: uuid
  };
})();
