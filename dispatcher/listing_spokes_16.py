"""Agent 16 - After-Close & Referral, built against the full spec.

Operates strictly from the human-supplied client list - the list is the
authority, never an inferred or assumed inclusion. Opt-out halts every
touch for that contact same-day. Referral incentives are always a
human/counsel decision, never this agent's call.
"""
from __future__ import annotations

from .core import Envelope

SOURCE_VERIFIED = "source_verified"
STATED_BY_PARTY = "stated_by_party"
UNKNOWN = "unknown"

_NEW_BUSINESS_WORDS = ("thinking of selling", "looking to buy again",
                      "want to list", "ready to move")
_PRICING_WORDS = ("what's my house worth", "what is my house worth",
                 "what would it sell for now")
_INCENTIVE_WORDS = ("gift card", "referral bonus", "pay you for",
                   "reward for referring")


def _env(frm, to, intent, ctx, payload, confidence=UNKNOWN):
    return Envelope(from_agent=frm, to_agent=to, intent=intent,
                    client_context_id=ctx, payload=payload,
                    confidence=confidence,
                    provenance={"source": f"spoke-{frm}", "captured_at": "runtime",
                                "verbatim_available": True})


class Spoke16AfterCloseReferral:
    """DECISIONS.md tuples implemented directly:
      1. adverse life event on record -> hold the touch, human decides
      2. opt-out mid-cycle -> halt ALL touches for that contact same-day.
         Fixed 2026-07-16: 4 of the 5 touch-generating paths (refi alerts,
         referral solicitations, review requests, post-close check-ins)
         silently swallowed a block with zero signal anywhere - only
         date.trigger produced any record. A human auditing why a contact
         never got a scheduled touch had nothing to look at. Every blocked
         touch now logs consistently via interaction.log, whatever the
         touch type or block reason (opt-out, adverse event, stale, or
         not on list).
      3. past client signals new business -> lead.captured to 02, never
         negotiate
      4. greeting requested for someone not on the supplied list -> refuse,
         the list is the authority
      5. referral gift/incentive idea -> escalation.legal_line
      6. anniversary touch during a known distress event -> suppress,
         human decides
      7. past client asks a new-transaction question -> new context opened,
         routed as new lead; old context stays closed
      8. referral reward mention requested -> 17 gate first
      9. contact bounced/disconnected -> mark stale, no skip-tracing,
         re-permission is a human choice
    """

    def __init__(self, hub):
        self.hub = hub
        # The supplied client list IS the authority (tuple 4) - a context
        # not in this dict is refused, never assumed eligible.
        self.client_list: dict[str, dict] = {}
        self.opted_out: set[str] = set()  # tuple 2 - halts everything, human-only to clear
        self.adverse_event: dict[str, bool] = {}  # tuple 1/6 - human-only to clear
        self.stale_contacts: set[str] = set()  # tuple 9
        self.closed_transactions: dict[str, dict] = {}
        self.checkins_sent: dict[str, set[int]] = {}  # ctx -> {30, 90, 365} sent
        hub.register("16", self.handle)

    def _touch_blocked(self, ctx: str) -> str | None:
        """Single shared gate every touch path runs through - returns a
        block reason or None. Centralizing this avoids the field-mapping-
        by-hand risk of each touch type re-implementing its own version
        of the same three checks slightly differently."""
        if ctx not in self.client_list:
            return "not_on_supplied_list"
        if ctx in self.opted_out:
            return "opted_out"
        if self.adverse_event.get(ctx):
            return "adverse_event"
        if ctx in self.stale_contacts:
            return "stale_contact"
        return None

    def _log_blocked_touch(self, ctx: str, touch_kind: str, block: str):
        """Was: only date.trigger produced any record when a touch was
        suppressed - refi alerts, referral solicitations, review requests,
        and post-close check-ins all silently swallowed a block with zero
        signal anywhere, regardless of reason. A human auditing why a
        contact never got their 90-day check-in, or why a refi alert never
        went out, had nothing to look at. Now every blocked touch logs
        consistently, whatever the touch type."""
        self.hub.send(_env("16", "14", "interaction.log", ctx,
                           {"kind": "touch_blocked", "touch_type": touch_kind,
                            "reason": block}))

    def check_post_close_milestones(self, ctx: str, today: str):
        """Job component (the agent's own FIRST listed one): execute
        check-ins at 30, 90, and 365 days post-close. Found completely
        unimplemented while drafting the cadence config - date.trigger
        handling, feedback caps, and referral/review solicitation were
        all built, but this specific, explicitly-named mechanism never
        was. Schedule-driven, matching the established pattern (check_
        deadlines/check_vendor_holdups/check_sla)."""
        import datetime
        record = self.closed_transactions.get(ctx)
        if record is None or not record.get("close_date"):
            return "no_close_date"
        close_d = datetime.date.fromisoformat(record["close_date"])
        today_d = datetime.date.fromisoformat(today)
        days_since = (today_d - close_d).days
        sent = self.checkins_sent.setdefault(ctx, set())

        due = None
        for milestone in (30, 90, 365):
            if days_since >= milestone and milestone not in sent:
                due = milestone
                break
        if due is None:
            return "none_due"

        block = self._touch_blocked(ctx)
        if block:
            self._log_blocked_touch(ctx, "post_close_checkin", block)
            return f"blocked:{block}"

        sent.add(due)
        self.hub.send(_env("16", "11", "client.message.request", ctx,
                           {"template": f"post_close_checkin_{due}day"}))
        self.hub.send(_env("16", "14", "interaction.log", ctx,
                           {"kind": "post_close_checkin_sent", "day": due}))
        return f"sent:{due}"

    def handle(self, env: Envelope):
        ctx = env.client_context_id
        payload = env.payload

        if env.intent == "transaction.closed":
            self.closed_transactions[ctx] = {"close_date": payload.get("close_date")}
            return

        if env.intent == "date.trigger":
            event_type = payload.get("event_type")
            block = self._touch_blocked(ctx)
            if block:
                # tuple 4: not on list -> refuse. tuple 1/6: adverse event
                # -> suppress, human decides. tuple 2: opted out -> halt.
                self.hub.ingest_spoke_trace(
                    "16", env.envelope_id,
                    thought=f"date.trigger {event_type!r} for ctx={ctx!r} "
                            f"blocked: {block} - no touch sent",
                    result=f"blocked: {block}")
                self._log_blocked_touch(ctx, "date_trigger_greeting", block)
                if block == "not_on_supplied_list":
                    self.hub.send(_env("16", "queue", "clarification.request",
                                       ctx, {"reason": "greeting requested for "
                                                       "a contact not on the "
                                                       "supplied list - "
                                                       "refused, the list is "
                                                       "the authority"}))
                return
            self.hub.send(_env("16", "11", "client.message.request", ctx,
                               {"template": f"{event_type}_greeting"}))
            self.hub.send(_env("16", "14", "interaction.log", ctx,
                               {"kind": "touch_sent", "event_type": event_type}))
            return

        if env.intent == "config.update":
            if "client_list_entry" in payload:
                entry = payload["client_list_entry"]
                self.client_list[ctx] = entry
                return
            if "opt_out" in payload:
                # tuple 2: halts ALL touches, same day, human-only to clear
                if payload["opt_out"]:
                    self.opted_out.add(ctx)
                else:
                    # explicit human reinstatement only - never automatic
                    self.opted_out.discard(ctx)
                return
            if "adverse_event" in payload:
                # tuple 1/6: human-only to set OR clear
                self.adverse_event[ctx] = bool(payload["adverse_event"])
                return
            if "contact_bounced" in payload:
                # tuple 9: mark stale, no skip-tracing - re-permission is
                # a human choice (does NOT auto-clear; a human must
                # explicitly re-add via client_list_entry after
                # re-establishing contact through their own channel)
                self.stale_contacts.add(ctx)
                return
            if "reinstate_contact" in payload:
                # Real gap found via recovery-path check: tuple 9 frames
                # re-permission as "a human choice" - implying a path
                # should exist - but nothing ever cleared stale_contacts.
                # Explicit human action only, never automatic.
                if payload["reinstate_contact"]:
                    self.stale_contacts.discard(ctx)
                return
            if "refi_rate_alert" in payload:
                rate = payload["refi_rate_alert"].get("rate")
                source = payload["refi_rate_alert"].get("source")
                if not source:
                    # provenance-carrying source only
                    self.hub.send(_env("16", "queue", "clarification.request",
                                       ctx, {"reason": "refi rate alert has "
                                                       "no provenance source "
                                                       "- not sent"}))
                    return
                block = self._touch_blocked(ctx)
                if block:
                    self._log_blocked_touch(ctx, "refi_rate_alert", block)
                    return
                # states the fact, never advice
                self.hub.send(_env("16", "11", "client.message.request", ctx,
                                   {"template": "refi_rate_fact",
                                    "rate": rate, "source": source},
                                   confidence=SOURCE_VERIFIED))
                return
            if "referral_solicitation_due" in payload:
                block = self._touch_blocked(ctx)
                if block:
                    self._log_blocked_touch(ctx, "referral_solicitation", block)
                    return
                self.hub.send(_env("16", "11", "client.message.request", ctx,
                                   {"template": "referral_solicitation"}))
                return
            if "review_request_due" in payload:
                block = self._touch_blocked(ctx)
                if block:
                    self._log_blocked_touch(ctx, "review_request", block)
                    return
                self.hub.send(_env("16", "11", "client.message.request", ctx,
                                   {"template": "review_request"}))
                return
            return

        if env.intent == "content.verdict":
            verdict = payload.get("verdict")
            self.hub.send(_env("16", "18", "agent.status", ctx,
                               {"waiting_on": "referral_reward_review",
                                "resolved": True}))
            self.hub.ingest_spoke_trace(
                "16", env.envelope_id,
                thought=f"compliance verdict on referral-reward mention: "
                        f"{verdict!r} - {'cleared' if verdict == 'approved' else 'not cleared, held'}",
                result=f"verdict: {verdict}")
            return

        if env.intent == "lead.reply":
            message = str(payload.get("message", "")).lower()

            if payload.get("mentions_referral_reward"):
                # tuple 8: referral reward mention -> 17 gate first.
                # Checked before the fuzzy incentive-word match below - an
                # explicit caller-supplied signal is more reliable than my
                # own keyword guess, and the two lists can genuinely
                # overlap (e.g. "reward for referring" matches both).
                self.hub.send(_env("16", "17", "content.review", ctx,
                                   {"reason": "referral reward mention - "
                                             "inducement rules vary by state"}))
                self.hub.send(_env("16", "18", "agent.status", ctx,
                                   {"waiting_on": "referral_reward_review",
                                    "since": payload.get("today")}))
                return

            if any(w in message for w in _INCENTIVE_WORDS):
                # tuple 5: referral gift/incentive idea -> Legal Line
                self.hub.escalate("escalation.legal_line",
                                  {"client_context_id": ctx,
                                   "trigger": payload.get("message"),
                                   "agent": "16"})
                return

            if any(w in message for w in _PRICING_WORDS):
                # pricing advice -> hand off
                self.hub.escalate("escalation.legal_line",
                                  {"client_context_id": ctx,
                                   "trigger": payload.get("message"),
                                   "agent": "16"})
                return

            if any(w in message for w in _NEW_BUSINESS_WORDS):
                # tuple 3/7: new business signal -> lead.captured to 02,
                # NEW context, old context stays closed - never negotiate
                new_ctx = payload.get("new_lead_context_id", f"{ctx}-new")
                self.hub.send(_env("16", "02", "lead.captured", new_ctx,
                                   {"name": payload.get("name"),
                                    "source": "past_client_referral_signal",
                                    "original_context": ctx},
                                   confidence=STATED_BY_PARTY))
                self.hub.send(_env("16", "14", "interaction.log", ctx,
                                   {"kind": "new_business_signal_routed",
                                    "new_context": new_ctx}))
                return
            return
