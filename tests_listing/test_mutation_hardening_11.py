"""Mutation-hardening — spoke 11 (client communication).

Closes the 4 spoke-11 gaps: 153, 295, 414, 415. Each kill-verified.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.signatures import Ed25519Signer, Ed25519Verifier
from dispatcher.listing_spokes_11 import Spoke11ClientCommunication

IDENTITY_ROUTES = os.path.join(os.path.dirname(__file__), "..", "identity",
                               "routes.json")


def make_hub(tmp_path):
    signer = Ed25519Signer()
    verifier = Ed25519Verifier(signer.public_key_bytes())
    return Hub(Routes(IDENTITY_ROUTES),
               AuditLog(os.path.join(tmp_path, "a.jsonl")),
               signature_verifier=verifier.verifier())


def spoke(tmp_path, **kw):
    return Spoke11ClientCommunication(make_hub(tmp_path), **kw)


def persisted(hub, intent=None):
    return [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"
            and (intent is None or e["intent"] == intent)]


def client_reply(ctx, message):
    return Envelope(from_agent="external", to_agent="11", intent="client.reply",
                    client_context_id=ctx,
                    payload={"message": message, "hour_date": "2026-08-10T10"},
                    provenance={"source": "external"})


def record_response(ctx, payload):
    return Envelope(from_agent="14", to_agent="11", intent="record.response",
                    client_context_id=ctx, payload=payload,
                    provenance={"source": "t"})


# ------------------------------------------------------------ 153
def test_11_quiet_hours_holds_send_normal_hours_send(tmp_path):
    """_send_client_message returns False and does NOT send during quiet
    hours; returns True and sends a client.message.send during normal hours.
    Kills the 153 quiet-hours `return False`."""
    s = spoke(str(tmp_path), quiet_hours=(21, 8))
    s.hub.on_turn_start()
    # hour 23 -> quiet -> held
    result = s._send_client_message("q-1", "update", {"name": "Jo"}, hour=23)
    assert result is False, "a quiet-hours send must be held"
    assert not persisted(s.hub, "client.message.send"), \
        "nothing may reach external during quiet hours"

    # hour 10 -> normal -> sent
    result2 = s._send_client_message("q-2", "update", {"name": "Jo"}, hour=10)
    assert result2 is True
    assert persisted(s.hub, "client.message.send"), \
        "a normal-hours send must reach external"


# ------------------------------------------------------------ 295
def test_11_advice_question_sets_awaiting_human(tmp_path):
    """An advice-seeking client message escalates to legal and sets
    awaiting_human_response[ctx]=True; a routine message does not. Kills the
    295 flag assignment."""
    s = spoke(str(tmp_path))
    s.hub.on_turn_start()
    s.handle(client_reply("a-1", "should i offer over asking?"))
    assert s.awaiting_human_response.get("a-1") is True, \
        "an advice question must set awaiting_human_response"

    s2 = spoke(str(tmp_path) + "_b")
    s2.hub.on_turn_start()
    s2.handle(client_reply("a-2", "thanks, see you at the showing"))
    assert not s2.awaiting_human_response.get("a-2"), \
        "a routine message must not set awaiting_human_response"


# ------------------------------------------------------------ 414/415
def test_11_showing_response_gates_on_agreement_relays_identity(tmp_path):
    """record.response: no buyer agreement -> routes to 13, no showing.request
    (414). With agreement -> showing.request to 06 carrying the
    requester_identity_verified flag verbatim (415)."""
    s = spoke(str(tmp_path))
    s.hub.on_turn_start()
    s.pending_showing_requests["r-1"] = {"requested_time": "2026-08-12T14:00"}
    s.handle(record_response("r-1", {"buyer_agreement_on_file": False}))
    assert not persisted(s.hub, "showing.request"), \
        "no agreement must not produce a showing.request (kills 414 default)"

    s2 = spoke(str(tmp_path) + "_b")
    s2.hub.on_turn_start()
    s2.pending_showing_requests["r-2"] = {"requested_time": "2026-08-12T14:00"}
    s2.handle(record_response("r-2", {"buyer_agreement_on_file": True,
                                      "requester_identity_verified": True}))
    sr = persisted(s2.hub, "showing.request")
    assert sr, "a valid agreement must release the showing.request"
    assert sr[0]["payload"]["requester_identity_verified"] is True, \
        "the identity flag must relay verbatim (kills 415 default flip)"


def test_11_buyer_agreement_defaults_false_when_absent(tmp_path):
    """414: absent buyer_agreement_on_file defaults to False, so the request
    routes to 13 and no showing.request goes out. Flip default to True and a
    showing.request would leak -> this fails."""
    s = spoke(str(tmp_path))
    s.hub.on_turn_start()
    s.pending_showing_requests["r-abs"] = {"requested_time": "2026-08-12T14:00"}
    s.handle(record_response("r-abs", {}))  # key absent
    assert not persisted(s.hub, "showing.request"), \
        "absent buyer agreement must default False, no showing released"
    assert persisted(s.hub, "lead.reply"), \
        "absent agreement must route to 13 via lead.reply"


def test_11_showing_identity_flag_defaults_false(tmp_path):
    """415: absent requester_identity_verified defaults to False in the relay."""
    s = spoke(str(tmp_path))
    s.hub.on_turn_start()
    s.pending_showing_requests["r-3"] = {"requested_time": "2026-08-12T14:00"}
    s.handle(record_response("r-3", {"buyer_agreement_on_file": True}))
    sr = persisted(s.hub, "showing.request")
    assert sr and sr[0]["payload"]["requester_identity_verified"] is False, \
        "absent identity flag must default to False"
    s = spoke(str(tmp_path))
    s.hub.on_turn_start()
    s.pending_showing_requests["r-3"] = {"requested_time": "2026-08-12T14:00"}
    s.handle(record_response("r-3", {"buyer_agreement_on_file": True}))
    sr = persisted(s.hub, "showing.request")
    assert sr and sr[0]["payload"]["requester_identity_verified"] is False, \
        "absent identity flag must default to False"
