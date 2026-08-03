"""Mutation-hardening — spoke 15 (financial tracking), remaining gaps.

Fable covers 15:152 and 15:276. This closes 15:81 (signed_docs_only ->
commission projection vs signed) and 15:221 (receipt_on_file -> expense
verified). Kill-verified.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.signatures import Ed25519Signer, Ed25519Verifier
from dispatcher.listing_spokes_15 import Spoke15FinancialTracking

IDENTITY_ROUTES = os.path.join(os.path.dirname(__file__), "..", "identity",
                               "routes.json")


def make_hub(tmp_path):
    signer = Ed25519Signer()
    verifier = Ed25519Verifier(signer.public_key_bytes())
    hub = Hub(Routes(IDENTITY_ROUTES),
              AuditLog(os.path.join(tmp_path, "a.jsonl")),
              signature_verifier=verifier.verifier())
    return hub, signer


def spoke(tmp_path):
    hub, signer = make_hub(tmp_path)
    return Spoke15FinancialTracking(hub), hub, signer


def config(signer, ctx, payload):
    e = Envelope(from_agent="human", to_agent="15", intent="config.update",
                 client_context_id=ctx, payload=payload,
                 provenance={"source": "human", "captured_at": "t",
                             "verbatim_available": True})
    signer.sign(e)
    return e


def closed(ctx, payload):
    return Envelope(from_agent="07", to_agent="15", intent="transaction.closed",
                    client_context_id=ctx, payload=payload,
                    provenance={"source": "t"})


def report(ctx, payload):
    return Envelope(from_agent="14", to_agent="15", intent="report.package",
                    client_context_id=ctx, payload=payload,
                    provenance={"source": "t"})


# ------------------------------------------------------------ 81
def test_15_unsigned_close_is_projection_signed_is_final(tmp_path):
    """transaction.closed with signed_docs_only True records a SIGNED
    commission; absent/False records a PROJECTION labeled unsigned. Kills
    the 81 default flip (False -> True would mark unsigned closes as final)."""
    s, hub, signer = spoke(str(tmp_path))
    hub.on_turn_start()
    s.handle(closed("c-signed", {"commission_amount": 15000,
                                 "signed_docs_only": True}))
    assert s.commissions["c-signed"]["signed"] is True
    assert "source" in s.commissions["c-signed"], \
        "a signed close records a final commission"

    s.handle(closed("c-unsigned", {"commission_amount": 15000}))  # absent
    rec = s.commissions["c-unsigned"]
    assert rec["signed"] is False, \
        "an unsigned close must be marked signed=False (kills the default flip)"
    assert "projection" in rec["labeled"], \
        "an unsigned commission must be labeled a projection"


# ------------------------------------------------------------ 221
def test_15_expense_verified_only_with_receipt(tmp_path):
    """An expense with receipt_on_file True is verified; absent/False is
    unverified. Kills the 221 default flip."""
    s, hub, signer = spoke(str(tmp_path))
    hub.on_turn_start()
    hub.send(config(signer, "e-1", {"add_expense": {
        "amount": 200, "categories": ["marketing"],
        "receipt_on_file": True}}))
    assert s.expenses["e-1"][0]["verified"] is True, \
        "a receipted expense must be verified"

    hub.send(config(signer, "e-2", {"add_expense": {
        "amount": 300, "categories": ["marketing"]}}))  # no receipt
    assert s.expenses["e-2"][0]["verified"] is False, \
        "an expense without a receipt must be unverified (kills default flip)"
