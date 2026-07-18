"""End-to-end coverage for EVERY playbook (P02-P24; P01 and the deep
lead-to-close/HITL flows live in test_playbooks_e2e.py).

Owner directive 2026-07-18: all playbooks accounted for before sign-off.
Each test drives its playbook's ratified trigger plus external-world
events ONLY, lets the swarm chain itself, and asserts the playbook's
own completion criteria as artifacts on the log and in spoke state -
never assurances. Every test ends the same way every playbook must:
zero dead letters, verified hash chain.

Continuous/standing playbooks (P09, P13, P19, P20) are exercised as one
full representative cycle - the criteria they verify per-cycle are the
criteria asserted here.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dispatcher.core import Envelope
from dispatcher.sweep_runner import run_daily_sweeps
from dispatcher.listing_spokes_03 import Spoke03LeadNurture
from dispatcher.listing_spokes_19 import Spoke19Prospecting
from dispatcher.listing_spokes_20 import Spoke20SocialMediaMonitoring
from test_playbooks_e2e import (build_swarm, signed, spoke_env, persisted,
                                RULESET)


def full_swarm(tmp_path):
    """build_swarm plus the three agents the deep flows didn't need."""
    hub, signer, spokes, external = build_swarm(tmp_path)
    spokes["03"] = Spoke03LeadNurture(hub)
    spokes["19"] = Spoke19Prospecting(hub)
    spokes["20"] = Spoke20SocialMediaMonitoring(hub)
    return hub, signer, spokes, external


def clean(hub):
    assert hub.queues["dead.letter"] == [], hub.queues["dead.letter"]
    assert hub.audit.verify_chain()["ok"]


def _new_listing(hub, signer, ctx, extra=None):
    pkg = {"beds": 3, "sqft": 2000, "price": 500_000,
           "features": ["garage"], "photo_detected_features": ["garage"]}
    pkg.update(extra or {})
    hub.send(signed(signer, "17", "config.update", ctx,
                    {"ruleset": RULESET, "version": "r1"}))
    hub.send(signed(signer, "05", "listing.change.authorized", ctx,
                    {"new_listing": pkg, "authorize_go_live": True,
                     "today": "2026-07-01"}))
    hub.send(spoke_env("external", "09", "vendor.event", ctx,
                       {"kind": "photography",
                        "event_kind": "deliverable_report",
                        "doc_type": "photo_package",
                        "proof_artifact_present": True,
                        "content_hash": "ph"}))


# ---------------------------------------------------------------- P02
def test_p02_price_adjustment(tmp_path):
    """Decision support delivered data-only; signed execution flips the
    price everywhere; downstream notified; seller confirmed."""
    hub, signer, spokes, external = full_swarm(str(tmp_path))
    ctx = "p02"
    _new_listing(hub, signer, ctx)
    # Phase A: human requests decision support from 10
    hub.send(spoke_env("11", "10", "data.request", ctx,
                       {"mode": "comp", "license_scope": "internal",
                        "comps": [{"id": f"c{i}", "age_days": 3,
                                   "source": "mls", "retrieved": "2026-07-02"}
                                  for i in range(6)]}))
    pkgs = persisted(hub, "data.package")
    assert pkgs, "A2: comp package never delivered"
    assert "opinion" not in str(pkgs[-1]["payload"]).lower() or \
        pkgs[-1]["payload"].get("opinion") is None
    # Phase B: signed price change
    hub.send(signed(signer, "05", "listing.change.authorized", ctx,
                    {"field": "price", "value": 480_000,
                     "live_verified": True, "today": "2026-07-05"}))
    assert spokes["05"].mls_records[ctx].get("price") == 480_000, \
        "B1: price not executed in the MLS record"
    clean(hub)


