"""Mutation-hardening — spoke 02 (lead qualification), remaining gaps.

Fable covers 02:146. This closes 203, 205, 285, 398. Each kill-verified.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.signatures import Ed25519Signer, Ed25519Verifier
from dispatcher.listing_spokes_02 import Spoke02LeadQualification
from dispatcher.listing_spokes import Spoke14CRMPipeline

IDENTITY_ROUTES = os.path.join(os.path.dirname(__file__), "..", "identity",
                               "routes.json")

RUBRIC = {"budget_threshold": 500000, "budget_weight": 40,
          "timeline_days_threshold": 30, "timeline_weight": 30,
          "financing_weight": 30, "hot_threshold": 80, "warm_threshold": 50}


def make_hub(tmp_path):
    signer = Ed25519Signer()
    verifier = Ed25519Verifier(signer.public_key_bytes())
    return Hub(Routes(IDENTITY_ROUTES),
               AuditLog(os.path.join(tmp_path, "a.jsonl")),
               signature_verifier=verifier.verifier()), signer


def persisted(hub, intent=None):
    return [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"
            and (intent is None or e["intent"] == intent)]


# ------------------------------------------------------------ 203/205
def test_02_oscillation_needs_true_aba_not_just_two_distinct(tmp_path):
    """_oscillating_third_time: True only for a genuine A,B,A return; False
    for <3 history (203) and for A,B,C or A,A,B (205 `a==c and a!=b`)."""
    hub, _ = make_hub(str(tmp_path))
    s = Spoke02LeadQualification(hub)
    # fewer than 3 -> False (203)
    s.tier_history["o-1"] = ["HOT", "COLD"]
    assert s._oscillating_third_time("o-1") is False, \
        "under 3 history entries can't oscillate"
    # genuine A,B,A -> True
    s.tier_history["o-2"] = ["HOT", "COLD", "HOT"]
    assert s._oscillating_third_time("o-2") is True, \
        "A,B,A is a real oscillation"
    # A,B,C (all distinct) -> False (a != c)
    s.tier_history["o-3"] = ["HOT", "COLD", "WARM"]
    assert s._oscillating_third_time("o-3") is False, \
        "three distinct tiers is not an oscillation (kills the a==c flip)"
    # A,A,B (a == b) -> False (a != b clause)
    s.tier_history["o-4"] = ["HOT", "HOT", "COLD"]
    assert s._oscillating_third_time("o-4") is False, \
        "A,A,B is not a back-and-forth (kills the a!=b flip)"


# ------------------------------------------------------------ 398
def test_02_cold_tier_archived_flag_hot_not(tmp_path):
    """The interaction.log carries archived=True only for a COLD tier.
    Kills the `tier == "COLD"` flip: a HOT lead must log archived=False."""
    hub, signer = make_hub(str(tmp_path))
    Spoke14CRMPipeline(hub)
    s = Spoke02LeadQualification(hub)
    hub.on_turn_start()
    rc = Envelope(from_agent="human", to_agent="02", intent="config.update",
                  client_context_id="cfg",
                  payload={"rubric": RUBRIC, "version": "v1"},
                  provenance={"source": "human", "captured_at": "t",
                              "verbatim_available": True})
    signer.sign(rc)
    hub.send(rc)
    # a cold lead (low budget, slow timeline, no financing)
    cold = Envelope(from_agent="01", to_agent="02", intent="lead.captured",
                    client_context_id="cold-1",
                    payload={"budget": 100000, "timeline_days": 400,
                             "financing_progress": None,
                             "stated_urgency": "low"},
                    provenance={"source": "t"})
    hub.send(cold)
    logs = [e for e in persisted(hub, "interaction.log")
            if e["client_context_id"] == "cold-1"]
    assert logs and logs[0]["payload"]["archived"] is True, \
        "a COLD lead must be logged archived=True"

    # a hot lead
    hot = Envelope(from_agent="01", to_agent="02", intent="lead.captured",
                   client_context_id="hot-1",
                   payload={"budget": 900000, "timeline_days": 5,
                            "financing_progress": "preapproved",
                            "stated_urgency": "high"},
                   provenance={"source": "t"})
    hub.send(hot)
    hlogs = [e for e in persisted(hub, "interaction.log")
             if e["client_context_id"] == "hot-1"]
    assert hlogs and hlogs[0]["payload"]["archived"] is False, \
        "a non-COLD lead must be logged archived=False (kills the flip)"


# ------------------------------------------------------------ 285
def test_02_hot_response_resolves_wait_status(tmp_path):
    """When a hot-lead human response is recorded, 02 emits an agent.status
    to 18 with resolved=True. Kills the 285 resolved-flag flip."""
    hub, signer = make_hub(str(tmp_path))
    Spoke14CRMPipeline(hub)
    s = Spoke02LeadQualification(hub)
    hub.on_turn_start()
    # a signed config.update carrying resolve_hot_lead clears the wait
    s.open_escalations.add("hr-1")
    resp = Envelope(from_agent="human", to_agent="02", intent="config.update",
                    client_context_id="cfg",
                    payload={"resolve_hot_lead": "hr-1"},
                    provenance={"source": "human", "captured_at": "t",
                                "verbatim_available": True})
    signer.sign(resp)
    hub.send(resp)
    statuses = [e for e in persisted(hub, "agent.status")
                if e["client_context_id"] == "hr-1"
                and e["payload"].get("waiting_on") == "hot_lead_human_response"]
    assert statuses and statuses[0]["payload"]["resolved"] is True, \
        "a recorded hot-lead response must resolve the wait (resolved=True)"
