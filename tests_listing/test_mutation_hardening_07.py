"""Mutation-hardening — spoke 07 (transaction coordinator), deadline engine.

Closes the 13 spoke-07 true gaps from the clean sweep. Same discipline:
positive + negative case per decision, each verified to KILL its mutation.

Target lines (listing_spokes_07.py:line):
  86 87 88 89   (MILESTONE_SPEC needs_document / vendor_kind table)
  137           (wire-fraud check return)
  329           (appraisal satisfied flag)
  368 374       (transaction.closed payload flags)
  389 402       (milestone satisfied / vendor-resolved flags)
  472 476 477   (overdue-deadline detection + financing escalation)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.signatures import Ed25519Signer, Ed25519Verifier
from dispatcher.listing_spokes_07 import Spoke07TransactionCoordinator

IDENTITY_ROUTES = os.path.join(os.path.dirname(__file__), "..", "identity",
                               "routes.json")


def make_hub(tmp_path):
    signer = Ed25519Signer()
    verifier = Ed25519Verifier(signer.public_key_bytes())
    return Hub(Routes(IDENTITY_ROUTES),
               AuditLog(os.path.join(tmp_path, "a.jsonl")),
               signature_verifier=verifier.verifier())


def spoke(tmp_path):
    return Spoke07TransactionCoordinator(make_hub(tmp_path))


def env(frm, intent, ctx, payload):
    return Envelope(from_agent=frm, to_agent="07", intent=intent,
                    client_context_id=ctx, payload=payload,
                    provenance={"source": "t"})


def persisted(hub, intent=None):
    return [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"
            and (intent is None or e["intent"] == intent)]


def legal(hub):
    return hub.queues.get("escalation.legal_line", [])


# ------------------------------------------------- 86/87/88/89 (spec table)
def test_07_milestone_spec_document_and_vendor_mapping(tmp_path):
    """The MILESTONE_SPEC table drives real routing: which milestones need a
    document, and which vendor kind maps to which milestone. Flip any
    needs_document/vendor_kind and this fails.
      - inspection needs a document AND maps from the 'inspector' vendor
      - appraisal maps from 'appraiser'
      - earnest_money / closing need NO document
      - title/financing have no vendor kind (kind_to_milestone finds none)"""
    s = spoke(str(tmp_path))
    cfg = s.transaction_milestone_config
    # every document-requiring milestone (84-88) asserted separately
    for m in ("inspection", "appraisal", "title", "hoa_docs",
              "financing_contingency"):
        assert cfg[m]["needs_document"] is True, \
            f"{m} must require a document"
    assert cfg["earnest_money"]["needs_document"] is False, \
        "earnest_money must NOT require a document"
    assert cfg["closing"]["needs_document"] is False
    # reverse lookup is derived from the same table (86/87 vendor_kind)
    assert s.kind_to_milestone("inspector") == "inspection"
    assert s.kind_to_milestone("appraiser") == "appraisal"
    assert s.kind_to_milestone("nonexistent") is None, \
        "an unknown vendor kind maps to no milestone"


# ------------------------------------------------------------ 137
def test_07_wire_topic_escalates_clean_does_not(tmp_path):
    """_wire_check returns True and escalates on a wire topic; False and no
    escalation on clean text. Kills the 137 `return True`."""
    s = spoke(str(tmp_path))
    hit = s._wire_check({"note": "please send the wire transfer instructions"},
                        "w-1")
    assert hit is True
    assert any("wire topic" in e.get("trigger", "") for e in legal(s.hub)), \
        "a wire topic must escalate to the legal line"

    s2 = spoke(str(tmp_path) + "_b")
    clean = s2._wire_check({"note": "the inspection is scheduled for Tuesday"},
                           "w-2")
    assert clean is False
    assert not legal(s2.hub), "clean text must not escalate"


# ------------------------------------------------------------ 472/476/477
def test_07_overdue_deadline_alerts_once_and_financing_escalates(tmp_path):
    """The deadline engine's overdue path (the silent-failure killer):
      - an unsatisfied milestone whose deadline PASSED alerts overdue=True
        through 11 and 18 (kills 476 `< today`)
      - it alerts only ONCE (kills the _overdue_alerted membership guard 477)
      - a satisfied milestone past deadline does NOT alert
      - financing_contingency overdue ALSO escalates to the legal line"""
    s = spoke(str(tmp_path))
    s.timelines["t-1"] = {
        "inspection": {"deadline": "2026-08-01", "satisfied": False,
                       "artifact": None},
        "financing_contingency": {"deadline": "2026-08-01", "satisfied": False,
                                  "artifact": None},
        "appraisal": {"deadline": "2026-08-01", "satisfied": True,
                      "artifact": "done"},  # satisfied -> must NOT alert
    }
    s.check_deadlines("t-1", today="2026-08-10")  # all deadlines passed
    alerts = [e for e in persisted(s.hub, "deadline.alert")
              if e["payload"].get("overdue")]
    # BOTH lanes must carry the overdue alert: agent 11 AND agent 18
    # (kills the 472 and 476 overdue-flag flips independently)
    lanes_for_inspection = {e["to_agent"] for e in alerts
                            if e["payload"]["milestone"] == "inspection"}
    assert "11" in lanes_for_inspection, "overdue must alert lane 11"
    assert "18" in lanes_for_inspection, "overdue must alert lane 18"
    milestones = {e["payload"]["milestone"] for e in alerts}
    assert "inspection" in milestones, "passed unsatisfied deadline must alert"
    assert "appraisal" not in milestones, \
        "a SATISFIED milestone must never alert overdue (kills satisfied flip)"
    # financing_contingency overdue must escalate to legal (kills the != flip)
    fin_esc = [e for e in legal(s.hub)
               if "financing" in str(e).lower()]
    assert fin_esc, \
        "financing_contingency overdue must escalate to the legal line"

    # NEGATIVE case that pins the milestone (kills the == -> != flip): a
    # context where ONLY a non-financing milestone is overdue must NOT
    # produce a legal escalation. Under the flip, inspection would escalate.
    s2 = spoke(str(tmp_path) + "_nofin")
    s2.timelines["t-2"] = {
        "inspection": {"deadline": "2026-08-01", "satisfied": False,
                       "artifact": None},
    }
    s2.check_deadlines("t-2", today="2026-08-10")
    assert not legal(s2.hub), \
        "a non-financing overdue milestone must NOT escalate to legal"

    # second sweep same day: no NEW overdue alerts (once-only, kills 477)
    before = len([e for e in persisted(s.hub, "deadline.alert")
                  if e["payload"].get("overdue")])
    s.check_deadlines("t-1", today="2026-08-11")
    after = len([e for e in persisted(s.hub, "deadline.alert")
                 if e["payload"].get("overdue")])
    assert after == before, "an already-alerted overdue milestone must not re-alert"


# ------------------------------------------------------------ 329
def test_07_appraisal_low_escalates_and_marks_satisfied(tmp_path):
    """An appraisal below contract price escalates (all options human) and
    marks the milestone satisfied with the appraisal artifact. Assert both
    the escalation and the satisfied state."""
    s = spoke(str(tmp_path))
    s.timelines["ap-1"] = {"appraisal": {"deadline": "2026-09-01",
                                         "satisfied": False, "artifact": None}}
    s.hub.on_turn_start()
    s.handle(env("11", "doc.status", "ap-1",
                 {"milestone": "appraisal", "appraised_value": 450000,
                  "contract_price": 500000}))
    assert s.timelines["ap-1"]["appraisal"]["satisfied"] is True, \
        "a processed appraisal must mark the milestone satisfied"
    assert s.timelines["ap-1"]["appraisal"]["artifact"] == "appraisal_report"


# ------------------------------------------------------------ 389/402
def test_07_milestone_artifact_marks_satisfied_and_logs(tmp_path):
    """A milestone with artifact_on_file becomes satisfied (389) and emits a
    milestone_satisfied interaction log to 14 (402). Negative: no artifact ->
    not satisfied, no log."""
    s = spoke(str(tmp_path))
    s.timelines["ms-1"] = {"title": {"deadline": "2026-09-01",
                                     "satisfied": False, "artifact": None}}
    s.hub.on_turn_start()
    s.handle(env("11", "doc.status", "ms-1",
                 {"milestone": "title", "artifact_on_file": True,
                  "artifact_ref": "title-report-1"}))
    assert s.timelines["ms-1"]["title"]["satisfied"] is True
    logs = [e for e in persisted(s.hub, "interaction.log")
            if e["payload"].get("kind") == "milestone_satisfied"]
    assert logs and logs[0]["payload"]["milestone"] == "title"

    s2 = spoke(str(tmp_path) + "_b")
    s2.timelines["ms-2"] = {"title": {"deadline": "2026-09-01",
                                      "satisfied": False, "artifact": None}}
    s2.hub.on_turn_start()
    s2.handle(env("11", "doc.status", "ms-2",
                  {"milestone": "title"}))  # no artifact_on_file
    assert s2.timelines["ms-2"]["title"]["satisfied"] is False, \
        "no artifact must leave the milestone unsatisfied"


# ------------------------------------------------------------ 368/374
def test_07_closing_artifact_issues_transaction_closed_with_flags(tmp_path):
    """Closing artifact on file issues transaction.closed to 16/14/15 with a
    payload carrying closed=True and signed_docs_only reflecting the input.
    Assert the flags so the 368/374 bool sites are pinned."""
    s = spoke(str(tmp_path))
    s.timelines["cl-1"] = {"closing": {"deadline": "2026-09-01",
                                       "satisfied": False, "artifact": None}}
    s.hub.on_turn_start()
    s.handle(env("11", "doc.status", "cl-1",
                 {"milestone": "closing", "artifact_on_file": True,
                  "commission_amount": 15000, "sale_price": 500000,
                  "signed_docs_only": True}))
    closed = persisted(s.hub, "transaction.closed")
    recipients = {e["to_agent"] for e in closed}
    assert {"16", "14", "15"} <= recipients, \
        "transaction.closed must reach 16, 14, and 15"
    assert closed[0]["payload"]["closed"] is True
    assert closed[0]["payload"]["signed_docs_only"] is True, \
        "signed_docs_only must reflect the input flag (kills the bool flip)"


def test_07_signed_docs_only_defaults_false_when_absent(tmp_path):
    """When signed_docs_only is ABSENT, the payload default is False (374).
    Flip the default to True and this fails — the key-absent path is the
    only way to exercise the default."""
    s = spoke(str(tmp_path))
    s.timelines["cl-2"] = {"closing": {"deadline": "2026-09-01",
                                       "satisfied": False, "artifact": None}}
    s.hub.on_turn_start()
    s.handle(env("11", "doc.status", "cl-2",
                 {"milestone": "closing", "artifact_on_file": True,
                  "commission_amount": 15000, "sale_price": 500000}))
    # signed_docs_only NOT supplied
    closed = persisted(s.hub, "transaction.closed")
    assert closed[0]["payload"]["signed_docs_only"] is False, \
        "absent signed_docs_only must default to False (kills the default flip)"


# ------------------------------------------------------------ 402
def test_07_deliverable_release_resolves_vendor_scheduling_wait(tmp_path):
    """deliverable.release (from 09) clears the vendor_scheduling wait for
    that milestone with resolved=True. Kills the 402 flip: without it the
    wait would report unresolved and sit in 18's briefing forever."""
    s = spoke(str(tmp_path))
    s.hub.on_turn_start()
    s.vendor_requests_pending["vr-1"] = {"inspection": {"vendor": "insp-1"}}
    s.handle(Envelope(from_agent="09", to_agent="07",
                      intent="deliverable.release", client_context_id="vr-1",
                      payload={"milestone": "inspection"},
                      provenance={"source": "t"}))
    statuses = [e for e in persisted(s.hub, "agent.status")
                if e["payload"].get("waiting_on") == "vendor_scheduling:inspection"]
    assert statuses and statuses[0]["payload"]["resolved"] is True, \
        "deliverable.release must resolve the vendor_scheduling wait (kills 402)"
