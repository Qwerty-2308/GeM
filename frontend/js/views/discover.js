import { el, notice, formatDateTime, splitList, clip } from "../dom.js";
import { EMPTY_PROFILE } from "./profile.js";
import { contactCard } from "./safety.js";

const STATUS_STYLE = {
  PENDING: ["neutral", "Not analysed"],
  DOWNLOADED: ["ok", "Analysed"],
  FAILED: ["bad", "Failed"],
  SKIPPED: ["neutral", "Skipped"],
};

const OUTCOME_STYLE = { ok: "ok", stopped: "bad", failed: "bad" };

function docBadge(status) {
  const [cls, text] = STATUS_STYLE[status] || ["neutral", status];
  return el("span", { class: `badge ${cls}` }, text);
}

function matchText(item) {
  if (!item.matched_items) return "—";
  return item.matched_items === item.total_items ? "All" : `${item.matched_items} of ${item.total_items}`;
}

// The search's own settings: what counts as his product, and which bids come first.
function lookForForm(container, api, profile) {
  const products = el("textarea", { id: "products" });
  products.value = profile.categories.join(", ");
  const ignore = el("textarea", { id: "ignore" });
  ignore.value = profile.ignore_words.join(", ");
  const order = el("select", { id: "bid-order" },
    el("option", { value: "match" }, "Bids made mostly of my products"),
    el("option", { value: "closing" }, "Bids closing soonest"));
  order.value = profile.bid_order;
  const save = el("button", { type: "submit" }, "Save what to look for");
  const status = el("div");

  const form = el("form", { class: "card" },
    el("div", { class: "grid-2" },
      el("div", {}, el("label", { for: "products" }, "Products you sell"), products,
        el("div", { class: "hint" }, "Comma-separated, as GeM names them: fire extinguisher, office chair. " +
          "A bid counts when one of its items has all the words of one product.")),
      el("div", {}, el("label", { for: "ignore" }, "Ignore items that mention"), ignore,
        el("div", { class: "hint" }, "For example: refilling, repair, AMC. Such items never count as yours.")),
      el("div", {}, el("label", { for: "bid-order" }, "Download and list first"), order,
        el("div", { class: "hint" }, "Most bids are judged on total value, so you must quote every item in them. " +
          "Bids made mostly of your products are the ones you can win.")),
    ),
    el("div", { class: "row", style: "margin-top:16px" }, save),
    status,
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    save.disabled = true;
    try {
      await api.saveProfile({ ...profile, categories: splitList(products.value),
        ignore_words: splitList(ignore.value), bid_order: order.value });
      container.replaceChildren();
      await renderDiscover(container, {}, api);
    } catch (error) {
      status.replaceChildren(notice("bad", error.message));
      save.disabled = false;
    }
  });
  return form;
}

export async function renderDiscover(container, _params, api) {
  const [status, savedProfile, safety] = await Promise.all([api.getDiscovery(), api.getProfile(), api.getSafety()]);
  const profile = { ...EMPTY_PROFILE, ...savedProfile };
  const { limits } = status;

  container.append(
    el("h1", {}, "Find bids on GeM"),
    el("div", { class: "subtle" },
      "Searches GeM's public bid list for your products, then downloads and checks the bids that fit best."),
  );
  if (!safety.contact_email) {
    container.append(el("h2", {}, "Before your first search"), contactCard(api, null));
  }
  container.append(el("h2", {}, "What to look for"), lookForForm(container, api, profile));

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
        el("label", {}, "Searches GeM for"),
        status.keywords.length
          ? el("div", {}, status.keywords.join(", "))
          : el("div", {}, "Nothing yet: add your products above."),
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
  // His products first (the server ranks them by his chosen order); the rest only on request.
  const mine = status.listings.filter((item) => item.matched_items > 0);
  const others = status.listings.length - mine.length;
  const showAll = el("input", { type: "checkbox", id: "show-all" });
  const table = el("div");
  const draw = () => {
    const shown = showAll.checked ? status.listings : mine;
    table.replaceChildren(shown.length === 0
      ? el("div", { class: "card empty" }, `None of the ${status.listings.length} bids found has your products as an item.`)
      : el("div", { class: "table-wrap" },
        el("table", {},
          el("thead", {}, el("tr", {},
            ...["Bid no.", "Items", "Your items", "Buyer", "Closes", "Status"].map((h) => el("th", {}, h)))),
          el("tbody", {}, shown.map((item) =>
            el("tr", item.bid_id ? { class: "clickable", onclick: () => { location.hash = `#/bids/${item.bid_id}`; } } : {},
              el("td", {}, el("strong", {}, item.bid_number), item.kind === "RA" ? el("span", { class: "subtle" }, " (RA)") : null),
              el("td", { title: item.category }, clip(item.category) || "—"),
              el("td", {}, matchText(item)),
              el("td", {}, item.department || item.ministry || "—"),
              el("td", {}, formatDateTime(item.ends_at)),
              el("td", {}, docBadge(item.doc_status), item.doc_note ? el("div", { class: "hint" }, item.doc_note) : null),
            ))),
        ),
      ));
  };
  showAll.addEventListener("change", draw);
  if (others) {
    container.append(el("label", { class: "check", for: "show-all" }, showAll,
      `Also show ${others} bid${others === 1 ? "" : "s"} GeM returned that aren't your products`));
  }
  container.append(table);
  draw();
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
        // Redraw for the results, but keep whatever he was typing and hadn't saved yet.
        const typed = [...container.querySelectorAll("textarea[id], select[id], input[type=email][id]")]
          .map((field) => [field.id, field.value]);
        container.replaceChildren();
        await renderDiscover(container, {}, api);
        for (const [id, value] of typed) {
          const field = container.querySelector(`#${id}`);
          if (field) field.value = value;
        }
        return;
      }
    } catch { /* keep polling */ }
    setTimeout(tick, 3000);
  };
  setTimeout(tick, 3000);
}
