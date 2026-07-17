import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, "/home/claude/pillars_pth")

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.listing_spokes_12 import Spoke12MarketingCampaign
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


def asset_release(ctx, payload):
    return Envelope(from_agent="04", to_agent="12", intent="asset.release",
                    client_context_id=ctx, payload=payload,
                    provenance={"source": "spoke-04", "captured_at": "runtime",
                                "verbatim_available": True})


def status_update(ctx, status):
    return Envelope(from_agent="05", to_agent="12", intent="status.update",
                    client_context_id=ctx, payload={"status": status},
                    provenance={"source": "spoke-05", "captured_at": "runtime",
                                "verbatim_available": True})


def config_update(signer, ctx, payload):
    env = Envelope(from_agent="human", to_agent="12", intent="config.update",
                   client_context_id=ctx, payload=payload,
                   provenance={"source": "human", "captured_at": "runtime",
                               "verbatim_available": True})
    signer.sign(env)
    return env


def verdict(ctx, payload):
    return Envelope(from_agent="17", to_agent="12", intent="content.verdict",
                    client_context_id=ctx, payload=payload,
                    provenance={"source": "spoke-17", "captured_at": "runtime",
                                "verbatim_available": True})


def platform_event(ctx, payload):
    return Envelope(from_agent="external", to_agent="12", intent="platform.metrics",
                    client_context_id=ctx, payload=payload,
                    provenance={"source": "external", "captured_at": "runtime",
                                "verbatim_available": True})


