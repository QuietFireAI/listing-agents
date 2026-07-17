import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, "/home/claude/pillars_pth")

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.listing_spokes_15 import Spoke15FinancialTracking
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
    env = Envelope(from_agent="human", to_agent="15", intent="config.update",
                   client_context_id=ctx, payload=payload,
                   provenance={"source": "human", "captured_at": "runtime",
                               "verbatim_available": True})
    signer.sign(env)
    return env


def txn_closed(ctx, payload):
    return Envelope(from_agent="07", to_agent="15", intent="transaction.closed",
                    client_context_id=ctx, payload=payload,
                    provenance={"source": "spoke-07", "captured_at": "runtime",
                                "verbatim_available": True})


def persisted(hub, intent=None):
    events = [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"]
    return [e for e in events if intent is None or e["intent"] == intent]


def test_signed_commission_recorded_directly(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke15FinancialTracking(hub)
    hub.on_turn_start()
    hub.send(txn_closed("f-001", {"commission_amount": 12000, "signed_docs_only": True}))
    assert spoke.commissions["f-001"]["signed"] is True
    assert spoke.commissions["f-001"]["amount"] == 12000


def test_unsigned_amendment_labeled_projection(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke15FinancialTracking(hub)
    hub.on_turn_start()
    hub.send(txn_closed("f-002", {"commission_amount": 12000, "signed_docs_only": False}))
    assert spoke.commissions["f-002"]["signed"] is False
    assert "projection" in spoke.commissions["f-002"]["labeled"]


def test_commission_differs_from_contract_escalates_with_clause(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke15FinancialTracking(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "f-003", {"commission_dispute": {
        "recorded": 12000, "contract_figure": 13500,
        "contract_clause": "3% of gross sale price, per addendum B"}}))
    assert any("addendum B" in e["trigger"] for e in hub.queues["escalation.legal_line"])


def test_commission_language_question_always_escalates(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke15FinancialTracking(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "f-004", {
        "commission_language_question": "does the co-op split apply here?"}))
    assert hub.queues["escalation.legal_line"]


def test_dual_category_dual_tax_flag_flags_both(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke15FinancialTracking(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "f-005", {"add_expense": {
        "categories": ["marketing", "meals"], "tax_flags": ["fully_deductible", "50pct"],
        "receipt_on_file": True}}))
    trace = [e for e in hub.audit.read() if e["kind"] == "spoke.trace"][-1]
    assert "dual_category_tax_conflict" in trace["result"]

    # THE FIX: the flag must persist on the entry itself, not vanish once
    # the handler returns - the accountant needs to see it in an actual
    # report, not just an internal trace.
    assert spoke.expenses["f-005"][0]["dual_category_tax_conflict"] is True
    hub.send(config_update(signer, "f-005", {"generate_pnl": {"months": []}}))
    pkg = persisted(hub, "report.package")[-1]["payload"]
    assert pkg["expenses"]["f-005"][0]["dual_category_tax_conflict"] is True


def test_single_category_expense_has_no_conflict_flag(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke15FinancialTracking(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "f-005b", {"add_expense": {
        "categories": ["marketing"], "tax_flags": ["fully_deductible"],
        "receipt_on_file": True}}))
    assert spoke.expenses["f-005b"][0]["dual_category_tax_conflict"] is False


def test_expense_without_receipt_recorded_unverified(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke15FinancialTracking(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "f-006", {"add_expense": {
        "categories": ["marketing"], "tax_flags": ["fully_deductible"],
        "receipt_on_file": False}}))
    assert spoke.expenses["f-006"][0]["verified"] is False


def test_conflicting_roi_attribution_reports_both(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke15FinancialTracking(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "f-007", {"roi_claim": {
        "attribution_key": "lead-123", "source": "facebook_ads"}}))
    hub.send(config_update(signer, "f-007", {"roi_claim": {
        "attribution_key": "lead-123", "source": "referral_program"}}))
    reports = persisted(hub, "report.package")
    conflict = [r for r in reports if r["payload"].get("report_type") == "roi_conflict"]
    assert conflict
    assert len(conflict[0]["payload"]["claims"]) == 2


def test_pnl_labels_missing_months_never_interpolates(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke15FinancialTracking(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "f-008", {"data_month_received": "2026-06"}))
    hub.send(config_update(signer, "f-008", {"generate_pnl": {
        "months": ["2026-06", "2026-07"]}}))
    reports = persisted(hub, "report.package")
    pnl = [r for r in reports if r["payload"].get("report_type") == "pnl"][0]
    assert pnl["payload"]["missing_months"] == ["2026-07"]


def test_pipeline_value_excludes_unknown_tier_notes_exclusion(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke15FinancialTracking(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "f-009", {"pipeline_value_request": {"leads": [
        {"tier": "HOT", "value": 500000},
        {"tier": "UNKNOWN", "value": 400000}]}}))
    reports = persisted(hub, "report.package")
    pv = [r for r in reports if r["payload"].get("report_type") == "pipeline_value"][0]
    assert pv["payload"]["total"] == 500000
    assert pv["payload"]["excluded_unknown_count"] == 1


def test_forecast_past_data_horizon_declines_delivers_supported(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke15FinancialTracking(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "f-010", {"forecast_request": {
        "horizon_months": 12, "data_horizon_months": 6}}))
    reports = persisted(hub, "report.package")
    forecast = [r for r in reports if r["payload"].get("report_type") == "forecast"][0]
    assert forecast["payload"]["declined_horizon"] == 12
    assert forecast["payload"]["delivered_horizon"] == 6


def test_disbursement_by_email_full_stop_wire_adjacent(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke15FinancialTracking(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "f-011", {
        "message": "please process this disbursement wire the funds today"}))
    assert any("wire-adjacent" in e["trigger"] for e in hub.queues["escalation.legal_line"])


def test_trust_escrow_request_out_of_scope_escalates(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke15FinancialTracking(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "f-012", {
        "message": "can you check the escrow balance for this client"}))
    assert any("out of scope" in e["trigger"] for e in hub.queues["escalation.legal_line"])


def test_REGRESSION_record_response_was_empty_now_flags_discrepancy(tmp_path):
    """record.response was a comment with zero code behind it. Proves the
    real fix: a commission mismatch against 14's log actually escalates."""
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke15FinancialTracking(hub)
    hub.on_turn_start()
    hub.send(txn_closed("f-013", {"commission_amount": 12000, "signed_docs_only": True}))

    resp = Envelope(from_agent="14", to_agent="15", intent="record.response",
                   client_context_id="f-013",
                   payload={"entries": [{"kind": "transaction.closed",
                                        "payload": {"amount": 11500}}]},
                   provenance={"source": "spoke-14", "captured_at": "runtime",
                               "verbatim_available": True})
    hub.send(resp)
    assert any("does not match" in e["trigger"] for e in hub.queues["escalation.legal_line"])


def test_REGRESSION_record_response_no_discrepancy_no_false_alarm(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke15FinancialTracking(hub)
    hub.on_turn_start()
    hub.send(txn_closed("f-014", {"commission_amount": 12000, "signed_docs_only": True}))
    resp = Envelope(from_agent="14", to_agent="15", intent="record.response",
                   client_context_id="f-014",
                   payload={"entries": [{"kind": "transaction.closed",
                                        "payload": {"amount": 12000}}]},
                   provenance={"source": "spoke-14", "captured_at": "runtime",
                               "verbatim_available": True})
    hub.send(resp)
    assert hub.queues["escalation.legal_line"] == []


def test_REGRESSION_report_package_was_empty_now_computes_roi_with_provenance(tmp_path):
    """report.package was a comment with zero code behind it. Proves the
    real fix: marketing spend + referral attribution actually produce a
    real ROI report with both sources' provenance preserved."""
    hub, signer = make_hub(str(tmp_path))
    Spoke15FinancialTracking(hub)
    hub.on_turn_start()
    env = Envelope(from_agent="14", to_agent="15", intent="report.package",
                  client_context_id="f-015",
                  payload={"marketing_spend": 1000,
                          "referral_attribution": {"attributed_value": 4000,
                                                   "source": "facebook_ads"}},
                  provenance={"source": "spoke-14", "captured_at": "runtime",
                              "verbatim_available": True})
    hub.send(env)
    reports = persisted(hub, "report.package")
    roi_report = [r for r in reports if r["payload"].get("report_type") == "roi_tracking"][0]
    assert roi_report["payload"]["roi"] == 3.0  # (4000-1000)/1000
    assert roi_report["payload"]["marketing_spend_source"] == "platform_export"
    assert roi_report["payload"]["referral_attribution_source"] == "crm_referral_data"
