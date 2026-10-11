"""Command line entry points.

    gem-assistant serve              start the app at http://127.0.0.1:8000
    gem-assistant import FILE.pdf    analyse a downloaded bid PDF
    gem-assistant fetch LINK         download one bid document from GeM and analyse it
    gem-assistant discover           search GeM for your profile's categories (one run)
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .config import Settings
from .container import Container


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gem-assistant")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="start the local web app")
    serve.add_argument("--port", type=int, default=8000)

    imp = sub.add_parser("import", help="analyse a local bid PDF")
    imp.add_argument("pdf", type=Path)

    fetch = sub.add_parser("fetch", help="download one bid document from GeM by its link")
    fetch.add_argument("link")

    sub.add_parser("discover", help="search the GeM listing once for your categories")

    args = parser.parse_args(argv)
    container = Container.build(Settings.from_env())

    if args.command == "serve":
        import uvicorn

        from .api.app import create_app

        # 127.0.0.1 only: never reachable from other machines on the network.
        uvicorn.run(create_app(container), host="127.0.0.1", port=args.port)
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
    return 1


def _print_bid(container: Container, bid_id: int, note: str) -> None:
    item = container.bid_query.get(bid_id)
    print(f"Bid {item.bid.bid_number or '?'} → {item.assessment.verdict.value}{note}")
    for r in item.assessment.results:
        print(f"  - [{r.outcome.value}] {r.reason}")


if __name__ == "__main__":
    raise SystemExit(main())
