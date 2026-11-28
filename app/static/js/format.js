/* Small formatting and DOM helpers shared by the page scripts. */
(function () {
  "use strict";

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text; // never innerHTML
    return node;
  }

  function icon(name, extraClass) {
    var node = el("i", "bi bi-" + name + (extraClass ? " " + extraClass : ""));
    node.setAttribute("aria-hidden", "true");
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

  function sameDay(a, b) { return a.toDateString() === b.toDateString(); }

  function shortDate(iso) {
    var date = new Date(iso);
    var now = new Date();
    if (sameDay(date, now)) return clock(iso);
    if (sameDay(date, new Date(now.getTime() - 86400000))) return "Yesterday";
    return date.toLocaleDateString([], { day: "numeric", month: "short" });
  }

  function dayLabel(iso) {
    var date = new Date(iso);
    var now = new Date();
    if (sameDay(date, now)) return "Today";
    if (sameDay(date, new Date(now.getTime() - 86400000))) return "Yesterday";
    var sameYear = date.getFullYear() === now.getFullYear();
    return date.toLocaleDateString([], sameYear
      ? { weekday: "short", day: "numeric", month: "long" }
      : { day: "numeric", month: "long", year: "numeric" });
  }

  function lastSeenText(iso) {
    if (!iso) return "Offline";
    var date = new Date(iso);
    if (sameDay(date, new Date())) return "Last seen today at " + clock(iso);
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

  // Resolves true if the text reached the clipboard.
  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text).then(function () { return true; },
        function () { return false; });
    }
    try {
      var area = el("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.className = "visually-hidden";
      document.body.appendChild(area);
      area.select();
      var ok = document.execCommand && document.execCommand("copy");
      area.remove();
      return Promise.resolve(!!ok);
    } catch (e) {
      return Promise.resolve(false);
    }
  }

  window.ChatFormat = {
    el: el, icon: icon, avatarNode: avatarNode, clock: clock, shortDate: shortDate,
    dayLabel: dayLabel, lastSeenText: lastSeenText, uuid: uuid, copyText: copyText
  };
})();
