"""Agent 15 - Financial Tracking, built against the full spec.

Highest confidentiality sensitivity in the swarm. Trust/escrow accounting
is out of scope entirely - not a judgment call, a hard boundary. Commission
math computes on signed documents only; unsigned amendments never enter
the number, only a labeled projection.
"""
from __future__ import annotations

from .core import Envelope

SOURCE_VERIFIED = "source_verified"
STATED_BY_PARTY = "stated_by_party"
UNKNOWN = "unknown"

_TRUST_ESCROW_WORDS = ("earnest money", "trust account", "escrow",
                      "wire the funds", "disbursement")


def _env(frm, to, intent, ctx, payload, confidence=UNKNOWN, in_reply_to=None):
    return Envelope(from_agent=frm, to_agent=to, intent=intent,
                    client_context_id=ctx, payload=payload,
                    confidence=confidence, in_reply_to=in_reply_to,
                    provenance={"source": f"spoke-{frm}", "captured_at": "runtime",
                                "verbatim_available": True})


class Spoke15FinancialTracking:
    """DECISIONS.md tuples implemented directly:
      1. recorded commission differs from contract language -> escalate
         with the clause verbatim
      2. expense fits two categories with different tax flags -> flag
         both, accountant decides
      3. ROI attribution claimed by two sources -> report both claims
      4. a month of data is missing -> label missing, never interpolate
      5. any commission-language question -> escalation.legal_line
      6. commission math depends on an unsigned amendment -> compute on
         signed docs only, projection labeled as such
      7. expense without receipt artifact -> unverified, never promoted
         to verified by time
      8. pipeline value including UNKNOWN-tier leads -> exclude them,
         note the exclusion
      9. disbursement instruction by email -> wire-adjacent, full stop
         legal line, voice-verified human process only
      10. forecast past data horizon -> decline the horizon, deliver
          what data supports
    """

    def __init__(self, hub):
        self.hub = hub
        self.commissions: dict[str, dict] = {}  # ctx -> {amount, signed, source}
        self.expenses: dict[str, list[dict]] = {}
        self.roi_claims: dict[str, list[dict]] = {}  # attribution_key -> claims
        self.data_months_present: set[str] = set()
        hub.register("15", self.handle)

    def _trust_escrow_hit(self, text: str) -> str | None:
        low = text.lower()
        for w in _TRUST_ESCROW_WORDS:
            if w in low:
                return text.strip()
        return None

    def handle(self, env: Envelope):
        ctx = env.client_context_id
        payload = env.payload

        if env.intent == "transaction.closed":
            amount = payload.get("commission_amount")
            signed = payload.get("signed_docs_only", False)
            source = payload.get("source", "closing_statement")
            if not signed:
                # tuple 6: unsigned amendment -> compute on signed docs
                # only, projection labeled as such
                self.commissions[ctx] = {"amount": amount, "signed": False,
                                         "labeled": "projection - unsigned amendment"}
            else:
                self.commissions[ctx] = {"amount": amount, "signed": True,
                                         "source": source}
            self.hub.send(_env("15", "14", "record.request", ctx,
                               {"dedupe_key": ctx}))
            return

        if env.intent == "record.response":
            # Cross-check the commission this agent computed against 14's
            # actual log entries - was a comment describing this with zero
            # code behind it. A discrepancy here is exactly the class of
            # thing tuple 1 exists to catch, just sourced from the
            # system-of-record instead of a human-reported dispute.
            commission = self.commissions.get(ctx)
            entries = payload.get("entries", [])
            if commission is None or not entries:
                return
            logged_amounts = [e["payload"].get("amount") for e in entries
                             if e.get("kind") == "transaction.closed"
                             and e["payload"].get("amount") is not None]
            if logged_amounts and commission["amount"] not in logged_amounts:
                self.hub.escalate("escalation.legal_line",
                                  {"client_context_id": ctx,
                                   "trigger": f"commission recorded here "
                                             f"({commission['amount']}) "
                                             f"does not match 14's logged "
                                             f"amount(s) {logged_amounts} - "
                                             f"discrepancy, not silently "
                                             f"reconciled",
                                   "agent": "15"})
            return

        if env.intent == "report.package":
            # Marketing spend (platform, source_verified) + referral
            # attribution (CRM, stated_by_party) - both need provenance
            # preserved separately, per job component #2's explicit
            # requirement. Was a comment with zero code behind it.
            spend = payload.get("marketing_spend")
            attribution = payload.get("referral_attribution")
            if spend is None and attribution is None:
                return
            roi = None
            if spend is not None and attribution is not None:
                attributed_value = attribution.get("attributed_value", 0)
                roi = (attributed_value - spend) / spend if spend else None
            self.hub.send(_env("15", "human", "report.package", ctx,
                               {"report_type": "roi_tracking",
                                "marketing_spend": spend,
                                "marketing_spend_source": "platform_export",
                                "referral_attribution": attribution,
                                "referral_attribution_source": "crm_referral_data",
                                "roi": roi}))
            return

        if env.intent == "config.update":
            text_fields = " ".join(str(v) for v in payload.values()
                                   if isinstance(v, str))
            hit = self._trust_escrow_hit(text_fields)
            if hit and "disbursement" in text_fields.lower():
                # tuple 9: disbursement by email, wire-adjacent -> full
                # stop, voice-verified human process only
                self.hub.escalate("escalation.legal_line",
                                  {"client_context_id": ctx,
                                   "trigger": f"disbursement instruction "
                                             f"received - wire-adjacent, "
                                             f"full stop, voice-verified "
                                             f"human process only: {hit!r}",
                                   "agent": "15"})
                return
            if hit:
                # trust/escrow accounting is out of scope entirely
                self.hub.escalate("escalation.legal_line",
                                  {"client_context_id": ctx,
                                   "trigger": f"trust/escrow accounting "
                                             f"request - out of scope "
                                             f"entirely, regulated "
                                             f"brokerage function: {hit!r}",
                                   "agent": "15"})
                return

            if "commission_language_question" in payload:
                # tuple 5: any commission-language question -> Legal Line
                self.hub.escalate("escalation.legal_line",
                                  {"client_context_id": ctx,
                                   "trigger": payload["commission_language_question"],
                                   "agent": "15"})
                return

            if "commission_dispute" in payload:
                dispute = payload["commission_dispute"]
                recorded = dispute.get("recorded")
                contract_clause = dispute.get("contract_clause")
                if recorded != dispute.get("contract_figure"):
                    # tuple 1: differs from contract -> escalate with
                    # clause verbatim
                    self.hub.escalate("escalation.legal_line",
                                      {"client_context_id": ctx,
                                       "trigger": f"recorded commission "
                                                 f"{recorded} differs from "
                                                 f"contract figure - clause: "
                                                 f"{contract_clause!r}",
                                       "agent": "15"})
                return

            if "add_expense" in payload:
                expense = payload["add_expense"]
                categories = expense.get("categories", [])
                tax_flags = expense.get("tax_flags", [])
                receipt_on_file = expense.get("receipt_on_file", False)
                if len(categories) > 1 and len(set(tax_flags)) > 1:
                    # tuple 2: two categories, different tax flags -> flag
                    # both, accountant decides
                    self.hub.ingest_spoke_trace(
                        "15", env.envelope_id,
                        thought=f"expense fits categories {categories} "
                                f"with differing tax flags {tax_flags} - "
                                f"flagging both, accountant decides",
                        result="flagged: dual_category_tax_conflict")
                entry = {**expense,
                         "verified": bool(receipt_on_file)}
                # tuple 7: no receipt -> unverified, never promoted by time
                self.expenses.setdefault(ctx, []).append(entry)
                return

            if "roi_claim" in payload:
                claim = payload["roi_claim"]
                key = claim.get("attribution_key")
                self.roi_claims.setdefault(key, []).append(claim)
                if len(self.roi_claims[key]) > 1:
                    sources = {c.get("source") for c in self.roi_claims[key]}
                    if len(sources) > 1:
                        # tuple 3: two sources claim same attribution ->
                        # report both, never pick
                        self.hub.send(_env("15", "human", "report.package",
                                           ctx, {"report_type": "roi_conflict",
                                                "claims": self.roi_claims[key]}))
                return

            if "data_month_received" in payload:
                self.data_months_present.add(payload["data_month_received"])
                return

            if "generate_pnl" in payload:
                requested_months = payload["generate_pnl"].get("months", [])
                missing = [m for m in requested_months
                          if m not in self.data_months_present]
                report = {"report_type": "pnl", "months": requested_months,
                         "missing_months": missing,
                         "commissions": dict(self.commissions),
                         "expenses": dict(self.expenses)}
                self.hub.send(_env("15", "human", "report.package", ctx, report))
                return

            if "pipeline_value_request" in payload:
                leads = payload["pipeline_value_request"].get("leads", [])
                # tuple 8: exclude UNKNOWN-tier leads, note the exclusion
                excluded = [l for l in leads if l.get("tier") == "UNKNOWN"]
                included = [l for l in leads if l.get("tier") != "UNKNOWN"]
                total = sum(l.get("value", 0) for l in included)
                self.hub.send(_env("15", "human", "report.package", ctx,
                                   {"report_type": "pipeline_value",
                                    "total": total,
                                    "excluded_unknown_count": len(excluded),
                                    "note": "UNKNOWN-tier leads excluded - "
                                           "unknowns are not value"}))
                return

            if "forecast_request" in payload:
                horizon = payload["forecast_request"].get("horizon_months")
                data_horizon = payload["forecast_request"].get("data_horizon_months", 0)
                if horizon > data_horizon:
                    # tuple 10: forecast past data horizon -> decline the
                    # horizon, deliver what data supports
                    self.hub.send(_env("15", "human", "report.package", ctx,
                                       {"report_type": "forecast",
                                        "declined_horizon": horizon,
                                        "delivered_horizon": data_horizon,
                                        "note": "forecast horizon declined "
                                               "beyond what data supports"}))
                    return
                self.hub.send(_env("15", "human", "report.package", ctx,
                                   {"report_type": "forecast",
                                    "horizon": horizon}))
                return
            return
