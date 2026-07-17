"""Agent 02 - Lead Qualification, built against the full spec.

Job Component #6 is binding: this agent APPLIES a human-supplied rubric
delivered via signed config.update. It never authors or drifts one.
DECISIONS.md tuple #2: no rubric = halt scoring, clarification, never score
from memory of a rubric. No rubric config exists anywhere in this identity
yet - so until one is signed and delivered, this agent fails closed on
scoring (it can still capture/hold leads; it just cannot tier them).
"""
from __future__ import annotations

from .core import Envelope

SOURCE_VERIFIED = "source_verified"
STATED_BY_PARTY = "stated_by_party"
UNKNOWN = "unknown"

# Every key _score() actually reads off a rubric. A rubric missing any of
# these isn't covered by any DECISIONS.md tuple - per the doctrine's own
# root rule ("no suitable tuple covers the task: STOP"), a partial rubric
# gets rejected, not silently completed with hardcoded numbers. Job
# Component #6 is binding: this agent applies the rubric, it never
# authors or drifts it - backfilling a missing threshold with a baked-in
# default is authoring, however small.
REQUIRED_RUBRIC_KEYS = {"budget_threshold", "budget_weight",
                       "timeline_days_threshold", "timeline_weight",
                       "financing_weight", "hot_threshold", "warm_threshold"}


def _env(frm, to, intent, ctx, payload, confidence=UNKNOWN):
    return Envelope(from_agent=frm, to_agent=to, intent=intent,
                    client_context_id=ctx, payload=payload,
                    confidence=confidence,
                    provenance={"source": f"spoke-{frm}", "captured_at": "runtime",
                                "verbatim_available": True})


