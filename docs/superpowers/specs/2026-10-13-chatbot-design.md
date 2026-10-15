# One chatbot in front of the pipeline — design

**Date:** 2026-10-13 · **Status:** decided (owner: "hide all of this ... integrate it into a single
chatbot ... the pipeline is for us to help the chatbot with complex tasks ... shipping with groq api key")

## What changes

The app's only screen becomes a chat. Search, download, parsing, eligibility and the safety layer stay
exactly as they are; the chatbot calls them as tools. The old pages are no longer in the menu but stay
reachable from links in chat cards (full bid evidence, the Safety page).

## Decisions

| # | Decision | Why |
|---|---|---|
| D1 | Groq chat completions (`https://api.groq.com/openai/v1/chat/completions`), model `openai/gpt-oss-120b` (production, tool use, 131k context), `GROQ_MODEL` overrides. Called with httpx, already a dependency. | Owner's choice of provider; no new SDK. |
| D2 | Key from `GROQ_API_KEY` or `<data folder>/groq-api-key.txt` (0600), set with `gem-assistant set-groq-key`. Never in the repo, never sent to the browser. | A key in git or in shipped code leaks. |
| D3 | **Figures come from the pipeline, not the model.** Every tool returns data for the model *and* a card the page renders from the same data (verdict, reasons, page + quote). | Architecture rule: the LLM is never the source of truth. |
| D4 | Tools: `get_profile`, `propose_profile_update`, `search_gem`, `search_status`, `list_bids`, `get_bid`, `add_bid_from_link`, `safety_status`. **Not tools:** resetting the safety stop, setting the contact email, the Groq key, corpus/probe. | Bid text is untrusted and reaches the model. Worst case an injected clause can propose a profile change (needs his click), start a rate-limited search, or fetch a GeM document. |
| D5 | `propose_profile_update` saves nothing. The page shows the change with a Confirm button that calls the existing `PUT /api/profile`. | Human gate on the one write the model can ask for. |
| D6 | Server is stateless: the page keeps the conversation (localStorage) and sends it each turn; the server adds the system prompt, drops any client `system` message, keeps the last 40 messages, at most 6 tool rounds per turn. | No new tables; bounded cost. |
| D7 | Replies in the language he writes in (Hindi, English, Hinglish). | He is the user. |

## What leaves the machine

Everything typed in the chat and everything the tools return goes to Groq: his products, turnover,
experience, MSE status, and bid details including quoted clauses. The contact email does not
(`safety_status` says only whether it is set). This replaces the earlier "the profile never leaves the
machine" rule, by the owner's choice of a hosted model.

## Testing

Groq adapter against `httpx.MockTransport`; tools against the real container; the tool loop and the API
with a scripted fake model, including a bid whose text tells the model to change the profile (nothing
may be saved); one browser test: ask, get a reply and a card, confirm a profile proposal.

## Addendum 2026-10-14: as a website

Owner: "i wanted it to be accessible in a website, the groq api key is going to be mine, in a chat bot interface."

| # | Decision | Why |
|---|---|---|
| D8 | `serve --host 0.0.0.0` is website mode; it refuses to start without `GEM_PASSWORD` (12+ chars). Every `/api` call then needs a login: a stdlib-signed cookie (`<expiry>.<hmac>`, HttpOnly, Secure, SameSite=Strict, 30 days) whose signature covers a hash of the password, so changing it logs everyone out. A wrong password costs 1 s. The host-name allowlist gives way to the login; writes must come from the same origin. | On the open internet anyone could otherwise spend the owner's Groq key, read the seller's data and send GeM requests under his email. |
| D9 | One seller, one password. | The data model has one profile. Several sellers means per-user data: a separate change. |
| D10 | A `Dockerfile` (Python 3.12 slim, uv-locked deps, Playwright Chromium, data at `/data`, secrets only from env, exactly one copy). CI builds it, launches Chromium inside it and checks the login. | Deployable on any Docker host. |
| D11 | Deploy in India; the first post-deploy check is one real GeM search from the server. If GeM refuses data-centre addresses, run the same image at home behind Cloudflare Tunnel or Tailscale Funnel. | GeM's WAF on data-centre IPs is untested (architecture doc §4.9, §14). |
| D12 | Google sign-in (OpenID Connect code flow, stdlib + httpx): `state` + `nonce` in a 10-minute SameSite=Lax cookie; ID token taken straight from Google's token endpoint over HTTPS with the client secret, so its signature isn't checked (Google's guide) but `iss`, `aud`, `exp`, `nonce` and `email_verified` are; **only `GEM_ALLOWED_EMAILS` get in**. Password login stays optional; with Google only, `/api/login` is off. | Owner asked for Google auth. A Google account alone proves nothing about who may use the site. |
