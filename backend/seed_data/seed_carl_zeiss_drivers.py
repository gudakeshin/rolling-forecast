"""Seed quantity/unit-price driver links for the Carl Zeiss India revenue lines.

Run this AFTER `carl_zeiss_india_actuals.csv` (see generate_carl_zeiss_india.py)
has been ingested for a company -- ingestion is what creates the `LineItem`
rows this script attaches drivers to.

Without this, `backend/app/services/variance_attribution.py`'s "honest ladder"
has no `DriverLink` with `relation="quantity"` to work with, so `_line_qp_points`
always returns None and every bridge falls through to the unattributed
`method: "none"` rung. This script makes the top rung (`identity_qp`, real
Volume/Price/Mix decomposition) reachable for the major revenue lines by:

  1. Synthesizing a plausible physical quantity (units) series per revenue
     line item.
  2. Deriving unit_price = value / quantity for the *same* periods already
     ingested as actuals, so Q * P == L holds exactly and passes
     `qp_coherence()` (driver_series.py) -- this is a coherence requirement,
     not a modeling choice.
  3. Writing both series as `Driver` + `DriverValue` (value_type="actual")
     rows, and linking them to the line item with two active `DriverLink`
     rows (relation="quantity" / "unit_price") sharing a composition_group.

No `LineItemDependency` rollup rows are created: this seed CSV only has leaf
accounts (e.g. "IQS-REV-001"), no rollup line item like "Total Revenue" for
`ensure_standard_dependencies` (coa_dependencies.py) to attach to. Leaf-line
attribution is what Explain Variance shows for these accounts either way.
"""

import argparse
import sys

import numpy as np

from app.database import SessionLocal
from app.models.actuals import ActualsRecord
from app.models.business_unit import BusinessUnit
from app.models.driver import Driver, DriverLink, DriverValue
from app.models.line_item import LineItem

# The revenue accounts seeded by generate_carl_zeiss_india.py, across all
# four segments -- these are the "major revenue lines" the plan calls out.
REVENUE_SUFFIXES = ["REV-001", "REV-002", "REV-003"]
SEGMENTS = ["IQS", "MED", "VIS", "RMS"]
REVENUE_ACCOUNT_CODES = [f"{seg}-{suf}" for seg in SEGMENTS for suf in REVENUE_SUFFIXES]


def _quantity_series(account_code: str, periods: list[str], values: list[float]) -> list[float]:
    """A plausible unit-volume series: trending with the revenue line but with
    its own independent noise, so price isn't just a constant divided out."""
    rng = np.random.RandomState(abs(hash(account_code)) % (2**32))
    n = len(periods)
    # Base unit count scaled off the first period's revenue so quantities
    # land in a readable range (hundreds-to-thousands of units), independent
    # of each line's absolute INR value.
    base_units = max(values[0] / 50_000.0, 10.0)
    trend = 1 + 0.06 * np.arange(n) / 12  # mild independent unit-volume growth
    noise = rng.normal(1.0, 0.05, size=n)
    return list(np.round(base_units * trend * noise, 1))


def seed_drivers_for_business_unit(db, business_unit: BusinessUnit) -> int:
    line_items = (
        db.query(LineItem)
        .filter(
            LineItem.business_unit_id == business_unit.id,
            LineItem.account_code.in_(REVENUE_ACCOUNT_CODES),
        )
        .all()
    )
    if not line_items:
        print(
            f"No matching revenue line items found for business unit "
            f"'{business_unit.name}' -- ingest carl_zeiss_india_actuals.csv for "
            f"this company first."
        )
        return 0

    links_created = 0
    for li in line_items:
        already_linked = (
            db.query(DriverLink)
            .filter(
                DriverLink.line_item_id == li.id,
                DriverLink.relation == "quantity",
                DriverLink.status == "active",
            )
            .first()
        )
        if already_linked is not None:
            print(f"Skipping {li.account_code}: already has an active quantity driver link.")
            continue

        records = (
            db.query(ActualsRecord)
            .filter(ActualsRecord.line_item_id == li.id)
            .order_by(ActualsRecord.period)
            .all()
        )
        if len(records) < 2:
            continue
        periods = [r.period for r in records]
        values = [r.value for r in records]
        quantities = _quantity_series(li.account_code, periods, values)
        prices = [v / q for v, q in zip(values, quantities)]

        # Driver.key is globally unique (not BU-scoped) -- prefix with the
        # business unit id so seeding this for more than one company can't
        # collide on the same account_code.
        key_prefix = f"{business_unit.id}_{li.account_code}"
        qty_driver = Driver(
            key=f"{key_prefix}_qty",
            name=f"{li.name} - Units",
            driver_type="volume",
            unit="units",
            aggregation="sum",
            business_unit_id=business_unit.id,
        )
        price_driver = Driver(
            key=f"{key_prefix}_price",
            name=f"{li.name} - Unit Price",
            driver_type="price",
            currency="INR",
            aggregation="average",
            business_unit_id=business_unit.id,
        )
        db.add(qty_driver)
        db.add(price_driver)
        db.flush()

        for period, qty, price in zip(periods, quantities, prices):
            db.add(DriverValue(driver_id=qty_driver.id, period=period, value=qty, value_type="actual"))
            db.add(DriverValue(driver_id=price_driver.id, period=period, value=price, value_type="actual"))

        composition_group = f"{li.account_code}_qp"
        db.add(
            DriverLink(
                driver_id=qty_driver.id,
                line_item_id=li.id,
                link_type="manual",
                relation="quantity",
                status="active",
                composition_group=composition_group,
                notes="Seeded synthetic unit-volume series for demo/seed data.",
            )
        )
        db.add(
            DriverLink(
                driver_id=price_driver.id,
                line_item_id=li.id,
                link_type="manual",
                relation="unit_price",
                status="active",
                composition_group=composition_group,
                notes="Derived as value/quantity so Q*P reproduces actuals exactly.",
            )
        )
        links_created += 1
        print(f"Seeded quantity/price drivers for {li.account_code} ({li.name})")

    db.commit()
    return links_created


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--business-unit",
        required=True,
        help="Name or id of the company whose Carl Zeiss India actuals were ingested.",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        bu = (
            db.query(BusinessUnit)
            .filter((BusinessUnit.id == args.business_unit) | (BusinessUnit.name == args.business_unit))
            .first()
        )
        if bu is None:
            print(f"Business unit '{args.business_unit}' not found.", file=sys.stderr)
            return 1
        count = seed_drivers_for_business_unit(db, bu)
        print(f"Done: {count} revenue line(s) now have active quantity/unit_price driver links.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
