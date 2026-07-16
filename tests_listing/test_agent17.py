import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, "/home/claude/pillars_pth")

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.listing_spokes_17 import Spoke17ComplianceFairHousing
from dispatcher.signatures import Ed25519Signer, Ed25519Verifier

IDENTITY_ROUTES = os.path.join(os.path.dirname(__file__), "..", "identity",
                               "routes.json")

RULESET = {"prohibited_phrases": [
    {"phrase": "no children", "rule_id": "FHA-familial-status"},
    {"phrase": "adults only", "rule_id": "FHA-familial-status"}],
    "state_rules": {"CA": [{"phrase": "no section 8", "rule_id": "CA-source-of-income"}]}}


def make_hub(tmp_path, **kw):
    audit_path = os.path.join(tmp_path, f"audit-{uuid.uuid4().hex[:8]}.jsonl")
    signer = Ed25519Signer()
    verifier = Ed25519Verifier(signer.public_key_bytes())
    hub = Hub(Routes(IDENTITY_ROUTES), AuditLog(audit_path),
             signature_verifier=verifier.verifier(), **kw)
    return hub, signer


def sign_ruleset(signer, ruleset=RULESET, version="v1"):
    env = Envelope(from_agent="human", to_agent="17", intent="config.update",
                   client_context_id="ruleset", payload={"ruleset": ruleset,
                                                         "version": version},
                   provenance={"source": "human", "captured_at": "runtime",
                               "verbatim_available": True})
    signer.sign(env)
    return env


def review(ctx, payload, frm="04"):
    return Envelope(from_agent=frm, to_agent="17", intent="content.review",
                    client_context_id=ctx, payload=payload,
                    provenance={"source": f"spoke-{frm}", "captured_at": "runtime",
                                "verbatim_available": True})