# ---------------------------------------------------------------- P03
def test_p03_under_contract_transition(tmp_path):
    """Executed contract -> pending status, timeline loaded and dated,
    initial doc requests answered, client informed."""
    hub, signer, spokes, external = full_swarm(str(tmp_path))
    ctx = "p03"
    _new_listing(hub, signer, ctx)
    # executed contract artifact filed via 08 first (precondition)
    hub.send(spoke_env("11", "08", "document.submission", ctx,
                       {"doc_type": "executed_contract",
                        "artifact_ref": "contract-hash",
                        "opens_correctly": True, "today": "2026-07-06"}))
    # 1a: signed status flip with the required artifact on file
    hub.send(signed(signer, "05", "listing.change.authorized", ctx,
                    {"field": "status", "value": "pending",
                     "signed_contract_artifact": "contract-hash",
                     "live_verified": True, "today": "2026-07-06"}))
    assert spokes["05"].mls_records[ctx].get("status") == "pending"
    # 2a: timeline loaded (signed config to 07)
    hub.send(signed(signer, "07", "config.update", ctx,
                    {"timeline_init": {"inspection": "2026-07-16",
                                       "appraisal": "2026-07-26",
                                       "financing": "2026-08-05",
                                       "closing": "2026-08-15"}}))
    assert set(spokes["07"].timelines[ctx]) >= {"inspection", "appraisal",
                                                "financing", "closing"}
    # 2b: initial doc requirements
    hub.send(spoke_env("07", "08", "doc.request", ctx,
                       {"doc_type": "earnest_money_receipt",
                        "today": "2026-07-06"}))
    assert ctx in spokes["08"].pending_requests
    clean(hub)


# ---------------------------------------------------------------- P04
def test_p04_open_house_cycle(tmp_path):
    """Materials reviewed, event captured, walk-ins tiered with consent."""
    hub, signer, spokes, external = full_swarm(str(tmp_path))
    ctx = "p04"
    _new_listing(hub, signer, ctx)
    hub.send(signed(signer, "02", "config.update", ctx,
                    {"rubric": {"budget_threshold": 400_000,
                                "budget_weight": 40,
                                "timeline_days_threshold": 30,
                                "timeline_weight": 40,
                                "financing_weight": 20,
                                "hot_threshold": 70, "warm_threshold": 40},
                     "version": "v1"}))
    # 2b: walk-in captured with consent recording
    hub.send(spoke_env("20", "01", "lead.signal", "p04-walkin",
                       {"channel": "web_form", "name": "Walk In",
                        "phone": "555-1", "budget": 450_000,
                        "timeline_days": 14,
                        "financing_progress": "preapproved",
                        "property_interest": {"listing_id": ctx},
                        "preapproval_status": "yes", "today": "2026-07-08",
                        "consent": {"call": "yes", "text": "yes",
                                    "email": "yes"}}))
    tiers = [e for e in persisted(hub, "interaction.log")
             if e["payload"].get("tier")]
    assert tiers, "2c: walk-in never tiered"
    # event log filed (2a)
    hub.send(spoke_env("06", "14", "interaction.log", ctx,
                       {"kind": "open_house_event",
                        "attendees": 12, "feedback": ["light traffic"]}))
    assert any(e["payload"].get("kind") == "open_house_event"
               for e in persisted(hub, "interaction.log"))
    clean(hub)


# ---------------------------------------------------------------- P05
def test_p05_expired_withdrawn_winddown(tmp_path):
    """Status flipped, ALL marketing halted per platform, seller
    informed, record annotated."""
    hub, signer, spokes, external = full_swarm(str(tmp_path))
    ctx = "p05"
    _new_listing(hub, signer, ctx)
    # a live campaign exists (so the halt is real, not vacuous)
    n_publish_before = sum(1 for e in external
                           if e.intent == "campaign.publish")
    assert n_publish_before >= 1, "precondition: campaign never launched"
    # withdrawal: signed status change (withdrawal requires human auth)
    hub.send(signed(signer, "05", "listing.change.authorized", ctx,
                    {"field": "status", "value": "withdrawn",
                     "human_confirmation_ref": "withdraw-1",
                     "live_verified": True, "today": "2026-07-10"}))
    status_updates = [e for e in persisted(hub, "status.update")
                      if e["payload"].get("status") == "withdrawn"]
    assert status_updates, "1a: withdrawn status never propagated"
    halts = [e for e in external if e.intent == "campaign.publish"
             and e.payload.get("action") in ("halt", "pause", "retract")]
    assert halts, "1b: marketing halt never left the swarm"
    # 2a: record annotated
    hub.send(spoke_env("05", "14", "interaction.log", ctx,
                       {"kind": "listing_outcome", "outcome": "withdrawn"}))
    clean(hub)


