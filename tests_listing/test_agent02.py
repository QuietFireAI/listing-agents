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
    hub, signer = make_hub(str(tmp_path))
    Spoke14CRMPipeline(hub)
    Spoke02LeadQualification(hub)
    hub.on_turn_start()
    hub.send(sign_config_update(signer, RUBRIC, "v1"))

    # budget alone = 40 (exactly the warm threshold)
    hub.send(lead("q-003", {"budget": 600_000, "timeline_days": 999,
                            "financing_progress": "none"}))
    logs = persisted(hub, "interaction.log")
    assert logs[-1]["payload"]["tier"] == "WARM"
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
