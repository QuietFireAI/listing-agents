import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.listing_spokes_19 import Spoke19Prospecting
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
    env = Envelope(from_agent="human", to_agent="19", intent="config.update",
                   client_context_id=ctx, payload=payload,
                   provenance={"source": "human", "captured_at": "runtime",
                               "verbatim_available": True})
    signer.sign(env)
    return env


def discovery(ctx, payload):
    return Envelope(from_agent="external", to_agent="19", intent="discovery.feed",
                    client_context_id=ctx, payload=payload,
                    provenance={"source": "external", "captured_at": "runtime",
                                "verbatim_available": True})


def persisted(hub, intent=None):
    events = [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"]
    return [e for e in events if intent is None or e["intent"] == intent]


def _setup_zip(signer):
    return config_update(signer, "setup", {"zip_codes": ["44811"]})


def test_outside_monitored_zip_ignored(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke19Prospecting(hub)
    hub.on_turn_start()
    hub.send(_setup_zip(signer))
    hub.send(discovery("d-001", {"listing_id": "L1", "zip_code": "99999",
                                 "status": "new"}))
    assert not persisted(hub, "prospect.opportunity")


def test_new_listing_in_monitored_zip_delivered_to_human(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke19Prospecting(hub)
    hub.on_turn_start()
    hub.send(_setup_zip(signer))
    hub.send(discovery("d-002", {"listing_id": "L2", "zip_code": "44811",
                                 "status": "new", "source": "mls_feed"}))
    opps = persisted(hub, "prospect.opportunity")
    assert opps and opps[0]["to_agent"] == "human"


def test_unapproved_source_for_expired_excluded_and_flagged(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke19Prospecting(hub)
    hub.on_turn_start()
    hub.send(_setup_zip(signer))
    hub.send(discovery("d-003", {"listing_id": "L3", "zip_code": "44811",
                                 "status": "expired", "source": "scraper_x"}))
    assert not persisted(hub, "prospect.opportunity")
    clar = persisted(hub, "clarification.request")
    assert any("not on the approved" in c["payload"]["reason"] for c in clar)


def test_approved_source_for_expired_proceeds_with_legal_gate(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke19Prospecting(hub)
    hub.on_turn_start()
    hub.send(_setup_zip(signer))
    hub.send(config_update(signer, "setup", {"approved_sources": ["mls_expired_feed"]}))
    hub.send(discovery("d-004", {"listing_id": "L4", "zip_code": "44811",
                                 "status": "expired", "source": "mls_expired_feed"}))
    assert persisted(hub, "prospect.opportunity")
    assert any("expired listing surfaced" in e["trigger"]
              for e in hub.queues["escalation.legal_line"])


def test_dnc_match_suppresses_and_logs(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke19Prospecting(hub)
    hub.on_turn_start()
    hub.send(_setup_zip(signer))
    hub.send(config_update(signer, "setup", {"dnc_entries": ["555-0100"]}))
    hub.send(discovery("d-005", {"listing_id": "L5", "zip_code": "44811",
                                 "status": "new", "source": "mls_feed",
                                 "owner_contact": "555-0100"}))
    logs = persisted(hub, "interaction.log")
    assert any(l["payload"].get("kind") == "suppressed_by_rule" for l in logs)
    assert spoke.opportunities["L5"]["dnc_status"] == "on_dnc"


def test_unclear_representation_status_marked_unknown(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke19Prospecting(hub)
    hub.on_turn_start()
    hub.send(_setup_zip(signer))
    hub.send(discovery("d-006", {"listing_id": "L6", "zip_code": "44811",
                                 "status": "new", "source": "mls_feed",
                                 "representation_status": "unclear_garbage"}))
    assert spoke.opportunities["L6"]["representation_status"] == "unknown"


def test_expired_relisted_with_broker_closes_opportunity(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke19Prospecting(hub)
    hub.on_turn_start()
    hub.send(_setup_zip(signer))
    hub.send(config_update(signer, "setup", {"approved_sources": ["mls_expired_feed"]}))
    hub.send(discovery("d-007", {"listing_id": "L7", "zip_code": "44811",
                                 "status": "expired", "source": "mls_expired_feed"}))
    hub.send(discovery("d-007b", {"listing_id": "L7", "zip_code": "44811",
                                  "status": "expired", "source": "mls_expired_feed",
                                  "relisted_with_broker": True}))
    assert spoke.opportunities["L7"]["status_at_retrieval"] == "closed_relisted"


def test_matches_existing_client_context_flags_relationship(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke19Prospecting(hub)
    hub.on_turn_start()
    hub.send(_setup_zip(signer))
    hub.send(config_update(signer, "setup", {"known_client_contexts": ["existing-client-1"]}))
    hub.send(discovery("d-008", {"listing_id": "L8", "zip_code": "44811",
                                 "status": "new", "source": "mls_feed",
                                 "matches_client_context": "existing-client-1"}))
    clar = persisted(hub, "clarification.request")
    assert any("no outreach implication" in c["payload"]["reason"] for c in clar)


def test_ranking_below_threshold_presents_unranked(tmp_path):
    """tuple 9 was in the docstring with zero code behind it - found and
    fixed during this build's own deep pass, not after being asked twice."""
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke19Prospecting(hub)
    hub.on_turn_start()
    hub.send(_setup_zip(signer))
    hub.send(discovery("d-009", {"listing_id": "L9", "zip_code": "44811",
                                 "status": "new", "source": "mls_feed",
                                 "rank_score": 0.9, "rank_basis_strength": 0.2,
                                 "rank_threshold": 0.5}))
    assert spoke.opportunities["L9"]["ranked"] is False
    assert "rank_score" not in spoke.opportunities["L9"]


def test_ranking_above_threshold_included(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke19Prospecting(hub)
    hub.on_turn_start()
    hub.send(_setup_zip(signer))
    hub.send(discovery("d-010", {"listing_id": "L10", "zip_code": "44811",
                                 "status": "new", "source": "mls_feed",
                                 "rank_score": 0.9, "rank_basis_strength": 0.8,
                                 "rank_threshold": 0.5}))
    assert spoke.opportunities["L10"]["ranked"] is True
    assert spoke.opportunities["L10"]["rank_score"] == 0.9


def test_farm_data_individual_level_refused(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke19Prospecting(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "d-011", {"farm_data_request": {
        "individual_level": True}}))
    assert any("individual household-level" in e["trigger"]
              for e in hub.queues["escalation.legal_line"])
    assert not persisted(hub, "data.request")


def test_farm_data_aggregate_level_requests_neighborhood_package(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke19Prospecting(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "d-012", {"farm_data_request": {
        "individual_level": False}}))
    req = persisted(hub, "data.request")
    assert req and req[0]["payload"]["mode"] == "neighborhood"


def test_bulk_outreach_instruction_never_triggers_actual_outreach(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke19Prospecting(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "d-013", {"bulk_outreach_instruction": "contact everyone"}))
    sent_intents = {e["intent"] for e in persisted(hub)}
    assert sent_intents <= {"config.update"}


def test_request_market_context_sends_data_request(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke19Prospecting(hub)
    hub.on_turn_start()
    hub.send(_setup_zip(signer))
    hub.send(discovery("d-014", {"listing_id": "L14", "zip_code": "44811",
                                 "status": "new", "source": "mls_feed"}))
    spoke.request_market_context("L14")
    req = persisted(hub, "data.request")
    assert req and req[-1]["to_agent"] == "10"


def test_REGRESSION_property_data_forwarded_enables_real_matching_in_13(tmp_path):
    """Real bug found on re-review: prospect.opportunity only ever carried
    meta/compliance fields, never actual property data (price, area, etc.)
    - verified directly that a match could "succeed" with zero real price
    comparison against a buyer's budget. Fixed to forward actual property
    facts, verbatim, never invented. Proven here with a genuine
    cross-agent integration: an over-budget property in the buyer's
    target area must now correctly trigger 13's real conflict-check."""
    import sys
    sys.path.insert(0, os.path.dirname(__file__) + "/..")
    from dispatcher.listing_spokes_13 import Spoke13BuyerSearchMatch

    hub, signer = make_hub(str(tmp_path))
    Spoke19Prospecting(hub)
    p13 = Spoke13BuyerSearchMatch(hub)
    hub.on_turn_start()
    hub.send(_setup_zip(signer))
    p13.buyer_criteria["buyer-1"] = [
        {"field": "budget", "value": 500000, "hard": True, "source": "stated_by_party"},
        {"field": "area", "value": "downtown", "hard": True, "source": "stated_by_party"}]

    hub.send(discovery("d-015", {"listing_id": "L15", "zip_code": "44811",
                                 "status": "new", "source": "mls_feed",
                                 "buyer_profile_match_ctx": "buyer-1",
                                 "price": 700000, "area": "downtown"}))

    clar = persisted(hub, "clarification.request")
    assert any("exceeds stated budget" in c["payload"]["reason"] for c in clar)
    assert p13.match_history.get("buyer-1") is None
