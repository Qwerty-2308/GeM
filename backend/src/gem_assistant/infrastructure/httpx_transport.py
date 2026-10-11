"""httpx adapter for HttpTransport. Only SafeFetcher may use this."""

from __future__ import annotations

import httpx

from ..domain.ports import HttpResponse


class ResponseTooLarge(Exception):
    pass


class HttpxTransport:
    def __init__(self, timeout_seconds: float):
        # No redirects (a redirect is treated as a warning sign), no cookies kept,
        # no retries, one connection.
        self._client = httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=False,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
        )

    def get(self, url: str, headers: dict[str, str], max_bytes: int) -> HttpResponse:
        with self._client.stream("GET", url, headers=headers) as response:
            declared = response.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise ResponseTooLarge(f"Declared size {declared} exceeds {max_bytes}")
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > max_bytes:  # enforced while downloading, not after
                    raise ResponseTooLarge(f"Response exceeded {max_bytes} bytes")
            self._client.cookies.clear()
            return HttpResponse(
                url=str(response.url),
                status=response.status_code,
                headers={k.lower(): v for k, v in response.headers.items()},
                body=bytes(body),
            )

    def close(self) -> None:
        self._client.close()