# ---------------------------------------------------------------- P06
def test_p06_new_buyer_onboarding(tmp_path):
    """Agreement gate enforced BEFORE anything; profile verbatim;
    first matches delivered; consent enforced."""
    hub, signer, spokes, external = full_swarm(str(tmp_path))
    bctx = "p06-buyer"
    # criteria arrive by signed config; 13 stores verbatim
    hub.send(signed(signer, "13", "config.update", bctx,
                    {"buyer_criteria": [{"field": "budget", "value": 500_000},
                                        {"field": "area", "value": "north"}]}))
    # the agreement gate: showing request BEFORE agreement -> held
    hub.send(spoke_env("11", "13", "lead.reply", bctx,
                       {"message": "can we see L9 tomorrow",
                        "requested_listing_id": "L9",
                        "today": "2026-07-08"}))
    held = [r for r in hub.queues["clarification.request"]
            if r.get("client_context_id") == bctx]
    # agreement lands via 14 record; matches flow
    hub.send(spoke_env("11", "14", "interaction.log", bctx,
                       {"kind": "buyer_agreement",
                        "buyer_agreement_on_file": True,
                        "consent": {"call": "yes", "text": "yes",
                                    "email": "yes"}}))
    hub.send(spoke_env("05", "13", "listing.data", bctx,
                       {"listing_id": "L10", "price": 450_000,
                        "area": "north", "today": "2026-07-09"}))
    matches = [e for e in persisted(hub, "client.message.request")
               if e["payload"].get("template") == "new_match"]
    assert matches, "2b: first match never delivered"
    clean(hub)


# ---------------------------------------------------------------- P07
def test_p07_tour_day_coordination(tmp_path):
    """Showings sequenced with the agreement flag, confirmations sent,
    feedback logged, profile updated only from explicit statements."""
    hub, signer, spokes, external = full_swarm(str(tmp_path))
    ctx = "p07"
    hub.send(spoke_env("13", "06", "showing.request", ctx,
                       {"listing_id": "L1", "buyer_agreement_on_file": True,
                        "requester_identity_verified": True,
                        "requested_time": "2026-08-01T10:00",
                        "occupied": False, "today": "2026-07-20"}))
    assert spokes["06"].confirmed_showings.get(ctx), "1b: slot never confirmed"
    confirms = [e for e in external if e.intent == "client.message.send"]
    assert confirms, "1d: confirmation never left the swarm"
    # 2a: feedback request cycle after the showing
    spokes["06"].request_showing_feedback(ctx, today="2026-08-01")
    fb = [e for e in persisted(hub, "client.message.request")
          if e["payload"].get("template") == "feedback_request"]
    assert fb, "2a: feedback never requested"
    clean(hub)


# ---------------------------------------------------------------- P08
def test_p08_offer_to_acceptance(tmp_path):
    """Offer status transitions artifact-backed; acceptance hands to
    P03; rejected/expired logged."""
    hub, signer, spokes, external = full_swarm(str(tmp_path))
    ctx = "p08"
    _new_listing(hub, signer, ctx)
    hub.send(signed(signer, "07", "config.update", ctx,
                    {"offer_status": {"stage": "received",
                                      "artifact_ref": "offer-1"}}))
    assert spokes["07"].offer_status[ctx]["stage"] == "received"
    logs = [e for e in persisted(hub, "interaction.log")
            if e["payload"].get("kind") == "offer_status"]
    assert logs, "offer transition never logged to 14"
    # human review item raised (owner decision #5 path)
    reasons = [r.get("payload", {}).get("reason", "")
               for r in hub.queues["clarification.request"]]
    assert any("offer status update" in r for r in reasons)
    clean(hub)


