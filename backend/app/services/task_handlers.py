"""Handlers for app.services.background_tasks.

Each was the body of a request handler; moved here unchanged so the inline
and background paths run the same code. Params are plain JSON (they cross the
Redis queue); fast validation that should fail the request itself (404/400)
stays in the endpoint, before ``run_or_enqueue``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.services.audit import record_audit
from app.services.background_tasks import task


@task("integration_pull", "Integration pull")
async def integration_pull(db: Session, actor: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Pull actuals from a registered warehouse or ERP connection and ingest them."""
    from app.models.integration import IntegrationConnection
    from app.services.actuals_ingest import persist_pulled_actuals
    from app.services.ingestion.erp_adapter import get_actuals_provider
    from app.services.secret_box import decrypt_secret

    kind = params["kind"]
    conn = (
        db.query(IntegrationConnection)
        .filter(IntegrationConnection.id == params["connection_id"])
        .first()
    )
    if conn is None or not conn.enabled or conn.kind != kind:
        raise ValueError("Integration connection not found, disabled, or of another kind")

    try:
        url = decrypt_secret(conn.encrypted_url)
        token = decrypt_secret(conn.encrypted_token) if conn.encrypted_token else None
    except ValueError as e:
        # Server misconfiguration (key rotated/missing), not a bad request.
        raise RuntimeError(f"Stored connection secret can't be decrypted: {e}") from e

    if kind == "warehouse":
        provider = get_actuals_provider("warehouse", connection_url=url)
        request = {"query": params["query"], "source_name": params["source_name"]}
        details: dict[str, Any] = {"connection_name": conn.name}
    else:
        provider = get_actuals_provider("erp", base_url=url, token=token)
        request = {"relative_path": params["relative_path"], "source_name": params["source_name"]}
        details = {"connection_name": conn.name, "path": params["relative_path"]}

    result = await provider.pull_actuals(request)
    if not result.success and result.error and any(
        marker in result.error for marker in ("Only SELECT", "single SELECT", "Forbidden SQL")
    ):
        raise ValueError(result.error)

    record_audit(
        db,
        action=f"integrations.{kind}_pull",
        entity_type="integration_connection",
        entity_id=conn.id,
        actor_id=getattr(actor, "id", None),
        actor_username=getattr(actor, "username", None),
        details={**details, "success": result.success},
        commit=False,
    )
    return await persist_pulled_actuals(
        db, result, kind, params["source_name"],
        actor_id=getattr(actor, "id", None),
        actor_username=getattr(actor, "username", None) or "system",
        integration_connection_id=conn.id,
        business_unit_id=conn.business_unit_id,
    )


