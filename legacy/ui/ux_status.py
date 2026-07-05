from __future__ import annotations


def classify_latency_ms(latency_ms: float | None) -> tuple[str, str]:
    """
    Return a user-facing latency quality label and color.
    """
    if latency_ms is None:
        return ("Latency: n/a (server unreachable or probe timed out)", "#999999")
    if latency_ms < 30.0:
        return (f"Latency: {latency_ms:.0f} ms (Good)", "#00cc66")
    if latency_ms < 70.0:
        return (f"Latency: {latency_ms:.0f} ms (Fair)", "#ffcc00")
    return (f"Latency: {latency_ms:.0f} ms (Poor)", "#ff5555")


def readiness_state(participant_count: int, placeholder_count: int = 0) -> tuple[str, str]:
    try:
        total = max(0, int(participant_count))
    except (TypeError, ValueError):
        total = 0
    try:
        placeholders = max(0, int(placeholder_count))
    except (TypeError, ValueError):
        placeholders = 0
    if total - placeholders > 0:
        return ("Room: ready", "#00cc66")
    return ("Room: waiting for participants", "#ffcc00")


def connection_summary(jamulus_state: str, webex_state: str) -> str:
    return f"Jamulus: {jamulus_state} | Webex: {webex_state}"