def persisted(hub, intent=None):
    events = [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"]
    return [e for e in events if intent is None or e["intent"] == intent]


def test_asset_without_mls_confirmation_holds_ccp_gate(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke12MarketingCampaign(hub)
    hub.on_turn_start()
    hub.send(asset_release("m-001", {"draft": {"facts": []}}))
    assert not persisted(hub, "campaign.publish")


def test_asset_with_mls_confirmed_publishes(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke12MarketingCampaign(hub)
    hub.on_turn_start()
    hub.send(status_update("m-002", "active"))
    hub.send(asset_release("m-002", {"draft": {"facts": []}}))
    pub = persisted(hub, "campaign.publish")
    assert pub and pub[0]["to_agent"] == "external"


def test_exempt_status_with_disclosure_clears_gate(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke12MarketingCampaign(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "m-003", {"exempt_status": {"exempt": True,
                                                        "disclosure_on_file": True}}))
    hub.send(asset_release("m-003", {"draft": {"facts": []}}))
    assert persisted(hub, "campaign.publish")


def test_exempt_without_disclosure_still_holds(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke12MarketingCampaign(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "m-004", {"exempt_status": {"exempt": True,
                                                        "disclosure_on_file": False}}))
    hub.send(asset_release("m-004", {"draft": {"facts": []}}))
    assert not persisted(hub, "campaign.publish")


def test_asset_referencing_unconfirmed_data_pulled(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke12MarketingCampaign(hub)
    hub.on_turn_start()
    hub.send(status_update("m-005", "active"))
    hub.send(asset_release("m-005", {"draft": {}, "references_unconfirmed_data": True}))
    assert not persisted(hub, "campaign.publish")
    clar = persisted(hub, "clarification.request")
    assert any("outrun" in c["payload"]["reason"] for c in clar)


def test_demographic_targeting_refused_outright(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke12MarketingCampaign(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "m-006", {"new_campaign": {"targeting_demographic": True}}))
    assert hub.queues["escalation.legal_line"]
    assert not persisted(hub, "content.review")


def test_trending_topic_requires_human_approval_first(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke12MarketingCampaign(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "m-007", {"new_campaign": {"trending_topic_tie_in": True}}))
    clar = persisted(hub, "clarification.request")
    assert any("trending-topic" in c["payload"]["reason"] for c in clar)


def test_approved_verdict_publishes_only_with_ccp_clear(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke12MarketingCampaign(hub)
    hub.on_turn_start()
    hub.send(status_update("m-008", "active"))
    hub.send(config_update(signer, "m-008", {"new_campaign": {"body": "newsletter text"}}))
    hub.send(verdict("m-008", {"verdict": "approved"}))
    pub = persisted(hub, "campaign.publish")
    assert pub and pub[0]["payload"]["source"] == "self_written"


def test_approved_verdict_without_ccp_gate_holds(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke12MarketingCampaign(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "m-009", {"new_campaign": {"body": "newsletter text"}}))
    hub.send(verdict("m-009", {"verdict": "approved"}))
    assert not persisted(hub, "campaign.publish")


def test_flagged_verdict_never_publishes(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke12MarketingCampaign(hub)
    hub.on_turn_start()
    hub.send(status_update("m-010", "active"))
    hub.send(config_update(signer, "m-010", {"new_campaign": {"body": "text"}}))
    hub.send(verdict("m-010", {"verdict": "flagged", "findings": [{"phrase": "x"}]}))
    assert not persisted(hub, "campaign.publish")


def test_verbal_budget_change_ignored_signed_stands(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke12MarketingCampaign(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "m-011", {"signed_budget": 500}))
    hub.send(config_update(signer, "m-011", {"budget_change_verbal": 800}))
    assert spoke.signed_budgets["m-011"] == 500


def test_conflicting_engagement_sources_reports_both_named(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke12MarketingCampaign(hub)
    hub.on_turn_start()
    hub.send(platform_event("m-012", {"platform": "facebook", "engagement_value": 40}))
    hub.send(platform_event("m-012", {"platform": "ad_manager", "engagement_value": 65}))
    logs = persisted(hub, "interaction.log")
    assert any(l["payload"].get("kind") == "engagement_conflict" for l in logs)
    clar = persisted(hub, "clarification.request")
    assert any("conflict" in c["payload"]["reason"] for c in clar)


def test_engagement_spike_feeds_behavioral_signal_to_03(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke12MarketingCampaign(hub)
    hub.on_turn_start()
    hub.send(platform_event("m-013", {"platform": "facebook", "engagement_value": 80,
                                      "spike_threshold": 50}))
    sig = persisted(hub, "behavioral.signal")
    assert sig and sig[0]["to_agent"] == "03"


def test_platform_rejection_logged_never_tweaks_targeting(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke12MarketingCampaign(hub)
    hub.on_turn_start()
    hub.send(platform_event("m-014", {"event_kind": "rejection",
                                      "raw_rejection": "policy violation XYZ"}))
    logs = persisted(hub, "interaction.log")
    assert any(l["payload"].get("kind") == "platform_rejection" for l in logs)
    clar = persisted(hub, "clarification.request")
    assert any("policy violation XYZ" in c["payload"]["reason"] for c in clar)


def test_stale_asset_correction_unlocks_verdict_for_reentry(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke12MarketingCampaign(hub)
    hub.on_turn_start()
    hub.send(status_update("m-015", "active"))
    hub.send(config_update(signer, "m-015", {"new_campaign": {"body": "x"}}))
    hub.send(verdict("m-015", {"verdict": "approved"}))
    assert spoke.published["m-015"]["verdict_locked"] is True

    hub.send(platform_event("m-015", {"event_kind": "stale_detected",
                                      "delta": "price changed"}))
    assert spoke.published["m-015"]["verdict_locked"] is False
    logs = persisted(hub, "interaction.log")
    assert any(l["payload"].get("kind") == "stale_asset_corrected" for l in logs)


# ------------------------------------------- THE FIX: retract (tuple 9)
def test_stale_asset_retraction_actually_pulls_it_down(tmp_path):
    """Was: only 'correct' existed - no path ever actually retracted a
    stale asset, despite the tuple offering it as a real choice."""
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke12MarketingCampaign(hub)
    hub.on_turn_start()
    hub.send(status_update("m-016", "active"))
    hub.send(config_update(signer, "m-016", {"new_campaign": {"body": "x"}}))
    hub.send(verdict("m-016", {"verdict": "approved"}))

    hub.send(platform_event("m-016", {"event_kind": "stale_detected",
                                      "delta": "listing withdrawn",
                                      "retract_requested": True}))
    assert spoke.published["m-016"].get("retracted") is True
    assert spoke.published["m-016"]["verdict_locked"] is True, \
        "a retraction isn't a correction - verdict_locked should be untouched"
    publishes = persisted(hub, "campaign.publish")
    assert any(p["payload"].get("action") == "retract" for p in publishes)
    logs = persisted(hub, "interaction.log")
    assert any(l["payload"].get("kind") == "stale_asset_retracted" for l in logs)


def test_REGRESSION_approved_campaign_publishes_once_gate_clears_later(tmp_path):
    """Real bug found on re-review: an approved-but-gated campaign was
    popped from tracking and never stored anywhere else - it would never
    publish even after MLS confirmation arrived later."""
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke12MarketingCampaign(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "m-016", {"new_campaign": {"body": "newsletter"}}))
    hub.send(verdict("m-016", {"verdict": "approved"}))
    assert not persisted(hub, "campaign.publish")
    assert "m-016" in spoke.approved_awaiting_ccp

    hub.send(status_update("m-016", "active"))
    pub = persisted(hub, "campaign.publish")
    assert pub and pub[0]["payload"]["source"] == "self_written"
    assert "m-016" not in spoke.approved_awaiting_ccp


def test_REGRESSION_asset_from_04_publishes_once_gate_clears_later(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke12MarketingCampaign(hub)
    hub.on_turn_start()
    hub.send(asset_release("m-017", {"draft": {"facts": []}}))
    assert not persisted(hub, "campaign.publish")
    assert "m-017" in spoke.approved_awaiting_ccp

    hub.send(config_update(signer, "m-017", {"exempt_status": {
        "exempt": True, "disclosure_on_file": True}}))
    pub = persisted(hub, "campaign.publish")
    assert pub and pub[0]["payload"]["source"] == "04_asset"


def test_REGRESSION_platform_truncation_goes_back_through_17(tmp_path):
    """tuple 1 was listed as 'implemented' in the docstring but had zero
    actual code - found on re-review."""
    hub, signer = make_hub(str(tmp_path))
    Spoke12MarketingCampaign(hub)
    hub.on_turn_start()
    hub.send(platform_event("m-018", {"event_kind": "truncated",
                                      "truncated_content": "cut off text..."}))
    review = persisted(hub, "content.review")
    assert review and review[0]["to_agent"] == "17"
    assert "truncation" in review[0]["payload"]["reason"]
