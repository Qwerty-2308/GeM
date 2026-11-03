// The app's one screen: a chat. The model answers in words; every figure he sees comes from
// the cards, which are drawn from the pipeline's own data (verdict, reasons, page + quote).
import { el, notice, verdictBadge, evidenceBlock, formatDateTime, clip } from "../dom.js";
import { contactCard } from "./safety.js";

const STORE = "gem-chat";

// The conversation lives in this browser only. Storage can be unavailable; the chat still works.
function load() {
  try { return JSON.parse(localStorage.getItem(STORE)) || { messages: [], shown: [] }; }
  catch { return { messages: [], shown: [] }; }
}
function save(state) {
  try { localStorage.setItem(STORE, JSON.stringify(state)); } catch { /* not kept; fine */ }
}

const PROFILE_LABELS = {
  products: "Products", ignore_words: "Ignore items that mention", annual_turnover_rupees: "Annual turnover (₹)",
  years_experience: "Years of experience", is_mse: "MSE", is_startup: "Startup", is_oem: "OEM",
  business_name: "Business name", bid_order: "Download first",
};
const shownValue = (v) => (Array.isArray(v) ? v.join(", ") : typeof v === "boolean" ? (v ? "Yes" : "No")
  : typeof v === "number" ? v.toLocaleString("en-IN") : String(v));

function bidListCard(card) {
  return el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, ...["Bid no.", "Items", "Buyer", "Closes", "Verdict"].map((h) => el("th", {}, h)))),
    el("tbody", {}, card.bids.map((b) => el("tr", { class: "clickable", onclick: () => { location.hash = `#/bids/${b.id}`; } },
      el("td", {}, el("strong", {}, b.bid_number || "—")),
      el("td", { title: b.items }, clip(b.items, 90) || "—"),
      el("td", {}, b.buyer || "—"),
      el("td", {}, formatDateTime(b.closes_at)),
      el("td", {}, verdictBadge(b.verdict)))))));
}

function bidCard(card) {
  const reasons = card.reasons.filter((r) => r.outcome !== "ELIGIBLE");
  const found = card.fields.filter((f) => f.status === "FOUND");
  return el("div", { class: "card" },
    el("div", { class: "row" }, el("strong", {}, card.bid_number || "Bid"), verdictBadge(card.verdict),
      el("a", { href: `#/bids/${card.id}` }, "Full evidence")),
    reasons.length ? el("ul", { class: "reasons" }, reasons.map((r) => el("li", {},
      verdictBadge(r.outcome), " ", r.reason,
      r.page ? evidenceBlock({ page: r.page, quote: r.quote_from_bid_document }) : null))) : null,
    found.length ? el("details", {}, el("summary", {}, `Values read from the document (${found.length})`),
      el("ul", { class: "reasons" }, found.map((f) => el("li", {}, el("strong", {}, f.label), ": ", f.value,
        el("span", { class: "page-ref" }, ` · page ${f.page}`))))) : null);
}

function listingsCard(card) {
  return el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, ...["Bid no.", "Items", "Your items", "Closes", "Checked"].map((h) => el("th", {}, h)))),
    el("tbody", {}, card.listings.map((l) => el("tr", l.bid_id ? { class: "clickable", onclick: () => { location.hash = `#/bids/${l.bid_id}`; } } : {},
      el("td", {}, el("strong", {}, l.bid_number)),
      el("td", { title: l.items }, clip(l.items, 90)),
      el("td", {}, l.your_items),
      el("td", {}, formatDateTime(l.closes_at)),
      el("td", {}, docBadge(l.checked), l.note ? el("div", { class: "hint" }, l.note) : null))))));
}

// "Not found" here read as "GeM had nothing"; most of these are simply not downloaded yet (5 a search).
const DOC_STYLE = { DOWNLOADED: ["ok", "Checked"], PENDING: ["neutral", "Not yet"],
  SKIPPED: ["neutral", "Skipped"], FAILED: ["bad", "Failed"] };
function docBadge(status) {
  const [cls, text] = DOC_STYLE[status] || ["neutral", status];
  return el("span", { class: `badge ${cls}` }, text);
}

