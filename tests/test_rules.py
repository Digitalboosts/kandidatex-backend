from app.ids import generate_interview_id, normalize_interview_id, normalize_name
from app.monitoring_rules import EVENT_CATALOG, derive_status_events


def test_interview_id_roundtrip_and_look_alike_letters():
    interview_id = generate_interview_id()
    assert normalize_interview_id(interview_id.lower().replace("-", " ")) == interview_id
    assert normalize_interview_id("oooo-iiii-llll") == "0000-1111-1111"


def test_normalize_name():
    assert normalize_name("  Sam   CANDIDATE ") == "sam candidate"


def test_status_events():
    f = derive_status_events
    assert f(None, None, True, 100, "stream_analysis") == ["screen_detected"]
    assert f(True, 100, False, 0, "stream_analysis") == ["screen_lost"]
    assert f(True, 100, True, 60, "stream_analysis") == ["visibility_dropped"]
    assert f(True, 60, True, 85, "stream_analysis") == ["positioning_corrected"]
    assert f(True, 75, True, 72, "stream_analysis") == []  # hysteresis: no flapping near the line
    assert f(None, None, True, 100, "calibration") == []


def test_every_event_has_severity_and_message():
    assert len(EVENT_CATALOG) == 25
    assert all(severity in ("INFO", "WARNING", "CRITICAL") and message for severity, message in EVENT_CATALOG.values())
