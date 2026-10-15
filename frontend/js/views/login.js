import { el, notice } from "../dom.js";

// Codes only, never text from the address bar: a crafted link can't put words on this page.
const REFUSALS = {
  not_allowed: "That Google account isn't allowed here. Ask the owner to add its email.",
  failed: "Google sign-in didn't complete. Try again.",
};

// Website mode only: Google sign-in for listed emails, a password, or both. The session is an
// HttpOnly cookie.
export function renderLogin(container, api, session) {
  const status = el("div");
  const refusal = REFUSALS[new URLSearchParams(location.search).get("login_error")];
  if (refusal) {
    status.append(notice("bad", refusal));
    history.replaceState(null, "", location.pathname);  // don't show it again on reload
  }
  const card = el("div", { class: "card login" }, el("h1", {}, "GeM Assistant"));

  if (session.methods.includes("google")) {
    card.append(el("a", { class: "button", href: "/api/auth/google" }, "Sign in with Google"));
  }
  if (session.methods.includes("password")) {
    const input = el("input", { type: "password", id: "password", autocomplete: "current-password" });
    const button = el("button", { type: "submit" }, "Log in");
    const form = el("form", {}, el("label", { for: "password" }, "Password"), input,
      el("div", { class: "row", style: "margin-top:12px" }, button));
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
    card.append(form);
  }
  card.append(status);
  container.replaceChildren(card);
}
