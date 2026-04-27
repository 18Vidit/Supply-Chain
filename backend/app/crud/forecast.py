from .db_models import ForecastAlert
from ..database import async_session


async def save_forecast_alert(alert: dict) -> None:
    """Integration contract for Person 2's scheduler.

    Accepts a plain dict. Self-managing session. Returns None.
    """
    allowed = {
        "truck_id", "forecast_type", "forecast_value",
        "threshold", "hours_ahead", "lat", "lng",
    }
    filtered = {k: v for k, v in alert.items() if k in allowed}

    async with async_session() as session:
        row = ForecastAlert(**filtered)
        session.add(row)
        await session.commit()
