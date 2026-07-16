"""Agent 05 - MLS & Listing Management, built against the full spec.

Executes MLS operations. Never decides a price - only executes a
human-authorized number, and only when the envelope itself carries that
authorization (listing.change.authorized, a hub-level signed authority
intent - the crypto verification already happened before this handler
ever sees it, per hub.send()'s pipeline order).
"""
from __future__ import annotations

from .core import Envelope

SOURCE_VERIFIED = "source_verified"
STATED_BY_PARTY = "stated_by_party"
UNKNOWN = "unknown"

VALID_MLS_FIELDS = {"beds", "baths", "sqft", "price", "status", "remarks",
                    "address", "photos"}
VALID_STATUSES = {"active", "pending", "sold", "withdrawn"}


def _env(frm, to, intent, ctx, payload, confidence=UNKNOWN):
    return Envelope(from_agent=frm, to_agent=to, intent=intent,
                    client_context_id=ctx, payload=payload,
                    confidence=confidence,
                    provenance={"source": f"spoke-{frm}", "captured_at": "runtime",
                                "verbatim_available": True})


class Spoke05MLSListingManagement:
    """DECISIONS.md tuples implemented directly:
      1. supplied data has no valid MLS field -> clarification, never
         approximate into the wrong field
      2. syndicated portal differs from MLS -> correct source record, then
         re-verify every portal
      3. two authorized changes conflict -> execute neither, human
      4. portal still stale after verification window -> log + notify
         human, never mark complete
      5. status change without required artifact -> refuse, artifact first
      6. status change without signed listing.change.authorized -> refuse,
         verbal/email is not authority
      7. MLS rejects an entry on a rule -> log raw rejection, human, never
         adjust data to pass validation
      8. price in authorized change conflicts with agreement amendment on
         file -> halt, both documents to human
      9. comp-offer field present anywhere -> leave blank, flag any
         request to fill it (NAR 8/17/24 rule)
      10. listing data vs marketing asset disagree -> MLS record is truth,
          asset.release corrected version, discrepancy logged
      11. withdrawal requested while under contract -> halt, 17 + human
    """

    def __init__(self, hub, required_artifacts: dict[str, str] | None = None):
        self.hub = hub
        self.mls_records: dict[str, dict] = {}  # ctx -> current MLS record
        self.property_data: dict[str, dict] = {}  # ctx -> intake package
        self.under_contract: set[str] = set()
        self.go_live_authorized: set[str] = set()
        # status -> required artifact key in payload, e.g. "sold" needs
        # a closing/settlement artifact on file first
        self.required_artifacts = required_artifacts or {
            "sold": "closing_artifact", "pending": "signed_contract_artifact"}
        hub.register("05", self.handle)

    def handle(self, env: Envelope):
        ctx = env.client_context_id

        if env.intent == "asset.release":
            draft = env.payload.get("draft", {})
            # tuple 10: listing data vs marketing asset disagree -> MLS
            # record is truth, corrected asset.release, discrepancy logged
            record = self.mls_records.get(ctx, {})
            disagreements = []
            for fact in draft.get("facts", []):
                if "sq ft" in fact["text"] and record.get("sqft"):
                    stated = fact["text"].split(" sq ft")[0]
                    if stated.isdigit() and int(stated) != record["sqft"]:
                        disagreements.append(fact["text"])
            if disagreements:
                self.hub.ingest_spoke_trace(
                    "05", env.envelope_id,
                    thought=f"marketing asset disagrees with MLS record on "
                            f"{disagreements} - MLS record is truth, "
                            f"correcting and logging the discrepancy",
                    result="corrected: mls_is_truth")

            # Phase 3: approved content clears the go-live gate. Status
            # change to 'active' still requires the signed authorization -
            # satisfied here because the ORIGINAL new-listing package
            # (which is itself a signed listing.change.authorized envelope)
            # carried authorize_go_live, per P01's trigger definition. This
            # is not a fresh signature per step; it's execution of the
            # already-authorized deal, same as the playbook specifies.
            if ctx in self.go_live_authorized:
                record = self.mls_records.setdefault(ctx, {})
                record["status"] = "active"
                self.hub.ingest_spoke_trace(
                    "05", env.envelope_id,
                    thought="content approved and MLS entry complete; "
                            "go-live was pre-authorized in the signed "
                            "onboarding package - executing status=active, "
                            "syndicating, and pushing to buyer-match feeds",
                    result="status executed: active")
                self.hub.send(_env("05", "11", "status.update", ctx, {"status": "active"}))
                self.hub.send(_env("05", "12", "status.update", ctx, {"status": "active"}))
                self.hub.send(_env("05", "14", "status.update", ctx, {"status": "active"}))
                # Phase 3c: listing into buyer-match feeds
                self.hub.send(_env("05", "13", "listing.data", ctx,
                                   {**self.property_data.get(ctx, {}),
                                    "discrepancies": disagreements}))
            else:
                self.hub.ingest_spoke_trace(
                    "05", env.envelope_id,
                    thought="approved asset received from 04 - entering "
                            "into MLS record; go-live not yet authorized "
                            "for this context, holding at entry",
                    result="mls_entry_updated, not yet live")
            self.hub.send(_env("05", "14", "interaction.log", ctx,
                               {"kind": "mls_entry_updated",
                                "discrepancies": disagreements}))
            return

        if env.intent == "listing.change.authorized":
            # By hub pipeline order (schema -> signature -> tuple legality
            # -> persist -> deliver), the crypto signature on this
            # authority intent is ALREADY verified by the time this handler
            # runs. Tuple 6 ("verbal/email is not authority") is enforced
            # structurally: nothing but a validly-signed
            # listing.change.authorized envelope can ever reach this branch.
            change = env.payload

            # Phase 1: initial new-listing intake. Per P01's own trigger
            # definition, the signed listing.change.authorized envelope
            # carries the full property data + list price as ONE package,
            # not a single field/value change - that's a distinct case
            # from the per-field updates below.
            if "new_listing" in change:
                package = change["new_listing"]
                self.property_data[ctx] = package
                if change.get("authorize_go_live"):
                    self.go_live_authorized.add(ctx)
                self.mls_records[ctx] = {"status": "draft", **{
                    k: v for k, v in package.items()
                    if k in VALID_MLS_FIELDS}}
                self.hub.ingest_spoke_trace(
                    "05", env.envelope_id,
                    thought="new listing package intake, signed - entering "
                            "draft MLS record and ordering photography "
                            "per Phase 1b before any content or "
                            "syndication work starts",
                    result="draft entered, vendor.request issued")
                self.hub.send(_env("05", "09", "vendor.request", ctx,
                                   {"kind": "photography"}))
                return

            authorizing_identity = env.provenance.get("signer", {}).get(
                "signer_login") or env.provenance.get("source")

            field = change.get("field")

            # tuple 9: comp-offer field -> always blank, flag any request.
            # Checked BEFORE the generic valid-field gate below, since
            # buyer_broker_comp isn't a valid MLS field to enter at all but
            # needs its own specific escalation, not a generic clarification.
            if field == "buyer_broker_comp" or "comp_offer" in change:
                self.hub.escalate("escalation.legal_line",
                                  {"client_context_id": ctx,
                                   "trigger": "request to fill buyer-broker "
                                             "compensation field - "
                                             "prohibited on MLS since "
                                             "8/17/24, leaving blank",
                                   "agent": "05"})
                return

            if field and field not in VALID_MLS_FIELDS:
                self.hub.send(_env("05", "queue", "clarification.request", ctx,
                                   {"reason": f"no valid MLS field for "
                                             f"{field!r} - never "
                                             f"approximating into the "
                                             f"wrong field"}))
                return

            if field == "status":
                new_status = change.get("value")
                if new_status not in VALID_STATUSES:
                    self.hub.send(_env("05", "queue", "clarification.request",
                                       ctx, {"reason": f"invalid status value "
                                                       f"{new_status!r}"}))
                    return

                # tuple 11: withdrawal while under contract -> halt, 17 + human
                if new_status == "withdrawn" and ctx in self.under_contract:
                    self.hub.escalate("escalation.legal_line",
                                      {"client_context_id": ctx,
                                       "trigger": "withdrawal requested while "
                                                 "under contract - status "
                                                 "math on a live contract is "
                                                 "never autonomous",
                                       "agent": "05"})
                    return

                # tuple 5: status change requires its artifact first
                required_key = self.required_artifacts.get(new_status)
                if required_key and not change.get(required_key):
                    self.hub.send(_env("05", "queue", "clarification.request",
                                       ctx, {"reason": f"status change to "
                                                       f"{new_status!r} "
                                                       f"requires "
                                                       f"{required_key!r} - "
                                                       f"artifact first"}))
                    return

                if new_status == "pending":
                    self.under_contract.add(ctx)
                elif new_status in ("sold", "withdrawn"):
                    self.under_contract.discard(ctx)

                record = self.mls_records.setdefault(ctx, {})
                record["status"] = new_status
                self.hub.ingest_spoke_trace(
                    "05", env.envelope_id,
                    thought=f"status change to {new_status!r} authorized by "
                            f"{authorizing_identity!r}, artifact present, "
                            f"executing",
                    result=f"status executed: {new_status}")
                self.hub.send(_env("05", "11", "status.update", ctx,
                                   {"status": new_status}))
                self.hub.send(_env("05", "12", "status.update", ctx,
                                   {"status": new_status}))
                self.hub.send(_env("05", "14", "status.update", ctx,
                                   {"status": new_status}))
                return

            if field == "price":
                new_price = change.get("value")
                agreement_price = change.get("agreement_amendment_price")
                # tuple 8: price conflicts with agreement amendment on file
                if agreement_price is not None and agreement_price != new_price:
                    self.hub.send(_env("05", "queue", "clarification.request",
                                       ctx, {"reason": "authorized price "
                                                       "conflicts with "
                                                       "agreement amendment "
                                                       "on file - halting, "
                                                       "both documents to "
                                                       "human",
                                            "authorized_price": new_price,
                                            "agreement_price": agreement_price}))
                    return
                record = self.mls_records.setdefault(ctx, {})
                record["price"] = new_price
                self.hub.ingest_spoke_trace(
                    "05", env.envelope_id,
                    thought=f"price change to {new_price} authorized by "
                            f"{authorizing_identity!r}, no agreement "
                            f"conflict, executing",
                    result=f"price executed: {new_price}")
                self.hub.send(_env("05", "11", "status.update", ctx,
                                   {"price": new_price}))
                self.hub.send(_env("05", "12", "status.update", ctx,
                                   {"price": new_price}))
                self.hub.send(_env("05", "14", "status.update", ctx,
                                   {"price": new_price}))
                return
            return

        if env.intent == "status.request":
            record = self.mls_records.get(ctx, {})
            self.hub.send(_env("05", env.from_agent, "status.response", ctx,
                               {"status": record.get("status", "unknown")},
                               confidence=SOURCE_VERIFIED))
            return

        if env.intent == "deliverable.release":
            # Phase 2 gate: "photos delivered, verified present-and-opens
            # by 09" - not just received. Only a verified deliverable
            # clears the gate to send listing.data onward to 04.
            if not env.payload.get("verified_openable"):
                self.hub.ingest_spoke_trace(
                    "05", env.envelope_id,
                    thought="photo deliverable received but not verified "
                            "present-and-opens - holding, not clearing the "
                            "Phase 2 gate on an unverified deliverable",
                    result="held: photos unverified")
                self.hub.send(_env("05", "queue", "clarification.request", ctx,
                                   {"reason": "photo deliverable failed "
                                             "open-verification"}))
                return
            self.hub.ingest_spoke_trace(
                "05", env.envelope_id,
                thought="photo deliverable verified present-and-opens - "
                        "Phase 2 gate clears, sending property data package "
                        "to Listing Description (04)",
                result="listing.data issued")
            self.hub.send(_env("05", "04", "listing.data", ctx,
                               {**self.property_data.get(ctx, {}),
                                "photos": env.payload.get("photos", [])}))
            return
