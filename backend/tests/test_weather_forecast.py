"""
Unit tests for the Weather Forecast analysis logic.

Tests threshold detection for each weather type:
- High wind (> 80 km/h)
- Heavy precipitation (> 20 mm/hr)
- Heavy snowfall (> 5 cm/hr)
- Zero visibility (< 1000m)
"""

import pytest

from backend.app.services.external.weather_forecast import (
    analyze_forecast_risks,
)
from backend.app.models.risk_models import ForecastType


# ═══════════════════════════════════════════════════════════════
# Test Fixtures
# ═══════════════════════════════════════════════════════════════

def make_forecast(
    windspeed=None,
    precipitation=None,
    snowfall=None,
    visibility=None,
    hours=24,
):
    """Create a synthetic Open-Meteo response."""
    times = [f"2026-04-21T{h:02d}:00" for h in range(hours)]

    hourly = {"time": times}

    if windspeed is not None:
        hourly["windspeed_10m"] = windspeed if isinstance(windspeed, list) else [windspeed] * hours
    else:
        hourly["windspeed_10m"] = [20.0] * hours  # calm default

    if precipitation is not None:
        hourly["precipitation"] = precipitation if isinstance(precipitation, list) else [precipitation] * hours
    else:
        hourly["precipitation"] = [0.0] * hours

    if snowfall is not None:
        hourly["snowfall"] = snowfall if isinstance(snowfall, list) else [snowfall] * hours
    else:
        hourly["snowfall"] = [0.0] * hours

    if visibility is not None:
        hourly["visibility"] = visibility if isinstance(visibility, list) else [visibility] * hours
    else:
        hourly["visibility"] = [50000.0] * hours

    return {"hourly": hourly}


# ═══════════════════════════════════════════════════════════════
# Wind Tests
# ═══════════════════════════════════════════════════════════════

class TestWindAlerts:
    """Tests for high wind detection."""

    def test_high_wind_detected(self):
        """Wind > 80 km/h should trigger an alert."""
        forecast = make_forecast(windspeed=95.0)
        alerts = analyze_forecast_risks(forecast, lat=36.7, lng=-119.8)

        wind_alerts = [a for a in alerts if a.forecast_type == ForecastType.HIGH_WIND]
        assert len(wind_alerts) == 1
        assert wind_alerts[0].forecast_value == 95.0
        assert wind_alerts[0].threshold == 80.0

    def test_normal_wind_no_alert(self):
        """Wind < 80 km/h should not trigger an alert."""
        forecast = make_forecast(windspeed=40.0)
        alerts = analyze_forecast_risks(forecast, lat=36.7, lng=-119.8)

        wind_alerts = [a for a in alerts if a.forecast_type == ForecastType.HIGH_WIND]
        assert len(wind_alerts) == 0


# ═══════════════════════════════════════════════════════════════
# Precipitation Tests
# ═══════════════════════════════════════════════════════════════

class TestPrecipitationAlerts:
    """Tests for heavy precipitation detection."""

    def test_heavy_rain_detected(self):
        """Precipitation > 20 mm/hr should trigger an alert."""
        forecast = make_forecast(precipitation=25.0)
        alerts = analyze_forecast_risks(forecast, lat=36.7, lng=-119.8)

        rain_alerts = [a for a in alerts if a.forecast_type == ForecastType.HEAVY_PRECIPITATION]
        assert len(rain_alerts) == 1

    def test_light_rain_no_alert(self):
        """Precipitation < 20 mm/hr should not trigger."""
        forecast = make_forecast(precipitation=5.0)
        alerts = analyze_forecast_risks(forecast, lat=36.7, lng=-119.8)

        rain_alerts = [a for a in alerts if a.forecast_type == ForecastType.HEAVY_PRECIPITATION]
        assert len(rain_alerts) == 0


# ═══════════════════════════════════════════════════════════════
# Visibility Tests
# ═══════════════════════════════════════════════════════════════

class TestVisibilityAlerts:
    """Tests for zero visibility detection."""

    def test_zero_visibility_detected(self):
        """Visibility < 1000m should trigger an alert."""
        forecast = make_forecast(visibility=500.0)
        alerts = analyze_forecast_risks(forecast, lat=36.7, lng=-119.8)

        vis_alerts = [a for a in alerts if a.forecast_type == ForecastType.ZERO_VISIBILITY]
        assert len(vis_alerts) == 1
        assert vis_alerts[0].forecast_value == 500.0

    def test_good_visibility_no_alert(self):
        """Visibility > 1000m should not trigger."""
        forecast = make_forecast(visibility=20000.0)
        alerts = analyze_forecast_risks(forecast, lat=36.7, lng=-119.8)

        vis_alerts = [a for a in alerts if a.forecast_type == ForecastType.ZERO_VISIBILITY]
        assert len(vis_alerts) == 0


# ═══════════════════════════════════════════════════════════════
# Snow Tests
# ═══════════════════════════════════════════════════════════════

class TestSnowAlerts:
    """Tests for heavy snowfall detection."""

    def test_heavy_snow_detected(self):
        """Snowfall > 5 cm/hr should trigger an alert."""
        forecast = make_forecast(snowfall=8.0)
        alerts = analyze_forecast_risks(forecast, lat=36.7, lng=-119.8)

        snow_alerts = [a for a in alerts if a.forecast_type == ForecastType.HEAVY_SNOW]
        assert len(snow_alerts) == 1

    def test_light_snow_no_alert(self):
        """Snowfall < 5 cm/hr should not trigger."""
        forecast = make_forecast(snowfall=2.0)
        alerts = analyze_forecast_risks(forecast, lat=36.7, lng=-119.8)

        snow_alerts = [a for a in alerts if a.forecast_type == ForecastType.HEAVY_SNOW]
        assert len(snow_alerts) == 0


# ═══════════════════════════════════════════════════════════════
# Combined / Edge Case Tests
# ═══════════════════════════════════════════════════════════════

class TestForecastEdgeCases:
    """Edge case and combined scenario tests."""

    def test_multiple_alerts_different_types(self):
        """Multiple dangerous conditions should produce multiple alerts."""
        forecast = make_forecast(
            windspeed=100.0,
            precipitation=30.0,
            visibility=200.0,
        )
        alerts = analyze_forecast_risks(forecast, lat=36.7, lng=-119.8)

        types = {a.forecast_type for a in alerts}
        assert ForecastType.HIGH_WIND in types
        assert ForecastType.HEAVY_PRECIPITATION in types
        assert ForecastType.ZERO_VISIBILITY in types

    def test_empty_forecast(self):
        """Empty forecast data should return no alerts."""
        alerts = analyze_forecast_risks({"hourly": {}}, lat=36.7, lng=-119.8)
        assert len(alerts) == 0

    def test_none_values_handled(self):
        """None values in hourly data should be skipped."""
        forecast = make_forecast(windspeed=[None, None, 90.0] + [20.0] * 21)
        alerts = analyze_forecast_risks(forecast, lat=36.7, lng=-119.8)

        wind_alerts = [a for a in alerts if a.forecast_type == ForecastType.HIGH_WIND]
        assert len(wind_alerts) == 1
        assert wind_alerts[0].forecast_value == 90.0

    def test_all_calm_no_alerts(self):
        """All values within safe ranges → zero alerts."""
        forecast = make_forecast(
            windspeed=30.0,
            precipitation=2.0,
            snowfall=0.0,
            visibility=50000.0,
        )
        alerts = analyze_forecast_risks(forecast, lat=36.7, lng=-119.8)
        assert len(alerts) == 0
