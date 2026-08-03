"""Mutation-hardening — spoke 20 (social media monitoring), remaining gaps.

Closes 20:97 (is_viral -> escalation priority) and 20:202 (lead.reply intent
-> log). Each kill-verified.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.signatures import Ed25519Signer, Ed25519Verifier
from dispatcher.listing_spokes_20 import Spoke20SocialMediaMonitoring

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


def mention(ctx, sentiment, is_viral):
    return Envelope(from_agent="external", to_agent="20",
                    intent="social.mention", client_context_id=ctx,
                    payload={"channel": "x", "text": "bad experience",
                             "sentiment": sentiment, "is_viral": is_viral},
                    provenance={"source": "external"})


def legal(hub):
    return hub.queues.get("escalation.legal_line", []) + \
        hub.queues.get("escalation.broker", [])


def all_escalations(hub):
    out = []
    for q in hub.queues.values():
        out.extend(q)
    return out


# ------------------------------------------------------------ 97
def test_20_viral_complaint_gets_viral_priority_nonviral_does_not(tmp_path):
    """A viral complaint escalates with priority 'viral'; a non-viral
    complaint escalates without it. Kills the 97 is_viral default flip."""
    hub = make_hub(str(tmp_path))
    s = Spoke20SocialMediaMonitoring(hub)
    s.monitored_channels.add("x")
    hub.on_turn_start()
    hub.send(mention("v-1", "complaint", True))
    viral = [e for e in all_escalations(hub)
             if e.get("priority") == "viral" and e.get("client_context_id") == "v-1"]
    assert viral, "a viral complaint must escalate with priority 'viral'"

    hub2 = make_hub(str(tmp_path) + "_b")
    s2 = Spoke20SocialMediaMonitoring(hub2)
    s2.monitored_channels.add("x")
    hub2.on_turn_start()
    hub2.send(mention("v-2", "complaint", False))
    non_viral = [e for e in all_escalations(hub2)
                 if e.get("client_context_id") == "v-2"]
    assert non_viral, "a complaint still escalates"
    assert not any(e.get("priority") == "viral" for e in non_viral), \
        "a non-viral complaint must NOT carry viral priority (kills the flip)"

    # key-absent case: is_viral omitted defaults to False -> no viral priority.
    # Flip the default to True and this fails.
    hub3 = make_hub(str(tmp_path) + "_c")
    s3 = Spoke20SocialMediaMonitoring(hub3)
    s3.monitored_channels.add("x")
    hub3.on_turn_start()
    hub3.send(Envelope(from_agent="external", to_agent="20",
                       intent="social.mention", client_context_id="v-3",
                       payload={"channel": "x", "text": "bad",
                                "sentiment": "complaint"},  # is_viral absent
                       provenance={"source": "external"}))
    absent = [e for e in all_escalations(hub3)
              if e.get("client_context_id") == "v-3"]
    assert absent, "a complaint still escalates"
    assert not any(e.get("priority") == "viral" for e in absent), \
        "absent is_viral must default False, no viral priority (kills default flip)"


# ------------------------------------------------------------ 202
def test_20_lead_reply_logged_other_intent_not(tmp_path):
    """A lead.reply is logged as reply_received; a different intent does not
    produce that log. Kills the 202 intent flip."""
    hub = make_hub(str(tmp_path))
    Spoke20SocialMediaMonitoring(hub)
    hub.on_turn_start()
    hub.send(Envelope(from_agent="11", to_agent="20", intent="lead.reply",
                      client_context_id="lr-1", payload={"message": "hi"},
                      provenance={"source": "t"}))
    logs = [e for e in persisted(hub, "interaction.log")
            if e["payload"].get("kind") == "reply_received"]
    assert logs, "a lead.reply must be logged as reply_received"

    hub2 = make_hub(str(tmp_path) + "_b")
    s2 = Spoke20SocialMediaMonitoring(hub2)
    s2.monitored_channels.add("x")
    hub2.on_turn_start()
    hub2.send(mention("lr-2", "praise", False))  # social.mention, not lead.reply
    assert not [e for e in persisted(hub2, "interaction.log")
                if e["payload"].get("kind") == "reply_received"], \
        "a non-lead.reply intent must not log reply_received (kills the flip)"