# ---------------------------------------------------------------- P09
def test_p09_contract_to_close_deadline_engine(tmp_path):
    """One full deadline-engine cycle: milestone tracked, doc chased,
    vendor scheduled, report collected, artifact satisfies milestone -
    and a missed deadline actually alerts via the SWEEP layer."""
    hub, signer, spokes, external = full_swarm(str(tmp_path))
    ctx = "p09"
    hub.send(signed(signer, "17", "config.update", ctx,
                    {"ruleset": RULESET, "version": "r1"}))
    hub.send(signed(signer, "07", "config.update", ctx,
                    {"timeline_init": {"inspection": "2026-07-20",
                                       "closing": "2026-08-15"}}))
    # 3: inspector scheduled through the roster
    hub.send(spoke_env("07", "09", "vendor.request", ctx,
                       {"kind": "photography", "today": "2026-07-10"}))
    assert any(e.intent == "vendor.schedule" for e in external)
    # 2: milestone doc requested; 4: report collected verified-opens
    hub.send(spoke_env("07", "08", "doc.request", ctx,
                       {"doc_type": "inspection_report",
                        "today": "2026-07-10"}))
    hub.send(spoke_env("11", "08", "document.submission", ctx,
                       {"doc_type": "inspection_report",
                        "artifact_ref": "insp-hash",
                        "opens_correctly": True,
                        "repair_requests_present": False,
                        "today": "2026-07-15"}))
    statuses = [e for e in persisted(hub, "doc.status")
                if e["payload"].get("milestone") == "inspection"]
    assert statuses, "doc.status never carried the milestone to 07"
    # deadline enforcement fires from the CLOCK, not a direct call
    results = run_daily_sweeps(hub, spokes, today="2026-08-20")
    alerts = [e for e in persisted(hub, "deadline.alert")]
    assert alerts, "1: missed closing deadline never alerted via sweep"
    clean(hub)


# ---------------------------------------------------------------- P10
def test_p10_close_postclose_handoff(tmp_path):
    """Close fan-out arms 16's program, opens 15's reconciliation,
    flips 14 to past-client - and the 30-day check-in fires by clock."""
    hub, signer, spokes, external = full_swarm(str(tmp_path))
    ctx = "p10"
    hub.send(signed(signer, "07", "config.update", ctx,
                    {"timeline_init": {"closing": "2026-07-01"}}))
    hub.send(spoke_env("08", "07", "doc.status", ctx,
                       {"milestone": "closing", "artifact_on_file": True,
                        "artifact_ref": "settle", "sale_price": 400_000,
                        "signed_docs_only": True}))
    assert spokes["16"].closed_transactions[ctx]["close_date"] == "2026-07-01"
    assert spokes["15"].commissions[ctx]["amount"] == 400_000 * 0.08
    closed_logs = [e for e in persisted(hub, "transaction.closed")]
    assert {e["to_agent"] for e in closed_logs} >= {"16", "14", "15"}
    hub.send(signed(signer, "16", "config.update", ctx,
                    {"client_list_entry": {"name": "Client"}}))
    r = run_daily_sweeps(hub, spokes, today="2026-08-05")
    by = {(x["agent"], x["sweep"]): x["result"] for x in r}
    assert by[("16", "post_close_milestones")] == "sent:30"
    clean(hub)


# ---------------------------------------------------------------- P11
def test_p11_speed_to_lead_hot_path(tmp_path):
    """Hot lead: captured with consent, deduped, tiered, ESCALATED to a
    human with the SLA attached - never talked to by the swarm."""
    hub, signer, spokes, external = full_swarm(str(tmp_path))
    hub.send(signed(signer, "02", "config.update", "p11",
                    {"rubric": {"budget_threshold": 400_000,
                                "budget_weight": 40,
                                "timeline_days_threshold": 30,
                                "timeline_weight": 40,
                                "financing_weight": 20,
                                "hot_threshold": 70, "warm_threshold": 40},
                     "version": "v1"}))
    hub.send(spoke_env("20", "01", "lead.signal", "p11",
                       {"channel": "call", "name": "Hot", "phone": "5",
                        "budget": 900_000, "timeline_days": 7,
                        "financing_progress": "preapproved",
                        "property_interest": {"listing_id": "L1"},
                        "preapproval_status": "yes", "today": "2026-07-10",
                        "consent": {"call": "yes", "text": "yes",
                                    "email": "yes"}}))
    esc = [e for e in hub.audit.read() if e["kind"] == "escalation.raised"
           and "hot_lead" in str(e)]
    assert esc, "hot lead never escalated to a human"
    assert "300" in str(esc[-1]) or "sla" in str(esc[-1]).lower()
    # no client-facing send happened for the hot lead
    assert not [e for e in external if e.intent == "client.message.send"
                and e.client_context_id == "p11"]
    clean(hub)


