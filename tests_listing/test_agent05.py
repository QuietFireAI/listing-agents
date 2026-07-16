import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, "/home/claude/pillars_pth")

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
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


def authorized_change(signer, ctx, change):
    env = Envelope(from_agent="human", to_agent="05",
                  intent="listing.change.authorized",
                  client_context_id=ctx, payload=change,
                  provenance={"source": "human", "captured_at": "runtime",
                              "verbatim_available": True})
    signer.sign(env)
    return env


def persisted(hub, intent=None):
    events = [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"]
    return [e for e in events if intent is None or e["intent"] == intent]


def test_unsigned_change_never_reaches_the_handler(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke05MLSListingManagement(hub)
    hub.on_turn_start()
    env = Envelope(from_agent="human", to_agent="05",
                  intent="listing.change.authorized",
                  client_context_id="p-001",
                  payload={"field": "status", "value": "sold"},
                  provenance={"source": "human", "captured_at": "runtime",
                              "verbatim_available": True})
    # no signature at all
    result = hub.send(env)
    assert result["status"] == "reject"


def test_status_change_requires_artifact_first(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke05MLSListingManagement(hub)
    hub.on_turn_start()
    hub.send(authorized_change(signer, "p-002",
                               {"field": "status", "value": "sold"}))
    clar = persisted(hub, "clarification.request")
    assert any("closing_artifact" in c["payload"]["reason"] for c in clar)


def test_status_change_with_artifact_executes(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke05MLSListingManagement(hub)
    hub.on_turn_start()
    hub.send(authorized_change(signer, "p-003",
                               {"field": "status", "value": "sold",
                                "closing_artifact": "closing-doc-123"}))
    assert spoke.mls_records["p-003"]["status"] == "sold"
    updates = persisted(hub, "status.update")
    assert {u["to_agent"] for u in updates} == {"11", "12", "14"}


def test_withdrawal_while_under_contract_halts(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke05MLSListingManagement(hub)
    hub.on_turn_start()
    hub.send(authorized_change(signer, "p-004",
                               {"field": "status", "value": "pending",
                                "signed_contract_artifact": "contract-1"}))
    hub.send(authorized_change(signer, "p-004",
                               {"field": "status", "value": "withdrawn"}))
    assert hub.queues["escalation.legal_line"]
    assert spoke.mls_records["p-004"]["status"] == "pending"  # never changed


def test_comp_offer_field_always_refused(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke05MLSListingManagement(hub)
    hub.on_turn_start()
    hub.send(authorized_change(signer, "p-005",
                               {"field": "buyer_broker_comp", "value": "2.5%"}))
    assert hub.queues["escalation.legal_line"]
    assert any("8/17/24" in e["trigger"] for e in hub.queues["escalation.legal_line"])


def test_price_conflicts_with_agreement_amendment_halts(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke05MLSListingManagement(hub)
    hub.on_turn_start()
    hub.send(authorized_change(signer, "p-006",
                               {"field": "price", "value": 500_000,
                                "agreement_amendment_price": 495_000}))
    clar = persisted(hub, "clarification.request")
    assert any("agreement amendment" in c["payload"]["reason"] for c in clar)
    assert "p-006" not in spoke.mls_records or "price" not in spoke.mls_records.get("p-006", {})


def test_price_no_conflict_executes(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke05MLSListingManagement(hub)
    hub.on_turn_start()
    hub.send(authorized_change(signer, "p-007", {"field": "price", "value": 500_000}))
    assert spoke.mls_records["p-007"]["price"] == 500_000


def test_invalid_mls_field_clarifies_never_approximates(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke05MLSListingManagement(hub)
    hub.on_turn_start()
    hub.send(authorized_change(signer, "p-008", {"field": "garage_spaces", "value": 2}))
    clar = persisted(hub, "clarification.request")
    assert any("no valid MLS field" in c["payload"]["reason"] for c in clar)


def test_new_listing_intake_stores_data_and_orders_photography(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke05MLSListingManagement(hub)
    hub.on_turn_start()
    hub.send(authorized_change(signer, "p-009", {
        "new_listing": {"beds": 3, "baths": 2, "sqft": 1800, "price": 450_000},
        "authorize_go_live": True,
    }))
    assert spoke.property_data["p-009"]["beds"] == 3
    assert "p-009" in spoke.go_live_authorized
    vendor = persisted(hub, "vendor.request")
    assert vendor and vendor[0]["to_agent"] == "09"


def test_unverified_photo_deliverable_does_not_clear_gate(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke05MLSListingManagement(hub)
    hub.on_turn_start()
    env = Envelope(from_agent="09", to_agent="05", intent="deliverable.release",
                  client_context_id="p-010", payload={"photos": ["a.jpg"]},
                  provenance={"source": "spoke-09", "captured_at": "runtime",
                              "verbatim_available": True})
    hub.send(env)
    assert not persisted(hub, "listing.data")
    clar = persisted(hub, "clarification.request")
    assert any("open-verification" in c["payload"]["reason"] for c in clar)


def test_verified_photo_deliverable_sends_listing_data_to_04(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke05MLSListingManagement(hub)
    hub.on_turn_start()
    hub.send(authorized_change(signer, "p-011", {
        "new_listing": {"beds": 4, "sqft": 2200}, "authorize_go_live": True}))
    env = Envelope(from_agent="09", to_agent="05", intent="deliverable.release",
                  client_context_id="p-011",
                  payload={"photos": ["a.jpg"], "verified_openable": True},
                  provenance={"source": "spoke-09", "captured_at": "runtime",
                              "verbatim_available": True})
    hub.send(env)
    ld = persisted(hub, "listing.data")
    assert ld and ld[0]["to_agent"] == "04"
    assert ld[0]["payload"]["beds"] == 4


def test_approved_asset_with_go_live_authorization_goes_live_and_feeds_13(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke05MLSListingManagement(hub)
    hub.on_turn_start()
    hub.send(authorized_change(signer, "p-012", {
        "new_listing": {"beds": 3}, "authorize_go_live": True}))
    env = Envelope(from_agent="04", to_agent="05", intent="asset.release",
                  client_context_id="p-012", payload={"draft": {"facts": []}},
                  provenance={"source": "spoke-04", "captured_at": "runtime",
                              "verbatim_available": True})
    hub.send(env)
    assert spoke.mls_records["p-012"]["status"] == "active"
    updates = persisted(hub, "status.update")
    assert {u["to_agent"] for u in updates} == {"11", "12", "14"}
    ld = persisted(hub, "listing.data")
    assert any(e["to_agent"] == "13" for e in ld)


def test_approved_asset_without_go_live_authorization_holds_at_entry(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke05MLSListingManagement(hub)
    hub.on_turn_start()
    env = Envelope(from_agent="04", to_agent="05", intent="asset.release",
                  client_context_id="p-013", payload={"draft": {"facts": []}},
                  provenance={"source": "spoke-04", "captured_at": "runtime",
                              "verbatim_available": True})
    hub.send(env)
    assert spoke.mls_records.get("p-013", {}).get("status") != "active"
    assert not persisted(hub, "status.update")
