import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.listing_spokes_13 import Spoke13BuyerSearchMatch
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


def config_update(signer, ctx, payload):
    env = Envelope(from_agent="human", to_agent="13", intent="config.update",
                   client_context_id=ctx, payload=payload,
                   provenance={"source": "human", "captured_at": "runtime",
                               "verbatim_available": True})
    signer.sign(env)
    return env


def listing_data(ctx, payload):
    return Envelope(from_agent="05", to_agent="13", intent="listing.data",
                    client_context_id=ctx, payload=payload,
                    provenance={"source": "spoke-05", "captured_at": "runtime",
                                "verbatim_available": True})


def lead_reply(ctx, payload):
    return Envelope(from_agent="11", to_agent="13", intent="lead.reply",
                    client_context_id=ctx, payload=payload,
                    provenance={"source": "spoke-11", "captured_at": "runtime",
                                "verbatim_available": True})


def persisted(hub, intent=None):
    events = [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"]
    return [e for e in events if intent is None or e["intent"] == intent]


def test_protected_class_criterion_refused_and_escalated(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke13BuyerSearchMatch(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "b-001", {"buyer_criteria": [
        {"field": "school_quality", "value": "excellent", "hard": False}]}))
    assert hub.queues["escalation.legal_line"]
    assert spoke.buyer_criteria.get("b-001", []) == []


def test_clean_criteria_stored_verbatim_stated_by_party(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke13BuyerSearchMatch(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "b-002", {"buyer_criteria": [
        {"field": "budget", "value": 450000, "hard": True}]}))
    assert spoke.buyer_criteria["b-002"][0]["source"] == "stated_by_party"
    assert spoke.buyer_criteria["b-002"][0]["value"] == 450000


def test_fair_housing_sensitive_criterion_goes_to_compliance_first(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke13BuyerSearchMatch(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "b-003", {"buyer_criteria": [
        {"field": "walkability", "value": "high", "fair_housing_sensitive": True}]}))
    review = persisted(hub, "content.review")
    assert review and review[0]["to_agent"] == "17"
    assert spoke.buyer_criteria.get("b-003", []) == []  # not usable until cleared


def test_criteria_cleared_after_compliance_approval(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke13BuyerSearchMatch(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "b-004", {"buyer_criteria": [
        {"field": "walkability", "value": "high", "fair_housing_sensitive": True}]}))
    verdict = Envelope(from_agent="17", to_agent="13", intent="content.verdict",
                      client_context_id="b-004", payload={"verdict": "approved"},
                      provenance={"source": "spoke-17", "captured_at": "runtime",
                                  "verbatim_available": True})
    hub.send(verdict)
    assert len(spoke.buyer_criteria["b-004"]) == 1


def test_price_opinion_question_escalates(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke13BuyerSearchMatch(hub)
    hub.on_turn_start()
    hub.send(lead_reply("b-005", {"message": "is this a good price for the area"}))
    assert hub.queues["escalation.legal_line"]


def test_seller_position_question_refused(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke13BuyerSearchMatch(hub)
    hub.on_turn_start()
    hub.send(lead_reply("b-006", {"message": "what will they accept, really"}))
    assert hub.queues["escalation.legal_line"]


def test_budget_area_conflict_presented_never_relaxed(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke13BuyerSearchMatch(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "b-007", {"buyer_criteria": [
        {"field": "budget", "value": 400000, "hard": True},
        {"field": "area", "value": "downtown", "hard": True}]}))
    hub.send(listing_data("b-007", {"listing_id": "L1", "price": 500000,
                                    "area": "downtown"}))
    clar = persisted(hub, "clarification.request")
    assert any("conflict" in c["payload"]["reason"] for c in clar)
    assert spoke.match_history.get("b-007", []) == []


def test_data_anomaly_flagged_never_presented_as_inventory(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke13BuyerSearchMatch(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "b-008", {"buyer_criteria": [
        {"field": "budget", "value": 500000, "hard": True}]}))
    hub.send(listing_data("b-008", {"listing_id": "L2", "price": 400000,
                                    "data_anomaly": "sqft mismatch"}))
    clar = persisted(hub, "clarification.request")
    assert any("anomaly" in c["payload"]["reason"] for c in clar)
    assert spoke.match_history.get("b-008", []) == []


def test_missing_hard_criterion_holds_without_standing_preference(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke13BuyerSearchMatch(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "b-009", {"buyer_criteria": [
        {"field": "garage", "value": True, "hard": True}]}))
    hub.send(listing_data("b-009", {"listing_id": "L3", "price": 300000}))
    clar = persisted(hub, "clarification.request")
    assert any("incomplete" in c["payload"]["reason"] for c in clar)
    assert spoke.match_history.get("b-009", []) == []


def test_missing_hard_criterion_delivers_unknown_with_standing_preference(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke13BuyerSearchMatch(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "b-010", {"buyer_criteria": [
        {"field": "garage", "value": True, "hard": True}]}))
    hub.send(config_update(signer, "b-010", {"deliver_unknown_standing_preference": True}))
    hub.send(listing_data("b-010", {"listing_id": "L4", "price": 300000}))
    assert len(spoke.match_history["b-010"]) == 1
    assert spoke.match_history["b-010"][0]["unknown_fields"] == ["garage"]


def test_colleague_listing_escalates_before_showing_motion(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke13BuyerSearchMatch(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "b-011", {"buyer_criteria": [
        {"field": "budget", "value": 500000, "hard": True}]}))
    hub.send(listing_data("b-011", {"listing_id": "L5", "price": 400000,
                                    "colleague_listing": True}))
    assert hub.queues["escalation.legal_line"]
    assert spoke.match_history.get("b-011", []) == []


def test_clean_match_delivers_via_11(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke13BuyerSearchMatch(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "b-012", {"buyer_criteria": [
        {"field": "budget", "value": 500000, "hard": True}]}))
    hub.send(listing_data("b-012", {"listing_id": "L6", "price": 400000}))
    msgs = persisted(hub, "client.message.request")
    assert any(m["payload"].get("template") == "new_match" for m in msgs)


def test_contradicting_feedback_asks_never_rewrites_silently(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke13BuyerSearchMatch(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "b-013", {"buyer_criteria": [
        {"field": "area", "value": "suburbs", "hard": True}]}))
    hub.send(lead_reply("b-013", {"message": "I love this downtown place though",
                                  "contradicts_stated_criteria": True}))
    msgs = persisted(hub, "client.message.request")
    assert any(m["payload"].get("template") == "confirm_criteria_update" for m in msgs)
    assert len(spoke.buyer_criteria["b-013"]) == 1  # unchanged


def test_explicit_feedback_updates_criteria(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke13BuyerSearchMatch(hub)
    hub.on_turn_start()
    hub.send(lead_reply("b-014", {"explicit_feedback_field": "min_bedrooms",
                                  "explicit_feedback_value": 3}))
    assert any(c["field"] == "min_bedrooms" for c in spoke.buyer_criteria["b-014"])


def test_showing_request_gated_on_buyer_agreement(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke13BuyerSearchMatch(hub)
    hub.on_turn_start()
    hub.send(lead_reply("b-015", {"requests_showing": True, "listing_id": "L7"}))
    req = persisted(hub, "record.request")
    assert req and req[0]["to_agent"] == "14"
    assert not persisted(hub, "showing.request")


def test_showing_request_sent_when_agreement_on_file(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke13BuyerSearchMatch(hub)
    hub.on_turn_start()
    hub.send(lead_reply("b-016", {"requests_showing": True, "listing_id": "L8"}))
    resp = Envelope(from_agent="14", to_agent="13", intent="record.response",
                   client_context_id="b-016",
                   payload={"buyer_agreement_on_file": True,
                           "requester_identity_verified": True},
                   provenance={"source": "spoke-14", "captured_at": "runtime",
                               "verbatim_available": True})
    hub.send(resp)
    req = persisted(hub, "showing.request")
    assert req and req[0]["to_agent"] == "06"
    assert req[0]["payload"]["buyer_agreement_on_file"] is True


def test_showing_request_escalates_when_no_agreement_on_file(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke13BuyerSearchMatch(hub)
    hub.on_turn_start()
    hub.send(lead_reply("b-017", {"requests_showing": True, "listing_id": "L9"}))
    resp = Envelope(from_agent="14", to_agent="13", intent="record.response",
                   client_context_id="b-017",
                   payload={"buyer_agreement_on_file": False},
                   provenance={"source": "spoke-14", "captured_at": "runtime",
                               "verbatim_available": True})
    hub.send(resp)
    assert hub.queues["escalation.legal_line"]
    assert not persisted(hub, "showing.request")


def test_preapproval_expiry_notifies_and_marks_matches(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke13BuyerSearchMatch(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "b-018", {"buyer_criteria": [
        {"field": "budget", "value": 500000, "hard": True}]}))
    hub.send(config_update(signer, "b-018", {"preapproval_expired": True}))
    msgs = persisted(hub, "client.message.request")
    assert any(m["payload"].get("template") == "preapproval_expired_notice" for m in msgs)

    hub.send(listing_data("b-018", {"listing_id": "L10", "price": 400000}))
    assert spoke.match_history["b-018"][0]["unverified_financing"] is True


def test_request_neighborhood_data_sends_data_request(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke13BuyerSearchMatch(hub)
    hub.on_turn_start()
    spoke.request_neighborhood_data("b-019")
    req = persisted(hub, "data.request")
    assert req and req[0]["to_agent"] == "10"


# --------- tuple 5 overload digest (owner decision #7, 2026-07-18)
def test_overload_batches_into_ranked_digest_nothing_dropped(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke13BuyerSearchMatch(hub)
    for a in ("11", "14", "18"):
        hub.register(a, lambda env: None)
    hub.on_turn_start()
    ctx = "b-ovl"
    hub.send(config_update(signer, ctx, {"buyer_criteria": [
        {"field": "budget", "value": 500000},
        {"field": "area", "value": "north"}]}))
    # 8 matches same day: 5 ping individually, 3 defer to digest
    for i in range(8):
        hub.send(listing_data(ctx, {
            "listing_id": f"L{i}", "price": 400000 + i * 10000,
            "area": "north" if i % 2 == 0 else "south",
            "today": "2026-07-18"}))
    pings = [e for e in persisted(hub, "client.message.request")
             if e["payload"].get("template") == "new_match"]
    assert len(pings) == 5
    # every match logged to 14 - nothing silently dropped
    logs = [e for e in persisted(hub, "interaction.log")
            if e["payload"].get("kind") == "match_delivered"]
    assert len(logs) == 8
    # digest: ranked by stated-criteria-met desc, ties newest-first
    assert spoke.flush_match_digest(ctx) == "flushed:3"
    digests = [e for e in persisted(hub, "client.message.request")
               if e["payload"].get("template") == "match_digest"]
    assert len(digests) == 1
    ids = [m["listing_id"] for m in digests[0]["payload"]["matches"]]
    # overflow = L5(south,1 met), L6(north,2 met), L7(south,1 met)
    # rank: L6 first (2 met), then L7 over L5 (tie, newest first)
    assert ids == ["L6", "L7", "L5"]
    assert digests[0]["payload"]["rank_basis"] == "stated_criteria_met"
    # flush twice never re-sends
    assert spoke.flush_match_digest(ctx) == "nothing_to_flush"


def test_overload_counter_resets_on_new_day(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke13BuyerSearchMatch(hub)
    for a in ("11", "14", "18"):
        hub.register(a, lambda env: None)
    hub.on_turn_start()
    ctx = "b-day"
    hub.send(config_update(signer, ctx, {"buyer_criteria": [
        {"field": "area", "value": "north"}]}))
    for i in range(6):
        hub.send(listing_data(ctx, {"listing_id": f"D{i}", "area": "north",
                                    "today": "2026-07-18"}))
    assert len(spoke.matches_today[ctx]["overflow"]) == 1
    hub.send(listing_data(ctx, {"listing_id": "D6", "area": "north",
                                "today": "2026-07-19"}))
    assert spoke.matches_today[ctx]["day"] == "2026-07-19"
    assert spoke.matches_today[ctx]["count"] == 1
    assert spoke.matches_today[ctx]["overflow"] == []