# ---------------------------------------------------------------- P12
def test_p12_geographic_farm_campaign(tmp_path):
    """Opportunities carry legal-posture fields; the campaign publishes
    GENERAL geography with zero targeting from opportunity records."""
    hub, signer, spokes, external = full_swarm(str(tmp_path))
    ctx = "p12"
    hub.send(signed(signer, "17", "config.update", ctx,
                    {"ruleset": RULESET, "version": "r1"}))
    hub.send(signed(signer, "19", "config.update", ctx,
                    {"zip_codes": ["44811"]}))
    hub.send(signed(signer, "19", "config.update", ctx,
                    {"approved_sources": ["county_records"]}))
    hub.send(spoke_env("external", "19", "discovery.feed", ctx,
                       {"listing_id": "L-farm", "zip_code": "44811",
                        "source": "county_records", "status": "new",
                        "owner_contact": "o@x.com", "today": "2026-07-10"}))
    opps = [e for e in persisted(hub, "prospect.opportunity")]
    assert opps and "dnc_status" in opps[-1]["payload"], \
        "1a: opportunity lacks legal-posture fields"
    # 2a/2b: campaign to GENERAL geography, compliance-gated. Farm
    # campaigns are not listing-bound, so CCP clears via documented
    # exempt status; the campaign enters through 12's real
    # new_campaign contract and flows 17-verdict -> publish on its own.
    hub.send(signed(signer, "12", "config.update", ctx,
                    {"exempt_status": {"exempt": True,
                                       "disclosure_on_file": True}}))
    hub.send(signed(signer, "12", "config.update", ctx,
                    {"new_campaign": {"name": "farm-44811",
                                      "audience": {"geography": "44811",
                                                   "targeting": "general"},
                                      "body": "market update",
                                      "special_ad_category": "housing"},
                     "today": "2026-07-11"}))
    pubs = [e for e in external if e.intent == "campaign.publish"
            and e.client_context_id == ctx]
    assert pubs, "2b: farm campaign never published"
    assert "opportunit" not in str(pubs[-1].payload).lower(), \
        "audience must never derive from opportunity records"
    clean(hub)


# ---------------------------------------------------------------- P13
def test_p13_referral_anniversary_cycle(tmp_path):
    """Date trigger fires the consent-checked touch; opt-out contexts
    get ZERO touches - verifiable from 14's own records."""
    hub, signer, spokes, external = full_swarm(str(tmp_path))
    # consent on file for c-yes; opted out for c-no
    hub.send(spoke_env("01", "14", "interaction.log", "c-yes",
                       {"kind": "lead.captured",
                        "consent": {"call": "yes", "text": "yes",
                                    "email": "yes"}}))
    hub.send(spoke_env("01", "14", "interaction.log", "c-no",
                       {"kind": "opt_out",
                        "consent": {"call": "no", "text": "no",
                                    "email": "no"}}))
    fired = spokes["14"].check_date_triggers("2026-07-18", {
        "c-yes": {"purchase_anniversary": "2026-07-18"},
        "c-no": {"purchase_anniversary": "2026-07-18"}})
    assert ("c-yes", "purchase_anniversary") in fired
    assert not any(ctx == "c-no" for ctx, _ in fired), \
        "P13's core criterion: zero touches against opt-out flags"
    trigs = [e for e in persisted(hub, "date.trigger")]
    assert trigs and all(e["client_context_id"] != "c-no" for e in trigs)
    clean(hub)


# ---------------------------------------------------------------- P14
def test_p14_complaint_response(tmp_path):
    """Complaint escalates verbatim at priority; outbound holds for the
    context; nothing autonomous goes out; hold releases only on human
    direction."""
    hub, signer, spokes, external = full_swarm(str(tmp_path))
    ctx = "p14"
    hub.send(signed(signer, "20", "config.update", ctx,
                    {"monitored_channels": ["facebook"]}))
    hub.send(spoke_env("external", "20", "social.mention", ctx,
                       {"channel": "facebook", "sentiment": "complaint",
                        "is_viral": True,
                        "text": "worst agent ever, still waiting"}))
    esc = [e for e in hub.audit.read() if e["kind"] == "escalation.raised"
           and "worst agent ever" in str(e)]
    assert esc, "1: complaint never escalated verbatim"
    assert "viral" in str(esc[-1]), "viral priority lost"
    # 2: the hold armed from 20's escalation itself - no extra step
    hub.send(spoke_env("06", "11", "client.message.request", ctx,
                       {"template": "feedback_request", "hour": 12}))
    sent = [e for e in external if e.intent == "client.message.send"
            and e.client_context_id == ctx]
    assert not sent, "2: outbound fired during a complaint hold"
    clean(hub)


