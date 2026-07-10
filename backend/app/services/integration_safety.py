"""SQL and URL safety checks for integration pulls."""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urljoin, urlparse

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
    if kind == "erp" and parsed.scheme not in ("http", "https"):
        raise ValueError("ERP URLs must use http or https")
    if is_private_host(host):
        raise ValueError("URL host is not allowed (private/loopback)")


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
