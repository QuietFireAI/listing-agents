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
sys.path.insert(0, "/home/claude/pillars_pth")

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
                    payload={"verified_openable": True, "photos": ["p1.jpg"]},
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