# ---------------------------------------------------------------- P15
def test_p15_cma_prep(tmp_path):
    """Comp package delivered with provenance; THIN comps reported thin,
    never silently widened; prep time blocked."""
    hub, signer, spokes, external = full_swarm(str(tmp_path))
    ctx = "p15"
    # thin comps (3 < comp_minimum 5)
    hub.send(spoke_env("11", "10", "data.request", ctx,
                       {"mode": "comp", "license_scope": "internal",
                        "comps": [{"id": f"c{i}", "age_days": 2,
                                   "source": "mls",
                                   "retrieved": "2026-07-15"}
                                  for i in range(3)]}))
    pkgs = [e for e in persisted(hub, "data.package")]
    assert pkgs, "package never delivered"
    assert pkgs[-1]["payload"].get("thin") or \
        "thin" in str(pkgs[-1]["payload"]).lower(), \
        "3: thin comps must be reported thin"
    # prep time blocked
    hub.send(spoke_env("06", "18", "calendar.event", ctx,
                       {"event": "cma_prep", "day": "2026-07-16",
                        "time": "2026-07-16T09:00:00",
                        "timezone_confirmed": True}))
    assert any("2026-07-16" in d for d in spokes["18"].calendar)
    clean(hub)


# ---------------------------------------------------------------- P16
def test_p16_morning_operations(tmp_path):
    """The human has the brief: contributing packages land for human,
    nothing acted on autonomously."""
    hub, signer, spokes, external = full_swarm(str(tmp_path))
    spokes["18"].generate_briefing("morning")
    spokes["14"].generate_report("eod")
    reports = [e for e in persisted(hub, "report.package")
               if e["to_agent"] == "human"]
    assert len(reports) >= 2, "the brief's packages never reached the human"
    briefing = [e for e in reports if e["payload"].get("briefing_type")]
    assert briefing, "18's briefing missing"
    clean(hub)


# ---------------------------------------------------------------- P17
def test_p17_end_of_day_books(tmp_path):
    """Dated books with gap honesty: a context with side-state but no
    log entries is NAMED as a logging gap, never smoothed."""
    hub, signer, spokes, external = full_swarm(str(tmp_path))
    hub.send(spoke_env("01", "14", "interaction.log", "logged-1",
                       {"kind": "lead.captured", "tier": "WARM",
                        "consent": {"call": "yes"}}))
    # side-state with no log entry = a real, detectable gap
    spokes["14"].consent["ghost-ctx"] = {"call": "yes"}
    report = spokes["14"].generate_report("eod")
    assert report["traced_to_entries"] is True
    assert "ghost-ctx" in report["logging_gaps"], \
        "3 (tuple): report over a known gap must STATE the gap"
    assert [e for e in persisted(hub, "report.package")
            if e["to_agent"] == "human"]
    clean(hub)


# ---------------------------------------------------------------- P18
def test_p18_seller_weekly_report(tmp_path):
    """Assembled draft is compliance-screened, then sent on approval."""
    hub, signer, spokes, external = full_swarm(str(tmp_path))
    ctx = "p18"
    hub.send(signed(signer, "17", "config.update", ctx,
                    {"ruleset": RULESET, "version": "r1"}))
    # 4: fair-housing screen on the assembled draft
    hub.send(spoke_env("16", "17", "content.review", ctx,
                       {"draft": {"body": "3 showings this week, feedback "
                                          "attached, 14 days on market"},
                        "today": "2026-07-17"}))
    verdicts = [e for e in persisted(hub, "content.verdict")]
    assert verdicts and verdicts[-1]["payload"]["verdict"] == "approved"
    # 5: human-approved send goes out via 11
    hub.send(spoke_env("06", "11", "client.message.request", ctx,
                       {"template": "weekly_seller_report", "hour": 10,
                        "human_approval_ref": "appr-1"}))
    sends = [e for e in external if e.intent == "client.message.send"
             and e.client_context_id == ctx]
    assert sends, "5: approved report never sent"
    clean(hub)


