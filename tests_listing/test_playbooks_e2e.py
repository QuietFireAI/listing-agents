"""End-to-end playbook execution - real hub, real spokes, real routing.

Gap named 2026-07-18: all per-agent tests were green while three
cross-agent contracts were dead (close_date never sent, commission chain
None-driven, 15<->14 request shape mismatch) - the exact defect class
only continuous flows catch. These tests run playbook flows as ONE
uninterrupted stream of hub traffic: every agent registered, every gate
crossed by real envelopes, completion asserted against artifacts (the
audit log and spoke state), never assurances.
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.signatures import Ed25519Signer, Ed25519Verifier
from dispatcher.sweep_runner import run_daily_sweeps
from dispatcher.listing_spokes import Spoke01LeadCapture, Spoke14CRMPipeline
from dispatcher.listing_spokes_02 import Spoke02LeadQualification
from dispatcher.listing_spokes_04 import Spoke04ListingDescription
from dispatcher.listing_spokes_05 import Spoke05MLSListingManagement
from dispatcher.listing_spokes_06 import Spoke06ShowingScheduler
from dispatcher.listing_spokes_07 import Spoke07TransactionCoordinator
from dispatcher.listing_spokes_08 import Spoke08DocumentCollection
from dispatcher.listing_spokes_09 import Spoke09VendorCoordination
from dispatcher.listing_spokes_10 import Spoke10MarketData
from dispatcher.listing_spokes_11 import Spoke11ClientCommunication
from dispatcher.listing_spokes_12 import Spoke12MarketingCampaign
from dispatcher.listing_spokes_13 import Spoke13BuyerSearchMatch
from dispatcher.listing_spokes_15 import Spoke15FinancialTracking
from dispatcher.listing_spokes_16 import Spoke16AfterCloseReferral
from dispatcher.listing_spokes_17 import Spoke17ComplianceFairHousing
from dispatcher.listing_spokes_18 import Spoke18CalendarTask

IDENTITY_ROUTES = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "identity", "routes.json")

RULESET = {"prohibited_phrases": [
    {"phrase": "perfect for families", "rule_id": "FH-STEER-1"}],
    "state_rules": {}}


def build_swarm(tmp_path):
    signer = Ed25519Signer()
    verifier = Ed25519Verifier(signer.public_key_bytes())
    hub = Hub(Routes(IDENTITY_ROUTES),
              AuditLog(os.path.join(tmp_path, f"a-{uuid.uuid4().hex[:6]}.jsonl")),
              signature_verifier=verifier.verifier())
    external_seen = []
    hub.register("external", lambda env: external_seen.append(env))
    hub.register("human", lambda env: None)
    spokes = {
        "01": Spoke01LeadCapture(hub, brokerage_scope={"L1", "MLS-1"}),
        "02": Spoke02LeadQualification(hub),
        "04": Spoke04ListingDescription(hub),
        "05": Spoke05MLSListingManagement(hub),
        "06": Spoke06ShowingScheduler(hub),
        "07": Spoke07TransactionCoordinator(hub),
        "08": Spoke08DocumentCollection(hub, expected_senders={
            "deal-1": {"closing_settlement_statement": {"title-co@example.com"}}}),
        "09": Spoke09VendorCoordination(
            hub, roster={"v-photo": {"kind": "photography",
                                     "license_expiry": "2027-01-01",
                                     "insurance_expiry": "2027-01-01",
                                     "regulated": False}}),
        "10": Spoke10MarketData(hub),
        "11": Spoke11ClientCommunication(hub),
        "12": Spoke12MarketingCampaign(hub),
        "13": Spoke13BuyerSearchMatch(hub),
        "14": Spoke14CRMPipeline(hub),
        "15": Spoke15FinancialTracking(hub),
        "16": Spoke16AfterCloseReferral(hub),
        "17": Spoke17ComplianceFairHousing(hub),
        "18": Spoke18CalendarTask(hub),
    }
    hub.on_turn_start()
    return hub, signer, spokes, external_seen


def signed(signer, to, intent, ctx, payload, frm="human"):
    env = Envelope(from_agent=frm, to_agent=to, intent=intent,
                   client_context_id=ctx, payload=payload,
                   provenance={"source": frm, "captured_at": "runtime",
                               "verbatim_available": True})
    signer.sign(env)
    return env


def spoke_env(frm, to, intent, ctx, payload):
    return Envelope(from_agent=frm, to_agent=to, intent=intent,
                    client_context_id=ctx, payload=payload,
                    provenance={"source": f"spoke-{frm}",
                                "captured_at": "runtime",
                                "verbatim_available": True})


def persisted(hub, intent=None):
    return [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"
            and (intent is None or e["intent"] == intent)]


def test_p01_new_listing_onboarding_all_three_phases(tmp_path):
    """P01 as one continuous run: setup -> production (photo gate,
    compliance gate) -> go-live (Clear Cooperation gate). Completion
    asserted against artifacts."""
    hub, signer, spokes, external = build_swarm(str(tmp_path))
    ctx = "listing-p01"

    # ruleset for 17 (signed authority config)
    hub.send(signed(signer, "17", "config.update", ctx,
                    {"ruleset": RULESET, "version": "r1"}))

    # ---- The playbook's REAL trigger: ONE signed listing.change.
    # authorized envelope carrying the full package + go-live
    # authorization. Everything after this is the swarm chaining itself;
    # the driver injects only external-world events. ----
    hub.send(signed(signer, "05", "listing.change.authorized", ctx,
                    {"new_listing": {"beds": 3, "sqft": 2000,
                                     "price": 500_000,
                                     "features": ["garage"],
                                     "photo_detected_features": ["garage"]},
                     "authorize_go_live": True,
                     "today": "2026-07-01"}))
    # 1a: agreement logged (14's context), driven alongside the trigger
    hub.send(spoke_env("07", "14", "interaction.log", ctx,
                       {"kind": "listing_agreement",
                        "consent": {"call": "yes", "text": "yes",
                                    "email": "yes"}}))
    # 1b proof: 05 ordered photography itself; roster selected the vendor
    assert spokes["09"].scheduled[ctx]["photography"]["vendor_id"] == "v-photo"
    assert any(e.intent == "vendor.schedule" for e in external), \
        "1b proof: vendor booking left the swarm"
    # 1d: seller onboarding message (consent recorded via 14 above)
    hub.send(spoke_env("06", "11", "client.message.request", ctx,
                       {"template": "seller_onboarding", "hour": 10}))
    assert any(e.intent == "client.message.send" for e in external), \
        "1d proof: onboarding message left the swarm"

    # ---- Phase 2 gate: photos delivered, verified present-and-opens ----
    hub.send(spoke_env("external", "09", "vendor.event", ctx,
                       {"kind": "photography",
                        "event_kind": "deliverable_report",
                        "doc_type": "photo_package",
                        "proof_artifact_present": True,
                        "content_hash": "photos-hash"}))
    # 2a proof: 09 released deliverables to 05
    assert persisted(hub, "deliverable.release"), "phase-2 gate never opened"

    # 2b happened WITHOUT the driver: 05 sent its stored property
    # package to 04 on the verified deliverable
    # 2c/2d: 04 drafted and submitted to 17 automatically
    assert persisted(hub, "content.review"), "2d: assets never reached 17"
    # 2e: verdict approved (no prohibited phrases in facts-only draft)
    verdicts = persisted(hub, "content.verdict")
    assert verdicts and verdicts[-1]["payload"]["verdict"] == "approved"
    # 2f: approved assets released to 05 and 12
    releases = persisted(hub, "asset.release")
    assert {r["to_agent"] for r in releases} == {"05", "12"}, \
        "2f: release must reach BOTH 05 and 12"

    # ---- Phase 3: go-live fired from the approved-asset loop (the
    # signed trigger pre-authorized it, per P01's own trigger
    # definition) - no additional driver step at all ----
    status_updates = persisted(hub, "status.update")
    receivers = {e["to_agent"] for e in status_updates}
    assert {"11", "12", "14"} <= receivers, \
        f"3a: status.update(active) must reach 11/12/14, got {receivers}"
    # 3b: Clear Cooperation satisfied -> campaign actually published
    assert any(e.intent == "campaign.publish" for e in external), \
        "3b: campaign never left the swarm after the gate opened"
    # 3c: listing into buyer-match feeds
    assert any(e["to_agent"] == "13" and e["intent"] == "listing.data"
               for e in persisted(hub)), "3c: 13 never got the listing"
    # 3e: seller informed - live notice through 11
    sends = [e for e in external if e.intent == "client.message.send"]
    assert len(sends) >= 2, "3e: seller live-notice never sent"

    # completion criteria: no dead letters, no unresolved integrity holds
    assert hub.queues["dead.letter"] == [], \
        f"completion: dead letters present: {hub.queues['dead.letter']}"
    chain = hub.audit.verify_chain()
    assert chain["ok"], f"audit chain broken: {chain}"


def test_p01_compliance_flag_halts_marketing_path(tmp_path):
    """P01 HITL gate: content.verdict flagged -> marketing halts. The
    flagged draft must never be released to 05/12."""
    hub, signer, spokes, external = build_swarm(str(tmp_path))
    ctx = "listing-flag"
    hub.send(signed(signer, "17", "config.update", ctx,
                    {"ruleset": RULESET, "version": "r1"}))
    # a draft that trips the fair-housing ruleset via human-requested copy
    hub.send(spoke_env("05", "04", "listing.data", ctx,
                       {"beds": 2, "features": ["garage"],
                        "requested_copy": "perfect for families",
                        "today": "2026-07-05"}))
    # steering language is structurally excluded from the draft, so the
    # verdict can be approved - but the tuple requires the request itself
    # was traced and never echoed. Assert the released draft NEVER
    # contains the steering phrase, whatever the verdict.
    for r in persisted(hub, "asset.release"):
        assert "perfect for families" not in str(r["payload"])
    # now a draft where the DATA ITSELF carries a prohibited phrase
    hub.send(spoke_env("05", "04", "listing.data", "listing-flag2",
                       {"beds": 2, "features": ["perfect for families"],
                        "today": "2026-07-05"}))
    v = [e["payload"]["verdict"] for e in persisted(hub, "content.verdict")
         if e["client_context_id"] == "listing-flag2"]
    # the healthy loop: 17 flags with the exact phrase, 04 applies the
    # verdict changes EXACTLY (tuple: never approve with a note),
    # resubmits, corrected draft approves
    assert "flagged" in v, "the steering phrase was never flagged"
    releases = [e for e in persisted(hub, "asset.release")
                if e["client_context_id"] == "listing-flag2"]
    for r in releases:
        assert "perfect for families" not in str(r["payload"]), \
            "HITL gate: the flagged phrase must NEVER release"


def test_lead_to_close_full_lifecycle_with_sweeps(tmp_path):
    """The revenue path end-to-end: lead captured -> qualified ->
    transaction milestones -> settlement statement -> close fans out ->
    16's check-ins fire from the SWEEP LAYER -> 15's commission is real
    money computed at the ratified default rate. Every leg is real hub
    traffic; the clock legs run through run_daily_sweeps."""
    hub, signer, spokes, external = build_swarm(str(tmp_path))
    ctx = "deal-1"

    # rubric for 02 (signed authority config)
    hub.send(signed(signer, "02", "config.update", ctx,
                    {"rubric": {"budget_threshold": 400_000,
                                "budget_weight": 40,
                                "timeline_days_threshold": 30,
                                "timeline_weight": 40,
                                "financing_weight": 20,
                                "hot_threshold": 70, "warm_threshold": 40},
                     "version": "v1"}))

    # lead arrives, dedupes through 14, lands in 02
    hub.send(spoke_env("20", "01", "lead.signal", ctx,
                       {"channel": "call", "name": "Jane", "phone": "555",
                        "property_interest": {"listing_id": "L1"},
                        "timeline": "2 weeks", "timeline_days": 14,
                        "budget": 450_000, "preapproval_status": "yes",
                        "financing_progress": "preapproved",
                        "today": "2026-06-01",
                        "consent": {"call": "yes", "text": "yes",
                                    "email": "yes"}}))
    logs = [e for e in persisted(hub, "interaction.log")
            if e["payload"].get("tier")]
    assert logs and logs[-1]["payload"]["tier"] == "HOT", \
        "qualification never produced a tier from real traffic"

    # transaction timeline established (signed), then the settlement
    # statement lands in 08 - money figures pass through VERBATIM
    hub.send(signed(signer, "07", "config.update", ctx,
                    {"timeline_init": {"closing": "2026-07-01"}}))
    hub.send(spoke_env("11", "08", "document.submission", ctx,
                       {"doc_type": "closing_settlement_statement",
                        "submitting_party": "title-co@example.com",
                        "artifact_ref": "settle-hash", "opens_correctly": True,
                        "sale_price": 450_000, "signed": True,
                        "today": "2026-07-01"}))
    # the fan-out: 07 -> 16 (close_date), 07 -> 15 (sale_price)
    assert spokes["16"].closed_transactions[ctx]["close_date"] == "2026-07-01"
    rec = spokes["15"].commissions[ctx]
    assert rec["amount"] == 450_000 * 0.08, \
        "commission not computed at the ratified default rate"
    assert rec["source"].startswith("computed_at_default_rate_")

    # 16's client list entry (signed human config), then the CLOCK runs:
    hub.send(signed(signer, "16", "config.update", ctx,
                    {"client_list_entry": {"name": "Jane"}}))
    results = run_daily_sweeps(hub, spokes, today="2026-08-05")
    by = {(r["agent"], r["sweep"]): r["result"] for r in results
          if r["agent"] == "16"}
    assert by[("16", "post_close_milestones")] == "sent:30", \
        "the 30-day check-in must fire from the sweep layer on real state"

    assert hub.queues["dead.letter"] == []
    assert hub.audit.verify_chain()["ok"]
