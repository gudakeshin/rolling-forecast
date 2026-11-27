"""Dashboard and analytics endpoints for side-panel views.

Split by panel; every module registers on the one ``router`` defined in
``_common``. Importing a module registers its routes, so the order
below is the route order. Names are re-exported so
``from app.api.dashboard import get_anomaly_dashboard`` (and patching
``app.api.dashboard.<name>``) keep working.
"""

from app.api.dashboard._common import router  # noqa: F401
from app.api.dashboard._common import (  # noqa: F401
    _build_driver_context,
    _extract_exog_payload,
    _preload_driver_context,
    _scoped_results_query,
    formatCurrency,
)
from app.api.dashboard.executive import (  # noqa: F401
    _executive_risk_envelope,
    get_executive_dashboard,
)
from app.api.dashboard.review import (  # noqa: F401
    AcceptAIRequest,
    BatchReviewRequest,
    REVIEW_UNDO_TTL_SECONDS,
    ReviewItemRequest,
    UndoReviewRequest,
    _ai_analyze_item,
    _create_undo_snapshot,
    _snapshot_review_state,
    accept_ai_recommendations,
    batch_review,
    get_review_dashboard,
    review_item,
    undo_review,
)
from app.api.dashboard.accuracy import (  # noqa: F401
    get_accuracy_tracking,
    get_value_metrics,
)
from app.api.dashboard.driver_inputs import (  # noqa: F401
    DriverSubmitRequest,
    get_driver_inputs,
    submit_driver_inputs,
)
from app.api.dashboard.rescore import (  # noqa: F401
    rescore_all_forecasts,
    rescore_forecasts,
)
from app.api.dashboard.overrides import (  # noqa: F401
    InlineOverrideRequest,
    inline_override,
)
from app.api.dashboard.anomalies import (  # noqa: F401
    AnomalyDismissRequest,
    dismiss_anomaly,
    get_anomaly_dashboard,
    undismiss_anomaly,
)
from app.api.dashboard.search import (  # noqa: F401
    search_line_items,
)