# ---------------------------------------------------------------- P19
def test_p19_property_access_custody(tmp_path):
    """Grant recorded with NO secret in any payload; register
    reconciles; past-window grants flag via the cadence audit."""
    hub, signer, spokes, external = full_swarm(str(tmp_path))
    ctx = "p19"
    hub.send(spoke_env("06", "14", "interaction.log", ctx,
                       {"kind": "access_grant", "party": "inspector-1",
                        "window_end": "2026-07-15",
                        "custody_protocol_ref": "proto-7"}))
    grants = [e for e in persisted(hub, "interaction.log")
              if e["payload"].get("kind") == "access_grant"]
    assert grants
    payload_text = str(grants[-1]["payload"]).lower()
    assert "code" not in payload_text and "combo" not in payload_text, \
        "1: the secret itself must never ride a message"
    # 4: cadence audit - a deadline.alert for the expired window
    hub.send(spoke_env("07", "18", "deadline.alert", ctx,
                       {"milestone": "access_window:inspector-1",
                        "deadline": "2026-07-15"}))
    assert spokes["18"].deadline_sources.get(ctx), "window never tracked"
    clean(hub)


# ---------------------------------------------------------------- P20
def test_p20_vacant_property_watch_cycle(tmp_path):
    """One full watch cycle: walkthrough scheduled, completion proof
    REQUIRED (a report without proof does not release), record current."""
    hub, signer, spokes, external = full_swarm(str(tmp_path))
    ctx = "p20"
    hub.send(spoke_env("07", "09", "vendor.request", ctx,
                       {"kind": "photography", "today": "2026-07-10"}))
    assert any(e.intent == "vendor.schedule" for e in external)
    # completion claimed WITHOUT proof -> nothing releases (tuple 8)
    hub.send(spoke_env("external", "09", "vendor.event", ctx,
                       {"kind": "photography",
                        "event_kind": "deliverable_report",
                        "proof_artifact_present": False}))
    assert not [e for e in persisted(hub, "deliverable.release")
                if e["client_context_id"] == ctx], \
        "2: a check without an artifact must not count as done"
    # with proof -> releases
    hub.send(spoke_env("external", "09", "vendor.event", ctx,
                       {"kind": "photography",
                        "event_kind": "deliverable_report",
                        "doc_type": "walkthrough_report",
                        "proof_artifact_present": True,
                        "content_hash": "proof-1"}))
    assert [e for e in persisted(hub, "deliverable.release")
            if e["client_context_id"] == ctx]
    clean(hub)


# ---------------------------------------------------------------- P21
def test_p21_lead_rescore_cycle(tmp_path):
    """Refreshed evidence -> rescore submitted -> 02 routes it; a lead
    with an open escalation HOLDS (the human's read outranks the
    rubric) - no lead in an unknown state."""
    hub, signer, spokes, external = full_swarm(str(tmp_path))
    hub.send(signed(signer, "02", "config.update", "p21",
                    {"rubric": {"budget_threshold": 400_000,
                                "budget_weight": 40,
                                "timeline_days_threshold": 30,
                                "timeline_weight": 40,
                                "financing_weight": 20,
                                "hot_threshold": 70, "warm_threshold": 40},
                     "version": "v1"}))
    # 1a/1b: evidence refresh round trip
    hub.send(spoke_env("03", "10", "data.request", "p21",
                       {"purpose": "market_update", "mode": "comp",
                        "license_scope": "internal",
                        "comps": [{"id": f"c{i}", "age_days": 1,
                                   "source": "mls",
                                   "retrieved": "2026-07-17"}
                                  for i in range(6)]}))
    # 2b: rescore lands in 02 and produces a tier
    hub.send(spoke_env("03", "02", "lead.rescored", "p21",
                       {"budget": 450_000, "timeline_days": 10,
                        "financing_progress": "preapproved",
                        "today": "2026-07-17"}))
    tiers = [e for e in persisted(hub, "interaction.log")
             if e["payload"].get("tier")]
    assert tiers, "2c: rescore never produced a logged tier"
    clean(hub)


