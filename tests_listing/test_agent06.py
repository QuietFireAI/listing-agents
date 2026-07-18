import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.listing_spokes_06 import Spoke06ShowingScheduler
from dispatcher.listing_spokes_05 import Spoke05MLSListingManagement
from dispatcher.signatures import Ed25519Signer, Ed25519Verifier

IDENTITY_ROUTES = os.path.join(os.path.dirname(__file__), "..", "identity",
                               "routes.json")


def make_hub(tmp_path, **kw):
    audit_path = os.path.join(tmp_path, f"audit-{uuid.uuid4().hex[:8]}.jsonl")
    signer = Ed25519Signer()
    verifier = Ed25519Verifier(signer.public_key_bytes())
    hub = Hub(Routes(IDENTITY_ROUTES), AuditLog(audit_path),
             signature_verifier=verifier.verifier(), **kw)
    return hub, signer


def showing_req(ctx, payload, frm="13"):
    return Envelope(from_agent=frm, to_agent="06", intent="showing.request",
                    client_context_id=ctx, payload=payload,
                    provenance={"source": f"spoke-{frm}", "captured_at": "runtime",
                                "verbatim_available": True})


def persisted(hub, intent=None):
    events = [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"]
    return [e for e in events if intent is None or e["intent"] == intent]


def base_payload(**overrides):
    p = {"buyer_agreement_on_file": True, "requester_identity_verified": True,
        "requested_time": "2026-08-01T14:00"}
    p.update(overrides)
    return p


def test_clean_request_confirms_showing(tmp_path):
    hub, _ = make_hub(str(tmp_path))
    spoke = Spoke06ShowingScheduler(hub)
    hub.on_turn_start()
    hub.send(showing_req("s-001", base_payload()))
    assert spoke.confirmed_showings["s-001"][0]["confirmed"] is True
    assert persisted(hub, "calendar.event")


def test_access_request_language_escalates_never_schedules(tmp_path):
    hub, _ = make_hub(str(tmp_path))
    spoke = Spoke06ShowingScheduler(hub)
    hub.on_turn_start()
    hub.send(showing_req("s-002", base_payload(message="can you just let them in")))
    assert hub.queues["escalation.legal_line"]
    assert "s-002" not in spoke.confirmed_showings


def test_access_code_request_never_transmitted(tmp_path):
    hub, _ = make_hub(str(tmp_path))
    spoke = Spoke06ShowingScheduler(hub)
    hub.on_turn_start()
    hub.send(showing_req("s-003", base_payload(requests_access_code=True)))
    assert hub.queues["escalation.legal_line"]
    assert "s-003" not in spoke.confirmed_showings


def test_missing_buyer_agreement_holds_and_escalates(tmp_path):
    hub, _ = make_hub(str(tmp_path))
    spoke = Spoke06ShowingScheduler(hub)
    hub.on_turn_start()
    hub.send(showing_req("s-004", base_payload(buyer_agreement_on_file=False)))
    assert hub.queues["escalation.legal_line"]
    assert "s-004" not in spoke.confirmed_showings


def test_unverified_identity_cancels_not_holds(tmp_path):
    hub, _ = make_hub(str(tmp_path))
    spoke = Spoke06ShowingScheduler(hub)
    hub.on_turn_start()
    hub.send(showing_req("s-005", base_payload(requester_identity_verified=False)))
    logs = persisted(hub, "interaction.log")
    assert any(l["payload"].get("kind") == "showing_cancelled" for l in logs)


def test_occupied_property_insufficient_notice_offers_next_slot(tmp_path):
    hub, _ = make_hub(str(tmp_path))
    spoke = Spoke06ShowingScheduler(hub)
    hub.on_turn_start()
    hub.send(showing_req("s-006", base_payload(
        property_occupied=True, hours_notice=2)))
    assert "s-006" not in spoke.confirmed_showings
    msgs = persisted(hub, "client.message.request")
    assert any(m["payload"].get("template") == "next_legal_slot_offer" for m in msgs)


def test_possibly_under_contract_confirms_via_05_before_scheduling(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    mls = Spoke05MLSListingManagement(hub)
    spoke = Spoke06ShowingScheduler(hub)
    hub.on_turn_start()

    # set the record to 'active' via signed onboarding
    env = Envelope(from_agent="human", to_agent="05",
                  intent="listing.change.authorized", client_context_id="s-007",
                  payload={"new_listing": {"beds": 3}, "authorize_go_live": True},
                  provenance={"source": "human", "captured_at": "runtime",
                              "verbatim_available": True})
    signer.sign(env)
    hub.send(env)
    mls.mls_records["s-007"]["status"] = "active"  # simulate go-live already happened

    hub.send(showing_req("s-007", base_payload(possibly_under_contract=True)))
    assert persisted(hub, "status.request")
    assert spoke.confirmed_showings.get("s-007", [{}])[0].get("confirmed") is True


def test_not_active_status_holds_never_schedules(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    mls = Spoke05MLSListingManagement(hub)
    spoke = Spoke06ShowingScheduler(hub)
    hub.on_turn_start()
    mls.mls_records["s-008"] = {"status": "pending"}

    hub.send(showing_req("s-008", base_payload(possibly_under_contract=True)))
    assert "s-008" not in spoke.confirmed_showings
    clar = persisted(hub, "clarification.request")
    assert any("not show-able" in c["payload"]["reason"] for c in clar)


def test_no_show_logs_and_requests_feedback_no_reproach(tmp_path):
    hub, _ = make_hub(str(tmp_path))
    Spoke06ShowingScheduler(hub)
    hub.on_turn_start()
    env = Envelope(from_agent="11", to_agent="06", intent="showing.no_show",
                  client_context_id="s-009", payload={},
                  provenance={"source": "spoke-11", "captured_at": "runtime",
                              "verbatim_available": True})
    hub.send(env)
    msgs = persisted(hub, "client.message.request")
    assert any(m["payload"].get("tone") == "neutral_no_reproach" for m in msgs)


def test_showing_agent_second_no_show_flags_pattern(tmp_path):
    hub, _ = make_hub(str(tmp_path))
    Spoke06ShowingScheduler(hub)
    hub.on_turn_start()
    for i in range(2):
        env = Envelope(from_agent="11", to_agent="06", intent="showing.no_show",
                      client_context_id=f"s-010-{i}",
                      payload={"showing_agent_id": "agent-x", "is_agent_no_show": True},
                      provenance={"source": "spoke-11", "captured_at": "runtime",
                                  "verbatim_available": True})
        hub.send(env)
    clar = persisted(hub, "clarification.request")
    assert any("agent-x" in c["payload"]["reason"] for c in clar)


def test_open_house_orders_vendor_signage(tmp_path):
    hub, _ = make_hub(str(tmp_path))
    Spoke06ShowingScheduler(hub)
    hub.on_turn_start()
    hub.send(showing_req("s-011", base_payload(is_open_house=True)))
    vr = persisted(hub, "vendor.request")
    assert vr and vr[0]["to_agent"] == "09"


def test_overlapping_showing_conflict_holds_for_clarification(tmp_path):
    hub, _ = make_hub(str(tmp_path))
    spoke = Spoke06ShowingScheduler(hub)
    hub.on_turn_start()
    hub.send(showing_req("s-012", base_payload(requested_time="2026-08-01T10:00")))
    hub.send(showing_req("s-012", base_payload(requested_time="2026-08-01T10:00")))
    clar = persisted(hub, "clarification.request")
    assert any("calendar conflict" in c["payload"]["reason"] for c in clar)


# ------------------------------------------------- THE FIX: buffer window
def test_near_miss_within_buffer_window_is_now_a_conflict(tmp_path):
    """Tuple 9: 'sequence with buffer, never double-book and hope'. Was:
    the conflict check was exact-time-match only - buffer_minutes rode
    along in the calendar.event payload as pure data, never compared
    against anything. Two showings 15 minutes apart with
    buffer_minutes=30 produced zero conflict detection."""
    hub, _ = make_hub(str(tmp_path))
    spoke = Spoke06ShowingScheduler(hub)
    hub.on_turn_start()
    ctx = "s-020"
    hub.send(showing_req(ctx, base_payload(requested_time="2026-08-01T10:00",
                                           buffer_minutes=30)))
    hub.send(showing_req(ctx, base_payload(requested_time="2026-08-01T10:15",
                                           buffer_minutes=30)))
    clar = persisted(hub, "clarification.request")
    assert any("calendar conflict" in c["payload"]["reason"] for c in clar)
    # the second (conflicting) showing must not have been confirmed
    assert len(spoke.confirmed_showings[ctx]) == 1


def test_showing_outside_buffer_window_is_not_a_conflict(tmp_path):
    hub, _ = make_hub(str(tmp_path))
    spoke = Spoke06ShowingScheduler(hub)
    hub.on_turn_start()
    ctx = "s-021"
    hub.send(showing_req(ctx, base_payload(requested_time="2026-08-01T10:00",
                                           buffer_minutes=30)))
    hub.send(showing_req(ctx, base_payload(requested_time="2026-08-01T11:00",
                                           buffer_minutes=30)))
    clar = persisted(hub, "clarification.request")
    assert not any("calendar conflict" in c["payload"].get("reason", "")
                  for c in clar)
    assert len(spoke.confirmed_showings[ctx]) == 2


# ------------------------------------- THE FIX: protected deadline bump
def test_protected_deadline_claim_holds_for_human_and_bumps_only_on_confirm(tmp_path):
    """Owner decision 2026-07-17: protected_deadline is a caller-writable
    payload flag - any requester claiming it used to auto-bump confirmed
    showings. Now the claim HOLDS for human confirmation; the bump (with
    displaced-party notice) executes only on the human's signed
    config.update confirm_protected_bump."""
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke06ShowingScheduler(hub)
    hub.on_turn_start()
    ctx = "s-022"
    hub.send(showing_req(ctx, base_payload(requested_time="2026-08-01T10:00")))
    assert len(spoke.confirmed_showings[ctx]) == 1

    hub.send(showing_req(ctx, base_payload(requested_time="2026-08-01T10:00",
                                           protected_deadline=True,
                                           lead_tier="HOT")))
    # claim alone (even HOT): nothing bumped, original stands, human queue holds it
    assert [s_["time"] for s_ in spoke.confirmed_showings[ctx]] == ["2026-08-01T10:00"]
    assert ctx in spoke.pending_bumps
    reasons = [r.get("payload", {}).get("reason", "")
               for r in hub.queues["clarification.request"]]
    assert any("human confirmation required" in r for r in reasons)

    # human confirms via signed config.update -> bump executes + notices
    env = Envelope(from_agent="human", to_agent="06", intent="config.update",
                   client_context_id=ctx,
                   payload={"confirm_protected_bump": True},
                   provenance={"source": "human", "captured_at": "runtime",
                               "verbatim_available": True})
    signer.sign(env)
    hub.send(env)
    assert ctx not in spoke.pending_bumps
    assert len(spoke.confirmed_showings[ctx]) == 1
    bump_notices = [e for e in persisted(hub, "client.message.request")
                    if e["payload"].get("template") == "showing_bumped_notice"]
    assert bump_notices, "the displaced party must be notified"
    bump_logs = [e for e in persisted(hub, "interaction.log")
                if e["payload"].get("kind") == "showing_bumped"]
    assert bump_logs

def test_feedback_ask_stops_after_two_never_asks_a_third(tmp_path):
    """tuple 10 was declared (feedback_asks dict) but never actually
    implemented - nothing incremented or read it. Proves the real fix."""
    hub, _ = make_hub(str(tmp_path))
    spoke = Spoke06ShowingScheduler(hub)
    hub.on_turn_start()
    ctx = "fb-001"
    r1 = spoke.request_showing_feedback(ctx, today="2026-08-01")
    r2 = spoke.request_showing_feedback(ctx)
    r3 = spoke.request_showing_feedback(ctx)
    assert [r1, r2, r3] == ["asked", "asked", "stopped"]
    asks = persisted(hub, "client.message.request")
    assert len(asks) == 2  # never a third


def test_feedback_response_clears_the_wait(tmp_path):
    hub, _ = make_hub(str(tmp_path))
    spoke = Spoke06ShowingScheduler(hub)
    hub.on_turn_start()
    spoke.request_showing_feedback("fb-002", today="2026-08-01")
    status = persisted(hub, "agent.status")
    assert any(s["payload"].get("waiting_on") == "showing_feedback" for s in status)

    env = Envelope(from_agent="11", to_agent="06", intent="showing.feedback_response",
                  client_context_id="fb-002", payload={"response": "great showing"},
                  provenance={"source": "spoke-11", "captured_at": "runtime",
                              "verbatim_available": True})
    hub.send(env)
    assert "fb-002" not in spoke.feedback_asks
    statuses = persisted(hub, "agent.status")
    assert any(s["payload"].get("resolved") for s in statuses)



def test_protected_claim_without_hot_tier_gets_no_bump_offer(tmp_path):
    """Owner decision #2 refinement (2026-07-18): hotness gates the bump
    OFFER. Tier is relayed by 13 from 14's CRM records - a claim with no
    tier, or a non-hot one, falls back to plain conflict sequencing and
    never lands in pending_bumps."""
    hub, _ = make_hub(str(tmp_path))
    spoke = Spoke06ShowingScheduler(hub)
    hub.on_turn_start()
    ctx = "s-023"
    hub.send(showing_req(ctx, base_payload(requested_time="2026-08-02T10:00")))
    for tier in (None, "WARM"):
        hub.send(showing_req(ctx, base_payload(requested_time="2026-08-02T10:00",
                                               protected_deadline=True,
                                               lead_tier=tier)))
        assert ctx not in spoke.pending_bumps
    reasons = [r.get("payload", {}).get("reason", "")
               for r in hub.queues["clarification.request"]]
    assert any("lead tier not HOT - no bump offered" in r for r in reasons)
    assert len(spoke.confirmed_showings[ctx]) == 1
