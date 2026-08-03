"""Mutation-hardening — shared 01/14 (dispatcher/listing_spokes.py).

Gap set re-derived against the FULL suite this session (5 true survivors, not
the 9 first estimated): 215, 253, 369, 428, 635. Each kill-verified.
  215  Spoke14 report: tier extracted only from interaction.log w/ 'tier'
  253  Spoke14 merge: contact considered only when it has a value
  369  Spoke14: delete_record action freezes + escalates
  428  Spoke01 _legal_line_hit word match
  635  Spoke01: low-confidence CALL drops garbled transcript fields
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.signatures import Ed25519Signer, Ed25519Verifier
from dispatcher.listing_spokes import Spoke01LeadCapture, Spoke14CRMPipeline

IDENTITY_ROUTES = os.path.join(os.path.dirname(__file__), "..", "identity",
                               "routes.json")


def make_hub(tmp_path):
    signer = Ed25519Signer()
    verifier = Ed25519Verifier(signer.public_key_bytes())
    return Hub(Routes(IDENTITY_ROUTES),
               AuditLog(os.path.join(tmp_path, "a.jsonl")),
               signature_verifier=verifier.verifier()), signer


def persisted(hub, intent=None):
    return [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"
            and (intent is None or e["intent"] == intent)]


def legal(hub):
    return hub.queues.get("escalation.legal_line", [])


def lead_inbound(ctx, payload):
    p = {"channel": "call", "address_or_listing": "123 Main"}
    p.update(payload)
    return Envelope(from_agent="external", to_agent="01", intent="lead.inbound",
                    client_context_id=ctx, payload=p,
                    provenance={"source": "external"})


# ------------------------------------------------------------ 428
def test_01_legal_line_phrase_escalates_clean_does_not(tmp_path):
    """_legal_line_hit: a fiduciary/legal phrase escalates to the legal line;
    a plain inquiry does not. Kills the 428 `w in low` flip."""
    hub, _ = make_hub(str(tmp_path))
    Spoke01LeadCapture(hub, brokerage_scope={"123 Main"})
    hub.on_turn_start()
    hub.send(lead_inbound("ll-1", {"message": "what should i offer on this?"}))
    assert any(e.get("agent") == "01" for e in legal(hub)), \
        "a legal-line phrase must escalate to the legal line"

    hub2, _ = make_hub(str(tmp_path) + "_b")
    Spoke01LeadCapture(hub2, brokerage_scope={"123 Main"})
    hub2.on_turn_start()
    hub2.send(lead_inbound("ll-2", {"message": "is the house still available?"}))
    # under the flip (`w not in low`), a clean inquiry escalates on the first
    # non-matching word; assert NO agent-01 legal escalation fires at all.
    assert not any(e.get("agent") == "01" for e in legal(hub2)), \
        "a plain inquiry must not trip the legal line (kills the flip)"


# ------------------------------------------------------------ 635
def test_01_garbled_call_drops_soft_fields_clean_call_keeps(tmp_path):
    """A low-confidence CALL drops the interpretation-sensitive fields
    (never tier on a garbled transcript); a normal-confidence call keeps
    them. Kills the 635 `low_confidence and channel == "call"`."""
    hub, _ = make_hub(str(tmp_path))
    s = Spoke01LeadCapture(hub, brokerage_scope={"123 Main"})
    hub.on_turn_start()
    hub.send(lead_inbound("gc-1", {"transcription_confidence": "low",
                                   "timeline_days": 30,
                                   "stated_urgency": "high"}))
    assert "timeline_days" not in s.pending.get("gc-1", {}), \
        "a garbled call must drop timeline_days (never tier on it)"
    assert "stated_urgency" not in s.pending.get("gc-1", {})

    hub2, _ = make_hub(str(tmp_path) + "_b")
    s2 = Spoke01LeadCapture(hub2, brokerage_scope={"123 Main"})
    hub2.on_turn_start()
    hub2.send(lead_inbound("gc-2", {"transcription_confidence": "high",
                                    "timeline_days": 30,
                                    "stated_urgency": "high"}))
    assert s2.pending.get("gc-2", {}).get("timeline_days") == 30, \
        "a clean call must keep the fields (kills the guard flip)"


# ------------------------------------------------------------ 369
def test_14_delete_record_freezes_and_escalates(tmp_path):
    """A config.update with action=delete_record freezes the record and
    escalates; a config.update without that action does not. Kills the 369
    `and action == "delete_record"` guard."""
    hub, signer = make_hub(str(tmp_path))
    s = Spoke14CRMPipeline(hub)
    hub.on_turn_start()

    def cfg(ctx, payload):
        e = Envelope(from_agent="human", to_agent="14", intent="config.update",
                     client_context_id=ctx, payload=payload,
                     provenance={"source": "human", "captured_at": "t",
                                 "verbatim_available": True})
        signer.sign(e)
        return e

    hub.send(cfg("del-1", {"action": "delete_record"}))
    assert "del-1" in s.frozen, "a delete_record request must freeze the record"
    assert any(e.get("agent") == "14" for e in legal(hub)), \
        "a delete_record request must escalate to legal"

    hub.send(cfg("del-2", {"action": "something_else"}))
    assert "del-2" not in s.frozen, \
        "a non-delete action must NOT freeze (kills the guard flip)"


# ------------------------------------------------------------ 215
def test_14_report_tiers_only_from_tier_bearing_logs(tmp_path):
    """generate_report extracts tier only from interaction.log entries that
    carry a 'tier'; a log without a tier contributes none. Kills the 215
    `"tier" in e["payload"]` / kind== flips by asserting the tier appears
    only for the ctx whose log actually had one."""
    hub, _ = make_hub(str(tmp_path))
    s = Spoke14CRMPipeline(hub)
    hub.on_turn_start()
    # ctx A: an interaction.log WITH a tier
    s.records["A"] = [{"kind": "interaction.log", "entry_id": "e1",
                       "payload": {"tier": "HOT"}}]
    # ctx B: an interaction.log with NO tier
    s.records["B"] = [{"kind": "interaction.log", "entry_id": "e2",
                       "payload": {"kind": "note"}}]
    report = s.generate_report()
    tiers = report.get("tier_snapshot", {})
    assert tiers.get("A") == "HOT", "a tier-bearing log must be counted"
    assert "B" not in tiers, \
        "a log without a tier must contribute no tier (kills the 215 flip)"


# ------------------------------------------------------------ 253
def test_14_merge_candidates_need_contact_value(tmp_path):
    """check_merge_candidates flags two contexts sharing a contact VALUE;
    a lead.captured log whose contact has no value contributes nothing.
    Kills the 253 `contact and contact.get("value")` guard."""
    hub, _ = make_hub(str(tmp_path))
    s = Spoke14CRMPipeline(hub)
    hub.on_turn_start()
    # two contexts, same contact value -> merge candidate
    for ctx in ("m1", "m2"):
        s.records[ctx] = [{"kind": "interaction.log", "entry_id": ctx,
                           "payload": {"kind": "lead.captured",
                                       "contact": {"value": "555-0100"}}}]
    # a third with a contact but NO value -> must not group
    s.records["m3"] = [{"kind": "interaction.log", "entry_id": "m3",
                        "payload": {"kind": "lead.captured",
                                    "contact": {"label": "home"}}}]
    s.check_merge_candidates()
    flagged = persisted(hub, "clarification.request")
    text = " ".join(str(e["payload"]) for e in flagged)
    assert "m1" in text and "m2" in text, \
        "two contexts sharing a contact value must be flagged to merge"
    assert "m3" not in text, \
        "a contact with no value must not be grouped (kills the guard flip)"
