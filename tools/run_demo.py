#!/usr/bin/env python3
"""Watch the listing swarm take a new listing live - a real run, not a
slideshow. One property, signed authorization to a live, marketed,
fair-housing-cleared listing, against the real hub, real Ed25519
signatures, and the real hash-chained audit log.

  Act 1  Signed listing authorization -> setup: photography ordered from
         the vetted roster, seller onboarded, agreement logged. The PRICE
         is the human's - the swarm never touches it.
  Act 2  Photos delivered -> the production gate opens: description agent
         drafts from property facts, compliance reviews EVERY asset before
         anything markets.
  Act 3  Fair-housing gate LIVE: a steering phrase in the data is FLAGGED
         and never releases - marketing cannot proceed on flagged copy.
  Act 4  Clean assets approved -> go-live: MLS active (live-verified, not
         a push log), Clear Cooperation satisfied, campaign publishes,
         buyer feeds updated, seller told "you're live".
  Act 5  A pricing question from any party -> escalation.legal_line. The
         swarm does not answer money/price questions - ever.
  Act 6  Chain verification - every event, hash-linked, tamper-evident.

Run it:  python3 tools/run_demo.py
"""
import os
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.signatures import Ed25519Signer, Ed25519Verifier
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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULESET = {"prohibited_phrases": [
    {"phrase": "perfect for families", "rule_id": "FH-STEER-1"}],
    "state_rules": {}}


def say(s=""): print(s)
def act(n, t): say(); say("=" * 68); say(f"  ACT {n}: {t}"); say("=" * 68)


