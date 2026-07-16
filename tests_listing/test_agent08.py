import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, "/home/claude/pillars_pth")

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.listing_spokes_08 import Spoke08DocumentCollection
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


def doc_req(ctx, payload):
    return Envelope(from_agent="07", to_agent="08", intent="doc.request",
                    client_context_id=ctx, payload=payload,
                    provenance={"source": "spoke-07", "captured_at": "runtime",
                                "verbatim_available": True})


def doc_submission(ctx, payload, frm="11"):
    return Envelope(from_agent=frm, to_agent="08", intent="document.submission",
                    client_context_id=ctx, payload=payload,
                    provenance={"source": f"spoke-{frm}", "captured_at": "runtime",
                                "verbatim_available": True})


def deliverable(ctx, payload):
    return Envelope(from_agent="09", to_agent="08", intent="deliverable.release",
                    client_context_id=ctx, payload=payload,
                    provenance={"source": "spoke-09", "captured_at": "runtime",
                                "verbatim_available": True})


def persisted(hub, intent=None):
    events = [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"]
    return [e for e in events if intent is None or e["intent"] == intent]


def test_doc_request_chases_via_11_never_directly(tmp_path):
    hub, _ = make_hub(str(tmp_path))
    Spoke08DocumentCollection(hub)
    hub.on_turn_start()
    hub.send(doc_req("d-001", {"doc_type": "preapproval_letter"}))
    msgs = persisted(hub, "client.message.request")
    assert msgs and msgs[0]["to_agent"] == "11"


def test_clean_document_files_and_reports_received(tmp_path):
    hub, _ = make_hub(str(tmp_path))
    spoke = Spoke08DocumentCollection(hub)
    hub.on_turn_start()
    hub.send(doc_submission("d-002", {"doc_type": "preapproval_letter",
                                      "opens_correctly": True,
                                      "content_hash": "abc123"}))
    assert spoke.filed_documents["d-002"][0]["doc_type"] == "preapproval_letter"
    status = persisted(hub, "doc.status")
    assert status and status[0]["payload"]["status"] == "received"


def test_wrong_document_type_never_filed_as_substitute(tmp_path):
    hub, _ = make_hub(str(tmp_path))
    spoke = Spoke08DocumentCollection(hub)
    hub.on_turn_start()
    hub.send(doc_req("d-003", {"doc_type": "preapproval_letter"}))
    hub.send(doc_submission("d-003", {"doc_type": "prequalification"}))
    assert "d-003" not in spoke.filed_documents
    msgs = persisted(hub, "client.message.request")
    assert any(m["payload"].get("template") == "wrong_document_type" for m in msgs)


def test_partial_deliverable_reports_partial_never_marks_collected(tmp_path):
    hub, _ = make_hub(str(tmp_path))
    spoke = Spoke08DocumentCollection(hub)
    hub.on_turn_start()
    hub.send(deliverable("d-003b", {"doc_type": "inspection_report",
                                    "partial": True}))
    assert "d-003b" not in spoke.filed_documents
    status = persisted(hub, "doc.status")
    assert status[0]["payload"]["status"] == "partial"


def test_unreadable_document_requests_resend_logs_raw_error(tmp_path):
    hub, _ = make_hub(str(tmp_path))
    Spoke08DocumentCollection(hub)
    hub.on_turn_start()
    hub.send(doc_submission("d-004", {"doc_type": "title_commitment",
                                      "opens_correctly": False,
                                      "open_error": "PDF header corrupt at byte 12"}))
    logs = persisted(hub, "interaction.log")
    assert any(l["payload"].get("raw_error") == "PDF header corrupt at byte 12"
              for l in logs)


def test_wire_instructions_quarantined_never_filed(tmp_path):
    hub, _ = make_hub(str(tmp_path))
    spoke = Spoke08DocumentCollection(hub)
    hub.on_turn_start()
    hub.send(doc_submission("d-005", {"doc_type": "amendment",
                                      "notes": "see attached updated wire instructions"}))
    assert "d-005" not in spoke.filed_documents
    assert hub.queues["escalation.legal_line"]


def test_unexpected_sender_for_sensitive_doc_quarantined(tmp_path):
    hub, _ = make_hub(str(tmp_path))
    spoke = Spoke08DocumentCollection(hub, expected_senders={
        "d-006": {"preapproval_letter": {"lender@expected-bank.com"}}})
    hub.on_turn_start()
    hub.send(doc_submission("d-006", {"doc_type": "preapproval_letter",
                                      "opens_correctly": True,
                                      "submitting_party": "someone@random.com"}))
    assert "d-006" not in spoke.filed_documents
    assert hub.queues["escalation.legal_line"]


def test_unsigned_where_signature_expected_stays_incomplete(tmp_path):
    hub, _ = make_hub(str(tmp_path))
    Spoke08DocumentCollection(hub)
    hub.on_turn_start()
    hub.send(doc_submission("d-007", {"doc_type": "amendment",
                                      "signature_expected": True, "signed": False}))
    status = persisted(hub, "doc.status")
    assert status[0]["payload"]["status"] == "incomplete"


def test_conflicting_versions_keeps_both_never_picks(tmp_path):
    hub, _ = make_hub(str(tmp_path))
    spoke = Spoke08DocumentCollection(hub)
    hub.on_turn_start()
    hub.send(doc_submission("d-008", {"doc_type": "title_commitment",
                                      "version_conflict_key": "v1",
                                      "content_hash": "hash-a"}))
    hub.send(doc_submission("d-008", {"doc_type": "title_commitment",
                                      "version_conflict_key": "v1",
                                      "content_hash": "hash-b"}))
    assert len(spoke.filed_documents["d-008"]) == 2
    assert hub.queues["escalation.legal_line"]


def test_chase_timeout_exhausted_escalates_missing(tmp_path):
    hub, _ = make_hub(str(tmp_path))
    spoke = Spoke08DocumentCollection(hub)
    hub.on_turn_start()
    hub.send(doc_req("d-009", {"doc_type": "proof_of_funds"}))
    spoke.check_chase_timeout("d-009", "proof_of_funds")
    spoke.check_chase_timeout("d-009", "proof_of_funds")
    result = spoke.check_chase_timeout("d-009", "proof_of_funds")
    assert result == "escalated"
    assert hub.queues["escalation.legal_line"]


def test_disclosure_deadline_absence_alerts_never_softened(tmp_path):
    hub, _ = make_hub(str(tmp_path))
    spoke = Spoke08DocumentCollection(hub)
    hub.on_turn_start()
    result = spoke.check_disclosure_deadline("d-010", "disclosure")
    assert result == "escalated"
    status = persisted(hub, "doc.status")
    assert status[0]["payload"]["disclosure_deadline_approaching"] is True


def test_retention_query_holds_everything(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke08DocumentCollection(hub)
    hub.on_turn_start()
    env = Envelope(from_agent="human", to_agent="08", intent="config.update",
                  client_context_id="d-011", payload={"retention_query": True},
                  provenance={"source": "human", "captured_at": "runtime",
                              "verbatim_available": True})
    signer.sign(env)
    hub.send(env)
    clar = persisted(hub, "clarification.request")
    assert any("owner authorization" in c["payload"]["reason"] for c in clar)


# ------------------------ REAL CROSS-AGENT INTEGRATION TESTS ------------------
def test_INTEGRATION_inspection_report_flows_to_07_correctly(tmp_path):
    """Proves 08's doc.status output is actually consumed correctly by 07's
    real handler - not two isolated assumptions that happen to look alike."""
    hub, _ = make_hub(str(tmp_path))
    doc_agent = Spoke08DocumentCollection(hub)
    tc_agent = Spoke07TransactionCoordinator(hub)
    hub.on_turn_start()

    hub.send(deliverable("i-001", {"doc_type": "inspection_report",
                                   "opens_correctly": True,
                                   "content_hash": "insp-1",
                                   "repair_requests_present": True}))

    # 07 must have actually processed this and escalated per its own
    # tuple 6 (repair negotiation is human-only)
    assert hub.queues["escalation.legal_line"]
    assert any("repair negotiation" in e["trigger"]
              for e in hub.queues["escalation.legal_line"])


def test_INTEGRATION_appraisal_below_contract_escalates_through_07(tmp_path):
    hub, _ = make_hub(str(tmp_path))
    Spoke08DocumentCollection(hub)
    Spoke07TransactionCoordinator(hub)
    hub.on_turn_start()

    hub.send(deliverable("i-002", {"doc_type": "appraisal_report",
                                   "opens_correctly": True,
                                   "content_hash": "appr-1",
                                   "appraised_value": 440_000,
                                   "contract_price": 470_000}))
    assert any("all options are human" in e["trigger"]
              for e in hub.queues["escalation.legal_line"])


def test_INTEGRATION_title_exception_reaches_07_verbatim(tmp_path):
    hub, _ = make_hub(str(tmp_path))
    Spoke08DocumentCollection(hub)
    Spoke07TransactionCoordinator(hub)
    hub.on_turn_start()

    hub.send(doc_submission("i-003", {"doc_type": "title_commitment",
                                      "opens_correctly": True,
                                      "content_hash": "title-1",
                                      "exception_found": True,
                                      "exception_text": "easement dispute filed 2021"}))
    assert any("easement dispute filed 2021" in e["trigger"]
              for e in hub.queues["escalation.legal_line"])
