import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.listing_spokes_18 import Spoke18CalendarTask

IDENTITY_ROUTES = os.path.join(os.path.dirname(__file__), "..", "identity",
                               "routes.json")


def make_hub(tmp_path, **kw):
    audit_path = os.path.join(tmp_path, f"audit-{uuid.uuid4().hex[:8]}.jsonl")
    return Hub(Routes(IDENTITY_ROUTES), AuditLog(audit_path), **kw)


def cal_event(ctx, payload, frm="06"):
    return Envelope(from_agent=frm, to_agent="18", intent="calendar.event",
                    client_context_id=ctx, payload=payload,
                    provenance={"source": f"spoke-{frm}", "captured_at": "runtime",
                                "verbatim_available": True})


def deadline_alert(ctx, payload):
    return Envelope(from_agent="07", to_agent="18", intent="deadline.alert",
                    client_context_id=ctx, payload=payload,
                    provenance={"source": "spoke-07", "captured_at": "runtime",
                                "verbatim_available": True})


def agent_status(agent, ctx, payload):
    return Envelope(from_agent=agent, to_agent="18", intent="agent.status",
                    client_context_id=ctx, payload=payload,
                    provenance={"source": f"spoke-{agent}", "captured_at": "runtime",
                                "verbatim_available": True})


