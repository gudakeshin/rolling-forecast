"""Generate a synthetic P&L actuals dataset for "Carl Zeiss India" test data.

This is entirely synthetic/illustrative data for exercising the app (upload,
driver discovery, forecasting, FX conversion) -- it is NOT real Carl Zeiss
financial data. The entity name is used only to give the test dataset a
recognizable, realistic flavor (multi-segment industrial/medical/consumer
business, INR-denominated, Indian fiscal-year seasonality).

Produces two files:
  - carl_zeiss_india_actuals.csv   (account_code,account_name,category,
                                     business_unit,geography,product_line,
                                     period,value,currency)
  - carl_zeiss_india_fx_rates.csv  (from_currency,to_currency,period,rate,
                                     rate_type) -- INR -> USD, for
                                     backend/app/api/admin_fx.py's
                                     POST /admin/fx/rates/upload

36 months of history with an Indian fiscal-year (Apr-Mar) seasonal pattern,
a growth trend, a mid-series structural break (simulating a local assembly
line expansion), and noise/anomalies -- modeled after generate_seed.py.
"""

import os

import numpy as np
import pandas as pd
from datetime import datetime


def generate_carl_zeiss_india(output_dir: str | None = None, months: int = 36):
    np.random.seed(7)

    if output_dir is None:
        output_dir = os.path.dirname(__file__)

    # 36 months ending on the last complete month.
    end_date = datetime(2026, 8, 1)
    dates = pd.date_range(end=end_date, periods=months, freq="MS")
    periods = [d.strftime("%Y-%m") for d in dates]

    business_unit = "Carl Zeiss India"
    geography = "India"

    # Segments modeled on Carl Zeiss's real-world business groups, with
    # illustrative (not actual) monthly revenue bases in INR.
    segments = {
        "IQS": {
            "name": "Industrial Quality Solutions",
            "base": 55_00_00_00,  # INR 5.5 crore/month base
        },
        "MED": {
            "name": "Medical Technology",
            "base": 30_00_00_00,
        },
        "VIS": {
            "name": "Vision Care",
            "base": 20_00_00_00,
        },
        "RMS": {
            "name": "Research Microscopy Solutions",
            "base": 12_00_00_00,
        },
    }

    revenue_items = [
        ("REV-001", "Equipment & Product Revenue", "Revenue", 0.60),
        ("REV-002", "Service & AMC Revenue", "Revenue", 0.28),
        ("REV-003", "Training & Applications Revenue", "Revenue", 0.12),
    ]
    cogs_items = [
        ("COGS-001", "Imported Components & Materials", "COGS", 0.32),
        ("COGS-002", "Installation & Service Labor", "COGS", 0.14),
        ("COGS-003", "Freight, Customs & Logistics", "COGS", 0.08),
    ]
    opex_items = [
        ("OPEX-001", "Sales & Marketing", "OpEx", 0.11),
        ("OPEX-002", "Applications Engineering & R&D", "OpEx", 0.07),
        ("OPEX-003", "G&A", "OpEx", 0.05),
        ("OPEX-004", "Facilities & Rent", "OpEx", 0.03),
        ("OPEX-005", "IT & Software", "OpEx", 0.02),
    ]
    all_items = revenue_items + cogs_items + opex_items

    records = []

    for seg_code, seg in segments.items():
        rev_base = seg["base"]
        seg_name = seg["name"]

        for i, period in enumerate(periods):
            month = dates[i].month

            # Growth trend: ~11% annualized.
            growth_factor = 1 + (0.11 * i / 12)

            # Indian fiscal-year (Apr-Mar) seasonality: Q4 (Jan-Mar) capex
            # push, a new-fiscal-year (Apr) lull, and a monsoon (Jul-Aug) dip.
            seasonal = {
                1: 1.10, 2: 1.14, 3: 1.22,   # FY-end capex push
                4: 0.90,                      # new FY budget lull
                5: 0.97, 6: 1.00,
                7: 0.93, 8: 0.94,             # monsoon slowdown
                9: 1.00,
                10: 1.04, 11: 1.06,           # festive season
                12: 1.02,
            }
            season_factor = seasonal.get(month, 1.0)

            # Festive (Oct-Nov) bump is stronger for the consumer segment.
            if seg_code == "VIS" and month in (10, 11):
                season_factor *= 1.10

            # Structural break: local ("Make in India") assembly line for
            # Industrial Quality Solutions comes online at month 24.
            break_factor = 1.0
            if seg_code == "IQS" and i >= 24:
                break_factor = 1.20

            for code_suffix, name, category, pct in all_items:
                account_code = f"{seg_code}-{code_suffix}"
                account_name = f"{seg_name} - {name}"
                base_value = rev_base * pct * growth_factor * season_factor * break_factor

                noise = np.random.normal(1.0, 0.06)
                value = base_value * noise

                if np.random.random() < 0.02:
                    value *= np.random.choice([0.5, 1.6])

                records.append(
                    {
                        "account_code": account_code,
                        "account_name": account_name,
                        "category": category,
                        "business_unit": business_unit,
                        "geography": geography,
                        "product_line": seg_name,
                        "period": period,
                        "value": round(value, 2),
                        "currency": "INR",
                    }
                )

    df = pd.DataFrame(records)
    actuals_path = os.path.join(output_dir, "carl_zeiss_india_actuals.csv")
    df.to_csv(actuals_path, index=False)

    # Companion INR -> USD FX rates, one per period, gently depreciating
    # with small monthly noise -- for reporting-currency=USD setups.
    # (If the app's reporting currency is set to INR instead, no FX rates
    # are needed at all: same-currency lookups short-circuit to 1.0.)
    fx_records = []
    inr_per_usd_start, inr_per_usd_end = 83.0, 86.5
    for i, period in enumerate(periods):
        inr_per_usd = inr_per_usd_start + (inr_per_usd_end - inr_per_usd_start) * (i / (months - 1))
        inr_per_usd *= np.random.normal(1.0, 0.004)
        fx_records.append(
            {
                "from_currency": "INR",
                "to_currency": "USD",
                "period": period,
                "rate": round(1 / inr_per_usd, 6),
                "rate_type": "average",
            }
        )
    fx_df = pd.DataFrame(fx_records)
    fx_path = os.path.join(output_dir, "carl_zeiss_india_fx_rates.csv")
    fx_df.to_csv(fx_path, index=False)

    print(f"Generated {len(df)} actuals records across {months} periods, {len(segments)} segments")
    print(f"Saved actuals to: {actuals_path}")
    print(f"Saved FX rates to: {fx_path}")
    print(f"Segments: {[s['name'] for s in segments.values()]}")
    print(f"Date range: {df['period'].min()} to {df['period'].max()}")

    return df, fx_df


if __name__ == "__main__":
    generate_carl_zeiss_india()