def persisted(hub, intent=None):
    events = [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"]
    return [e for e in events if intent is None or e["intent"] == intent]


def test_no_ruleset_fails_closed(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke17ComplianceFairHousing(hub)
    hub.on_turn_start()
    hub.send(review("c-001", {"draft": {"facts": ["3 bedrooms"]}}))
    assert not persisted(hub, "content.verdict")
    clar = persisted(hub, "clarification.request")
    assert any("no ruleset" in c["payload"]["reason"] for c in clar)


def test_clean_content_approved(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke17ComplianceFairHousing(hub)
    hub.on_turn_start()
    hub.send(sign_ruleset(signer))
    hub.send(review("c-002", {"draft": {"facts": ["3 bedrooms, great yard"]}}))
    verdicts = persisted(hub, "content.verdict")
    assert verdicts[0]["payload"]["verdict"] == "approved"
    assert verdicts[0]["payload"]["ruleset_version"] == "v1"


def test_prohibited_phrase_flagged_with_exact_phrase_and_rule(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke17ComplianceFairHousing(hub)
    hub.on_turn_start()
    hub.send(sign_ruleset(signer))
    hub.send(review("c-003", {"draft": {"facts": ["great for adults only"]}}))
    verdicts = persisted(hub, "content.verdict")
    assert verdicts[0]["payload"]["verdict"] == "flagged"
    finding = verdicts[0]["payload"]["findings"][0]
    assert finding["phrase"] == "adults only"
    assert finding["rule"] == "FHA-familial-status"


def test_state_specific_rule_applies_on_top_of_federal(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke17ComplianceFairHousing(hub)
    hub.on_turn_start()
    hub.send(sign_ruleset(signer))
    hub.send(review("c-004", {"draft": {"facts": ["no section 8 accepted"]}},
                    ))
    env = review("c-004b", {"draft": {"facts": ["no section 8 accepted"]},
                            "state": "CA"})
    hub.send(env)
    verdicts = [v for v in persisted(hub, "content.verdict")
               if v["client_context_id"] == "c-004b"]
    assert verdicts[0]["payload"]["verdict"] == "flagged"
    assert verdicts[0]["payload"]["findings"][0]["jurisdiction"] == "CA"


def test_resubmission_unchanged_flags_repeat_and_escalates(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke17ComplianceFairHousing(hub)
    hub.on_turn_start()
    hub.send(sign_ruleset(signer))
    payload = {"draft": {"facts": ["adults only community"]}, "content_hash": "same-hash"}
    hub.send(review("c-005", payload))
    hub.send(review("c-005", dict(payload)))
    assert hub.queues["escalation.legal_line"]
    verdicts = persisted(hub, "content.verdict")
    assert verdicts[-1]["payload"]["verdict"] == "flagged"


def test_missing_brokerage_id_blocks_no_format_exceptions(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke17ComplianceFairHousing(hub)
    hub.on_turn_start()
    hub.send(sign_ruleset(signer))
    hub.send(review("c-006", {"draft": {}, "content_type": "advertising",
                              "brokerage_id_present": False}))
    verdicts = persisted(hub, "content.verdict")
    assert verdicts[0]["payload"]["verdict"] == "flagged"
    assert any("brokerage identification" in f.get("reason", "")
              for f in verdicts[0]["payload"]["findings"])


def test_uncovered_construction_flagged_never_approved_by_omission(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke17ComplianceFairHousing(hub)
    hub.on_turn_start()
    hub.send(sign_ruleset(signer))
    hub.send(review("c-007", {"draft": {}, "uncovered_construction": True}))
    verdicts = persisted(hub, "content.verdict")
    assert verdicts[0]["payload"]["verdict"] == "flagged"
    assert any(f.get("uncovered") for f in verdicts[0]["payload"]["findings"])


def test_state_rule_uncertainty_blocks_and_escalates(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke17ComplianceFairHousing(hub)
    hub.on_turn_start()
    hub.send(sign_ruleset(signer))
    hub.send(review("c-008", {"draft": {}, "state_rule_uncertain": True}))
    assert hub.queues["escalation.legal_line"]
    verdicts = persisted(hub, "content.verdict")
    assert verdicts[0]["payload"]["verdict"] == "flagged"


def test_template_class_preapproval_refused(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke17ComplianceFairHousing(hub)
    hub.on_turn_start()
    hub.send(sign_ruleset(signer))
    hub.send(review("c-009", {"request_type": "template_class_preapproval"}))
    verdicts = persisted(hub, "content.verdict")
    assert verdicts[0]["payload"]["verdict"] == "flagged"
    assert "per-item" in verdicts[0]["payload"]["findings"][0]["reason"]


def test_near_miss_pattern_reported_after_third_flag(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke17ComplianceFairHousing(hub)
    hub.on_turn_start()
    hub.send(sign_ruleset(signer))
    for i in range(3):
        hub.send(review(f"c-010-{i}", {"draft": {"facts": ["adults only"]},
                                       "content_hash": f"hash-{i}"}, frm="04"))
    reports = persisted(hub, "report.package")
    pattern = [r for r in reports if r["payload"].get("report_type") == "near_miss_pattern"]
    assert pattern
    assert pattern[0]["payload"]["agent"] == "04"
    assert pattern[0]["payload"]["count"] == 3


def test_sla_within_bounds_no_alert(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke17ComplianceFairHousing(hub, sla_days=1)
    hub.on_turn_start()
    hub.send(sign_ruleset(signer))
    hub.send(review("c-011", {"draft": {}, "today": "2026-08-01"}))
    result = spoke.check_sla("c-011", "2026-08-01")
    assert result == "not_pending"  # already resolved same-turn, nothing pending


def test_sla_breach_alerts_using_tracked_state_not_caller_math(tmp_path):
    """Real fix proven: previously required the caller to pre-compute
    elapsed_seconds; pending_reviews was declared but never read."""
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke17ComplianceFairHousing(hub, sla_days=1)
    hub.on_turn_start()
    hub.send(sign_ruleset(signer))
    # manually simulate a review that's still pending (verdict not yet issued)
    spoke.pending_reviews["c-012"] = {"submitted_at": "2026-08-01", "agent": "04"}
    result = spoke.check_sla("c-012", "2026-08-05")
    assert result == "breached"
    clar = persisted(hub, "clarification.request")
    assert any("SLA breach" in c["payload"]["reason"] for c in clar)


def test_REGRESSION_resubmission_recomputes_fresh_not_stale_cache(tmp_path):
    """Real bug found on re-review: resubmission-unchanged used to return
    prior['findings'] WITHOUT recomputing - stale cached findings instead
    of a real check against the current ruleset. Directly violates tuple
    8 ('never rubber-stamp its own history'). If the ruleset changed
    between submissions (removing the phrase that caused the original
    flag), a fresh recheck should now correctly approve it, not blindly
    repeat the old flagged verdict."""
    hub, signer = make_hub(str(tmp_path))
    Spoke17ComplianceFairHousing(hub)
    hub.on_turn_start()
    hub.send(sign_ruleset(signer, ruleset={"prohibited_phrases": [
        {"phrase": "adults only", "rule_id": "FHA-familial-status"}]}, version="v1"))
    payload = {"draft": {"facts": ["adults only community"]}, "content_hash": "steer-hash"}
    hub.send(review("c-020", payload))
    verdicts = persisted(hub, "content.verdict")
    assert verdicts[-1]["payload"]["verdict"] == "flagged"

    # ruleset is corrected/updated to no longer prohibit this phrase
    hub.send(sign_ruleset(signer, ruleset={"prohibited_phrases": []}, version="v2"))
    hub.send(review("c-020", dict(payload)))
    verdicts = persisted(hub, "content.verdict")
    # fresh recompute against v2 ruleset - genuinely approved now, not a
    # stale repeat of the v1 flagged verdict
    assert verdicts[-1]["payload"]["verdict"] == "approved"
    assert verdicts[-1]["payload"]["ruleset_version"] == "v2"


def test_soft_steering_language_flagged_same_as_hard_prohibited(tmp_path):
    """tuple 6 explicitly says 'however soft' - confirming the mechanism
    actually catches a subtle real-estate steering phrase, not just the
    blunt examples already in the ruleset fixture."""
    hub, signer = make_hub(str(tmp_path))
    Spoke17ComplianceFairHousing(hub)
    hub.on_turn_start()
    steering_ruleset = {"prohibited_phrases": [
        {"phrase": "quiet family neighborhood", "rule_id": "steering-familial"}]}
    hub.send(sign_ruleset(signer, ruleset=steering_ruleset, version="v1"))
    hub.send(review("c-021", {"draft": {"facts": ["perfect quiet family neighborhood"]}}))
    verdicts = persisted(hub, "content.verdict")
    assert verdicts[0]["payload"]["verdict"] == "flagged"
    assert verdicts[0]["payload"]["findings"][0]["rule"] == "steering-familial"


def test_approved_content_resubmitted_does_not_falsely_trigger_repeat_escalation(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke17ComplianceFairHousing(hub)
    hub.on_turn_start()
    hub.send(sign_ruleset(signer))
    payload = {"draft": {"facts": ["lovely 3 bedroom home"]}, "content_hash": "clean-hash"}
    hub.send(review("c-022", payload))
    hub.send(review("c-022", dict(payload)))
    assert hub.queues["escalation.legal_line"] == []
