"""Generate synthetic P&L actuals data for development and testing.

Creates 36 months of realistic financial data across 3 business units
with seasonality, trends, and a structural break.
"""

import pandas as pd
import numpy as np
from datetime import datetime
import os


def generate_seed_data(output_path: str | None = None, months: int = 36):
    """Generate synthetic P&L actuals CSV."""
    np.random.seed(42)

    if output_path is None:
        output_path = os.path.join(os.path.dirname(__file__), "sample_actuals.csv")

    # Date range: 36 months ending Dec 2025
    end_date = datetime(2025, 12, 1)
    dates = pd.date_range(end=end_date, periods=months, freq="MS")
    periods = [d.strftime("%Y-%m") for d in dates]

    # Business units
    bus = ["North America", "EMEA", "APAC"]
    bu_revenue_base = {"North America": 5_000_000, "EMEA": 3_000_000, "APAC": 1_500_000}

    records = []

    for bu in bus:
        rev_base = bu_revenue_base[bu]

        # Revenue line items
        revenue_items = [
            ("REV-001", f"{bu} - Product Revenue", "Revenue", 0.65),
            ("REV-002", f"{bu} - Service Revenue", "Revenue", 0.25),
            ("REV-003", f"{bu} - Subscription Revenue", "Revenue", 0.10),
        ]

        # COGS items
        cogs_items = [
            ("COGS-001", f"{bu} - Material Costs", "COGS", 0.35),
            ("COGS-002", f"{bu} - Labor Costs", "COGS", 0.15),
            ("COGS-003", f"{bu} - Fulfillment", "COGS", 0.10),
        ]

        # OpEx items
        opex_items = [
            ("OPEX-001", f"{bu} - Sales & Marketing", "OpEx", 0.12),
            ("OPEX-002", f"{bu} - R&D", "OpEx", 0.08),
            ("OPEX-003", f"{bu} - G&A", "OpEx", 0.05),
            ("OPEX-004", f"{bu} - Rent & Facilities", "OpEx", 0.03),
            ("OPEX-005", f"{bu} - IT & Software", "OpEx", 0.02),
        ]

        all_items = revenue_items + cogs_items + opex_items

        for i, period in enumerate(periods):
            # Growth trend: 5-8% annual growth
            growth_factor = 1 + (0.06 * i / 12)

            # Seasonality: Q4 spike (15%), Q1 dip (-5%)
            month = dates[i].month
            seasonal = {
                1: 0.95, 2: 0.97, 3: 1.00,
                4: 1.02, 5: 1.03, 6: 1.01,
                7: 0.98, 8: 0.97, 9: 1.02,
                10: 1.05, 11: 1.08, 12: 1.15,
            }
            season_factor = seasonal.get(month, 1.0)

            # Structural break at month 18 (simulating an acquisition)
            break_factor = 1.0
            if i >= 18 and bu == "APAC":
                break_factor = 1.3  # APAC grows 30% after acquisition

            for code_suffix, name, category, pct in all_items:
                account_code = f"{bu[:2].upper()}-{code_suffix}"
                base_value = rev_base * pct * growth_factor * season_factor * break_factor

                # Add noise (5-10%)
                noise = np.random.normal(1.0, 0.07)
                value = base_value * noise

                # Occasional anomaly (2% chance)
                if np.random.random() < 0.02:
                    value *= np.random.choice([0.5, 1.5])  # 50% drop or 50% spike

                records.append({
                    "account_code": account_code,
                    "account_name": name,
                    "category": category,
                    "business_unit": bu,
                    "geography": bu,
                    "product_line": "All Products",
                    "period": period,
                    "value": round(value, 2),
                    "currency": "USD",
                })

    df = pd.DataFrame(records)

    # Save
    df.to_csv(output_path, index=False)
    print(f"Generated {len(df)} records across {months} periods, {len(bus)} BUs")
    print(f"Saved to: {output_path}")
    print(f"Unique accounts: {df['account_code'].nunique()}")
    print(f"Categories: {df['category'].unique().tolist()}")
    print(f"Date range: {df['period'].min()} to {df['period'].max()}")

    return df


if __name__ == "__main__":
    generate_seed_data()
