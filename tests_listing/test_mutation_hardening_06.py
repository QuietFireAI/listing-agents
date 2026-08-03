"""Mutation-hardening — spoke 06 (showing scheduler).

Closes the 6 spoke-06 gaps: 100, 265, 274, 292, 299, 410. Each kill-verified.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.signatures import Ed25519Signer, Ed25519Verifier
from dispatcher.listing_spokes_06 import Spoke06ShowingScheduler

IDENTITY_ROUTES = os.path.join(os.path.dirname(__file__), "..", "identity",
                               "routes.json")


def make_hub(tmp_path):
    signer = Ed25519Signer()
    verifier = Ed25519Verifier(signer.public_key_bytes())
    return Hub(Routes(IDENTITY_ROUTES),
               AuditLog(os.path.join(tmp_path, "a.jsonl")),
               signature_verifier=verifier.verifier())


def spoke(tmp_path, **kw):
    return Spoke06ShowingScheduler(make_hub(tmp_path), **kw)


def env(frm, intent, ctx, payload):
    return Envelope(from_agent=frm, to_agent="06", intent=intent,
                    client_context_id=ctx, payload=payload,
                    provenance={"source": "t"})


def persisted(hub, intent=None):
    return [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"
            and (intent is None or e["intent"] == intent)]


# ------------------------------------------------------------ 292/299
def test_06_time_conflict_within_buffer_and_unparseable_fallback(tmp_path):
    """_time_conflict: two times inside the buffer conflict; outside do not;
    an unparseable time falls back to exact-match (299), and identical
    unparseable strings therefore 'conflict' while different ones don't."""
    s = spoke(str(tmp_path))
    # 15 min apart, 30-min buffer -> conflict
    assert s._time_conflict("2026-08-10T10:00", "2026-08-10T10:15", 30) is True
    # 45 min apart, 30-min buffer -> no conflict
    assert s._time_conflict("2026-08-10T10:00", "2026-08-10T10:45", 30) is False
    # unparseable, identical -> exact-match fallback True (kills 299 flip)
    assert s._time_conflict("not-a-time", "not-a-time", 30) is True
    # unparseable, different -> exact-match fallback False
    assert s._time_conflict("not-a-time", "other-bad", 30) is False


# ------------------------------------------------------------ 274/265
def test_06_agent_no_show_pattern_flags_at_threshold(tmp_path):
    """A buyer-agent no-show increments a per-agent counter (265
    is_agent_no_show), and at the threshold a pattern clarification fires;
    below threshold it does not. Kills the >= flip and the default flip."""
    s = spoke(str(tmp_path), no_show_pattern_threshold=2)
    s.hub.on_turn_start()
    # first no-show (agent) -> counted, below threshold, no flag
    s.handle(env("11", "showing.no_show", "ns-1",
                 {"showing_agent_id": "ag-1", "is_agent_no_show": True,
                  "today": "2026-08-10"}))
    assert s.showing_agent_no_shows["ag-1"] == 1
    assert not any("no-shows" in c["payload"].get("reason", "")
                   for c in persisted(s.hub, "clarification.request")), \
        "one no-show is below threshold: no pattern flag"
    # second -> reaches threshold -> flag
    s.handle(env("11", "showing.no_show", "ns-1",
                 {"showing_agent_id": "ag-1", "is_agent_no_show": True,
                  "today": "2026-08-10"}))
    assert any("no-shows" in c["payload"].get("reason", "")
               for c in persisted(s.hub, "clarification.request")), \
        "two no-shows must trip the pattern flag (kills the >= flip)"


def test_06_non_agent_no_show_not_counted(tmp_path):
    """265: with is_agent_no_show absent/False, the agent counter is not
    incremented. Kills the default-True flip."""
    s = spoke(str(tmp_path))
    s.hub.on_turn_start()
    s.handle(env("11", "showing.no_show", "ns-2",
                 {"showing_agent_id": "ag-2", "today": "2026-08-10"}))
    assert s.showing_agent_no_shows.get("ag-2", 0) == 0, \
        "a non-agent no-show must not increment the agent counter"


# ------------------------------------------------------------ 100
def test_06_feedback_cap_stops_and_resolves(tmp_path):
    """At the feedback ask cap, request_showing_feedback stops and emits an
    agent.status resolved=True to 18. Kills the 100 resolved-flag flip."""
    s = spoke(str(tmp_path), feedback_ask_cap=2)
    s.hub.on_turn_start()
    s.feedback_asks["fb-1"] = 2  # already at cap
    result = s.request_showing_feedback("fb-1", today="2026-08-10")
    assert result == "stopped"
    statuses = [e for e in persisted(s.hub, "agent.status")
                if e["client_context_id"] == "fb-1"
                and e["payload"].get("waiting_on") == "showing_feedback"]
    assert statuses and statuses[0]["payload"]["resolved"] is True, \
        "hitting the feedback cap must resolve the wait (resolved=True)"


# ------------------------------------------------------------ 410
def test_06_scheduled_showing_marks_timezone_confirmed(tmp_path):
    """A scheduled showing sends a calendar.event to 18 with
    timezone_confirmed=True. Kills the 410 bool flip."""
    s = spoke(str(tmp_path))
    s.hub.on_turn_start()
    s._schedule("sc-1",
                {"requested_time": "2026-08-12T14:00",
                 "occupancy": "vacant", "buffer_minutes": 30,
                 "requester_identity_verified": True,
                 "timezone_confirmed": True},
                env("13", "showing.request", "sc-1", {}))
    cal = [e for e in persisted(s.hub, "calendar.event")
           if e["client_context_id"] == "sc-1"]
    assert cal, "a scheduled showing must emit a calendar.event"
    assert cal[0]["payload"]["timezone_confirmed"] is True, \
        "the calendar.event must carry timezone_confirmed=True"


# ------------------------------------------------------------ 292 (empty-string guard)
def test_06_time_conflict_empty_time_never_conflicts(tmp_path):
    """_time_conflict returns False when EITHER time string is empty/None
    (the sweep's 292 mutation flips this guard's `return False` to True).
    An empty existing time or an empty requested time must NOT be treated as
    a conflict."""
    s = spoke(str(tmp_path))
    assert s._time_conflict("", "2026-08-10T10:00", 30) is False, \
        "an empty existing time must not conflict (kills the 292 else-False flip)"
    assert s._time_conflict("2026-08-10T10:00", "", 30) is False, \
        "an empty requested time must not conflict"
    assert s._time_conflict(None, "2026-08-10T10:00", 30) is False, \
        "a None existing time must not conflict"
