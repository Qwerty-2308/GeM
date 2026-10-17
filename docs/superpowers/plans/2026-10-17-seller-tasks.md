# More seller tasks (17 Oct 2026)

The owner asked to build the rows of the capability table that AI could do but the app didn't.
Rules kept from the blueprint (`../docs/00-RESEARCH-AND-ARCHITECTURE.md`): the model finds and
quotes, code validates every quote against the document, he decides (§8.1); never log in; every
GeM request goes through `SafeFetcher` (gap, daily cap, breaker, robots.txt); no pricing
recommendations (V1 "do not build").

Facts gathered first (17 Oct):
- Corrigendum replies have a fixed shape: per change "Modified On", then "Bid extended to"
  (+ "Bid Opening Date", sometimes "(Auto Extension)") or a "Download" link
  `/bidding/bid/showcorrigendumpdf/<id>/<doc_id>`. Six real samples are on disk.
- Bid PDFs since 2026 carry "Document required from seller" (e.g. "Experience Criteria, Bidder
  Turnover, Certificate (Requested in ATC)").
- The buyer's ATC file (`fulfilment.gem.gov.in/contract/slafds?...`) is public, its host has no
  robots.txt, and it is real text: the sample is 141 pages, ~33k tokens.
- Results: the listing filter "Bid / RA Awarded" and a "View Bid Results" page per bid
  (`/bidding/bid/getBidResultView/<b_id>`). Not yet checked: public? robots? fields?
- A free Groq key: 8,000 tokens a minute, 200,000 a day.

Build order, each its own commit with tests:

1. **Daily search by itself.** A thread in `serve` runs the profile's search once a day at
   `GEM_DAILY_SEARCH_AT` (default 09:00 IST; empty turns it off), with every guard of a manual
   search. A server started after that time catches up if today's has not run.
2. **Corrigendum read.** Parse each change. An extension sets the deadline he sees and reads as
   information; a corrigendum document stays a thing to check, with its link.
3. **Document checklist.** "Document required from seller" from the PDF, plus what follows from
   the bid and his profile (EMD or proof of exemption, MSE/Startup exemption proof, certificates
   asked for in the ATC), each with its page and quote.
4. **Deadline reminder.** A calendar file per bid (`deadline.ics`, alarms 2 days and 3 hours
   before): his phone's calendar reminds him. WhatsApp/SMS need a business account; not built.
5. **Excel report.** `report.csv` (UTF-8 with BOM so Excel shows ₹ and Hindi) of checked bids.
6. **Buyer's ATC file.** Link taken from the bid PDF, downloaded once per bid through
   `SafeFetcher`, text extracted, clause rules run on it; findings say "ATC page N" with a quote.
7. **AI reading with quote checks.** On request per bid: Groq reads bid + ATC pages in chunks and
   returns requirements, documents and disqualifiers, each with an exact quote and page. Code keeps
   only quotes found verbatim on that page. On request because a free key covers ~5 ATC files a day.
8. **Past results and winning prices.** Live recon first; then awarded bids for his products and
   their result pages, shown as facts with links. No suggested price.
9. **Email digest.** After the daily search, if SMTP settings are in `.env`; off otherwise.

Not built: price suggestions, anything needing his GeM login, an order tracker (later, if asked).
