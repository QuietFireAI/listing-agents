"""Mutation-hardening — spoke 10 (market data), remaining gap.

Closes 10:130 (appraisal-substitution-smell detection). Kill-verified.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.signatures import Ed25519Signer, Ed25519Verifier
from dispatcher.listing_spokes_10 import Spoke10MarketData

IDENTITY_ROUTES = os.path.join(os.path.dirname(__file__), "..", "identity",
                               "routes.json")


def make_hub(tmp_path):
    signer = Ed25519Signer()
    verifier = Ed25519Verifier(signer.public_key_bytes())
    return Hub(Routes(IDENTITY_ROUTES),
               AuditLog(os.path.join(tmp_path, "a.jsonl")),
               signature_verifier=verifier.verifier())


def spoke(tmp_path):
    return Spoke10MarketData(make_hub(tmp_path))


def data_request(frm, ctx, message):
    comps = [{"address": f"{i} Main", "sold_price": 400000 + i * 1000,
              "source": "mls", "retrieval_date": "2026-08-01"}
             for i in range(6)]
    return Envelope(from_agent=frm, to_agent="10", intent="data.request",
                    client_context_id=ctx,
                    payload={"message": message, "mode": "comp",
                             "comps": comps, "license_scope": "internal"},
                    provenance={"source": "t"})


def persisted(hub, intent=None):
    return [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"
            and (intent is None or e["intent"] == intent)]


def test_10_appraisal_substitution_smell_triggers_notice(tmp_path):
    """A data request phrased as an appraisal-substitution ('what's it
    actually worth') triggers a compliance.notice to 17 and a
    not_an_appraisal_note on the package; a plain comp request does not.
    Kills the 130 `w in message` flip."""
    s = spoke(str(tmp_path))
    s.hub.on_turn_start()
    s.handle(data_request("11", "sm-1", "what's it actually worth"))
    notices = [e for e in persisted(s.hub, "compliance.notice")
               if "appraisal substitution" in str(e["payload"].get("trigger", ""))]
    assert notices, \
        "an appraisal-substitution phrasing must raise a compliance.notice"

    s2 = spoke(str(tmp_path) + "_b")
    s2.hub.on_turn_start()
    s2.handle(data_request("11", "sm-2", "please send recent comps"))
    notices2 = [e for e in persisted(s2.hub, "compliance.notice")
                if "appraisal substitution" in str(e["payload"].get("trigger", ""))]
    assert not notices2, \
        "a plain comp request must NOT raise the substitution notice"