// Nothing the model proposes is saved until he clicks Confirm.
function proposalCard(card, api) {
  const status = el("div");
  const confirm = el("button", { type: "button" }, "Confirm");
  const cancel = el("button", { type: "button", class: "secondary" }, "Cancel");
  // Tells the model what happened here, so the next question builds on it.
  const note = (text) => status.dispatchEvent(new CustomEvent("chat-note", { bubbles: true, detail: text }));
  confirm.addEventListener("click", async () => {
    confirm.disabled = cancel.disabled = true;
    try {
      await api.saveProfile(card.profile);
      status.replaceChildren(notice("ok", "Saved."));
    } catch (error) {
      status.replaceChildren(notice("bad", error.message));
      confirm.disabled = cancel.disabled = false;
      return;
    }
    if (!("products" in card.changes)) return note("He confirmed the profile change; it is saved.");
    // He named products to find bids for: search now rather than make him ask a second time.
    try {
      await api.runDiscovery();
      status.append(searchCard(api));
      note("He confirmed the products; they are saved and a GeM search for them has started.");
    } catch (error) {
      status.append(notice("warn", `Saved, but the search didn't start: ${error.message}`));
      note(`He confirmed the products; they are saved. The search didn't start: ${error.message}`);
    }
  });
  cancel.addEventListener("click", () => {
    confirm.disabled = cancel.disabled = true;
    status.replaceChildren(notice("warn", "Not saved."));
  });
  return el("div", { class: "card" },
    el("strong", {}, "Change your profile?"),
    el("ul", { class: "reasons" }, Object.entries(card.changes).map(([k, v]) =>
      el("li", {}, el("strong", {}, PROFILE_LABELS[k] || k), ": ", shownValue(v)))),
    el("div", { class: "row" }, confirm, cancel), status);
}

// A search takes minutes; this card follows it and shows what it found.
function searchCard(api) {
  const box = el("div", { class: "card" }, notice("warn", "Searching GeM… this updates by itself."));
  const tick = async () => {
    if (!box.isConnected) return;
    try {
      const status = await api.getDiscovery();
      if (!status.running) {
        const mine = status.listings.filter((l) => l.matched_items > 0).map((l) => ({
          bid_number: l.bid_number, items: l.category || "", your_items: `${l.matched_items} of ${l.total_items}`,
          closes_at: l.ends_at, checked: l.doc_status, note: l.doc_note, bid_id: l.bid_id }));
        box.replaceChildren(
          notice(status.last_run && status.last_run.outcome === "ok" ? "ok" : "bad",
            (status.last_run && status.last_run.message) || "The search ended."),
          mine.length ? listingsCard({ listings: mine }) : el("div", { class: "hint" }, "None of the bids found has your products as an item."));
        return;
      }
    } catch { /* keep following */ }
    setTimeout(tick, 4000);
  };
  setTimeout(tick, 4000);
  return box;
}

function renderCard(card, api) {
  switch (card.type) {
    case "bid_list": return bidListCard(card);
    case "bid": return bidCard(card);
    case "listings": return listingsCard(card);
    case "profile_proposal": return proposalCard(card, api);
    case "search": return searchCard(api);
    case "safety_stop": return el("div", {}, notice("bad", `Fetching from GeM is stopped for safety: ${card.reason || ""}`),
      el("a", { href: "#/safety" }, "Check GeM in a normal browser, then resume on the Safety page"));
    case "contact_needed": return contactCard(api, null);
    default: return null;
  }
}

// The model writes Markdown now and then (tables, **bold**, # headings, - bullets) however it's
// asked. Shown as formatting, built from text nodes like everything else: never HTML from the model.
const TABLE_ROW = /^\s*\|.*\|\s*$/;
const RULE_ROW = /^[\s|:-]+$/;  // |---|:---:|

const inline = (text) => text.split(/\*\*(.+?)\*\*/g).map((part, i) => (i % 2 ? el("strong", {}, part) : part));
const cells = (row) => row.trim().slice(1, -1).split("|").map((cell) => inline(cell.trim()));

function richText(text) {
  const blocks = [];
  let prose = [];
  const endProse = () => {
    const joined = prose.join("\n").trim();
    if (joined) blocks.push(el("div", {}, inline(joined)));
    prose = [];
  };
  const lines = text.split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    if (!TABLE_ROW.test(lines[i])) {
      prose.push(lines[i].replace(/^\s*#{1,6}\s+/, "").replace(/^(\s*)[-*]\s+/, "$1• "));
      continue;
    }
    endProse();
    const rows = [];
    for (; i < lines.length && TABLE_ROW.test(lines[i]); i++) if (!RULE_ROW.test(lines[i])) rows.push(cells(lines[i]));
    i--;
    if (!rows.length) continue;
    const [head, ...body] = rows;
    blocks.push(el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, head.map((cell) => el("th", {}, cell)))),
      el("tbody", {}, body.map((row) => el("tr", {}, row.map((cell) => el("td", {}, cell))))))));
  }
  endProse();
  return blocks;
}

