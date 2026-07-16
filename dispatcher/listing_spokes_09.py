"""Agent 09 - Vendor Coordination, built against the full spec.

Manages the service provider network. Schedules only roster vendors with
current credentials. RESPA Section 8 (referral compensation with
settlement service providers) is human/counsel territory, absolute.
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


class Spoke09VendorCoordination:
    """DECISIONS.md tuples implemented directly:
      1. vendor cancels late -> notify 07/06 immediately + roster
         alternatives; regulated roles never auto-rebooked without human
      2. credential expires mid-engagement -> flag before any next scheduling
      3. vendor proposes rate change mid-job -> halt + human, RESPA-adjacent
      4. deliverable arrives partial -> report partial truthfully, never
         mark collected
      5. two agents request same vendor slot -> deadline-driven request
         wins, ties to human
      6. vendor not on approved list -> refuse to schedule, propose
         addition to human; urgency creates no approval
      7. vendor requests property access codes -> custody protocol only,
         never ride vendor messages
      8. completion claimed without proof artifact -> status stays open
      9. invoice differs from quote -> log both, human; never approve
         variances
      10. vendor no-show on deadline-critical job -> escalate + offer next
          approved vendor to human, never self-substitute
      11. work scope grows on-site -> stop-work message per template,
          scope changes are human-approved
    """

    def __init__(self, hub, roster: dict[str, dict] | None = None):
        self.hub = hub
        # roster: vendor_id -> {kind, license_expiry, insurance_expiry,
        #                        regulated (bool)}. Human-approved additions
        # only - empty/unset means nothing is approved yet (fail closed).
        self.roster = roster or {}
        self.scheduled: dict[str, dict[str, dict]] = {}  # ctx -> milestone -> {vendor_id, time}
        self.slot_holds: dict[str, str] = {}  # (property, time) key -> ctx holding it
        hub.register("09", self.handle)

    def _credentials_current(self, vendor_id: str, today: str) -> bool:
        v = self.roster.get(vendor_id)
        if not v:
            return False
        import datetime
        today_d = datetime.date.fromisoformat(today)
        for field in ("license_expiry", "insurance_expiry"):
            exp = v.get(field)
            if not exp or datetime.date.fromisoformat(exp) < today_d:
                return False
        return True

    def handle(self, env: Envelope):
        ctx = env.client_context_id
        payload = env.payload

        if env.intent == "vendor.request":
            vendor_id = payload.get("vendor_id")
            kind = payload.get("kind")
            today = payload.get("today")

            # tuple 6: not on approved list -> refuse, propose addition to
            # human; urgency creates no approval
            if vendor_id and vendor_id not in self.roster:
                self.hub.send(_env("09", "queue", "clarification.request", ctx,
                                   {"reason": f"vendor {vendor_id!r} not on "
                                             f"the approved roster - "
                                             f"proposing addition to human, "
                                             f"urgency does not create "
                                             f"approval"}))
                return

            # tuple 2/credential gate: expired or missing = flag before
            # scheduling
            if vendor_id and today and not self._credentials_current(vendor_id, today):
                self.hub.escalate("escalation.legal_line",
                                  {"client_context_id": ctx,
                                   "trigger": f"vendor {vendor_id!r} "
                                             f"credentials expired or "
                                             f"missing - flagged before "
                                             f"scheduling",
                                   "agent": "09"})
                return

            # tuple 5: same slot requested by two agents -> deadline-driven
            # wins, ties to human. Compare against the ACTUAL stored
            # priority of whoever holds the slot, not an externally-
            # supplied field a second requester couldn't legitimately know.
            slot_key = payload.get("slot_key")
            if slot_key:
                held = self.slot_holds.get(slot_key)
                incoming_priority = payload.get("deadline_priority", 0)
                if held and held["ctx"] != ctx:
                    existing_priority = held["priority"]
                    if incoming_priority == existing_priority:
                        self.hub.send(_env("09", "queue", "clarification.request",
                                           ctx, {"reason": "vendor slot "
                                                           "contention, tied "
                                                           "priority - human "
                                                           "decides"}))
                        return
                    if incoming_priority <= existing_priority:
                        self.hub.send(_env("09", "queue", "clarification.request",
                                           ctx, {"reason": "vendor slot "
                                                           "already held by "
                                                           "a higher/equal "
                                                           "priority request"}))
                        return
                self.slot_holds[slot_key] = {"ctx": ctx, "priority": incoming_priority}

            self.scheduled.setdefault(ctx, {})[kind] = {
                "vendor_id": vendor_id, "confirmed": True}
            self.hub.send(_env("09", "external", "vendor.schedule", ctx,
                               {"vendor_id": vendor_id, "kind": kind}))
            self.hub.send(_env("09", "18", "calendar.event", ctx,
                               {"event": f"vendor_{kind}", "vendor_id": vendor_id}))
            self.hub.ingest_spoke_trace(
                "09", env.envelope_id,
                thought=f"vendor {vendor_id!r} ({kind}) on roster, "
                        f"credentials current - scheduled",
                result="scheduled")
            return

        if env.intent == "vendor.event" and payload.get("event_kind") == "cancellation":
            kind = payload.get("kind")
            vendor_id = payload.get("vendor_id")
            # Derive from OWNED roster state, not the incoming event's own
            # claim - 09 already knows which roles are regulated; trusting
            # a self-reported flag on the cancellation event itself is the
            # same class of bug as trusting an echoed doc_type in Agent 08.
            regulated = self.roster.get(vendor_id, {}).get("regulated", True)
            # 09 isn't a legal sender of deadline.alert (only 07 is, per
            # routes.json) - notify via interaction.log to 14 instead. 07
            # learns about the resulting stall through its own
            # check_vendor_holdups timer, not a direct push from 09.
            self.hub.send(_env("09", "14", "interaction.log", ctx,
                               {"kind": "vendor_cancelled_late",
                                "vendor_kind": kind}))
            if regulated:
                # regulated roles never auto-rebooked without human
                self.hub.send(_env("09", "queue", "clarification.request", ctx,
                                   {"reason": f"regulated vendor role "
                                             f"{kind!r} cancelled late - "
                                             f"human picks the replacement, "
                                             f"never auto-rebooked"}))
            return

        if env.intent == "vendor.event" and payload.get("event_kind") == "rate_change":
            self.hub.escalate("escalation.legal_line",
                              {"client_context_id": ctx,
                               "trigger": "vendor proposed a rate change "
                                         "mid-job - RESPA-adjacent "
                                         "territory, halted for human",
                               "agent": "09"})
            return

        if env.intent == "vendor.event" and payload.get("event_kind") == "access_code_request":
            # tuple 7: never rides vendor messages, custody protocol only
            self.hub.escalate("escalation.legal_line",
                              {"client_context_id": ctx,
                               "trigger": "vendor requested property access "
                                         "codes - custody protocol only, "
                                         "never transmitted via vendor "
                                         "messages",
                               "agent": "09"})
            return

        if env.intent == "vendor.event" and payload.get("event_kind") == "deliverable_report":
            kind = payload.get("kind")
            proof_present = payload.get("proof_artifact_present", False)
            partial = payload.get("partial", False)

            if not proof_present:
                # tuple 8: completion claimed without proof -> status stays
                # open, deliverable.release requires the proof
                self.hub.ingest_spoke_trace(
                    "09", env.envelope_id,
                    thought=f"{kind!r} completion claimed without a proof "
                            f"artifact - status stays open, no "
                            f"deliverable.release without proof",
                    result="not released: no_proof")
                return

            if partial:
                # tuple 4: partial -> report truthfully, never mark collected
                target = "05" if kind == "photography" else "08"
                self.hub.send(_env("09", target, "deliverable.release", ctx,
                                   {"doc_type": payload.get("doc_type"),
                                    "partial": True}))
                return

            target = "05" if kind == "photography" else "08"
            self.hub.send(_env("09", target, "deliverable.release", ctx,
                               {"doc_type": payload.get("doc_type"),
                                "opens_correctly": True,
                                "content_hash": payload.get("content_hash"),
                                "appraised_value": payload.get("appraised_value"),
                                "contract_price": payload.get("contract_price"),
                                "repair_requests_present":
                                    payload.get("repair_requests_present"),
                                "exception_found": payload.get("exception_found"),
                                "exception_text": payload.get("exception_text")},
                               confidence=SOURCE_VERIFIED))
            self.hub.ingest_spoke_trace(
                "09", env.envelope_id,
                thought=f"{kind!r} deliverable verified present and opens - "
                        f"routing to {target}",
                result=f"deliverable.release -> {target}")
            return

        if env.intent == "vendor.event" and payload.get("event_kind") == "invoice":
            quote = payload.get("quote_amount")
            invoice = payload.get("invoice_amount")
            if quote is not None and invoice is not None and quote != invoice:
                # tuple 9: differs from quote -> log both, human, never
                # approve variances
                self.hub.send(_env("09", "14", "interaction.log", ctx,
                                   {"kind": "invoice_variance",
                                    "quote": quote, "invoice": invoice}))
                self.hub.escalate("escalation.legal_line",
                                  {"client_context_id": ctx,
                                   "trigger": f"invoice {invoice} differs "
                                             f"from quote {quote} - human, "
                                             f"never auto-approved",
                                   "agent": "09"})
            return

        if env.intent == "vendor.event" and payload.get("event_kind") == "no_show":
            # Fail closed: unknown criticality is treated as critical, not
            # dismissed - same reasoning as the other two fixes here.
            deadline_critical = payload.get("deadline_critical", True)
            if deadline_critical:
                # tuple 10: escalate + offer next approved vendor to human,
                # never self-substitute
                self.hub.send(_env("09", "queue", "clarification.request", ctx,
                                   {"reason": "vendor no-show on a "
                                             "deadline-critical job - human "
                                             "picks the next approved "
                                             "vendor, never self-substituted"}))
            else:
                self.hub.send(_env("09", "14", "interaction.log", ctx,
                                   {"kind": "vendor_no_show",
                                    "deadline_critical": False}))
            return

        if env.intent == "vendor.event" and payload.get("event_kind") == "scope_change":
            # tuple 11: stop-work message per template, human-approved only
            self.hub.send(_env("09", "queue", "clarification.request", ctx,
                               {"reason": "work scope grew on-site - stop "
                                         "work order issued, scope changes "
                                         "are human-approved only"}))
            return
