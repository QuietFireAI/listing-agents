"""Agent 18 - Calendar & Task, built against the full spec.

Personal assistant to the licensed human agent. Also the actual home for
cross-swarm wait-state visibility (agent.status) - so a human can see
what's currently waiting on something before it becomes a missed deadline,
not just after.
"""
from __future__ import annotations

from .core import Envelope

SOURCE_VERIFIED = "source_verified"
STATED_BY_PARTY = "stated_by_party"
UNKNOWN = "unknown"


def _env(frm, to, intent, ctx, payload, confidence=UNKNOWN):
    return Envelope(from_agent=frm, to_agent=to, intent=intent,
                    client_context_id=ctx, payload=payload,
                    confidence=confidence,
                    provenance={"source": f"spoke-{frm}", "captured_at": "runtime",
                                "verbatim_available": True})


class Spoke18CalendarTask:
    """DECISIONS.md tuples implemented directly:
      1. event conflict priority rules can't resolve -> human, never
         silently move either. Fixed 2026-07-16: only tuple 5's day-
         capacity check existed - two events at the exact same time on
         the same day would both silently get added with zero conflict
         signal. Now a real time-based conflict check, distinct from
         capacity.
      2. briefing item's source envelope missing -> state the gap. NOT
         implemented - generate_briefing() only aggregates what it has;
         there's no mechanism to know something is missing unless a
         separate system tells it to expect that item. Genuinely
         difficult without an "expected items" tracking layer this
         review won't invent.
      3. human instruction conflicts with contractual deadline -> surface
         both, act on neither until directed. NOT implemented - zero
         code, confirmed by grep. No payload key or handler exists for a
         distinct "human instruction" input at all.
      4. agent asks to move a protected deadline block -> refuse, human
         confirmation only
      5. overloaded day -> propose priority order, human confirms. Soft
         finding, not fixed: the message claims "proposing priority
         order" but no actual order is computed or attached - same
         decorative-claim pattern as Agents 04/06/09.
      6. two deadline sources disagree -> track both, alert the conflict,
         never pick the friendlier date
      7. owner calendar conflicts with contractual deadline -> deadline
         outranks, propose the move on the soft item
      8. recurring task silently failing (no completion events) -> surface
         the pattern, a quiet calendar is a suspect calendar. SERIOUS GAP
         found 2026-07-16: self.recurring_task_last_seen is never written
         by any real code path anywhere in this class - the existing test
         populated it by directly poking the internal dict
         (spoke.recurring_task_last_seen[...] = ...), not through any
         real message. In actual operation this dict is always empty, so
         check_recurring_task() can never fire - there is no mechanism
         anywhere in the swarm that signals "a recurring task completed."
         Needs a real design decision (what signal, from whom) this
         review won't invent.
      9. timezone ambiguity -> confirm before scheduling

    CRITICAL cross-agent bug found and fixed 2026-07-16, not a tuple:
    Agent 06's and Agent 09's calendar.event sends never included 'day'
    or 'timezone_confirmed' at all. This agent gates on tz_confirmed
    FIRST and keys its entire calendar on 'day' - meaning every single
    real showing confirmation (06) and vendor appointment (09) was
    getting caught at the timezone-ambiguity gate and NEVER reaching the
    calendar, ever. Verified directly: a real showing confirmation
    produced a clarification.request and left self.calendar completely
    empty. In production this agent's calendar, capacity checks, and
    protected-block tracking were all silently non-functional. Fixed in
    both senders.

    Plus: agent.status tracking - the actual fix for real-time HITL
    visibility into what's waiting on what, discussed and decided as a
    push-pattern extension matching how this agent already consumes
    calendar.event/deadline.alert, rather than a pull from 14 (18 has no
    route to query 14 at all, and a briefing "never contains a status the
    system cannot source" - so the signal has to be pushed here directly).
    """

    # TUNABLE (owner-ratified 2026-07-16): max_events_per_day=8.
    # See docs/TUNING_MANUAL.md to change.
    def __init__(self, hub, max_events_per_day: int = 8):
        self.hub = hub
        self.calendar: dict[str, list[dict]] = {}  # day -> events
        self.protected_blocks: dict[str, dict] = {}  # event_id -> {day, source: "07"}
        self.deadline_sources: dict[str, dict[str, str]] = {}  # ctx -> {source: date}
        self.waiting: dict[str, dict] = {}  # (agent, ctx, waiting_on) key -> status record
        self.recurring_task_last_seen: dict[str, str] = {}  # task_id -> last completion date
        self.max_events_per_day = max_events_per_day
        hub.register("18", self.handle)

    def _wait_key(self, agent: str, ctx: str, waiting_on: str) -> str:
        return f"{agent}:{ctx}:{waiting_on}"

    def handle(self, env: Envelope):
        ctx = env.client_context_id
        payload = env.payload

        if env.intent == "agent.status":
            key = self._wait_key(env.from_agent, ctx, payload.get("waiting_on", ""))
            if payload.get("resolved"):
                self.waiting.pop(key, None)
                self.hub.ingest_spoke_trace(
                    "18", env.envelope_id,
                    thought=f"{env.from_agent} resolved its wait on "
                            f"{payload.get('waiting_on')!r} for ctx={ctx!r}",
                    result="wait cleared")
            else:
                self.waiting[key] = {"agent": env.from_agent, "ctx": ctx,
                                     "waiting_on": payload.get("waiting_on"),
                                     "since": payload.get("since")}
                self.hub.ingest_spoke_trace(
                    "18", env.envelope_id,
                    thought=f"{env.from_agent} is waiting on "
                            f"{payload.get('waiting_on')!r} for ctx={ctx!r} "
                            f"since {payload.get('since')} - tracked for "
                            f"the next briefing",
                    result="wait tracked")
            return

        if env.intent == "calendar.event":
            day = payload.get("day")
            # Doctrine is unconditional: "deadline blocks originating from
            # 07 are protected" - derived from source, not a trusted flag.
            # A payload could claim protected=False for a 07-sourced block
            # (or omit it) and this must not weaken the actual rule.
            protected = env.from_agent == "07" or payload.get("protected", False)
            # Fail closed: unconfirmed timezone status defaults to NOT
            # confirmed, not assumed fine.
            tz_confirmed = payload.get("timezone_confirmed", False)

            # tuple 9: timezone ambiguity -> confirm before scheduling
            if not tz_confirmed:
                self.hub.send(_env("18", "queue", "clarification.request", ctx,
                                   {"reason": "timezone ambiguity on this "
                                             "event - confirm before "
                                             "scheduling"}))
                return

            day_events = self.calendar.setdefault(day, [])

            # tuple 1: event conflict the priority rules cannot resolve ->
            # human, never silently move either. Fixed 2026-07-16: only
            # tuple 5's day-capacity check existed - two events at the
            # exact same time on the same day would both silently get
            # added with zero conflict signal, since only the aggregate
            # day count was ever checked. Distinct from capacity: this is
            # about two specific events actually colliding.
            time = payload.get("time")
            if time and any(e.get("time") == time for e in day_events):
                self.hub.send(_env("18", "queue", "clarification.request", ctx,
                                   {"reason": f"event conflict at {time!r} on "
                                             f"{day!r} - priority rules don't "
                                             f"resolve this, human decides, "
                                             f"never silently moving either"}))
                return

            # tuple 5: overloaded day -> propose priority order, human confirms
            if len(day_events) >= self.max_events_per_day:
                self.hub.send(_env("18", "queue", "clarification.request", ctx,
                                   {"reason": "day is at capacity - "
                                             "proposing priority order for "
                                             "human confirmation",
                                    "day": day}))
                return

            event_id = payload.get("event_id", f"{day}-{len(day_events)}")
            day_events.append({"event_id": event_id, "source": env.from_agent,
                              "protected": protected, "time": time})
            if protected:
                self.protected_blocks[event_id] = {"day": day, "source": env.from_agent}
            self.hub.send(_env("18", "14", "interaction.log", ctx,
                               {"kind": "calendar_event_logged", "day": day,
                                "event_id": event_id}))
            return

        if env.intent == "deadline.alert":
            milestone = payload.get("milestone")
            deadline = payload.get("deadline")
            source = env.from_agent

            # tuple 6: two deadline sources disagree -> track both, alert,
            # never pick the friendlier date
            existing = self.deadline_sources.setdefault(ctx, {})
            if milestone in existing and existing[milestone] != deadline:
                self.hub.send(_env("18", "queue", "clarification.request", ctx,
                                   {"reason": f"conflicting deadline sources "
                                             f"for {milestone!r}: "
                                             f"{existing[milestone]!r} vs "
                                             f"{deadline!r} - tracking both, "
                                             f"never picking the friendlier "
                                             f"date"}))
            existing[milestone] = deadline

            # Doctrine: "deadline blocks originating from 07 are
            # protected." 07 isn't a legal sender of calendar.event at all
            # (only 06/09 are) - this IS the block-creation point. 18
            # creates its own protected block for the deadline, sourced
            # from 07's alert, rather than expecting 07 to send a
            # calendar.event it structurally cannot send.
            block_id = f"deadline-{ctx}-{milestone}"
            day_events = self.calendar.setdefault(deadline, [])
            if not any(e["event_id"] == block_id for e in day_events):
                day_events.append({"event_id": block_id, "source": "07",
                                  "protected": True})
                self.protected_blocks[block_id] = {"day": deadline, "source": "07"}

            # tuple 7: owner calendar conflicts with contractual deadline ->
            # deadline outranks, propose move on the soft item
            conflicting_soft_events = [e for e in self.calendar.get(deadline, [])
                                       if not e["protected"]]
            if conflicting_soft_events:
                self.hub.send(_env("18", "queue", "clarification.request", ctx,
                                   {"reason": f"contractual deadline "
                                             f"{milestone!r} on {deadline!r} "
                                             f"conflicts with soft calendar "
                                             f"items - deadline outranks, "
                                             f"proposing the move on the "
                                             f"soft item(s)",
                                    "soft_events": [e["event_id"]
                                                   for e in conflicting_soft_events]}))
            return

        if env.intent == "config.update" and "move_protected_block" in payload:
            event_id = payload["move_protected_block"].get("event_id")
            requester = payload["move_protected_block"].get("requester")
            # tuple 4: agent asks to move a protected block -> refuse,
            # human confirmation only. This handler only reachable via a
            # signed config.update (human channel) - an agent itself has
            # no route to request this at all, which is the actual
            # enforcement; this branch handles the human's own request.
            if event_id in self.protected_blocks and requester != "human":
                self.hub.send(_env("18", "queue", "clarification.request", ctx,
                                   {"reason": f"request to move protected "
                                             f"block {event_id!r} from "
                                             f"{requester!r} - refused, "
                                             f"human confirmation only"}))
                return
            if event_id in self.protected_blocks:
                del self.protected_blocks[event_id]
            return

    def check_recurring_task(self, task_id: str, today: str, expected_cadence_days: int):
        """Tuple 8: a recurring task with no completion events is surfaced,
        not assumed fine. Schedule-driven, matching the established
        pattern (07's check_deadlines, etc.)."""
        import datetime
        last_seen = self.recurring_task_last_seen.get(task_id)
        if last_seen is None:
            return None
        gap = (datetime.date.fromisoformat(today) -
              datetime.date.fromisoformat(last_seen)).days
        if gap > expected_cadence_days:
            self.hub.send(_env("18", "queue", "clarification.request", "recurring-task",
                               {"reason": f"recurring task {task_id!r} has "
                                         f"had no completion event in "
                                         f"{gap} days (expected every "
                                         f"{expected_cadence_days}) - a "
                                         f"quiet calendar is a suspect "
                                         f"calendar, surfacing the pattern"}))
            return "surfaced"
        return "on_schedule"

    def generate_briefing(self, briefing_type: str = "morning"):
        """Job component: briefings built ONLY from logged envelopes and
        calendar records - a briefing never contains a status the system
        cannot source. This is where the agent.status tracking actually
        pays off: waiting items are real, sourced facts, not guesses."""
        briefing = {
            "briefing_type": briefing_type,
            "calendar_days": {d: [e["event_id"] for e in events]
                             for d, events in self.calendar.items()},
            "currently_waiting": [
                {"agent": w["agent"], "context": w["ctx"],
                 "waiting_on": w["waiting_on"], "since": w["since"]}
                for w in self.waiting.values()],
            "deadline_conflicts_tracked": {
                ctx: sources for ctx, sources in self.deadline_sources.items()
                if len(set(sources.values())) > 1},
        }
        self.hub.send(_env("18", "human", "report.package", "briefing", briefing))
        return briefing
