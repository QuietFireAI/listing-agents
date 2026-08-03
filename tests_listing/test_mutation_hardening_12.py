"""Mutation-hardening — spoke 12 (marketing campaign).

Closes the 4 spoke-12 gaps: 97, 98, 121, 253. Each kill-verified.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.signatures import Ed25519Signer, Ed25519Verifier
from dispatcher.listing_spokes_12 import Spoke12MarketingCampaign

IDENTITY_ROUTES = os.path.join(os.path.dirname(__file__), "..", "identity",
                               "routes.json")


def make_hub(tmp_path):
    signer = Ed25519Signer()
    verifier = Ed25519Verifier(signer.public_key_bytes())
    return Hub(Routes(IDENTITY_ROUTES),
               AuditLog(os.path.join(tmp_path, "a.jsonl")),
               signature_verifier=verifier.verifier())


def spoke(tmp_path):
    return Spoke12MarketingCampaign(make_hub(tmp_path))


def status_update(ctx, status):
    return Envelope(from_agent="05", to_agent="12", intent="status.update",
                    client_context_id=ctx, payload={"status": status},
                    provenance={"source": "t"})


def verdict(ctx, v, campaign=None):
    p = {"verdict": v}
    if campaign is not None:
        p["campaign"] = campaign
    return Envelope(from_agent="17", to_agent="12", intent="content.verdict",
                    client_context_id=ctx, payload=p,
                    provenance={"source": "t"})


def persisted(hub, intent=None):
    return [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"
            and (intent is None or e["intent"] == intent)]


def publish_decided(spoke, ctx):
    """campaign.publish is an ABSOLUTE-SIGNAL `public` send that HOLDS at the
    gate. The publish DECISION is recorded in spoke.published[ctx] inside
    _publish_approved, independent of the gated external delivery."""
    return ctx in spoke.published


# ------------------------------------------------------------ 97/98
def test_12_held_campaign_publishes_when_gate_clears(tmp_path):
    """A campaign approved while the CCP gate was closed publishes once the
    gate clears (MLS active), emitting a resolved status (97); if nothing was
    pending, no spurious publish (98)."""
    s = spoke(str(tmp_path))
    s.hub.on_turn_start()
    # approve while gate closed -> held
    s.pending_review["g-1"] = {"copy": "x"}
    s.handle(verdict("g-1", "approved"))
    assert "g-1" in s.approved_awaiting_ccp, "campaign should hold on closed gate"
    assert not persisted(s.hub, "campaign.publish"), "must not publish yet"
    # gate clears via MLS active
    s.handle(status_update("g-1", "active"))
    assert publish_decided(s, "g-1"), \
        "the held campaign must publish once the gate clears"
    resolved = [e for e in persisted(s.hub, "agent.status")
                if e["payload"].get("waiting_on") == "ccp_gate"
                and e["payload"].get("resolved") is True]
    assert resolved, "clearing the gate must emit resolved=True (kills 97)"

    # negative for 98: MLS active with NOTHING pending -> no publish
    s2 = spoke(str(tmp_path) + "_b")
    s2.hub.on_turn_start()
    s2.handle(status_update("g-2", "active"))
    assert not publish_decided(s2, "g-2"), \
        "an active status with nothing pending must not publish (kills 98)"

    # 98 elif: a held campaign must SURVIVE a non-clearing re-check (not be
    # lost). Approve while gate closed, trigger _check with the gate still
    # closed -> the campaign must remain held (elif re-stores it).
    s3 = spoke(str(tmp_path) + "_c")
    s3.hub.on_turn_start()
    s3.pending_review["g-3"] = {"copy": "z"}
    s3.handle(verdict("g-3", "approved"))          # held, gate closed
    assert "g-3" in s3.approved_awaiting_ccp
    s3._check_awaiting_ccp("g-3")                   # gate STILL closed
    assert "g-3" in s3.approved_awaiting_ccp, \
        "a held campaign must survive a non-clearing re-check (kills 98 elif)"


# ------------------------------------------------------------ 121
def test_12_non_active_status_halts_and_unconfirms_mls(tmp_path):
    """A non-active status.update sets mls_confirmed False (121) and halts
    any published campaign. Kills the 121 flag: an active status keeps it
    True (via the other branch), a non-active must set it False."""
    s = spoke(str(tmp_path))
    s.hub.on_turn_start()
    # publish something first
    s.mls_confirmed["h-1"] = True
    s.pending_review["h-1"] = {"copy": "y"}
    s.handle(verdict("h-1", "approved"))
    assert s.mls_confirmed.get("h-1") is True
    assert "h-1" in s.published
    # now a non-active status halts and unconfirms
    s.handle(status_update("h-1", "withdrawn"))
    assert s.mls_confirmed.get("h-1") is False, \
        "a non-active status must set mls_confirmed False (kills 121)"
    assert "h-1" not in s.published, "the published campaign must be halted"


# ------------------------------------------------------------ 253
def test_12_flagged_verdict_does_not_publish_approved_does(tmp_path):
    """content.verdict flagged -> no publish (held/logged); approved (with an
    open gate) -> publishes. Kills the 253 `verdict == "flagged"` flip."""
    s = spoke(str(tmp_path))
    s.hub.on_turn_start()
    s.mls_confirmed["f-1"] = True  # gate open
    s.pending_review["f-1"] = {"copy": "bad"}
    s.handle(verdict("f-1", "flagged"))
    assert not publish_decided(s, "f-1"), \
        "a flagged verdict must not publish"
    # assert the FLAGGED branch specifically (kills == -> !=, which would
    # fall through to the unrecognized-verdict clarification instead)
    flagged_trace = [t for t in s.hub.spoke_traces
                     if t["agent"] == "12" and "flagged" in t.get("result", "")]
    assert flagged_trace, \
        "a flagged verdict must hit the flagged branch (result 'held: flagged')"
    assert not persisted(s.hub, "clarification.request"), \
        "flagged must not fall through to unrecognized-verdict clarification"

    s2 = spoke(str(tmp_path) + "_b")
    s2.hub.on_turn_start()
    s2.mls_confirmed["f-2"] = True
    s2.pending_review["f-2"] = {"copy": "good"}
    s2.handle(verdict("f-2", "approved"))
    assert publish_decided(s2, "f-2"), \
        "an approved verdict on an open gate must publish"