@task("document_embedding", "Document indexing")
async def document_embedding(db: Session, actor: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Extract, chunk and embed an uploaded document saved in 'processing' state."""
    from app.models.document import Document
    from app.services.context_engine import document_processor

    doc = db.query(Document).filter(Document.id == params["document_id"]).first()
    if doc is None:
        raise ValueError("Document not found")
    doc = await document_processor.process_document(db, doc, params["file_path"])
    return {"id": doc.id, "status": doc.status, "chunk_count": getattr(doc, "chunk_count", None)}


@task("board_pack_distribute", "Board pack email")
async def board_pack_distribute(db: Session, actor: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Render the board pack and email it (SMTP). Audited even when SMTP is off."""
    from app.services.board_pack import build_board_pack_pdf, build_board_pack_pptx
    from app.services.permissions import get_accessible_forecast_version

    version = get_accessible_forecast_version(db, actor, params["version_id"])
    fmt = params.get("format") or "pdf"
    if fmt == "pptx":
        data = build_board_pack_pptx(db, version)
        filename = f"{version.name}_board_pack.pptx"
    else:
        data = build_board_pack_pdf(db, version)
        filename = f"{version.name}_board_pack.pdf"

    recipients = list(params["recipients"])
    sent = False
    error = None
    if settings.smtp_host:
        try:
            import smtplib
            from email.message import EmailMessage

            msg = EmailMessage()
            msg["Subject"] = params.get("subject") or f"Forecast Board Pack: {version.name}"
            msg["From"] = settings.distribution_from_email
            msg["To"] = ", ".join(recipients)
            msg.set_content(params.get("message") or f"Attached is the board pack for {version.name}.")
            subtype = "pdf" if fmt == "pdf" else "vnd.openxmlformats-officedocument.presentationml.presentation"
            msg.add_attachment(data, maintype="application", subtype=subtype, filename=filename)

            with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as smtp:
                smtp.starttls()
                if settings.smtp_user:
                    smtp.login(settings.smtp_user, settings.smtp_password)
                smtp.send_message(msg)
            sent = True
        except Exception as e:
            error = str(e)
    else:
        error = "SMTP not configured; pack generated but not emailed"

    record_audit(
        db,
        action="executive.distribute",
        entity_type="forecast_version",
        entity_id=version.id,
        actor_id=getattr(actor, "id", None),
        actor_username=getattr(actor, "username", None),
        details={"recipients": recipients, "sent": sent, "error": error},
        commit=True,
    )
    return {"success": sent, "filename": filename, "error": error, "bytes": len(data)}


@task("rescore_all", "Re-score all forecasts")
async def rescore_all(db: Session, actor: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Re-score every version the actor can see. Versions whose pinned
    selection_rule disagrees with the live rule are skipped unless forced."""
    from app.domain.engines.model_registry import effective_selection_rule
    from app.domain.skills.generate_baseline import _generate_remediation
    from app.models.forecast import ForecastLineResult, ForecastVersion
    from app.models.line_item import LineItem
    from app.services.confidence import classify_confidence, compute_confidence_score

    force = bool(params.get("force"))
    live_rule = effective_selection_rule()
    versions = db.query(ForecastVersion).all()
    total_rescored = 0
    skipped: list[dict[str, Any]] = []
    version_ids = [v.id for v in versions]
    results_by_version: dict[str, list] = {vid: [] for vid in version_ids}
    if version_ids:
        for r in (
            db.query(ForecastLineResult)
            .join(LineItem)
            .filter(ForecastLineResult.version_id.in_(version_ids))
            .all()
        ):
            results_by_version.setdefault(r.version_id, []).append(r)

    for version in versions:
        pinned = version.selection_rule
        if pinned and pinned != live_rule and not force:
            skipped.append({"version_id": version.id, "selection_rule": pinned})
            continue
        results = results_by_version.get(version.id, [])
        high = medium = low = 0
        for r in results:
            score = compute_confidence_score(r)
            level = classify_confidence(score)
            r.confidence_score = score
            r.confidence_level = level
            if level == "high":
                high += 1
            elif level == "medium":
                medium += 1
            else:
                low += 1
            remed = _generate_remediation(r)
            r.ai_recommendation = remed["action"]
            r.ai_reasoning = remed["reason"]
            r.ai_risk_score = (
                0.0 if remed["severity"] == "info"
                else 40.0 if remed["severity"] == "warning"
                else 75.0
            )
        version.high_confidence_count = high
        version.medium_confidence_count = medium
        version.low_confidence_count = low
        total_rescored += len(results)

    record_audit(
        db,
        action="forecast.rescore_all",
        entity_type="forecast_version",
        entity_id=None,
        actor_id=getattr(actor, "id", None),
        actor_username=getattr(actor, "username", None),
        details={
            "versions_updated": len(versions) - len(skipped),
            "versions_skipped": len(skipped),
            "total_lines_rescored": total_rescored,
            "forced": force,
            "live_rule": live_rule,
            "skipped": skipped[:50],
        },
    )
    db.commit()

    return {
        "success": True,
        "message": (
            f"Re-scored {total_rescored} forecast lines across "
            f"{len(versions) - len(skipped)} versions"
            + (f" (skipped {len(skipped)} mismatched)" if skipped else "")
        ),
        "total_lines_rescored": total_rescored,
        "versions_skipped": len(skipped),
        "forced": force,
        "live_rule": live_rule,
    }


@task("reflection_run", "Heuristic reflection")
async def reflection_run(db: Session, actor: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Detect heuristic candidates from closed cycles for one company (or all)."""
    from app.services.reflection import run_reflection_pass

    return run_reflection_pass(
        db,
        actor=actor,
        business_unit_id=params.get("business_unit_id"),
        min_cycles=params["min_cycles"],
        max_candidates=params["max_candidates"],
        kinds=params.get("kinds"),
    )
