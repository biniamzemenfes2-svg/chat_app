const { JSDOM, CookieJar } = require("jsdom");
const BASE = process.env.BASE || "http://127.0.0.1:5058";
const S = JSON.parse(process.argv[2]);
const CONV = `/chats/${S.conversation_id}`;
const pages = [];
let failures = 0;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function check(label, ok, extra) {
  console.log((ok ? "PASS " : "FAIL ") + label + (extra ? "  " + extra : ""));
  if (!ok) failures++;
}
async function waitFor(fn, label, timeout = 6000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    try { const v = fn(); if (v) return v; } catch (e) {}
    await sleep(50);
  }
  check("WAIT TIMEOUT: " + label, false);
  return null;
}

function makeFetch(jar) {
  return (url, opts = {}) => {
    const abs = new URL(url, BASE).href;
    const headers = Object.assign({}, opts.headers || {}, { cookie: jar.getCookieStringSync(abs) });
    return fetch(abs, Object.assign({}, opts, { headers, redirect: "manual" }));
  };
}

async function open(path, user) {
  const jar = new CookieJar();
  jar.setCookieSync(`session=${user.cookie}; Path=/`, BASE);
  const dom = await JSDOM.fromURL(BASE + path, {
    cookieJar: jar, runScripts: "dangerously", resources: "usable", pretendToBeVisual: true,
    beforeParse(window) {
      window.fetch = makeFetch(jar);
      window.__errors = [];
      window.addEventListener("error", (e) => window.__errors.push(String(e.message)));
    },
  });
  pages.push(dom);
  const w = dom.window;
  await waitFor(() => w.ChatApp && w.ChatApp.socket.connected && w.ChatApp.presenceReady, "socket connected: " + path);
  return dom;
}
const $ = (dom, sel) => dom.window.document.querySelector(sel);
const $$ = (dom, sel) => Array.from(dom.window.document.querySelectorAll(sel));
const texts = (dom, sel) => $$(dom, sel).map((n) => n.textContent);

function typeAndSend(dom, text) {
  const w = dom.window, input = $(dom, "#composer-input");
  input.value = text;
  input.dispatchEvent(new w.Event("input", { bubbles: true }));
  input.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
}

