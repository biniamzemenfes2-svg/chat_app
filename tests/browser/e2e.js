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
      window.__copied = null;
      Object.defineProperty(window.navigator, "clipboard", {
        value: { writeText: (text) => { window.__copied = text; return Promise.resolve(); } },
      });
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
  input.value = S.alice.username.slice(0, -1); // unique prefix
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


  // ======================================================================
  // Phase 4: emoji, reactions, reply, edit, copy, delete, search, windows
  // ======================================================================
  const doc = (dom) => dom.window.document;
  const msgNode = (dom, text) => $$(dom, ".msg").find((n) => n.querySelector(".msg-body") && n.querySelector(".msg-body").textContent === text);
  const click = (dom, node) => node.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true, cancelable: true }));
  async function openMenu(dom, node) {
    click(dom, node.querySelector(".msg-menu-btn"));
    return waitFor(() => doc(dom).querySelector(".msg-menu"), "action menu opens");
  }
  const menuLabels = (dom) => $$(dom, ".msg-menu-item").map((i) => i.textContent.trim());
  function menuItem(dom, label) { return $$(dom, ".msg-menu-item").find((i) => i.textContent.trim() === label); }
  const chips = (dom, text) => { const n = msgNode(dom, text); return n ? Array.from(n.querySelectorAll(".reaction-chip")).map((c) => c.textContent.replace(/\s+/g, "")) : null; };

  // ---- emoji picker in the composer
  click(alice, $(alice, "#emoji-button"));
  await waitFor(() => { const p = doc(alice).querySelector(".emoji-picker"); return p && !p.hidden; }, "picker opens");
  const picker = () => doc(alice).querySelector(".emoji-picker");
  check("emoji picker opens with a category tab per group + search", picker().querySelectorAll(".emoji-tab").length >= 9 && !!picker().querySelector("input[type=search]"));
  check("picker offers the full catalog (>1500 emoji)", picker().querySelectorAll(".emoji-btn").length > 1500, String(picker().querySelectorAll(".emoji-btn").length));
  const search = picker().querySelector("input[type=search]");
  search.value = "grinning face"; search.dispatchEvent(new alice.window.Event("input", { bubbles: true }));
  const grin = await waitFor(() => picker().querySelector(".emoji-results .emoji-btn[data-emoji='\u{1F600}']"), "search result");
  check("emoji search by name finds the emoji", !!grin);
  search.value = "zzzzqqq"; search.dispatchEvent(new alice.window.Event("input", { bubbles: true }));
  check("emoji search with no match says so", /No emoji found/.test(picker().querySelector(".emoji-results").textContent));
  search.value = "grinning face"; search.dispatchEvent(new alice.window.Event("input", { bubbles: true }));
  click(alice, picker().querySelector(".emoji-results .emoji-btn[data-emoji='\u{1F600}']"));
  check("picking an emoji inserts it into the composer and keeps the picker open", $(alice, "#composer-input").value.includes("\u{1F600}") && !picker().hidden);
  alice.window.document.dispatchEvent(new alice.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  check("Escape closes the picker", picker().hidden);
  click(alice, $(alice, "#emoji-button"));
  await waitFor(() => !picker().hidden, "picker reopens");
  const firstRecent = picker().querySelector("#emoji-section-recent .emoji-btn");
  check("picked emoji appears under 'Frequently used'", firstRecent && firstRecent.dataset.emoji === "\u{1F600}" && !picker().querySelector("#emoji-section-recent").hidden);
  click(alice, $(alice, "#emoji-button")); // toggle closed
  check("the emoji button toggles the picker", picker().hidden);
  $(alice, "#composer-input").value = "I am happy \u{1F600}";
  typeAndSend(alice, "I am happy \u{1F600}");
  await waitFor(() => msgNode(bob4, "I am happy \u{1F600}"), "emoji message arrives");
  check("emoji text arrives intact", !!msgNode(bob4, "I am happy \u{1F600}"));

  // ---- authorization in the action menu
  let target = msgNode(bob4, "Hello Bob");
  await openMenu(bob4, target);
  check("recipient's menu has Reply/Copy but no Edit/Delete for someone else's message", menuLabels(bob4).join() === "Reply,Copy text", menuLabels(bob4).join());
  check("quick reactions are offered", $$(bob4, ".msg-menu-react button").length === 7);

  // ---- reactions: add, count, replace (one per user), remove, full picker
  click(bob4, doc(bob4).querySelector(".msg-menu-react button[aria-label='React with \u{1F44D}']"));
  await waitFor(() => (chips(alice, "Hello Bob") || []).length === 1, "alice sees reaction");
  check("reaction shows live on both sides", chips(alice, "Hello Bob").join() === "\u{1F44D}" && chips(bob4, "Hello Bob").join() === "\u{1F44D}");
  check("own reaction is highlighted, other's is not", msgNode(bob4, "Hello Bob").querySelector(".reaction-chip").classList.contains("mine") && !msgNode(alice, "Hello Bob").querySelector(".reaction-chip").classList.contains("mine"));
  await openMenu(alice, msgNode(alice, "Hello Bob"));
  click(alice, doc(alice).querySelector(".msg-menu-react button[aria-label='React with \u{1F44D}']"));
  await waitFor(() => chips(bob4, "Hello Bob").join() === "\u{1F44D}2", "count 2");
  check("same emoji from both users is grouped with a count", chips(alice, "Hello Bob").join() === "\u{1F44D}2", chips(alice, "Hello Bob").join());
  await openMenu(bob4, msgNode(bob4, "Hello Bob"));
  click(bob4, doc(bob4).querySelector(".msg-menu-react button[aria-label='React with \u2764\uFE0F']"));
  await waitFor(() => chips(alice, "Hello Bob").length === 2, "replaced reaction");
  check("choosing another emoji replaces your previous reaction", chips(alice, "Hello Bob").join() === "\u{1F44D},\u2764\uFE0F", chips(alice, "Hello Bob").join());
  click(bob4, Array.from(msgNode(bob4, "Hello Bob").querySelectorAll(".reaction-chip")).find((c) => c.textContent.includes("\u2764")));
  await waitFor(() => chips(alice, "Hello Bob").length === 1, "toggled off");
  check("clicking your own reaction chip removes it", chips(alice, "Hello Bob").join() === "\u{1F44D}");
  await openMenu(bob4, msgNode(bob4, "Hello Bob"));
  click(bob4, $$(bob4, ".msg-menu-react button").pop()); // the "+" button
  await waitFor(() => { const p = doc(bob4).querySelector(".emoji-picker"); return p && !p.hidden; }, "full picker for reactions");
  const bobPicker = doc(bob4).querySelector(".emoji-picker");
  const bs = bobPicker.querySelector("input[type=search]"); bs.value = "party popper"; bs.dispatchEvent(new bob4.window.Event("input", { bubbles: true }));
  click(bob4, bobPicker.querySelector(".emoji-results .emoji-btn"));
  await waitFor(() => chips(alice, "Hello Bob").some((c) => c.includes("\u{1F389}")), "custom reaction");
  check("any catalog emoji works as a reaction via the full picker (and it closes)", chips(alice, "Hello Bob").some((c) => c.includes("\u{1F389}")) && bobPicker.hidden);

  // ---- reply + quote + jump
  await openMenu(bob4, msgNode(bob4, "Hello Bob"));
  click(bob4, menuItem(bob4, "Reply"));
  check("reply bar shows who and what", !$(bob4, "#context-bar").hidden && /Replying to Alice/.test($(bob4, "#context-title").textContent) && /Hello Bob/.test($(bob4, "#context-preview").textContent));
  typeAndSend(bob4, "Replying to you!");
  await waitFor(() => msgNode(alice, "Replying to you!"), "reply arrives");
  const quote = msgNode(alice, "Replying to you!").querySelector(".msg-quote");
  check("reply arrives with a quote of the original", quote && /Hello Bob/.test(quote.textContent) && /You/.test(quote.querySelector(".msg-quote-name").textContent));
  check("reply bar closes after sending", $(bob4, "#context-bar").hidden);
  click(alice, quote);
  await sleep(100);
  check("clicking a quote jumps to and highlights the original", msgNode(alice, "Hello Bob").classList.contains("flash"));
  await openMenu(bob4, msgNode(bob4, "Hello Bob")); click(bob4, menuItem(bob4, "Reply"));
  bob4.window.document.getElementById("composer-input").dispatchEvent(new bob4.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  check("Escape cancels a reply", $(bob4, "#context-bar").hidden);

  // ---- copy
  await openMenu(alice, msgNode(alice, "Hello Bob")); click(alice, menuItem(alice, "Copy text"));
  await sleep(100);
  check("Copy puts the message text on the clipboard and confirms", alice.window.__copied === "Hello Bob" && /Copied/.test($(alice, "#chat-toast").textContent));

  // ---- edit
  await openMenu(alice, msgNode(alice, "Hello Bob"));
  check("own message offers Edit and Delete", menuLabels(alice).join() === "Reply,Copy text,Edit,Delete", menuLabels(alice).join());
  click(alice, menuItem(alice, "Edit"));
  check("edit mode loads the text into the composer", $(alice, "#composer-input").value === "Hello Bob" && /Editing/.test($(alice, "#context-title").textContent));
  typeAndSend(alice, "Hello Bob (edited)");
  await waitFor(() => msgNode(bob4, "Hello Bob (edited)"), "edit arrives");
  check("edit is applied live for the other participant with an 'edited' label", !!msgNode(bob4, "Hello Bob (edited)").querySelector(".msg-edited"));
  check("edit mode closes and composer clears", $(alice, "#context-bar").hidden && $(alice, "#composer-input").value === "");
  check("edit keeps reactions and does not duplicate the bubble", chips(alice, "Hello Bob (edited)").length >= 1 && $$(alice, ".msg").filter((n) => /Hello Bob/.test(n.textContent) && n.querySelector(".msg-body").textContent.startsWith("Hello Bob")).length === 1);
  check("quotes of an edited message show the new text", /Hello Bob \(edited\)/.test(msgNode(alice, "Replying to you!").querySelector(".msg-quote-text").textContent));
  await openMenu(alice, msgNode(alice, "Hello Bob (edited)")); click(alice, menuItem(alice, "Edit"));
  $(alice, "#context-cancel").dispatchEvent(new alice.window.MouseEvent("click", { bubbles: true }));
  check("cancelling an edit clears the composer", $(alice, "#context-bar").hidden && $(alice, "#composer-input").value === "");

  // ---- delete (with confirmation)
  await openMenu(alice, msgNode(alice, "Hello Bob (edited)")); click(alice, menuItem(alice, "Delete"));
  await sleep(150);
  check("delete asks for confirmation first", !!msgNode(alice, "Hello Bob (edited)"));
  click(alice, $(alice, "#delete-confirm"));
  await waitFor(() => !msgNode(bob4, "Hello Bob (edited)"), "deleted for bob");
  const deletedFor = (dom) => $$(dom, ".msg-deleted");
  check("deleted for everyone: placeholder on both sides", deletedFor(alice).length === 1 && deletedFor(bob4).length === 1 && /was deleted/.test(deletedFor(bob4)[0].textContent));
  check("deleted text is gone from the page, including quotes of it", !/Hello Bob/.test(doc(bob4).querySelector("#message-list").textContent) && !!msgNode(alice, "Replying to you!").querySelector(".msg-quote-deleted"));
  check("deleted message has no reactions or action menu", !deletedFor(bob4)[0].querySelector(".reaction-chip, .msg-menu-btn") && !deletedFor(alice)[0].querySelector(".msg-menu-btn"));
  const histAfter = await (await makeFetch((() => { const j = new CookieJar(); j.setCookieSync(`session=${S.bob.cookie}; Path=/`, BASE); return j; })())(`/api/conversations/${S.conversation_id}/messages?limit=100`)).json();
  check("server no longer returns the deleted text", !JSON.stringify(histAfter).includes("Hello Bob") && histAfter.messages.some((m) => m.deleted && m.body === null));

  // ---- in-conversation search + long history (alice <-> carol, 130 seeded messages)
  const long = await open(`/chats/${S.conversation2_id}`, S.alice);
  await waitFor(() => $$(long, ".msg").length === 50, "latest page loaded");
  check("long conversation loads the newest 50 messages in order", texts(long, ".msg-body")[0] === "history 081" && texts(long, ".msg-body")[49] === "history 130", texts(long, ".msg-body")[0]);
  check("'Load earlier' is offered, 'Load newer' is not", !$(long, "#load-earlier").hidden && $(long, "#load-newer").hidden);
  check("date separators are shown", $$(long, ".date-sep").length >= 1 && /Today|Yesterday/.test($(long, ".date-sep").textContent), texts(long, ".date-sep").join("|"));
  click(long, $(long, "#load-earlier"));
  await waitFor(() => $$(long, ".msg").length === 100, "older page");
  check("'Load earlier' prepends older messages in order", texts(long, ".msg-body")[0] === "history 031" && texts(long, ".msg-body")[99] === "history 130");
  check("older days get their own separator", $$(long, ".date-sep").length >= 2, texts(long, ".date-sep").join("|"));

  click(long, $(long, "#chat-search-toggle"));
  check("search bar opens", !$(long, "#chat-search").hidden);
  const si = $(long, "#chat-search-input"); si.value = "history 003"; si.dispatchEvent(new long.window.Event("input", { bubbles: true }));
  await waitFor(() => $$(long, ".search-hit").length === 1, "search hit");
  check("search finds the message and highlights the match", $$(long, ".search-hit mark").length === 1 && $(long, ".search-hit mark").textContent.toLowerCase() === "history 003");
  si.value = "history 12"; si.dispatchEvent(new long.window.Event("input", { bubbles: true }));
  await waitFor(() => $$(long, ".search-hit").length === 10, "multiple hits");
  check("results are newest first", $(long, ".search-hit .search-hit-text").textContent.includes("history 129"), $(long, ".search-hit .search-hit-text").textContent);
  si.value = "nothing matches this"; si.dispatchEvent(new long.window.Event("input", { bubbles: true }));
  await waitFor(() => /No messages found/.test($(long, "#chat-search-results").textContent), "no hits");
  check("search with no matches says so", /No messages found/.test($(long, "#chat-search-results").textContent));
  si.value = "history 003"; si.dispatchEvent(new long.window.Event("input", { bubbles: true }));
  await waitFor(() => $$(long, ".search-hit").length === 1, "hit again");
  click(long, $(long, ".search-hit"));
  await waitFor(() => msgNode(long, "history 003") && $$(long, ".msg").length <= 60, "jumped to old message");
  check("jumping to a message outside the loaded range opens a window around it", !!msgNode(long, "history 003") && msgNode(long, "history 003").classList.contains("flash") && !msgNode(long, "history 130"));
  check("windowed view offers 'Load newer' and 'Latest messages'", !$(long, "#load-newer").hidden && !$(long, "#jump-latest").hidden);

  // live message while reading old history is not spliced in; it is counted
  const carol = await open(`/chats/${S.conversation2_id}`, S.carol);
  typeAndSend(carol, "fresh while you read");
  await waitFor(() => /new message/.test($(long, "#jump-label").textContent), "unseen counter");
  check("new message while reading history is counted, not inserted", /1 new message/.test($(long, "#jump-label").textContent) && !msgNode(long, "fresh while you read"));
  click(long, $(long, "#load-newer"));
  await waitFor(() => msgNode(long, "history 060"), "newer page");
  check("'Load newer' appends the following messages", !!msgNode(long, "history 060") && !msgNode(long, "history 100"));
  click(long, $(long, "#jump-latest"));
  await waitFor(() => msgNode(long, "fresh while you read"), "latest reloaded");
  check("'Latest messages' returns to the newest and includes the live message", !!msgNode(long, "fresh while you read") && $(long, "#jump-latest").hidden && $(long, "#load-newer").hidden && !!msgNode(long, "history 130"));
  // sending from a windowed view first returns to the latest
  si.value = "history 003"; si.dispatchEvent(new long.window.Event("input", { bubbles: true }));
  await waitFor(() => $$(long, ".search-hit").length === 1, "hit 3");
  click(long, $(long, ".search-hit"));
  await waitFor(() => msgNode(long, "history 003") && !$(long, "#load-newer").hidden, "windowed again");
  typeAndSend(long, "sent from the past");
  await waitFor(() => msgNode(long, "sent from the past") && msgNode(long, "history 130") && $(long, "#load-newer").hidden, "returned to latest and sent", 8000);
  check("sending from old history jumps to the latest first, then sends", !!msgNode(long, "sent from the past") && !!msgNode(long, "history 130") && $$(long, ".msg").pop().querySelector(".msg-body").textContent === "sent from the past");
  await waitFor(() => msgNode(carol, "sent from the past"), "carol receives");
  check("the message from the windowed view reaches the other person", !!msgNode(carol, "sent from the past"));

  // ---------------------------------------------------------------- hygiene
  const errs = pages.flatMap((p, i) => (p.window.__errors || []).map((e) => `page${i}: ${e}`));
  check("no uncaught JavaScript errors on any page", errs.length === 0, errs.slice(0, 3).join(" | "));
  pages.forEach((p) => { try { p.window.close(); } catch (e) {} });
  console.log(failures === 0 ? "\nALL BROWSER-LEVEL CHECKS PASSED" : `\n${failures} CHECK(S) FAILED`);
  process.exit(failures === 0 ? 0 : 1);
})().catch((e) => { console.error("HARNESS ERROR", e); process.exit(2); });
