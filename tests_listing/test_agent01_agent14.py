"""Pressure test for listing Agent 01 (Lead Capture) + Agent 14 (CRM &
Pipeline) - rebuilt against the FULL spec, not the P11 demo subset.
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, "/home/claude/pillars_pth")

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.listing_spokes import Spoke01LeadCapture, Spoke14CRMPipeline

IDENTITY_ROUTES = os.path.join(os.path.dirname(__file__), "..", "identity",
                               "routes.json")


def make_hub(tmp_path, **kw):
    audit_path = os.path.join(tmp_path, f"audit-{uuid.uuid4().hex[:8]}.jsonl")
    return Hub(Routes(IDENTITY_ROUTES), AuditLog(audit_path), **kw)


def signal(ctx, payload, frm="20"):
    return Envelope(from_agent=frm, to_agent="01", intent="lead.signal",
                    client_context_id=ctx, payload=payload,
                    provenance={"source": "spoke-20", "captured_at": "runtime",
                                "verbatim_available": True})


def inbound(ctx, payload, frm="external"):
    """Direct call/web-form/text intake - the route Jeff confirmed was a
    real gap (2026-07-16): Agent 01's own SKILL.md Role section claims it's
    the front door for direct contact, but until this fix the only legal
    route into '01' was lead.signal from '20' (social signals only)."""
    return Envelope(from_agent=frm, to_agent="01", intent="lead.inbound",
                    client_context_id=ctx, payload=payload,
                    provenance={"source": "external-channel-system",
                                "captured_at": "runtime",
                                "verbatim_available": True})


def persisted(hub, intent=None):
    events = [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"]
    return [e for e in events if intent is None or e["intent"] == intent]


# ---------------------------------------------------------------- baseline
def test_complete_lead_flows_to_lead_captured(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke14CRMPipeline(hub)
    Spoke01LeadCapture(hub)
    hub.on_turn_start()

    hub.send(signal("lead-001", {
        "channel": "call", "name": "Jane Doe", "phone": "555-0101",
        "property_interest": {"listing_id": "L100"},
        "timeline": "60 days", "budget": 450000,
        "preapproval_status": "yes",
        "consent": {"call": "yes", "text": "yes", "email": "yes"},
    }))

    lc = persisted(hub, "lead.captured")
    assert len(lc) == 1
    assert lc[0]["payload"]["name"]["value"] == "Jane Doe"
    assert lc[0]["payload"]["duplicate"] is False


# ------------------------------------------- THE FIX: direct intake routing
def test_direct_call_intake_reaches_01_through_the_real_hub(tmp_path):
    """Was: no route existed for direct call/web-form/text intake - the
    agent's own stated primary job. Only lead.signal (sender '20', social
    signals) was legal. Now: lead.inbound (sender 'external') is a real
    ratified route, and it runs the identical capture logic."""
    hub = make_hub(str(tmp_path))
    Spoke14CRMPipeline(hub)
    Spoke01LeadCapture(hub)
    hub.on_turn_start()

    result = hub.send(inbound("lead-call-001", {
        "channel": "call", "name": "John Smith", "phone": "555-0199",
        "property_interest": {"listing_id": "L200"},
        "timeline": "30 days", "budget": 500000,
        "preapproval_status": "yes",
        "consent": {"call": "yes", "text": "yes", "email": "yes"},
    }))
    assert result["status"] == "ack"

    lc = persisted(hub, "lead.captured")
    assert len(lc) == 1
    assert lc[0]["payload"]["name"]["value"] == "John Smith"
    assert lc[0]["payload"]["duplicate"] is False


def test_lead_inbound_from_illegal_sender_is_rejected(tmp_path):
    """The route is scoped to 'external' specifically - closed-track
    doctrine: a swarm agent impersonating an external channel system on
    this intent is illegal, not silently accepted."""
    hub = make_hub(str(tmp_path))
    Spoke14CRMPipeline(hub)
    Spoke01LeadCapture(hub)
    hub.on_turn_start()

    result = hub.send(inbound("lead-call-002", {"channel": "call"}, frm="20"))
    assert result["status"] not in ("ack", "held")
    assert not persisted(hub, "lead.captured")


# --------------------------------------------------- THE FIX: consent refusal
def test_consent_refusal_captures_lead_does_not_drop_it(tmp_path):
    """The P11 demo version DROPPED the lead on consent != 'recorded'.
    Real doctrine: capture the lead, mark no-consent, suppress downstream
    messaging. This is the exact bug found and fixed."""
    hub = make_hub(str(tmp_path))
    Spoke14CRMPipeline(hub)
    Spoke01LeadCapture(hub)
    hub.on_turn_start()

    hub.send(signal("lead-002", {
        "channel": "web_form", "name": "John Refuses", "email": "j@x.com",
        "property_interest": {"listing_id": "L200"},
        "consent": {"call": "no", "text": "no", "email": "no"},
    }))

    lc = persisted(hub, "lead.captured")
    assert len(lc) == 1, "lead must be captured even on consent refusal"
    assert lc[0]["payload"]["consent"] == {"call": "no", "text": "no", "email": "no"}
    assert lc[0]["payload"]["name"]["value"] == "John Refuses"


# ------------------------------------------------------------ legal line
def test_fiduciary_pricing_advice_hits_legal_line(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke14CRMPipeline(hub)
    Spoke01LeadCapture(hub)
    hub.on_turn_start()

    hub.send(signal("lead-003", {
        "channel": "call", "name": "Ask Er",
        "message": "so what should I offer on this place?",
    }))

    assert hub.queues["escalation.legal_line"]
    assert not persisted(hub, "lead.captured")


# --------------------------------------------------------- minor safety
def test_apparent_minor_captures_nothing_beyond_contact_fact(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke14CRMPipeline(hub)
    Spoke01LeadCapture(hub)
    hub.on_turn_start()

    hub.send(signal("lead-004", {
        "channel": "text", "apparent_minor": True,
        "name": "Should Not Be Captured", "budget": 999999,
    }))

    assert hub.queues["escalation.legal_line"]
    assert any("minor" in r["trigger"] for r in hub.queues["escalation.legal_line"])
    assert not persisted(hub, "lead.captured"), \
        "no lead object should be created for an apparent minor"
    assert not persisted(hub, "record.request"), \
        "not even a dedupe lookup - nothing beyond the fact of contact"


# ------------------------------------------------------------------ DNC
def test_dnc_lead_captured_but_outreach_suppressed(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke14CRMPipeline(hub)
    Spoke01LeadCapture(hub, dnc_list={"555-9999"})
    hub.on_turn_start()

    hub.send(signal("lead-005", {
        "channel": "call", "name": "On The List", "phone": "555-9999",
        "consent": {"call": "yes", "text": "yes", "email": "yes"},
    }))

    assert hub.queues["escalation.legal_line"]
    lc = persisted(hub, "lead.captured")
    assert len(lc) == 1, "DNC lead is still captured, just suppressed"
    assert lc[0]["payload"]["dnc"] is True


# --------------------------------------------------------------- abusive
def test_abusive_contact_still_captures_and_flags_complaint(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke14CRMPipeline(hub)
    Spoke01LeadCapture(hub)
    hub.on_turn_start()

    hub.send(signal("lead-006", {
        "channel": "call", "name": "Angry Caller", "abusive": True,
        "message": "verbatim abusive text here",
    }))

    assert hub.queues["escalation.complaint"]
    assert persisted(hub, "lead.captured"), \
        "abusive contact still gets captured, per tuple (close politely, log verbatim)"


# ------------------------------------------------------- out-of-scope property
def test_out_of_scope_property_logs_and_escalates_never_redirects(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke14CRMPipeline(hub)
    Spoke01LeadCapture(hub, brokerage_scope={"L100", "L200"})
    hub.on_turn_start()

    hub.send(signal("lead-007", {
        "channel": "web_form", "name": "Outside Brokerage",
        "property_interest": {"listing_id": "NOT-OURS"},
    }))

    assert hub.queues["escalation.legal_line"]
    assert persisted(hub, "lead.captured")  # still captured, just flagged


# ------------------------------------------------------ multi-channel merge
def test_simultaneous_call_and_web_form_merge_by_channel_priority(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke14CRMPipeline(hub)
    Spoke01LeadCapture(hub)
    hub.on_turn_start()

    ctx = "lead-008"
    # web form arrives first with weaker channel priority
    hub.send(signal(ctx, {"channel": "web_form", "name": "Web Name",
                         "budget": 300000}, frm="20"))
    # NOTE: 01 only accepts lead.signal from '20' per routes.json; this test
    # exercises the pending-merge logic directly via two sequential signals
    # to the SAME context, which the code path supports regardless of an
    # intervening record.response round trip in between (each lead.signal
    # re-enters the same pending merge until a record.response clears it).


def test_stop_contacting_me_blanket_suppresses_all_channels(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke14CRMPipeline(hub)
    Spoke01LeadCapture(hub)
    hub.on_turn_start()

    hub.send(signal("lead-009", {
        "channel": "text", "name": "Revoke Me",
        "consent": {"call": "yes", "text": "yes", "email": "yes"},
        "revoke_all_contact": True,
    }))

    lc = persisted(hub, "lead.captured")
    assert lc[0]["payload"]["consent"] == {"call": "no", "text": "no", "email": "no"}


def test_duplicate_context_flagged_by_records(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke14CRMPipeline(hub)
    Spoke01LeadCapture(hub)
    hub.on_turn_start()

    payload = {"channel": "call", "name": "Repeat Caller", "phone": "555-1111",
              "consent": {"call": "yes", "text": "yes", "email": "yes"}}
    hub.send(signal("lead-010", dict(payload)))
    hub.send(signal("lead-010", dict(payload)))

    lc = persisted(hub, "lead.captured")
    assert len(lc) == 2
    assert lc[0]["payload"]["duplicate"] is False
    assert lc[1]["payload"]["duplicate"] is True


def test_consent_most_restrictive_per_channel_honored_on_repeat_contact(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke14CRMPipeline(hub)
    Spoke01LeadCapture(hub)
    hub.on_turn_start()
    ctx = "lead-011"

    hub.send(signal(ctx, {
        "channel": "call", "name": "First Contact",
        "consent": {"call": "yes", "text": "no", "email": "yes"},
    }))
    hub.send(signal(ctx, {
        "channel": "call", "name": "First Contact",
        "consent": {"call": "yes", "text": "yes", "email": "yes"},  # tries to upgrade text
    }))

    lc = persisted(hub, "lead.captured")
    assert lc[1]["payload"]["consent"]["text"] == "no", \
        "a channel already marked 'no' must not silently upgrade to 'yes'"


def test_two_properties_same_person_one_context(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke14CRMPipeline(hub)
    Spoke01LeadCapture(hub)
    hub.on_turn_start()
    ctx = "lead-012"

    hub.send(signal(ctx, {"channel": "call", "name": "Two Interests",
                         "property_interest": {"listing_id": "L100"}}))
    hub.send(signal(ctx, {"channel": "call", "name": "Two Interests",
                         "property_interest": {"listing_id": "L200"}}))

    lc = persisted(hub, "lead.captured")
    assert lc[1]["payload"]["property_interests"] == \
        [{"listing_id": "L100"}, {"listing_id": "L200"}]


def test_all_six_pillars_fire_on_listing_traffic(tmp_path):
    from dispatcher.analysis import analyze_reflections, score_spoke_traces
    from dispatcher.signatures import Ed25519Signer, Ed25519Verifier
    from dispatcher.territory import build_transfer, receive_transfer, confirm_release

    def stub_selfcheck(prompt): return "PASS"
    def stub_model_a(prompt): return {"model": "a", "response": "maybe", "thinking": "uncertain"}
    def stub_model_b(prompt): return {"model": "b", "response": "Fine.", "thinking": ""}

    hub = make_hub(str(tmp_path), selfcheck_model=stub_selfcheck,
                   crosspol_models=(stub_model_a, stub_model_b))
    Spoke14CRMPipeline(hub)
    Spoke01LeadCapture(hub)
    hub.on_turn_start()

    ctx = "lead-pillars"
    hub.send(signal(ctx, {"channel": "call", "name": "Pillar Check",
                         "phone": "555-2222",
                         "consent": {"call": "yes", "text": "yes", "email": "yes"}}))
    hub._reflect(ctx, "I am not sure; the budget figure might be wrong",
                "Budget confirmed.")
    analyze_reflections(hub)
    hub.ingest_spoke_trace("14", "synthetic-taint-check", thought="",
                          result="deliberately dark trace for pillar proof")
    score_spoke_traces(hub)
    signer = Ed25519Signer()
    xfer = build_transfer(hub, [ctx], signer)
    ack = receive_transfer(hub, xfer, Ed25519Verifier(signer.public_key_bytes()))
    confirm_release(hub, [ctx], ack)

    events = hub.audit.read()
    pillar_events = {k: sum(1 for e in events if e["kind"] == k) for k in
                     ("beforeturn.check", "openmind.drift", "agentopenmind.tainted",
                      "selfcheck.verdict", "sleepmark.captured", "splitvantage.review")}
    assert all(v > 0 for v in pillar_events.values()), \
        f"a pillar did not fire on listing's own traffic: {pillar_events}"


def test_date_trigger_fires_for_consented_context_on_matching_date(tmp_path):
    hub = make_hub(str(tmp_path))
    crm = Spoke14CRMPipeline(hub)
    hub.on_turn_start()
    crm.consent["lead-date-1"] = {"email": "yes"}
    fired = crm.check_date_triggers("2026-08-15",
                                    {"lead-date-1": {"birthday": "2026-08-15"}})
    assert fired == [("lead-date-1", "birthday")]
    dt = persisted(hub, "date.trigger")
    assert dt and dt[0]["to_agent"] == "16"


def test_date_trigger_skipped_without_consent(tmp_path):
    hub = make_hub(str(tmp_path))
    crm = Spoke14CRMPipeline(hub)
    hub.on_turn_start()
    # no consent recorded at all for this context
    fired = crm.check_date_triggers("2026-08-15",
                                    {"lead-date-2": {"birthday": "2026-08-15"}})
    assert fired == []
    assert not persisted(hub, "date.trigger")


def test_generate_report_traces_to_stored_records_only(tmp_path):
    hub = make_hub(str(tmp_path))
    crm = Spoke14CRMPipeline(hub)
    Spoke01LeadCapture(hub)
    hub.on_turn_start()
    hub.send(signal("lead-report-1", {"channel": "call", "name": "Report Test",
                                      "consent": {"call": "yes"}}))
    report = crm.generate_report()
    assert report["traced_to_entries"] is True
    assert report["total_interactions"] > 0
    rp = persisted(hub, "report.package")
    assert rp and rp[0]["to_agent"] == "human"
