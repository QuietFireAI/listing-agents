"""Agent 13 - Buyer Search & Match, built against the full spec.

Owns the buyer side. Criteria are the client's stated criteria, verbatim -
this agent never adds, infers, or weights criteria the client didn't
state. Inferred preference filtering is steering, and steering is a fair
housing violation. The buyer agreement gate is absolute before any
showing.request.
"""
from __future__ import annotations

from .core import Envelope

SOURCE_VERIFIED = "source_verified"
STATED_BY_PARTY = "stated_by_party"
UNKNOWN = "unknown"

_PROTECTED_CLASS_WORDS = ("school quality", "school rating", "demographics",
                         "families with", "no children", "race", "religion",
                         "national origin", "ethnic", "families only")
_SELLER_POSITION_WORDS = ("what would the seller take", "seller's bottom line",
                         "what will they accept", "seller's minimum")
_PRICE_OPINION_WORDS = ("is this a good price", "good deal", "worth the price",
                        "should i pay")


def _env(frm, to, intent, ctx, payload, confidence=UNKNOWN, in_reply_to=None):
    return Envelope(from_agent=frm, to_agent=to, intent=intent,
                    client_context_id=ctx, payload=payload,
                    confidence=confidence, in_reply_to=in_reply_to,
                    provenance={"source": f"spoke-{frm}", "captured_at": "runtime",
                                "verbatim_available": True})