def persisted(hub, intent=None):
    events = [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"]
    return [e for e in events if intent is None or e["intent"] == intent]


def test_waiting_status_tracked_and_appears_in_briefing(tmp_path):
    hub = make_hub(str(tmp_path))
    spoke = Spoke18CalendarTask(hub)
    hub.on_turn_start()
    hub.send(agent_status("09", "cal-001", {"waiting_on": "vendor_scheduling",
                                            "since": "2026-08-01"}))
    briefing = spoke.generate_briefing()
    assert len(briefing["currently_waiting"]) == 1
    assert briefing["currently_waiting"][0]["agent"] == "09"
    assert briefing["currently_waiting"][0]["waiting_on"] == "vendor_scheduling"


def test_resolved_status_clears_from_waiting_list(tmp_path):
    hub = make_hub(str(tmp_path))
    spoke = Spoke18CalendarTask(hub)
    hub.on_turn_start()
    hub.send(agent_status("09", "cal-002", {"waiting_on": "vendor_scheduling",
                                            "since": "2026-08-01"}))
    hub.send(agent_status("09", "cal-002", {"waiting_on": "vendor_scheduling",
                                            "resolved": True}))
    briefing = spoke.generate_briefing()
    assert briefing["currently_waiting"] == []


def test_deadline_alert_creates_its_own_protected_block(tmp_path):
    """07 isn't a legal sender of calendar.event at all - the protected
    block doctrine means 18 creates its own block in response to the
    deadline.alert, not that 07 sends a calendar.event directly."""
    hub = make_hub(str(tmp_path))
    spoke = Spoke18CalendarTask(hub)
    hub.on_turn_start()
    hub.send(deadline_alert("cal-003", {"milestone": "closing", "deadline": "2026-09-10"}))
    block_id = "deadline-cal-003-closing"
    assert block_id in spoke.protected_blocks
    assert spoke.protected_blocks[block_id]["source"] == "07"


def test_unconfirmed_timezone_holds_for_clarification(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke18CalendarTask(hub)
    hub.on_turn_start()
    hub.send(cal_event("cal-004", {"day": "2026-08-10", "event_id": "ev-2"}))
    clar = persisted(hub, "clarification.request")
    assert any("timezone" in c["payload"]["reason"] for c in clar)


def test_overloaded_day_proposes_priority_never_silently_drops(tmp_path):
    hub = make_hub(str(tmp_path))
    spoke = Spoke18CalendarTask(hub, max_events_per_day=2)
    hub.on_turn_start()
    for i in range(2):
        hub.send(cal_event(f"cal-005-{i}", {"day": "2026-08-11",
                                           "event_id": f"ev-{i}",
                                           "timezone_confirmed": True}))
    hub.send(cal_event("cal-005-overflow", {"day": "2026-08-11", "event_id": "ev-overflow",
                                            "timezone_confirmed": True}))
    clar = persisted(hub, "clarification.request")
    assert any("capacity" in c["payload"]["reason"] for c in clar)
    assert "ev-overflow" not in [e["event_id"] for e in spoke.calendar["2026-08-11"]]


def test_conflicting_deadline_sources_tracks_both_never_picks(tmp_path):
    hub = make_hub(str(tmp_path))
    spoke = Spoke18CalendarTask(hub)
    hub.on_turn_start()
    hub.send(deadline_alert("cal-006", {"milestone": "closing", "deadline": "2026-09-01"}))
    hub.send(deadline_alert("cal-006", {"milestone": "closing", "deadline": "2026-09-03"}))
    assert spoke.deadline_sources["cal-006"]["closing"] == "2026-09-03"
    clar = persisted(hub, "clarification.request")
    assert any("conflicting deadline sources" in c["payload"]["reason"] for c in clar)


def test_deadline_conflicts_with_soft_calendar_item_proposes_move(tmp_path):
    hub = make_hub(str(tmp_path))
    spoke = Spoke18CalendarTask(hub)
    hub.on_turn_start()
    hub.send(cal_event("cal-007a", {"day": "2026-09-05", "event_id": "soft-1",
                                    "timezone_confirmed": True}, frm="06"))
    hub.send(deadline_alert("cal-007b", {"milestone": "title", "deadline": "2026-09-05"}))
    clar = persisted(hub, "clarification.request")
    assert any("deadline outranks" in c["payload"]["reason"] for c in clar)


def test_recurring_task_silence_surfaced(tmp_path):
    """NOTE: this seeds spoke.recurring_task_last_seen directly because
    nothing in this class ever writes to it through a real message path -
    there is no signal anywhere in the swarm for "a recurring task
    completed." This test verifies check_recurring_task()'s own logic in
    isolation, not that the mechanism works end-to-end in production -
    it doesn't; see the class docstring's tuple 8 note."""
    hub = make_hub(str(tmp_path))
    spoke = Spoke18CalendarTask(hub)
    hub.on_turn_start()
    spoke.recurring_task_last_seen["weekly_market_update"] = "2026-07-01"
    result = spoke.check_recurring_task("weekly_market_update", "2026-07-20", 7)
    assert result == "surfaced"
    clar = persisted(hub, "clarification.request")
    assert any("quiet calendar" in c["payload"]["reason"] for c in clar)


def test_briefing_never_contains_unsourced_status(tmp_path):
    """A briefing with nothing tracked must be genuinely empty, not
    fabricated - matches 'never contains a status the system cannot source.'"""
    hub = make_hub(str(tmp_path))
    spoke = Spoke18CalendarTask(hub)
    hub.on_turn_start()
    briefing = spoke.generate_briefing()
    assert briefing["currently_waiting"] == []
    assert briefing["calendar_days"] == {}


def test_INTEGRATION_vendor_and_document_waits_surface_in_briefing(tmp_path):
    """The actual point of this whole exercise: a human checking 18's
    briefing should see what's currently waiting on something, before it
    becomes a missed deadline - not just after."""
    import sys
    sys.path.insert(0, os.path.dirname(__file__) + "/..")
    from dispatcher.listing_spokes_07 import Spoke07TransactionCoordinator
    from dispatcher.listing_spokes_08 import Spoke08DocumentCollection
    from dispatcher.signatures import Ed25519Signer, Ed25519Verifier
    from dispatcher.core import Envelope as Env2

    audit_path = os.path.join(str(tmp_path), "audit-integ.jsonl")
    from dispatcher.core import Routes, AuditLog
    from dispatcher.hub import Hub as HubCls
    signer = Ed25519Signer()
    verifier = Ed25519Verifier(signer.public_key_bytes())
    hub = HubCls(Routes(IDENTITY_ROUTES), AuditLog(audit_path),
                signature_verifier=verifier.verifier())
    tc = Spoke07TransactionCoordinator(hub)
    doc = Spoke08DocumentCollection(hub)
    cal = Spoke18CalendarTask(hub)
    hub.on_turn_start()

    env = Env2(from_agent="human", to_agent="07", intent="config.update",
              client_context_id="integ-001",
              payload={"timeline_init": {"inspection": "2026-08-20"},
                      "today": "2026-08-01"},
              provenance={"source": "human", "captured_at": "runtime",
                          "verbatim_available": True})
    signer.sign(env)
    hub.send(env)

    briefing = cal.generate_briefing()
    waiting_on = {w["waiting_on"] for w in briefing["currently_waiting"]}
    assert "document:inspection" in waiting_on
    assert "vendor_scheduling:inspection" in waiting_on

    # now the document actually arrives - its wait should clear
    hub.send(Env2(from_agent="11", to_agent="08", intent="document.submission",
                  client_context_id="integ-001",
                  payload={"doc_type": "inspection", "opens_correctly": True,
                          "content_hash": "insp-x"},
                  provenance={"source": "spoke-11", "captured_at": "runtime",
                              "verbatim_available": True}))
    briefing2 = cal.generate_briefing()
    waiting_on2 = {w["waiting_on"] for w in briefing2["currently_waiting"]}
    assert "document:inspection" not in waiting_on2
    assert "vendor_scheduling:inspection" in waiting_on2  # still waiting on this one


# --------------------------------------- calendar-detected no-show (ratified)
# SKILL.md edge OUT -> 06 showing.no_show existed in routes.json with ZERO
# code behind it until 2026-07-17. These tests exercise the implementation.
def _showing(hub, ctx, iso_time):
    hub.send(cal_event(ctx, {"event": "showing", "time": iso_time,
                             "day": iso_time.split("T")[0],
                             "timezone_confirmed": True}))


def _status_from_06(ctx, waiting_on="showing_feedback", resolved=False):
    p = {"waiting_on": waiting_on}
    if resolved:
        p["resolved"] = True
    return Envelope(from_agent="06", to_agent="18", intent="agent.status",
                    client_context_id=ctx, payload=p,
                    provenance={"source": "spoke-06", "captured_at": "runtime",
                                "verbatim_available": True})


def _no_show_sends(hub):
    return [e for e in hub.audit.read()
            if e["kind"] == "envelope.persisted"
            and e["intent"] == "showing.no_show"
            and e["from_agent"] == "18" and e["to_agent"] == "06"]


def test_no_show_reported_after_grace_silence(tmp_path):
    hub = make_hub(str(tmp_path))
    spoke = Spoke18CalendarTask(hub)
    hub.register("06", lambda env: None)  # capture side not under test
    hub.on_turn_start()
    _showing(hub, "s1", "2026-07-17T10:00:00")
    assert spoke.check_showing_no_show("s1", "2026-07-17T10:30:00") == "within_grace"
    assert spoke.check_showing_no_show("s1", "2026-07-17T11:30:00") == "reported"
    sends = _no_show_sends(hub)
    assert len(sends) == 1
    assert sends[0]["payload"]["detected_by"] == "calendar"
    assert sends[0]["payload"]["is_agent_no_show"] is False
    assert sends[0]["payload"]["today"] == "2026-07-17"
    # never report the same slot twice
    assert spoke.check_showing_no_show("s1", "2026-07-17T12:30:00") == "already_reported"
    assert len(_no_show_sends(hub)) == 1


def test_no_show_suppressed_when_06_processed_an_outcome(tmp_path):
    hub = make_hub(str(tmp_path))
    spoke = Spoke18CalendarTask(hub)
    hub.on_turn_start()
    _showing(hub, "s2", "2026-07-17T10:00:00")
    hub.send(_status_from_06("s2"))  # 06 heard something post-showing
    assert spoke.check_showing_no_show("s2", "2026-07-17T13:00:00") == \
        "outcome_already_processed"
    assert _no_show_sends(hub) == []


def test_no_show_ignores_unknown_or_timeless_showings(tmp_path):
    hub = make_hub(str(tmp_path))
    spoke = Spoke18CalendarTask(hub)
    hub.on_turn_start()
    assert spoke.check_showing_no_show("nope", "2026-07-17T13:00:00") == \
        "no_showing_or_no_time"
