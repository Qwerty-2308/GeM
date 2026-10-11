import { el, verdictBadge, statusBadge, evidenceBlock, formatDateTime, notice } from "../dom.js";

// Dates arrive as ISO strings; show them the way they appear on GeM.
function displayValue(value) {
  if (value === null || value === undefined) return "—";
  if (/^\d{4}-\d{2}-\d{2}T/.test(value)) return formatDateTime(value);
  return value;
}

// A corrigendum can change the deadline, quantity or terms. It is shown verbatim and
// never interpreted — the figures in "Bid details" below are from the original PDF.
const CORRIGENDUM_COPY = {
  PRESENT: ["warn", "This bid was amended after it was published. Read the corrigendum; "
                  + "the values below come from the original bid document."],
  NONE: ["ok", "GeM reports no corrigendum for this bid."],
  NOT_CHECKED: ["warn", "Not checked for a corrigendum. Open the bid on GeM and use "
                      + "“View Corrigendum/Representation” before you bid."],
  FAILED: ["bad", "The corrigendum check did not finish, so this bid may have been amended "
                + "without us seeing it. Check it on GeM."],
};

function corrigendumSection(corrigendum) {
  if (!corrigendum) return [];
  const [kind, message] = CORRIGENDUM_COPY[corrigendum.status] || ["warn", corrigendum.status];
  const parts = [el("h2", {}, "Corrigendum"), notice(kind, message)];
  if (corrigendum.note) parts.push(el("div", { class: "hint" }, corrigendum.note));
  if (corrigendum.checked_at) {
    parts.push(el("div", { class: "subtle" }, `Checked ${formatDateTime(corrigendum.checked_at)}`));
  }
  // textContent via el(), never innerHTML: this text comes from the portal.
  if (corrigendum.text) parts.push(el("div", { class: "card quote" }, corrigendum.text));
  return parts;
}

export async function renderBidDetail(container, { id }, api) {
  const bid = await api.getBid(id);

  container.append(
    el("a", { href: "#/bids" }, "← All bids"),
    el("div", { class: "row", style: "margin-top:12px" },
      el("h1", {}, bid.bid_number || "Unknown bid number"),
      verdictBadge(bid.verdict),
    ),
    el("div", { class: "subtle" },
      `${bid.source_name} · ${bid.page_count} pages · analysed ${formatDateTime(bid.imported_at)}`),
    el("div", { class: "notice warn" },
      "This is a decision aid, not a decision. Check every quote against the PDF before you bid."),
  );

  // ---- why this verdict ----
  const reasons = bid.results.filter((r) => r.outcome !== "ELIGIBLE");
  container.append(el("h2", {}, "Why this verdict"));
  if (reasons.length === 0) {
    container.append(el("div", { class: "card" }, "No problems found by the checks."));
  } else {
    container.append(el("div", { class: "card" },
      el("ul", { class: "reasons" },
        reasons.map((r) => el("li", {},
          verdictBadge(r.outcome), " ", r.reason,
          r.evidence ? evidenceBlock(r.evidence) : null,
        )),
      ),
    ));
  }

  container.append(...corrigendumSection(bid.corrigendum));

  // ---- extracted fields ----
  container.append(el("h2", {}, "Bid details"));
  container.append(
    el("div", { class: "table-wrap" },
      el("table", {},
        el("thead", {}, el("tr", {}, ...["Field", "Value", "Status", "Evidence"].map((h) => el("th", {}, h)))),
        el("tbody", {}, bid.fields.map((f) =>
          el("tr", {},
            el("td", {}, f.label, f.critical ? el("span", { class: "subtle" }, " *") : null),
            el("td", {}, displayValue(f.value)),
            el("td", {}, statusBadge(f.status)),
            el("td", {}, evidenceBlock(f.evidence)),
          ))),
      ),
    ),
    el("div", { class: "hint" }, "* critical field"),
  );

  // ---- clause flags ----
  container.append(el("h2", {}, "Buyer clauses worth reading"));
  if (bid.flags.length === 0) {
    container.append(el("div", { class: "card subtle" }, "No clauses matched the warning patterns."));
  } else {
    container.append(el("div", { class: "card" },
      el("ul", { class: "reasons" },
        bid.flags.map((flag) => el("li", {}, el("strong", {}, flag.label), evidenceBlock(flag.evidence))),
      ),
    ));
  }

  if (bid.unread_lines > 0) {
    container.append(notice("warn",
      `${bid.unread_lines} Hindi lines were skipped because the PDF's Hindi text doesn't extract cleanly. ` +
      "The English labels were used instead."));
  }
}
