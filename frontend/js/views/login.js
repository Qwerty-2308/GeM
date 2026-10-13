import { el, notice } from "../dom.js";

// Website mode only: the password from GEM_PASSWORD. The session is an HttpOnly cookie.
export function renderLogin(container, api) {
  const input = el("input", { type: "password", id: "password", autocomplete: "current-password" });
  const button = el("button", { type: "submit" }, "Log in");
  const status = el("div");
  const form = el("form", { class: "card login" },
    el("h1", {}, "GeM Assistant"),
    el("label", { for: "password" }, "Password"),
    input, el("div", { class: "row", style: "margin-top:12px" }, button), status);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    button.disabled = true;
    try {
      await api.login(input.value);
      location.reload();
    } catch (error) {
      status.replaceChildren(notice("bad", error.message));
      button.disabled = false;
      input.select();
    }
  });
  container.replaceChildren(form);
  input.focus();
}
