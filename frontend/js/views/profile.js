import { el, notice, splitList } from "../dom.js";

const EMPTY_PROFILE = {
  business_name: "",
  is_mse: false,
  is_startup: false,
  is_oem: false,
  annual_turnover_rupees: null,
  years_experience: null,
  categories: [],
  certifications: [],
  states: [],
};

function field(labelText, input, hint) {
  return el("div", {}, el("label", { for: input.id }, labelText), input, hint ? el("div", { class: "hint" }, hint) : null);
}

function checkbox(id, labelText, checked) {
  const input = el("input", { type: "checkbox", id, checked });
  return { input, node: el("label", { class: "check", for: id }, input, labelText) };
}

function numberOrNull(value) {
  return value === "" ? null : Number(value);
}

export async function renderProfile(container, _params, api) {
  const profile = (await api.getProfile()) || EMPTY_PROFILE;

  const name = el("input", { type: "text", id: "business_name", value: profile.business_name });
  const turnover = el("input", { type: "number", id: "turnover", min: 0, value: profile.annual_turnover_rupees ?? "" });
  const years = el("input", { type: "number", id: "years", min: 0, value: profile.years_experience ?? "" });
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
      field("Annual turnover (₹)", turnover, "Average of the last 3 financial years, in rupees."),
      field("Years of experience", years),
      el("div", {}, el("label", {}, "Status"), mse.node, startup.node, oem.node),
      field("Product categories", categories, "Comma-separated, as they appear on GeM."),
      field("Certifications held", certs, "For example: ISO 9001, BIS"),
      field("States you can deliver to", states, "Comma-separated."),
    ),
    el("div", { class: "row", style: "margin-top:16px" }, save),
    status,
  );

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    save.disabled = true;
    try {
      await api.saveProfile({
        business_name: name.value.trim(),
        is_mse: mse.input.checked,
        is_startup: startup.input.checked,
        is_oem: oem.input.checked,
        annual_turnover_rupees: numberOrNull(turnover.value),
        years_experience: numberOrNull(years.value),
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
