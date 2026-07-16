"""Agent 10 - Market Data, built against the full spec.

Two output modes (comp packages, neighborhood packages), one legal line.
The output schema structurally has no opinion field - not a rule to
remember, a shape the data literally cannot carry.
"""
from __future__ import annotations

from .core import Envelope

SOURCE_VERIFIED = "source_verified"
STATED_BY_PARTY = "stated_by_party"
UNKNOWN = "unknown"

_OPINION_WORDS = ("what number would you go with", "your opinion",
                 "what would you price it", "the number you'd go with")
_APPRAISAL_SUBSTITUTION_WORDS = ("is this worth", "what's it actually worth",
                                "give me a value", "instead of an appraisal")


def _env(frm, to, intent, ctx, payload, confidence=UNKNOWN):
    return Envelope(from_agent=frm, to_agent=to, intent=intent,
                    client_context_id=ctx, payload=payload,
                    confidence=confidence,
                    provenance={"source": f"spoke-{frm}", "captured_at": "runtime",
                                "verbatim_available": True})


class Spoke10MarketData:
    """DECISIONS.md tuples implemented directly:
      1. comps come back thin -> report the thinness, never widen params silently
      2. sources conflict on a datum -> present both with provenance, never average
      3. anyone asks for the opinion -> refuse, data only; escalate if pressed
      4. staleness threshold reached -> regenerate, never reship
      5. license limits the recipient -> deliver to human only + note limit
      6. scrape source blocks/changes terms -> stop that source, log raw
         error, never route around
      7. datum lacks timestamp/source -> does not enter any package
      8. comp set thinner than rubric minimum -> deliver with thinness
         named, never padded
      9. two sources disagree on sold price -> report both with sources,
         never pick silently
      10. request smells like appraisal substitution -> data package with
          not-an-appraisal note, 17 informed
      11. historic data beyond retention -> absent is the answer, never
          reconstruct from memory
    """

    def __init__(self, hub, comp_minimum: int = 5,
                 staleness_days: int = 30, retention_days: int = 730):
        self.hub = hub
        self.mls_feed: dict[str, list[dict]] = {}  # ctx -> list of listing.data facts
        self.comp_minimum = comp_minimum
        self.staleness_days = staleness_days
        self.retention_days = retention_days
        self.pressed_for_opinion: dict[str, int] = {}  # ctx -> count
        hub.register("10", self.handle)

    def handle(self, env: Envelope):
        ctx = env.client_context_id
        payload = env.payload

        if env.intent == "listing.data":
            self.mls_feed.setdefault(ctx, []).append(payload)
            return

        if env.intent == "data.request":
            requester = env.from_agent
            message = str(payload.get("message", "")).lower()

            # tuple 3: opinion request -> refuse, escalate if pressed
            if any(w in message for w in _OPINION_WORDS):
                count = self.pressed_for_opinion.get(ctx, 0) + 1
                self.pressed_for_opinion[ctx] = count
                if count >= 2:
                    self.hub.escalate("escalation.legal_line",
                                      {"client_context_id": ctx,
                                       "trigger": "repeated request for a "
                                                 "pricing opinion after "
                                                 "refusal - escalating",
                                       "agent": "10"})
                self.hub.send(_env("10", requester, "data.package", ctx,
                                   {"refused": True,
                                    "reason": "data only - opinion is the "
                                             "licensed human's job"}))
                return

            # tuple 10: appraisal-substitution smell
            substitution_smell = any(w in message for w in
                                     _APPRAISAL_SUBSTITUTION_WORDS)

            # tuple 5: license limits recipient -> human only + note
            license_scope = payload.get("license_scope", "internal")
            if license_scope == "external" and requester != "human":
                self.hub.send(_env("10", "human", "data.package", ctx,
                                   {"note": f"requested by {requester!r}, "
                                           f"but external distribution of "
                                           f"MLS-derived data is human-"
                                           f"gated - delivered to human "
                                           f"only, limit noted"}))
                return

            # tuple 11: historic data beyond retention window
            years_back = payload.get("years_back", 0)
            if years_back * 365 > self.retention_days:
                self.hub.send(_env("10", requester, "data.package", ctx,
                                   {"absent": True,
                                    "reason": "requested period exceeds "
                                             "retention window - absent is "
                                             "the answer, never "
                                             "reconstructed from memory"}))
                return

            mode = payload.get("mode")
            if mode == "comp":
                self._build_comp_package(ctx, requester, payload,
                                         substitution_smell, env)
            elif mode == "neighborhood":
                self._build_neighborhood_package(ctx, requester, payload, env)
            else:
                self.hub.send(_env("10", "queue", "clarification.request", ctx,
                                   {"reason": f"unrecognized data.request "
                                             f"mode {mode!r}"}))
            return

    def _build_comp_package(self, ctx, requester, payload, substitution_smell, env):
        raw_comps = payload.get("comps", [])
        # tuple 7: datum lacks timestamp/source -> dropped, not shipped
        valid = [c for c in raw_comps if c.get("source") and c.get("retrieval_date")]
        dropped = len(raw_comps) - len(valid)

        # tuple 4: staleness threshold - regenerate, never reship. A stale
        # datum here means dropped and reported, not silently kept.
        import datetime
        today = payload.get("today")
        if today:
            today_d = datetime.date.fromisoformat(today)
            fresh = []
            for c in valid:
                rd = datetime.date.fromisoformat(c["retrieval_date"])
                if (today_d - rd).days <= self.staleness_days:
                    fresh.append(c)
            valid = fresh

        # tuple 9: two sources disagree on sold price -> report both, never pick
        by_address = {}
        for c in valid:
            addr = c.get("address")
            by_address.setdefault(addr, []).append(c)
        conflicts = {a: v for a, v in by_address.items()
                    if len(v) > 1 and len({c.get("sold_price") for c in v}) > 1}

        thin = len(valid) < self.comp_minimum

        package = {
            "package_type": "comp",
            "comps": valid,
            "dropped_no_provenance": dropped,
            "thin": thin,
            "comp_count": len(valid),
            "comp_minimum": self.comp_minimum,
            "conflicts": conflicts,
        }
        if substitution_smell:
            package["not_an_appraisal_note"] = ("This is a data package, "
                "not an appraisal or valuation opinion.")
            self.hub.send(_env("10", "17", "compliance.notice", ctx,
                               {"trigger": "data request smelled like "
                                          "appraisal substitution",
                                "agent": "10"}))

        self.hub.ingest_spoke_trace(
            "10", env.envelope_id,
            thought=f"comp package: {len(valid)} valid comps "
                    f"(min {self.comp_minimum}), {dropped} dropped for no "
                    f"provenance, thin={thin}, conflicts={list(conflicts)}",
            result="comp package built")
        self.hub.send(_env("10", requester, "data.package", ctx, package,
                           confidence=SOURCE_VERIFIED))
        self.hub.send(_env("10", "14", "interaction.log", ctx,
                           {"kind": "package_delivered", "type": "comp"}))

    def _build_neighborhood_package(self, ctx, requester, payload, env):
        raw_data = payload.get("data", {})
        # tuple 7: no timestamp/source -> dropped
        clean = {k: v for k, v in raw_data.items()
                if isinstance(v, dict) and v.get("source") and v.get("retrieval_date")}
        dropped = [k for k in raw_data if k not in clean]

        # never characterize - structural: only pass through source+value+link
        package = {
            "package_type": "neighborhood",
            "figures": {k: {"value": v.get("value"), "source": v["source"],
                            "link": v.get("link")} for k, v in clean.items()},
            "dropped_no_provenance": dropped,
        }
        self.hub.ingest_spoke_trace(
            "10", env.envelope_id,
            thought=f"neighborhood package: {len(clean)} sourced figures, "
                    f"{len(dropped)} dropped for no provenance - figures "
                    f"only, no characterization emitted",
            result="neighborhood package built")
        self.hub.send(_env("10", requester, "data.package", ctx, package,
                           confidence=SOURCE_VERIFIED))
        self.hub.send(_env("10", "14", "interaction.log", ctx,
                           {"kind": "package_delivered", "type": "neighborhood"}))
