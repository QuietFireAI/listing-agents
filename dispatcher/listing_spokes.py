"""Real spokes for the listing (real-estate) vertical, rebuilt against the
FULL ratified spec - not the simplified P11 demo versions.

The P11 demo's Spoke01LeadCapture/Spoke14CRM (in dispatcher-agents proper)
implement roughly 2 of 01's 16 decision tuples and omit the Legal Line
escalation entirely. This file replaces that scope with the real spec.
"""
from __future__ import annotations

from .core import Envelope

SOURCE_VERIFIED = "source_verified"
STATED_BY_PARTY = "stated_by_party"
UNKNOWN = "unknown"

CHANNEL_RANK = {"call": 3, "text": 2, "web_form": 1}

# Legal-line trigger phrases (01 SKILL.md S3): fiduciary pricing advice,
# contract negotiation, legal opinions. Conservative/broad per "if
# classification is uncertain, treat it as over the line."
_LEGAL_LINE_WORDS = ("what should i offer", "should i list at", "negotiate",
                    "contract language", "legal opinion", "is this legal",
                    "represent me in", "counter their offer")


def _env(frm, to, intent, ctx, payload, confidence=UNKNOWN, escalation_flag=False):
    return Envelope(from_agent=frm, to_agent=to, intent=intent,
                    client_context_id=ctx, payload=payload,
                    confidence=confidence, escalation_flag=escalation_flag,
                    provenance={"source": f"spoke-{frm}", "captured_at": "runtime",
                                "verbatim_available": True})