(async () => {
  // ---------------------------------------------------------------- chat pages
  const alice = await open(CONV, S.alice);
  check("header shows peer name", $(alice, ".chat-peer-name").textContent === "Bob Builder");
  check("peer starts offline (nobody else connected)", /Offline|Last seen/.test($(alice, "#presence-text").textContent), $(alice, "#presence-text").textContent);
  check("empty history renders no messages", $$(alice, ".msg").length === 0);

  const bob = await open(CONV, S.bob);
  await waitFor(() => $(alice, "#presence-text").textContent === "Online", "alice sees bob online");
  check("presence: alice sees Bob Online live", $(alice, "#presence-text").textContent === "Online" && $(alice, "#presence-dot").classList.contains("online"));
  check("presence: bob sees Alice Online", $(bob, "#presence-text").textContent === "Online");

  // send + receive + receipts
  typeAndSend(alice, "Hello Bob");
  check("optimistic bubble appears immediately for sender", texts(alice, ".msg-out .msg-body").includes("Hello Bob"));
  await waitFor(() => texts(bob, ".msg-in .msg-body").includes("Hello Bob"), "bob receives message");
  check("recipient sees the message live", texts(bob, ".msg-in .msg-body").includes("Hello Bob"));
  check("composer cleared after send", $(alice, "#composer-input").value === "");
  await waitFor(() => $(alice, ".msg-out .msg-status").classList.contains("msg-status-read"), "read receipt", 8000);
  check("sender sees Delivered->Read receipt", $(alice, ".msg-out .msg-status").classList.contains("msg-status-read"), $(alice, ".msg-out .msg-status").title);
  check("exactly one bubble on each side (ack + broadcast deduped)", $$(alice, ".msg").length === 1 && $$(bob, ".msg").length === 1);

  // typing indicator
  const aInput = $(alice, "#composer-input");
  aInput.value = "typ";
  aInput.dispatchEvent(new alice.window.Event("input", { bubbles: true }));
  await waitFor(() => !$(bob, "#typing-indicator").hidden, "typing shows");
  check("peer sees typing indicator", !$(bob, "#typing-indicator").hidden);
  check("sender does not see own typing indicator", $(alice, "#typing-indicator").hidden);
  aInput.value = "";
  aInput.dispatchEvent(new alice.window.Event("input", { bubbles: true }));
  await waitFor(() => $(bob, "#typing-indicator").hidden, "typing hides");
  check("typing indicator clears when text is deleted", $(bob, "#typing-indicator").hidden);

  // XSS
  const evil = '<img src=x onerror="window.__pwned=1"><script>window.__pwned=2</script> & "q"';
  typeAndSend(alice, evil);
  await waitFor(() => texts(bob, ".msg-in .msg-body").includes(evil), "bob receives evil payload");
  check("XSS payload shown as literal text", texts(bob, ".msg-in .msg-body").includes(evil));
  check("no <img>/<script> elements injected into the DOM", $$(bob, ".msg-body img, .msg-body script").length === 0 && $$(alice, ".msg-body img, .msg-body script").length === 0);
  await sleep(300);
  check("payload did not execute", bob.window.__pwned === undefined && alice.window.__pwned === undefined);

  // reply, newline handling
  typeAndSend(bob, "Hi Alice\nsecond line");
  await waitFor(() => texts(alice, ".msg-in .msg-body").includes("Hi Alice\nsecond line"), "alice receives reply");
  check("reply arrives with newline intact", texts(alice, ".msg-in .msg-body").includes("Hi Alice\nsecond line"));
  check("Shift+Enter is not a send (newline allowed)", (() => {
    const w = alice.window, i = $(alice, "#composer-input"); i.value = "draft";
    const ev = new w.KeyboardEvent("keydown", { key: "Enter", shiftKey: true, bubbles: true, cancelable: true });
    i.dispatchEvent(ev); const kept = i.value === "draft" && !ev.defaultPrevented; i.value = ""; return kept;
  })());
  check("empty/whitespace message is not sent", (() => {
    const before = $$(alice, ".msg").length; typeAndSend(alice, "   \n  "); return $$(alice, ".msg").length === before;
  })());

  // too long message: server rejects, text restored, error shown
  const tooLong = "x".repeat(4001);
  const aInput2 = $(alice, "#composer-input"); aInput2.removeAttribute("maxlength");
  const before = $$(alice, ".msg").length;
  typeAndSend(alice, tooLong);
  await waitFor(() => !$(alice, "#send-error").hidden, "send error shown");
  check("rejected message: error shown, bubble removed, text restored",
    !$(alice, "#send-error").hidden && /too long/i.test($(alice, "#send-error").textContent) && $$(alice, ".msg").length === before && aInput2.value === tooLong,
    $(alice, "#send-error").textContent);
  aInput2.value = ""; $(alice, "#send-error").hidden = true;

  // ------------------------------------------------------ dashboard, live
  bob.window.close();
  await sleep(300);
  const dash = await open("/", S.bob);
  await waitFor(() => $$(dash, ".conv-item").length === 1, "conversation listed");
  check("dashboard lists the conversation with peer + preview", $(dash, ".conv-name").textContent === "Alice Wonder" && /Hi Alice/.test($(dash, ".conv-preview").textContent), $(dash, ".conv-preview").textContent);
  check("no unread badge when everything was read", $$(dash, ".conv-unread").length === 0);
  check("dashboard shows Alice online dot", $(dash, ".presence-dot").classList.contains("online"));

  typeAndSend(alice, "ping dashboard");
  await waitFor(() => $$(dash, ".conv-unread").length === 1, "unread badge appears live");
  check("unread badge increments live without reload", $(dash, ".conv-unread") && $(dash, ".conv-unread").textContent === "1");
  check("preview updates live", /ping dashboard/.test($(dash, ".conv-preview").textContent));

  const bob2 = await open(CONV, S.bob); // opening the chat reads it
  await waitFor(() => $$(dash, ".conv-unread").length === 0, "badge cleared via other tab");
  check("opening the chat in another tab clears the dashboard badge", $$(dash, ".conv-unread").length === 0);
  check("history loaded in order on fresh page", (() => {
    const t = texts(bob2, ".msg-body"); return t[0] === "Hello Bob" && t[t.length - 1] === "ping dashboard";
  })(), JSON.stringify(texts(bob2, ".msg-body")));

  // ------------------------------------------------------ dashboard search
  const input = $(dash, "#user-search");
  input.value = S.alice.username.slice(0, 6);
  input.dispatchEvent(new dash.window.Event("input", { bubbles: true }));
  await waitFor(() => $$(dash, ".search-result").length === 1, "search result");
  const link = $(dash, ".search-result");
  check("search finds the user and links to their profile", link && link.getAttribute("href") === `/u/${S.alice.username}` && /Alice Wonder/.test(link.textContent));
  input.value = "%%";
  input.dispatchEvent(new dash.window.Event("input", { bubbles: true }));
  await sleep(700);
  check("wildcard-only query shows nothing", $$(dash, ".search-result").length === 0);
  input.value = "zzzznoone";
  input.dispatchEvent(new dash.window.Event("input", { bubbles: true }));
  await waitFor(() => !$(dash, "#search-status").hidden, "no-results text");
  check("no-results message shown", /No one found/.test($(dash, "#search-status").textContent));

  // -------------------------------------- offline delivery & presence "gone"
  dash.window.close(); bob2.window.close();
  await waitFor(() => /Last seen|Offline/.test($(alice, "#presence-text").textContent), "alice sees bob leave");
  check("presence: peer shown offline with last-seen after leaving", /Last seen/.test($(alice, "#presence-text").textContent), $(alice, "#presence-text").textContent);
  typeAndSend(alice, "sent while you are away");
  await waitFor(() => texts(alice, ".msg-out .msg-body").includes("sent while you are away"), "optimistic");
  const last = () => $$(alice, ".msg-out").pop();
  await waitFor(() => last().dataset.id, "server confirmed");
  check("message to an offline user stays 'Sent' (not delivered)", last().querySelector(".msg-status").classList.contains("msg-status-sent"), last().querySelector(".msg-status").title);
  const bob3 = await open("/", S.bob);
  await waitFor(() => last().querySelector(".msg-status").classList.contains("msg-status-delivered"), "delivered on connect");
  check("becomes 'Delivered' as soon as the recipient connects", last().querySelector(".msg-status").classList.contains("msg-status-delivered"));
  const bob4 = await open(CONV, S.bob);
  await waitFor(() => last().querySelector(".msg-status").classList.contains("msg-status-read"), "read on open");
  check("becomes 'Read' once the recipient opens the chat", last().querySelector(".msg-status").classList.contains("msg-status-read"));

  // ---- lost connection: buffered send + retry with same client_id => one message
  const socket = alice.window.ChatApp.socket;
  socket.disconnect();
  await waitFor(() => !$(alice, "#connection-banner").hidden, "banner shown");
  check("connection banner shows while disconnected", !$(alice, "#connection-banner").hidden);
  typeAndSend(alice, "sent in a tunnel");
  const tunnelNode = () => $$(alice, ".msg-out").pop();
  check("message shows as pending while offline", tunnelNode().classList.contains("msg-pending"));
  await waitFor(() => tunnelNode().classList.contains("msg-failed"), "marked failed after timeout", 11000);
  check("marked failed (tap to retry) when no acknowledgement arrives", tunnelNode().classList.contains("msg-failed"));
  tunnelNode().querySelector(".msg-status").dispatchEvent(new alice.window.Event("click", { bubbles: true })); // retry (same client_id)
  socket.connect();
  await waitFor(() => tunnelNode().dataset.id, "delivered after reconnect", 10000);
  await sleep(500);
  const copies = texts(alice, ".msg-out .msg-body").filter((t) => t === "sent in a tunnel").length;
  check("after reconnect the message is confirmed exactly once in the UI", copies === 1, "copies=" + copies);
  check("banner hides again after reconnect", $(alice, "#connection-banner").hidden);
  const hist = await (await makeFetch((() => { const j = new CookieJar(); j.setCookieSync(`session=${S.alice.cookie}; Path=/`, BASE); return j; })())(`/api/conversations/${S.conversation_id}/messages?limit=100`)).json();
  const serverCopies = hist.messages.filter((m) => m.body === "sent in a tunnel").length;
  check("server stored it exactly once (idempotent retry)", serverCopies === 1, "server copies=" + serverCopies);
  await waitFor(() => texts(bob4, ".msg-in .msg-body").includes("sent in a tunnel"), "bob got it");
  check("recipient got it exactly once", texts(bob4, ".msg-in .msg-body").filter((t) => t === "sent in a tunnel").length === 1);

  // ---------------------------------------------------------------- hygiene
  const errs = pages.flatMap((p, i) => (p.window.__errors || []).map((e) => `page${i}: ${e}`));
  check("no uncaught JavaScript errors on any page", errs.length === 0, errs.slice(0, 3).join(" | "));
  pages.forEach((p) => { try { p.window.close(); } catch (e) {} });
  console.log(failures === 0 ? "\nALL BROWSER-LEVEL CHECKS PASSED" : `\n${failures} CHECK(S) FAILED`);
  process.exit(failures === 0 ? 0 : 1);
})().catch((e) => { console.error("HARNESS ERROR", e); process.exit(2); });
