import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, "/home/claude/pillars_pth")

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.listing_spokes_07 import Spoke07TransactionCoordinator
from dispatcher.signatures import Ed25519Signer, Ed25519Verifier

IDENTITY_ROUTES = os.path.join(os.path.dirname(__file__), "..", "identity",
                               "routes.json")


def make_hub(tmp_path, **kw):
    audit_path = os.path.join(tmp_path, f"audit-{uuid.uuid4().hex[:8]}.jsonl")
    signer = Ed25519Signer()
    verifier = Ed25519Verifier(signer.public_key_bytes())
    hub = Hub(Routes(IDENTITY_ROUTES), AuditLog(audit_path),
             signature_verifier=verifier.verifier(), **kw)
    return hub, signer


def config_update(signer, ctx, payload):
    env = Envelope(from_agent="human", to_agent="07", intent="config.update",
                   client_context_id=ctx, payload=payload,
                   provenance={"source": "human", "captured_at": "runtime",
                               "verbatim_available": True})
    signer.sign(env)
    return env


def doc_status(ctx, payload):
    return Envelope(from_agent="08", to_agent="07", intent="doc.status",
                    client_context_id=ctx, payload=payload,
                    provenance={"source": "spoke-08", "captured_at": "runtime",
                                "verbatim_available": True})


