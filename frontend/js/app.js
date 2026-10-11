// Composition root for the frontend: create the API client once and hand it
// to each view. Views never construct their own dependencies.

import { ApiClient } from "./api.js";
import { Router } from "./router.js";
import { renderBidList } from "./views/bidList.js";
import { renderBidDetail } from "./views/bidDetail.js";
import { renderProfile } from "./views/profile.js";
import { renderSafety } from "./views/safety.js";
import { renderDiscover } from "./views/discover.js";

const api = new ApiClient();
const app = document.getElementById("app");

async function refreshSafetyPill() {
  const pill = document.getElementById("safety-pill");
  try {
    const safety = await api.getSafety();
    pill.className = `pill ${safety.breaker.open ? "bad" : "ok"}`;
    pill.textContent = safety.breaker.open ? "Fetching stopped" : "Safe mode on";
  } catch {
    pill.className = "pill bad";
    pill.textContent = "Backend offline";
  }
}

async function withErrors(view, container, params) {
  try {
    await view(container, params, api);
  } catch (error) {
    container.replaceChildren();
    const box = document.createElement("div");
    box.className = "notice bad";
    box.textContent = `Something went wrong: ${error.message}`;
    container.append(box);
  }
  refreshSafetyPill();
}

new Router(app)
  .register("/bids", (c, p) => withErrors(renderBidList, c, p))
  .register("/bids/:id", (c, p) => withErrors(renderBidDetail, c, p))
  .register("/discover", (c, p) => withErrors(renderDiscover, c, p))
  .register("/profile", (c, p) => withErrors(renderProfile, c, p))
  .register("/safety", (c, p) => withErrors(renderSafety, c, p))
  .resolve();
