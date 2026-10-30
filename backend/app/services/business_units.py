"""BusinessUnit lookup/creation helpers.

Kept separate from ingestion (app/domain/skills/ingest_actuals.py,
app/services/actuals_ingest.py) deliberately: those paths only accept an
*existing* business unit (any "generate"-permission user could otherwise
spin up new companies via a typo), whereas admin-gated user/CoA management
(app/api/auth.py, app/api/admin.py) may create one on the fly.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.business_unit import BusinessUnit
from app.models.company import Company


def get_or_create_business_unit(db: Session, name: str | None) -> BusinessUnit | None:
    """Look up a BusinessUnit by name, creating it (and its 1:1 Company --
    see migration 027_company) if absent. None-safe."""
    if not name:
        return None
    bu = db.query(BusinessUnit).filter(BusinessUnit.name == name).first()
    if bu is None:
        company = db.query(Company).filter(Company.name == name).first()
        if company is None:
            company = Company(name=name)
            db.add(company)
            db.flush()
        bu = BusinessUnit(name=name, company_id=company.id)
        db.add(bu)
        db.flush()
    return bu
