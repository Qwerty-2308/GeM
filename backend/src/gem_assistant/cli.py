"""Command line entry points.

    gem-assistant serve              start the app at http://127.0.0.1:8000 and open it
    gem-assistant import FILE.pdf    analyse a downloaded bid PDF
    gem-assistant fetch LINK         download one bid document from GeM and analyse it
    gem-assistant discover           search GeM for your profile's categories (one run)
    gem-assistant corpus KEYWORD...  collect real bid documents for parser work (one run)
    gem-assistant probe FOLDER       after corpus: do buyer attachments open without login?
    gem-assistant reanalyse          re-read stored bids with the current rules (serve does this too)
    gem-assistant install-app        macOS: a GeM Assistant app icon; the server runs from login
    gem-assistant set-groq-key       store the Groq API key the chat uses
"""

from __future__ import annotations

import argparse
import getpass
import os
import plistlib
import socket
import subprocess
import sys
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
    serve.add_argument("--host", default="127.0.0.1",
                       help="0.0.0.0 serves it as a website; then GEM_PASSWORD (12+ characters) is required")

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

    sub.add_parser("install-app", help="make a double-clickable GeM Assistant app in ~/Applications (macOS)")
    sub.add_parser("set-groq-key", help="store the Groq API key the chat uses (typed, not shown)")

    args = parser.parse_args(argv)
    if args.command == "install-app":
        return _install_app(Settings.from_env().data_dir)
    if args.command == "set-groq-key":
        return _set_groq_key(Settings.from_env())
    login = None
    if args.command == "serve" and args.host not in ("127.0.0.1", "localhost", "::1"):
        login = _website_login(Settings.from_env())
        if login is None:
            return 2
    if args.command == "serve" and _already_running(args.port):
        url = f"http://127.0.0.1:{args.port}/"
        print(f"GeM Assistant is already running at {url}")
        if not args.no_open:
            _open_browser_later(url)
        return 0
    container = Container.build(Settings.from_env())

    if args.command == "serve":
        _reanalyse(container)  # a parser update reaches existing bids before anyone looks
        import uvicorn

        from .api.app import create_app

        if not args.no_open:
            _open_browser_later(f"http://127.0.0.1:{args.port}/")
        # By default 127.0.0.1 only: never reachable from other machines. As a website, every
        # API call needs a login.
        uvicorn.run(create_app(container, login=login), host=args.host, port=args.port)
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


def _website_login(settings: Settings):
    """Google sign-in for listed emails, a password, or both. None (and why) if neither is set up
    completely: a website without a login would hand anyone the Groq key and his data."""
    from .api.auth import GoogleLogin, Login, load_or_create_secret

    google = None
    if settings.google_client_id or settings.google_client_secret or settings.allowed_emails:
        if not (settings.google_client_id and settings.google_client_secret and settings.allowed_emails):
            print("Google sign-in needs all three: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and GEM_ALLOWED_EMAILS "
                  "(the emails that may sign in).")
            return None
        google = GoogleLogin(settings.google_client_id, settings.google_client_secret,
                             frozenset(settings.allowed_emails))
    password = settings.password
    if password is not None and len(password) < 12:
        print("GEM_PASSWORD must be 12 or more characters.")
        return None
    if google is None and password is None:
        print("Serving it as a website needs a login: Google sign-in (GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, "
              "GEM_ALLOWED_EMAILS) or GEM_PASSWORD (12+ characters), or both.")
        return None
    return Login.build(load_or_create_secret(settings.data_dir / "session-secret.txt"), password, google)


def _already_running(port: int) -> bool:
    """Something already listens on our port: the background copy that install-app set up."""
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
        return True
    except OSError:
        return False


_AGENT = "local.gem-assistant.server"


def _install_app(data_dir: Path) -> int:
    """Two parts. The server is a Login Item of its own (a launchd agent): it starts at login and
    again after a crash, running this install's command on the data folder used now. The app icon
    only opens the page; it starts nothing, because macOS ends whatever an app started when the
    app quits."""
    if sys.platform != "darwin":
        print("install-app makes a macOS app. On other systems run: gem-assistant serve")
        return 2
    data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)  # launchd writes the log there
    log = str(data_dir / "app.log")
    agent = Path.home() / "Library" / "LaunchAgents" / f"{_AGENT}.plist"
    agent.parent.mkdir(parents=True, exist_ok=True)
    agent.write_bytes(plistlib.dumps({
        "Label": _AGENT,
        "ProgramArguments": [str(Path(sys.executable).with_name("gem-assistant")), "serve", "--no-open"],
        "EnvironmentVariables": {"GEM_DATA_DIR": str(data_dir)},
        "StandardOutPath": log, "StandardErrorPath": log,
        "RunAtLoad": True, "KeepAlive": {"SuccessfulExit": False}}))
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", f"{domain}/{_AGENT}"], capture_output=True)  # not loaded yet: fine
    subprocess.run(["launchctl", "bootstrap", domain, str(agent)], check=True)

    app = Path.home() / "Applications" / "GeM Assistant.app"
    (app / "Contents" / "MacOS").mkdir(parents=True, exist_ok=True)
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({
        "CFBundleName": "GeM Assistant", "CFBundleExecutable": "open-page",
        "CFBundleIdentifier": "local.gem-assistant", "CFBundlePackageType": "APPL"}))
    url = "http://127.0.0.1:8000/"
    launcher = app / "Contents" / "MacOS" / "open-page"
    launcher.write_text(  # right after login the server may still be starting
        "#!/bin/sh\n"
        f"i=0; while [ $i -lt 30 ] && ! curl -s -o /dev/null {url}; do sleep 1; i=$((i + 1)); done\n"
        f"open {url}\n")
    launcher.chmod(0o755)
    print(f"GeM Assistant now runs in the background from login (log: {log}).\n"
          f"Made {app}: open it from Finder or Launchpad, or drag it to the Dock.")
    return 0


def _set_groq_key(settings: Settings) -> int:
    """The key goes in the data folder, owner-only: never in the project, so never in git."""
    key = getpass.getpass("Groq API key (not shown as you type): ").strip()
    if not key:
        print("Nothing typed; the key was not changed.")
        return 2
    settings.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(settings.groq_key_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key + "\n")
    os.chmod(settings.groq_key_file, 0o600)  # in case the file already existed with wider rights
    print(f"Saved. The chat uses it from now on (stored in {settings.groq_key_file}).")
    return 0


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
