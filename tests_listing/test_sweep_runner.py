"""The clock layer: production's caller for every check_* method.
Named gap 2026-07-18 - tests called sweeps directly, production had no
caller at all, so every time-based protection was real code that never
ran."""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.signatures import Ed25519Signer, Ed25519Verifier
from dispatcher.sweep_runner import run_daily_sweeps
from dispatcher.listing_spokes import Spoke01LeadCapture, Spoke14CRMPipeline
from dispatcher.listing_spokes_16 import Spoke16AfterCloseReferral
from dispatcher.listing_spokes_18 import Spoke18CalendarTask

IDENTITY_ROUTES = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "identity", "routes.json")


def make_hub(tmp_path):
    signer = Ed25519Signer()
    verifier = Ed25519Verifier(signer.public_key_bytes())
    hub = Hub(Routes(IDENTITY_ROUTES),
              AuditLog(os.path.join(tmp_path, f"a-{uuid.uuid4().hex[:6]}.jsonl")),
              signature_verifier=verifier.verifier())
    return hub, signer


def _lead(ctx, today):
    return Envelope(from_agent="20", to_agent="01", intent="lead.signal",
                    client_context_id=ctx,
                    payload={"channel": "call", "name": "J", "phone": "5",
                             "property_interest": {"listing_id": "L1"},
                             "timeline": "30 days", "budget": 1,
                             "preapproval_status": "no", "today": today,
                             "consent": {"call": "yes", "text": "no",
                                         "email": "no"}},
                    provenance={"source": "spoke-20", "captured_at": "runtime",
                                "verbatim_available": True})


def test_sweeps_fire_from_the_runner_not_just_direct_calls(tmp_path):
    """End-to-end through the runner: a lead stuck waiting on 14 (never
    registered) must be retried then escalated by the DAILY SWEEP, and a
    showing slot that passed in silence must be reported - with nobody
    calling any check_* directly."""
    hub, _ = make_hub(str(tmp_path))
    s01 = Spoke01LeadCapture(hub)
    s18 = Spoke18CalendarTask(hub)
    hub.register("14", lambda env: None)   # swallows: response never comes
    hub.register("06", lambda env: None)
    hub.on_turn_start()
    hub.send(_lead("swp-1", "2026-07-01"))
    hub.send(Envelope(from_agent="06", to_agent="18", intent="calendar.event",
                      client_context_id="swp-s1",
                      payload={"event": "showing", "day": "2026-07-10",
                               "time": "2026-07-10T10:00:00",
                               "timezone_confirmed": True},
                      provenance={"source": "spoke-06",
                                  "captured_at": "runtime",
                                  "verbatim_available": True}))
    spokes = {"01": s01, "18": s18}

    r1 = run_daily_sweeps(hub, spokes, today="2026-07-10")
    by = {(r["agent"], r["sweep"]): r["result"] for r in r1}
    assert by[("01", "record_response_timeout")] == "retried"
    assert by[("18", "showing_no_show")] == "reported"

    r2 = run_daily_sweeps(hub, spokes, today="2026-07-20")
    by2 = {(r["agent"], r["sweep"]): r["result"] for r in r2}
    assert by2[("01", "record_response_timeout")] == "handoff.failed"
    assert by2[("18", "showing_no_show")] == "already_reported"
    assert any(e["kind"] == "sweep.completed" for e in hub.audit.read())


def test_one_broken_sweep_never_silences_the_rest(tmp_path):
    hub, _ = make_hub(str(tmp_path))
    s16 = Spoke16AfterCloseReferral(hub)
    s18 = Spoke18CalendarTask(hub)
    hub.register("06", lambda env: None)
    hub.on_turn_start()
    s16.closed_transactions["boom"] = {"close_date": "2026-07-01"}
    s16.check_post_close_milestones = None  # not callable -> sweep raises
    hub.send(Envelope(from_agent="06", to_agent="18", intent="calendar.event",
                      client_context_id="ok-1",
                      payload={"event": "showing", "day": "2026-07-10",
                               "time": "2026-07-10T10:00:00",
                               "timezone_confirmed": True},
                      provenance={"source": "spoke-06",
                                  "captured_at": "runtime",
                                  "verbatim_available": True}))
    results = run_daily_sweeps(hub, {"16": s16, "18": s18},
                               today="2026-07-10")
    by = {(r["agent"], r["sweep"]): r for r in results}
    assert by[("16", "post_close_milestones")].get("error")
    assert by[("18", "showing_no_show")]["result"] == "reported"
    assert any(e["kind"] == "sweep.error" for e in hub.audit.read()), \
        "a broken sweep must be DECLARED on the audit log, never swallowed"
