import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, "/home/claude/pillars_pth")

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.listing_spokes_11 import Spoke11ClientCommunication

IDENTITY_ROUTES = os.path.join(os.path.dirname(__file__), "..", "identity",
                               "routes.json")


def make_hub(tmp_path, **kw):
    audit_path = os.path.join(tmp_path, f"audit-{uuid.uuid4().hex[:8]}.jsonl")
    return Hub(Routes(IDENTITY_ROUTES), AuditLog(audit_path), **kw)


def status_update(ctx, payload, frm="05"):
    return Envelope(from_agent=frm, to_agent="11", intent="status.update",
                    client_context_id=ctx, payload=payload,
                    provenance={"source": f"spoke-{frm}", "captured_at": "runtime",
                                "verbatim_available": True})


def client_reply(ctx, payload):
    return Envelope(from_agent="external", to_agent="11", intent="client.reply",
                    client_context_id=ctx, payload=payload,
                    provenance={"source": "external", "captured_at": "runtime",
                                "verbatim_available": True})


def persisted(hub, intent=None):
    events = [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"]
    return [e for e in events if intent is None or e["intent"] == intent]


def test_routine_update_sends_during_daytime(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke11ClientCommunication(hub)
    hub.on_turn_start()
    hub.send(status_update("c-001", {"template": "inspection_scheduled",
                                     "variables": {"date": "Aug 10"}, "hour": 14}))
    sends = persisted(hub, "client.message.send")
    assert sends and sends[0]["payload"]["template"] == "inspection_scheduled"


def test_quiet_hours_queues_instead_of_sending(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke11ClientCommunication(hub)
    hub.on_turn_start()
    hub.send(status_update("c-002", {"template": "milestone_update",
                                     "variables": {}, "hour": 23}))
    assert not persisted(hub, "client.message.send")
    clar = persisted(hub, "clarification.request")
    assert any("quiet hours" in c["payload"]["reason"] for c in clar)


def test_exempt_alert_class_sends_during_quiet_hours(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke11ClientCommunication(hub, exempt_alert_classes={"urgent_deadline"})
    hub.on_turn_start()
    hub.send(status_update("c-003", {"template": "urgent", "variables": {},
                                     "hour": 23, "alert_class": "urgent_deadline"}))
    assert persisted(hub, "client.message.send")


def test_unresolved_template_variable_never_sends(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke11ClientCommunication(hub)
    hub.on_turn_start()
    hub.send(status_update("c-004", {"template": "appraisal_update",
                                     "variables": {"value": None}, "hour": 12}))
    assert not persisted(hub, "client.message.send")
    clar = persisted(hub, "clarification.request")
    assert any("unresolved" in c["payload"]["reason"] for c in clar)


def test_conflicting_statuses_send_nothing(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke11ClientCommunication(hub)
    hub.on_turn_start()
    hub.send(status_update("c-005", {"template": "status", "milestone": "closing",
                                     "conflict_key": "closing-date", "value": "Sept 1",
                                     "variables": {}, "hour": 12}))
    hub.send(status_update("c-005", {"template": "status", "milestone": "closing",
                                     "conflict_key": "closing-date", "value": "Sept 3",
                                     "variables": {}, "hour": 12}))
    sends = persisted(hub, "client.message.send")
    assert len(sends) == 1  # only the first, non-conflicting one
    clar = persisted(hub, "clarification.request")
    assert any("conflicting statuses" in c["payload"]["reason"] for c in clar)


def test_what_would_you_do_escalates_verbatim(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke11ClientCommunication(hub)
    hub.on_turn_start()
    hub.send(client_reply("c-006", {"message": "what would you do in my situation?"}))
    assert any("what would you do" in e["trigger"].lower()
              for e in hub.queues["escalation.legal_line"])


def test_pricing_question_escalates_with_template_ack(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke11ClientCommunication(hub)
    hub.on_turn_start()
    hub.send(client_reply("c-007", {"message": "what's it worth to you, negotiate"}))
    assert hub.queues["escalation.legal_line"]
    sends = persisted(hub, "client.message.send")
    assert any(s["payload"]["template"] == "advice_ack_handoff" for s in sends)


def test_mixed_routine_and_advice_splits_and_routes_both(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke11ClientCommunication(hub)
    hub.on_turn_start()
    hub.send(client_reply("c-008", {"message": "should i offer more, also when is my showing",
                                    "has_routine_component": True,
                                    "owning_agent": "13"}))
    assert hub.queues["escalation.legal_line"]
    reply = persisted(hub, "lead.reply")
    assert reply and reply[0]["payload"]["split"] == "routine_only"


def test_angry_client_acknowledges_no_promises(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke11ClientCommunication(hub)
    hub.on_turn_start()
    hub.send(client_reply("c-009", {"message": "this is unacceptable", "angry": True}))
    assert hub.queues["escalation.complaint"]
    sends = persisted(hub, "client.message.send")
    assert any(s["payload"]["template"] == "acknowledge_no_promises" for s in sends)


def test_wire_topic_in_client_message_escalates(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke11ClientCommunication(hub)
    hub.on_turn_start()
    hub.send(client_reply("c-010", {"message": "can you confirm the wire instructions"}))
    assert hub.queues["escalation.legal_line"]
    assert not persisted(hub, "lead.reply")


def test_channel_change_honored_recorded_confirmed_once(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke11ClientCommunication(hub)
    hub.on_turn_start()
    hub.send(client_reply("c-011", {"message": "text me instead please",
                                    "requests_channel_change": True,
                                    "new_channel": "text"}))
    logs = persisted(hub, "interaction.log")
    assert any(l["payload"].get("kind") == "channel_change" for l in logs)
    sends = persisted(hub, "client.message.send")
    assert any(s["payload"]["template"] == "channel_change_confirmed" for s in sends)


def test_off_log_reference_asks_for_particulars_never_pretends_recall(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke11ClientCommunication(hub)
    hub.on_turn_start()
    hub.send(client_reply("c-012", {"message": "like we discussed on the phone",
                                    "references_unlogged_conversation": True}))
    sends = persisted(hub, "client.message.send")
    assert any(s["payload"]["template"] == "ask_for_particulars" for s in sends)


def test_showing_no_show_reply_routes_to_06(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke11ClientCommunication(hub)
    hub.on_turn_start()
    hub.send(client_reply("c-013", {"message": "sorry I missed it",
                                    "about_showing": True, "no_show": True}))
    ns = persisted(hub, "showing.no_show")
    assert ns and ns[0]["to_agent"] == "06"


def test_client_requested_showing_routes_to_06(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke11ClientCommunication(hub)
    hub.on_turn_start()
    hub.send(client_reply("c-014", {"message": "can I see it Saturday",
                                    "requests_showing": True,
                                    "requested_time": "2026-08-15T10:00"}))
    req = persisted(hub, "showing.request")
    assert req and req[0]["to_agent"] == "06"


def test_document_attached_routes_to_08(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke11ClientCommunication(hub)
    hub.on_turn_start()
    hub.send(client_reply("c-015", {"message": "attached is my pre-approval",
                                    "document_attached": True,
                                    "doc_type": "preapproval_letter",
                                    "opens_correctly": True, "content_hash": "pa-1"}))
    sub = persisted(hub, "document.submission")
    assert sub and sub[0]["to_agent"] == "08"


def test_routine_reply_routes_to_owning_agent(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke11ClientCommunication(hub)
    hub.on_turn_start()
    hub.send(client_reply("c-016", {"message": "sounds good, thanks",
                                    "owning_agent": "03"}))
    reply = persisted(hub, "lead.reply")
    assert reply and reply[0]["to_agent"] == "03"


def test_market_update_package_delivered_via_data_package(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke11ClientCommunication(hub)
    hub.on_turn_start()
    env = Envelope(from_agent="10", to_agent="11", intent="data.package",
                  client_context_id="c-017",
                  payload={"figures": {"school_rating": {"value": 8}}, "hour": 12},
                  provenance={"source": "spoke-10", "captured_at": "runtime",
                              "verbatim_available": True})
    hub.send(env)
    sends = persisted(hub, "client.message.send")
    assert any(s["payload"]["template"] == "weekly_market_update" for s in sends)


def test_request_market_update_sends_data_request(tmp_path):
    hub = make_hub(str(tmp_path))
    spoke = Spoke11ClientCommunication(hub)
    hub.on_turn_start()
    spoke.request_market_update("c-018")
    req = persisted(hub, "data.request")
    assert req and req[0]["to_agent"] == "10"
