"""Demand classification helpers for intermittent forecasting (SBC)."""

from __future__ import annotations

import numpy as np

# Syntetos-Boylan-Croston thresholds (standard ADI / CV² cutoffs)
ADI_INTERMITTENT = 1.32
CV2_ERRATIC = 0.49


def demand_classification(y: np.ndarray) -> dict[str, float | str | bool]:
    """Classify a series via ADI and CV² (Syntetos–Boylan).

    Returns keys: adi, cv2, n_nonzero, intermittent, erratic, pattern
    (smooth | intermittent | erratic | lumpy).
    """
    y = np.asarray(y, dtype=float)
    n = len(y)
    nonzero = y[y != 0]
    n_nz = int(len(nonzero))
    if n_nz == 0:
        return {
            "adi": float("inf"),
            "cv2": 0.0,
            "n_nonzero": 0,
            "intermittent": True,
            "erratic": False,
            "pattern": "zero",
        }
    adi = float(n / n_nz)
    mean_d = float(np.mean(np.abs(nonzero)))
    std_d = float(np.std(nonzero, ddof=1)) if n_nz > 1 else 0.0
    cv2 = float((std_d / (mean_d + 1e-12)) ** 2)
    intermittent = adi >= ADI_INTERMITTENT
    erratic = cv2 >= CV2_ERRATIC
    if not intermittent and not erratic:
        pattern = "smooth"
    elif intermittent and not erratic:
        pattern = "intermittent"
    elif not intermittent and erratic:
        pattern = "erratic"
    else:
        pattern = "lumpy"
    return {
        "adi": adi,
        "cv2": cv2,
        "n_nonzero": n_nz,
        "intermittent": intermittent,
        "erratic": erratic,
        "pattern": pattern,
    }
