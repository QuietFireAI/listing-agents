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
      3. two authorized changes conflict -> execute neither, human
      5. status change without required artifact -> refuse, artifact first
      6. status change without signed listing.change.authorized -> refuse,
         verbal/email is not authority
      8. price in authorized change conflicts with agreement amendment on
         file -> halt, both documents to human
      9. comp-offer field present anywhere -> leave blank, flag any
         request to fill it (NAR 8/17/24 rule)
      10. listing data vs marketing asset disagree -> MLS record is
          truth, corrected listing.data sent to 04 to rebuild against,
          discrepancy logged
      11. withdrawal requested while under contract -> halt, 17 + human

    NOT implemented (found during review, 2026-07-16), and not a code bug
    to silently patch - these need a real external integration that
    doesn't exist anywhere in this system yet, not a fake one built to
    make the docstring's old claim true:
      2. syndicated portal differs from MLS -> correct source record,
         re-verify every portal
      4. portal still stale after verification window -> log + notify
         human, never mark complete
      7. MLS rejects an entry on a rule -> log raw rejection, human,
         never adjust data to pass validation
    All three require a real syndication/MLS feedback channel (Zillow,
    Realtor.com, Redfin, or the MLS system itself) to ever fire - there is
    currently no inbound intent or code path anywhere in this identity
    for "portal sync result" or "MLS entry rejected" to reach this agent
    at all. Building real handling for these without a real integration
    to build it against would be exactly the kind of stub Jeff's rules
    forbid. Flagging honestly instead: this is a genuine, structural gap,
    not something this pass can close.
    """

    def __init__(self, hub, required_artifacts: dict[str, str] | None = None):
        self.hub = hub
        self.mls_records: dict[str, dict] = {}  # ctx -> current MLS record
        self.property_data: dict[str, dict] = {}  # ctx -> intake package
        self.under_contract: set[str] = set()
        self.go_live_authorized: set[str] = set()
        # tuple 3: two authorized changes conflict -> execute neither, human.
        # ctx -> field -> {"value":..., "authorizing_identity":...} - the
        # last SUCCESSFULLY EXECUTED authorized change per field. A new
        # authorized change for a field already set by a DIFFERENT signer
        # with a DIFFERENT value is a genuine conflict (two people giving
        # contradictory signed instructions); the same signer changing
        # their own prior instruction is a normal update, not a conflict.
        self.last_authorization: dict[str, dict[str, dict]] = {}
        # status -> required artifact key in payload, e.g. "sold" needs
        # a closing/settlement artifact on file first
        self.required_artifacts = required_artifacts or {
            "sold": "closing_artifact", "pending": "signed_contract_artifact"}
        hub.register("05", self.handle)

    def _conflicting_authorization(self, ctx: str, field: str, value,
                                    authorizing_identity: str) -> dict | None:
        """Returns the prior conflicting record if this authorized change
        conflicts with the last one executed for this field, else None."""
        prior = self.last_authorization.get(ctx, {}).get(field)
        if (prior and prior["value"] != value
                and prior["authorizing_identity"] != authorizing_identity):
            return prior
        return None

    def _record_authorization(self, ctx: str, field: str, value,
                               authorizing_identity: str):
        self.last_authorization.setdefault(ctx, {})[field] = {
            "value": value, "authorizing_identity": authorizing_identity}


    def handle(self, env: Envelope):
        ctx = env.client_context_id

        if env.intent == "asset.release":
            draft = env.payload.get("draft", {})
            # tuple 10: listing data vs marketing asset disagree -> MLS
            # record is truth, corrected version sent back, discrepancy
            # logged. "Corrected asset.release" was never buildable
            # literally - 05 has no legal route to send asset.release
            # (only 04 does, per routes.json). The real fix: 05 sends a
            # corrected listing.data back to 04 (05's own real, already-
            # legal edge) carrying the MLS-truth values, so 04 can rebuild
            # the asset against the corrected facts and resubmit through
            # compliance - not silently log-and-move-on.
            record = self.mls_records.get(ctx, {})
            disagreements = []
            corrected_fields = {}
            for fact in draft.get("facts", []):
                if "sq ft" in fact["text"] and record.get("sqft"):
                    stated = fact["text"].split(" sq ft")[0]
                    if stated.isdigit() and int(stated) != record["sqft"]:
                        disagreements.append(fact["text"])
                        corrected_fields["sqft"] = record["sqft"]
            if disagreements:
                self.hub.ingest_spoke_trace(
                    "05", env.envelope_id,
                    thought=f"marketing asset disagrees with MLS record on "
                            f"{disagreements} - MLS record is truth, "
                            f"sending 04 the corrected listing.data package "
                            f"to rebuild the asset against, not just logging "
                            f"the discrepancy",
                    result="corrected: mls_is_truth")
                self.hub.send(_env("05", "04", "listing.data", ctx,
                                   {**self.property_data.get(ctx, {}),
                                    **corrected_fields,
                                    "correction_of_discrepancy": disagreements}))

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
                # Market Data (10) needs new/changed listings too - SKILL.md
                # has documented this edge all along; the code just never
                # fired it.
                self.hub.send(_env("05", "10", "listing.data", ctx,
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
                self.hub.send(_env("05", "18", "agent.status", ctx,
                                   {"waiting_on": "photography_deliverable",
                                    "since": env.payload.get("today")}))
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

            # tuple 3: two authorized changes conflict -> execute neither,
            # human. A different signer authorizing a different value for
            # a field this context already has an executed value for, from
            # a DIFFERENT signer, is a real conflict - not the same party
            # updating their own prior instruction.
            if field in ("status", "price"):
                new_value = change.get("value")
                conflict = self._conflicting_authorization(
                    ctx, field, new_value, authorizing_identity)
                if conflict:
                    self.hub.ingest_spoke_trace(
                        "05", env.envelope_id,
                        thought=f"authorized {field}={new_value!r} by "
                                f"{authorizing_identity!r} conflicts with "
                                f"already-executed {field}="
                                f"{conflict['value']!r} by "
                                f"{conflict['authorizing_identity']!r} - "
                                f"two different signers, contradictory "
                                f"instructions - executing neither, human",
                        result="held: conflicting authorized changes")
                    self.hub.send(_env("05", "queue", "clarification.request",
                                       ctx, {"reason": "two authorized changes "
                                                       "conflict for the same "
                                                       "field",
                                            "field": field,
                                            "new_value": new_value,
                                            "new_authorizer": authorizing_identity,
                                            "existing_value": conflict["value"],
                                            "existing_authorizer": conflict["authorizing_identity"]}))
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
                    self.hub.send(_env("05", "17", "compliance.notice", ctx,
                                       {"trigger": "withdrawal requested "
                                                  "while under contract",
                                        "agent": "05"}))
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
                self._record_authorization(ctx, "status", new_status,
                                           authorizing_identity)
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
                self._record_authorization(ctx, "price", new_price,
                                           authorizing_identity)
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
            self.hub.send(_env("05", "18", "agent.status", ctx,
                               {"waiting_on": "photography_deliverable",
                                "resolved": True}))
            return
