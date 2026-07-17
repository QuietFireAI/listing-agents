"""Agent 03 - Lead Nurture, built against the full spec.

Note: the P11 demo's Spoke03Nurture was a DELIBERATE negative-path exhibit
(empty thought trace, to prove the taint gate catches it) - not a real
implementation. This is the real one: it submits real thought traces like
every other spoke, and the taint-gate proof already lives in the pillar
tests (a synthetic empty trace injected directly), not in this agent.
"""
from __future__ import annotations

from .core import Envelope

SOURCE_VERIFIED = "source_verified"
STATED_BY_PARTY = "stated_by_party"
UNKNOWN = "unknown"


def _env(frm, to, intent, ctx, payload, confidence=UNKNOWN, in_reply_to=None):
    return Envelope(from_agent=frm, to_agent=to, intent=intent,
                    client_context_id=ctx, payload=payload,
                    confidence=confidence, in_reply_to=in_reply_to,
                    provenance={"source": f"spoke-{frm}", "captured_at": "runtime",
                                "verbatim_available": True})


_LEGAL_LINE_WORDS = ("what should i offer", "should i list at",
                    "negotiate", "contract terms", "legal opinion")


class Spoke03LeadNurture:
    """Long-cycle engagement: drip sequences, market updates, behavioral
    re-engagement, hands leads back to 02 on readiness change.

    DECISIONS.md tuples implemented directly:
      1. lead replies mid-sequence -> pause, route the reply, never auto-continue
      2. ambiguous opt-out ('stop sending so many') -> frequency complaint:
         reduce + confirm; explicit 'stop' is a full opt-out (different
         tuple). Fixed 2026-07-16: tuple 1's blanket pause used to be set
         unconditionally before this branch ran, and nothing here ever
         undid it - a frequency complaint silently killed the sequence
         forever, identical in effect to the opt-out this tuple explicitly
         says it isn't. This branch now explicitly resumes the sequence.
         Fixed 2026-07-17: the "+ confirm" half never sent anything to
         the client either - now sends a real client-facing confirmation.
      3. sequence content has expired market data -> regenerate or skip the touch
      4. two sequences eligible -> run neither until human picks, never
         stack. Confirmed 2026-07-16 (owner) distinct from tuple 10: this
         is an ambiguous, simultaneous conflict with no clear precedence
         - the default behavior for any sequence_id overlap that isn't
         explicitly signaled as a reassignment.
      5. engagement spike -> rescore via 02, never convert signal into
         direct outreach itself
      6. contact replies STOP/equivalent -> suppress across ALL channels
         immediately, confirmation (channel rules generally require it).
         Fixed 2026-07-17: only an internal interaction.log entry to 14
         existed - a system-of-record entry, not a message to the client.
         Now sends a real confirmation, reusing Agent 01's opt_out_
         confirmed template rather than inventing a near-duplicate.
      7. step lands on legal holiday/outside contact hours -> shift to next
         legal window, never send anyway. Fixed 2026-07-17: zero
         implementing code existed despite this docstring previously
         listing it as done - send_scheduled_touch() only ever checked
         compliance_status/paused/frequency_cap. Now checks a real
         legal_contact_hours window (constructor tunable) and an explicit
         holiday flag before sending.
      8. engagement spike mid-sequence -> pause + lead.rescored, never keep
         dripping on a hot signal
      9. content references a listing that changed status -> pull the step,
         stale claims are fabrications
      10. two sequences target one context -> one per context, newest
          signed instruction wins, overlap logged. Confirmed 2026-07-16
          (owner) distinct from tuple 4: this is a deliberate, later
          reassignment (02 re-evaluating after a rescore), not an
          ambiguous simultaneous conflict. Fixed: lead.nurture now
          carries reassignment=True when triggered by env.intent==
          'lead.rescored' on 02's side - that's the actual signal this
          agent needed and never had. When set, the new sequence
          supersedes the old one outright (logged, not held); when
          absent, tuple 4's hold-for-human applies.
      11. substantive question from inside a drip -> route to 11 for a
          gated human-reviewed reply, out of sequence
    """

    # TUNABLE (owner-ratified 2026-07-16): frequency_cap_per_week=3.
    # TUNABLE (added 2026-07-17): legal_contact_hours=(8, 21) - matches
    # Agent 11's quiet_hours convention (24h clock, wraps if start>end).
    # See docs/TUNING_MANUAL.md to change.
    def __init__(self, hub, frequency_cap_per_week: int = 3,
                 legal_contact_hours: tuple[int, int] = (8, 21)):
        self.hub = hub
        self.active_sequences: dict[str, dict] = {}  # ctx -> {sequence_id, paused, touch_count}
        self.frequency_cap_per_week = frequency_cap_per_week
        self.legal_contact_hours = legal_contact_hours
        self.touch_log: dict[str, list] = {}  # ctx -> list of touch timestamps/weeks
        hub.register("03", self.handle)

    def _in_legal_contact_hours(self, hour: int) -> bool:
        start, end = self.legal_contact_hours
        if start > end:  # wraps midnight
            return hour >= start or hour < end
        return start <= hour < end

    def _legal_line_hit(self, text: str) -> str | None:
        low = text.lower()
        for w in _LEGAL_LINE_WORDS:
            if w in low:
                return text.strip()
        return None

    def request_market_update(self, ctx: str):
        """Job component: 'send market updates built from Market Data (10)
        packages' - the data.request half of that, called when a
        market-update touch is due in an active sequence. Data only, no
        opinion - built entirely from whatever 10 returns in data.package."""
        seq = self.active_sequences.get(ctx)
        if not seq or seq.get("compliance_status") != "cleared":
            return None  # no send-eligible sequence, nothing to update
        self.hub.send(_env("03", "10", "data.request", ctx,
                           {"purpose": "market_update"}))
        return True

    def send_scheduled_touch(self, ctx: str, content: dict, today_week: str,
                             hour: int | None = None, is_legal_holiday: bool = False):
        """Actually delivers the next scheduled touch, enforcing the
        frequency cap for real. Previously: touch_log was declared but
        never read or written anywhere, and frequency_cap_per_week only
        ever decreased on a complaint - nothing checked touches sent this
        week against the cap before sending, meaning the whole mechanism
        was decorative. Fixed: real enforcement, using the log that was
        already sitting there unused.

        Fixed 2026-07-17 (tuple 7): a step landing on a legal holiday or
        outside contact hours must shift to the next legal window, never
        send anyway - this had zero implementing code despite the class
        docstring listing it as done. Held here, matching the same status-
        string pattern as the frequency-cap hold; the actual "shift to
        next window" retry is the caller's job once told not now, same as
        every other scheduled-sweep method in this codebase."""
        seq = self.active_sequences.get(ctx)
        if not seq or seq.get("compliance_status") != "cleared" or seq.get("paused"):
            return "not_eligible"

        if is_legal_holiday:
            self.hub.ingest_spoke_trace(
                "03", "internal",
                thought=f"ctx={ctx!r}: today is a legal holiday - shifting "
                        f"to the next legal window, never sending anyway",
                result="held: legal_holiday")
            return "held_legal_holiday"
        if hour is not None and not self._in_legal_contact_hours(hour):
            self.hub.ingest_spoke_trace(
                "03", "internal",
                thought=f"ctx={ctx!r}: hour={hour} is outside legal contact "
                        f"hours {self.legal_contact_hours} - shifting to "
                        f"the next legal window, never sending anyway",
                result="held: outside_contact_hours")
            return "held_outside_contact_hours"

        weeks_log = self.touch_log.setdefault(ctx, [])
        this_week_count = sum(1 for w in weeks_log if w == today_week)
        if this_week_count >= self.frequency_cap_per_week:
            self.hub.ingest_spoke_trace(
                "03", "internal",
                thought=f"ctx={ctx!r} already at {this_week_count}/"
                        f"{self.frequency_cap_per_week} touches for week "
                        f"{today_week!r} - holding, cap enforced for real",
                result="held: frequency_cap")
            return "held_frequency_cap"

        weeks_log.append(today_week)
        seq["touch_count"] = seq.get("touch_count", 0) + 1
        self.hub.send(_env("03", "11", "client.message.request", ctx,
                           {"template": "sequence_touch", "content": content}))
        return "sent"

    def handle(self, env: Envelope):
        ctx = env.client_context_id

        if env.intent == "lead.nurture":
            consent = env.payload.get("consent") or {}
            # Job component: verify consent before ANY send - no consent on
            # file = no send, escalate.
            if not any(v == "yes" for v in consent.values()):
                self.hub.ingest_spoke_trace(
                    "03", env.envelope_id,
                    thought="no channel has recorded consent - no send "
                            "permitted on any channel, escalating rather "
                            "than silently holding forever",
                    result="escalated: no_consent")
                self.hub.escalate("escalation.legal_line",
                                  {"client_context_id": ctx,
                                   "trigger": "nurture assignment with no "
                                             "consent on any channel",
                                   "agent": "03"})
                return

            existing = self.active_sequences.get(ctx)
            requested_seq = env.payload.get("sequence_id", "default_drip")
            is_reassignment = env.payload.get("reassignment", False)
            if existing and existing["sequence_id"] != requested_seq \
                    and not existing.get("superseded"):
                if is_reassignment:
                    # tuple 10: "two sequences would target one context...
                    # newest SIGNED instruction wins, overlap logged" - a
                    # deliberate later re-evaluation (e.g. Agent 02
                    # reassigning after a rescore), not an ambiguous
                    # simultaneous eligibility conflict (tuple 4 below).
                    # Distinct scenarios, confirmed 2026-07-16 - the code
                    # used to treat every overlap as tuple 4's, meaning a
                    # legitimate reassignment got stuck in human review it
                    # never needed.
                    self.hub.ingest_spoke_trace(
                        "03", env.envelope_id,
                        thought=f"ctx={ctx!r} reassigned from "
                                f"{existing['sequence_id']!r} to "
                                f"{requested_seq!r} by a newer signed "
                                f"instruction - superseding, not holding; "
                                f"overlap logged",
                        result=f"superseded: {existing['sequence_id']!r} -> "
                              f"{requested_seq!r}")
                    self.hub.send(_env("03", "14", "interaction.log", ctx,
                                       {"kind": "sequence_reassigned",
                                        "previous_sequence_id": existing["sequence_id"],
                                        "new_sequence_id": requested_seq}))
                    # falls through to the fresh-assignment path below -
                    # the new instruction replaces the old one outright
                else:
                    # tuple 4: two sequences eligible for one lead -> run
                    # neither until the human picks, never stack
                    self.hub.ingest_spoke_trace(
                        "03", env.envelope_id,
                        thought=f"ctx={ctx!r} already running "
                                f"{existing['sequence_id']!r}; new request for "
                                f"{requested_seq!r} - two sequences eligible, "
                                f"running NEITHER until human picks (never "
                                f"stack), overlap logged",
                        result="held: overlapping sequences")
                    self.hub.send(_env("03", "queue", "clarification.request", ctx,
                                       {"reason": "overlapping sequences",
                                        "existing": existing["sequence_id"],
                                        "requested": requested_seq}))
                    self.active_sequences[ctx] = {**existing, "paused": True,
                                                  "overlap_with": requested_seq}
                    return

            self.active_sequences[ctx] = {"sequence_id": requested_seq,
                                          "paused": False, "touch_count": 0,
                                          "consent": consent,
                                          "compliance_status": "pending"}
            self.hub.send(_env("03", "18", "agent.status", ctx,
                               {"waiting_on": "compliance_review",
                                "since": env.payload.get("today")}))
            self.hub.ingest_spoke_trace(
                "03", env.envelope_id,
                thought=f"sequence {requested_seq!r} started for ctx={ctx!r}; "
                        f"consent verified, but per job component #5 no "
                        f"content sends until Compliance (17) clears it - "
                        f"submitting for review before first send",
                result="content.review issued, sequence pending compliance")
            self.hub.send(_env("03", "17", "content.review", ctx,
                               {"sequence_id": requested_seq}))
            return

        if env.intent == "lead.reply":
            seq = self.active_sequences.get(ctx)
            if seq:
                seq["paused"] = True  # tuple 1: reply pauses, never auto-continue
            message = env.payload.get("message", "")

            trigger = self._legal_line_hit(message)
            if trigger:
                self.hub.ingest_spoke_trace(
                    "03", env.envelope_id,
                    thought=f"reply crosses the legal line: {trigger!r} - "
                            f"escalating verbatim",
                    result="escalated: legal_line")
                self.hub.escalate("escalation.legal_line",
                                  {"client_context_id": ctx,
                                   "trigger": trigger, "agent": "03"})
                return

            low = message.lower().strip()
            if low in ("stop", "unsubscribe", "opt out", "optout"):
                # tuple 6: explicit STOP -> full suppression, all channels,
                # confirmation (channel rules generally require it - SMS/
                # CTIA mandates a one-time confirmation reply). Fixed
                # 2026-07-17: only an internal interaction.log entry to 14
                # existed - that's a system-of-record entry, not a message
                # to the client. Nothing ever actually told the client the
                # stop was received. Reuses Agent 01's opt_out_confirmed
                # template (same underlying confirmation, same wording).
                if seq:
                    seq["consent"] = {k: "no" for k in seq.get("consent", {})}
                    seq["paused"] = True
                self.hub.ingest_spoke_trace(
                    "03", env.envelope_id,
                    thought="explicit STOP received - full opt-out, "
                            "suppression across ALL channels immediately, "
                            "propagating to CRM same-day, confirming to "
                            "the client once",
                    result="opted out: all channels")
                self.hub.send(_env("03", "14", "interaction.log", ctx,
                                   {"kind": "opt_out", "scope": "all_channels",
                                    "consent": seq["consent"] if seq else {}}))
                self.hub.send(_env("03", "11", "client.message.request", ctx,
                                   {"template": "opt_out_confirmed"}))
                return

            if "stop sending so many" in low or "too many" in low:
                # tuple 2: ambiguous frequency complaint -> reduce + confirm,
                # NOT a full opt-out. Was: the blanket "any reply pauses"
                # logic above (tuple 1) set paused=True unconditionally,
                # and nothing here ever undid it - a lead saying "you're
                # emailing me too much" silently killed their entire
                # sequence forever, instead of continuing at a lower
                # cadence as this tuple actually requires. Fixed: this
                # reply type explicitly resumes the sequence. Fixed
                # 2026-07-17: the "+ confirm" half never sent anything
                # to the client either - reducing cadence silently isn't
                # the same as confirming it to the person who asked.
                self.frequency_cap_per_week = max(1, self.frequency_cap_per_week - 1)
                if seq:
                    seq["paused"] = False
                self.hub.ingest_spoke_trace(
                    "03", env.envelope_id,
                    thought="frequency complaint, not an explicit opt-out - "
                            "reducing cadence and confirming to the client, "
                            "sequence resumes (not left paused) at lower "
                            "frequency",
                    result=f"frequency reduced to {self.frequency_cap_per_week}/wk, "
                          f"resumed")
                self.hub.send(_env("03", "11", "client.message.request", ctx,
                                   {"template": "frequency_reduced_confirmed"}))
                return

            # substantive question mid-drip -> route to 11, gated human review
            self.hub.ingest_spoke_trace(
                "03", env.envelope_id,
                thought="substantive reply from inside a drip - routing to "
                        "Client Communication (11) for a gated, human-"
                        "reviewed reply; sequence stays paused",
                result="routed to 11")
            self.hub.send(_env("03", "11", "client.message.request", ctx,
                               {"reason": "substantive drip reply",
                                "verbatim": message},
                               in_reply_to=env.envelope_id))
            return

        if env.intent == "behavioral.signal":
            score_delta = env.payload.get("engagement_score", 0)
            spike = score_delta >= env.payload.get("spike_threshold", 50)
            if spike:
                seq = self.active_sequences.get(ctx)
                if seq:
                    seq["paused"] = True  # tuples 5 + 8: pause, never drip on a hot signal
                self.hub.ingest_spoke_trace(
                    "03", env.envelope_id,
                    thought="engagement spike detected - pausing sequence "
                            "and rescoring via 02; never converting the "
                            "signal into direct outreach myself",
                    result="paused, rescored")
                self.hub.send(_env("03", "02", "lead.rescored", ctx,
                                   {"reason": "engagement_spike",
                                    "engagement_score": score_delta}))
            return

        if env.intent == "content.verdict":
            verdict = env.payload.get("verdict")
            seq = self.active_sequences.get(ctx)
            if verdict == "approved":
                if seq:
                    seq["compliance_status"] = "cleared"
                self.hub.send(_env("03", "18", "agent.status", ctx,
                                   {"waiting_on": "compliance_review",
                                    "resolved": True}))
                self.hub.ingest_spoke_trace(
                    "03", env.envelope_id,
                    thought=f"compliance approved sequence content for "
                            f"ctx={ctx!r} - cleared to send",
                    result="sequence cleared")
                return
            if verdict == "flagged":
                if seq:
                    seq["compliance_status"] = "pending"
                    seq["paused"] = True
                findings = env.payload.get("findings", [])
                self.hub.ingest_spoke_trace(
                    "03", env.envelope_id,
                    thought=f"compliance flagged sequence content: "
                            f"{findings} - sequence stays paused, never "
                            f"sends flagged content",
                    result="sequence held: flagged")
                return
            self.hub.ingest_spoke_trace(
                "03", env.envelope_id,
                thought=f"unrecognized verdict {verdict!r} - 17's contract "
                        f"is only 'approved' or 'flagged'",
                result="held: unknown verdict type")
            return

        if env.intent == "data.package":
            listing_status = env.payload.get("listing_status")
            if listing_status == "changed":
                # tuple 9: stale property claims are fabrications - pull
                # the step rather than send with outdated status
                self.hub.ingest_spoke_trace(
                    "03", env.envelope_id,
                    thought="market-update package references a listing "
                            "whose status changed - stale property claims "
                            "are fabrications, pulling the step rather "
                            "than sending",
                    result="step pulled: stale listing status")
                return
            if env.payload.get("expired"):
                # tuple 3: expired market data -> regenerate or skip
                self.hub.ingest_spoke_trace(
                    "03", env.envelope_id,
                    thought="market data package is expired - regenerating "
                            "before send, or skipping this touch if a "
                            "fresh package isn't available in time",
                    result="skipped touch: expired data")
                return
            self.hub.ingest_spoke_trace(
                "03", env.envelope_id,
                thought="fresh, current-status market data package received "
                        "- clear to build the update touch",
                result="package accepted")
            return
