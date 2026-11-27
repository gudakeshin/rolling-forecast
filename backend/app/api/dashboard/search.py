"""Line-item search for the command palette."""


import logging

from fastapi import Depends
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.database import get_db
from app.models.line_item import LineItem
from app.models.user import User
from app.services.permissions import (
    line_item_scope_filter,
)

# Scoring lives in services.confidence; remediation stays on the skill module.

logger = logging.getLogger(__name__)


from app.api.dashboard._common import router

# ──────────────────────────────────────────────────
# Line item search (⌘K command palette "jump to line item")
# ──────────────────────────────────────────────────

@router.get("/line-items/search")
async def search_line_items(
    q: str = "",
    limit: int = 10,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Small, BU-scoped line item lookup for the command palette — not a full listing."""
    q = q.strip()
    if not q:
        return {"items": []}

    capped_limit = max(1, min(limit, 25))
    query = db.query(LineItem).filter(
        (LineItem.name.ilike(f"%{q}%")) | (LineItem.account_code.ilike(f"%{q}%"))
    )
    query = line_item_scope_filter(query, current_user, LineItem)
    rows = query.order_by(LineItem.name).limit(capped_limit).all()

    return {
        "items": [
            {
                "id": li.id,
                "name": li.name,
                "account_code": li.account_code,
                "category": li.category,
                "business_unit": li.business_unit,
            }
            for li in rows
        ],
    }
