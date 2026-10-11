import { el, verdictBadge, formatDateTime, notice } from "../dom.js";

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
    container.append(el("div", { class: "card empty" }, "No bids yet. Analyse a bid PDF above to get started."));
    return;
  }

  const rows = bids.map((bid) =>
    el("tr", { class: "clickable", onclick: () => { location.hash = `#/bids/${bid.id}`; } },
      el("td", {}, el("strong", {}, bid.bid_number || "Unknown bid no.")),
      el("td", {}, bid.item_category || "—"),
      el("td", {}, bid.organisation || bid.ministry || "—"),
      el("td", {}, formatDateTime(bid.closes_at)),
      el("td", {}, verdictBadge(bid.verdict)),
      el("td", {}, bid.review_count ? `${bid.review_count} to check` : "—"),
    ));

  container.append(
    el("div", { class: "table-wrap" },
      el("table", {},
        el("thead", {}, el("tr", {},
          ...["Bid no.", "Item", "Buyer", "Closes", "Verdict", "Review"].map((h) => el("th", {}, h)))),
        el("tbody", {}, rows),
      ),
    ),
  );
}
