"""Command line entry points.

    gem-assistant serve              start the app at http://127.0.0.1:8000 and open it
    gem-assistant import FILE.pdf    analyse a downloaded bid PDF
    gem-assistant fetch LINK         download one bid document from GeM and analyse it
    gem-assistant discover           search GeM for your profile's categories (one run)
    gem-assistant corpus KEYWORD...  collect real bid documents for parser work (one run)
    gem-assistant probe FOLDER       after corpus: do buyer attachments open without login?
    gem-assistant reanalyse          re-read stored bids with the current rules (serve does this too)
"""

from __future__ import annotations

import argparse
import threading
import webbrowser
from pathlib import Path

from .config import Settings
from .container import Container


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gem-assistant")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="start the local web app")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--no-open", action="store_true", help="don't open the browser")

    imp = sub.add_parser("import", help="analyse a local bid PDF")
    imp.add_argument("pdf", type=Path)

    fetch = sub.add_parser("fetch", help="download one bid document from GeM by its link")
    fetch.add_argument("link")

    sub.add_parser("discover", help="search the GeM listing once for your categories")

    corpus = sub.add_parser("corpus", help="collect real bid documents for parser work (one guarded run)")
    corpus.add_argument("keywords", nargs="+")
    corpus.add_argument("--limit", type=int, default=30, help="documents to download (at most 30)")

    sub.add_parser("reanalyse", help="re-read stored bids with the current rules (serve does this too)")

    probe = sub.add_parser("probe", help="after corpus: check once whether buyer attachments open without login")
    probe.add_argument("folder", type=Path)

    args = parser.parse_args(argv)
    container = Container.build(Settings.from_env())

    if args.command == "serve":
        _reanalyse(container)  # a parser update reaches existing bids before anyone looks
        import uvicorn

        from .api.app import create_app

        if not args.no_open:
            _open_browser_later(f"http://127.0.0.1:{args.port}/")
        # 127.0.0.1 only: never reachable from other machines on the network.
        uvicorn.run(create_app(container), host="127.0.0.1", port=args.port)
        return 0

    if args.command == "reanalyse":
        _reanalyse(container)
        return 0

    if args.command == "import":
        result = container.bid_import.import_pdf(args.pdf.read_bytes(), source_name=args.pdf.name)
        _print_bid(container, result.id, " (already imported)" if result.already_imported else "")
        return 0

    if args.command == "fetch":
        result = container.bid_fetch.fetch_from_link(args.link)
        note = "" if result.downloaded else " (already had it; nothing downloaded)"
        _print_bid(container, result.import_result.id, note)
        return 0

    if args.command == "discover":
        from .services.discovery_service import DiscoveryNotAllowed

        try:
            run = container.discovery.run(trigger="cli")
        except DiscoveryNotAllowed as error:
            print(f"Not started: {error}")
            return 2
        print(f"[{run.outcome}] {run.message}  (requests to GeM: {run.requests})")
        return 0 if run.outcome == "ok" else 1

    if args.command == "corpus":
        from .services.discovery_service import DiscoveryNotAllowed

        try:
            result = container.corpus.collect(args.keywords, args.limit)
        except DiscoveryNotAllowed as error:
            print(f"Not started: {error}")
            return 2
        run = result.run
        print(f"[{run.outcome}] {run.message}  (requests to GeM: {run.requests})")
        print(f"Folder: {result.folder}")
        return 0 if run.outcome == "ok" else 1

    if args.command == "probe":
        if not (args.folder / "manifest.json").is_file():
            print(f"No manifest.json in {args.folder}. Point this at a folder made by 'gem-assistant corpus'.")
            return 2
        report = container.corpus.probe(args.folder)
        for host, state in report["robots"].items():
            print(f"robots.txt {host}: {state}")
        for item in report["probes"]:
            print(f"{item['kind']}: {item['outcome']}")
        if container.safety.status().breaker.open:
            print("Fetching is now stopped. Check GeM in a normal browser, then reset it on the Safety page.")
        return 0
    return 1


def _open_browser_later(url: str) -> None:
    """Give the server a moment to start listening, then open the app."""
    threading.Timer(1.5, webbrowser.open, args=(url,)).start()


def _reanalyse(container: Container) -> None:
    report = container.bid_import.reanalyse_outdated()
    if report.updated:
        print(f"Re-read {report.updated} stored bid(s) with the updated rules.")
    if report.without_pdf:
        print(f"{report.without_pdf} bid(s) were analysed before PDFs were kept and still show the old "
              "reading. Import or fetch them again to update them.")
    if report.failed:
        print(f"{report.failed} stored PDF(s) could not be read again; their earlier reading is kept.")


def _print_bid(container: Container, bid_id: int, note: str) -> None:
    item = container.bid_query.get(bid_id)
    print(f"Bid {item.bid.bid_number or '?'} → {item.assessment.verdict.value}{note}")
    for r in item.assessment.results:
        print(f"  - [{r.outcome.value}] {r.reason}")


if __name__ == "__main__":
    raise SystemExit(main())
