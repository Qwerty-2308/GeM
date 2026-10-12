// Small DOM helpers. Bid text comes from government PDFs and is untrusted,
// so everything is set through textContent — never innerHTML with data.

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, String(value));
  }
  for (const child of children.flat()) {
    if (child === undefined || child === null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

const VERDICT_STYLE = {
  ELIGIBLE: ["ok", "Eligible"],
  NEEDS_REVIEW: ["warn", "Needs review"],
  BLOCKED: ["bad", "Blocked"],
};

const STATUS_STYLE = {
  FOUND: ["ok", "Found"],
  NOT_FOUND: ["neutral", "Not found"],
  UNCLEAR: ["warn", "Unclear"],
  CONFLICTING: ["bad", "Conflicting"],
};

export function verdictBadge(verdict) {
  const [cls, text] = VERDICT_STYLE[verdict] || ["neutral", verdict];
  return el("span", { class: `badge ${cls}` }, text);
}

export function statusBadge(status) {
  const [cls, text] = STATUS_STYLE[status] || ["neutral", status];
  return el("span", { class: `badge ${cls}` }, text);
}

export function formatDateTime(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("en-IN", {
    day: "2-digit", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata",
  });
}

export function notice(kind, text) {
  return el("div", { class: `notice ${kind}`, role: kind === "bad" ? "alert" : "status" }, text);
}

export function evidenceBlock(evidence) {
  if (!evidence) return el("span", { class: "subtle" }, "—");
  return el("div", {},
    el("div", { class: "page-ref" }, `Page ${evidence.page}`),
    el("div", { class: "quote" }, evidence.quote),
  );
}

// GeM item lists run to 1,000 characters; the full text goes in a tooltip.
export function clip(text, max = 140) {
  return text && text.length > max ? `${text.slice(0, max)}…` : text;
}

export function splitList(text) {
  return text.split(/[,\n]/).map((s) => s.trim()).filter(Boolean);
}