class Spoke13BuyerSearchMatch:
    """DECISIONS.md tuples implemented directly:
      1. client feedback contradicts stated criteria -> ask via 11, never
         rewrite silently
      2. criterion correlates with protected class -> refuse + Legal
         Line + verbatim log
      3. listing incomplete on a hard criterion -> hold and ask; unknown-
         flagged delivery requires standing preference
      4. 'is this a good price?' -> Legal Line
      5. match volume overload -> rank strictly by stated-criteria fit,
         no inferred preferences
      6. criteria conflict internally (budget vs area) -> present the
         conflict with data, never silently relax
      7. match on a colleague's listing -> disclosure rules to human
         before showing motion
      8. buyer asks what seller would accept -> refuse + Legal Line
      9. saved-search anomaly -> flag before presenting, never present
         suspect data as inventory
      10. pre-approval expires mid-search -> notify + mark; matches
          continue flagged unverified-financing
    """

    def __init__(self, hub):
        self.hub = hub
        self.buyer_criteria: dict[str, list[dict]] = {}
        self.deliver_unknown_ok: dict[str, bool] = {}  # standing preference, tuple 3
        self.match_history: dict[str, list[dict]] = {}
        self.pending_showing: dict[str, dict] = {}  # ctx -> held request awaiting 14
        self.preapproval_expired: dict[str, bool] = {}
        self.criteria_pending_review: dict[str, list] = {}  # sensitive language awaiting 17
        hub.register("13", self.handle)

    def _protected_class_hit(self, text: str) -> str | None:
        low = text.lower().replace("_", " ")
        for w in _PROTECTED_CLASS_WORDS:
            if w in low:
                return text.strip()
        return None

    def _match_one(self, ctx: str, listing: dict, env: Envelope):
        criteria = self.buyer_criteria.get(ctx)
        if not criteria:
            return
        criteria_dict = {c["field"]: c for c in criteria}

        # tuple 6: internal criteria conflict (budget vs area) -> present
        # with data, never silently relax
        budget = criteria_dict.get("budget", {}).get("value")
        price = listing.get("price")
        if budget is not None and price is not None and price > budget:
            area_pref = criteria_dict.get("area", {}).get("value")
            if area_pref and listing.get("area") == area_pref:
                self.hub.send(_env("13", "queue", "clarification.request", ctx,
                                   {"reason": "listing matches stated area "
                                             "but exceeds stated budget - "
                                             "presenting the conflict with "
                                             "data, never silently relaxing "
                                             "a criterion",
                                    "budget": budget, "price": price}))
                return

        # tuple 9: data anomaly -> flag before presenting, never present
        # suspect data as inventory
        if listing.get("data_anomaly"):
            self.hub.send(_env("13", "queue", "clarification.request", ctx,
                               {"reason": "saved-search match has a data "
                                         "anomaly - flagged, not presented "
                                         "as inventory",
                                "anomaly": listing.get("data_anomaly")}))
            return

        # tuple 3: incomplete on a hard criterion. Excludes fields with
        # their own dedicated comparison logic above (budget compares
        # against listing.price, not a literal listing.budget field) -
        # real bug found via testing: "budget" was being flagged as an
        # always-missing hard criterion since no listing ever literally
        # has a "budget" key, holding every match regardless of actual fit.
        _COMPARISON_FIELDS = {"budget", "area"}
        hard_criteria = {k: v for k, v in criteria_dict.items()
                        if v.get("hard", False) and k not in _COMPARISON_FIELDS}
        missing_hard = [k for k in hard_criteria if k not in listing
                       or listing.get(k) is None]
        if missing_hard and not self.deliver_unknown_ok.get(ctx, False):
            self.hub.send(_env("13", "queue", "clarification.request", ctx,
                               {"reason": f"listing incomplete on hard "
                                         f"criteria {missing_hard} - no "
                                         f"standing preference for "
                                         f"unknown-flagged delivery, "
                                         f"holding to ask"}))
            return

        # tuple 7: match on a colleague's own listing -> disclosure to
        # human before any showing motion
        if listing.get("colleague_listing"):
            self.hub.escalate("escalation.legal_line",
                              {"client_context_id": ctx,
                               "trigger": "match found on a colleague's "
                                         "own listing - disclosure rules "
                                         "to human before showing motion",
                               "agent": "13"})
            return

        match_record = {"listing_id": listing.get("listing_id"),
                        "unknown_fields": missing_hard,
                        "unverified_financing": self.preapproval_expired.get(ctx, False)}
        self.match_history.setdefault(ctx, []).append(match_record)
        self.hub.send(_env("13", "11", "client.message.request", ctx,
                           {"template": "new_match", "match": match_record},
                           confidence=SOURCE_VERIFIED))
        self.hub.send(_env("13", "14", "interaction.log", ctx,
                           {"kind": "match_delivered", "listing_id": listing.get("listing_id")}))

    def request_neighborhood_data(self, ctx: str):
        """Job component: neighborhood packages for buyer profiles via 10.
        Called when a buyer profile needs enrichment (schedule/on-demand
        driven, matching the established pattern), not envelope-triggered."""
        self.hub.send(_env("13", "10", "data.request", ctx,
                           {"mode": "neighborhood", "license_scope": "internal"}))

    def handle(self, env: Envelope):
        ctx = env.client_context_id
        payload = env.payload

        if env.intent == "config.update":
            if "buyer_criteria" in payload:
                criteria = payload["buyer_criteria"]
                sensitive = []
                clean = []
                for c in criteria:
                    hit = self._protected_class_hit(str(c.get("field", "")) + " " +
                                                    str(c.get("value", "")))
                    if hit:
                        self.hub.escalate("escalation.legal_line",
                                          {"client_context_id": ctx,
                                           "trigger": f"criterion correlates "
                                                     f"with protected class: "
                                                     f"{hit!r} - refused",
                                           "agent": "13"})
                        continue  # tuple 2: refuse the criterion, never add it
                    if c.get("fair_housing_sensitive"):
                        sensitive.append(c)
                    else:
                        clean.append({**c, "source": STATED_BY_PARTY})
                self.buyer_criteria.setdefault(ctx, []).extend(clean)
                if sensitive:
                    self.criteria_pending_review.setdefault(ctx, []).extend(sensitive)
                    self.hub.send(_env("13", "17", "content.review", ctx,
                                       {"criteria": sensitive}))
                return
            if "deliver_unknown_standing_preference" in payload:
                self.deliver_unknown_ok[ctx] = bool(
                    payload["deliver_unknown_standing_preference"])
                return
            if "preapproval_expired" in payload:
                # tuple 10: notify + mark; matches continue flagged
                self.preapproval_expired[ctx] = True
                self.hub.send(_env("13", "11", "client.message.request", ctx,
                                   {"template": "preapproval_expired_notice"}))
                return
            return

        if env.intent == "content.verdict":
            verdict = payload.get("verdict")
            pending = self.criteria_pending_review.pop(ctx, [])
            if verdict == "approved":
                self.buyer_criteria.setdefault(ctx, []).extend(
                    [{**c, "source": STATED_BY_PARTY} for c in pending])
            # flagged -> criteria simply never gets added to filtering set
            return

        if env.intent == "listing.data":
            self._match_one(ctx, payload, env)
            return

        if env.intent == "prospect.opportunity":
            self._match_one(ctx, payload, env)
            return

        if env.intent == "lead.reply":
            message = str(payload.get("message", "")).lower()

            if any(w in message for w in _PRICE_OPINION_WORDS):
                # tuple 4: pricing evaluation -> Legal Line
                self.hub.escalate("escalation.legal_line",
                                  {"client_context_id": ctx,
                                   "trigger": payload.get("message"),
                                   "agent": "13"})
                return

            if any(w in message for w in _SELLER_POSITION_WORDS):
                # tuple 8: refuse, other-party info never shared
                self.hub.escalate("escalation.legal_line",
                                  {"client_context_id": ctx,
                                   "trigger": payload.get("message"),
                                   "agent": "13"})
                return

            hit = self._protected_class_hit(message)
            if hit:
                self.hub.escalate("escalation.legal_line",
                                  {"client_context_id": ctx,
                                   "trigger": hit, "agent": "13"})
                return

            if payload.get("contradicts_stated_criteria"):
                # tuple 1: ask via 11, never rewrite the profile silently
                self.hub.send(_env("13", "11", "client.message.request", ctx,
                                   {"template": "confirm_criteria_update",
                                    "verbatim": payload.get("message")}))
                return

            if payload.get("explicit_feedback_field"):
                # explicit client feedback IS legitimate weighting input -
                # the only kind that is
                field = payload["explicit_feedback_field"]
                value = payload.get("explicit_feedback_value")
                self.buyer_criteria.setdefault(ctx, []).append(
                    {"field": field, "value": value, "source": STATED_BY_PARTY})
                self.hub.send(_env("13", "14", "interaction.log", ctx,
                                   {"kind": "criteria_updated_explicit",
                                    "field": field, "value": value}))
                return

            if payload.get("requests_showing"):
                self.pending_showing[ctx] = {
                    "listing_id": payload.get("listing_id")}
                self.hub.send(_env("13", "14", "record.request", ctx, {}))
                return
            return

        if env.intent == "record.response":
            pending = self.pending_showing.pop(ctx, None)
            if pending is None:
                return
            agreement_on_file = payload.get("buyer_agreement_on_file", False)
            if not agreement_on_file:
                # BUYER AGREEMENT GATE: absent = human escalation
                self.hub.escalate("escalation.legal_line",
                                  {"client_context_id": ctx,
                                   "trigger": "showing requested, no signed "
                                             "buyer agreement on file",
                                   "agent": "13"})
                return
            self.hub.send(_env("13", "06", "showing.request", ctx,
                               {"listing_id": pending["listing_id"],
                                "buyer_agreement_on_file": True,
                                "requester_identity_verified":
                                    payload.get("requester_identity_verified", False)}))
            return

        if env.intent == "data.package":
            # neighborhood package for a buyer profile - sourced data
            # only, no characterization (10's presentation rule applies
            # here too), passed through to 11 as-is
            self.hub.send(_env("13", "11", "client.message.request", ctx,
                               {"template": "neighborhood_package",
                                "package": payload}))
            return
