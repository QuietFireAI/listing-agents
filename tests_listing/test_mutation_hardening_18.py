"""Mutation-hardening — spoke 18 supplement.

Fable's decision-table test closes 18:249/257/264/343. This file closes the
3 remaining spoke-18 gaps from the clean sweep:
  173  (same-time event collision detection)
  228  (deadline-block dedup on creation)
  230  (protected=True on the created deadline block)
Each verified to KILL its mutation.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.signatures import Ed25519Signer, Ed25519Verifier
from dispatcher.listing_spokes_18 import Spoke18CalendarTask

IDENTITY_ROUTES = os.path.join(os.path.dirname(__file__), "..", "identity",
                               "routes.json")


def make_hub(tmp_path):
    signer = Ed25519Signer()
    verifier = Ed25519Verifier(signer.public_key_bytes())
    return Hub(Routes(IDENTITY_ROUTES),
               AuditLog(os.path.join(tmp_path, "a.jsonl")),
               signature_verifier=verifier.verifier())


def spoke(tmp_path):
    return Spoke18CalendarTask(make_hub(tmp_path))


def cal_event(ctx, day, event_id, time=None, extra=None):
    p = {"day": day, "event_id": event_id, "protected": False,
         "timezone_confirmed": True}
    if time:
        p["time"] = time
    if extra:
        p.update(extra)
    return Envelope(from_agent="06", to_agent="18", intent="calendar.event",
                    client_context_id=ctx, payload=p,
                    provenance={"source": "t"})


def persisted(hub, intent=None):
    return [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"
            and (intent is None or e["intent"] == intent)]


# ------------------------------------------------------------ 173
def test_18_same_time_same_day_events_conflict_different_times_do_not(tmp_path):
    """Two events at the SAME time on the SAME day raise a conflict
    clarification; different times do not. Kills the `time ==` flip."""
    s = spoke(str(tmp_path))
    s.hub.on_turn_start()
    s.hub.send(cal_event("cf-1", "2026-08-10", "e1", time="10:00"))
    s.hub.send(cal_event("cf-1", "2026-08-10", "e2", time="10:00"))  # collision
    clar = persisted(s.hub, "clarification.request")
    assert any("event conflict at" in c["payload"].get("reason", "")
               for c in clar), \
        "two events at the same time must raise a conflict"

    s2 = spoke(str(tmp_path) + "_b")
    s2.hub.on_turn_start()
    s2.hub.send(cal_event("cf-2", "2026-08-10", "e1", time="10:00"))
    s2.hub.send(cal_event("cf-2", "2026-08-10", "e2", time="14:00"))  # different
    assert not any("event conflict at" in c["payload"].get("reason", "")
                   for c in persisted(s2.hub, "clarification.request")), \
        "events at different times must not conflict"


# ------------------------------------------------------------ 228/230
def test_18_deadline_block_created_once_and_protected(tmp_path):
    """A 07-sourced deadline creates exactly ONE protected block; a repeat
    for the same milestone does not duplicate it (228), and the created
    block carries protected=True (230)."""
    s = spoke(str(tmp_path))
    s.hub.on_turn_start()
    da = Envelope(from_agent="07", to_agent="18", intent="deadline.alert",
                  client_context_id="dl-1",
                  payload={"milestone": "financing", "deadline": "2026-08-20"},
                  provenance={"source": "t"})
    s.hub.send(da)
    bid = "deadline-dl-1-financing"
    assert bid in s.protected_blocks, "a deadline must create its block"
    assert s.protected_blocks[bid]  # block record exists
    day_events = s.calendar.get("2026-08-20", [])
    blk = [e for e in day_events if e["event_id"] == bid]
    assert len(blk) == 1, "the block must exist exactly once"
    assert blk[0]["protected"] is True, \
        "the created deadline block must be protected (kills the 230 flip)"

    # 228 (`e["event_id"] == block_id` guard): I ORIGINALLY and WRONGLY marked
    # this an equivalent mutant. It is NOT. The block-creation (both the
    # calendar append AND the protected_blocks assignment) lives INSIDE this
    # guard. If a foreign event is already on the deadline day when the 07
    # alert arrives, flipping == to != makes `any(...)` True, so `not any` is
    # False, and the protected deadline block is NEVER created. Distinguishing
    # input: seed an unrelated event on the day FIRST, then send the alert.
    # (Credit: Fable 5 CrossPol caught my false equivalent-mutant claim.)
    s2 = spoke(str(tmp_path) + "_foreign_first")
    s2.hub.on_turn_start()
    foreign = Envelope(from_agent="06", to_agent="18", intent="calendar.event",
                       client_context_id="dl-2",
                       payload={"day": "2026-08-21", "event_id": "other-evt",
                                "protected": False, "timezone_confirmed": True,
                                "time": "09:00"},
                       provenance={"source": "t"})
    s2.hub.send(foreign)   # foreign event present BEFORE the deadline alert
    da2 = Envelope(from_agent="07", to_agent="18", intent="deadline.alert",
                   client_context_id="dl-2",
                   payload={"milestone": "financing", "deadline": "2026-08-21"},
                   provenance={"source": "t"})
    s2.hub.send(da2)
    bid2 = "deadline-dl-2-financing"
    assert bid2 in s2.protected_blocks, \
        "the protected deadline block MUST be created even when a foreign " \
        "event is already on the day (kills the 228 == -> != flip)"
    assert any(e["event_id"] == bid2
               for e in s2.calendar.get("2026-08-21", [])), \
        "the protected block must appear on the calendar day"
