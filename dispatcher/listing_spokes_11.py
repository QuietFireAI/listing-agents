"""Agent 11 - Client Communication, built against the full spec.

Single voice for routine client updates. Reports facts, never
characterizations. Routes inbound replies to the owning agent BY CONTENT
- this is the actual implementation of the "routed by content" mechanism
referenced across the swarm (lead.reply, document.submission,
showing.no_show, showing.feedback_response all originate here).
"""
from __future__ import annotations

from .core import Envelope

SOURCE_VERIFIED = "source_verified"
STATED_BY_PARTY = "stated_by_party"
UNKNOWN = "unknown"

_ADVICE_WORDS = ("what would you do", "should i offer", "what's it worth",
                 "negotiate", "legal opinion", "pricing strategy")
_WIRE_WORDS = ("wire instructions", "wiring details", "wire transfer",
              "updated wire", "routing number")


def _env(frm, to, intent, ctx, payload, confidence=UNKNOWN, in_reply_to=None):
    return Envelope(from_agent=frm, to_agent=to, intent=intent,
                    client_context_id=ctx, payload=payload,
                    confidence=confidence, in_reply_to=in_reply_to,
                    provenance={"source": f"spoke-{frm}", "captured_at": "runtime",
                                "verbatim_available": True})


def _flatten_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _flatten_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _flatten_strings(v)


