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


def authorized_change(signer, ctx, change, authorizing_identity="human"):
    env = Envelope(from_agent="human", to_agent="05",
                  intent="listing.change.authorized",
                  client_context_id=ctx, payload=change,
                  provenance={"source": authorizing_identity,
                              "captured_at": "runtime",
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


# ------------------------------------------- THE FIX: conflicting changes
def test_conflicting_authorized_changes_from_different_signers_holds(tmp_path):
    """Tuple 3: two authorized changes conflict -> execute neither, human.
    Was: no conflict detection existed at all - any two signed changes for
    the same field just both executed in sequence, last write wins."""
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke05MLSListingManagement(hub)
    hub.on_turn_start()
    ctx = "p-020"
    hub.send(authorized_change(signer, ctx, {"field": "price", "value": 500_000},
                               authorizing_identity="agent-a"))
    assert spoke.mls_records[ctx]["price"] == 500_000

    hub.send(authorized_change(signer, ctx, {"field": "price", "value": 510_000},
                               authorizing_identity="agent-b"))
    clar = persisted(hub, "clarification.request")
    assert any("conflict" in c["payload"]["reason"] for c in clar)
    # neither the new value executed, nor was the prior value disturbed
    assert spoke.mls_records[ctx]["price"] == 500_000


def test_same_signer_updating_own_prior_change_is_not_a_conflict(tmp_path):
    """The same authorized party correcting their own prior instruction is
    a normal update, not tuple 3's conflict - distinguishing this was the
    actual design decision, not just detecting any two differing values."""
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke05MLSListingManagement(hub)
    hub.on_turn_start()
    ctx = "p-021"
    hub.send(authorized_change(signer, ctx, {"field": "price", "value": 500_000},
                               authorizing_identity="agent-a"))
    hub.send(authorized_change(signer, ctx, {"field": "price", "value": 505_000},
                               authorizing_identity="agent-a"))
    assert spoke.mls_records[ctx]["price"] == 505_000
    assert not any("conflict" in c["payload"].get("reason", "")
                  for c in persisted(hub, "clarification.request"))


# --------------------------------- THE FIX: corrected listing.data to 04
def test_asset_mls_discrepancy_sends_corrected_listing_data_to_04(tmp_path):
    """Tuple 10: MLS record is truth, corrected version sent, discrepancy
    logged. Was: discrepancy detected and logged, but nothing corrected
    ever went anywhere - '05 is truth' had no downstream effect."""
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke05MLSListingManagement(hub)
    hub.on_turn_start()
    ctx = "p-022"
    hub.send(authorized_change(signer, ctx,
                               {"new_listing": {"sqft": 1800, "beds": 3}}))
    spoke.mls_records[ctx]["sqft"] = 1800

    env = Envelope(from_agent="04", to_agent="05", intent="asset.release",
                  client_context_id=ctx,
                  payload={"draft": {"facts": [{"text": "1750 sq ft",
                                                "attribution": "per seller",
                                                "adjective": False}]}},
                  provenance={"source": "spoke-04", "captured_at": "runtime",
                              "verbatim_available": True})
    hub.send(env)

    corrections = [e for e in persisted(hub, "listing.data")
                   if e["to_agent"] == "04"]
    assert corrections, "05 must send 04 a corrected listing.data, not just log it"
    assert corrections[0]["payload"]["sqft"] == 1800


def test_go_live_sends_listing_data_to_market_data_agent_10(tmp_path):
    """Edge-table gap: SKILL.md documented 05 -> 10 listing.data all along;
    the code never actually sent it."""
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke05MLSListingManagement(hub)
    hub.on_turn_start()
    ctx = "p-023"
    hub.send(authorized_change(signer, ctx,
                               {"new_listing": {"sqft": 2000},
                                "authorize_go_live": True}))
    env = Envelope(from_agent="04", to_agent="05", intent="asset.release",
                  client_context_id=ctx, payload={"draft": {"facts": []}},
                  provenance={"source": "spoke-04", "captured_at": "runtime",
                              "verbatim_available": True})
    hub.send(env)
    to_10 = [e for e in persisted(hub, "listing.data") if e["to_agent"] == "10"]
    assert to_10, "Market Data (10) must receive listing.data on go-live"


# ------------------------------- THE FIX: withdrawal reaches 17 AND human
def test_withdrawal_under_contract_reaches_both_compliance_and_human(tmp_path):
    """Tuple 11 literally says '17 + human' - was: only escalation.legal_line
    (human queue) fired; 05 had no legal route to Compliance (17) at all."""
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke05MLSListingManagement(hub)
    hub.on_turn_start()
    ctx = "p-024"
    spoke.under_contract.add(ctx)
    hub.send(authorized_change(signer, ctx,
                               {"field": "status", "value": "withdrawn"}))
    assert hub.queues["escalation.legal_line"]
    notices = persisted(hub, "compliance.notice")
    assert any(e["to_agent"] == "17" for e in notices), \
        "withdrawal-under-contract must also reach Compliance (17), not just human"

