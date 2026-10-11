"""Decides which requests may be sent at all."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from .errors import UrlNotAllowed


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


class UrlPolicy:
    """GET is allowed on gem.gov.in except blocked paths. POST is allowed only to
    the exact paths listed in `post_allowed_paths` (the listing page's own search
    request) or under a prefix in `post_allowed_prefixes` (endpoints that carry an id
    in the path, e.g. the corrigendum view); everything else that would change or
    submit anything is refused."""

    def __init__(
        self,
        allowed_host_suffix: str,
        blocked_path_prefixes: tuple[str, ...],
        post_allowed_paths: tuple[str, ...] = (),
        post_allowed_prefixes: tuple[str, ...] = (),
    ):
        self._suffix = allowed_host_suffix.lower().lstrip(".")
        self._blocked = tuple(p.lower() for p in blocked_path_prefixes)
        self._post_allowed = {p.lower().rstrip("/") for p in post_allowed_paths}
        # Stored with a trailing slash so "/a/b/" never matches "/a/bc".
        self._post_prefixes = tuple(p.lower().rstrip("/") + "/" for p in post_allowed_prefixes)

    def check(self, url: str, method: str = "GET") -> None:
        method = method.upper()
        if method not in ("GET", "POST"):
            raise UrlNotAllowed(f"HTTP method {method} is not allowed")

        parts = urlsplit(url)
        if parts.scheme != "https":
            raise UrlNotAllowed(f"Only https is allowed: {url}")
        if parts.username or parts.password:
            raise UrlNotAllowed("URLs with credentials are not allowed")
        if parts.port not in (None, 443):
            raise UrlNotAllowed(f"Non-standard port is not allowed: {url}")

        host = (parts.hostname or "").lower()
        if not host:
            raise UrlNotAllowed(f"No host in URL: {url}")
        if _is_ip_literal(host):
            raise UrlNotAllowed("IP-address URLs are not allowed")
        if host != self._suffix and not host.endswith("." + self._suffix):
            raise UrlNotAllowed(f"Host {host} is not a {self._suffix} host")

        path = (parts.path or "/").lower()
        # Every path this tool uses is plain ASCII, so an encoded one is never ours. It is
        # refused rather than decoded because a server that decodes %2F or %2E sees a
        # different path than the one checked here — which would let an allowed endpoint
        # smuggle in a blocked one.
        if "%" in path:
            raise UrlNotAllowed(f"Percent-encoded paths are not allowed: {parts.path}")
        if ".." in path.split("/"):
            raise UrlNotAllowed("Path traversal segments are not allowed")
        for prefix in self._blocked:
            if path.startswith(prefix):
                raise UrlNotAllowed(f"Path {parts.path} is blocked ({prefix})")

        trimmed = path.rstrip("/")
        is_post_path = trimmed in self._post_allowed or self._matches_post_prefix(trimmed)
        if method == "POST" and not is_post_path:
            raise UrlNotAllowed(f"POST to {parts.path} is not allowed")
        if method == "GET" and is_post_path:
            raise UrlNotAllowed(f"{parts.path} is only reachable through the listing page itself")

    def _matches_post_prefix(self, trimmed_path: str) -> bool:
        """True for "<prefix>/<id>" and nothing deeper, so one allowed endpoint can't
        become a licence to POST anywhere under it."""
        for prefix in self._post_prefixes:
            if trimmed_path.startswith(prefix):
                remainder = trimmed_path[len(prefix):]
                if remainder and "/" not in remainder:
                    return True
        return False
