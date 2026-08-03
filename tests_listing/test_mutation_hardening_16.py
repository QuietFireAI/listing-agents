"""Mutation-hardening — spoke 16 (after-close referral), remaining gap.

Fable covers 16:246. This closes 16:283 (_NEW_BUSINESS_WORDS detection routing
a new lead.captured to 02). Kill-verified.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.signatures import Ed25519Signer, Ed25519Verifier
from dispatcher.listing_spokes_16 import Spoke16AfterCloseReferral
from dispatcher.listing_spokes_02 import Spoke02LeadQualification

IDENTITY_ROUTES = os.path.join(os.path.dirname(__file__), "..", "identity",
                               "routes.json")


def make_hub(tmp_path):
    signer = Ed25519Signer()
    verifier = Ed25519Verifier(signer.public_key_bytes())
    return Hub(Routes(IDENTITY_ROUTES),
               AuditLog(os.path.join(tmp_path, "a.jsonl")),
               signature_verifier=verifier.verifier())


def lead_reply(ctx, message):
    return Envelope(from_agent="11", to_agent="16", intent="lead.reply",
                    client_context_id=ctx,
                    payload={"message": message, "name": "Past Client"},
                    provenance={"source": "t"})


def persisted(hub, intent=None):
    return [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"
            and (intent is None or e["intent"] == intent)]


def test_16_new_business_signal_routes_new_lead_plain_does_not(tmp_path):
    """A past client signaling new business ('thinking of selling') routes a
    NEW lead.captured to 02 under a new context; a plain thank-you does not.
    Kills the 283 `w in message` flip."""
    hub = make_hub(str(tmp_path))
    Spoke16AfterCloseReferral(hub)
    hub.on_turn_start()
    hub.send(lead_reply("nb-1", "we're thinking of selling next spring"))
    captured = [e for e in persisted(hub, "lead.captured")
                if e["from_agent"] == "16"]
    assert captured, \
        "a new-business signal must route a new lead.captured to 02"
    assert captured[0]["payload"]["source"] == "past_client_referral_signal"
    assert captured[0]["payload"]["original_context"] == "nb-1"

    hub2 = make_hub(str(tmp_path) + "_b")
    Spoke16AfterCloseReferral(hub2)
    hub2.on_turn_start()
    hub2.send(lead_reply("nb-2", "thanks so much for everything!"))
    assert not [e for e in persisted(hub2, "lead.captured")
                if e["from_agent"] == "16"], \
        "a plain thank-you must NOT route a new lead (kills the flip)"