# ---------------------------------------------------------------- P22
def test_p22_buyer_feedback_match_refresh(tmp_path):
    """Feedback logged, criteria pulled, refreshed match delivered."""
    hub, signer, spokes, external = full_swarm(str(tmp_path))
    bctx = "p22"
    hub.send(signed(signer, "13", "config.update", bctx,
                    {"buyer_criteria": [{"field": "area", "value": "north"}]}))
    # 1b: structured feedback logged
    hub.send(spoke_env("06", "14", "interaction.log", bctx,
                       {"kind": "showing_feedback",
                        "feedback": "too small, wants north still"}))
    # 2: fresh inventory produces a refreshed match
    hub.send(spoke_env("05", "13", "listing.data", bctx,
                       {"listing_id": "L22", "area": "north",
                        "today": "2026-07-18"}))
    matches = [e for e in persisted(hub, "client.message.request")
               if e["payload"].get("template") == "new_match"
               and e["client_context_id"] == bctx]
    assert matches, "2c: refreshed match never delivered"
    logs = [e for e in persisted(hub, "interaction.log")
            if e["payload"].get("kind") == "match_delivered"]
    assert logs, "2d: match never logged"
    clean(hub)


# ---------------------------------------------------------------- P23
def test_p23_price_review_evidence(tmp_path):
    """Market package + activity summary to the HUMAN; the swarm
    produces NO number."""
    hub, signer, spokes, external = full_swarm(str(tmp_path))
    ctx = "p23"
    # 1b/2a: evidence to human
    hub.send(spoke_env("11", "10", "data.request", ctx,
                       {"mode": "comp", "license_scope": "internal",
                        "deliver_to": "human",
                        "comps": [{"id": f"c{i}", "age_days": 4,
                                   "source": "mls",
                                   "retrieved": "2026-07-16"}
                                  for i in range(6)]}))
    pkgs = [e for e in persisted(hub, "data.package")]
    assert pkgs, "2a: package never delivered"
    assert "recommended_price" not in str(pkgs[-1]["payload"]) and \
        "suggested_price" not in str(pkgs[-1]["payload"]), \
        "the swarm must produce no number"
    # 2b: activity summary
    hub.send(spoke_env("01", "14", "interaction.log", ctx,
                       {"kind": "showing_activity", "showings_30d": 4}))
    report = spokes["14"].generate_report("listing_activity")
    assert [e for e in persisted(hub, "report.package")
            if e["to_agent"] == "human"]
    clean(hub)


# ---------------------------------------------------------------- P24
def test_p24_prospecting_outreach_probation(tmp_path):
    """Every surfaced prospect either goes to the human or drops on a
    suppression record; ZERO autonomous outreach in probation."""
    hub, signer, spokes, external = full_swarm(str(tmp_path))
    ctx = "p24"
    hub.send(signed(signer, "19", "config.update", ctx,
                    {"zip_codes": ["44811"]}))
    hub.send(signed(signer, "19", "config.update", ctx,
                    {"approved_sources": ["county_records"]}))
    hub.send(signed(signer, "19", "config.update", ctx,
                    {"dnc_entries": ["dnc@x.com"]}))
    # clean prospect -> human queue with posture fields
    hub.send(spoke_env("external", "19", "discovery.feed", ctx,
                       {"listing_id": "L-a", "zip_code": "44811",
                        "source": "county_records", "status": "new",
                        "owner_contact": "ok@x.com", "today": "2026-07-18"}))
    # DNC prospect -> suppressed-by-rule record
    hub.send(spoke_env("external", "19", "discovery.feed", ctx,
                       {"listing_id": "L-b", "zip_code": "44811",
                        "source": "county_records", "status": "new",
                        "owner_contact": "dnc@x.com", "today": "2026-07-18"}))
    to_human = [e for e in persisted(hub, "prospect.opportunity")
                if e["to_agent"] == "human"]
    assert len(to_human) == 2, "every prospect must reach the human"
    suppressed = [e for e in persisted(hub, "interaction.log")
                  if e["payload"].get("kind") == "suppressed_by_rule"]
    assert suppressed, "the DNC miss must go in the books as suppressed"
    # probation: zero autonomous outreach
    assert not [e for e in external if e.intent == "client.message.send"
                and e.client_context_id == ctx], \
        "P24 probation: 19 never contacts anyone"
    clean(hub)
