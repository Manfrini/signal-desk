"""Guard against SSRF: validate that a URL is safe to fetch server-side.

Used by every code path where a URL can come from an external actor (the
client via the local API, a scraped RSS/HTML feed, a hostile web page) and
the server then makes an outbound HTTP request to it. Without this, that
actor could make the server fetch its own loopback services, other hosts on
the local network, or cloud metadata endpoints (e.g. 169.254.169.254) — and,
for routes that echo the fetched content back (like the deep-dive endpoint),
read the response.

Blocks non-http(s) schemes and requests whose host resolves to a
loopback/private/link-local/reserved/multicast address.

``fetch_capped_text`` follows redirects itself so this check also covers
every hop that changes origin: left to ``requests``, an approved URL could
answer ``302 Location: http://169.254.169.254/`` and reach a destination the
check would have refused.

Note: this resolves the hostname once, up front, and checks the resulting
IPs. It does not defend against DNS rebinding (a hostname that re-resolves to
a different IP between this check and the actual connection) — a hard
guarantee against that would require pinning the checked IP into the
connection itself. For a single-user local app this check is a large
improvement over no validation at all; treat it as a practical mitigation,
not an absolute one.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import requests

ALLOWED_SCHEMES = ("http", "https")
MAX_RESPONSE_BYTES = 5_000_000  # 5 MB is generous for any feed/article/page this app reads
MAX_REDIRECTS = 5  # requests' own default is 30; feeds never need more than a couple
REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})


def is_safe_public_url(url: str) -> tuple[bool, str]:
    """Return (True, "") if `url` is safe to fetch server-side, else (False, reason)."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False, "URL non valido"

    scheme = parsed.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        return False, f"schema non consentito: {scheme or '(nessuno)'}"

    hostname = parsed.hostname
    if not hostname:
        return False, "host mancante nell'URL"

    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        return False, f"impossibile risolvere l'host: {exc}"
    if not addr_infos:
        return False, "impossibile risolvere l'host"

    for info in addr_infos:
        raw_ip = info[4][0]
        try:
            ip = ipaddress.ip_address(raw_ip)
        except ValueError:
            return False, f"indirizzo non valido: {raw_ip}"
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False, f"indirizzo non raggiungibile (privato/locale): {raw_ip}"

    return True, ""


def fetch_capped_text(
    url: str,
    *,
    timeout: float = 15,
    headers: dict[str, str] | None = None,
    max_bytes: int = MAX_RESPONSE_BYTES,
) -> str:
    """GET a URL like ``requests.get(...).text``, but never buffer more than
    ``max_bytes`` of response body.

    A hostile or accidentally huge response would otherwise be fully read
    into memory before any of this app's own content-length limits (e.g. the
    article excerpt cap) ever get a chance to apply — a timeout bounds how
    long a fetch can take, but not how much data it can send. Raises
    ``ValueError`` if the body exceeds the cap, so callers can fall back the
    same way they already do for any other fetch failure.

    Redirects are followed manually so that every hop after the first passes
    ``is_safe_public_url``. Left to ``requests``, a URL that cleared the
    caller's check could answer ``302 Location: http://169.254.169.254/`` and
    send the fetch somewhere the check would have refused. The first URL is
    still the caller's responsibility, as before.
    """
    response = _get_following_safe_redirects(url, timeout=timeout, headers=headers or {})
    response.raise_for_status()
    chunks: list[bytes] = []
    total = 0
    try:
        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"response body exceeds {max_bytes} bytes: {url}")
            chunks.append(chunk)
    finally:
        response.close()
    raw = b"".join(chunks)
    return raw.decode(_resolve_encoding(response, raw), errors="replace")


def _get_following_safe_redirects(
    url: str,
    *,
    timeout: float,
    headers: dict[str, str],
    max_redirects: int = MAX_REDIRECTS,
) -> "requests.Response":
    """GET ``url``, following redirects only to destinations that pass the guard."""
    current = url
    for _ in range(max_redirects + 1):
        response = requests.get(
            current, timeout=timeout, headers=headers, stream=True, allow_redirects=False
        )
        # Match on the status code rather than requests' is_redirect, which a
        # test double cannot express: an unset Mock attribute is truthy and
        # would turn every mocked response into an endless redirect.
        if getattr(response, "status_code", None) not in REDIRECT_STATUS_CODES:
            return response

        location = response.headers.get("Location", "")
        response.close()
        if not location:
            raise ValueError(f"redirect senza Location: {current}")
        target = urljoin(current, location)
        # Only a hop that leaves the current origin needs re-checking. The
        # origin we are on was already approved -- by the caller for the
        # first URL, by this loop for every hop after it -- so re-validating
        # a same-origin hop would reject nothing and would break the
        # relative Location redirects that ordinary sites serve.
        if _origin(target) != _origin(current):
            safe, reason = is_safe_public_url(target)
            if not safe:
                raise ValueError(f"redirect verso un URL non sicuro ({reason}): {target}")
        current = target

    raise ValueError(f"troppi redirect (oltre {max_redirects}): {url}")


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urlparse(url)
    return (parsed.scheme.lower(), (parsed.hostname or "").lower(), parsed.port)


def _resolve_encoding(response: "requests.Response", raw: bytes) -> str:
    """Pick the decoding charset for an already-streamed response body.

    ``response.apparent_encoding`` cannot be used here: it reads
    ``response.content``, which raises once ``iter_content`` has consumed the
    stream. That made every source whose server omits the charset (e.g.
    ``Content-Type: application/xml`` with no ``charset=``) fail permanently
    with "content for this response was already consumed" — a whole class of
    otherwise healthy feeds. Detect from the bytes we already hold instead.
    """
    if response.encoding:
        return response.encoding
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(raw).best()
        if best and best.encoding:
            return best.encoding
    except Exception:  # noqa: BLE001 - detection is best-effort, never fatal
        pass
    return "utf-8"
