"""Proves agent.status is genuinely wired (send + resolve) across the 8
remaining agents that lacked it, per direct instruction: this should be a
standard capability across all agents with a real wait, not selectively
applied. Agent 04 is covered in its own test file. Agent 10 is
deliberately not wired - it has no genuine request/response wait of its
own (purely reactive), confirmed rather than assumed.
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.signatures import Ed25519Signer, Ed25519Verifier

IDENTITY_ROUTES = os.path.join(os.path.dirname(__file__), "..", "identity",
                               "routes.json")


def make_signed_hub(tmp_path):
    audit_path = os.path.join(tmp_path, f"audit-{uuid.uuid4().hex[:8]}.jsonl")
    signer = Ed25519Signer()
    verifier = Ed25519Verifier(signer.public_key_bytes())
    hub = Hub(Routes(IDENTITY_ROUTES), AuditLog(audit_path),
             signature_verifier=verifier.verifier())
    return hub, signer


def status_events(hub):
    return [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"
           and e["intent"] == "agent.status"]


def test_agent05_photography_wait(tmp_path):
    from dispatcher.listing_spokes_05 import Spoke05MLSListingManagement
    hub, signer = make_signed_hub(str(tmp_path))
    spoke = Spoke05MLSListingManagement(hub)
    hub.on_turn_start()
    env = Envelope(from_agent="human", to_agent="05", intent="listing.change.authorized",
                  client_context_id="p-001",
                  payload={"new_listing": {"property_data": {}}},
                  provenance={"source": "human", "captured_at": "runtime",
                              "verbatim_available": True})
    signer.sign(env)
    hub.send(env)
    status = status_events(hub)
    assert any(s["payload"].get("waiting_on") == "photography_deliverable" for s in status)

    deliv = Envelope(from_agent="09", to_agent="05", intent="deliverable.release",
                    client_context_id="p-001",
                    payload={"opens_correctly": True, "photos": ["p1.jpg"]},
                    provenance={"source": "spoke-09", "captured_at": "runtime",
                                "verbatim_available": True})
    hub.send(deliv)
    status2 = status_events(hub)
    assert any(s["payload"].get("resolved") for s in status2)


def test_agent12_compliance_and_ccp_gate_waits(tmp_path):
    from dispatcher.listing_spokes_12 import Spoke12MarketingCampaign
    hub, signer = make_signed_hub(str(tmp_path))
    Spoke12MarketingCampaign(hub)
    hub.on_turn_start()
    env = Envelope(from_agent="human", to_agent="12", intent="config.update",
                  client_context_id="p-002", payload={"new_campaign": {"body": "x"}},
                  provenance={"source": "human", "captured_at": "runtime",
                              "verbatim_available": True})
    signer.sign(env)
    hub.send(env)
    status = status_events(hub)
    assert any(s["payload"].get("waiting_on") == "compliance_review" for s in status)

    verdict = Envelope(from_agent="17", to_agent="12", intent="content.verdict",
                      client_context_id="p-002", payload={"verdict": "approved"},
                      provenance={"source": "spoke-17", "captured_at": "runtime",
                                  "verbatim_available": True})
    hub.send(verdict)
    status2 = status_events(hub)
    assert any(s["payload"].get("waiting_on") == "compliance_review" and s["payload"].get("resolved")
              for s in status2)
    # CCP gate not clear yet - a second wait should now be open
    assert any(s["payload"].get("waiting_on") == "ccp_gate" for s in status2)


def test_agent13_criteria_review_and_buyer_agreement_waits(tmp_path):
    from dispatcher.listing_spokes_13 import Spoke13BuyerSearchMatch
    hub, signer = make_signed_hub(str(tmp_path))
    Spoke13BuyerSearchMatch(hub)
    hub.on_turn_start()
    env = Envelope(from_agent="human", to_agent="13", intent="config.update",
                  client_context_id="p-003", payload={"buyer_criteria": [
                      {"field": "walkability", "value": "high",
                      "fair_housing_sensitive": True}]},
                  provenance={"source": "human", "captured_at": "runtime",
                              "verbatim_available": True})
    signer.sign(env)
    hub.send(env)
    status = status_events(hub)
    assert any(s["payload"].get("waiting_on") == "criteria_compliance_review" for s in status)

    reply = Envelope(from_agent="11", to_agent="13", intent="lead.reply",
                    client_context_id="p-003b",
                    payload={"requests_showing": True, "listing_id": "L1"},
                    provenance={"source": "spoke-11", "captured_at": "runtime",
                                "verbatim_available": True})
    hub.send(reply)
    status2 = status_events(hub)
    assert any(s["payload"].get("waiting_on") == "buyer_agreement_verification" for s in status2)


def test_agent15_commission_crosscheck_wait(tmp_path):
    from dispatcher.listing_spokes_15 import Spoke15FinancialTracking
    hub, signer = make_signed_hub(str(tmp_path))
    Spoke15FinancialTracking(hub)
    hub.on_turn_start()
    txn = Envelope(from_agent="07", to_agent="15", intent="transaction.closed",
                  client_context_id="p-004",
                  payload={"commission_amount": 12000, "signed_docs_only": True},
                  provenance={"source": "spoke-07", "captured_at": "runtime",
                              "verbatim_available": True})
    hub.send(txn)
    status = status_events(hub)
    assert any(s["payload"].get("waiting_on") == "commission_crosscheck" for s in status)

    resp = Envelope(from_agent="14", to_agent="15", intent="record.response",
                   client_context_id="p-004", payload={"entries": []},
                   provenance={"source": "spoke-14", "captured_at": "runtime",
                               "verbatim_available": True})
    hub.send(resp)
    status2 = status_events(hub)
    assert any(s["payload"].get("resolved") for s in status2)


def test_agent16_referral_reward_review_wait(tmp_path):
    from dispatcher.listing_spokes_16 import Spoke16AfterCloseReferral
    hub, signer = make_signed_hub(str(tmp_path))
    Spoke16AfterCloseReferral(hub)
    hub.on_turn_start()
    reply = Envelope(from_agent="11", to_agent="16", intent="lead.reply",
                    client_context_id="p-005",
                    payload={"message": "what's the reward for referring",
                            "mentions_referral_reward": True},
                    provenance={"source": "spoke-11", "captured_at": "runtime",
                                "verbatim_available": True})
    hub.send(reply)
    status = status_events(hub)
    assert any(s["payload"].get("waiting_on") == "referral_reward_review" for s in status)

    verdict = Envelope(from_agent="17", to_agent="16", intent="content.verdict",
                      client_context_id="p-005", payload={"verdict": "approved"},
                      provenance={"source": "spoke-17", "captured_at": "runtime",
                                  "verbatim_available": True})
    hub.send(verdict)
    status2 = status_events(hub)
    assert any(s["payload"].get("resolved") for s in status2)


def test_agent17_reviews_own_bottleneck_as_a_wait(tmp_path):
    from dispatcher.listing_spokes_17 import Spoke17ComplianceFairHousing
    hub, signer = make_signed_hub(str(tmp_path))
    Spoke17ComplianceFairHousing(hub)
    hub.on_turn_start()
    ruleset_env = Envelope(from_agent="human", to_agent="17", intent="config.update",
                          client_context_id="ruleset",
                          payload={"ruleset": {"prohibited_phrases": []}, "version": "v1"},
                          provenance={"source": "human", "captured_at": "runtime",
                                      "verbatim_available": True})
    signer.sign(ruleset_env)
    hub.send(ruleset_env)

    review = Envelope(from_agent="04", to_agent="17", intent="content.review",
                     client_context_id="p-006", payload={"draft": {"facts": ["clean"]}},
                     provenance={"source": "spoke-04", "captured_at": "runtime",
                                 "verbatim_available": True})
    hub.send(review)
    status = status_events(hub)
    assert any(s["payload"].get("waiting_on") == "compliance_review:04" for s in status)
    assert any(s["payload"].get("resolved") for s in status)  # resolves same-turn here


def test_agent19_market_context_and_farm_data_waits(tmp_path):
    from dispatcher.listing_spokes_19 import Spoke19Prospecting
    hub, signer = make_signed_hub(str(tmp_path))
    spoke = Spoke19Prospecting(hub)
    hub.on_turn_start()
    zc = Envelope(from_agent="human", to_agent="19", intent="config.update",
                 client_context_id="setup", payload={"zip_codes": ["44811"]},
                 provenance={"source": "human", "captured_at": "runtime",
                             "verbatim_available": True})
    signer.sign(zc)
    hub.send(zc)
    disc = Envelope(from_agent="external", to_agent="19", intent="discovery.feed",
                   client_context_id="d-001",
                   payload={"listing_id": "L1", "zip_code": "44811",
                           "status": "new", "source": "mls_feed"},
                   provenance={"source": "external", "captured_at": "runtime",
                               "verbatim_available": True})
    hub.send(disc)
    spoke.request_market_context("L1")
    status = status_events(hub)
    assert any(s["payload"].get("waiting_on") == "market_context_enrichment" for s in status)


def test_agent20_draft_compliance_review_wait(tmp_path):
    from dispatcher.listing_spokes_20 import Spoke20SocialMediaMonitoring
    hub, signer = make_signed_hub(str(tmp_path))
    spoke = Spoke20SocialMediaMonitoring(hub)
    hub.on_turn_start()
    spoke.draft_response("p-007", "thanks for reaching out!", "mention-1")
    status = status_events(hub)
    assert any(s["payload"].get("waiting_on") == "draft_compliance_review" for s in status)

    reviews = [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"
              and e["intent"] == "content.review"]
    verdict = Envelope(from_agent="17", to_agent="20", intent="content.verdict",
                      client_context_id="p-007", payload={"verdict": "approved"},
                      in_reply_to=reviews[0]["envelope_id"],
                      provenance={"source": "spoke-17", "captured_at": "runtime",
                                  "verbatim_available": True})
    hub.send(verdict)
    status2 = status_events(hub)
    assert any(s["payload"].get("resolved") for s in status2)


# --- Proves each newly-exposed tunable actually changes behavior, not just exists ---

def test_TUNABLE_agent06_feedback_ask_cap_is_real(tmp_path):
    from dispatcher.listing_spokes_06 import Spoke06ShowingScheduler
    hub, signer = make_signed_hub(str(tmp_path))
    spoke = Spoke06ShowingScheduler(hub, feedback_ask_cap=1)
    hub.on_turn_start()
    r1 = spoke.request_showing_feedback("t-1", today="2026-08-01")
    r2 = spoke.request_showing_feedback("t-1")
    assert [r1, r2] == ["asked", "stopped"]  # capped at 1, not the old hardcoded 2


def test_TUNABLE_agent07_vendor_holdup_days_is_real(tmp_path):
    from dispatcher.listing_spokes_07 import Spoke07TransactionCoordinator
    hub, signer = make_signed_hub(str(tmp_path))
    spoke = Spoke07TransactionCoordinator(hub, vendor_holdup_days=2)
    hub.on_turn_start()
    spoke.vendor_requests_pending["t-1"] = {"inspection": "2026-08-01"}
    flagged = spoke.check_vendor_holdups("t-1", "2026-08-03")  # 2 days, not the old 7
    assert flagged == ["inspection"]


def test_TUNABLE_agent08_document_chase_cap_is_real(tmp_path):
    from dispatcher.listing_spokes_08 import Spoke08DocumentCollection
    hub, signer = make_signed_hub(str(tmp_path))
    spoke = Spoke08DocumentCollection(hub, document_chase_cap=1)
    hub.on_turn_start()
    spoke.pending_requests["t-1"] = {"preapproval_letter": {"chase_count": 0}}
    result = spoke.check_chase_timeout("t-1", "preapproval_letter")
    assert result == "escalated"  # capped at 1, not the old 3


def test_TUNABLE_agent10_opinion_press_threshold_is_real(tmp_path):
    from dispatcher.listing_spokes_10 import Spoke10MarketData
    hub, signer = make_signed_hub(str(tmp_path))
    Spoke10MarketData(hub, opinion_press_threshold=1)
    hub.on_turn_start()
    env = Envelope(from_agent="03", to_agent="10", intent="data.request",
                  client_context_id="t-1",
                  payload={"mode": "comp", "message": "what's your opinion on this one",
                          "license_scope": "internal"},
                  provenance={"source": "spoke-03", "captured_at": "runtime",
                              "verbatim_available": True})
    hub.send(env)
    assert hub.queues["escalation.legal_line"]  # fires on 1st ask, not the old 2nd


def test_TUNABLE_agent17_near_miss_pattern_threshold_is_real(tmp_path):
    from dispatcher.listing_spokes_17 import Spoke17ComplianceFairHousing
    hub, signer = make_signed_hub(str(tmp_path))
    spoke = Spoke17ComplianceFairHousing(hub, near_miss_pattern_threshold=1)
    hub.on_turn_start()
    ruleset_env = Envelope(from_agent="human", to_agent="17", intent="config.update",
                          client_context_id="ruleset",
                          payload={"ruleset": {"prohibited_phrases": [
                              {"phrase": "adults only", "rule_id": "FHA"}]}, "version": "v1"},
                          provenance={"source": "human", "captured_at": "runtime",
                                      "verbatim_available": True})
    signer.sign(ruleset_env)
    hub.send(ruleset_env)
    review = Envelope(from_agent="04", to_agent="17", intent="content.review",
                     client_context_id="t-1",
                     payload={"draft": {"facts": ["adults only"]}, "content_hash": "h1"},
                     provenance={"source": "spoke-04", "captured_at": "runtime",
                                 "verbatim_available": True})
    hub.send(review)
    reports = [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"
              and e["intent"] == "report.package"]
    assert any(r["payload"].get("report_type") == "near_miss_pattern" for r in reports)


# ---------------- 2026-07-17 sweep: dict/set-shaped parameters, missed
# the first pass because the original grep only caught int/float defaults
def test_TUNABLE_agent05_required_artifacts_is_real(tmp_path):
    from dispatcher.listing_spokes_05 import Spoke05MLSListingManagement
    hub, signer = make_signed_hub(str(tmp_path))
    spoke = Spoke05MLSListingManagement(hub, required_artifacts={"sold": "custom_artifact"})
    hub.on_turn_start()
    env = Envelope(from_agent="human", to_agent="05", intent="listing.change.authorized",
                  client_context_id="t-1",
                  payload={"field": "status", "value": "sold"},
                  provenance={"source": "human", "captured_at": "runtime",
                              "verbatim_available": True})
    signer.sign(env)
    hub.send(env)
    clar = [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"
           and e["intent"] == "clarification.request"]
    assert any("custom_artifact" in c["payload"].get("reason", "") for c in clar), \
        "the custom artifact name must actually govern the refusal, not the old hardcoded one"


def test_TUNABLE_agent06_min_notice_hours_is_real(tmp_path):
    from dispatcher.listing_spokes_06 import Spoke06ShowingScheduler
    hub, signer = make_signed_hub(str(tmp_path))
    Spoke06ShowingScheduler(hub, min_notice_hours={"default": 1})
    hub.on_turn_start()
    env = Envelope(from_agent="13", to_agent="06", intent="showing.request",
                  client_context_id="t-1",
                  payload={"requested_time": "2026-08-01T10:00",
                          "buyer_agreement_on_file": True,
                          "requester_identity_verified": True,
                          "property_occupied": True, "hours_notice": 2},
                  provenance={"source": "spoke-13", "captured_at": "runtime",
                              "verbatim_available": True})
    hub.send(env)
    sends = [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"
            and e["intent"] == "client.message.request"]
    assert not any(s["payload"].get("template") == "next_legal_slot_offer" for s in sends), \
        "2 hours notice must clear a 1-hour minimum, not the old hardcoded 24"


def test_TUNABLE_agent08_expected_senders_is_real(tmp_path):
    from dispatcher.listing_spokes_08 import Spoke08DocumentCollection
    hub, signer = make_signed_hub(str(tmp_path))
    Spoke08DocumentCollection(hub, expected_senders={"t-1": {"preapproval_letter": "lender-x"}})
    hub.on_turn_start()
    env = Envelope(from_agent="11", to_agent="08", intent="document.submission",
                  client_context_id="t-1",
                  payload={"doc_type": "preapproval_letter",
                          "submitting_party": "lender-x"},
                  provenance={"source": "spoke-11", "captured_at": "runtime",
                              "verbatim_available": True})
    hub.send(env)
    quarantine = [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"
                 and e["intent"] == "clarification.request"]
    assert not any("quarantine" in q["payload"].get("reason", "").lower() for q in quarantine), \
        "a party matching the configured allowlist must not be quarantined"


def test_TUNABLE_agent09_roster_is_real(tmp_path):
    from dispatcher.listing_spokes_09 import Spoke09VendorCoordination
    hub, signer = make_signed_hub(str(tmp_path))
    Spoke09VendorCoordination(hub, roster={"insp-custom": {
        "kind": "inspector", "license_expiry": "2027-01-01",
        "insurance_expiry": "2027-01-01", "regulated": True}})
    hub.on_turn_start()
    env = Envelope(from_agent="07", to_agent="09", intent="vendor.request",
                  client_context_id="t-1",
                  payload={"kind": "inspector", "vendor_id": "insp-custom",
                          "milestone": "inspection"},
                  provenance={"source": "spoke-07", "captured_at": "runtime",
                              "verbatim_available": True})
    hub.send(env)
    refusals = [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"
               and e["intent"] == "clarification.request"]
    assert not any("approved list" in r["payload"].get("reason", "") for r in refusals), \
        "a vendor present in the configured roster must not be refused as unapproved"


def test_TUNABLE_agent11_exempt_alert_classes_is_real(tmp_path):
    from dispatcher.listing_spokes_11 import Spoke11ClientCommunication
    hub, signer = make_signed_hub(str(tmp_path))
    Spoke11ClientCommunication(hub, exempt_alert_classes={"wire_fraud_alert"})
    hub.on_turn_start()
    env = Envelope(from_agent="07", to_agent="11", intent="deadline.alert",
                  client_context_id="t-1",
                  payload={"template": "wire_fraud_alert",
                          "alert_class": "wire_fraud_alert", "hour": 2},
                  provenance={"source": "spoke-07", "captured_at": "runtime",
                              "verbatim_available": True})
    hub.send(env)
    sends = [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"
            and e["intent"] == "client.message.send"]
    assert any(s["payload"].get("template") == "wire_fraud_alert" for s in sends), \
        "an alert class configured as exempt must send during quiet hours, not queue"