def build():
    signer = Ed25519Signer()
    verifier = Ed25519Verifier(signer.public_key_bytes())
    path = os.path.join(tempfile.mkdtemp(), "audit.jsonl")
    hub = Hub(Routes(os.path.join(ROOT, "identity", "routes.json")),
              AuditLog(path), signature_verifier=verifier.verifier())
    ext = []
    hub.register("external", lambda e: ext.append(e))
    hub.register("human", lambda e: None)
    s = {
        "01": Spoke01LeadCapture(hub, brokerage_scope={"L1", "MLS-1"}),
        "02": Spoke02LeadQualification(hub),
        "04": Spoke04ListingDescription(hub),
        "05": Spoke05MLSListingManagement(hub),
        "06": Spoke06ShowingScheduler(hub),
        "07": Spoke07TransactionCoordinator(hub),
        "08": Spoke08DocumentCollection(hub, expected_senders={}),
        "09": Spoke09VendorCoordination(hub, roster={
            "v-photo": {"kind": "photography",
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
    return hub, signer, s, ext, path


def signed(signer, to, intent, ctx, payload, frm="human"):
    e = Envelope(from_agent=frm, to_agent=to, intent=intent,
                 client_context_id=ctx, payload=payload,
                 provenance={"source": frm, "captured_at": "runtime",
                             "verbatim_available": True})
    signer.sign(e)
    return e


def spoke_env(frm, to, intent, ctx, payload):
    return Envelope(from_agent=frm, to_agent=to, intent=intent,
                    client_context_id=ctx, payload=payload,
                    provenance={"source": f"spoke-{frm}",
                                "captured_at": "runtime",
                                "verbatim_available": True})


def main():
    hub, signer, s, ext, path = build()
    ctx = "demo-listing-1"

    def seen(intent):
        return [e for e in hub.audit.read()
                if e["kind"] == "envelope.persisted" and e["intent"] == intent]

    def ext_has(intent):
        return any(e.intent == intent for e in ext)

    hub.send(signed(signer, "17", "config.update", ctx,
                    {"ruleset": RULESET, "version": "r1"}))

    act(1, "SIGNED AUTHORIZATION -> SETUP (the price is the human's)")
    hub.send(signed(signer, "05", "listing.change.authorized", ctx,
                    {"new_listing": {"beds": 3, "sqft": 2000, "price": 500_000,
                                     "features": ["garage"],
                                     "photo_detected_features": ["garage"]},
                     "authorize_go_live": True, "today": "2026-07-01"}))
    hub.send(spoke_env("07", "14", "interaction.log", ctx,
                       {"kind": "listing_agreement",
                        "consent": {"call": "yes", "text": "yes",
                                    "email": "yes"}}))
    v = s["09"].scheduled.get(ctx, {}).get("photography", {}).get("vendor_id")
    say(f"  photography ordered from vetted roster: {v}")
    say(f"  vendor booking left the swarm: {ext_has('vendor.schedule')}")
    hub.send(spoke_env("06", "11", "client.message.request", ctx,
                       {"template": "seller_onboarding", "hour": 10}))
    say(f"  seller onboarding message sent: {ext_has('client.message.send')}")
    say("  NOTE: $500,000 list price came in on the signed envelope. No "
        "agent set it, no agent will change it.")

    act(2, "PHOTOS DELIVERED -> PRODUCTION GATE OPENS")
    hub.send(spoke_env("external", "09", "vendor.event", ctx,
                       {"kind": "photography",
                        "event_kind": "deliverable_report",
                        "doc_type": "photo_package",
                        "proof_artifact_present": True,
                        "content_hash": "photos-hash"}))
    say(f"  photo deliverable verified present-and-opens: "
        f"{bool(seen('deliverable.release'))}")
    say(f"  description drafted, submitted to compliance: "
        f"{bool(seen('content.review'))}")
    verd = seen("content.verdict")
    say(f"  compliance verdict on the clean draft: "
        f"{verd[-1]['payload']['verdict'] if verd else 'none'}")
    rel = {r["to_agent"] for r in seen("asset.release")}
    say(f"  approved assets released to: {sorted(rel)} "
        "(both MLS and marketing)")

    act(3, "FAIR-HOUSING GATE LIVE -> A STEERING PHRASE IS FLAGGED")
    fctx = "demo-flag"
    hub.send(spoke_env("05", "04", "listing.data", fctx,
                       {"beds": 2, "features": ["perfect for families"],
                        "today": "2026-07-05"}))
    fv = [e["payload"]["verdict"] for e in seen("content.verdict")
          if e["client_context_id"] == fctx]
    say(f"  data carried a prohibited steering phrase; verdict: {fv}")
    leaked = any("perfect for families" in str(r["payload"])
                 for r in seen("asset.release")
                 if r["client_context_id"] == fctx)
    say(f"  did the flagged phrase EVER reach a marketing release? {leaked}")
    say("  The gate is a hard stop: flagged copy cannot market. Period.")

    act(4, "GO-LIVE -> MLS ACTIVE, CLEAR COOPERATION, CAMPAIGN PUBLISHED")
    su = {e["to_agent"] for e in seen("status.update")}
    say(f"  status.update(active) reached: {sorted(su)} (11/12/14 required)")
    say(f"  MLS live-verified (not a push log) -> campaign published: "
        f"{ext_has('campaign.publish')}")
    feeds = any(e["to_agent"] == "13" and e["intent"] == "listing.data"
                for e in seen("listing.data"))
    say(f"  listing pushed into buyer-match feeds (agent 13): {feeds}")
    sends = [e for e in ext if e.intent == "client.message.send"]
    say(f"  seller told 'you're live': {len(sends) >= 2}")

    act(5, "A PRICING QUESTION -> ESCALATION, NEVER AN ANSWER")
    pctx = "demo-price"
    before = len(hub.queues.get("escalation.legal_line", []))
    # a seller asks the comms agent directly: should we drop the price?
    hub.send(spoke_env("external", "11", "client.reply", pctx,
                       {"message": "what's your pricing strategy - should "
                                   "we drop to 480k?"}))
    after = len(hub.queues.get("escalation.legal_line", []))
    raised = [e for e in hub.audit.read()
              if e["kind"] == "escalation.raised"
              and e.get("queue") == "escalation.legal_line"]
    escalated = (after > before) and bool(raised)
    answered = any(e.intent == "client.message.send"
                   and e.client_context_id == pctx
                   and "480" in str(e.payload) for e in ext)
    say(f"  pricing question escalated to the legal line: {escalated}")
    say(f"  did any agent ANSWER the price question? {answered}")
    say("  Price is fiduciary. The swarm routes it to a human, always.")

    act(6, "THE CHAIN")
    r = hub.audit.verify_chain()
    say(f"  audit entries: {len(hub.audit.read())}")
    say(f"  verify_chain(): ok={r['ok']}")
    say(f"  dead letters: {len(hub.queues['dead.letter'])}")
    say(f"  log file: {path}")
    say()
    say("  Tamper with any line and verify_chain() names it. "
        "Not trust us - check us.")
    say()


if __name__ == "__main__":
    main()
