import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.listing_spokes_16 import Spoke16AfterCloseReferral
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
    env = Envelope(from_agent="human", to_agent="16", intent="config.update",
                   client_context_id=ctx, payload=payload,
                   provenance={"source": "human", "captured_at": "runtime",
                               "verbatim_available": True})
    signer.sign(env)
    return env


def date_trigger(ctx, event_type):
    return Envelope(from_agent="14", to_agent="16", intent="date.trigger",
                    client_context_id=ctx, payload={"event_type": event_type,
                                                    "date": "2026-08-01"},
                    provenance={"source": "spoke-14", "captured_at": "runtime",
                                "verbatim_available": True})


def lead_reply(ctx, payload):
    return Envelope(from_agent="11", to_agent="16", intent="lead.reply",
                    client_context_id=ctx, payload=payload,
                    provenance={"source": "spoke-11", "captured_at": "runtime",
                                "verbatim_available": True})


def persisted(hub, intent=None):
    events = [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"]
    return [e for e in events if intent is None or e["intent"] == intent]


def _on_list(signer, ctx="n-001"):
    return config_update(signer, ctx, {"client_list_entry": {"name": "Jane"}})


def test_greeting_for_contact_not_on_list_refused(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke16AfterCloseReferral(hub)
    hub.on_turn_start()
    hub.send(date_trigger("n-001", "birthday"))
    assert not persisted(hub, "client.message.request")
    clar = persisted(hub, "clarification.request")
    assert any("supplied list" in c["payload"]["reason"] for c in clar)


def test_greeting_for_listed_contact_sends(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke16AfterCloseReferral(hub)
    hub.on_turn_start()
    hub.send(_on_list(signer))
    hub.send(date_trigger("n-001", "birthday"))
    sends = persisted(hub, "client.message.request")
    assert any(s["payload"]["template"] == "birthday_greeting" for s in sends)


def test_opt_out_halts_touch_and_is_reversible_by_human(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke16AfterCloseReferral(hub)
    hub.on_turn_start()
    hub.send(_on_list(signer))
    hub.send(config_update(signer, "n-001", {"opt_out": True}))
    hub.send(date_trigger("n-001", "birthday"))
    assert not persisted(hub, "client.message.request")

    # recovery path: explicit human reinstatement
    hub.send(config_update(signer, "n-001", {"opt_out": False}))
    hub.send(date_trigger("n-001", "home_anniversary"))
    sends = persisted(hub, "client.message.request")
    assert sends and sends[0]["payload"]["template"] == "home_anniversary_greeting"


def test_adverse_event_suppresses_and_is_reversible_by_human(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke16AfterCloseReferral(hub)
    hub.on_turn_start()
    hub.send(_on_list(signer))
    hub.send(config_update(signer, "n-001", {"adverse_event": True}))
    hub.send(date_trigger("n-001", "home_anniversary"))
    assert not persisted(hub, "client.message.request")

    hub.send(config_update(signer, "n-001", {"adverse_event": False}))
    hub.send(date_trigger("n-001", "home_anniversary"))
    assert persisted(hub, "client.message.request")


def test_stale_contact_blocks_and_is_reversible_by_human(tmp_path):
    """Real gap found via recovery-path check: nothing ever cleared
    stale_contacts before this fix."""
    hub, signer = make_hub(str(tmp_path))
    Spoke16AfterCloseReferral(hub)
    hub.on_turn_start()
    hub.send(_on_list(signer))
    hub.send(config_update(signer, "n-001", {"contact_bounced": True}))
    hub.send(date_trigger("n-001", "birthday"))
    assert not persisted(hub, "client.message.request")

    hub.send(config_update(signer, "n-001", {"reinstate_contact": True}))
    hub.send(date_trigger("n-001", "birthday"))
    assert persisted(hub, "client.message.request")


def test_incentive_mention_escalates(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke16AfterCloseReferral(hub)
    hub.on_turn_start()
    hub.send(lead_reply("n-002", {"message": "would you pay you for referrals"}))
    assert hub.queues["escalation.legal_line"]


def test_referral_reward_mention_gated_through_17(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke16AfterCloseReferral(hub)
    hub.on_turn_start()
    hub.send(lead_reply("n-003", {"message": "what's the reward for referring someone",
                                  "mentions_referral_reward": True}))
    review = persisted(hub, "content.review")
    assert review and review[0]["to_agent"] == "17"


def test_pricing_question_escalates(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke16AfterCloseReferral(hub)
    hub.on_turn_start()
    hub.send(lead_reply("n-004", {"message": "what's my house worth now"}))
    assert hub.queues["escalation.legal_line"]


def test_new_business_signal_opens_new_context_old_stays_closed(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke16AfterCloseReferral(hub)
    hub.on_turn_start()
    hub.send(lead_reply("n-005", {"message": "we're thinking of selling again",
                                  "new_lead_context_id": "n-005-new"}))
    leads = persisted(hub, "lead.captured")
    assert leads and leads[0]["client_context_id"] == "n-005-new"
    assert leads[0]["client_context_id"] != "n-005"


def test_refi_alert_without_provenance_source_not_sent(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke16AfterCloseReferral(hub)
    hub.on_turn_start()
    hub.send(_on_list(signer))
    hub.send(config_update(signer, "n-001", {"refi_rate_alert": {"rate": 5.5}}))
    assert not persisted(hub, "client.message.request")
    clar = persisted(hub, "clarification.request")
    assert any("provenance" in c["payload"]["reason"] for c in clar)


def test_refi_alert_with_provenance_states_fact_not_advice(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke16AfterCloseReferral(hub)
    hub.on_turn_start()
    hub.send(_on_list(signer))
    hub.send(config_update(signer, "n-001", {"refi_rate_alert": {"rate": 5.5, "source": "freddiemac"}}))
    sends = persisted(hub, "client.message.request")
    assert sends and sends[0]["payload"]["template"] == "refi_rate_fact"
    assert sends[0]["payload"]["rate"] == 5.5


def test_referral_solicitation_respects_touch_gate(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke16AfterCloseReferral(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "n-006", {"referral_solicitation_due": True}))
    assert not persisted(hub, "client.message.request")  # not on list

    # THE FIX: a blocked touch must actually be logged, not silently
    # swallowed - a human auditing why a solicitation never went out
    # needs a real record.
    logs = persisted(hub, "interaction.log")
    assert any(l["payload"].get("kind") == "touch_blocked"
              and l["payload"].get("touch_type") == "referral_solicitation"
              and l["payload"].get("reason") == "not_on_supplied_list"
              for l in logs)

    hub.send(config_update(signer, "n-006", {"client_list_entry": {"name": "Bob"}}))
    hub.send(config_update(signer, "n-006", {"referral_solicitation_due": True}))
    sends = persisted(hub, "client.message.request")
    assert any(s["payload"]["template"] == "referral_solicitation" for s in sends)


def test_review_request_respects_touch_gate(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke16AfterCloseReferral(hub)
    hub.on_turn_start()
    hub.send(_on_list(signer))
    hub.send(config_update(signer, "n-001", {"review_request_due": True}))
    sends = persisted(hub, "client.message.request")
    assert any(s["payload"]["template"] == "review_request" for s in sends)


def test_REGRESSION_post_close_checkins_actually_implemented(tmp_path):
    """First-listed job component for this agent had zero code behind it
    until now - found while drafting the cadence config, not assumed."""
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke16AfterCloseReferral(hub)
    hub.on_turn_start()
    hub.send(_on_list(signer))
    txn = Envelope(from_agent="07", to_agent="16", intent="transaction.closed",
                  client_context_id="n-001", payload={"close_date": "2026-07-01"},
                  provenance={"source": "spoke-07", "captured_at": "runtime",
                              "verbatim_available": True})
    hub.send(txn)

    r1 = spoke.check_post_close_milestones("n-001", "2026-07-15")  # 14 days
    assert r1 == "none_due"
    r2 = spoke.check_post_close_milestones("n-001", "2026-07-31")  # 30 days
    assert r2 == "sent:30"
    r3 = spoke.check_post_close_milestones("n-001", "2026-08-01")  # still 30-day window, already sent
    assert r3 == "none_due"

    sends = persisted(hub, "client.message.request")
    assert any(s["payload"]["template"] == "post_close_checkin_30day" for s in sends)


def test_post_close_checkin_respects_touch_gate(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke16AfterCloseReferral(hub)
    hub.on_turn_start()
    hub.send(_on_list(signer))
    hub.send(config_update(signer, "n-001", {"opt_out": True}))
    txn = Envelope(from_agent="07", to_agent="16", intent="transaction.closed",
                  client_context_id="n-001", payload={"close_date": "2026-07-01"},
                  provenance={"source": "spoke-07", "captured_at": "runtime",
                              "verbatim_available": True})
    hub.send(txn)
    result = spoke.check_post_close_milestones("n-001", "2026-07-31")
    assert result == "blocked:opted_out"
    logs = persisted(hub, "interaction.log")
    assert any(l["payload"].get("kind") == "touch_blocked"
              and l["payload"].get("touch_type") == "post_close_checkin"
              and l["payload"].get("reason") == "opted_out"
              for l in logs)