function bubble(item, api) {
  if (item.kind === "user") return el("div", { class: "msg user" }, item.text);
  if (item.kind === "bot") return el("div", { class: "msg bot" }, richText(item.text));
  // Cards that act (Confirm, live search) only make sense the moment they arrive.
  if (item.card.type === "profile_proposal" || item.card.type === "search") {
    return item.fresh ? renderCard(item.card, api) : null;
  }
  return renderCard(item.card, api);
}

export async function renderChat(container, _params, api) {
  const state = load();
  const log = el("div", { class: "chat-log", "aria-live": "polite" });
  const input = el("textarea", { id: "chat-input", rows: 2,
    placeholder: "Ask anything: “find fire extinguisher bids”, “which bids close this week?”, “what does bid 3 need?”" });
  const send = el("button", { type: "submit" }, "Send");
  const clear = el("button", { type: "button", class: "secondary" }, "New chat");
  // Invite-only bids open only after his own GeM login: he downloads the PDF there and adds it here.
  // This app never logs in to GeM.
  const file = el("input", { type: "file", id: "bid-file", accept: "application/pdf", hidden: true });
  const attach = el("button", { type: "button", class: "secondary", onclick: () => file.click() }, "Add bid PDF");
  const form = el("form", { class: "chat-form" }, input, el("div", { class: "row" }, send, attach, clear, file));

  const show = (item) => {
    const node = bubble(item, api);
    if (node) log.append(node);
    log.lastElementChild?.scrollIntoView({ block: "end" });
  };

  const [chat, safety] = await Promise.all([api.chatStatus(), api.getSafety()]);
  const setup = el("div");
  if (!chat.ready) {
    setup.append(notice("warn", "The chat has no Groq API key yet. On a server, set GROQ_API_KEY; on a Mac, run: gem-assistant set-groq-key"));
  }
  if (!safety.contact_email) setup.append(contactCard(api, null));

  container.append(setup, log, form);
  state.shown.forEach(show);
  if (state.shown.length === 0) {
    show({ kind: "bot", text: "Namaste! Tell me what you sell, and I'll look for GeM bids and check them for you." });
  }

  const ask = async (said, addedBidId = null) => {
    const text = (said ?? input.value).trim();
    if (!text) return;
    input.value = "";
    send.disabled = true;
    const user = { kind: "user", text };
    state.messages.push({ role: "user", content: text });
    state.shown.push(user);
    show(user);
    const waiting = el("div", { class: "msg bot subtle" }, "…");
    log.append(waiting);
    try {
      const reply = await api.chat(state.messages, addedBidId);
      waiting.remove();
      state.messages.push(...reply.messages);
      for (const card of reply.cards) {
        state.shown.push({ kind: "card", card });
        show({ kind: "card", card, fresh: true });
      }
      for (const m of reply.messages) {
        if (m.role === "assistant" && m.content) {
          const item = { kind: "bot", text: m.content };
          state.shown.push(item);
          show(item);
        }
      }
      save(state);
    } catch (error) {
      waiting.remove();
      state.messages.pop();  // not answered: he can ask again
      log.append(notice("bad", error.message));
      save(state);
    } finally {
      send.disabled = false;
      input.focus();
    }
  };

  log.addEventListener("chat-note", (event) => {
    state.messages.push({ role: "assistant", content: event.detail });
    save(state);
  });
  file.addEventListener("change", async () => {
    const pdf = file.files[0];
    file.value = "";  // the same file can be chosen again
    if (!pdf) return;
    attach.disabled = true;
    try {
      const { id } = await api.importBidPdf(pdf);
      await ask(`I've added a bid PDF myself (${pdf.name}). Check it for me.`, id);  // the server opens it
    } catch (error) {
      log.append(notice("bad", `That PDF couldn't be read as a GeM bid: ${error.message}`));
    } finally {
      attach.disabled = false;
    }
  });
  form.addEventListener("submit", (event) => { event.preventDefault(); ask(); });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); ask(); }
  });
  clear.addEventListener("click", () => {
    state.messages = [];
    state.shown = [];
    save(state);
    log.replaceChildren();
    show({ kind: "bot", text: "New chat. What would you like to do?" });
  });
}