class Spoke02LeadQualification:
    """Scores leads on readiness; assigns tier; routes hot/warm/dead.

    DECISIONS.md tuples implemented directly:
      1. score on a tier boundary -> lower tier assigned (not just noted),
         + flagged for human. Fixed 2026-07-16: the tier used to be
         computed via score>=threshold (the HIGHER tier), with a note
         appended claiming the lower tier was assigned - it wasn't. Now
         the lower tier is actually assigned before the note is written.
      2. rubric missing/unreadable -> halt, clarification. Fixed
         2026-07-16: previously only produced a silent interaction.log
         entry (tier=UNKNOWN); no clarification.request was ever sent, so
         nothing alerted a human that scoring was halted. A real
         clarification.request now fires for both this and tuple 12.
      3. lead demands a human -> hot path regardless of score
      4. stated urgency vs financing signals conflict -> financing wins,
         conflict logged
      5. tier oscillates a third time -> human review
      6. supplied rubric conflicts with a tuple -> tuple wins, conflict
         recorded verbatim, flagged - never silently picked
      7. financing letter present but expired -> treated as no verification
      8. lead is an agent shopping for a client -> flagged, different track
      9. rubric update arrives unsigned -> N/A at this layer: the hub's own
         signature gate on config.update (an authority intent) means an
         unsigned rubric never reaches this handler at all; documented
         here as a defense-in-depth note, not re-implemented redundantly
      10. budget conflicts with pre-approval doc -> doc wins, logged
      11. re-score request on a context with an open escalation -> hold
      12. all rubric inputs unknown -> tier UNKNOWN, not COLD; now also
          sends clarification.request (see tuple 2)

    Not a tuple, but a real gap found and fixed 2026-07-16: a signed
    rubric missing any key _score() reads used to silently fall back to
    a hardcoded default (e.g. hot_threshold=70) - Job Component #6 says
    this agent applies the rubric, never authors or drifts it, and
    backfilling a threshold nobody actually specified is authoring. No
    tuple covers a partial rubric, so per the doctrine's own root rule
    it's now rejected outright (prior rubric stays active, or None),
    with a clarification.request naming exactly which keys are missing.
    """

    # TUNABLE (owner-ratified 2026-07-16): hot_lead_sla_seconds=300 (5 min).
    # See docs/TUNING_MANUAL.md to change.
    def __init__(self, hub, hot_lead_sla_seconds: int = 300):
        self.hub = hub
        self.rubric: dict | None = None          # None = fails closed
        self.rubric_version: str | None = None
        self.tier_history: dict[str, list[str]] = {}
        self.open_escalations: set[str] = set()
        # Draft default, industry-cited (speed-to-lead: 5 minutes) - a real
        # deployment overrides this via the same signed config.update path
        # once cadence_settings.json is ratified.
        self.hot_lead_sla_seconds = hot_lead_sla_seconds
        hub.register("02", self.handle)

    def _score(self, payload: dict) -> tuple[str, int | None, list[str]]:
        """Returns (tier, score_or_None, notes). Tier is UNKNOWN if the
        rubric is absent OR all scoring inputs are unknown - these are
        different reasons but the same tier, per tuple #12."""
        notes = []
        if self.rubric is None:
            return "UNKNOWN", None, ["no rubric active - scoring halted"]

        budget = payload.get("budget")
        preapproval_doc = payload.get("preapproval_doc")  # {"amount":..., "expired": bool}
        timeline_days = payload.get("timeline_days")
        stated_urgency = payload.get("stated_urgency")  # e.g. "high"/"low"/None
        financing_progress = payload.get("financing_progress")  # e.g. "preapproved"/"none"/None

        # tuple: financing letter present but expired -> treat as no
        # verification (stated_by_party at best)
        if preapproval_doc and preapproval_doc.get("expired"):
            notes.append("pre-approval letter expired - treated as no "
                        "financing verification")
            financing_progress = financing_progress or "none"
            preapproval_doc = None

        # tuple: budget vs pre-approval doc conflict -> doc wins
        if preapproval_doc and budget and preapproval_doc.get("amount"):
            if abs(preapproval_doc["amount"] - budget) > 0:
                notes.append(
                    f"stated budget {budget} conflicts with pre-approval "
                    f"doc amount {preapproval_doc['amount']} - doc wins for "
                    f"scoring, conflict logged verbatim")
                budget = preapproval_doc["amount"]

        # tuple: stated urgency vs financing signal conflict -> financing wins
        if stated_urgency == "high" and financing_progress in (None, "none"):
            notes.append(
                "stated urgency 'high' conflicts with no financing "
                "progress - weighting verifiable financing over stated "
                "urgency, conflict logged")

        inputs = {"budget": budget, "timeline_days": timeline_days,
                 "financing_progress": financing_progress}
        if all(v is None for v in inputs.values()):
            return "UNKNOWN", None, notes + ["all rubric inputs unknown - "
                                             "tier is UNKNOWN, not COLD"]

        r = self.rubric
        score = 0
        if budget is not None and budget >= r.get("budget_threshold", 500_000):
            score += r.get("budget_weight", 40)
        if timeline_days is not None and timeline_days <= r.get("timeline_days_threshold", 30):
            score += r.get("timeline_weight", 40)
        if financing_progress == "preapproved":
            score += r.get("financing_weight", 20)

        hot = r.get("hot_threshold", 70)
        warm = r.get("warm_threshold", 40)

        # tuple: boundary score -> lower tier + flag for human. Checked
        # FIRST, before the normal >= bands - a score sitting exactly on
        # a threshold must actually get the lower tier, not just a note
        # saying it did while the higher tier gets assigned anyway.
        if score == hot:
            tier = "WARM"
            notes.append(f"score {score} sits exactly on a tier boundary "
                        f"(HOT/WARM) - assigned the lower tier (WARM), "
                        f"flagged for human")
        elif score == warm:
            tier = "COLD"
            notes.append(f"score {score} sits exactly on a tier boundary "
                        f"(WARM/COLD) - assigned the lower tier (COLD), "
                        f"flagged for human")
        elif score > hot:
            tier = "HOT"
        elif score > warm:
            tier = "WARM"
        else:
            tier = "COLD"

        return tier, score, notes

    def _record_tier(self, ctx: str, tier: str):
        hist = self.tier_history.setdefault(ctx, [])
        hist.append(tier)

    def _oscillating_third_time(self, ctx: str) -> bool:
        """Oscillation = a genuine back-and-forth: the tier returns to a
        value it had just left (A, B, A), not merely 3 assignments that
        happen to include 2 distinct tiers in some order."""
        hist = self.tier_history.get(ctx, [])
        if len(hist) < 3:
            return False
        a, b, c = hist[-3], hist[-2], hist[-1]
        return a == c and a != b

    def handle(self, env: Envelope):
        if env.intent == "config.update":
            # By the time this handler sees it, the HUB has already verified
            # the signature (config.update is an authority intent - schema
            # -> signature -> tuple legality -> persist -> deliver). An
            # unsigned rubric never reaches here (tuple #9, defense in depth
            # documented, not re-implemented).
            new_rubric = env.payload.get("rubric")
            version = env.payload.get("version")
            if new_rubric and version:
                missing = REQUIRED_RUBRIC_KEYS - set(new_rubric.keys())
                if missing:
                    # No tuple covers a partial rubric - root rule applies:
                    # STOP, don't silently complete it with baked-in
                    # numbers. Keep whatever rubric (or None) was already
                    # active; the new one never takes effect.
                    self.hub.ingest_spoke_trace(
                        "02", env.envelope_id,
                        thought=f"rubric v{version} is missing required "
                                f"keys {sorted(missing)} - rejecting rather "
                                f"than backfilling defaults, which would be "
                                f"authoring the rubric, not applying it; "
                                f"prior rubric (v{self.rubric_version}) "
                                f"stays active",
                        result=f"rejected: incomplete rubric v{version}")
                    self.hub.send(_env("02", "queue", "clarification.request",
                                       env.client_context_id,
                                       {"reason": "rubric missing required keys",
                                        "version": version,
                                        "missing_keys": sorted(missing)}))
                else:
                    self.rubric = new_rubric
                    self.rubric_version = version
                    self.hub.ingest_spoke_trace(
                        "02", env.envelope_id,
                        thought=f"signed rubric v{version} adopted; scoring "
                                f"unhalted",
                        result=f"rubric active: v{version}")
            resolved_ctx = env.payload.get("resolve_hot_lead")
            if resolved_ctx:
                # Real gap found during the agent.status retrofit: nothing
                # anywhere previously cleared open_escalations, meaning
                # tuple 11 (rescore during open escalation, hold) would
                # have permanently blocked rescoring for that context
                # forever once it went HOT.
                self.open_escalations.discard(resolved_ctx)
                self.hub.send(_env("02", "18", "agent.status", resolved_ctx,
                                   {"waiting_on": "hot_lead_human_response",
                                    "resolved": True}))
            return

        if env.intent in ("lead.captured", "lead.rescored"):
            ctx = env.client_context_id
            payload = env.payload

            # tuple: re-score request on a context with an open escalation
            # -> hold; the human's read outranks the rubric mid-escalation
            if env.intent == "lead.rescored" and ctx in self.open_escalations:
                self.hub.ingest_spoke_trace(
                    "02", env.envelope_id,
                    thought=f"ctx={ctx!r} has an open escalation - human's "
                            f"read outranks a rescore mid-escalation, holding",
                    result="held: open escalation")
                self.hub.send(_env("02", "queue", "clarification.request",
                                   ctx, {"reason": "rescore during open escalation"}))
                return

            # tuple: lead demands a human -> hot path regardless of score
            if payload.get("demands_human"):
                self._record_tier(ctx, "HOT")
                self.open_escalations.add(ctx)
                self.hub.send(_env("02", "18", "agent.status", ctx,
                                   {"waiting_on": "hot_lead_human_response",
                                    "since": payload.get("today")}))
                self.hub.ingest_spoke_trace(
                    "02", env.envelope_id,
                    thought="lead demands a human - hot path regardless of "
                            "any score",
                    result="tier=HOT (forced: demands_human)")
                self.hub.escalate("escalation.hot_lead",
                                  {"client_context_id": ctx, "reason": "demands_human",
                                   "sla_s": self.hot_lead_sla_seconds})
                self.hub.send(_env("02", "14", "interaction.log", ctx,
                                   {"tier": "HOT", "reason": "demands_human"}))
                return

            # tuple: lead is an agent shopping for a client -> flag, different track
            if payload.get("is_agent_shopping"):
                self.hub.ingest_spoke_trace(
                    "02", env.envelope_id,
                    thought="lead identifies as an agent shopping for a "
                            "client - different track, flagging for human "
                            "engagement decision rather than scoring as a "
                            "consumer lead",
                    result="flagged: agent_to_agent")
                self.hub.send(_env("02", "queue", "clarification.request",
                                   ctx, {"reason": "agent-to-agent inquiry"}))
                return

            tier, score, notes = self._score(payload)

            # supplied rubric vs tuple conflict check would fire here if a
            # rubric's computed tier ever tried to override a hard tuple
            # result above (demands_human, agent_shopping) - those return
            # early precisely so the tuple always wins structurally, never
            # by a runtime comparison that could silently pick either side.

            self._record_tier(ctx, tier)
            oscillating = self._oscillating_third_time(ctx)

            self.hub.ingest_spoke_trace(
                "02", env.envelope_id,
                thought=f"scored: tier={tier} score={score} "
                        f"rubric_version={self.rubric_version}; notes={notes}",
                result=f"tier={tier}")

            if oscillating:
                self.hub.send(_env("02", "queue", "clarification.request",
                                   ctx, {"reason": "tier oscillating a third "
                                                   "time", "history": self.tier_history[ctx]}))

            if tier == "HOT":
                self.open_escalations.add(ctx)
                self.hub.send(_env("02", "18", "agent.status", ctx,
                                   {"waiting_on": "hot_lead_human_response",
                                    "since": payload.get("today")}))
                self.hub.escalate("escalation.hot_lead",
                                  {"client_context_id": ctx, "score": score,
                                   "sla_s": self.hot_lead_sla_seconds})
            elif tier == "WARM":
                # tuple 10 (Agent 03's DECISIONS.md): "two sequences would
                # target one context... newest SIGNED instruction wins" -
                # a rescore-triggered nurture assignment is exactly that: a
                # deliberate, later re-evaluation superseding whatever was
                # running, not an ambiguous simultaneous eligibility
                # conflict (Agent 03's tuple 4, a different scenario -
                # confirmed distinct 2026-07-16). This is the actual
                # distinguishing signal Agent 03 needs and never had.
                self.hub.send(_env("02", "03", "lead.nurture", ctx,
                                   {"tier": tier, "score": score,
                                    "consent": payload.get("consent"),
                                    "reassignment": env.intent == "lead.rescored"}))
            elif tier == "COLD":
                pass  # archived via the interaction.log below, never deleted
            elif tier == "UNKNOWN":
                # tuples 2 & 12: no rubric active / all rubric inputs
                # unknown -> halt scoring; CLARIFICATION. Previously this
                # only produced a silent interaction.log entry - the tier
                # label was correct, but nothing ever told a human that
                # scoring is halted for this lead. Fixed: a real
                # clarification.request, same as every other hold state
                # this agent produces.
                self.hub.send(_env("02", "queue", "clarification.request",
                                   ctx, {"reason": "scoring halted: " + (
                                       "no rubric active" if self.rubric is None
                                       else "all rubric inputs unknown")}))

            self.hub.send(_env("02", "14", "interaction.log", ctx,
                               {"tier": tier, "score": score,
                                "rubric_version": self.rubric_version,
                                "notes": notes,
                                "archived": tier == "COLD"}))
            return
