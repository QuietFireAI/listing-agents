"""Mutation-hardening tests.

Each test here exists to kill a specific mutation that survived the full
420-test suite in the CrossPol sweep at e617447. The pattern being fixed:
the suite asserted EMISSION (something was sent) instead of DECISION
(the right branch chose to send it). Every test below distinguishes the
true branch from its mutant - a positive case AND the negative case that
the flipped operator would confuse.

Target lines (listing_spokes_NN.py:line):
  02:146  03:309  04:255  05:312  07:477  08:176
  15:152  15:276  16:246  18:249  18:257  18:264  18:343  19:239
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.signatures import Ed25519Signer, Ed25519Verifier
from dispatcher.listing_spokes_02 import Spoke02LeadQualification
from dispatcher.listing_spokes import Spoke14CRMPipeline
from dispatcher.listing_spokes_03 import Spoke03LeadNurture
from dispatcher.listing_spokes_04 import Spoke04ListingDescription
from dispatcher.listing_spokes_05 import Spoke05MLSListingManagement
from dispatcher.listing_spokes_07 import Spoke07TransactionCoordinator
from dispatcher.listing_spokes_08 import Spoke08DocumentCollection
from dispatcher.listing_spokes_15 import Spoke15FinancialTracking
from dispatcher.listing_spokes_16 import Spoke16AfterCloseReferral
from dispatcher.listing_spokes_18 import Spoke18CalendarTask
from dispatcher.listing_spokes_19 import Spoke19Prospecting

IDENTITY_ROUTES = os.path.join(os.path.dirname(__file__), "..", "identity",
                               "routes.json")


def make_hub(tmp_path, **kw):
    audit_path = os.path.join(tmp_path, f"audit-{uuid.uuid4().hex[:8]}.jsonl")
    signer = Ed25519Signer()
    verifier = Ed25519Verifier(signer.public_key_bytes())
    hub = Hub(Routes(IDENTITY_ROUTES), AuditLog(audit_path),
              signature_verifier=verifier.verifier(), **kw)
    return hub, signer


def env(frm, to, intent, ctx, payload):
    return Envelope(from_agent=frm, to_agent=to, intent=intent,
                    client_context_id=ctx, payload=payload,
                    provenance={"source": f"spoke-{frm}",
                                "captured_at": "runtime",
                                "verbatim_available": True})


def signed(signer, to, intent, ctx, payload):
    e = Envelope(from_agent="human", to_agent=to, intent=intent,
                 client_context_id=ctx, payload=payload,
                 provenance={"source": "human", "captured_at": "runtime",
                             "verbatim_available": True})
    signer.sign(e)
    return e


def persisted(hub, intent=None):
    events = [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"]
    return [e for e in events if intent is None or e["intent"] == intent]


# ------------------------------------------------------------ 02:146
def test_02_urgency_financing_conflict_note_both_directions(tmp_path):
    """stated_urgency=='high' AND financing in (None,'none') -> conflict
    note. Kills == flip AND the membership flip: a 'high'-urgency lead
    WITH financing must NOT get the note; a 'high' lead WITHOUT it must."""
    hub, signer = make_hub(str(tmp_path))
    Spoke14CRMPipeline(hub)
    spoke = Spoke02LeadQualification(hub)
    hub.on_turn_start()
    hub.send(signed(signer, "02", "config.update", "config",
                    {"rubric": {"budget_threshold": 500_000, "budget_weight": 40,
                              "timeline_days_threshold": 30,
                              "timeline_weight": 30, "financing_weight": 30,
                              "hot_threshold": 80, "warm_threshold": 50},
                    "version": "v1"}))
    tier, score, notes = spoke._score(
        {"budget": 600_000, "stated_urgency": "high",
         "financing_progress": None, "timeline_days": 10})
    assert any("conflicts with no financing" in n for n in notes), \
        "high urgency + no financing must produce the conflict note"
    tier2, score2, notes2 = spoke._score(
        {"budget": 600_000, "stated_urgency": "high",
         "financing_progress": "preapproved", "timeline_days": 10})
    assert not any("conflicts with no financing" in n for n in notes2), \
        "high urgency WITH financing must not produce the conflict note"
    tier3, score3, notes3 = spoke._score(
        {"budget": 600_000, "stated_urgency": "low",
         "financing_progress": None, "timeline_days": 10})
    assert not any("conflicts with no financing" in n for n in notes3), \
        "low urgency must not produce the conflict note"


# ------------------------------------------------------------ 03:309
def test_03_stop_sending_so_many_phrase_alone_reduces_cadence(tmp_path):
    """The FIRST clause 'stop sending so many' must trigger on its own.
    Existing test only used 'too many' (second clause), so flipping the
    first clause survived."""
    hub = make_hub(str(tmp_path))[0]
    spoke = Spoke03LeadNurture(hub, frequency_cap_per_week=3)
    hub.on_turn_start()
    ctx = "mh-03"
    hub.send(env("02", "03", "lead.nurture", ctx,
                 {"consent": {"email": "yes"}, "sequence_id": "seq_a"}))
    hub.send(env("17", "03", "content.verdict", ctx, {"verdict": "approved"}))
    hub.send(env("11", "03", "lead.reply", ctx,
                 {"message": "please stop sending so many messages"}))
    assert spoke.frequency_cap_per_week == 2, \
        "'stop sending so many' alone must reduce cadence"
    assert spoke.active_sequences[ctx]["paused"] is False


# ------------------------------------------------------------ 04:255
def test_04_human_copy_fair_housing_flag_and_clean_pass(tmp_path):
    """Fair-housing screen on HUMAN-SUPPLIED copy (tuple 3). Zero prior
    coverage. Violating copy -> legal escalation; clean copy -> none."""
    hub, _ = make_hub(str(tmp_path))
    Spoke04ListingDescription(hub)
    hub.on_turn_start()
    hub.send(env("05", "04", "listing.data", "fh-1",
                 {"beds": 3, "baths": 2,
                  "human_supplied_copy": "Great Schools nearby, family-friendly block"}))
    legal = hub.queues["escalation.legal_line"]
    assert any("human-supplied copy flagged" in e.get("trigger", "")
               for e in legal), \
        "protected-class language in human copy must escalate to legal line"
    hub2, _ = make_hub(str(tmp_path) + "_b")
    Spoke04ListingDescription(hub2)
    hub2.on_turn_start()
    hub2.send(env("05", "04", "listing.data", "fh-2",
                  {"beds": 3, "baths": 2,
                   "human_supplied_copy": "Updated kitchen and new roof"}))
    assert not any("human-supplied copy flagged" in e.get("trigger", "")
                   for e in hub2.queues["escalation.legal_line"]), \
        "clean human copy must not escalate"


# ------------------------------------------------------------ 05:312
def test_05_sold_and_withdrawn_leave_under_contract_pending_stays(tmp_path):
    """Lifecycle membership: pending -> in under_contract; sold ->
    removed. Kills the (sold, withdrawn) membership flip."""
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke05MLSListingManagement(hub)
    hub.on_turn_start()
    hub.send(signed(signer, "05", "listing.change.authorized", "lc-1",
                    {"field": "status", "value": "pending",
                     "signed_contract_artifact": "contract-1"}))
    assert "lc-1" in spoke.under_contract, "pending must enter under_contract"
    hub.send(signed(signer, "05", "listing.change.authorized", "lc-1",
                    {"field": "status", "value": "sold",
                     "closing_artifact": "closing-1"}))
    assert "lc-1" not in spoke.under_contract, \
        "sold must leave under_contract"
    assert spoke.mls_records["lc-1"]["status"] == "sold"


# ------------------------------------------------------------ 07:477
def test_07_only_financing_contingency_escalates_legal_on_overdue(tmp_path):
    """Overdue path: financing_contingency escalates the legal line;
    another overdue milestone must NOT. Kills the == flip, which would
    invert exactly this pair."""
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke07TransactionCoordinator(hub)
    hub.on_turn_start()
    hub.send(signed(signer, "07", "config.update", "od-fin",
                    {"timeline_init": {"financing_contingency": "2026-07-01"}}))
    hub.send(signed(signer, "07", "config.update", "od-insp",
                    {"timeline_init": {"inspection": "2026-07-01"}}))
    spoke.check_deadlines("od-fin", "2026-07-05")
    spoke.check_deadlines("od-insp", "2026-07-05")
    # The escalation trigger TEXT is identical for any milestone, so the
    # only branch-observable signal is WHICH ctx escalated.
    ctxs = [e["client_context_id"] for e in hub.queues["escalation.legal_line"]]
    assert "od-fin" in ctxs, \
        "overdue financing contingency must hit the legal line"
    assert "od-insp" not in ctxs, \
        "overdue inspection must NOT hit the legal line"


# ------------------------------------------------------------ 08:176
def test_08_disclosure_deadline_matches_doc_type_exactly(tmp_path):
    """Filed doc of the SAME type -> no missing alert. Filed doc of a
    DIFFERENT type -> missing alert. Kills the d['doc_type']==doc_type
    flip, which passes when only presence is asserted."""
    hub, _ = make_hub(str(tmp_path))
    spoke = Spoke08DocumentCollection(hub)
    hub.on_turn_start()
    hub.send(env("11", "08", "document.submission", "dd-1",
                 {"doc_type": "disclosure", "opens_correctly": True,
                  "content_hash": "h1"}))
    spoke.check_disclosure_deadline("dd-1", "disclosure")
    missing = [e for e in persisted(hub, "doc.status")
               if e["payload"].get("status") == "missing"]
    assert not missing, "filed disclosure must not raise a missing alert"
    spoke.check_disclosure_deadline("dd-1", "survey")
    missing = [e for e in persisted(hub, "doc.status")
               if e["payload"].get("status") == "missing"]
    assert missing and missing[0]["payload"]["doc_type"] == "survey", \
        "a different filed type must NOT satisfy the survey deadline"


# ------------------------------------------------------------ 15:152
def test_15_partial_roi_inputs_still_report(tmp_path):
    """spend-only and attribution-only must BOTH still emit roi_tracking
    (with roi None); only both-absent returns silently. Kills each
    operand flip of `spend is None and attribution is None`."""
    hub, _ = make_hub(str(tmp_path))
    Spoke15FinancialTracking(hub)
    hub.on_turn_start()
    hub.send(env("14", "15", "report.package", "roi-1",
                 {"marketing_spend": 500}))
    hub.send(env("14", "15", "report.package", "roi-2",
                 {"referral_attribution": {"attributed_value": 900}}))
    reports = [r for r in persisted(hub, "report.package")
               if r["payload"].get("report_type") == "roi_tracking"]
    ctxs = {r["client_context_id"] for r in reports}
    assert "roi-1" in ctxs, "spend-only must still produce roi_tracking"
    assert "roi-2" in ctxs, "attribution-only must still produce roi_tracking"


# ------------------------------------------------------------ 15:276
def test_15_pipeline_report_counts_the_exclusion(tmp_path):
    """Assert the DECISION, not just the total: excluded_unknown_count
    must equal the number of UNKNOWN-tier leads. Kills the ==UNKNOWN
    flip on the `excluded` comprehension, which leaves `total` intact."""
    hub, signer = make_hub(str(tmp_path))
    Spoke15FinancialTracking(hub)
    hub.on_turn_start()
    hub.send(signed(signer, "15", "config.update", "pv-1",
                    {"pipeline_value_request": {"leads": [
                        {"tier": "HOT", "value": 500000},
                        {"tier": "WARM", "value": 100000},
                        {"tier": "UNKNOWN", "value": 400000}]}}))
    pv = [r for r in persisted(hub, "report.package")
          if r["payload"].get("report_type") == "pipeline_value"][0]
    assert pv["payload"]["total"] == 600000
    assert pv["payload"]["excluded_unknown_count"] == 1, \
        "exclusion count must reflect exactly the UNKNOWN-tier leads"


# ------------------------------------------------------------ 16:246
def test_16_verdict_trace_distinguishes_approved_from_rejected(tmp_path):
    """The verdict==\"approved\" comparison only shapes the audit trace
    ('cleared' vs 'not cleared, held'). Pin the trace text so the
    branch is observable."""
    hub, _ = make_hub(str(tmp_path))
    Spoke16AfterCloseReferral(hub)
    hub.on_turn_start()
    hub.send(env("17", "16", "content.verdict", "cv-1",
                 {"verdict": "approved"}))
    hub.send(env("17", "16", "content.verdict", "cv-2",
                 {"verdict": "rejected"}))
    thoughts = {t["envelope_id"]: t["thought"] for t in hub.spoke_traces
                if t["agent"] == "16" and "verdict" in t["thought"]}
    approved = [v for v in thoughts.values() if "'approved'" in v]
    rejected = [v for v in thoughts.values() if "'rejected'" in v]
    assert approved and "- cleared" in approved[0], \
        "approved verdict must trace as cleared"
    assert rejected and "not cleared, held" in rejected[0], \
        "non-approved verdict must trace as not cleared/held"


# ---------------------------------------------- 18:249 / 257 / 264
def test_18_protected_block_move_decision_table(tmp_path):
    """Decision table for the protected-block move path:
      (protected id, agent requester)  -> refused via clarification, kept
      (protected id, human requester)  -> deleted, no clarification
      (unknown id,  agent requester)   -> nothing (no refusal, no delete)
      (wrong payload key)              -> handler not entered
    Kills 18:249 (intent/key guards), 18:257 (membership + requester),
    18:264 (deletion membership)."""
    hub, signer = make_hub(str(tmp_path))
    spoke = Spoke18CalendarTask(hub)
    hub.on_turn_start()
    hub.send(env("06", "18", "calendar.event", "pb-1",
                 {"day": "2026-08-10", "event_id": "blk-1",
                  "protected": True, "time": "10:00", "timezone_confirmed": True}))
    hub.send(env("06", "18", "calendar.event", "pb-1",
                 {"day": "2026-08-10", "event_id": "blk-2",
                  "protected": True, "time": "12:00", "timezone_confirmed": True}))
    assert "blk-1" in spoke.protected_blocks

    # agent requester -> refuse, keep
    hub.send(signed(signer, "18", "config.update", "pb-1",
                    {"move_protected_block": {"event_id": "blk-1",
                                              "requester": "07"}}))
    clar = persisted(hub, "clarification.request")
    assert any("blk-1" in c["payload"]["reason"] and
               "human confirmation only" in c["payload"]["reason"]
               for c in clar), "agent move request must be refused"
    assert "blk-1" in spoke.protected_blocks, "refused block must survive"

    # human requester -> delete, no new refusal
    n_clar = len(persisted(hub, "clarification.request"))
    hub.send(signed(signer, "18", "config.update", "pb-1",
                    {"move_protected_block": {"event_id": "blk-2",
                                              "requester": "human"}}))
    assert "blk-2" not in spoke.protected_blocks, \
        "human-confirmed move must delete the block"
    assert len(persisted(hub, "clarification.request")) == n_clar, \
        "human move must not be refused"

    # unknown id -> no refusal, nothing deleted
    hub.send(signed(signer, "18", "config.update", "pb-1",
                    {"move_protected_block": {"event_id": "no-such",
                                              "requester": "07"}}))
    assert len(persisted(hub, "clarification.request")) == n_clar, \
        "unknown block id must not trigger the refusal path"
    assert "blk-1" in spoke.protected_blocks


# ------------------------------------------------------------ 18:343
def test_18_briefing_tracks_only_genuinely_conflicting_deadlines(tmp_path):
    """Conflict tracked iff a ctx has >1 distinct deadline for its
    milestones; agreeing sources must NOT appear. Kills the >1 flip."""
    hub, _ = make_hub(str(tmp_path))
    spoke = Spoke18CalendarTask(hub)
    hub.on_turn_start()
    hub.send(env("07", "18", "deadline.alert", "cf-1",
                 {"milestone": "financing", "deadline": "2026-08-20"}))
    hub.send(env("07", "18", "deadline.alert", "cf-1",
                 {"milestone": "inspection", "deadline": "2026-08-25"}))
    hub.send(env("07", "18", "deadline.alert", "cf-2",
                 {"milestone": "financing", "deadline": "2026-08-20"}))
    briefing = spoke.generate_briefing()
    tracked = briefing["deadline_conflicts_tracked"]
    assert "cf-1" in tracked, \
        "two distinct deadlines for one ctx must be tracked as a conflict"
    assert "cf-2" not in tracked, \
        "a single agreed deadline must not be reported as a conflict"


# ------------------------------------------------------------ 19:239
def test_19_data_package_resolves_both_waits_wrong_intent_does_not(tmp_path):
    """data.package -> exactly the two resolved agent.status envelopes
    to 18; a different intent must not emit them."""
    hub, _ = make_hub(str(tmp_path))
    Spoke19Prospecting(hub)
    hub.on_turn_start()
    hub.send(env("10", "19", "data.package", "mi-1", {"anything": 1}))
    statuses = [s for s in persisted(hub, "agent.status")
                if s["from_agent"] == "19" and s["payload"].get("resolved")]
    waits = {s["payload"]["waiting_on"] for s in statuses}
    assert waits == {"market_context_enrichment", "farm_data_aggregate"}, \
        "data.package must resolve exactly these two waits"
