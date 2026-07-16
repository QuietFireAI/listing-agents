import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, "/home/claude/pillars_pth")

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.listing_spokes_09 import Spoke09VendorCoordination

IDENTITY_ROUTES = os.path.join(os.path.dirname(__file__), "..", "identity",
                               "routes.json")

ROSTER = {"insp-1": {"kind": "inspector", "license_expiry": "2027-01-01",
                     "insurance_expiry": "2027-01-01"},
         "appr-1": {"kind": "appraiser", "license_expiry": "2026-01-01",
                    "insurance_expiry": "2027-01-01"}}  # license expired


def make_hub(tmp_path, **kw):
    audit_path = os.path.join(tmp_path, f"audit-{uuid.uuid4().hex[:8]}.jsonl")
    return Hub(Routes(IDENTITY_ROUTES), AuditLog(audit_path), **kw)


def vendor_req(ctx, payload, frm="07"):
    return Envelope(from_agent=frm, to_agent="09", intent="vendor.request",
                    client_context_id=ctx, payload=payload,
                    provenance={"source": f"spoke-{frm}", "captured_at": "runtime",
                                "verbatim_available": True})


def vendor_event(ctx, payload):
    return Envelope(from_agent="external", to_agent="09", intent="vendor.event",
                    client_context_id=ctx, payload=payload,
                    provenance={"source": "external", "captured_at": "runtime",
                                "verbatim_available": True})


