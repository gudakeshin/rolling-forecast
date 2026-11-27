"""Contract test against a RUNNING fm-service container (stdlib only).

    python tests/contract_test.py http://127.0.0.1:8100

Checks, on a small fixed batch (with and without covariates and a group):
* every requested series comes back with the requested horizon;
* quantiles are monotone (P10 <= P50 <= P90) and finite;
* the same request twice gives identical output (deterministic inference);
* /v1/info reports the pinned model id / revision / service version.
"""

from __future__ import annotations

import json
import math
import sys
import urllib.request


def _post(base: str, path: str, body: dict) -> dict:
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())


def _get(base: str, path: str) -> dict:
    with urllib.request.urlopen(base + path, timeout=30) as r:
        return json.loads(r.read())


def _batch() -> dict:
    n = 36
    trend = [100 + 2 * t + 10 * math.sin(2 * math.pi * t / 12) for t in range(n)]
    driver = [50 + t for t in range(n)]
    return {
        "series": [
            {"id": "plain", "target": trend, "prediction_length": 12},
            {
                "id": "with_cov",
                "target": [v + 0.5 * d for v, d in zip(trend, driver)],
                "prediction_length": 6,
                "past_covariates": {"driver": driver},
                "future_covariates": {"driver": [50 + n + h for h in range(6)]},
            },
            {"id": "g_a", "target": trend[:24], "prediction_length": 3, "group_id": "grp"},
            {"id": "g_b", "target": [v * 1.1 for v in trend[:24]], "prediction_length": 3, "group_id": "grp"},
        ]
    }


def main(base: str) -> int:
    info = _get(base, "/v1/info")
    assert info["model_id"], info
    assert info["model_revision"] and info["model_revision"] != "unknown", info
    assert info["service_version"], info

    body = _batch()
    first = _post(base, "/v1/forecast", body)
    second = _post(base, "/v1/forecast", body)
    want = {s["id"]: s["prediction_length"] for s in body["series"]}
    got = {f["id"]: f for f in first["forecasts"]}
    assert set(got) == set(want), (set(got), set(want))
    for sid, h in want.items():
        f = got[sid]
        for q in ("p10", "p50", "p90"):
            assert len(f[q]) == h, (sid, q, len(f[q]))
            assert all(math.isfinite(v) for v in f[q]), (sid, q)
        for lo, mid, hi in zip(f["p10"], f["p50"], f["p90"]):
            assert lo <= mid <= hi, (sid, lo, mid, hi)
    assert first["forecasts"] == second["forecasts"], "non-deterministic output"
    assert first["model"]["model_revision"] == info["model_revision"]
    print(f"contract ok: {len(got)} series, model {info['model_id']}@{info['model_revision'][:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8100"))
