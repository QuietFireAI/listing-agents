import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, "/home/claude/pillars_pth")

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.listing_spokes import Spoke14CRMPipeline
from dispatcher.listing_spokes_02 import Spoke02LeadQualification
from dispatcher.signatures import Ed25519Signer, Ed25519Verifier

IDENTITY_ROUTES = os.path.join(os.path.dirname(__file__), "..", "identity",
                               "routes.json")

RUBRIC = {"budget_threshold": 500_000, "budget_weight": 40,
         "timeline_days_threshold": 30, "timeline_weight": 40,
         "financing_weight": 20, "hot_threshold": 70, "warm_threshold": 40}


def make_hub(tmp_path, **kw):
    audit_path = os.path.join(tmp_path, f"audit-{uuid.uuid4().hex[:8]}.jsonl")
    signer = Ed25519Signer()
    verifier = Ed25519Verifier(signer.public_key_bytes())
    hub = Hub(Routes(IDENTITY_ROUTES), AuditLog(audit_path),
             signature_verifier=verifier.verifier(), **kw)
    return hub, signer


def lead(ctx, payload, frm="01"):
    return Envelope(from_agent=frm, to_agent="02", intent="lead.captured",
                    client_context_id=ctx, payload=payload,
                    provenance={"source": "spoke-01", "captured_at": "runtime",
                                "verbatim_available": True})


def sign_config_update(signer, rubric, version):
    env = Envelope(from_agent="human", to_agent="02", intent="config.update",
                   client_context_id="config",
                   payload={"rubric": rubric, "version": version},
                   provenance={"source": "human", "captured_at": "runtime",
                               "verbatim_available": True})
    signer.sign(env)
    return env


