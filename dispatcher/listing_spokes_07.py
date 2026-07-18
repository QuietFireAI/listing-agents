"""Agent 07 - Transaction Coordinator, built against the full spec.

Owns the transaction TIMELINE from offer submission through closing.
Never represents, signs, or opines. The wire-fraud line is absolute and
checked first, on every inbound event, regardless of type.
"""
from __future__ import annotations

from .core import Envelope

SOURCE_VERIFIED = "source_verified"
STATED_BY_PARTY = "stated_by_party"
UNKNOWN = "unknown"

_WIRE_WORDS = ("wire instructions", "wiring details", "wire transfer",
              "updated wire", "routing number", "account number for closing")


def _env(frm, to, intent, ctx, payload, confidence=UNKNOWN):
    return Envelope(from_agent=frm, to_agent=to, intent=intent,
                    client_context_id=ctx, payload=payload,
                    confidence=confidence,
                    provenance={"source": f"spoke-{frm}", "captured_at": "runtime",
                                "verbatim_available": True})


class Spoke07TransactionCoordinator:
    """DECISIONS.md tuples implemented directly:
      1. deadline calculable two ways -> clarification, both calculations shown
      2. extension claimed w/ no amendment on file -> track original,
         record claim stated_by_party, alert human
      3. artifact contradicts tracked deadline -> halt milestone, human
         with both items
      4. multiple deadlines same day -> alert each individually, never
         summarize into one
      5. wire topic anywhere -> full stop, escalation.legal_line, no
         partial handling
      6. inspection report received -> log + distribute; repair
         NEGOTIATION content is human-only, full stop
      7. appraisal below contract price -> log fact, all options human,
         never draft gap strategies
      8. financing contingency date passes with no removal on file ->
         alert human same hour, never assume waived
      9. title exception surfaces -> log verbatim to human, never
         characterize severity
      10. closing date moves verbally -> track original until signed
          amendment, claim stated_by_party
      11. earnest money receipt not confirmed by deadline -> escalate,
          money milestones never get benefit of the doubt
      12. possession terms ambiguous -> clarification with exact clause
          quoted, never interpolate intent

    Wired for real 2026-07-17 (owner decision, not left as documentation-
    only): transaction milestone data (which milestones need a document
    request, which need vendor scheduling and what kind) used to be two
    independent hardcoded structures here plus a THIRD independent
    hardcoded reverse-mapping in the vendor.cancellation_notice handler -
    three copies of overlapping facts that could silently drift from each
    other. Now one constructor parameter (transaction_milestone_config),
    genuinely single-sourced; kind_to_milestone() derives the reverse
    mapping from it rather than duplicating it. Default reproduces the
    prior hardcoded values exactly - every existing test passes
    unmodified. Deliberately NOT covered: the per-milestone business logic
    in doc.status handling (inspection/appraisal/title/earnest_money/
    closing) and the financing_contingency deadline-alert case - those are
    genuinely distinct logic per milestone, not lookup data, and making
    them "configurable" would mean inventing a rules engine and guessing
    at business logic that isn't this review's to invent.
    """

    # TUNABLE (owner-ratified 2026-07-16): vendor_holdup_days=7.
    # TUNABLE (owner-ratified 2026-07-17): transaction_milestone_config -
    # was two independent hardcoded structures (doc_milestones,
    # vendor_milestones) plus a THIRD independent hardcoded reverse-mapping
    # in Agent 09's cancellation handler (kind_to_milestone) that could
    # drift from either. Now one source of truth, constructor-injected;
    # default reproduces the exact prior hardcoded behavior byte-for-byte.
    # Does NOT cover the per-milestone business logic in doc.status
    # handling (inspection/appraisal/title/earnest_money/closing) or the
    # financing_contingency deadline-alert special case - those are
    # genuinely distinct logic per milestone, not lookup data, and stay
    # in Python. See docs/TUNING_MANUAL.md to change.
    _DEFAULT_TRANSACTION_MILESTONES = {
        "inspection": {"needs_document": True, "vendor_kind": "inspector"},
        "appraisal": {"needs_document": True, "vendor_kind": "appraiser"},
        "title": {"needs_document": True, "vendor_kind": None},
        "hoa_docs": {"needs_document": True, "vendor_kind": None},
        "financing_contingency": {"needs_document": True, "vendor_kind": None},
        "earnest_money": {"needs_document": False, "vendor_kind": None},
        "closing": {"needs_document": False, "vendor_kind": None},
    }

    def __init__(self, hub, vendor_holdup_days: int = 7,
                 transaction_milestone_config: dict[str, dict] | None = None):
        self.hub = hub
        self.vendor_holdup_days = vendor_holdup_days
        self.transaction_milestone_config = (transaction_milestone_config or
                                             self._DEFAULT_TRANSACTION_MILESTONES)
        self.timelines: dict[str, dict] = {}  # ctx -> {milestone: {deadline, satisfied, artifact}}
        self.offer_status: dict[str, dict] = {}  # ctx -> {stage, response_deadline}
        # tracks outstanding vendor.request per (ctx, milestone) -> date sent,
        # cleared on the matching deliverable.release. 07 is cc'd on 09's
        # deliverables specifically so a silent vendor failure doesn't go
        # unnoticed until the deadline itself lapses - a 7-day hold-up
        # timer to HITL, independent of and faster than deadline expiry.
        self.vendor_requests_pending: dict[str, dict[str, str]] = {}
        hub.register("07", self.handle)

    def kind_to_milestone(self, vendor_kind: str) -> str | None:
        """Reverse lookup, genuinely derived from transaction_milestone_config
        (not an independent hardcoded copy - this is what used to drift)."""
        for m, cfg in self.transaction_milestone_config.items():
            if cfg.get("vendor_kind") == vendor_kind:
                return m
        return None

    def _wire_check(self, payload: dict, ctx: str) -> bool:
        def flatten_strings(obj):
            if isinstance(obj, str):
                yield obj
            elif isinstance(obj, dict):
                for v in obj.values():
                    yield from flatten_strings(v)
            elif isinstance(obj, (list, tuple)):
                for v in obj:
                    yield from flatten_strings(v)
        text = " ".join(flatten_strings(payload)).lower()
        for w in _WIRE_WORDS:
            if w in text:
                self.hub.escalate("escalation.legal_line",
                                  {"client_context_id": ctx,
                                   "trigger": f"wire topic detected: {w!r} - "
                                             "full stop, out-of-band "
                                             "verification required, no "
                                             "partial handling",
                                   "agent": "07"})
                return True
        return False

    def handle(self, env: Envelope):
        ctx = env.client_context_id
        payload = env.payload

        # Wire-fraud line: checked FIRST on every inbound event, regardless
        # of intent type - "any wire topic in any channel", active every step.
        if self._wire_check(payload, ctx):
            return

        if env.intent == "vendor.cancellation_notice":
            # 09's direct, immediate signal (tuple 1 on 09's side) - not
            # the 7-day holdup timer, which is built for silence/non-
            # response, the wrong mechanism for a definitive cancellation.
            # Map the vendor kind back to the actual milestone it was
            # scheduled for, genuinely derived from the same
            # transaction_milestone_config used to order inspector/
            # appraiser vendors (self.kind_to_milestone()) - not an
            # independent hardcoded copy that could drift from it, which
            # is what this was before 2026-07-17.
            vendor_kind = payload.get("vendor_kind")
            milestone = self.kind_to_milestone(vendor_kind)
            deadline = (self.timelines.get(ctx, {}).get(milestone, {}).get("deadline")
                       if milestone else None)
            self.hub.escalate("escalation.legal_line",
                              {"client_context_id": ctx,
                               "trigger": f"vendor ({vendor_kind!r}) "
                                         f"cancelled late" +
                                         (f" - milestone {milestone!r} "
                                          f"(deadline {deadline!r}) is now "
                                          f"at risk" if milestone else
                                          " - no tracked milestone maps to "
                                          "this vendor kind"),
                               "agent": "07"})
            self.hub.send(_env("07", "14", "interaction.log", ctx,
                               {"kind": "vendor_cancellation_received",
                                "vendor_kind": vendor_kind,
                                "affected_milestone": milestone}))
            return

        if env.intent == "config.update":
            if "offer_status" in payload:
                stage_data = payload["offer_status"]
                self.offer_status[ctx] = stage_data
                # Owner decision #5 (2026-07-17): offer-status tracking is a
                # HUMAN-facing alert, not a client touch. The old 11 send
                # rendered through 11's generic template path straight to
                # the CLIENT channel, and (found in 18's review) the
                # milestone-less payload also created a junk None-keyed
                # protected block on day None in 18's calendar. Both gone:
                # human queue + record only.
                self.hub.send(_env("07", "queue", "clarification.request", ctx,
                                   {"reason": "offer status update - human "
                                             "review, never a client touch",
                                    "offer_status": stage_data}))
                self.hub.send(_env("07", "14", "interaction.log", ctx,
                                   {"kind": "offer_status", **stage_data}))
                return

            if "timeline_init" in payload:
                # P09 precondition: timeline loaded with full deadline set
                milestones = payload["timeline_init"]
                self.timelines[ctx] = {
                    m: {"deadline": d, "satisfied": False, "artifact": None}
                    for m, d in milestones.items()}
                self.hub.send(_env("07", "14", "interaction.log", ctx,
                                   {"kind": "timeline_loaded",
                                    "milestones": list(milestones)}))
                # job component: request required documents from 08 for
                # every document-bearing milestone, as they come due
                sent_date = payload.get("today")  # test/caller supplies today
                for m in milestones:
                    cfg = self.transaction_milestone_config.get(m, {})
                    if cfg.get("needs_document"):
                        self.hub.send(_env("07", "08", "doc.request", ctx,
                                           {"milestone": m, "today": sent_date}))
                # job component: inspector/appraiser scheduling per milestone
                for m in milestones:
                    kind = self.transaction_milestone_config.get(m, {}).get("vendor_kind")
                    if kind:
                        self.hub.send(_env("07", "09", "vendor.request", ctx,
                                           {"kind": kind, "milestone": m}))
                        if sent_date:
                            self.vendor_requests_pending.setdefault(ctx, {})[m] = sent_date
                            self.hub.send(_env("07", "18", "agent.status", ctx,
                                               {"waiting_on": f"vendor_scheduling:{m}",
                                                "since": sent_date}))
                return

            if "deadline_two_ways" in payload:
                calcs = payload["deadline_two_ways"]
                self.hub.send(_env("07", "queue", "clarification.request", ctx,
                                   {"reason": "deadline calculable two ways",
                                    "calculations": calcs}))
                return

            if "extension_claim" in payload:
                claim = payload["extension_claim"]
                milestone = claim.get("milestone")
                if not claim.get("amendment_on_file"):
                    # tuple 2: track the ORIGINAL, record claim stated_by_party
                    self.hub.ingest_spoke_trace(
                        "07", env.envelope_id,
                        thought=f"extension claimed for {milestone!r} but no "
                                f"amendment on file - tracking original "
                                f"deadline, recording claim as "
                                f"stated_by_party, alerting human",
                        result="original deadline retained")
                    # Owner decision #5 (2026-07-17): "alert human" means
                    # the human queue - the old 11 send put an unconfirmed
                    # extension claim on the CLIENT channel via 11's
                    # generic template path.
                    self.hub.send(_env("07", "queue", "clarification.request",
                                       ctx, {"reason": "extension claimed "
                                                       "with no amendment on "
                                                       "file - original "
                                                       "deadline stands, "
                                                       "human review",
                                            "milestone": milestone},
                                       confidence=STATED_BY_PARTY))
                return

            if "closing_date_moved_verbally" in payload:
                claim = payload["closing_date_moved_verbally"]
                if not claim.get("signed_amendment"):
                    self.hub.ingest_spoke_trace(
                        "07", env.envelope_id,
                        thought="closing date reportedly moved verbally, no "
                                "signed amendment - tracking the original "
                                "date until one exists; claim recorded "
                                "stated_by_party",
                        result="original closing date retained")
                return

            if "possession_terms_ambiguous" in payload:
                clause = payload["possession_terms_ambiguous"].get("clause_text")
                self.hub.send(_env("07", "queue", "clarification.request", ctx,
                                   {"reason": "possession terms ambiguous",
                                    "exact_clause": clause}))
                return
            return

        if env.intent == "doc.status":
            milestone = payload.get("milestone")
            timeline = self.timelines.get(ctx, {})
            entry = timeline.get(milestone)

            if payload.get("contradicts_tracked_deadline"):
                self.hub.ingest_spoke_trace(
                    "07", env.envelope_id,
                    thought=f"received artifact contradicts the tracked "
                            f"deadline for {milestone!r} - halting this "
                            f"milestone, human gets both items",
                    result="halted: artifact contradiction")
                self.hub.escalate("escalation.legal_line",
                                  {"client_context_id": ctx,
                                   "trigger": f"artifact contradicts tracked "
                                             f"deadline: {milestone}",
                                   "agent": "07"})
                return

            if milestone == "inspection" and payload.get("report_received"):
                # tuple 6: log + distribute; repair NEGOTIATION is human-only
                self.hub.send(_env("07", "14", "interaction.log", ctx,
                                   {"kind": "inspection_report_received"}))
                if payload.get("repair_requests_present"):
                    self.hub.escalate("escalation.legal_line",
                                      {"client_context_id": ctx,
                                       "trigger": "repair negotiation content "
                                                 "- human-only, full stop",
                                       "agent": "07"})
                if entry:
                    entry["satisfied"] = True
                    entry["artifact"] = "inspection_report"
                return

            if milestone == "appraisal" and "appraised_value" in payload:
                contract_price = payload.get("contract_price")
                if contract_price and payload["appraised_value"] < contract_price:
                    # tuple 7: log the fact, all options human, never draft
                    # gap strategies
                    self.hub.escalate("escalation.legal_line",
                                      {"client_context_id": ctx,
                                       "trigger": f"appraisal "
                                                 f"{payload['appraised_value']} "
                                                 f"below contract price "
                                                 f"{contract_price} - all "
                                                 f"options are human",
                                       "agent": "07"})
                if entry:
                    entry["satisfied"] = True
                    entry["artifact"] = "appraisal_report"
                return

            if milestone == "title" and payload.get("exception_found"):
                # tuple 9: log verbatim, never characterize severity
                self.hub.escalate("escalation.legal_line",
                                  {"client_context_id": ctx,
                                   "trigger": f"title exception: "
                                             f"{payload.get('exception_text')!r} "
                                             f"(logged verbatim, not "
                                             f"characterized)",
                                   "agent": "07"})
                return

            if milestone == "earnest_money" and not payload.get("receipt_confirmed"):
                # tuple 11: money milestones never get benefit of the doubt
                self.hub.escalate("escalation.legal_line",
                                  {"client_context_id": ctx,
                                   "trigger": "earnest money receipt not "
                                             "confirmed by deadline",
                                   "agent": "07"})
                return

            if milestone == "closing" and payload.get("artifact_on_file"):
                if entry:
                    entry["satisfied"] = True
                    entry["artifact"] = payload.get("artifact_ref")
                # Contract fix, found 2026-07-17 (same defect class as the
                # 19->13 empty-contract bug): this send carried only
                # {"closed": True}. 16 reads close_date (without it the
                # 30/90/365 post-close check-ins NEVER fire) and 15 reads
                # commission_amount (without it every commission record is
                # None-driven). close_date is 07's own tracked truth - the
                # closing milestone's deadline. commission_amount has no
                # upstream source anywhere in this identity yet; sending
                # it when present (rather than inventing one) keeps that
                # gap visible instead of papered over - see the review
                # findings, owner decision required on its origin.
                closed_payload = {"closed": True,
                                  "close_date": (entry or {}).get("deadline"),
                                  "commission_amount":
                                      payload.get("commission_amount"),
                                  "sale_price": payload.get("sale_price"),
                                  "signed_docs_only":
                                      payload.get("signed_docs_only", False)}
                self.hub.ingest_spoke_trace(
                    "07", env.envelope_id,
                    thought="closing artifact on file - executing "
                            "transaction.closed to 16/14/15, triggering P10",
                    result="transaction.closed issued")
                self.hub.send(_env("07", "16", "transaction.closed", ctx,
                                   closed_payload))
                self.hub.send(_env("07", "14", "transaction.closed", ctx,
                                   closed_payload))
                self.hub.send(_env("07", "15", "transaction.closed", ctx,
                                   closed_payload))
                return

            if entry is not None and payload.get("artifact_on_file"):
                entry["satisfied"] = True
                entry["artifact"] = payload.get("artifact_ref")
                self.hub.send(_env("07", "14", "interaction.log", ctx,
                                   {"kind": "milestone_satisfied",
                                    "milestone": milestone}))
            return

        if env.intent == "deliverable.release":
            milestone = payload.get("milestone")
            if ctx in self.vendor_requests_pending:
                self.vendor_requests_pending[ctx].pop(milestone, None)
                self.hub.send(_env("07", "18", "agent.status", ctx,
                                   {"waiting_on": f"vendor_scheduling:{milestone}",
                                    "resolved": True}))
            self.hub.ingest_spoke_trace(
                "07", env.envelope_id,
                thought=f"vendor deliverable received for {milestone!r} - "
                        f"scheduling confirmed, clearing the hold-up timer "
                        f"for this milestone",
                result="vendor_request_cleared")
            self.hub.send(_env("07", "14", "interaction.log", ctx,
                               {"kind": "vendor_deliverable_received",
                                "milestone": milestone}))
            return

    def check_vendor_holdups(self, ctx: str, today: str):
        """7-day hold-up timer to HITL: if a vendor.request has gone
        unanswered (no deliverable.release) for 7+ days, that's a hold-up
        worth a human's attention on its own - independent of, and faster
        than, waiting for the milestone deadline itself to lapse."""
        import datetime
        pending = self.vendor_requests_pending.get(ctx, {})
        today_d = datetime.date.fromisoformat(today)
        flagged = []
        for milestone, sent_date in list(pending.items()):
            sent_d = datetime.date.fromisoformat(sent_date)
            if (today_d - sent_d).days >= self.vendor_holdup_days:
                self.hub.escalate("escalation.legal_line",
                                  {"client_context_id": ctx,
                                   "trigger": f"vendor scheduling for "
                                             f"{milestone!r} unconfirmed "
                                             f"after 7 days - hold-up to "
                                             f"HITL",
                                   "agent": "07"})
                flagged.append(milestone)
        return flagged

    def check_deadlines(self, ctx: str, today: str):
        """Alert lead time is configuration, not judgment - called by an
        external scheduler with today's date. Multiple deadlines landing
        the same day get individual alerts (tuple 4), never summarized."""
        timeline = self.timelines.get(ctx, {})
        due_today = [(m, e) for m, e in timeline.items()
                    if not e["satisfied"] and e["deadline"] == today]
        for milestone, entry in due_today:
            self.hub.send(_env("07", "11", "deadline.alert", ctx,
                               {"milestone": milestone, "deadline": today}))
            self.hub.send(_env("07", "18", "deadline.alert", ctx,
                               {"milestone": milestone, "deadline": today}))
            if milestone == "financing_contingency":
                # tuple 8: date passes with no removal on file -> alert
                # same hour, never assume waived
                self.hub.escalate("escalation.legal_line",
                                  {"client_context_id": ctx,
                                   "trigger": "financing contingency date "
                                             "passed, no removal on file",
                                   "agent": "07"})
        return due_today
