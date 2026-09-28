from __future__ import annotations

"""CompanyMembership -- the grant list behind real multi-company users.

A User's business_unit_id/role_id (app/models/user.py) are "the currently
active company + role," unchanged from before this model existed.
CompanyMembership is the separate set of companies a user MAY switch into,
and what role they hold in each -- see app/services/company_membership.py
for the invariant that a user's active company always has a matching row
here, and for the switch itself (a plain DB write, since every permission
check reads User.role/.business_unit_id live -- see
app/api/auth.py:get_current_user).

can_view_all_bus (app/models/user.py Role) is untouched and remains a
separate all-company bypass for admins; this is for everyone else, a
bounded, named set of companies instead of "all or exactly one."
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CompanyMembership(Base):
    """One (user, company) grant: the role that user holds in that company."""

    __tablename__ = "company_memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "company_id", name="uq_company_memberships_user_company"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
