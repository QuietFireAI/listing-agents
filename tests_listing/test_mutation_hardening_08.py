"""Mutation-hardening — spoke 08 (document collection), remaining gaps.

Fable covers 08:176. This closes 08:101 (wire-quarantine return) and 08:370
(signed_docs_only passthrough on the settlement statement). Kill-verified.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.signatures import Ed25519Signer, Ed25519Verifier
from dispatcher.listing_spokes_08 import Spoke08DocumentCollection

IDENTITY_ROUTES = os.path.join(os.path.dirname(__file__), "..", "identity",
                               "routes.json")


def make_hub(tmp_path):
    signer = Ed25519Signer()
    verifier = Ed25519Verifier(signer.public_key_bytes())
    return Hub(Routes(IDENTITY_ROUTES),
               AuditLog(os.path.join(tmp_path, "a.jsonl")),
               signature_verifier=verifier.verifier())


def spoke(tmp_path):
    return Spoke08DocumentCollection(make_hub(tmp_path))


def env(intent, ctx, payload):
    return Envelope(from_agent="11", to_agent="08", intent=intent,
                    client_context_id=ctx, payload=payload,
                    provenance={"source": "t"})


def persisted(hub, intent=None):
    return [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"
            and (intent is None or e["intent"] == intent)]


def legal(hub):
    return hub.queues.get("escalation.legal_line", [])


# ------------------------------------------------------------ 101
def test_08_wire_instructions_in_document_quarantine(tmp_path):
    """_wire_check returns True and escalates when a document carries wire
    instructions; False and no escalation on a clean document."""
    s = spoke(str(tmp_path))
    hit = s._wire_check(
        {"body": "here are the wire transfer instructions for closing"}, "w-1")
    assert hit is True
    assert any("wire instructions" in e.get("trigger", "") for e in legal(s.hub)), \
        "a document with wire instructions must be quarantined/escalated"

    s2 = spoke(str(tmp_path) + "_b")
    clean = s2._wire_check({"body": "the signed disclosure is attached"}, "w-2")
    assert clean is False
    assert not legal(s2.hub), "a clean document must not escalate"


# ------------------------------------------------------------ 370
def test_08_settlement_signed_flag_passthrough_and_default(tmp_path):
    """The settlement statement's signed_docs_only rides from the input
    `signed` flag; absent, it defaults to False. Kills the 370 default flip."""
    s = spoke(str(tmp_path))
    s.expected_senders["st-1"] = {"closing_settlement_statement": {"title_co"}}
    s.hub.on_turn_start()
    s.handle(env("document.submission", "st-1",
                 {"doc_type": "closing_settlement_statement",
                  "submitting_party": "title_co",
                  "opens_correctly": True, "content_hash": "h1",
                  "sale_price": 500000, "commission_amount": 15000,
                  "signed": True}))
    ds = [e for e in persisted(s.hub, "doc.status")
          if e["payload"].get("doc_type") == "closing_settlement_statement"]
    assert ds and ds[0]["payload"]["signed_docs_only"] is True, \
        "an input signed=True must pass through as signed_docs_only=True"

    s2 = spoke(str(tmp_path) + "_b")
    s2.expected_senders["st-2"] = {"closing_settlement_statement": {"title_co"}}
    s2.hub.on_turn_start()
    s2.handle(env("document.submission", "st-2",
                  {"doc_type": "closing_settlement_statement",
                   "submitting_party": "title_co",
                   "opens_correctly": True, "content_hash": "h2",
                   "sale_price": 500000, "commission_amount": 15000}))
    ds2 = [e for e in persisted(s2.hub, "doc.status")
           if e["payload"].get("doc_type") == "closing_settlement_statement"]
    assert ds2 and ds2[0]["payload"]["signed_docs_only"] is False, \
        "absent signed flag must default to False (kills the default flip)"