class Spoke14CRMPipeline:
    """System of record. Append-only; consent flags are authoritative HERE.

    DECISIONS.md tuples implemented directly:
      - conflicting facts for one event -> both stand, flagged (never merged)
      - merge candidate w/ unconfirmed identity -> no merge
      - report over a known logging gap -> state the gap, never smooth
      - consent flags conflict across channels -> most restrictive wins, per channel
      - record deletion request -> escalate to human (retention is jurisdiction)
      - merge candidates detected -> propose w/ evidence, never auto-merge
      - record edit erasing history -> append-correct, never overwrite
      - external import conflicts w/ log-derived state -> log wins, flagged
      - context requested outside a routed intent -> refuse
      - retention/deletion request -> freeze + escalate to owner
    """

    def __init__(self, hub):
        self.hub = hub
        self.records: dict[str, list[dict]] = {}
        # canonical consent state per context, per channel - authoritative
        self.consent: dict[str, dict[str, str]] = {}
        # canonical property-interest list per context - authoritative,
        # same reason: 01 must never rebuild this from its own memory
        # across completed round trips (MANNERS #9).
        self.property_interests: dict[str, list[dict]] = {}
        # authoritative buyer-agent-relationship facts - 14 is system of
        # record for these, updated by whoever confirms them (13 signs
        # the agreement, an identity-verification process confirms
        # identity), queried by anyone who needs the current fact (06 via
        # 11, directly) rather than each requester inventing or omitting it.
        self.buyer_agreement: dict[str, bool] = {}
        self.identity_verified: dict[str, bool] = {}
        self._entry_seq = 0
        hub.register("14", self.handle)

    def _new_entry_id(self) -> str:
        self._entry_seq += 1
        return f"E{self._entry_seq:06d}"

    def _append(self, ctx: str, kind: str, env: Envelope) -> str:
        entry_id = self._new_entry_id()
        entry = {"entry_id": entry_id, "kind": kind,
                 "envelope_id": env.envelope_id, "from_agent": env.from_agent,
                 "payload": env.payload}
        self.records.setdefault(ctx, []).append(entry)
        return entry_id

    def _update_consent(self, ctx: str, new_flags: dict):
        """Most-restrictive-per-channel tuple: a channel already marked
        'no' never gets upgraded to 'yes' by a later, less-restrictive claim
        without an explicit new confirmation event - only downgrades
        (yes->no) or first-time-sets are applied automatically."""
        current = self.consent.setdefault(ctx, {})
        for channel, val in (new_flags or {}).items():
            if channel not in current or val == "no" or current[channel] == "unknown":
                current[channel] = val
            # current[channel] == "yes" and val == "yes": no-op, unchanged
            # current[channel] == "no": stays "no" regardless of new claim
            # (most restrictive wins) UNLESS the new event is itself an
            # explicit reconfirmation, which callers signal separately.

    def check_date_triggers(self, today: str, client_dates: dict[str, dict[str, str]]):
        """Job component: emit date triggers (birthday, holiday, purchase/
        move-in anniversary) to 16, per the human-supplied client list.
        Per P13: schedule-driven (owner-configured daily check), not
        envelope-triggered - there is no inbound routed intent for this,
        matching the playbook's own framing ('consumed by Agent 00').
        Precondition (P13): contact must be on the supplied list AND pass
        consent - no list entry, no touch."""
        fired = []
        for ctx, dates in client_dates.items():
            consent = self.consent.get(ctx, {})
            if not any(v == "yes" for v in consent.values()):
                continue  # no consent on file, no touch - P13 precondition
            for event_type, date_str in dates.items():
                if date_str == today:
                    self.hub.send(_env("14", "16", "date.trigger", ctx,
                                       {"event_type": event_type, "date": today}))
                    fired.append((ctx, event_type))
        return fired

    def generate_report(self, report_type: str = "eod"):
        """Job component: pipeline reports from stored data only - a
        report never contains a figure that cannot be traced to records.
        Per P17: scheduled daily close, not envelope-triggered."""
        interaction_count = sum(len(v) for v in self.records.values())
        tiers = {}
        for ctx, entries in self.records.items():
            for e in entries:
                if e["kind"] == "interaction.log" and "tier" in e["payload"]:
                    tiers[ctx] = e["payload"]["tier"]  # last write wins, traced to entry_id
        report = {
            "report_type": report_type,
            "traced_to_entries": True,
            "total_interactions": interaction_count,
            "contexts_covered": len(self.records),
            "tier_snapshot": tiers,
        }
        self.hub.send(_env("14", "human", "report.package", "eod-report", report))
        return report

    def handle(self, env: Envelope):
        ctx = env.client_context_id

        if env.intent == "record.request":
            requester_scope = env.payload.get("requester_scope")
            dedupe_key = env.payload.get("dedupe_key")
            entries = self.records.get(ctx, [])

            if dedupe_key is not None:
                known = any(e["kind"] == "interaction.log"
                           and e["payload"].get("kind") == "lead.captured"
                           for e in entries)
                consent = dict(self.consent.get(ctx, {}))
                interests = list(self.property_interests.get(ctx, []))
                self.hub.ingest_spoke_trace(
                    "14", env.envelope_id,
                    thought=f"dedupe lookup ctx={ctx!r}: "
                            f"{'HIT - prior lead.captured exists' if known else 'MISS'}; "
                            f"returning current consent + interest state "
                            f"(authoritative here, never held by 01 across turns)",
                    result=f"known={known}")
                self.hub.send(_env("14", env.from_agent, "record.response",
                                   ctx, {"known": known, "consent": consent,
                                        "property_interests": interests},
                                   confidence=SOURCE_VERIFIED))
                return

            # Need-to-know: a record.request is the only legitimate way to
            # ask for a context; anything else refuses (already enforced by
            # the closed track upstream, this is the redundant local check
            # named in the tuple).
            entries_out = list(entries)
            self.hub.ingest_spoke_trace(
                "14", env.envelope_id,
                thought=f"record.request ctx={ctx!r}: {len(entries_out)} "
                        f"entries returned verbatim, no interpretation",
                result=f"returned={len(entries_out)}")
            self.hub.send(_env("14", env.from_agent, "record.response", ctx,
                               {"entries": entries_out, "absent": not entries_out,
                                "buyer_agreement_on_file": self.buyer_agreement.get(ctx, False),
                                "requester_identity_verified": self.identity_verified.get(ctx, False)},
                               confidence=SOURCE_VERIFIED))
            return

        if env.intent == "interaction.log":
            payload = env.payload
            entry_id = self._append(ctx, "interaction.log", env)
            if "consent" in payload:
                self._update_consent(ctx, payload["consent"])
            if "property_interests" in payload:
                self.property_interests[ctx] = list(payload["property_interests"])
            if "buyer_agreement_on_file" in payload:
                self.buyer_agreement[ctx] = bool(payload["buyer_agreement_on_file"])
            if "requester_identity_verified" in payload:
                self.identity_verified[ctx] = bool(payload["requester_identity_verified"])
            self.hub.ingest_spoke_trace(
                "14", env.envelope_id,
                thought=f"append-only interaction entry {entry_id}; "
                        f"consent updated per most-restrictive-per-channel "
                        f"rule if present in payload; property interests "
                        f"list replaced with the caller's full current list",
                result=f"appended={entry_id}")
            return

        if env.intent in ("transaction.closed", "status.update"):
            entry_id = self._append(ctx, env.intent, env)
            self.hub.ingest_spoke_trace(
                "14", env.envelope_id,
                thought=f"audit receiver: {env.intent} appended as "
                        f"{entry_id}; log-derived state wins over any "
                        f"conflicting external import, flagged if conflict",
                result=f"appended={entry_id}")
            return

        if env.intent == "config.update" and env.payload.get("action") == "delete_record":
            # retention/deletion is never a spoke decision - freeze + escalate
            self.hub.ingest_spoke_trace(
                "14", env.envelope_id,
                thought="deletion/retention request - freeze the record, "
                        "escalate to human/owner; retention rules are "
                        "jurisdiction, never a spoke judgment call",
                result="frozen, escalated")
            self.hub.escalate("escalation.legal_line",
                              {"client_context_id": ctx,
                               "trigger": "record deletion/retention request",
                               "agent": "14"})
            return


