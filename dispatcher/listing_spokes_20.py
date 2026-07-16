"""Agent 20 - Social Media Monitoring, built against the full spec.

Monitors, classifies, routes. Never posts a public reply autonomously -
a wrong public reply is a permanent public artifact. Drafts go to the
human via 17's review; publication is a human act, always.
"""
from __future__ import annotations

from .core import Envelope

SOURCE_VERIFIED = "source_verified"
STATED_BY_PARTY = "stated_by_party"
UNKNOWN = "unknown"

_PRICING_LEGAL_WORDS = ("the price is", "we can offer", "contract terms",
                       "legal advice", "guarantee")


def _env(frm, to, intent, ctx, payload, confidence=UNKNOWN, in_reply_to=None):
    return Envelope(from_agent=frm, to_agent=to, intent=intent,
                    client_context_id=ctx, payload=payload,
                    confidence=confidence, in_reply_to=in_reply_to,
                    provenance={"source": f"spoke-{frm}", "captured_at": "runtime",
                                "verbatim_available": True})


class Spoke20SocialMediaMonitoring:
    """DECISIONS.md tuples implemented directly (5 active; a 6th entry
    in DECISIONS.md is an owner deferral note, not a decision - not
    implemented as a tuple, since there is nothing to implement):
      1. sentiment genuinely mixed -> classify at complaint priority,
         protective default
      2. mention may involve a client, identity unconfirmed -> route
         without confirming identity anywhere, including the
         clarification itself
      3. negative thread going viral -> escalation.complaint at
         priority, no drafts
      4. praise post detected -> log only, no auto-engagement
      5. question via DM vs public -> both route to 01, channel recorded
         in provenance
    """

    def __init__(self, hub):
        self.hub = hub
        self.monitored_channels: set[str] = set()
        self.pending_drafts: dict[str, dict] = {}  # envelope_id-keyed via in_reply_to
        hub.register("20", self.handle)

    def _pricing_legal_hit(self, text: str) -> str | None:
        low = text.lower()
        for w in _PRICING_LEGAL_WORDS:
            if w in low:
                return text.strip()
        return None

    def handle(self, env: Envelope):
        ctx = env.client_context_id
        payload = env.payload

        if env.intent == "config.update":
            if "monitored_channels" in payload:
                self.monitored_channels.update(payload["monitored_channels"])
            return

        if env.intent == "content.verdict":
            # Drafts never auto-publish regardless of verdict - this
            # agent has no publish capability at all. Verdict is logged
            # for the human's eventual review, nothing more.
            draft = self.pending_drafts.pop(env.in_reply_to, None)
            self.hub.send(_env("20", "14", "interaction.log", ctx,
                               {"kind": "draft_verdict_received",
                                "verdict": payload.get("verdict")}))
            return

        if env.intent == "social.mention":
            channel = payload.get("channel")
            if channel not in self.monitored_channels:
                return  # not a monitored channel, not this agent's concern

            text = payload.get("text", "")
            sentiment = payload.get("sentiment")  # question/complaint/lead_signal/praise/noise/mixed
            is_viral = payload.get("is_viral", False)
            may_involve_client = payload.get("may_involve_client_unconfirmed", False)

            # tuple 1: genuinely mixed sentiment -> classify at complaint
            # priority, protective default
            effective_sentiment = "complaint" if sentiment == "mixed" else sentiment

            self.hub.send(_env("20", "14", "interaction.log", ctx,
                               {"kind": "mention_classified",
                                "sentiment": effective_sentiment,
                                "channel": channel, "text_verbatim": text},
                              confidence=SOURCE_VERIFIED))

            if effective_sentiment == "complaint":
                # tuple 3: negative thread going viral -> escalate at
                # priority, no drafts - viral or not, complaints never
                # get an autonomous draft either way (this agent never
                # publishes; but "no drafts" for viral means don't even
                # prepare one for human review, escalate raw instead)
                escalation_payload = {"client_context_id": ctx,
                                      "trigger": text, "agent": "20",
                                      "channel": channel}
                if is_viral:
                    escalation_payload["priority"] = "viral"
                # tuple 2: identity unconfirmed -> route without
                # confirming identity anywhere, including here - the
                # escalation never asserts client status either way
                if may_involve_client:
                    escalation_payload["note"] = ("may relate to an "
                                                  "existing matter - "
                                                  "identity not confirmed "
                                                  "or asserted")
                self.hub.escalate("escalation.complaint", escalation_payload)
                return

            if effective_sentiment == "praise":
                # tuple 4: log only, no auto-engagement
                return

            if effective_sentiment == "question":
                # tuple 5: DM vs public both route to 01, channel
                # recorded in provenance. If it may involve an existing
                # client, route to 11 instead - but never assert/confirm
                # identity in the routing itself either way (tuple 2).
                if may_involve_client:
                    # Real bug found on the deep pass: client.message.request
                    # is universally treated by 11 as an OUTBOUND send
                    # instruction (template + variables), not a way to
                    # forward an inbound question. Sending {"verbatim":...}
                    # with no "template" key would have made 11 attempt to
                    # send a nonsensical message using the intent name
                    # itself as the template. Fixed: a real template
                    # ("team_will_follow_up", matching the Restricted-Speed
                    # doctrine's own framing) with the verbatim question as
                    # a variable for the human to see when they follow up.
                    self.hub.send(_env("20", "11", "client.message.request",
                                       ctx, {"template": "team_will_follow_up",
                                            "variables": {"channel": channel},
                                            "hour": payload.get("hour", 12),
                                            "inbound_verbatim": text,
                                            "note": "may relate to an "
                                                    "existing matter - "
                                                    "identity not "
                                                    "confirmed or asserted"}))
                else:
                    self.hub.send(_env("20", "01", "lead.signal", ctx,
                                       {"channel": channel, "message": text,
                                        "consent": {"call": "unknown",
                                                   "text": "unknown",
                                                   "email": "unknown"}},
                                      confidence=STATED_BY_PARTY))
                return

            if effective_sentiment == "lead_signal":
                self.hub.send(_env("20", "01", "lead.signal", ctx,
                                   {"channel": channel, "message": text,
                                    "consent": {"call": "unknown",
                                               "text": "unknown",
                                               "email": "unknown"}},
                                  confidence=STATED_BY_PARTY))
                return

            # "noise" or unrecognized sentiment - no suitable tuple, per
            # root rule: STOP and ask, never guess
            self.hub.send(_env("20", "queue", "clarification.request", ctx,
                               {"reason": f"unrecognized sentiment "
                                         f"classification {sentiment!r} - "
                                         f"no suitable tuple, holding"}))
            return

        if env.intent == "lead.reply":
            # existing-client (or prospect) reply routed back through 11 -
            # this agent's job here is discovery/classification, not
            # ongoing conversation; log only
            self.hub.send(_env("20", "14", "interaction.log", ctx,
                               {"kind": "reply_received"}))
            return

    def draft_response(self, ctx: str, draft_text: str, source_mention_id: str):
        """Optionally draft a suggested response - clearly labeled DRAFT,
        never published by this agent. Job component: never state/imply
        pricing, legal, or contractual content, never confirm client
        relationship. Checked BEFORE ever submitting for review - this
        agent doesn't even draft that content, let alone publish it."""
        hit = self._pricing_legal_hit(draft_text)
        if hit:
            self.hub.escalate("escalation.legal_line",
                              {"client_context_id": ctx,
                               "trigger": f"drafted response would state/"
                                         f"imply pricing/legal/contractual "
                                         f"content: {hit!r} - never drafted, "
                                         f"let alone published",
                               "agent": "20"})
            return "refused"
        env = _env("20", "17", "content.review", ctx,
                  {"draft": {"text": draft_text, "label": "DRAFT"},
                   "source_mention_id": source_mention_id})
        self.hub.send(env)
        self.pending_drafts[env.envelope_id] = {"text": draft_text}
        return "submitted_for_review"