class Spoke11ClientCommunication:
    """DECISIONS.md tuples implemented directly:
      1. reply mixes routine + advice -> answer neither, split and route both
      2. client is angry -> acknowledge + escalation.complaint, no
         defensiveness, no promises
      3. two agents report conflicting statuses -> send nothing, clarification
      4. urgent alert inside quiet hours -> send only if config marks that
         class exempt, otherwise wake human not client
      5. 'what would you do?' -> escalation.legal_line, verbatim
      6. pricing/strategy question -> Legal Line, template ack + human handoff
      7. message would go out after legal contact hours -> queue for next
         window, urgency claim requires human override
      8. template variable unresolved -> does not send, blank-fill is
         fabrication
      9. client references an off-log conversation -> acknowledge + ask
         for particulars, never pretend recall
      10. negative-tone reply -> route P14 complaint intake, never freestyle
          de-escalation
      11. contact-channel change request -> honor immediately, record via
          14, confirm once
    """

    def __init__(self, hub, quiet_hours: tuple[int, int] = (21, 8),
                 exempt_alert_classes: set[str] | None = None):
        self.hub = hub
        self.quiet_hours = quiet_hours  # (start_hour, end_hour), wraps midnight
        self.exempt_alert_classes = exempt_alert_classes or set()
        self.pending_conflict: dict[str, list] = {}  # ctx -> conflicting statuses
        self.pending_showing_requests: dict[str, dict] = {}  # ctx -> held request
        self.awaiting_human_response: dict[str, bool] = {}  # ctx -> escalated, awaiting human
        hub.register("11", self.handle)

    def _in_quiet_hours(self, hour: int) -> bool:
        start, end = self.quiet_hours
        if start > end:  # wraps midnight, e.g. 21 -> 8
            return hour >= start or hour < end
        return start <= hour < end

    def _advice_hit(self, text: str) -> str | None:
        low = text.lower()
        for w in _ADVICE_WORDS:
            if w in low:
                return text.strip()
        return None

    def _wire_hit(self, payload: dict) -> bool:
        text = " ".join(_flatten_strings(payload)).lower()
        return any(w in text for w in _WIRE_WORDS)

    def _send_client_message(self, ctx: str, template: str, variables: dict,
                             hour: int, alert_class: str | None = None,
                             env: Envelope | None = None):
        # tuple 8: unresolved template variable -> does not send
        unresolved = [k for k, v in variables.items() if v is None]
        if unresolved:
            self.hub.ingest_spoke_trace(
                "11", env.envelope_id if env else "internal",
                thought=f"template {template!r} has unresolved variables "
                        f"{unresolved} - a blank filled by guess is a "
                        f"fabrication to a client, not sending",
                result="held: unresolved_template_variable")
            self.hub.send(_env("11", "queue", "clarification.request", ctx,
                               {"reason": f"unresolved template variables: "
                                         f"{unresolved}"}))
            return False

        # tuple 4/7: quiet hours / legal contact hours gate
        if self._in_quiet_hours(hour) and alert_class not in self.exempt_alert_classes:
            self.hub.ingest_spoke_trace(
                "11", env.envelope_id if env else "internal",
                thought=f"send would land inside quiet hours (hour={hour}) "
                        f"and alert_class={alert_class!r} is not config-"
                        f"exempt - queuing for the next window, waking the "
                        f"human instead of the client",
                result="queued: quiet_hours")
            self.hub.send(_env("11", "queue", "clarification.request", ctx,
                               {"reason": "send queued - quiet hours, "
                                         "human notified instead"}))
            return False

        self.hub.send(_env("11", "external", "client.message.send", ctx,
                           {"template": template, "variables": variables}))
        self.hub.send(_env("11", "14", "interaction.log", ctx,
                           {"kind": "client_touch", "template": template}))
        return True

    def request_market_update(self, ctx: str):
        """Job component: 'data for client market updates' via 10. Called
        on a weekly schedule (matching the established schedule-driven
        method pattern), not envelope-triggered."""
        self.hub.send(_env("11", "10", "data.request", ctx,
                           {"mode": "neighborhood", "license_scope": "internal"}))

    def handle(self, env: Envelope):
        ctx = env.client_context_id
        payload = env.payload

        if env.intent == "config.update":
            resolved_ctx = payload.get("resolve_advice_response")
            if resolved_ctx:
                self.awaiting_human_response.pop(resolved_ctx, None)
                self.hub.send(_env("11", "18", "agent.status", resolved_ctx,
                                   {"waiting_on": "human_advice_response",
                                    "resolved": True}))
            return

        if env.intent in ("status.update", "deadline.alert", "client.message.request"):
            hour = payload.get("hour", 12)
            alert_class = payload.get("alert_class")

            # tuple 3: conflicting statuses for the same milestone -> send
            # nothing, clarification
            existing = self.pending_conflict.setdefault(ctx, [])
            if env.intent == "status.update":
                milestone = payload.get("milestone") or payload.get("status")
                conflict_key = payload.get("conflict_key")
                if conflict_key:
                    prior = [s for s in existing if s.get("conflict_key") == conflict_key]
                    if prior and prior[-1]["value"] != payload.get("value"):
                        self.hub.send(_env("11", "queue", "clarification.request",
                                           ctx, {"reason": "conflicting statuses "
                                                           "for the same milestone "
                                                           "- sending nothing"}))
                        existing.append({"conflict_key": conflict_key,
                                        "value": payload.get("value")})
                        return
                    existing.append({"conflict_key": conflict_key,
                                    "value": payload.get("value")})

            template = payload.get("template", env.intent)
            variables = payload.get("variables", {k: v for k, v in payload.items()
                                                  if k not in ("hour", "alert_class",
                                                              "template")})
            self._send_client_message(ctx, template, variables, hour,
                                      alert_class, env)
            return

        if env.intent == "client.reply":
            message = str(payload.get("message", ""))
            low = message.lower()

            # wire fraud line - checked first, any channel
            if self._wire_hit(payload):
                self.hub.escalate("escalation.legal_line",
                                  {"client_context_id": ctx,
                                   "trigger": f"client message contains wire "
                                             f"topic: {message!r}",
                                   "agent": "11"})
                return

            # tuple 5: "what would you do" -> legal line, verbatim
            if "what would you do" in low:
                self.hub.escalate("escalation.legal_line",
                                  {"client_context_id": ctx,
                                   "trigger": message, "agent": "11"})
                return

            advice_hit = self._advice_hit(message)
            # Unlike the license_scope/opens_correctly/receipt_confirmed
            # class of fix, this one doesn't gate whether real protective
            # action happens - both branches escalate to Legal Line either
            # way. The doctrine also structures these as two distinct
            # tuples: advice-only (6) is the general case, "mixed" (1) is
            # the specific exception requiring its own detected condition.
            # Defaulting to "assume mixed" would invert that relationship.
            routine_present = payload.get("has_routine_component", False)

            # tuple 1: mixes routine + advice -> answer neither, split+route both
            if advice_hit and routine_present:
                self.hub.escalate("escalation.legal_line",
                                  {"client_context_id": ctx,
                                   "trigger": advice_hit, "agent": "11"})
                self.hub.send(_env("11", payload.get("owning_agent", "13"),
                                   "lead.reply", ctx,
                                   {"message": message, "split": "routine_only"}))
                return

            # tuple 6: pricing/strategy question -> legal line, template ack
            if advice_hit:
                self.hub.escalate("escalation.legal_line",
                                  {"client_context_id": ctx,
                                   "trigger": advice_hit, "agent": "11"})
                self.awaiting_human_response[ctx] = True
                self.hub.send(_env("11", "18", "agent.status", ctx,
                                   {"waiting_on": "human_advice_response",
                                    "since": payload.get("hour_date")}))
                self._send_client_message(ctx, "advice_ack_handoff", {},
                                          payload.get("hour", 12), env=env)
                return

            # tuple 2: angry client -> acknowledge + complaint escalation
            if payload.get("angry"):
                self.hub.escalate("escalation.complaint",
                                  {"client_context_id": ctx,
                                   "trigger": "angry client contact",
                                   "verbatim": message})
                self._send_client_message(ctx, "acknowledge_no_promises", {},
                                          payload.get("hour", 12), env=env)
                return

            # tuple 10: negative tone -> route P14 complaint intake, never
            # freestyle de-escalation
            if payload.get("negative_tone"):
                self.hub.send(_env("11", payload.get("owning_agent", "13"),
                                   "lead.reply", ctx,
                                   {"message": message, "route": "P14_complaint_intake"}))
                return

            # tuple 11: contact-channel change -> honor, record, confirm once
            if payload.get("requests_channel_change"):
                new_channel = payload.get("new_channel")
                # A client explicitly asking to be reached via a channel
                # clearly implies consent for it - log the event AND
                # update 14's authoritative consent record, or a system
                # reading consent later would still see it as
                # unknown/no despite the client's explicit request.
                self.hub.send(_env("11", "14", "interaction.log", ctx,
                                   {"kind": "channel_change",
                                    "new_channel": new_channel,
                                    "consent": {new_channel: "yes"} if new_channel else {}}))
                self._send_client_message(ctx, "channel_change_confirmed", {},
                                          payload.get("hour", 12), env=env)
                return

            # tuple 9: references an off-log conversation -> acknowledge +
            # ask for particulars, never pretend recall
            if payload.get("references_unlogged_conversation"):
                self._send_client_message(ctx, "ask_for_particulars", {},
                                          payload.get("hour", 12), env=env)
                return

            # client-requested showing -> query 14 for the facts 06
            # requires (buyer_agreement_on_file, requester_identity_
            # verified - 14 is system of record for these, same pattern as
            # consent), then send a genuinely complete showing.request
            # directly to 06 - using 11's own documented edge for real,
            # not routing around the problem.
            if payload.get("requests_showing"):
                self.pending_showing_requests[ctx] = {
                    "requested_time": payload.get("requested_time")}
                self.hub.send(_env("11", "14", "record.request", ctx, {}))
                return

            # showing-related reply -> route to 06
            if payload.get("about_showing"):
                if payload.get("no_show"):
                    self.hub.send(_env("11", "06", "showing.no_show", ctx,
                                       {"message": message}))
                else:
                    self.hub.send(_env("11", "06", "showing.feedback_response",
                                       ctx, {"message": message}))
                return

            # document submission -> route to 08
            if payload.get("document_attached"):
                self.hub.send(_env("11", "08", "document.submission", ctx,
                                   {"doc_type": payload.get("doc_type"),
                                    "submitting_party": payload.get("submitting_party"),
                                    "opens_correctly": payload.get("opens_correctly"),
                                    "content_hash": payload.get("content_hash")}))
                return

            # routine reply -> route to owning agent (by content, per the
            # swarm-wide "routed by content" convention this agent
            # implements for real)
            owning_agent = payload.get("owning_agent", "13")
            self.hub.send(_env("11", owning_agent, "lead.reply", ctx,
                               {"message": message}))
            return

        if env.intent == "data.package":
            # market update package arrived for a client send - delivered
            # as sourced figures, never characterized (job component: route
            # neighborhood questions to 10's sourced packages, report facts
            # never characterizations)
            self._send_client_message(ctx, "weekly_market_update",
                                      {"package": payload}, payload.get("hour", 12),
                                      env=env)
            return

        if env.intent == "record.response":
            pending = self.pending_showing_requests.pop(ctx, None)
            if pending is None:
                return  # a consent-check lookup unrelated to a showing request
            buyer_agreement = payload.get("buyer_agreement_on_file", False)
            identity_verified = payload.get("requester_identity_verified", False)
            if not buyer_agreement:
                # 14 doesn't have this on file - not 11's call to schedule
                # around it; route to 13 (which owns getting it signed)
                # rather than send an incomplete request 06 would bounce.
                self.hub.send(_env("11", "13", "lead.reply", ctx,
                                   {"reason": "showing requested, no buyer "
                                             "agreement on file yet",
                                    "requested_time": pending["requested_time"]}))
                return
            self.hub.send(_env("11", "06", "showing.request", ctx,
                               {"requested_time": pending["requested_time"],
                                "buyer_agreement_on_file": buyer_agreement,
                                "requester_identity_verified": identity_verified}))
            return
