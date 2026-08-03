"""Mutation-hardening — spoke 17 (compliance / fair housing), remaining gap.

Closes 17:279 (the compliance.notice intent branch that logs the notice to
14). Kill-verified.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.signatures import Ed25519Signer, Ed25519Verifier
from dispatcher.listing_spokes_17 import Spoke17ComplianceFairHousing

IDENTITY_ROUTES = os.path.join(os.path.dirname(__file__), "..", "identity",
                               "routes.json")


def make_hub(tmp_path):
    signer = Ed25519Signer()
    verifier = Ed25519Verifier(signer.public_key_bytes())
    return Hub(Routes(IDENTITY_ROUTES),
               AuditLog(os.path.join(tmp_path, "a.jsonl")),
               signature_verifier=verifier.verifier())


def persisted(hub, intent=None):
    return [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"
            and (intent is None or e["intent"] == intent)]


def test_17_compliance_notice_logged_other_intent_not(tmp_path):
    """A compliance.notice is logged to 14 as compliance_notice_received
    carrying its trigger; an unrelated intent does not produce that log.
    Kills the 279 `env.intent == "compliance.notice"` flip."""
    hub = make_hub(str(tmp_path))
    Spoke17ComplianceFairHousing(hub)
    hub.on_turn_start()
    hub.send(Envelope(from_agent="10", to_agent="17",
                      intent="compliance.notice", client_context_id="cn-1",
                      payload={"trigger": "appraisal substitution smell"},
                      provenance={"source": "t"}))
    logs = [e for e in persisted(hub, "interaction.log")
            if e["payload"].get("kind") == "compliance_notice_received"]
    assert logs, "a compliance.notice must be logged to 14"
    assert logs[0]["payload"]["trigger"] == "appraisal substitution smell"

    # a content.review (different intent) must not create the notice log
    hub2 = make_hub(str(tmp_path) + "_b")
    Spoke17ComplianceFairHousing(hub2)
    hub2.on_turn_start()
    hub2.send(Envelope(from_agent="04", to_agent="17", intent="content.review",
                       client_context_id="cn-2",
                       payload={"content": {"copy": "clean listing"},
                                "submitting_agent": "04"},
                       provenance={"source": "t"}))
    assert not [e for e in persisted(hub2, "interaction.log")
                if e["payload"].get("kind") == "compliance_notice_received"], \
        "a non-notice intent must not log a compliance notice (kills the flip)"
