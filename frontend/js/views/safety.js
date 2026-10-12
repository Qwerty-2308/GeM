import { el, notice, formatDateTime } from "../dom.js";

// GeM sees this in every request, so it can reach the seller instead of blocking him.
export function contactCard(api, current) {
  const input = el("input", { type: "text", inputmode: "email", autocomplete: "email", id: "contact-email",
    placeholder: "you@example.com", value: current || "" });
  const button = el("button", { type: "submit" }, "Save email");
  const status = el("div");
  const form = el("form", { class: "card" },
    el("label", { for: "contact-email" }, "Your email, sent to GeM with every request"),
    el("div", { class: "row" }, el("div", { style: "flex:1;min-width:240px" }, input), button),
    el("div", { class: "hint" },
      "So GeM can contact you if anything looks wrong, instead of blocking. Nothing else about you is sent."),
    status,
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    button.disabled = true;
    try {
      await api.setContactEmail(input.value);
      status.replaceChildren(notice("ok", "Saved. Searches can reach GeM now."));
    } catch (error) {
      status.replaceChildren(notice("bad", error.message));
    } finally {
      button.disabled = false;
    }
  });
  return form;
}

export async function renderSafety(container, _params, api) {
  const safety = await api.getSafety();
  const { breaker, budget } = safety;

  container.append(
    el("h1", {}, "Safety"),
    el("div", { class: "subtle" },
      "Every request to GeM goes through one guarded path. If anything looks wrong, it stops and waits for you."),
  );

  // ---- breaker ----
  const breakerCard = el("div", { class: "card" });
  if (breaker.open) {
    const confirm = el("input", { type: "checkbox", id: "confirm-reset" });
    const note = el("input", { type: "text", id: "reset-note", placeholder: "What did you check?" });
    const resetButton = el("button", { class: "danger", type: "button", disabled: true }, "Resume fetching");
    const status = el("div");
    confirm.addEventListener("change", () => { resetButton.disabled = !confirm.checked; });
    resetButton.addEventListener("click", async () => {
      resetButton.disabled = true;
      try {
        await api.resetBreaker(note.value.trim());
        location.reload();
      } catch (error) {
        status.replaceChildren(notice("bad", error.message));
        resetButton.disabled = false;
      }
    });
    breakerCard.append(
      el("div", { class: "status-big", style: "color:var(--bad)" }, "Fetching is stopped"),
      el("p", {}, `Reason: ${breaker.reason}`),
      el("p", { class: "subtle" }, `Stopped at ${formatDateTime(breaker.opened_at)}`),
      el("p", {}, "Before resuming, open GeM yourself in a normal browser and confirm it loads without a CAPTCHA or error page."),
      el("label", { class: "check", for: "confirm-reset" }, confirm, "I checked GeM manually and it works normally"),
      el("div", { style: "margin:8px 0" }, note),
      resetButton,
      status,
    );
  } else {
    breakerCard.append(
      el("div", { class: "status-big", style: "color:var(--ok)" }, "Running safely"),
      el("p", { class: "subtle" }, breaker.last_reset_at
        ? `Last resumed ${formatDateTime(breaker.last_reset_at)}${breaker.last_reset_note ? ` — “${breaker.last_reset_note}”` : ""}`
        : "Has never been stopped."),
    );
  }
  container.append(breakerCard);
  container.append(contactCard(api, safety.contact_email));

  // ---- limits ----
  container.append(
    el("div", { class: "grid-2" },
      el("div", { class: "card" },
        el("div", { class: "subtle" }, "Requests to GeM today"),
        el("div", { class: "stat" }, `${budget.used_today} / ${budget.daily_limit}`),
      ),
      el("div", { class: "card" },
        el("div", { class: "subtle" }, "Minimum gap between requests"),
        el("div", { class: "stat" }, `${safety.min_interval_seconds}s`),
      ),
    ),
  );

  // ---- rules ----
  container.append(
    el("h2", {}, "Rules enforced in code"),
    el("div", { class: "card" }, el("ul", { class: "reasons" }, safety.rules.map((r) => el("li", {}, r)))),
  );

  // ---- request log ----
  container.append(el("h2", {}, "Recent requests"));
  if (safety.recent_requests.length === 0) {
    container.append(el("div", { class: "card empty" }, "No requests have been made to GeM yet."));
    return;
  }
  container.append(
    el("div", { class: "table-wrap" },
      el("table", {},
        el("thead", {}, el("tr", {}, ...["Time", "URL", "Status", "Outcome"].map((h) => el("th", {}, h)))),
        el("tbody", {}, safety.recent_requests.map((r) =>
          el("tr", {},
            el("td", {}, formatDateTime(r.at)),
            el("td", { style: "word-break:break-all" }, r.url),
            el("td", {}, r.status ?? "—"),
            el("td", {}, r.outcome),
          ))),
      ),
    ),
  );
}