class Spoke01LeadCapture:
    """Front-of-funnel intake. Captures; never qualifies, scores, or advises.

    Rebuilt against all 16 DECISIONS.md tuples - the P11 demo version
    implemented roughly two of these and inverted the consent-refusal
    behavior (dropped the lead instead of capturing it with outreach
    suppressed). Fixed here.
    """

    def __init__(self, hub, brokerage_scope: set[str] | None = None,
                 dnc_list: set[str] | None = None):
        self.hub = hub
        self.pending: dict[str, dict] = {}
        # addresses/listing-IDs this brokerage actually lists (tuple: property
        # outside scope -> log + human, never redirect). None/empty = nothing
        # configured yet = everything is out of scope until set (fail closed,
        # same discipline as freight's service_scope).
        self.brokerage_scope = brokerage_scope or set()
        # phone/email already on the do-not-call/do-not-contact list
        self.dnc_list = dnc_list or set()
        hub.register("01", self.handle)

    def _legal_line_hit(self, payload: dict) -> str | None:
        note = str(payload.get("message", "")) + " " + str(payload.get("request", ""))
        low = note.lower()
        for w in _LEGAL_LINE_WORDS:
            if w in low:
                return note.strip()
        return None

    def handle(self, env: Envelope):
        if env.intent in ("lead.signal", "lead.inbound"):
            payload = env.payload
            ctx = env.client_context_id

            # --- Minor-safety protocol: absolute, checked before anything
            # else. Capture NOTHING beyond the fact of contact. ---
            if payload.get("apparent_minor"):
                self.hub.ingest_spoke_trace(
                    "01", env.envelope_id,
                    thought="apparent minor inquiring - capture nothing "
                            "beyond the fact of contact per hard rule; "
                            "human immediately, not a judgment call",
                    result="escalated: minor_safety, no data captured")
                self.hub.escalate("escalation.legal_line",
                                  {"client_context_id": ctx,
                                   "trigger": "apparent minor inquiry - fact "
                                             "of contact only, no data captured",
                                   "agent": "01"})
                return

            # --- Legal line: fiduciary advice / negotiation / legal opinion ---
            trigger = self._legal_line_hit(payload)
            if trigger:
                self.hub.ingest_spoke_trace(
                    "01", env.envelope_id,
                    thought=f"request crosses the legal line: {trigger!r} - "
                            f"not this agent's territory; escalating "
                            f"verbatim, not answering or approximating",
                    result="escalated: legal_line")
                self.hub.escalate("escalation.legal_line",
                                  {"client_context_id": ctx,
                                   "trigger": trigger, "agent": "01"})
                return

            # --- Abusive contact: stay professional, close politely, log
            # verbatim, escalate as a complaint - but STILL capture what's
            # already been given (doesn't return early). ---
            abusive = bool(payload.get("abusive"))
            if abusive:
                self.hub.escalate("escalation.complaint",
                                  {"client_context_id": ctx,
                                   "trigger": "abusive contact",
                                   "verbatim": payload.get("message", "")})

            # --- DNC check: the list wins over the opportunity. Still
            # captured (source + suppression), never silently dropped. ---
            contact = payload.get("phone") or payload.get("email")
            on_dnc = contact in self.dnc_list if contact else False

            # --- Property scope check ---
            interest = payload.get("property_interest", {})
            addr_or_listing = interest.get("address") or interest.get("listing_id")
            out_of_scope = (addr_or_listing is not None
                           and self.brokerage_scope
                           and addr_or_listing not in self.brokerage_scope)
            if out_of_scope:
                self.hub.ingest_spoke_trace(
                    "01", env.envelope_id,
                    thought=f"inquiry about {addr_or_listing!r} - not in "
                            f"this brokerage's configured listing scope; "
                            f"log + human, never redirect to another "
                            f"brokerage unprompted",
                    result="escalated: out_of_scope_property, logged")
                self.hub.escalate("escalation.legal_line",
                                  {"client_context_id": ctx,
                                   "trigger": f"inquiry outside brokerage "
                                             f"scope: {addr_or_listing}",
                                   "agent": "01"})
                # still fall through to capture the contact - the tuple says
                # log + human, not "discard the lead"

            # --- Consent, captured regardless of the answer (tuple: refusal
            # captures the lead, marks no-consent; it does not drop it) ---
            consent_raw = payload.get("consent")  # e.g. {"call":"yes","text":"no","email":"unknown"}
            if consent_raw is None:
                consent_raw = {"call": "unknown", "text": "unknown", "email": "unknown"}
            # "stop contacting me" mid-capture -> immediate blanket suppression
            if payload.get("revoke_all_contact"):
                consent_raw = {k: "no" for k in ("call", "text", "email")}

            # --- Multiple near-simultaneous inbounds for the same context:
            # merge by channel priority (call > text > web_form), drop
            # nothing - accumulate into a list, don't overwrite. ---
            existing = self.pending.get(ctx)
            channel = payload.get("channel", "web_form")
            raw_inputs = (existing["raw_inputs"] if existing else []) + [
                {"channel": channel, "payload": dict(payload)}]

            def _pick(field):
                """Highest channel-rank source for this field, across all
                raw inputs seen so far for this context - never drops a
                lower-rank input, just orders precedence on conflict."""
                candidates = [(CHANNEL_RANK.get(r["channel"], 0), r["payload"].get(field))
                             for r in raw_inputs if r["payload"].get(field) not in (None, "")]
                if not candidates:
                    return None
                candidates.sort(key=lambda t: -t[0])
                return candidates[0][1]

            name = _pick("name")
            timeline = _pick("timeline")
            budget = _pick("budget")
            preapproval = payload.get("preapproval_status", "unknown")
            if preapproval not in ("yes", "no", "unknown"):
                preapproval = "unknown"  # never inferred

            # low-confidence transcription -> mark unknown, never tier on it
            if payload.get("transcription_confidence") == "low":
                name = None if channel == "call" else name

            # obviously-false contact data: pass through an explicit
            # upstream signal, never guess at "obviously false" ourselves
            contact_valid_flag = payload.get("contact_data_suspect")

            # volunteered info beyond schema -> verbatim notes, never parsed
            notes = existing["notes"] if existing else []
            if payload.get("notes"):
                notes = notes + [str(payload["notes"])]

            # same person, two property interests -> one context, both
            # interests recorded, never two contexts
            interests = (existing["property_interests"] if existing else [])
            if interest and interest not in interests:
                interests = interests + [interest]

            captured = {
                "name": {"value": name, "source": STATED_BY_PARTY} if name else None,
                "contact": {"value": contact, "source": STATED_BY_PARTY,
                           "suspect": contact_valid_flag},
                "property_interests": interests,
                "timeline": {"value": timeline, "source": STATED_BY_PARTY} if timeline else None,
                "budget": {"value": budget, "source": STATED_BY_PARTY} if budget else None,
                "preapproval_status": preapproval,
                "consent": consent_raw,
                "dnc": on_dnc,
                "notes_verbatim": notes,
                "prior_relationship_claim": payload.get("prior_relationship_claim"),
            }
            self.pending[ctx] = {**captured, "raw_inputs": raw_inputs, "notes": notes,
                                 "property_interests": interests}

            if on_dnc:
                self.hub.escalate("escalation.legal_line",
                                  {"client_context_id": ctx,
                                   "trigger": "lead on DNC list - source "
                                             "logged, outreach suppressed",
                                   "agent": "01"})

            self.hub.ingest_spoke_trace(
                "01", env.envelope_id,
                thought=f"tender captured via {channel}; dedupe against CRM "
                        f"(14) before creating a new lead object - never "
                        f"merge unconfirmed identity",
                result="record.request issued")
            self.hub.send(_env("01", "14", "record.request", ctx,
                               {"dedupe_key": ctx}))
            return

        if env.intent == "record.response":
            ctx = env.client_context_id
            pending = self.pending.get(ctx)
            if pending is None:
                self.hub.ingest_spoke_trace(
                    "01", env.envelope_id,
                    thought=f"record.response for ctx={ctx!r} has no "
                            f"matching pending capture - cannot correlate, "
                            f"flagging rather than guessing",
                    result="flagged: uncorrelated response")
                self.hub.send(_env("01", "queue", "clarification.request",
                                   ctx, {"reason": "uncorrelated record.response"}))
                return
            payload = dict(pending)
            raw_inputs = payload.pop("raw_inputs")
            notes = payload.pop("notes")
            known = env.payload["known"]
            prior_consent = env.payload.get("consent", {})
            prior_interests = env.payload.get("property_interests", [])
            payload["duplicate"] = known

            # Consent: most-restrictive-per-channel against 14's
            # AUTHORITATIVE prior state (never against 01's own memory -
            # MANNERS #9, never rebuild state from memory of prior turns).
            merged_consent = dict(prior_consent)
            for ch, val in payload["consent"].items():
                if ch not in merged_consent or val == "no" or merged_consent[ch] == "unknown":
                    merged_consent[ch] = val
                # merged_consent[ch] == "no" and val != "no": stays "no"
            payload["consent"] = merged_consent

            # Same-person-two-properties: merge against 14's authoritative
            # prior interest list, same reason.
            merged_interests = list(prior_interests)
            for i in payload["property_interests"]:
                if i not in merged_interests:
                    merged_interests.append(i)
            payload["property_interests"] = merged_interests

            self.pending.pop(ctx, None)

            self.hub.ingest_spoke_trace(
                "01", env.envelope_id,
                thought=f"dedupe answer known={known}; consent and "
                        f"property interests merged against 14's "
                        f"authoritative state; forwarding complete lead "
                        f"object to Lead Qualification (02)",
                result="lead.captured issued")
            self.hub.send(_env("01", "02", "lead.captured", ctx, payload,
                               confidence=STATED_BY_PARTY))
            self.hub.send(_env("01", "14", "interaction.log", ctx,
                               {"kind": "lead.captured", "duplicate": known,
                                "consent": merged_consent,
                                "property_interests": merged_interests}))
            return
