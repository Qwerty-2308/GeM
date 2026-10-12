import { el, verdictBadge, formatDateTime, notice, clip } from "../dom.js";

const VERDICT_RANK = { ELIGIBLE: 0, NEEDS_REVIEW: 1, BLOCKED: 2 };
const closesAt = (bid) => (bid.closes_at ? new Date(bid.closes_at).getTime() : Infinity);

export async function renderBidList(container, _params, api) {
  const status = el("div");

  const fileInput = el("input", { type: "file", accept: "application/pdf", id: "bid-pdf" });
  const importButton = el("button", { type: "submit" }, "Analyse PDF");

  const importForm = el("form", { class: "card" },
    el("label", { for: "bid-pdf" }, "Analyse a bid document you downloaded"),
    el("div", { class: "row" }, fileInput, importButton),
    el("div", { class: "hint" },
      "The PDF is read on this computer. Nothing is fetched from GeM when you do this."),
    status,
  );

  importForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const file = fileInput.files[0];
    if (!file) {
      status.replaceChildren(notice("warn", "Choose a PDF first."));
      return;
    }
    importButton.disabled = true;
    status.replaceChildren(notice("warn", "Reading the document…"));
    try {
      const result = await api.importBidPdf(file);
      location.hash = `#/bids/${result.id}`;
    } catch (error) {
      status.replaceChildren(notice("bad", error.message));
    } finally {
      importButton.disabled = false;
    }
  });

  // ---- add by link ----
  const linkInput = el("input", { type: "text", id: "bid-link",
    placeholder: "https://bidplus.gem.gov.in/showbidDocument/8506455" });
  const linkButton = el("button", { type: "submit" }, "Download & analyse");
  const linkStatus = el("div");
  const linkForm = el("form", { class: "card" },
    el("label", { for: "bid-link" }, "Add a bid from its GeM link"),
    el("div", { class: "row" }, el("div", { style: "flex:1;min-width:240px" }, linkInput), linkButton),
    el("div", { class: "hint" }, "Makes one request to GeM through the safety layer. A bid already downloaded is never fetched again."),
    linkStatus,
  );
  linkForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!linkInput.value.trim()) return;
    linkButton.disabled = true;
    linkStatus.replaceChildren(notice("warn", "Downloading from GeM…"));
    try {
      const result = await api.fetchBidByLink(linkInput.value.trim());
      location.hash = `#/bids/${result.id}`;
    } catch (error) {
      linkStatus.replaceChildren(notice("bad", error.message));
    } finally {
      linkButton.disabled = false;
    }
  });

  container.append(
    el("h1", {}, "Bids"),
    el("div", { class: "subtle" }, "Each bid is checked against your seller profile. Open one to see the evidence."),
    linkForm,
    importForm,
  );

  const bids = await api.listBids();
  if (bids.length === 0) {
    container.append(el("div", { class: "card empty" },
      "No bids yet. Fill in your ", el("a", { href: "#/profile" }, "profile"), ", then ",
      el("a", { href: "#/discover" }, "search GeM"), " — or add a bid by link or PDF above."));
    return;
  }

  const sort = el("select", { id: "bid-sort", style: "width:auto" },
    el("option", { value: "closing" }, "Closing soonest"),
    el("option", { value: "verdict" }, "Best verdict first"),
    el("option", { value: "added" }, "Recently added"));
  const showClosed = el("input", { type: "checkbox", id: "show-closed" });
  const table = el("div");

  const draw = () => {
    const now = Date.now();
    const open = bids.filter((bid) => closesAt(bid) > now);
    const shown = showClosed.checked ? [...bids] : open;
    const byClosing = (a, b) => closesAt(a) - closesAt(b);
    shown.sort({
      closing: byClosing,
      verdict: (a, b) => (VERDICT_RANK[a.verdict] ?? 9) - (VERDICT_RANK[b.verdict] ?? 9) || byClosing(a, b),
      added: (a, b) => b.id - a.id,
    }[sort.value]);
    const hidden = bids.length - open.length;
    table.replaceChildren(
      !showClosed.checked && hidden ? el("div", { class: "hint" }, `${hidden} closed bid${hidden === 1 ? "" : "s"} hidden.`) : "",
      el("div", { class: "table-wrap" },
        el("table", {},
          el("thead", {}, el("tr", {},
            ...["Bid no.", "Item", "Buyer", "Closes", "Verdict", "Review"].map((h) => el("th", {}, h)))),
          el("tbody", {}, shown.map((bid) =>
            el("tr", { class: "clickable", onclick: () => { location.hash = `#/bids/${bid.id}`; } },
              el("td", {}, el("strong", {}, bid.bid_number || "Unknown bid no.")),
              el("td", { title: bid.item_category || "" }, clip(bid.item_category) || "—"),
              el("td", {}, bid.organisation || bid.ministry || "—"),
              el("td", {}, formatDateTime(bid.closes_at)),
              el("td", {}, verdictBadge(bid.verdict)),
              el("td", {}, bid.review_count ? `${bid.review_count} to check` : "—"),
            ))),
        ),
      ),
    );
  };
  sort.addEventListener("change", draw);
  showClosed.addEventListener("change", draw);
  container.append(
    el("div", { class: "row", style: "margin-top:16px" },
      el("label", { for: "bid-sort", style: "margin:0" }, "Sort"), sort,
      el("label", { class: "check", for: "show-closed" }, showClosed, "Show closed bids")),
    table,
  );
  draw();
}
