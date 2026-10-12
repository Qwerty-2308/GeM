import { el, notice, formatDateTime } from "../dom.js";

const STATUS_STYLE = {
  PENDING: ["neutral", "Not analysed"],
  DOWNLOADED: ["ok", "Analysed"],
  FAILED: ["bad", "Failed"],
  SKIPPED: ["neutral", "Skipped"],
};

const OUTCOME_STYLE = { ok: "ok", stopped: "bad", failed: "bad" };

// GeM lists every item of a bid; the full text stays in the tooltip.
function clip(text, max = 140) {
  return text && text.length > max ? `${text.slice(0, max)}…` : text;
}

function docBadge(status) {
  const [cls, text] = STATUS_STYLE[status] || ["neutral", status];
  return el("span", { class: `badge ${cls}` }, text);
}

export async function renderDiscover(container, _params, api) {
  const status = await api.getDiscovery();
  const { limits } = status;

  container.append(
    el("h1", {}, "Find bids on GeM"),
    el("div", { class: "subtle" },
      "Searches GeM's public bid list for the categories in your seller profile, then downloads and checks a few new bids."),
  );

  // ---- run card ----
  const message = el("div");
  const runButton = el("button", { type: "button" }, status.running ? "Searching…" : "Search GeM now");
  const now = new Date();
  const nextAllowed = status.next_allowed_at ? new Date(status.next_allowed_at) : null;
  const waiting = nextAllowed && nextAllowed > now;
  runButton.disabled = status.running || waiting || status.keywords.length === 0;

  runButton.addEventListener("click", async () => {
    runButton.disabled = true;
    try {
      await api.runDiscovery();
      message.replaceChildren(notice("warn", "Search started. This page updates by itself."));
      poll(container, api);
    } catch (error) {
      message.replaceChildren(notice("bad", error.message));
      runButton.disabled = false;
    }
  });

  const card = el("div", { class: "card" },
    el("div", { class: "row" },
      el("div", { style: "flex:1" },
        el("label", {}, "Keywords (from your profile)"),
        status.keywords.length
          ? el("div", {}, status.keywords.join(", "))
          : el("div", {}, "None yet. ", el("a", { href: "#/profile" }, "Add product categories to your profile.")),
      ),
      runButton,
    ),
    el("div", { class: "hint", style: "margin-top:8px" },
      `Each search: at most ${limits.max_keywords} keywords × ${limits.max_pages_per_keyword} pages, ` +
      `${limits.max_downloads_per_run} bid downloads, and at least ${limits.min_minutes_between_runs} minutes between searches.`),
    waiting ? el("div", { class: "hint" }, `Next search allowed at ${formatDateTime(status.next_allowed_at)}.`) : null,
    message,
  );
  container.append(card);

  // ---- last run ----
  if (status.last_run) {
    const run = status.last_run;
    const kind = run.outcome ? OUTCOME_STYLE[run.outcome] || "warn" : "warn";
    container.append(
      el("h2", {}, "Last search"),
      el("div", { class: "card" },
        el("div", { class: "row" },
          el("span", { class: `badge ${kind}` }, run.outcome || "running"),
          el("span", { class: "subtle" }, `${formatDateTime(run.started_at)} · ${run.requests} requests to GeM`),
        ),
        el("p", {}, run.message || "Running…"),
        run.outcome === "stopped"
          ? el("p", {}, "Fetching is paused for safety. ", el("a", { href: "#/safety" }, "See the Safety page."))
          : null,
      ),
    );
  }

  if (status.running) poll(container, api);

  // ---- listings ----
  container.append(el("h2", {}, "Found on GeM"));
  if (status.listings.length === 0) {
    container.append(el("div", { class: "card empty" }, "Nothing found yet."));
    return;
  }
  container.append(
    el("div", { class: "table-wrap" },
      el("table", {},
        el("thead", {}, el("tr", {}, ...["Bid no.", "Item", "Buyer", "Closes", "Status"].map((h) => el("th", {}, h)))),
        el("tbody", {}, status.listings.map((item) =>
          el("tr", item.bid_id ? { class: "clickable", onclick: () => { location.hash = `#/bids/${item.bid_id}`; } } : {},
            el("td", {}, el("strong", {}, item.bid_number), item.kind === "RA" ? el("span", { class: "subtle" }, " (RA)") : null),
            el("td", { title: item.category }, clip(item.category) || "—"),
            el("td", {}, item.department || item.ministry || "—"),
            el("td", {}, formatDateTime(item.ends_at)),
            el("td", {}, docBadge(item.doc_status), item.doc_note ? el("div", { class: "hint" }, item.doc_note) : null),
          ))),
      ),
    ),
  );
}

// Re-render while a search runs; stops as soon as the user leaves the page.
function poll(container, api) {
  if (container.dataset.polling) return;
  container.dataset.polling = "1";
  const tick = async () => {
    if (!container.isConnected || !location.hash.startsWith("#/discover")) {
      delete container.dataset.polling;
      return;
    }
    try {
      const status = await api.getDiscovery();
      if (!status.running) {
        delete container.dataset.polling;
        container.replaceChildren();
        await renderDiscover(container, {}, api);
        return;
      }
    } catch { /* keep polling */ }
    setTimeout(tick, 3000);
  };
  setTimeout(tick, 3000);
}
