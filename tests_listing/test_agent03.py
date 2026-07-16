"""Pressure test for listing Agent 03 (Lead Nurture).

Two classes of test here, deliberately distinguished:
  - FULL HUB tests: lead.nurture, content.verdict, data.package - these have
    real routes in identity/routes.json and go through hub.send() end to end.
  - LOGIC-ONLY tests: lead.reply, behavioral.signal - NO ROUTE EXISTS for
    these anywhere in the ratified identity (verified directly - checked
    every agent's SKILL.md and routes.json). Sending them via hub.send()
    would just get held in clarification.request, never reaching this
    spoke's handler. These tests call spoke.handle() directly to prove the
    business logic is correct, clearly marked as pending a real routing
    decision - not claiming these are wired end to end, because they are not.
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, "/home/claude/pillars_pth")

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.listing_spokes_03 import Spoke03LeadNurture

IDENTITY_ROUTES = os.path.join(os.path.dirname(__file__), "..", "identity",
                               "routes.json")


def make_hub(tmp_path, **kw):
    audit_path = os.path.join(tmp_path, f"audit-{uuid.uuid4().hex[:8]}.jsonl")
    return Hub(Routes(IDENTITY_ROUTES), AuditLog(audit_path), **kw)


def nurture_env(ctx, payload):
    return Envelope(from_agent="02", to_agent="03", intent="lead.nurture",
                    client_context_id=ctx, payload=payload,
                    provenance={"source": "spoke-02", "captured_at": "runtime",
                                "verbatim_available": True})


def persisted(hub, intent=None):
    events = [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"]
    return [e for e in events if intent is None or e["intent"] == intent]


# ------------------------------------------------------------ full hub path
def test_no_consent_on_any_channel_escalates_no_send(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke03LeadNurture(hub)
    hub.on_turn_start()

    hub.send(nurture_env("n-001", {"consent": {"call": "no", "text": "no", "email": "no"}}))
    assert hub.queues["escalation.legal_line"]


def test_consent_present_starts_sequence(tmp_path):
    hub = make_hub(str(tmp_path))
    spoke = Spoke03LeadNurture(hub)
    hub.on_turn_start()

    hub.send(nurture_env("n-002", {"consent": {"email": "yes"}, "sequence_id": "market_drip"}))
    assert spoke.active_sequences["n-002"]["sequence_id"] == "market_drip"
    assert spoke.active_sequences["n-002"]["paused"] is False


def test_overlapping_sequence_runs_neither_until_human_picks(tmp_path):
    hub = make_hub(str(tmp_path))
    spoke = Spoke03LeadNurture(hub)
    hub.on_turn_start()

    hub.send(nurture_env("n-003", {"consent": {"email": "yes"}, "sequence_id": "seq_a"}))
    hub.send(nurture_env("n-003", {"consent": {"email": "yes"}, "sequence_id": "seq_b"}))

    clar = persisted(hub, "clarification.request")
    assert any(c["payload"]["reason"] == "overlapping sequences" for c in clar)
    assert spoke.active_sequences["n-003"]["paused"] is True


def test_content_review_submitted_before_any_send(tmp_path):
    hub = make_hub(str(tmp_path))
    spoke = Spoke03LeadNurture(hub)
    hub.on_turn_start()
    hub.send(nurture_env("n-004", {"consent": {"email": "yes"}, "sequence_id": "seq_a"}))
    assert spoke.active_sequences["n-004"]["compliance_status"] == "pending"
    reviews = persisted(hub, "content.review")
    assert reviews and reviews[0]["to_agent"] == "17"


def test_content_verdict_flagged_holds_sequence(tmp_path):
    hub = make_hub(str(tmp_path))
    spoke = Spoke03LeadNurture(hub)
    hub.on_turn_start()
    hub.send(nurture_env("n-004b", {"consent": {"email": "yes"}, "sequence_id": "seq_a"}))
    env = Envelope(from_agent="17", to_agent="03", intent="content.verdict",
                  client_context_id="n-004b",
                  payload={"verdict": "flagged", "findings": [{"phrase": "guaranteed"}]},
                  provenance={"source": "spoke-17", "captured_at": "runtime",
                              "verbatim_available": True})
    hub.send(env)
    assert spoke.active_sequences["n-004b"]["compliance_status"] == "pending"
    assert spoke.active_sequences["n-004b"]["paused"] is True


def test_content_verdict_approved_clears_sequence(tmp_path):
    hub = make_hub(str(tmp_path))
    spoke = Spoke03LeadNurture(hub)
    hub.on_turn_start()
    hub.send(nurture_env("n-004c", {"consent": {"email": "yes"}, "sequence_id": "seq_a"}))
    env = Envelope(from_agent="17", to_agent="03", intent="content.verdict",
                  client_context_id="n-004c", payload={"verdict": "approved"},
                  provenance={"source": "spoke-17", "captured_at": "runtime",
                              "verbatim_available": True})
    hub.send(env)
    assert spoke.active_sequences["n-004c"]["compliance_status"] == "cleared"


def test_market_update_requests_data_only_when_cleared(tmp_path):
    hub = make_hub(str(tmp_path))
    spoke = Spoke03LeadNurture(hub)
    hub.on_turn_start()
    hub.send(nurture_env("n-004d", {"consent": {"email": "yes"}, "sequence_id": "seq_a"}))

    # not yet cleared - must not request data
    assert spoke.request_market_update("n-004d") is None
    assert not persisted(hub, "data.request")

    env = Envelope(from_agent="17", to_agent="03", intent="content.verdict",
                  client_context_id="n-004d", payload={"verdict": "approved"},
                  provenance={"source": "spoke-17", "captured_at": "runtime",
                              "verbatim_available": True})
    hub.send(env)
    assert spoke.request_market_update("n-004d") is True
    dr = persisted(hub, "data.request")
    assert dr and dr[0]["to_agent"] == "10"


def test_expired_market_data_skips_touch(tmp_path):
    hub = make_hub(str(tmp_path))
    spoke = Spoke03LeadNurture(hub)
    hub.on_turn_start()
    env = Envelope(from_agent="10", to_agent="03", intent="data.package",
                  client_context_id="n-005",
                  payload={"expired": True},
                  provenance={"source": "spoke-10", "captured_at": "runtime",
                              "verbatim_available": True})
    hub.send(env)
    events = hub.audit.read()
    trace = [e for e in events if e["kind"] == "spoke.trace"][-1]
    assert "expired" in trace["thought"]


def test_stale_listing_status_pulls_step(tmp_path):
    hub = make_hub(str(tmp_path))
    spoke = Spoke03LeadNurture(hub)
    hub.on_turn_start()
    env = Envelope(from_agent="10", to_agent="03", intent="data.package",
                  client_context_id="n-006",
                  payload={"listing_status": "changed"},
                  provenance={"source": "spoke-10", "captured_at": "runtime",
                              "verbatim_available": True})
    hub.send(env)
    events = hub.audit.read()
    trace = [e for e in events if e["kind"] == "spoke.trace"][-1]
    assert "fabrications" in trace["thought"]


# --------------------------------------------------- confirm the gap is closed
def test_lead_reply_now_reaches_agent03_through_the_real_hub(tmp_path):
    """Was: proved lead.reply held in clarification, unreachable. Now: the
    route exists (11 -> 03/04/12/13/20), so it reaches the handler for real."""
    hub = make_hub(str(tmp_path))
    spoke = Spoke03LeadNurture(hub)
    hub.on_turn_start()
    hub.send(nurture_env("n-007", {"consent": {"call": "yes", "text": "yes"},
                                   "sequence_id": "seq_a"}))

    env = Envelope(from_agent="11", to_agent="03", intent="lead.reply",
                  client_context_id="n-007", payload={"message": "stop"},
                  provenance={"source": "spoke-11", "captured_at": "runtime",
                              "verbatim_available": True})
    result = hub.send(env)
    assert result["status"] == "ack"
    events = hub.audit.read()
    assert [e for e in events if e["kind"] == "ack"
           and e["envelope_id"] == env.envelope_id]


def test_explicit_stop_suppresses_all_channels(tmp_path):
    hub = make_hub(str(tmp_path))
    spoke = Spoke03LeadNurture(hub)
    hub.on_turn_start()
    hub.send(nurture_env("n-008", {"consent": {"call": "yes", "text": "yes"},
                                   "sequence_id": "seq_a"}))

    env = Envelope(from_agent="11", to_agent="03", intent="lead.reply",
                  client_context_id="n-008", payload={"message": "STOP"},
                  provenance={"source": "spoke-11", "captured_at": "runtime",
                              "verbatim_available": True})
    hub.send(env)
    assert spoke.active_sequences["n-008"]["consent"] == {"call": "no", "text": "no"}


def test_engagement_spike_pauses_and_rescores(tmp_path):
    hub = make_hub(str(tmp_path))
    spoke = Spoke03LeadNurture(hub)
    hub.on_turn_start()
    hub.send(nurture_env("n-009", {"consent": {"email": "yes"}, "sequence_id": "seq_a"}))

    env = Envelope(from_agent="12", to_agent="03", intent="behavioral.signal",
                  client_context_id="n-009",
                  payload={"engagement_score": 80, "spike_threshold": 50},
                  provenance={"source": "spoke-12", "captured_at": "runtime",
                              "verbatim_available": True})
    result = hub.send(env)
    assert result["status"] == "ack"
    assert spoke.active_sequences["n-009"]["paused"] is True
    assert persisted(hub, "lead.rescored")


def test_substantive_question_routes_to_11_for_gated_human_reply(tmp_path):
    hub = make_hub(str(tmp_path))
    spoke = Spoke03LeadNurture(hub)
    hub.on_turn_start()
    hub.send(nurture_env("n-010", {"consent": {"call": "yes"}, "sequence_id": "seq_a"}))

    env = Envelope(from_agent="11", to_agent="03", intent="lead.reply",
                  client_context_id="n-010",
                  payload={"message": "What's the square footage on the second unit?"},
                  provenance={"source": "spoke-11", "captured_at": "runtime",
                              "verbatim_available": True})
    result = hub.send(env)
    assert result["status"] == "ack"
    routed = persisted(hub, "client.message.request")
    assert routed and routed[0]["to_agent"] == "11"


def test_compliance_wait_status_pushed_and_cleared_on_approval(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke03LeadNurture(hub)
    hub.on_turn_start()
    hub.send(nurture_env("n-020", {"consent": {"email": "yes"}, "sequence_id": "seq_a"}))
    status = persisted(hub, "agent.status")
    assert status and status[0]["payload"]["waiting_on"] == "compliance_review"

    verdict = Envelope(from_agent="17", to_agent="03", intent="content.verdict",
                      client_context_id="n-020", payload={"verdict": "approved"},
                      provenance={"source": "spoke-17", "captured_at": "runtime",
                                  "verbatim_available": True})
    hub.send(verdict)
    statuses = persisted(hub, "agent.status")
    assert any(s["payload"].get("resolved") for s in statuses)


def test_REGRESSION_frequency_cap_actually_enforced_now(tmp_path):
    """touch_log was declared but never read or written anywhere -
    frequency_cap_per_week only ever decreased on complaint, nothing ever
    checked touches sent against it. Proves real enforcement now."""
    hub = make_hub(str(tmp_path))
    spoke = Spoke03LeadNurture(hub, frequency_cap_per_week=2)
    hub.on_turn_start()
    ctx = "touch-001"
    hub.send(nurture_env(ctx, {"consent": {"email": "yes"}, "sequence_id": "seq_a"}))
    verdict = Envelope(from_agent="17", to_agent="03", intent="content.verdict",
                      client_context_id=ctx, payload={"verdict": "approved"},
                      provenance={"source": "spoke-17", "captured_at": "runtime",
                                  "verbatim_available": True})
    hub.send(verdict)

    r1 = spoke.send_scheduled_touch(ctx, {"body": "touch 1"}, "2026-W32")
    r2 = spoke.send_scheduled_touch(ctx, {"body": "touch 2"}, "2026-W32")
    r3 = spoke.send_scheduled_touch(ctx, {"body": "touch 3"}, "2026-W32")
    assert [r1, r2, r3] == ["sent", "sent", "held_frequency_cap"]

    sends = persisted(hub, "client.message.request")
    touches = [s for s in sends if s["payload"].get("template") == "sequence_touch"]
    assert len(touches) == 2  # never a third in the same week


def test_REGRESSION_frequency_cap_resets_next_week(tmp_path):
    hub = make_hub(str(tmp_path))
    spoke = Spoke03LeadNurture(hub, frequency_cap_per_week=1)
    hub.on_turn_start()
    ctx = "touch-002"
    hub.send(nurture_env(ctx, {"consent": {"email": "yes"}, "sequence_id": "seq_a"}))
    verdict = Envelope(from_agent="17", to_agent="03", intent="content.verdict",
                      client_context_id=ctx, payload={"verdict": "approved"},
                      provenance={"source": "spoke-17", "captured_at": "runtime",
                                  "verbatim_available": True})
    hub.send(verdict)
    r1 = spoke.send_scheduled_touch(ctx, {"body": "w32"}, "2026-W32")
    r2 = spoke.send_scheduled_touch(ctx, {"body": "w33"}, "2026-W33")
    assert [r1, r2] == ["sent", "sent"]


def test_REGRESSION_touch_never_sent_for_uncleared_sequence(tmp_path):
    hub = make_hub(str(tmp_path))
    spoke = Spoke03LeadNurture(hub)
    hub.on_turn_start()
    ctx = "touch-003"
    hub.send(nurture_env(ctx, {"consent": {"email": "yes"}, "sequence_id": "seq_a"}))
    # never approved - still pending
    result = spoke.send_scheduled_touch(ctx, {"body": "x"}, "2026-W32")
    assert result == "not_eligible"
    assert not persisted(hub, "client.message.request")
