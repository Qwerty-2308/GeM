import { el, notice, splitList } from "../dom.js";

export const EMPTY_PROFILE = {
  business_name: "",
  is_mse: false,
  is_startup: false,
  is_oem: false,
  annual_turnover_rupees: null,
  years_experience: null,
  categories: [],
  certifications: [],
  states: [],
  ignore_words: [],
  bid_order: "match",
};

function field(labelText, input, hint) {
  return el("div", {}, el("label", { for: input.id }, labelText), input, hint ? el("div", { class: "hint" }, hint) : null);
}

function checkbox(id, labelText, checked) {
  const input = el("input", { type: "checkbox", id, checked });
  return { input, node: el("label", { class: "check", for: id }, input, labelText) };
}

// "10,00,000", "10 lakh", "1.5 crore", "₹ 25,50,000" -> rupees; NaN when it can't be read.
// A number box would silently save "10,00,000" as blank in Safari.
export function parseRupees(text) {
  const m = text.toLowerCase().replace(/,/g, "").trim()
    .match(/^(?:₹|rs\.?|inr)?\s*(\d+(?:\.\d+)?)\s*(lakhs?|lacs?|crores?|cr)?$/);
  if (!m) return NaN;
  const unit = !m[2] ? 1 : m[2].startsWith("c") ? 1e7 : 1e5;
  return Math.round(Number(m[1]) * unit);
}

export const inr = (rupees) => `₹${rupees.toLocaleString("en-IN")}`;

export async function renderProfile(container, _params, api) {
  const profile = (await api.getProfile()) || EMPTY_PROFILE;

  const name = el("input", { type: "text", id: "business_name", value: profile.business_name });
  const turnover = el("input", { type: "text", inputmode: "decimal", id: "turnover",
    placeholder: "e.g. 25,00,000 or 25 lakh",
    value: profile.annual_turnover_rupees == null ? "" : profile.annual_turnover_rupees.toLocaleString("en-IN") });
  const turnoverHint = el("div", { class: "hint" }, "Average of the last 3 financial years.");
  turnover.addEventListener("input", () => {
    const rupees = parseRupees(turnover.value);
    turnoverHint.textContent = !turnover.value.trim() ? "Average of the last 3 financial years."
      : Number.isNaN(rupees) ? "Can't read this yet. Write it like 25,00,000 or 25 lakh." : `= ${inr(rupees)}`;
  });
  const years = el("input", { type: "text", inputmode: "numeric", id: "years", value: profile.years_experience ?? "" });
  const categories = el("textarea", { id: "categories" }); categories.value = profile.categories.join(", ");
  const certs = el("textarea", { id: "certs" }); certs.value = profile.certifications.join(", ");
  const states = el("textarea", { id: "states" }); states.value = profile.states.join(", ");
  const mse = checkbox("is_mse", "Registered MSE (Udyam)", profile.is_mse);
  const startup = checkbox("is_startup", "DPIIT-recognised startup", profile.is_startup);
  const oem = checkbox("is_oem", "We are the OEM (manufacturer), not a reseller", profile.is_oem);

  const status = el("div");
  const save = el("button", { type: "submit" }, "Save profile");

  const form = el("form", { class: "card" },
    el("div", { class: "grid-2" },
      field("Business name", name),
      el("div", {}, el("label", { for: "turnover" }, "Annual turnover (₹)"), turnover, turnoverHint),
      field("Years of experience", years),
      el("div", {}, el("label", {}, "Status"), mse.node, startup.node, oem.node),
      field("Products you sell", categories, "Comma-separated, as GeM names them. Also on the Find bids page."),
      field("Certifications held", certs, "For example: ISO 9001, BIS"),
      field("States you can deliver to", states, "Comma-separated."),
    ),
    el("div", { class: "row", style: "margin-top:16px" }, save),
    status,
  );

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const rupees = turnover.value.trim() ? parseRupees(turnover.value) : null;
    if (Number.isNaN(rupees)) {
      status.replaceChildren(notice("bad", `Turnover: couldn't read “${turnover.value.trim()}”. Write it like 25,00,000 or 25 lakh.`));
      return;
    }
    if (years.value.trim() && !/^\d+$/.test(years.value.trim())) {
      status.replaceChildren(notice("bad", "Years of experience: write a whole number, like 3."));
      return;
    }
    save.disabled = true;
    try {
      await api.saveProfile({
        ...profile,
        business_name: name.value.trim(),
        is_mse: mse.input.checked,
        is_startup: startup.input.checked,
        is_oem: oem.input.checked,
        annual_turnover_rupees: rupees,
        years_experience: years.value.trim() ? Number(years.value.trim()) : null,
        categories: splitList(categories.value),
        certifications: splitList(certs.value),
        states: splitList(states.value),
      });
      status.replaceChildren(notice("ok", "Saved. Bid verdicts now use this profile."));
    } catch (error) {
      status.replaceChildren(notice("bad", error.message));
    } finally {
      save.disabled = false;
    }
  });

  container.append(
    el("h1", {}, "Seller profile"),
    el("div", { class: "subtle" },
      "Used to check each bid. Stored only on this computer and never sent to GeM or to the AI."),
    form,
  );
}
