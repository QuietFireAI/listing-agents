"""Mutation-hardening — spoke 05 (MLS listing management), remaining gap.

Fable covers 05:312. This closes 05:203 (the VALID_MLS_FIELDS allowlist that
filters a new-listing package into the draft record). Kill-verified.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.signatures import Ed25519Signer, Ed25519Verifier
from dispatcher.listing_spokes_05 import Spoke05MLSListingManagement

IDENTITY_ROUTES = os.path.join(os.path.dirname(__file__), "..", "identity",
                               "routes.json")


def make_hub(tmp_path):
    signer = Ed25519Signer()
    verifier = Ed25519Verifier(signer.public_key_bytes())
    return Hub(Routes(IDENTITY_ROUTES),
               AuditLog(os.path.join(tmp_path, "a.jsonl")),
               signature_verifier=verifier.verifier()), signer


def test_05_new_listing_draft_keeps_valid_fields_drops_invalid(tmp_path):
    """05:203 - the draft MLS record is built ONLY from VALID_MLS_FIELDS.
    A valid field (beds) must be carried; an invalid/injected field
    (evil_field) must be dropped. Kills the `k in VALID_MLS_FIELDS` flip:
    flipped to `not in`, valid fields drop and junk carries."""
    hub, signer = make_hub(str(tmp_path))
    s = Spoke05MLSListingManagement(hub)
    hub.on_turn_start()
    e = Envelope(from_agent="human", to_agent="05",
                 intent="listing.change.authorized", client_context_id="nl-1",
                 payload={"new_listing": {
                     "beds": 3, "baths": 2, "price": 500000,
                     "evil_field": "should_be_dropped",
                     "internal_note": "not an MLS field"},
                     "signed_contract_artifact": "c-1"},
                 provenance={"source": "human", "captured_at": "t",
                             "verbatim_available": True})
    signer.sign(e)
    hub.send(e)
    rec = s.mls_records["nl-1"]
    assert rec["beds"] == 3, "a valid MLS field must be carried into the draft"
    assert rec["price"] == 500000
    assert "evil_field" not in rec, \
        "a non-MLS field must be dropped (kills the allowlist flip)"
    assert "internal_note" not in rec, "junk fields must not enter the record"