def persisted(hub, intent=None):
    events = [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"]
    return [e for e in events if intent is None or e["intent"] == intent]


def test_roster_vendor_current_credentials_schedules(tmp_path):
    hub = make_hub(str(tmp_path))
    spoke = Spoke09VendorCoordination(hub, roster=ROSTER)
    hub.on_turn_start()
    hub.send(vendor_req("v-001", {"vendor_id": "insp-1", "kind": "inspector",
                                  "today": "2026-08-01"}))
    assert spoke.scheduled["v-001"]["inspector"]["confirmed"] is True
    sched = persisted(hub, "vendor.schedule")
    assert sched and sched[0]["to_agent"] == "external"


def test_off_roster_vendor_refused_never_scheduled(tmp_path):
    hub = make_hub(str(tmp_path))
    spoke = Spoke09VendorCoordination(hub, roster=ROSTER)
    hub.on_turn_start()
    hub.send(vendor_req("v-002", {"vendor_id": "unknown-vendor", "kind": "inspector"}))
    assert "v-002" not in spoke.scheduled
    clar = persisted(hub, "clarification.request")
    assert any("not on the approved roster" in c["payload"]["reason"] for c in clar)


def test_expired_credentials_flagged_before_scheduling(tmp_path):
    hub = make_hub(str(tmp_path))
    spoke = Spoke09VendorCoordination(hub, roster=ROSTER)
    hub.on_turn_start()
    hub.send(vendor_req("v-003", {"vendor_id": "appr-1", "kind": "appraiser",
                                  "today": "2026-08-01"}))
    assert "v-003" not in spoke.scheduled
    assert hub.queues["escalation.legal_line"]


def test_same_slot_lower_priority_loses(tmp_path):
    hub = make_hub(str(tmp_path))
    spoke = Spoke09VendorCoordination(hub, roster=ROSTER)
    hub.on_turn_start()
    hub.send(vendor_req("v-004a", {"vendor_id": "insp-1", "kind": "inspector",
                                   "today": "2026-08-01", "slot_key": "slot-1",
                                   "deadline_priority": 5}))
    hub.send(vendor_req("v-004b", {"vendor_id": "insp-1", "kind": "inspector",
                                   "today": "2026-08-01", "slot_key": "slot-1",
                                   "deadline_priority": 2}))
    assert "v-004a" in spoke.scheduled
    assert "v-004b" not in spoke.scheduled


def test_tied_priority_goes_to_human(tmp_path):
    hub = make_hub(str(tmp_path))
    spoke = Spoke09VendorCoordination(hub, roster=ROSTER)
    hub.on_turn_start()
    hub.send(vendor_req("v-005a", {"vendor_id": "insp-1", "kind": "inspector",
                                   "today": "2026-08-01", "slot_key": "slot-2",
                                   "deadline_priority": 5}))
    hub.send(vendor_req("v-005b", {"vendor_id": "insp-1", "kind": "inspector",
                                   "today": "2026-08-01", "slot_key": "slot-2",
                                   "deadline_priority": 5,
                                   "existing_slot_priority": 5}))
    clar = persisted(hub, "clarification.request")
    assert any("tied priority" in c["payload"]["reason"] for c in clar)


def test_regulated_late_cancellation_never_auto_rebooked(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke09VendorCoordination(hub, roster=ROSTER)
    hub.on_turn_start()
    hub.send(vendor_event("v-006", {"event_kind": "cancellation",
                                    "kind": "inspector", "regulated": True}))
    clar = persisted(hub, "clarification.request")
    assert any("never auto-rebooked" in c["payload"]["reason"] for c in clar)
    logs = persisted(hub, "interaction.log")
    assert any(l["payload"].get("kind") == "vendor_cancelled_late" for l in logs)


def test_rate_change_halts_to_human(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke09VendorCoordination(hub, roster=ROSTER)
    hub.on_turn_start()
    hub.send(vendor_event("v-007", {"event_kind": "rate_change"}))
    assert any("RESPA" in e["trigger"] for e in hub.queues["escalation.legal_line"])


def test_access_code_request_never_transmitted(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke09VendorCoordination(hub, roster=ROSTER)
    hub.on_turn_start()
    hub.send(vendor_event("v-008", {"event_kind": "access_code_request"}))
    assert hub.queues["escalation.legal_line"]


def test_deliverable_without_proof_stays_open(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke09VendorCoordination(hub, roster=ROSTER)
    hub.on_turn_start()
    hub.send(vendor_event("v-009", {"event_kind": "deliverable_report",
                                    "kind": "inspector",
                                    "proof_artifact_present": False}))
    assert not persisted(hub, "deliverable.release")


def test_partial_deliverable_reports_partial(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke09VendorCoordination(hub, roster=ROSTER)
    hub.on_turn_start()
    hub.send(vendor_event("v-010", {"event_kind": "deliverable_report",
                                    "kind": "inspector",
                                    "proof_artifact_present": True,
                                    "partial": True, "doc_type": "inspection_report"}))
    rel = persisted(hub, "deliverable.release")
    assert rel and rel[0]["payload"]["partial"] is True
    assert rel[0]["to_agent"] == "08"


def test_complete_deliverable_routes_correctly(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke09VendorCoordination(hub, roster=ROSTER)
    hub.on_turn_start()
    hub.send(vendor_event("v-011", {"event_kind": "deliverable_report",
                                    "kind": "photography",
                                    "proof_artifact_present": True,
                                    "doc_type": "photos", "content_hash": "ph-1"}))
    rel = persisted(hub, "deliverable.release")
    assert rel[0]["to_agent"] == "05"


def test_invoice_variance_logged_and_escalated_never_approved(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke09VendorCoordination(hub, roster=ROSTER)
    hub.on_turn_start()
    hub.send(vendor_event("v-012", {"event_kind": "invoice",
                                    "quote_amount": 400, "invoice_amount": 550}))
    logs = persisted(hub, "interaction.log")
    assert any(l["payload"].get("kind") == "invoice_variance" for l in logs)
    assert hub.queues["escalation.legal_line"]


def test_no_show_deadline_critical_never_self_substituted(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke09VendorCoordination(hub, roster=ROSTER)
    hub.on_turn_start()
    hub.send(vendor_event("v-013", {"event_kind": "no_show", "deadline_critical": True}))
    clar = persisted(hub, "clarification.request")
    assert any("never self-substituted" in c["payload"]["reason"] for c in clar)


def test_scope_change_requires_human_approval(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke09VendorCoordination(hub, roster=ROSTER)
    hub.on_turn_start()
    hub.send(vendor_event("v-014", {"event_kind": "scope_change"}))
    clar = persisted(hub, "clarification.request")
    assert any("human-approved" in c["payload"]["reason"] for c in clar)
