"""Mutation-hardening — gaps the Fable 5 CrossPol found that my sweep missed.

None of these are in the original 105-mutation contract; my sweep never covered
them (06:83/06:333 because the 06 legal/fail-closed lines weren't in scope,
11:139 likewise). Fable surfaced all three as full-suite survivors. Each closed
here is kill-verified against its exact mutation.

  06:83   _access_request_hit — lockbox/access-word legal-line detection (w in low)
  06:333  protected_deadline fail-closed default (False)
  11:139  _send_client_message returns False on unresolved template variables
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.signatures import Ed25519Signer, Ed25519Verifier
from dispatcher.listing_spokes_06 import Spoke06ShowingScheduler
from dispatcher.listing_spokes_11 import Spoke11ClientCommunication

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


def legal(hub):
    return hub.queues.get("escalation.legal_line", [])


# ------------------------------------------------------------ 06:83
def test_06_access_request_words_escalate_clean_does_not(tmp_path):
    """_access_request_hit: a showing.request whose message asks for access
    ('lockbox combo', 'let them in') is the legal line -> escalate; a clean
    message does not. Kills the 83 `w in low` flip."""
    s = Spoke06ShowingScheduler(make_hub(str(tmp_path)))
    s.hub.on_turn_start()
    s.handle(Envelope(from_agent="13", to_agent="06", intent="showing.request",
                      client_context_id="ar-1",
                      payload={"message": "can you give them the lockbox combo",
                               "requested_time": "2026-08-12T14:00",
                               "buyer_agreement_on_file": True,
                               "requester_identity_verified": True,
                               "timezone_confirmed": True},
                      provenance={"source": "t"}))
    assert any(e.get("agent") == "06" or "access" in str(e).lower()
               for e in legal(s.hub)), \
        "an access-request message must escalate to the legal line"

    s2 = Spoke06ShowingScheduler(make_hub(str(tmp_path) + "_b"))
    s2.hub.on_turn_start()
    s2.handle(Envelope(from_agent="13", to_agent="06", intent="showing.request",
                       client_context_id="ar-2",
                       payload={"message": "please confirm the 2pm showing",
                                "requested_time": "2026-08-12T14:00",
                                "buyer_agreement_on_file": True,
                                "requester_identity_verified": True,
                                "timezone_confirmed": True},
                       provenance={"source": "t"}))
    # under the flip (`w not in low`), a clean message escalates on the first
    # word not present; assert NO agent-06 legal escalation fires at all.
    assert not any(e.get("agent") == "06" for e in legal(s2.hub)), \
        "a clean showing message must not escalate the access legal line " \
        "(kills the 83 in -> not in flip)"


# ------------------------------------------------------------ 06:333
def test_06_unprotected_conflict_sequences_not_bumps(tmp_path):
    """protected_deadline defaults to False (fail closed): a conflicting
    request WITHOUT the flag must produce a plain sequencing clarification
    and NEVER a held bump. Observability via pending_bumps (the tier-gate
    fallback ALSO emits 'calendar conflict' text, so asserting on the text
    gives a false kill — assert the bump state instead, per Fable's trace).
    Kills the 333 default False -> True flip."""
    s = Spoke06ShowingScheduler(make_hub(str(tmp_path)))
    s.hub.on_turn_start()
    # seed a confirmed showing so the new request conflicts
    s.confirmed_showings["cf-1"] = [{"time": "2026-08-12T14:00"}]
    s.handle(Envelope(from_agent="13", to_agent="06", intent="showing.request",
                      client_context_id="cf-1",
                      payload={"requested_time": "2026-08-12T14:10",  # 10min -> conflict
                               "buffer_minutes": 30,
                               "buyer_agreement_on_file": True,
                               "requester_identity_verified": True,
                               "timezone_confirmed": True,
                               "lead_tier": "HOT"},   # HOT so ONLY the default gates it
                      provenance={"source": "t"}))
    # with the default False (clean): unprotected -> plain sequencing, NO bump held
    assert "cf-1" not in s.pending_bumps, \
        "an unprotected conflict must NOT create a held bump (kills 333 default)"
    assert persisted(s.hub, "clarification.request"), \
        "an unprotected conflict must ask for sequencing"


def test_06_protected_hot_claim_holds_a_bump(tmp_path):
    """Positive side of 333: WITH protected_deadline True AND lead_tier HOT,
    the request holds a bump in pending_bumps (awaiting human confirm). This
    is the branch the default-False guards against for unprotected requests."""
    s = Spoke06ShowingScheduler(make_hub(str(tmp_path)))
    s.hub.on_turn_start()
    s.confirmed_showings["cf-2"] = [{"time": "2026-08-12T14:00"}]
    s.handle(Envelope(from_agent="13", to_agent="06", intent="showing.request",
                      client_context_id="cf-2",
                      payload={"requested_time": "2026-08-12T14:10",
                               "buffer_minutes": 30,
                               "buyer_agreement_on_file": True,
                               "requester_identity_verified": True,
                               "timezone_confirmed": True,
                               "protected_deadline": True,
                               "lead_tier": "HOT"},
                      provenance={"source": "t"}))
    assert "cf-2" in s.pending_bumps, \
        "a protected HOT claim must hold a bump for human confirmation"


# ------------------------------------------------------------ 11:139
def test_11_unresolved_template_var_holds_not_sends(tmp_path):
    """_send_client_message returns False (does NOT send) when a template
    variable is unresolved (None); returns True and sends when all are
    resolved. Kills the 139 `return False` -> the caller would get a false
    'sent' and a blank-filled message would go to the client."""
    s = Spoke11ClientCommunication(make_hub(str(tmp_path)))
    s.hub.on_turn_start()
    result = s._send_client_message("u-1", "update",
                                    {"name": None}, hour=10)  # unresolved
    assert result is False, \
        "an unresolved template variable must NOT send (kills the 139 flip)"
    assert not persisted(s.hub, "client.message.send"), \
        "nothing may reach external with an unresolved variable"
    assert persisted(s.hub, "clarification.request"), \
        "the unresolved variable must be surfaced for a human"

    # resolved -> sends
    ok = s._send_client_message("u-2", "update", {"name": "Jo"}, hour=10)
    assert ok is True and persisted(s.hub, "client.message.send"), \
        "a fully-resolved template must send"