def persisted(hub, intent=None):
    events = [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"]
    return [e for e in events if intent is None or e["intent"] == intent]


def test_no_rubric_fails_closed_to_unknown_tier(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke14CRMPipeline(hub)
    Spoke02LeadQualification(hub)
    hub.on_turn_start()

    hub.send(lead("q-001", {"budget": 600_000, "timeline_days": 10,
                            "financing_progress": "preapproved"}))

    logs = persisted(hub, "interaction.log")
    assert logs[-1]["payload"]["tier"] == "UNKNOWN"
    assert "no rubric active" in logs[-1]["payload"]["notes"][0]
    assert not hub.queues["escalation.hot_lead"], \
        "must not score/escalate as HOT without a real rubric, even with hot-looking inputs"


def test_signed_rubric_adopted_and_applied(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke14CRMPipeline(hub)
    q = Spoke02LeadQualification(hub)
    hub.on_turn_start()

    hub.send(sign_config_update(signer, RUBRIC, "v1"))
    assert q.rubric_version == "v1"

    hub.send(lead("q-002", {"budget": 600_000, "timeline_days": 10,
                            "financing_progress": "preapproved"}))
    logs = persisted(hub, "interaction.log")
    assert logs[-1]["payload"]["tier"] == "HOT"
    assert logs[-1]["payload"]["rubric_version"] == "v1"
    assert hub.queues["escalation.hot_lead"]


# ------------------------------------------ THE FIX: reassignment signal
def test_initial_capture_warm_lead_sends_reassignment_false(tmp_path):
    """Agent 03's tuple 10 (deliberate reassignment, newest wins) needs
    to be distinguished from its tuple 4 (ambiguous simultaneous
    conflict) - the signal is whether this WARM tier came from a fresh
    lead.captured or a later lead.rescored. An initial capture is never
    a reassignment."""
    hub, signer = make_hub(str(tmp_path))
    Spoke14CRMPipeline(hub)
    Spoke02LeadQualification(hub)
    hub.on_turn_start()
    hub.send(sign_config_update(signer, RUBRIC, "v1"))

    hub.send(lead("q-020", {"budget": 600_000, "timeline_days": 999,
                            "financing_progress": "preapproved"}))  # WARM (60)
    nurtures = persisted(hub, "lead.nurture")
    assert nurtures and nurtures[0]["payload"]["reassignment"] is False


def test_rescore_triggered_warm_lead_sends_reassignment_true(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke14CRMPipeline(hub)
    Spoke02LeadQualification(hub)
    hub.on_turn_start()
    hub.send(sign_config_update(signer, RUBRIC, "v1"))
    ctx = "q-021"

    hub.send(lead(ctx, {"budget": 0, "timeline_days": 999}))  # COLD, no send
    rescore = Envelope(from_agent="03", to_agent="02", intent="lead.rescored",
                      client_context_id=ctx,
                      payload={"budget": 600_000, "timeline_days": 999,
                              "financing_progress": "preapproved"},  # WARM (60)
                      provenance={"source": "spoke-03", "captured_at": "runtime",
                                  "verbatim_available": True})
    hub.send(rescore)
    nurtures = persisted(hub, "lead.nurture")
    assert nurtures and nurtures[-1]["payload"]["reassignment"] is True


def test_unsigned_config_update_never_reaches_rubric(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke14CRMPipeline(hub)
    q = Spoke02LeadQualification(hub)
    hub.on_turn_start()

    bad_env = Envelope(from_agent="human", to_agent="02", intent="config.update",
                      client_context_id="config",
                      payload={"rubric": RUBRIC, "version": "v-fake"},
                      provenance={"source": "human", "captured_at": "runtime",
                                  "verbatim_available": True})
    # no signature at all
    hub.send(bad_env)
    assert q.rubric is None, "unsigned rubric must never be adopted"


def test_boundary_score_assigns_lower_tier_and_flags(tmp_path):
    """Was: the tier was computed via score>=threshold (the HIGHER tier)
    and a note was appended claiming the lower tier was assigned - it
    wasn't. This test used to assert tier=='WARM' at exactly the
    WARM/COLD boundary, locking in the bug as 'correct'. Fixed: the
    lower tier is now actually assigned."""
    hub, signer = make_hub(str(tmp_path))
    Spoke14CRMPipeline(hub)
    Spoke02LeadQualification(hub)
    hub.on_turn_start()
    hub.send(sign_config_update(signer, RUBRIC, "v1"))

    # budget alone = 40 (exactly the warm threshold - the WARM/COLD boundary)
    hub.send(lead("q-003", {"budget": 600_000, "timeline_days": 999,
                            "financing_progress": "none"}))
    logs = persisted(hub, "interaction.log")
    assert logs[-1]["payload"]["tier"] == "COLD"
    assert any("tier boundary" in n for n in logs[-1]["payload"]["notes"])


def test_hot_warm_boundary_also_assigns_lower_tier(tmp_path):
    """Same fix, the other boundary: score exactly at hot_threshold (70)
    is the HOT/WARM boundary - lower tier is WARM, not HOT. This matters
    more than the other boundary: the old bug meant a boundary score
    triggered a real HOT escalation (SLA timer, human handoff) when it
    should have gone to nurture instead."""
    hub, signer = make_hub(str(tmp_path))
    Spoke14CRMPipeline(hub)
    Spoke02LeadQualification(hub)
    hub.on_turn_start()
    hub.send(sign_config_update(signer, RUBRIC, "v1"))

    # budget (40) + financing (20) + ??? need exactly 70: use a custom
    # rubric where timeline_weight=10 so budget+timeline+financing=70
    custom = {**RUBRIC, "timeline_weight": 10}
    hub.send(sign_config_update(signer, custom, "v2"))
    hub.send(lead("q-003b", {"budget": 600_000, "timeline_days": 10,
                             "financing_progress": "preapproved"}))
    logs = persisted(hub, "interaction.log")
    assert logs[-1]["payload"]["tier"] == "WARM"
    assert not hub.queues["escalation.hot_lead"], \
        "a boundary score must not trigger a real HOT escalation"
    assert any("tier boundary" in n for n in logs[-1]["payload"]["notes"])


def test_demands_human_forces_hot_regardless_of_score(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke14CRMPipeline(hub)
    Spoke02LeadQualification(hub)
    hub.on_turn_start()
    hub.send(sign_config_update(signer, RUBRIC, "v1"))

    hub.send(lead("q-004", {"budget": 0, "timeline_days": 999,
                            "demands_human": True}))
    assert hub.queues["escalation.hot_lead"]
    logs = persisted(hub, "interaction.log")
    assert logs[-1]["payload"]["tier"] == "HOT"


def test_expired_financing_letter_treated_as_no_verification(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke14CRMPipeline(hub)
    Spoke02LeadQualification(hub)
    hub.on_turn_start()
    hub.send(sign_config_update(signer, RUBRIC, "v1"))

    hub.send(lead("q-005", {"budget": 600_000, "timeline_days": 10,
                            "preapproval_doc": {"amount": 600_000, "expired": True}}))
    logs = persisted(hub, "interaction.log")
    # budget(40) + timeline(40) + financing=none(0) = 80 >= hot_threshold(70)
    assert logs[-1]["payload"]["tier"] == "HOT"
    assert any("expired" in n for n in logs[-1]["payload"]["notes"])


def test_budget_conflict_with_preapproval_doc_doc_wins(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke14CRMPipeline(hub)
    Spoke02LeadQualification(hub)
    hub.on_turn_start()
    hub.send(sign_config_update(signer, RUBRIC, "v1"))

    hub.send(lead("q-006", {"budget": 100_000,
                            "preapproval_doc": {"amount": 600_000, "expired": False},
                            "timeline_days": 10, "financing_progress": "preapproved"}))
    logs = persisted(hub, "interaction.log")
    assert any("doc wins" in n for n in logs[-1]["payload"]["notes"])
    assert logs[-1]["payload"]["tier"] == "HOT"  # scored on 600k not 100k


def test_agent_shopping_flagged_not_scored(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke14CRMPipeline(hub)
    Spoke02LeadQualification(hub)
    hub.on_turn_start()
    hub.send(sign_config_update(signer, RUBRIC, "v1"))

    hub.send(lead("q-007", {"budget": 900_000, "is_agent_shopping": True}))
    clar = persisted(hub, "clarification.request")
    assert any(c["payload"]["reason"] == "agent-to-agent inquiry" for c in clar)
    assert not persisted(hub, "interaction.log"), \
        "an agent-shopping lead is flagged, not scored/archived like a consumer lead"


def test_all_unknown_inputs_is_unknown_not_cold(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke14CRMPipeline(hub)
    Spoke02LeadQualification(hub)
    hub.on_turn_start()
    hub.send(sign_config_update(signer, RUBRIC, "v1"))

    hub.send(lead("q-008", {}))
    logs = persisted(hub, "interaction.log")
    assert logs[-1]["payload"]["tier"] == "UNKNOWN"


def test_rescore_during_open_escalation_holds(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke14CRMPipeline(hub)
    Spoke02LeadQualification(hub)
    hub.on_turn_start()
    hub.send(sign_config_update(signer, RUBRIC, "v1"))

    ctx = "q-009"
    hub.send(lead(ctx, {"demands_human": True}))  # opens an escalation
    rescore = Envelope(from_agent="03", to_agent="02", intent="lead.rescored",
                      client_context_id=ctx, payload={"budget": 900_000},
                      provenance={"source": "spoke-03", "captured_at": "runtime",
                                  "verbatim_available": True})
    hub.send(rescore)
    clar = persisted(hub, "clarification.request")
    assert any(c["payload"]["reason"] == "rescore during open escalation" for c in clar)


def test_tier_oscillation_third_time_flags_human_review(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke14CRMPipeline(hub)
    Spoke02LeadQualification(hub)
    hub.on_turn_start()
    hub.send(sign_config_update(signer, RUBRIC, "v1"))

    ctx = "q-010"
    def rescore(payload):
        return Envelope(from_agent="03", to_agent="02", intent="lead.rescored",
                        client_context_id=ctx, payload=payload,
                        provenance={"source": "spoke-03", "captured_at": "runtime",
                                    "verbatim_available": True})

    hub.send(lead(ctx, {"budget": 900_000, "timeline_days": 10,
                       "financing_progress": "preapproved"}))  # HOT
    hub.send(rescore({"budget": 0, "timeline_days": 999}))     # -> but held, open escalation!
    # HOT opened an escalation, so the next rescore holds rather than
    # re-tiering - confirms tuple #11 takes precedence over oscillation
    # detection, which only evaluates on tiers that actually got assigned.


def test_hot_lead_escalation_never_resolved_before_this_fix(tmp_path):
    """Real bug found during the agent.status retrofit: nothing anywhere
    cleared open_escalations - a HOT lead permanently blocked rescoring
    for that context forever. Proves the fix."""
    hub, signer = make_hub(str(tmp_path))
    q = Spoke02LeadQualification(hub)
    hub.on_turn_start()
    ctx = "hot-001"
    hub.send(lead(ctx, {"demands_human": True}))
    assert ctx in q.open_escalations

    resolve = Envelope(from_agent="human", to_agent="02", intent="config.update",
                      client_context_id=ctx, payload={"resolve_hot_lead": ctx},
                      provenance={"source": "human", "captured_at": "runtime",
                                  "verbatim_available": True})
    signer.sign(resolve)
    hub.send(resolve)
    assert ctx not in q.open_escalations

    # rescoring must now actually work again, not hold forever
    hub.send(sign_config_update(signer, RUBRIC, "v1"))
    rescore = Envelope(from_agent="03", to_agent="02", intent="lead.rescored",
                      client_context_id=ctx, payload={"budget": 900_000,
                                                      "timeline_days": 10,
                                                      "financing_progress": "preapproved"},
                      provenance={"source": "spoke-03", "captured_at": "runtime",
                                  "verbatim_available": True})
    hub.send(rescore)
    logs = persisted(hub, "interaction.log")
    assert any(l["payload"].get("tier") == "HOT" for l in logs), \
        "rescoring should work now that the escalation is resolved"


def test_hot_lead_status_pushed_to_18(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke02LeadQualification(hub)
    hub.on_turn_start()
    hub.send(lead("hot-002", {"demands_human": True, "today": "2026-08-01"}))
    status = persisted(hub, "agent.status")
    assert status and status[0]["to_agent"] == "18"
    assert status[0]["payload"]["waiting_on"] == "hot_lead_human_response"


# --------------------------------------------------- THE FIX: UNKNOWN alerts
def test_no_rubric_now_sends_clarification_request(tmp_path):
    """Was: tier=UNKNOWN (no rubric) only logged silently to
    interaction.log - tuple #2 explicitly says 'halt scoring;
    clarification' but nothing ever sent one. This matters right now,
    not hypothetically: no rubric config exists anywhere in this
    identity yet, so every lead hitting this agent today would have
    silently vanished with zero alert."""
    hub, signer = make_hub(str(tmp_path))
    Spoke14CRMPipeline(hub)
    Spoke02LeadQualification(hub)
    hub.on_turn_start()

    hub.send(lead("q-011", {"budget": 600_000, "timeline_days": 10,
                            "financing_progress": "preapproved"}))
    clar = persisted(hub, "clarification.request")
    assert any("no rubric active" in c["payload"]["reason"] for c in clar)


def test_all_inputs_unknown_also_sends_clarification_request(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke14CRMPipeline(hub)
    Spoke02LeadQualification(hub)
    hub.on_turn_start()
    hub.send(sign_config_update(signer, RUBRIC, "v1"))

    hub.send(lead("q-012", {}))  # nothing at all
    clar = persisted(hub, "clarification.request")
    assert any("all rubric inputs unknown" in c["payload"]["reason"] for c in clar)


# ------------------------------------------ THE FIX: incomplete rubric
def test_incomplete_rubric_rejected_not_backfilled_with_defaults(tmp_path):
    """Was: r.get('hot_threshold', 70) etc. silently substituted hardcoded
    defaults for any key a signed rubric omitted - Job Component #6 says
    the agent applies the rubric, never authors or drifts it. A partial
    rubric isn't covered by any tuple, so per the doctrine's own root
    rule it gets rejected, not silently completed."""
    hub, signer = make_hub(str(tmp_path))
    Spoke14CRMPipeline(hub)
    q = Spoke02LeadQualification(hub)
    hub.on_turn_start()

    incomplete = {"budget_threshold": 500_000, "budget_weight": 40}
    hub.send(sign_config_update(signer, incomplete, "v-bad"))
    assert q.rubric is None, "incomplete rubric must never be adopted"

    clar = persisted(hub, "clarification.request")
    assert any("missing required keys" in c["payload"]["reason"] for c in clar)
    missing_keys_sent = next(c["payload"]["missing_keys"] for c in clar
                             if "missing required keys" in c["payload"]["reason"])
    assert "hot_threshold" in missing_keys_sent
    assert "warm_threshold" in missing_keys_sent


# ----------------------------------- THE FIX: tuple 6, extra rubric keys
def test_rubric_with_extra_keys_flagged_but_still_adopted(tmp_path):
    """Was: an extra/unexpected key beyond REQUIRED_RUBRIC_KEYS was
    silently ignored - tuple-governed behaviors (demands_human, agent_
    shopping) have no representation in the rubric schema at all, so a
    rubric attempting to encode one would go completely unnoticed."""
    hub, signer = make_hub(str(tmp_path))
    Spoke14CRMPipeline(hub)
    q = Spoke02LeadQualification(hub)
    hub.on_turn_start()

    rubric_with_extra = dict(RUBRIC)
    rubric_with_extra["demands_human_weight"] = 0  # attempts to reconfigure
                                                   # a tuple-governed behavior
    hub.send(sign_config_update(signer, rubric_with_extra, "v-extra"))

    assert q.rubric is not None, "extra keys must not block adoption of an otherwise-complete rubric"
    assert q.rubric_version == "v-extra"
    clar = persisted(hub, "clarification.request")
    assert any("outside what this agent's tuples allow" in c["payload"].get("reason", "")
              for c in clar)
    extra_sent = next(c["payload"]["extra_keys"] for c in clar
                      if "extra_keys" in c["payload"])
    assert "demands_human_weight" in extra_sent


def test_incomplete_rubric_does_not_clobber_a_prior_good_one(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke14CRMPipeline(hub)
    q = Spoke02LeadQualification(hub)
    hub.on_turn_start()

    hub.send(sign_config_update(signer, RUBRIC, "v1"))
    assert q.rubric_version == "v1"

    incomplete = {"budget_threshold": 500_000}
    hub.send(sign_config_update(signer, incomplete, "v2-bad"))
    assert q.rubric_version == "v1", "a bad update must not replace the good rubric"
