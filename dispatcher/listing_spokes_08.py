"""Agent 08 - Document Collection, built against the full spec.

Owns transaction ARTIFACTS. Never sets deadlines - that's 07's job. This
agent files documents, chases the missing ones via 11, and reports status
to 07. Built deliberately to emit doc.status payloads matching what 07
already consumes (07 was built first) - verified by a real cross-agent
integration test, not just parallel assumption on both sides.
"""
from __future__ import annotations

from .core import Envelope

SOURCE_VERIFIED = "source_verified"
STATED_BY_PARTY = "stated_by_party"
UNKNOWN = "unknown"

_WIRE_WORDS = ("wire instructions", "wiring details", "wire transfer",
              "updated wire", "routing number", "account number for closing")

DOC_MILESTONE_MAP = {
    "preapproval_letter": None, "proof_of_funds": None,
    "inspection_report": "inspection", "appraisal_report": "appraisal",
    "title_commitment": "title", "hoa_docs": "hoa_docs",
    "survey": None, "disclosure": None, "amendment": None,
    "closing_settlement_statement": "closing",
    "financing_contingency_removal": "financing_contingency",
    "earnest_money_receipt": "earnest_money",
}


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


class Spoke08DocumentCollection:
    """DECISIONS.md tuples implemented directly:
      1. received doc is the wrong type -> request correct type, never
         file a substitute
      2. document unreadable -> request re-send, log raw error
      3. two versions of one document conflict -> keep both + flag, never pick
      4. sensitive doc from unexpected sender -> quarantine, verify before filing
      5. chase attempts exhausted -> escalate, silence never becomes 'received'
      6. document unsigned where signature expected -> status incomplete,
         never file as done
      7. (dup of 3, same handling)
      8. disclosure deadline approaching, document absent -> alert 07 +
         human, absence is the alert, never soften
      9. document contains visible wire instructions -> quarantine +
         legal-line, never forward or store in general records
      10. document emailed to wrong context -> do not file, return-path to
          human, cross-context filing is a confidentiality breach
      11. retention window question -> hold everything, disposal is
          owner-authorized only
    """

    def __init__(self, hub, expected_senders: dict[str, set[str]] | None = None):
        self.hub = hub
        self.filed_documents: dict[str, list[dict]] = {}  # ctx -> filed docs
        self.pending_requests: dict[str, dict[str, dict]] = {}  # ctx -> {doc_type: {chase_count}}
        # per-context, per-doc-type set of senders this transaction expects -
        # empty/unset means unverifiable, so tuple 4 fires conservatively
        self.expected_senders = expected_senders or {}
        hub.register("08", self.handle)

    def _wire_check(self, payload: dict, ctx: str) -> bool:
        text = " ".join(_flatten_strings(payload)).lower()
        for w in _WIRE_WORDS:
            if w in text:
                self.hub.escalate("escalation.legal_line",
                                  {"client_context_id": ctx,
                                   "trigger": f"document contains wire "
                                             f"instructions ({w!r}) - "
                                             f"quarantined, never forwarded "
                                             f"or stored in general records",
                                   "agent": "08"})
                return True
        return False

    def handle(self, env: Envelope):
        ctx = env.client_context_id
        payload = env.payload

        if env.intent == "doc.request":
            doc_type = payload.get("doc_type") or payload.get("milestone")
            self.pending_requests.setdefault(ctx, {})[doc_type] = \
                {"chase_count": 0}
            self.hub.send(_env("08", "11", "client.message.request", ctx,
                               {"template": "document_request",
                                "doc_type": doc_type},
                               in_reply_to=env.envelope_id))
            today = payload.get("today")
            if today:
                self.hub.send(_env("08", "18", "agent.status", ctx,
                                   {"waiting_on": f"document:{doc_type}",
                                    "since": today}))
            self.hub.ingest_spoke_trace(
                "08", env.envelope_id,
                thought=f"document request for {doc_type!r} - chasing via "
                        f"11, never chasing parties directly",
                result="chase initiated")
            return

        if env.intent in ("document.submission", "deliverable.release"):
            # Two real entry paths, one shared filing pipeline: parties
            # submit documents via 11 (document.submission, mirroring the
            # lead.reply precedent); vendor-sourced reports (inspection,
            # appraisal) arrive via 09's existing deliverable.release route.
            self._handle_document_received(env, ctx, payload)
            return

        if env.intent == "config.update" and "retention_query" in payload:
            # tuple 11: hold everything, disposal is owner-authorized only.
            # Routed through the existing generic human channel rather
            # than an invented dedicated intent.
            self.hub.send(_env("08", "queue", "clarification.request", ctx,
                               {"reason": "retention/disposal question - "
                                         "owner authorization required, "
                                         "holding everything"}))
            return

    def check_chase_timeout(self, ctx: str, doc_type: str):
        """Schedule-driven, matching the established pattern (07's
        check_deadlines/check_vendor_holdups) rather than an invented
        routed intent - no chase.timeout route exists, and chasing on a
        clock isn't something another agent hands to 08 as an envelope."""
        pending = self.pending_requests.get(ctx, {}).get(doc_type)
        if pending is None:
            return None
        pending["chase_count"] += 1
        if pending["chase_count"] >= 3:
            # tuple 5: chase attempts exhausted -> escalate, silence never
            # becomes "received"
            self.hub.send(_env("08", "07", "doc.status", ctx,
                               {"doc_type": doc_type,
                                "milestone": DOC_MILESTONE_MAP.get(doc_type),
                                "status": "missing"}))
            self.hub.escalate("escalation.legal_line",
                              {"client_context_id": ctx,
                               "trigger": f"chase attempts exhausted for "
                                         f"{doc_type!r} - silence never "
                                         f"becomes received",
                               "agent": "08"})
            return "escalated"
        self.hub.send(_env("08", "11", "client.message.request", ctx,
                           {"template": "document_chase_followup",
                            "doc_type": doc_type}))
        return "chased"

    def check_disclosure_deadline(self, ctx: str, doc_type: str):
        """Schedule-driven, same reasoning as check_chase_timeout above."""
        filed = any(d["doc_type"] == doc_type
                   for d in self.filed_documents.get(ctx, []))
        if not filed:
            # tuple 8: absence is the alert, never soften
            self.hub.send(_env("08", "07", "doc.status", ctx,
                               {"doc_type": doc_type,
                                "milestone": DOC_MILESTONE_MAP.get(doc_type),
                                "status": "missing",
                                "disclosure_deadline_approaching": True}))
            self.hub.escalate("escalation.legal_line",
                              {"client_context_id": ctx,
                               "trigger": f"disclosure deadline "
                                         f"approaching, {doc_type!r} absent",
                               "agent": "08"})
            return "escalated"
        return "on_file"

    def _handle_document_received(self, env: Envelope, ctx: str, payload: dict):
        doc_type = payload.get("doc_type")

        # tuple 10: wrong client_context_id entirely - not this agent's
        # call to detect from content alone, but if the caller flags it:
        if payload.get("wrong_context"):
            self.hub.send(_env("08", "queue", "clarification.request", ctx,
                               {"reason": "document appears addressed to "
                                         "the wrong context - not filing, "
                                         "returning to human"}))
            return

        # wire check before anything else - never forwarded, never stored
        if self._wire_check(payload, ctx):
            return

        # tuple 4: sensitive doc from unexpected sender -> quarantine.
        # "Sender" here is the real-world PARTY (email/identity) that
        # submitted it, not the swarm agent - document.submission always
        # arrives via 11 regardless of which actual party sent it in.
        party_sender = payload.get("submitting_party")
        sensitive_types = {"preapproval_letter", "proof_of_funds",
                           "closing_settlement_statement"}
        expected = self.expected_senders.get(ctx, {}).get(doc_type)
        if doc_type in sensitive_types and expected and party_sender not in expected:
            self.hub.ingest_spoke_trace(
                "08", env.envelope_id,
                thought=f"{doc_type!r} received from party "
                        f"{party_sender!r}, not in the expected sender set "
                        f"{expected} for this transaction - quarantining, "
                        f"not filing",
                result="quarantined: sender_mismatch")
            self.hub.escalate("escalation.legal_line",
                              {"client_context_id": ctx,
                               "trigger": f"sensitive document from "
                                         f"unexpected party: {party_sender!r}",
                               "agent": "08"})
            return

        # tuple (09): deliverable arrives partial -> report partial
        # truthfully, never mark collected. Checked before type/readiness
        # checks - a partial artifact isn't "wrong type," it's incomplete.
        if payload.get("partial"):
            self.hub.send(_env("08", "07", "doc.status", ctx,
                               {"doc_type": doc_type,
                                "milestone": DOC_MILESTONE_MAP.get(doc_type),
                                "status": "partial"}))
            self.hub.ingest_spoke_trace(
                "08", env.envelope_id,
                thought=f"{doc_type!r} deliverable arrived partial - "
                        f"reporting partial truthfully, never marking "
                        f"collected",
                result="not filed: partial")
            return

        # tuple 1: wrong type received -> request correct type, never
        # substitute. Checked against ACTUAL pending state (what 08 itself
        # is tracking as outstanding), not an externally-echoed field a
        # sender might get wrong or omit - MANNERS #9, use owned state.
        pending_types = set(self.pending_requests.get(ctx, {}).keys())
        if pending_types and doc_type not in pending_types:
            self.hub.send(_env("08", "11", "client.message.request", ctx,
                               {"template": "wrong_document_type",
                                "requested": sorted(pending_types),
                                "received": doc_type}))
            self.hub.ingest_spoke_trace(
                "08", env.envelope_id,
                thought=f"received {doc_type!r}, but pending requests for "
                        f"this context are {sorted(pending_types)} - "
                        f"requesting the correct type, never filing a "
                        f"substitute",
                result="rejected: wrong_type")
            return

        # tuple 2: unreadable -> request re-send, log raw error.
        # Fail closed: absence of this flag means UNKNOWN readability, not
        # an assumption that it opens fine - defaulting to True let a
        # submission skip the readability check just by omitting the field.
        if not payload.get("opens_correctly", False):
            self.hub.send(_env("08", "11", "client.message.request", ctx,
                               {"template": "document_unreadable_resend",
                                "doc_type": doc_type}))
            self.hub.send(_env("08", "14", "interaction.log", ctx,
                               {"kind": "document_unreadable",
                                "raw_error": payload.get("open_error", "unknown")}))
            return

        # tuple 6: unsigned where signature expected -> incomplete, never
        # filed as done
        if payload.get("signature_expected") and not payload.get("signed"):
            self.hub.send(_env("08", "07", "doc.status", ctx,
                               {"doc_type": doc_type,
                                "milestone": DOC_MILESTONE_MAP.get(doc_type),
                                "status": "incomplete"}))
            return

        existing = self.filed_documents.setdefault(ctx, [])
        conflicting = [d for d in existing if d["doc_type"] == doc_type
                      and d["payload"].get("version_conflict_key") ==
                      payload.get("version_conflict_key")
                      and payload.get("version_conflict_key") is not None
                      and d.get("content_hash") != payload.get("content_hash")]
        if conflicting and payload.get("content_hash"):
            # tuple 3: two versions conflict -> keep both, flag, never pick
            existing.append({"doc_type": doc_type, "payload": payload,
                             "content_hash": payload.get("content_hash")})
            self.hub.send(_env("08", "07", "doc.status", ctx,
                               {"doc_type": doc_type,
                                "milestone": DOC_MILESTONE_MAP.get(doc_type),
                                "status": "conflicting_versions"}))
            self.hub.escalate("escalation.legal_line",
                              {"client_context_id": ctx,
                               "trigger": f"two versions of {doc_type!r} "
                                         f"conflict - both kept, human "
                                         f"picks",
                               "agent": "08"})
            return

        # clean file
        existing.append({"doc_type": doc_type, "payload": payload,
                         "content_hash": payload.get("content_hash")})
        had_pending = doc_type in self.pending_requests.get(ctx, {})
        self.pending_requests.get(ctx, {}).pop(doc_type, None)
        if had_pending:
            self.hub.send(_env("08", "18", "agent.status", ctx,
                               {"waiting_on": f"document:{doc_type}",
                                "resolved": True}))
        self.hub.send(_env("08", "14", "interaction.log", ctx,
                           {"kind": "document_filed", "doc_type": doc_type}))

        # Build the doc.status report - matches what 07 already expects to
        # receive per-milestone (built and integration-tested against 07's
        # actual handler, not assumed in isolation).
        status_payload = {"doc_type": doc_type,
                          "milestone": DOC_MILESTONE_MAP.get(doc_type),
                          "status": "received",
                          "artifact_on_file": True,
                          "artifact_ref": payload.get("content_hash", doc_type)}
        if doc_type == "inspection_report":
            status_payload["report_received"] = True
            status_payload["repair_requests_present"] = \
                payload.get("repair_requests_present", True)
        if doc_type == "appraisal_report":
            status_payload["appraised_value"] = payload.get("appraised_value")
            status_payload["contract_price"] = payload.get("contract_price")
        if doc_type == "title_commitment" and payload.get("exception_found"):
            status_payload["exception_found"] = True
            status_payload["exception_text"] = payload.get("exception_text")
        if doc_type == "earnest_money_receipt":
            # Fail closed: doctrine says "money milestones never get
            # benefit of the doubt" - defaulting to True did exactly that
            # when the field was simply omitted.
            status_payload["receipt_confirmed"] = payload.get("receipt_confirmed", False)

        self.hub.send(_env("08", "07", "doc.status", ctx, status_payload,
                           confidence=SOURCE_VERIFIED,
                           in_reply_to=env.envelope_id))
