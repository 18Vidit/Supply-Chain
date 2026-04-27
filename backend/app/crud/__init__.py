"""CRUD integration functions exposed to Person 2's scheduler.

All functions manage their own DB sessions — no session parameter.
"""

from .trucks import get_active_trucks
from .hazards import get_active_hazards
from .events import save_risk_event
from .forecast import save_forecast_alert

__all__ = [
    "get_active_trucks",
    "get_active_hazards",
    "save_risk_event",
    "save_forecast_alert",
]
