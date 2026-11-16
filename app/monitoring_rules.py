"""Monitoring event catalog and rules (port of packages/shared/src/monitoring.ts).

Severity and wording of every event live here, so clients can't author event text.
Keep this in step with the TypeScript copy while the dashboard/mobile still import it."""

from typing import Literal

Severity = Literal["INFO", "WARNING", "CRITICAL"]

EVENT_CATALOG: dict[str, tuple[Severity, str]] = {
    "consent_accepted": ("INFO", "Candidate accepted the live monitoring notice."),
    "candidate_joined": ("INFO", "Candidate joined."),
    "candidate_left": ("WARNING", "Candidate left the session."),
    "candidate_reconnecting": ("WARNING", "Candidate connection lost, reconnecting."),
    "candidate_reconnected": ("INFO", "Candidate reconnected."),
    "interviewer_joined": ("INFO", "Interviewer joined."),
    "interviewer_left": ("INFO", "Interviewer left."),
    "camera_connected": ("INFO", "Camera connected."),
    "camera_disconnected": ("CRITICAL", "Camera disconnected."),
    "camera_permission_error": ("CRITICAL", "Candidate camera permission error."),
    "calibration_passed": ("INFO", "Screen calibration passed."),
    "monitoring_started": ("INFO", "Candidate connected to the live session."),
    "screen_detected": ("INFO", "Screen detected."),
    "screen_lost": ("CRITICAL", "Screen is no longer detected."),
    "visibility_dropped": ("WARNING", "Screen visibility dropped."),
    "positioning_corrected": ("INFO", "Screen positioning corrected."),
    "video_stopped": ("CRITICAL", "Video transmission stopped."),
    "video_resumed": ("INFO", "Video transmission resumed."),
    "network_unstable": ("WARNING", "Network connection is unstable."),
    "network_recovered": ("INFO", "Network connection recovered."),
    "app_backgrounded": ("WARNING", "Candidate app moved to the background."),
    "app_foregrounded": ("INFO", "Candidate app returned to the foreground."),
    "session_ended": ("INFO", "Session ended."),
    # Raised by the AI screen agent; message is supplied by the agent.
    "screen_app_review": ("CRITICAL", "Application on screen needs review."),
    "screen_app_changed": ("WARNING", "Application on screen changed."),
}

# Events the phone may report. Join, leave and camera events normally come from LiveKit webhooks.
CANDIDATE_REPORTABLE_EVENTS: frozenset[str] = frozenset(
    {
        "camera_permission_error",
        "calibration_passed",
        "candidate_reconnecting",
        "candidate_reconnected",
        "network_unstable",
        "network_recovered",
        "app_backgrounded",
        "app_foregrounded",
    }
)

# Events the interviewer's dashboard may report from what it observes on the stream.
INTERVIEWER_REPORTABLE_EVENTS: frozenset[str] = frozenset(
    {"video_stopped", "video_resumed", "network_unstable", "network_recovered"}
)

VISIBILITY_DROP_BELOW = 70
VISIBILITY_RECOVER_AT = 80


def derive_status_events(
    previous_detected: bool | None,
    previous_visibility: int | None,
    next_detected: bool,
    next_visibility: int,
    source: str,
) -> list[str]:
    """Timeline events implied by a status change.

    Only stream analysis produces them: during calibration the candidate is expected to
    be moving the phone around. Visibility has separate drop and recover levels so a
    value near the line doesn't flap. ``previous_*`` are None when there was no status."""
    if source != "stream_analysis":
        return []
    events: list[str] = []
    was_detected = bool(previous_detected)

    if not was_detected and next_detected:
        events.append("screen_detected")
    if was_detected and not next_detected:
        events.append("screen_lost")

    if was_detected and next_detected and previous_visibility is not None:
        if previous_visibility >= VISIBILITY_DROP_BELOW and next_visibility < VISIBILITY_DROP_BELOW:
            events.append("visibility_dropped")
        elif previous_visibility < VISIBILITY_RECOVER_AT and next_visibility >= VISIBILITY_RECOVER_AT:
            events.append("positioning_corrected")
    return events
