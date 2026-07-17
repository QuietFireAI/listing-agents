"""Agent 06 - Showing Scheduler, built against the full spec.

Schedules people; never authorizes access. Access requests of any kind
are an absolute Legal Line, not a judgment call.
"""
from __future__ import annotations

from .core import Envelope

SOURCE_VERIFIED = "source_verified"
STATED_BY_PARTY = "stated_by_party"
UNKNOWN = "unknown"

_ACCESS_REQUEST_WORDS = ("let them in", "access code", "lockbox combo",
                        "gate code", "just let", "combo for the")


def _env(frm, to, intent, ctx, payload, confidence=UNKNOWN):
    return Envelope(from_agent=frm, to_agent=to, intent=intent,
                    client_context_id=ctx, payload=payload,
                    confidence=confidence,
                    provenance={"source": f"spoke-{frm}", "captured_at": "runtime",
                                "verbatim_available": True})


class Spoke06ShowingScheduler:
    """DECISIONS.md tuples implemented directly:
      1. calendar double-booking -> protected deadline wins; otherwise
         first-confirmed wins, alternatives offered. Fixed 2026-07-16:
         "protected deadline wins" used to mean "ignore the conflict
         check and schedule anyway" - both showings ended up marked
         confirmed, and the bumped party got no notification at all. Now
         the displaced showing is actually removed and its party notified.
      2. requester identity cannot be verified -> cancel the access-bearing
         appointment, no exceptions
      3. 'just let them in' -> refuse + escalation.legal_line, access is
         the line
      4. no-show -> log + feedback request, no reproach messaging
      5. same-day request on occupied property -> minimum-notice rules
         still apply, occupancy never squeezed
      6. access code/lockbox combo requested in a message -> never
         transmit, routes through custody protocol only
      7. request inside occupied-home notice window -> offer first legal
         slot, never ask seller to waive notice unprompted
      8. agent no-shows twice -> flag pattern to human before third confirm
      9. overlapping showings -> sequence with buffer, never double-book
         and hope. Fixed 2026-07-16: the conflict check was exact-time-
         match only - buffer_minutes rode along in the calendar.event
         payload as pure data, never compared against anything. Two
         showings 15 minutes apart with buffer_minutes=30 produced zero
         conflict detection. Now a real buffer-aware overlap check.
      10. feedback unanswered after two asks -> stop asking
      11. request for a property under contract -> confirm show-ability
          via 05 first, never assume active

    Open question, not resolved here: tuple 1's "alternatives offered"
    and tuples 5/7's "offer first legal slot" send a template name and a
    supporting number (required_notice_hours) to 11, not a computed
    alternative time - this may be intentional division of labor (11's
    template layer computes the real slot) rather than a gap. Not
    changed without confirming which it is.
    """

    # TUNABLE (owner-ratified 2026-07-16): feedback_ask_cap=2,
    # no_show_pattern_threshold=2. See docs/TUNING_MANUAL.md to change.
    def __init__(self, hub, min_notice_hours: dict[str, int] | None = None,
                 feedback_ask_cap: int = 2, no_show_pattern_threshold: int = 2):
        self.hub = hub
        self.feedback_ask_cap = feedback_ask_cap
        self.no_show_pattern_threshold = no_show_pattern_threshold
        self.confirmed_showings: dict[str, list[dict]] = {}  # ctx -> showings
        self.showing_agent_no_shows: dict[str, int] = {}  # requester_id -> count
        self.feedback_asks: dict[str, int] = {}  # showing_id -> ask count
        self.pending_status_checks: dict[str, dict] = {}  # ctx -> held request
        # per-state or per-property minimum notice hours for occupied homes
        self.min_notice_hours = min_notice_hours or {"default": 24}
        hub.register("06", self.handle)

    def _access_request_hit(self, text: str) -> str | None:
        low = text.lower()
        for w in _ACCESS_REQUEST_WORDS:
            if w in low:
                return text.strip()
        return None

    def request_showing_feedback(self, ctx: str, today: str | None = None):
        """Tuple 10: feedback unanswered after two asks, stop asking. This
        was previously a declared dict (feedback_asks) that nothing ever
        read or incremented - the tuple was never actually implemented.
        Schedule-driven re-ask, matching the established pattern."""
        count = self.feedback_asks.get(ctx, 0)
        if count >= self.feedback_ask_cap:
            self.hub.ingest_spoke_trace(
                "06", "internal", thought=f"feedback unanswered after "
                f"{count} asks for ctx={ctx!r} - stopping, per tuple 10",
                result="stopped: ask_cap_reached")
            self.hub.send(_env("06", "18", "agent.status", ctx,
                               {"waiting_on": "showing_feedback",
                                "resolved": True}))
            return "stopped"
        self.feedback_asks[ctx] = count + 1
        self.hub.send(_env("06", "11", "client.message.request", ctx,
                           {"template": "feedback_request",
                            "tone": "neutral_no_reproach"}))
        if count == 0 and today:
            self.hub.send(_env("06", "18", "agent.status", ctx,
                               {"waiting_on": "showing_feedback",
                                "since": today}))
        return "asked"

    def handle(self, env: Envelope):
        ctx = env.client_context_id

        if env.intent == "vendor.cancellation_notice":
            # A vendor relevant to this context cancelled late (09's
            # direct, immediate signal - tuple 1 on 09's side). This
            # agent has no existing state linking a specific vendor to a
            # specific confirmed showing, so it can't automatically judge
            # which showing (if any) is affected - flagging for human
            # review rather than guessing, matching the ambiguity
            # protocol (section 6).
            self.hub.send(_env("06", "14", "interaction.log", ctx,
                               {"kind": "vendor_cancellation_received",
                                "vendor_kind": env.payload.get("vendor_kind")}))
            self.hub.send(_env("06", "queue", "clarification.request", ctx,
                               {"reason": f"vendor "
                                         f"{env.payload.get('vendor_kind')!r} "
                                         f"cancelled late - human should "
                                         f"check whether a confirmed showing "
                                         f"depends on this vendor's work"}))
            return

        if env.intent == "showing.request":
            payload = env.payload

            trigger = self._access_request_hit(str(payload.get("message", "")))
            if trigger:
                self.hub.ingest_spoke_trace(
                    "06", env.envelope_id,
                    thought=f"request contains an access request: "
                            f"{trigger!r} - access is the legal line, not "
                            f"a judgment call, refusing and escalating",
                    result="escalated: legal_line (access request)")
                self.hub.escalate("escalation.legal_line",
                                  {"client_context_id": ctx,
                                   "trigger": trigger, "agent": "06"})
                return

            # tuple 6: access code/lockbox combo requested -> never
            # transmit, regardless of who's asking
            if payload.get("requests_access_code"):
                self.hub.escalate("escalation.legal_line",
                                  {"client_context_id": ctx,
                                   "trigger": "access code/lockbox combo "
                                             "requested - never transmitted, "
                                             "custody protocol only",
                                   "agent": "06"})
                return

            # job component: buyer-agreement-on-file flag (set by 13),
            # absent = hold and escalate (NAR 8/17/24 rule)
            if not payload.get("buyer_agreement_on_file"):
                self.hub.ingest_spoke_trace(
                    "06", env.envelope_id,
                    thought="buyer-agreement-on-file flag absent - written "
                            "buyer agreement is required before touring "
                            "(NAR settlement, 8/17/24); holding, not "
                            "scheduling",
                    result="held: no buyer agreement on file")
                self.hub.escalate("escalation.legal_line",
                                  {"client_context_id": ctx,
                                   "trigger": "showing requested without "
                                             "buyer-agreement-on-file flag",
                                   "agent": "06"})
                return

            # job component: verify requester identity per configured
            # procedure before confirming any access-bearing appointment
            if not payload.get("requester_identity_verified"):
                # tuple 2: cannot verify -> cancel the appointment, no
                # exceptions (not merely hold - cancel outright)
                self.hub.ingest_spoke_trace(
                    "06", env.envelope_id,
                    thought="requester identity could not be verified - "
                            "cancelling the access-bearing appointment, no "
                            "exceptions (vacant-property fraud pattern)",
                    result="cancelled: identity unverified")
                self.hub.send(_env("06", "14", "interaction.log", ctx,
                                   {"kind": "showing_cancelled",
                                    "reason": "identity_unverified"}))
                return

            # tuple 11: property under contract -> confirm show-ability via
            # 05 first, never assume active. Hold this request pending the
            # status.response round trip.
            if payload.get("possibly_under_contract"):
                self.pending_status_checks[ctx] = payload
                self.hub.ingest_spoke_trace(
                    "06", env.envelope_id,
                    thought="property may be under contract - confirming "
                            "show-ability via MLS (05) before scheduling, "
                            "never assuming active",
                    result="status.request issued, holding")
                self.hub.send(_env("06", "05", "status.request", ctx, {}))
                return

            self._schedule(ctx, payload, env)
            return

        if env.intent == "status.response":
            payload = self.pending_status_checks.pop(ctx, None)
            if payload is None:
                return
            status = env.payload.get("status")
            if status not in ("active",):
                self.hub.ingest_spoke_trace(
                    "06", env.envelope_id,
                    thought=f"MLS status is {status!r}, not active - "
                            f"show-ability not confirmed, holding and "
                            f"escalating rather than scheduling",
                    result="held: not show-able")
                self.hub.send(_env("06", "queue", "clarification.request", ctx,
                                   {"reason": f"property status {status!r} "
                                             f"is not show-able"}))
                return
            self._schedule(ctx, payload, env)
            return

        if env.intent == "showing.feedback_response":
            self.feedback_asks.pop(ctx, None)
            self.hub.send(_env("06", "18", "agent.status", ctx,
                               {"waiting_on": "showing_feedback",
                                "resolved": True}))
            self.hub.send(_env("06", "14", "interaction.log", ctx,
                               {"kind": "feedback_received",
                                "response": env.payload.get("response")}))
            return

        if env.intent == "showing.no_show":
            agent_id = env.payload.get("showing_agent_id")
            was_buyer_agent = env.payload.get("is_agent_no_show", False)
            # tuple 4: no-show -> log + feedback request, NO reproach messaging
            self.hub.send(_env("06", "14", "interaction.log", ctx,
                               {"kind": "no_show"}))
            self.request_showing_feedback(ctx, env.payload.get("today"))
            if was_buyer_agent and agent_id:
                self.showing_agent_no_shows[agent_id] = \
                    self.showing_agent_no_shows.get(agent_id, 0) + 1
                # tuple 8: agent no-shows twice -> flag pattern before third
                if self.showing_agent_no_shows[agent_id] >= self.no_show_pattern_threshold:
                    self.hub.send(_env("06", "queue", "clarification.request",
                                       ctx, {"reason": f"showing agent "
                                                       f"{agent_id!r} has "
                                                       f"{self.showing_agent_no_shows[agent_id]} "
                                                       f"no-shows - human review "
                                                       f"before third confirmation"}))
            return

    def _time_conflict(self, existing_time_str, requested_time_str,
                       buffer_minutes) -> bool:
        """Was: exact-match only (s.get('time') == requested_time), so
        buffer_minutes rode along in the calendar.event payload as pure
        data - nothing ever compared it against anything. Two showings
        15 minutes apart with buffer_minutes=30 produced zero conflict
        detection. Fixed: real buffer-aware overlap check."""
        import datetime
        if not existing_time_str or not requested_time_str:
            return False
        try:
            existing_dt = datetime.datetime.fromisoformat(existing_time_str)
            requested_dt = datetime.datetime.fromisoformat(requested_time_str)
        except (ValueError, TypeError):
            # unparseable - fail toward the exact-match check rather than
            # silently treating an unparseable time as never conflicting
            return existing_time_str == requested_time_str
        delta_minutes = abs((requested_dt - existing_dt).total_seconds()) / 60
        return delta_minutes < buffer_minutes

    def _schedule(self, ctx: str, payload: dict, env: Envelope):
        # tuple 5/7: minimum-notice rules for occupied properties always
        # apply, never squeezed for same-day requests
        if payload.get("property_occupied"):
            requested_hours_notice = payload.get("hours_notice", 0)
            required = self.min_notice_hours.get(
                payload.get("state"), self.min_notice_hours["default"])
            if requested_hours_notice < required:
                self.hub.ingest_spoke_trace(
                    "06", env.envelope_id,
                    thought=f"occupied property requires {required}h "
                            f"minimum notice, request gives "
                            f"{requested_hours_notice}h - offering first "
                            f"legal slot instead, never asking seller to "
                            f"waive notice unprompted",
                    result="offered first legal slot")
                self.hub.send(_env("06", "11", "client.message.request", ctx,
                                   {"template": "next_legal_slot_offer",
                                    "required_notice_hours": required}))
                return

        existing = self.confirmed_showings.setdefault(ctx, [])
        # tuple 9: overlapping showings -> sequence with buffer, never
        # double-book and hope
        requested_time = payload.get("requested_time")
        buffer_minutes = payload.get("buffer_minutes", 30)
        conflicting = [s for s in existing
                      if self._time_conflict(s.get("time"), requested_time,
                                             buffer_minutes)]
        if conflicting:
            protected = payload.get("protected_deadline", False)
            if not protected:
                self.hub.send(_env("06", "queue", "clarification.request", ctx,
                                   {"reason": "calendar conflict within "
                                             f"{buffer_minutes}min buffer - "
                                             "sequencing required",
                                    "conflicting_times": [s["time"] for s in conflicting]}))
                return
            # "protected deadline wins" must actually RESOLVE the
            # collision, not just ignore the check and schedule over it.
            # Was: both showings ended up "confirmed" in state, and the
            # bumped party got no cancellation, no notification, nothing
            # - a real double-booking, contradicting this same tuple's
            # own "never double-book and hope". Fixed: the bumped
            # showing(s) are actually removed and the affected party is
            # notified.
            for bumped in conflicting:
                existing.remove(bumped)
                self.hub.ingest_spoke_trace(
                    "06", env.envelope_id,
                    thought=f"protected-deadline request at {requested_time!r} "
                            f"bumps existing showing at {bumped['time']!r} - "
                            f"removing it and notifying the affected party, "
                            f"not leaving both marked confirmed",
                    result=f"bumped: {bumped['time']!r}")
                self.hub.send(_env("06", "11", "client.message.request", ctx,
                                   {"template": "showing_bumped_notice",
                                    "original_time": bumped["time"],
                                    "reason": "protected_deadline_priority"}))
                self.hub.send(_env("06", "14", "interaction.log", ctx,
                                   {"kind": "showing_bumped",
                                    "time": bumped["time"]}))

        self.hub.send(_env("06", "18", "calendar.event", ctx,
                           {"event": "showing", "time": requested_time,
                            "buffer_minutes": buffer_minutes}))

        # job component: open house RSVP logistics - an open house (unlike
        # an individual buyer showing) needs event vendor support
        # (signage/staging), ordered via 09.
        if payload.get("is_open_house"):
            self.hub.send(_env("06", "09", "vendor.request", ctx,
                               {"kind": "open_house_signage"}))

        showing = {"time": requested_time, "confirmed": True}
        existing.append(showing)
        self.hub.ingest_spoke_trace(
            "06", env.envelope_id,
            thought="all gates cleared (buyer agreement on file, identity "
                    "verified, show-ability confirmed, no unresolved "
                    "conflict) - confirming showing",
            result="showing confirmed")
        self.hub.send(_env("06", "11", "client.message.request", ctx,
                           {"template": "showing_confirmation"}))
        self.hub.send(_env("06", "14", "interaction.log", ctx,
                           {"kind": "showing_confirmed", "time": requested_time}))