def persisted(hub, intent=None):
    events = [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"]
    return [e for e in events if intent is None or e["intent"] == intent]


def test_timeline_init_requests_docs_and_vendors(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke07TransactionCoordinator(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "t-001", {"timeline_init": {
        "inspection": "2026-08-10", "appraisal": "2026-08-15",
        "closing": "2026-09-01"}}))
    docs = persisted(hub, "doc.request")
    assert {d["payload"]["milestone"] for d in docs} == {"inspection", "appraisal"}
    vendors = persisted(hub, "vendor.request")
    assert {v["payload"]["milestone"] for v in vendors} == {"inspection", "appraisal"}


def test_wire_topic_full_stops_regardless_of_intent(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke07TransactionCoordinator(hub)
    hub.on_turn_start()
    hub.send(doc_status("t-002", {"milestone": "closing",
                                 "note": "please confirm updated wire instructions"}))
    assert hub.queues["escalation.legal_line"]
    assert not persisted(hub, "interaction.log")


def test_artifact_contradicts_tracked_deadline_halts(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke07TransactionCoordinator(hub)
    hub.on_turn_start()
    hub.send(doc_status("t-003", {"milestone": "title",
                                 "contradicts_tracked_deadline": True}))
    assert hub.queues["escalation.legal_line"]


def test_inspection_report_with_repairs_escalates_negotiation(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke07TransactionCoordinator(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "t-004", {"timeline_init": {"inspection": "2026-08-10"}}))
    hub.send(doc_status("t-004", {"milestone": "inspection", "report_received": True,
                                 "repair_requests_present": True}))
    assert hub.queues["escalation.legal_line"]
    assert spoke.timelines["t-004"]["inspection"]["satisfied"] is True


def test_appraisal_below_contract_escalates_never_drafts_strategy(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke07TransactionCoordinator(hub)
    hub.on_turn_start()
    hub.send(doc_status("t-005", {"milestone": "appraisal", "appraised_value": 450_000,
                                 "contract_price": 480_000}))
    assert hub.queues["escalation.legal_line"]
    assert any("all options are human" in e["trigger"]
              for e in hub.queues["escalation.legal_line"])


def test_title_exception_logged_verbatim_never_characterized(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke07TransactionCoordinator(hub)
    hub.on_turn_start()
    hub.send(doc_status("t-006", {"milestone": "title", "exception_found": True,
                                 "exception_text": "unresolved lien from 2019"}))
    assert any("unresolved lien from 2019" in e["trigger"]
              for e in hub.queues["escalation.legal_line"])


def test_earnest_money_unconfirmed_escalates_no_benefit_of_doubt(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke07TransactionCoordinator(hub)
    hub.on_turn_start()
    hub.send(doc_status("t-007", {"milestone": "earnest_money",
                                 "receipt_confirmed": False}))
    assert hub.queues["escalation.legal_line"]


def test_extension_claim_without_amendment_tracks_original(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke07TransactionCoordinator(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "t-008", {"timeline_init": {"financing_contingency": "2026-08-20"}}))
    hub.send(config_update(signer, "t-008", {"extension_claim": {
        "milestone": "financing_contingency", "amendment_on_file": False}}))
    assert spoke.timelines["t-008"]["financing_contingency"]["deadline"] == "2026-08-20"
    alerts = persisted(hub, "deadline.alert")
    assert any(a["payload"].get("kind") == "unconfirmed_extension_claim" for a in alerts)


def test_possession_terms_ambiguous_quotes_exact_clause(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke07TransactionCoordinator(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "t-009", {"possession_terms_ambiguous": {
        "clause_text": "possession within a reasonable time of closing"}}))
    clar = persisted(hub, "clarification.request")
    assert any(c["payload"]["exact_clause"] ==
              "possession within a reasonable time of closing" for c in clar)


def test_closing_satisfied_emits_transaction_closed_to_all_three(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke07TransactionCoordinator(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "t-010", {"timeline_init": {"closing": "2026-09-01"}}))
    hub.send(doc_status("t-010", {"milestone": "closing", "artifact_on_file": True,
                                 "artifact_ref": "settlement-stmt-1"}))
    closed = persisted(hub, "transaction.closed")
    assert {c["to_agent"] for c in closed} == {"16", "14", "15"}
    assert spoke.timelines["t-010"]["closing"]["satisfied"] is True


def test_multiple_deadlines_same_day_alert_individually(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke07TransactionCoordinator(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "t-011", {"timeline_init": {
        "title": "2026-08-20", "hoa_docs": "2026-08-20"}}))
    due = spoke.check_deadlines("t-011", "2026-08-20")
    assert len(due) == 2
    alerts = persisted(hub, "deadline.alert")
    milestones_alerted = {a["payload"].get("milestone") for a in alerts
                         if a["to_agent"] == "11"}
    assert milestones_alerted == {"title", "hoa_docs"}


def test_financing_contingency_passes_unremoved_alerts_same_hour(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke07TransactionCoordinator(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "t-012", {"timeline_init": {
        "financing_contingency": "2026-08-20"}}))
    spoke.check_deadlines("t-012", "2026-08-20")
    assert any("financing contingency" in e["trigger"]
              for e in hub.queues["escalation.legal_line"])


def test_wire_topic_caught_even_when_nested_in_dict(tmp_path):
    """Re-review found this: the original wire-check only scanned top-level
    string values, missing wire language buried in a nested dict payload."""
    hub, signer = make_hub(str(tmp_path))
    Spoke07TransactionCoordinator(hub)
    hub.on_turn_start()
    env = Envelope(from_agent="08", to_agent="07", intent="doc.status",
                  client_context_id="t-013",
                  payload={"milestone": "closing",
                          "note": {"message": "please confirm updated wire instructions"}},
                  provenance={"source": "spoke-08", "captured_at": "runtime",
                              "verbatim_available": True})
    hub.send(env)
    assert hub.queues["escalation.legal_line"]


def test_vendor_deliverable_clears_pending_holdup_timer(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke07TransactionCoordinator(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "t-014", {"timeline_init": {
        "inspection": "2026-08-10"}, "today": "2026-08-01"}))
    assert spoke.vendor_requests_pending["t-014"]["inspection"] == "2026-08-01"

    env = Envelope(from_agent="09", to_agent="07", intent="deliverable.release",
                  client_context_id="t-014", payload={"milestone": "inspection"},
                  provenance={"source": "spoke-09", "captured_at": "runtime",
                              "verbatim_available": True})
    hub.send(env)
    assert "inspection" not in spoke.vendor_requests_pending.get("t-014", {})


def test_vendor_holdup_past_7_days_escalates_to_hitl(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke07TransactionCoordinator(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "t-015", {"timeline_init": {
        "appraisal": "2026-09-01"}, "today": "2026-08-01"}))

    flagged = spoke.check_vendor_holdups("t-015", "2026-08-07")  # 6 days
    assert flagged == []
    flagged = spoke.check_vendor_holdups("t-015", "2026-08-08")  # 7 days
    assert flagged == ["appraisal"]
    assert any("hold-up to HITL" in e["trigger"]
              for e in hub.queues["escalation.legal_line"])
