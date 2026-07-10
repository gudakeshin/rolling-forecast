"""SQL and URL safety checks for integration pulls."""

from __future__ import annotations

import ipaddress
import re
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


def is_private_host(hostname: str) -> bool:
    """Return True if hostname resolves to / is a private or loopback address."""
    if not hostname:
        return True
    host = hostname.strip("[]").lower()
    if host in {"localhost", "metadata.google.internal"}:
        return True
    try:
        ip = ipaddress.ip_address(host)
        return any(ip in net for net in _PRIVATE_NETWORKS)
    except ValueError:
        # Hostname — block obvious internal names; DNS rebinding is Phase 4 concern
        if host.endswith(".local") or host.endswith(".internal"):
            return True
        return False


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
