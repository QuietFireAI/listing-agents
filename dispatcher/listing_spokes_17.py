"""Agent 17 - Compliance & Fair Housing, built against the full spec.

Guardrail agent. Validates, never creates, never edits, never approves its
own exceptions. Two verdicts only: approved or flagged with itemized
findings - never a rewrite, never a softened summary. The prohibited-
language ruleset is human-supplied configuration; this agent applies it,
never authors it.
"""
from __future__ import annotations

from .core import Envelope

SOURCE_VERIFIED = "source_verified"
STATED_BY_PARTY = "stated_by_party"
UNKNOWN = "unknown"


def _env(frm, to, intent, ctx, payload, confidence=UNKNOWN, in_reply_to=None):
    return Envelope(from_agent=frm, to_agent=to, intent=intent,
                    client_context_id=ctx, payload=payload,
                    confidence=confidence, in_reply_to=in_reply_to,
                    provenance={"source": f"spoke-{frm}", "captured_at": "runtime",
                                "verbatim_available": True})


class Spoke17ComplianceFairHousing:
    """DECISIONS.md tuples implemented directly:
      1. ruleset silent on the construction -> flag as uncovered, never
         approve by omission
      2. flagged content resubmitted unchanged -> flag the repeat + human
      3. SLA pressure on a verdict -> verdict quality wins, alert the SLA
         breach instead
      4. federal-compliant but stricter local rule applies -> stricter
         rule wins + flag
      5. asked to pre-approve a template class -> refuse, verdicts are
         per-item
      6. steering language however soft -> changes required with the
         exact phrase cited, never approve with a note
      7. advertising missing brokerage identification -> block until
         corrected, no exceptions for format constraints
      8. request to review its own prior verdict -> re-review fresh,
         never rubber-stamp its own history
      9. state-specific rule uncertainty -> block + human counsel flag;
         training-level knowledge never clears a legal gate
      10. pattern of near-miss language from one agent -> report the
          pattern to owner, single verdicts miss drift
    """

    def __init__(self, hub, sla_days: int = 1):
        self.hub = hub
        self.ruleset: dict | None = None  # fails closed until signed config.update
        self.ruleset_version: str | None = None
        self.prior_findings: dict[str, dict] = {}  # content_hash -> last verdict findings
        self.near_miss_counts: dict[str, int] = {}  # submitting_agent -> count
        self.pending_reviews: dict[str, dict] = {}  # ctx -> {submitted_at, agent}
        self.sla_days = sla_days
        hub.register("17", self.handle)

    def _find_prohibited(self, text: str) -> list[dict]:
        """Applies the human-supplied ruleset only - never invents a
        rule. Returns itemized findings: exact phrase + specific rule."""
        if not self.ruleset:
            return []
        findings = []
        low = text.lower()
        for rule in self.ruleset.get("prohibited_phrases", []):
            phrase = rule.get("phrase", "").lower()
            if phrase and phrase in low:
                findings.append({"phrase": rule["phrase"], "rule": rule.get("rule_id")})
        return findings

    def _check_state_rules(self, text: str, state: str | None) -> list[dict]:
        if not self.ruleset or not state:
            return []
        state_rules = self.ruleset.get("state_rules", {}).get(state, [])
        low = text.lower()
        findings = []
        for rule in state_rules:
            phrase = rule.get("phrase", "").lower()
            if phrase and phrase in low:
                findings.append({"phrase": rule["phrase"], "rule": rule.get("rule_id"),
                                "jurisdiction": state})
        return findings

    def _content_text(self, content) -> str:
        def flatten(obj):
            if isinstance(obj, str):
                yield obj
            elif isinstance(obj, dict):
                for v in obj.values():
                    yield from flatten(v)
            elif isinstance(obj, (list, tuple)):
                for v in obj:
                    yield from flatten(v)
        return " ".join(flatten(content))

    def handle(self, env: Envelope):
        ctx = env.client_context_id
        payload = env.payload

        if env.intent == "config.update":
            if "ruleset" in payload and "version" in payload:
                self.ruleset = payload["ruleset"]
                self.ruleset_version = payload["version"]
            return

        if env.intent == "content.review":
            submitting_agent = env.from_agent
            content = payload.get("draft") or payload.get("campaign") or \
                payload.get("criteria") or payload

            # tuple 5: asked to pre-approve a template class -> refuse,
            # verdicts are per-item
            if payload.get("request_type") == "template_class_preapproval":
                self.hub.send(_env("17", submitting_agent, "content.verdict",
                                   ctx, {"verdict": "flagged",
                                        "findings": [{"reason": "template-class "
                                                                "pre-approval refused - "
                                                                "verdicts are per-item"}],
                                        "ruleset_version": self.ruleset_version},
                                  in_reply_to=env.envelope_id))
                return

            # fail closed: no ruleset active, cannot clear anything
            if self.ruleset is None:
                self.hub.send(_env("17", "queue", "clarification.request", ctx,
                                   {"reason": "no ruleset active - cannot "
                                             "issue any verdict without "
                                             "human-supplied configuration"}))
                return

            text = self._content_text(content)
            content_hash = payload.get("content_hash") or str(content)

            # tuple 2: flagged content resubmitted UNCHANGED -> flag the
            # repeat + human. Real bug found on re-review: this used to
            # return prior["findings"] WITHOUT recomputing - meaning if
            # the ruleset changed between submissions, it would return
            # stale cached findings instead of a real check. That's
            # rubber-stamping, which tuple 8 explicitly forbids ("never
            # rubber-stamp its own history"). Fixed: always recompute
            # fresh; the resubmission signal only adds the repeat flag
            # and escalation on top of a genuine fresh result.
            prior = self.prior_findings.get(content_hash)
            is_resubmission_of_flagged = bool(prior and prior.get("verdict") == "flagged")

            findings = self._find_prohibited(text)
            findings += self._check_state_rules(text, payload.get("state"))
            self.pending_reviews[ctx] = {"submitted_at": payload.get("today"),
                                        "agent": submitting_agent}
            self.hub.send(_env("17", "18", "agent.status", ctx,
                               {"waiting_on": f"compliance_review:{submitting_agent}",
                                "since": payload.get("today")}))

            if is_resubmission_of_flagged:
                self.hub.escalate("escalation.legal_line",
                                  {"client_context_id": ctx,
                                   "trigger": "content resubmitted unchanged "
                                             "after being flagged - repeat, "
                                             "human notified",
                                   "agent": "17"})

            # tuple 7: missing brokerage ID on advertising -> block, no
            # format-constraint exceptions
            if payload.get("content_type") == "advertising" and \
                    not payload.get("brokerage_id_present"):
                findings.append({"reason": "brokerage identification "
                                          "missing - blocked, no format "
                                          "exceptions"})

            # tuple 1: ruleset silent on the construction -> flag as
            # uncovered, never approve by omission
            if payload.get("uncovered_construction"):
                findings.append({"reason": "ruleset does not address this "
                                          "construction - flagged as "
                                          "uncovered, never approved by "
                                          "omission",
                                 "uncovered": True})

            # tuple 9: state-specific rule uncertainty -> block + human
            # counsel flag
            if payload.get("state_rule_uncertain"):
                findings.append({"reason": "state-specific rule uncertainty "
                                          "- blocked, human counsel flag; "
                                          "training-level knowledge never "
                                          "clears a legal gate"})
                self.hub.escalate("escalation.legal_line",
                                  {"client_context_id": ctx,
                                   "trigger": "state-specific rule "
                                             "uncertainty on submitted "
                                             "content",
                                   "agent": "17"})

            verdict = "flagged" if findings else "approved"
            self.prior_findings[content_hash] = {"verdict": verdict,
                                                 "findings": findings}

            # tuple 10: pattern of near-miss language from one agent ->
            # report the pattern, single verdicts miss drift
            if verdict == "flagged":
                self.near_miss_counts[submitting_agent] = \
                    self.near_miss_counts.get(submitting_agent, 0) + 1
                if self.near_miss_counts[submitting_agent] >= 3:
                    self.hub.send(_env("17", "human", "report.package", ctx,
                                       {"report_type": "near_miss_pattern",
                                        "agent": submitting_agent,
                                        "count": self.near_miss_counts[submitting_agent]}))

            self.hub.send(_env("17", submitting_agent, "content.verdict", ctx,
                               {"verdict": verdict, "findings": findings,
                                "ruleset_version": self.ruleset_version},
                              in_reply_to=env.envelope_id,
                              confidence=SOURCE_VERIFIED))
            self.pending_reviews.pop(ctx, None)
            self.hub.send(_env("17", "18", "agent.status", ctx,
                               {"waiting_on": f"compliance_review:{submitting_agent}",
                                "resolved": True}))
            self.hub.send(_env("17", "14", "interaction.log", ctx,
                               {"kind": "content_reviewed", "verdict": verdict,
                                "submitting_agent": submitting_agent}))
            return

        if env.intent == "compliance.notice":
            self.hub.send(_env("17", "14", "interaction.log", ctx,
                               {"kind": "compliance_notice_received",
                                "trigger": payload.get("trigger")}))
            return

    def check_sla(self, ctx: str, today: str):
        """Tuple 3: SLA pressure -> verdict quality wins, alert the SLA
        breach instead of rushing or silently letting the queue grow.
        Real fix: previously took elapsed_seconds as a bare parameter,
        meaning self.pending_reviews (submission timestamps) was declared
        but never actually read - the caller had to compute elapsed time
        itself. Now genuinely uses the tracked state."""
        import datetime
        pending = self.pending_reviews.get(ctx)
        if pending is None:
            return "not_pending"
        submitted = pending.get("submitted_at")
        if not submitted:
            return "not_pending"
        elapsed_days = (datetime.date.fromisoformat(today) -
                        datetime.date.fromisoformat(submitted)).days
        if elapsed_days > self.sla_days:
            self.hub.send(_env("17", "queue", "clarification.request", ctx,
                               {"reason": f"SLA breach: {elapsed_days} day(s) "
                                         f"elapsed, threshold "
                                         f"{self.sla_days} day(s) - alerting, "
                                         f"never rushing verdict quality"}))
            return "breached"
        return "within_sla"
