"""Unattended nightly warehouse/ERP sync for connections that opt in.

Reuses the exact adapter + persistence path the interactive `/integrations/*`
routes use (`get_actuals_provider`, `persist_pulled_actuals`), so a scheduled
pull produces the same dataset/records/audit trail and triggers the same
accuracy-snapshot matching as a manual one.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models.integration import IntegrationConnection
from app.services.actuals_ingest import persist_pulled_actuals
from app.services.audit import record_audit
from app.services.ingestion.erp_adapter import get_actuals_provider
from app.services.secret_box import decrypt_secret

logger = logging.getLogger(__name__)

SCHEDULER_ACTOR_USERNAME = "system:scheduler"


async def run_scheduled_pull(db: Session, conn: IntegrationConnection) -> dict:
    """Pull and persist actuals for one auto-pull-enabled connection.

    Raises on any failure — callers sweeping multiple connections should catch
    per-connection so one bad connection doesn't abort the sweep.
    """
    source_name = conn.default_source_name or conn.kind
    if conn.kind == "warehouse":
        if not conn.default_query:
            raise ValueError(f"connection {conn.id} has auto_pull_enabled but no default_query")
        url = decrypt_secret(conn.encrypted_url)
        provider = get_actuals_provider("warehouse", connection_url=url)
        result = await provider.pull_actuals({"query": conn.default_query, "source_name": source_name})
    elif conn.kind == "erp":
        if not conn.default_relative_path:
            raise ValueError(f"connection {conn.id} has auto_pull_enabled but no default_relative_path")
        base_url = decrypt_secret(conn.encrypted_url)
        token = decrypt_secret(conn.encrypted_token) if conn.encrypted_token else None
        provider = get_actuals_provider("erp", base_url=base_url, token=token)
        result = await provider.pull_actuals(
            {"relative_path": conn.default_relative_path, "source_name": source_name}
        )
    else:
        raise ValueError(f"connection {conn.id} has unknown kind '{conn.kind}'")

    record_audit(
        db,
        action=f"integrations.scheduled_{conn.kind}_pull",
        entity_type="integration_connection",
        entity_id=conn.id,
        actor_id=None,
        actor_username=SCHEDULER_ACTOR_USERNAME,
        details={"connection_name": conn.name, "success": result.success},
        commit=False,
    )
    return await persist_pulled_actuals(
        db, result, conn.kind, source_name,
        actor_id=None, actor_username=SCHEDULER_ACTOR_USERNAME,
        integration_connection_id=conn.id,
        business_unit_id=conn.business_unit_id,
    )


async def sync_all_enabled_connections(db: Session) -> dict:
    """Sweep every enabled, auto-pull connection. Never raises — errors are collected."""
    connections = (
        db.query(IntegrationConnection)
        .filter(
            IntegrationConnection.enabled.is_(True),
            IntegrationConnection.auto_pull_enabled.is_(True),
        )
        .all()
    )
    synced = 0
    total_rows = 0
    errors: list[dict] = []
    pulled: list[dict] = []
    for conn in connections:
        try:
            outcome = await run_scheduled_pull(db, conn)
            synced += 1
            total_rows += outcome.get("row_count", 0) or 0
            pulled.append({
                "connection_id": conn.id,
                "connection_name": conn.name,
                "dataset_id": outcome.get("dataset_id"),
                "business_unit_id": conn.business_unit_id,
            })
        except Exception as e:
            logger.exception("Scheduled pull failed for connection %s (%s)", conn.id, conn.name)
            db.rollback()
            errors.append({"connection_id": conn.id, "connection_name": conn.name, "error": str(e)})
    return {
        "connections_checked": len(connections),
        "connections_synced": synced,
        "total_rows": total_rows,
        "errors": errors,
        "pulled": pulled,
    }
