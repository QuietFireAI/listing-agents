import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.listing_spokes_20 import Spoke20SocialMediaMonitoring
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


def mention(ctx, payload):
    return Envelope(from_agent="external", to_agent="20", intent="social.mention",
                    client_context_id=ctx, payload=payload,
                    provenance={"source": "external", "captured_at": "runtime",
                                "verbatim_available": True})


def config_update(signer, ctx, payload):
    env = Envelope(from_agent="human", to_agent="20", intent="config.update",
                   client_context_id=ctx, payload=payload,
                   provenance={"source": "human", "captured_at": "runtime",
                               "verbatim_available": True})
    signer.sign(env)
    return env


def persisted(hub, intent=None):
    events = [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"]
    return [e for e in events if intent is None or e["intent"] == intent]


def _monitor(signer, ctx="setup"):
    return config_update(signer, ctx, {"monitored_channels": ["twitter"]})


def test_unmonitored_channel_ignored(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke20SocialMediaMonitoring(hub)
    hub.on_turn_start()
    hub.send(_monitor(signer))
    hub.send(mention("m-001", {"channel": "instagram", "text": "hello",
                               "sentiment": "question"}))
    assert not persisted(hub, "lead.signal")
    assert not persisted(hub, "interaction.log")


def test_mixed_sentiment_classified_as_complaint(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke20SocialMediaMonitoring(hub)
    hub.on_turn_start()
    hub.send(_monitor(signer))
    hub.send(mention("m-002", {"channel": "twitter",
                               "text": "great agent but slow paperwork",
                               "sentiment": "mixed"}))
    assert hub.queues["escalation.complaint"]


def test_viral_complaint_gets_priority_flag(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke20SocialMediaMonitoring(hub)
    hub.on_turn_start()
    hub.send(_monitor(signer))
    hub.send(mention("m-003", {"channel": "twitter", "text": "terrible service!",
                               "sentiment": "complaint", "is_viral": True}))
    assert any(e.get("priority") == "viral" for e in hub.queues["escalation.complaint"])


def test_praise_logged_only_no_engagement(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke20SocialMediaMonitoring(hub)
    hub.on_turn_start()
    hub.send(_monitor(signer))
    hub.send(mention("m-004", {"channel": "twitter", "text": "best agent ever!",
                               "sentiment": "praise"}))
    assert not persisted(hub, "lead.signal")
    assert not persisted(hub, "client.message.request")
    logs = persisted(hub, "interaction.log")
    assert any(l["payload"].get("sentiment") == "praise" for l in logs)


def test_prospect_question_routes_to_01(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke20SocialMediaMonitoring(hub)
    hub.on_turn_start()
    hub.send(_monitor(signer))
    hub.send(mention("m-005", {"channel": "twitter",
                               "text": "any listings under 400k?",
                               "sentiment": "question"}))
    leads = persisted(hub, "lead.signal")
    assert leads and leads[0]["to_agent"] == "01"


def test_dm_question_also_routes_to_01_with_channel_recorded(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke20SocialMediaMonitoring(hub)
    hub.on_turn_start()
    hub.send(config_update(signer, "setup2", {"monitored_channels": ["twitter_dm"]}))
    hub.send(mention("m-006b", {"channel": "twitter_dm",
                                "text": "any listings under 400k?",
                                "sentiment": "question"}))
    leads = persisted(hub, "lead.signal")
    assert leads and leads[0]["payload"]["channel"] == "twitter_dm"


def test_existing_client_question_routes_to_11_without_asserting_identity(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke20SocialMediaMonitoring(hub)
    hub.on_turn_start()
    hub.send(_monitor(signer))
    hub.send(mention("m-007", {"channel": "twitter", "text": "when is my closing?",
                               "sentiment": "question",
                               "may_involve_client_unconfirmed": True}))
    sends = persisted(hub, "client.message.request")
    assert sends and sends[0]["to_agent"] == "11"
    assert sends[0]["payload"]["template"] != "client.message.request"  # real bug fixed


def test_lead_signal_sentiment_routes_to_01(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke20SocialMediaMonitoring(hub)
    hub.on_turn_start()
    hub.send(_monitor(signer))
    hub.send(mention("m-008", {"channel": "twitter",
                               "text": "thinking about buying next year",
                               "sentiment": "lead_signal"}))
    assert persisted(hub, "lead.signal")


def test_unrecognized_sentiment_holds_never_guesses(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    Spoke20SocialMediaMonitoring(hub)
    hub.on_turn_start()
    hub.send(_monitor(signer))
    hub.send(mention("m-009", {"channel": "twitter", "text": "???",
                               "sentiment": "garbage_value"}))
    clar = persisted(hub, "clarification.request")
    assert any("no suitable tuple" in c["payload"]["reason"] for c in clar)


def test_draft_with_pricing_language_refused_never_drafted(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke20SocialMediaMonitoring(hub)
    hub.on_turn_start()
    result = spoke.draft_response("m-010", "the price is negotiable", "mention-1")
    assert result == "refused"
    assert not persisted(hub, "content.review")
    assert hub.queues["escalation.legal_line"]


def test_clean_draft_submitted_for_review_never_published_directly(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke20SocialMediaMonitoring(hub)
    hub.on_turn_start()
    result = spoke.draft_response("m-011", "thanks for reaching out!", "mention-2")
    assert result == "submitted_for_review"
    review = persisted(hub, "content.review")
    assert review and review[0]["to_agent"] == "17"
    assert review[0]["payload"]["draft"]["label"] == "DRAFT"


def test_content_verdict_never_triggers_autonomous_publish(tmp_path):
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke20SocialMediaMonitoring(hub)
    hub.on_turn_start()
    spoke.draft_response("m-012", "thanks!", "mention-3")
    review = persisted(hub, "content.review")
    env = Envelope(from_agent="17", to_agent="20", intent="content.verdict",
                  client_context_id="m-012", payload={"verdict": "approved"},
                  in_reply_to=review[0]["envelope_id"],
                  provenance={"source": "spoke-17", "captured_at": "runtime",
                              "verbatim_available": True})
    hub.send(env)
    all_intents_ever_sent = {e["intent"] for e in persisted(hub)}
    publish_like = {i for i in all_intents_ever_sent if "publish" in i or "post" in i}
    assert publish_like == set()
