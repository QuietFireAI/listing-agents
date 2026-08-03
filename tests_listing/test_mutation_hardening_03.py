"""Mutation-hardening — spoke 03 (lead nurture).

The file I skipped in the first pass. Closes the spoke-03 gaps the full-suite
rescore found still open (Fable covers 03:309):
  100  _in_legal_contact_hours boundary (hour >= start / hour < end)
  267  lead.reply pauses the sequence (paused = True)
  294  stop/opt-out pauses the sequence (paused = True)
Each kill-verified against the sweep's exact mutation.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.signatures import Ed25519Signer, Ed25519Verifier
from dispatcher.listing_spokes_03 import Spoke03LeadNurture

IDENTITY_ROUTES = os.path.join(os.path.dirname(__file__), "..", "identity",
                               "routes.json")


def make_hub(tmp_path):
    signer = Ed25519Signer()
    verifier = Ed25519Verifier(signer.public_key_bytes())
    return Hub(Routes(IDENTITY_ROUTES),
               AuditLog(os.path.join(tmp_path, "a.jsonl")),
               signature_verifier=verifier.verifier())


def spoke(tmp_path, **kw):
    return Spoke03LeadNurture(make_hub(tmp_path), **kw)


def lead_reply(ctx, message):
    return Envelope(from_agent="11", to_agent="03", intent="lead.reply",
                    client_context_id=ctx, payload={"message": message},
                    provenance={"source": "t"})


# ------------------------------------------------------------ 100
def test_03_legal_contact_hours_boundaries(tmp_path):
    """_in_legal_contact_hours: with hours (8, 21), 8 is inside, 21 is
    outside, 7 is outside. Kills both the `hour >= start` and `hour < end`
    flips by asserting each boundary."""
    s = spoke(str(tmp_path), legal_contact_hours=(8, 21))
    assert s._in_legal_contact_hours(8) is True, "start hour is inside (>=)"
    assert s._in_legal_contact_hours(20) is True, "within range"
    assert s._in_legal_contact_hours(21) is False, "end hour is outside (<)"
    assert s._in_legal_contact_hours(7) is False, "before start is outside"
    assert s._in_legal_contact_hours(22) is False, "after end is outside"


def test_03_legal_contact_hours_wrap_midnight(tmp_path):
    """The wrap-midnight branch (start > end), e.g. (21, 8): 22 and 2 are
    inside (>= start OR < end), 12 is outside. Pins the wrap boundary."""
    s = spoke(str(tmp_path), legal_contact_hours=(21, 8))
    assert s._in_legal_contact_hours(22) is True, ">= start side of the wrap"
    assert s._in_legal_contact_hours(2) is True, "< end side of the wrap"
    assert s._in_legal_contact_hours(21) is True, "start hour inside (>=)"
    assert s._in_legal_contact_hours(8) is False, "end hour outside (<)"
    assert s._in_legal_contact_hours(12) is False, "midday is outside the wrap"


def test_03_touch_outside_hours_shifts_not_sends(tmp_path):
    """send_scheduled_touch honors the contact-hours gate: a touch at an
    out-of-hours time does not send; an in-hours one is eligible. Confirms
    the 100 gate is wired into the real send path, not just the helper."""
    s = spoke(str(tmp_path), legal_contact_hours=(8, 21))
    s.hub.on_turn_start()
    s.active_sequences["t-1"] = {"sequence_id": "seq", "paused": False,
                                 "touch_count": 0, "consent": {"text": "yes"}}
    out = s.send_scheduled_touch("t-1", {"template": "check_in"},
                                 today_week="2026-W32", hour=23)
    assert out != "sent", "an out-of-hours touch must not send"


# ------------------------------------------------------------ 267
def test_03_reply_pauses_sequence(tmp_path):
    """A lead.reply pauses the active sequence (never auto-continue). Kills
    the 267 `paused = True` flip."""
    s = spoke(str(tmp_path))
    s.hub.on_turn_start()
    s.active_sequences["r-1"] = {"sequence_id": "seq", "paused": False,
                                 "touch_count": 1, "consent": {"text": "yes"}}
    s.handle(lead_reply("r-1", "thanks for the update"))
    assert s.active_sequences["r-1"]["paused"] is True, \
        "any reply must pause the nurture sequence (kills the flip)"


# ------------------------------------------------------------ 294
def test_03_stop_pauses_and_revokes_consent(tmp_path):
    """A STOP/opt-out pauses the sequence and sets all consent to 'no'.
    Kills the 294 `paused = True` flip on the opt-out branch."""
    s = spoke(str(tmp_path))
    s.hub.on_turn_start()
    s.active_sequences["s-1"] = {"sequence_id": "seq", "paused": False,
                                 "touch_count": 2,
                                 "consent": {"text": "yes", "email": "yes"}}
    s.handle(lead_reply("s-1", "STOP"))
    assert s.active_sequences["s-1"]["paused"] is True, \
        "a STOP must pause the sequence (kills the flip)"
    assert all(v == "no" for v in s.active_sequences["s-1"]["consent"].values()), \
        "a STOP must revoke all consent"
