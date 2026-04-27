"""
Cascade Impact Predictor.
When a truck is rerouted, compute which downstream trucks are affected.
"""


def calculate_cascade(rerouted_truck_id: str, delay_minutes: int, all_trucks: list) -> dict:
    """
    Trucks sharing the same destination as the rerouted truck will be delayed
    if there is a meaningful delay.
    """
    rerouted = next((t for t in all_trucks if t["id"] == rerouted_truck_id), None)
    if not rerouted:
        return {
            "primary_callsign": rerouted_truck_id,
            "delay_min":        delay_minutes,
            "affected":         [],
            "affected_count":   0,
            "total_delay_min":  0,
        }

    delay_hr = delay_minutes / 60
    impacts  = []

    for other in all_trucks:
        if other["id"] == rerouted_truck_id:
            continue
        if other.get("destination") == rerouted.get("destination"):
            cascade_delay = max(0, delay_hr - 0.5)   # 30-min buffer
            if cascade_delay > 0:
                impacts.append({
                    "truck_id":    other["id"],
                    "callsign":    other.get("callsign", other["id"]),
                    "destination": other.get("destination", ""),
                    "delay_min":   round(cascade_delay * 60),
                })

    return {
        "primary_callsign": rerouted.get("callsign", rerouted_truck_id),
        "delay_min":        delay_minutes,
        "affected":         impacts,
        "affected_count":   len(impacts),          # ← both keys present
        "total_delay_min":  sum(i["delay_min"] for i in impacts),
    }
