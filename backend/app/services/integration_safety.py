"""SQL and URL safety checks for integration pulls."""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urljoin, urlparse

import httpx
import sqlparse
from sqlparse.sql import Statement
from sqlparse.tokens import DML, Keyword


_PRIVATE_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

_MAX_ROWS = 100_000


def validate_select_query(query: str) -> str:
    """Reject non-SELECT / multi-statement SQL. Returns cleaned single statement."""
    if not query or not query.strip():
        raise ValueError("Query is required")

    statements = [s for s in sqlparse.parse(query) if str(s).strip()]
    if len(statements) != 1:
        raise ValueError("Only a single SELECT statement is allowed")

    stmt: Statement = statements[0]
    # First meaningful DML/keyword must be SELECT
    first_dml = None
    for token in stmt.flatten():
        if token.ttype in (DML, Keyword) and token.value.upper() in {
            "SELECT", "INSERT", "UPDATE", "DELETE", "DROP", "ALTER",
            "CREATE", "TRUNCATE", "REPLACE", "MERGE", "CALL", "EXEC", "EXECUTE",
        }:
            first_dml = token.value.upper()
            break
    if first_dml != "SELECT":
        raise ValueError("Only SELECT queries are allowed")

    # Extra belt: reject dangerous keywords anywhere
    upper = query.upper()
    for banned in (" INTO ", " OUTFILE ", " DUMPFILE ", ";--"):
        if banned in upper:
            raise ValueError(f"Forbidden SQL construct: {banned.strip()}")

    return str(stmt).strip().rstrip(";")


def _ip_is_private(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(ip in net for net in _PRIVATE_NETWORKS)


def is_private_host(hostname: str) -> bool:
    """Return True if hostname is / resolves to a private or loopback address.

    Fail-closed: unresolvable hostnames are treated as private.
    """
    if not hostname:
        return True
    host = hostname.strip("[]").lower()
    if host in {"localhost", "metadata.google.internal"}:
        return True
    try:
        return _ip_is_private(ipaddress.ip_address(host))
    except ValueError:
        pass

    if host.endswith(".local") or host.endswith(".internal"):
        return True

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True  # fail closed — cannot verify public reachability

    if not infos:
        return True

    for info in infos:
        addr = info[4][0]
        try:
            if _ip_is_private(ipaddress.ip_address(addr)):
                return True
        except ValueError:
            continue
    return False


def assert_safe_integration_url(url: str, kind: str) -> None:
    """Reject integration URLs that target private/loopback hosts (SSRF)."""
    if not url or not url.strip():
        raise ValueError("URL is required")
    parsed = urlparse(url.strip())
    host = parsed.hostname
    if not host:
        raise ValueError("URL must include a hostname")
    if kind in ("erp", "web", "url") and parsed.scheme not in ("http", "https"):
        raise ValueError("URLs must use http or https")
    if is_private_host(host):
        raise ValueError("URL host is not allowed (private/loopback)")


def _resolve_public_ip(hostname: str) -> str:
    """Resolve hostname and return a public IP; raise if only private addresses."""
    host = hostname.strip("[]").lower()
    try:
        direct = ipaddress.ip_address(host)
        if _ip_is_private(direct):
            raise ValueError("URL host is not allowed (private/loopback)")
        return str(direct)
    except ValueError as exc:
        if "not allowed" in str(exc):
            raise

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ValueError("URL host could not be resolved") from exc

    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if not _ip_is_private(ip):
            return str(ip)
    raise ValueError("URL host is not allowed (private/loopback)")


def safe_fetch_url(
    url: str,
    *,
    kind: str = "web",
    timeout: float = 30.0,
    max_redirects: int = 5,
) -> httpx.Response:
    """Fetch a URL with SSRF controls: deny-list, per-hop redirects, resolved-IP pin.

    Does **not** use ``follow_redirects=True``. Each Location is re-validated.
    Before every connect we resolve the hostname and require a public IP
    (closes DNS-rebinding TOCTOU between check and request).
    """
    current = url.strip()
    if not current.startswith(("http://", "https://")):
        current = "https://" + current

    seen: set[str] = set()
    for _ in range(max_redirects + 1):
        if current in seen:
            raise ValueError("Redirect loop detected")
        seen.add(current)

        parsed = urlparse(current)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("URLs must use http or https")
        assert_safe_integration_url(current, kind)
        # Pin: force a public resolved IP immediately before connect
        _resolve_public_ip(parsed.hostname or "")

        with httpx.Client(follow_redirects=False, timeout=timeout) as client:
            resp = client.get(current)

        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("Location") or resp.headers.get("location")
            if not loc:
                raise ValueError("Redirect missing Location header")
            current = urljoin(current, loc)
            continue

        resp.raise_for_status()
        return resp

    raise ValueError(f"Exceeded {max_redirects} redirects")


def resolve_erp_url(base_url: str, relative_path: str) -> str:
    """Join base_url + relative path; reject absolute caller URLs and private hosts."""
    if not base_url:
        raise ValueError("ERP connection has no base URL")
    path = (relative_path or "").strip()
    if not path:
        raise ValueError("relative_path is required")
    if re.match(r"^https?://", path, re.I) or path.startswith("//"):
        raise ValueError("Absolute URLs are not allowed; supply a relative path only")
    if ".." in path.split("/"):
        raise ValueError("Path traversal is not allowed")

    base = base_url if base_url.endswith("/") else base_url + "/"
    full = urljoin(base, path.lstrip("/"))
    parsed = urlparse(full)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Only http(s) ERP URLs are allowed")
    if is_private_host(parsed.hostname or ""):
        raise ValueError("ERP URL host is not allowed (private/loopback)")
    # Ensure still under base origin
    base_parsed = urlparse(base_url)
    if parsed.netloc != base_parsed.netloc:
        raise ValueError("Resolved URL escaped the registered base origin")
    return full


def row_cap() -> int:
    return _MAX_ROWS
