#!/usr/bin/env python3
"""console.py - the operator's entry point. No Python required.

Built 2026-07-18 for operational testing: until this existed, feeding
the swarm a lead or a listing meant writing Python against the hub -
fine for tests, wrong for an operator setting up "like anyone else."
This is a thin, honest shim over the same hub the tests run: it builds
real envelopes, signs authority intents with a locally-generated key,
persists swarm state between invocations via each spoke's own state
(reconstructed from the audit log is future work - stated, not faked:
today each console SESSION is one continuous hub, and `--audit-log`
accumulates across sessions so the record survives even though live
spoke state does not).

Commands:
  python tools/console.py init                         # keygen + fresh log
  python tools/console.py session <script.jsonl>       # run a scripted session
  python tools/console.py verify                       # chain + anchor check

A session script is JSON Lines; each line is one action:
  {"do": "config",  "to": "02", "ctx": "setup", "payload": {...}}   # signed
  {"do": "authorize", "to": "05", "ctx": "L1", "payload": {...}}    # signed listing.change.authorized
  {"do": "send", "from": "20", "to": "01", "intent": "lead.signal",
   "ctx": "lead-1", "payload": {...}}                               # spoke/external traffic
  {"do": "sweep", "today": "2026-07-19"}                            # run the clock layer
  {"do": "show", "what": "queues"}                                  # or "briefing", "anchor"

Everything prints as JSON lines so a screen recording captures exactly
what the audit log captured.
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "console-state")
KEY_PATH = os.path.join(STATE_DIR, "operator-key.bin")
AUDIT_PATH = os.path.join(STATE_DIR, "console-audit.jsonl")


def _p(obj):
    print(json.dumps(obj, default=str))


def build(audit_path):
    from dispatcher.core import Routes, AuditLog, Envelope  # noqa: F401
    from dispatcher.hub import Hub
    from dispatcher.signatures import Ed25519Signer, Ed25519Verifier
    from dispatcher.listing_spokes import Spoke01LeadCapture, Spoke14CRMPipeline
    from dispatcher.listing_spokes_02 import Spoke02LeadQualification
    from dispatcher.listing_spokes_03 import Spoke03LeadNurture
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
    from dispatcher.listing_spokes_19 import Spoke19Prospecting
    from dispatcher.listing_spokes_20 import Spoke20SocialMediaMonitoring

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.exists(KEY_PATH):
        signer = Ed25519Signer(private_key_bytes=open(KEY_PATH, "rb").read())
    else:
        signer = Ed25519Signer()
    verifier = Ed25519Verifier(signer.public_key_bytes())
    hub = Hub(Routes(os.path.join(root, "identity", "routes.json")),
              AuditLog(audit_path),
              signature_verifier=verifier.verifier())
    external = []
    hub.register("external", lambda env: external.append(
        {"intent": env.intent, "ctx": env.client_context_id,
         "payload": env.payload}))
    hub.register("human", lambda env: _p(
        {"DELIVERED_TO_HUMAN": env.intent, "ctx": env.client_context_id,
         "payload": env.payload}))
    roster_path = os.path.join(root, "config", "vendor_panel.json")
    roster = {}
    if os.path.exists(roster_path):
        try:
            roster = json.load(open(roster_path)).get("vendors", {})
        except Exception:
            roster = {}
    spokes = {
        "01": Spoke01LeadCapture(hub), "02": Spoke02LeadQualification(hub),
        "03": Spoke03LeadNurture(hub), "04": Spoke04ListingDescription(hub),
        "05": Spoke05MLSListingManagement(hub),
        "06": Spoke06ShowingScheduler(hub),
        "07": Spoke07TransactionCoordinator(hub),
        "08": Spoke08DocumentCollection(hub),
        "09": Spoke09VendorCoordination(hub, roster=roster),
        "10": Spoke10MarketData(hub), "11": Spoke11ClientCommunication(hub),
        "12": Spoke12MarketingCampaign(hub),
        "13": Spoke13BuyerSearchMatch(hub), "14": Spoke14CRMPipeline(hub),
        "15": Spoke15FinancialTracking(hub),
        "16": Spoke16AfterCloseReferral(hub),
        "17": Spoke17ComplianceFairHousing(hub),
        "18": Spoke18CalendarTask(hub), "19": Spoke19Prospecting(hub),
        "20": Spoke20SocialMediaMonitoring(hub),
    }
    hub.on_turn_start()
    return hub, signer, spokes, external


def make_env(frm, to, intent, ctx, payload):
    from dispatcher.core import Envelope
    src = "human" if frm == "human" else \
        ("external" if frm == "external" else f"spoke-{frm}")
    return Envelope(from_agent=frm, to_agent=to, intent=intent,
                    client_context_id=ctx, payload=payload,
                    provenance={"source": src, "captured_at": "runtime",
                                "verbatim_available": True})


def cmd_init(_args):
    from dispatcher.signatures import Ed25519Signer
    os.makedirs(STATE_DIR, exist_ok=True)
    if os.path.exists(AUDIT_PATH):
        _p({"error": f"{AUDIT_PATH} exists - a console log is never "
                     f"silently replaced; move it aside yourself"})
        return 1
    signer = Ed25519Signer()
    with open(KEY_PATH, "wb") as f:
        f.write(signer.private_key_bytes())
    os.chmod(KEY_PATH, 0o600)
    _p({"initialized": STATE_DIR, "operator_key": "generated (0600)",
        "audit_log": AUDIT_PATH,
        "note": "this key signs YOUR authority actions - it stays on "
                "this machine"})
    return 0


def cmd_session(args):
    hub, signer, spokes, external = build(args.audit_log or AUDIT_PATH)
    from dispatcher.sweep_runner import run_daily_sweeps
    errors = 0
    for lineno, line in enumerate(open(args.script), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            a = json.loads(line)
        except json.JSONDecodeError as e:
            _p({"line": lineno, "error": f"bad JSON: {e}"})
            errors += 1
            continue
        do = a.get("do")
        if do in ("config", "authorize"):
            intent = "config.update" if do == "config" \
                else "listing.change.authorized"
            env = make_env("human", a["to"], intent, a.get("ctx", "console"),
                           a.get("payload", {}))
            signer.sign(env)
            r = hub.send(env)
        elif do == "send":
            env = make_env(a["from"], a["to"], a["intent"],
                           a.get("ctx", "console"), a.get("payload", {}))
            r = hub.send(env)
        elif do == "sweep":
            r = run_daily_sweeps(hub, spokes, today=a["today"],
                                 now_iso=a.get("now_iso"))
        elif do == "show":
            what = a.get("what")
            if what == "queues":
                r = {k: v for k, v in hub.queues.items() if v}
            elif what == "briefing":
                r = spokes["18"].generate_briefing("morning")
            elif what == "eod":
                r = spokes["14"].generate_report("eod")
            elif what == "external":
                r = external
            elif what == "anchor":
                r = hub.audit.anchor()
            else:
                r = {"error": f"unknown show target {what!r}"}
        else:
            r = {"error": f"unknown action {do!r}"}
            errors += 1
        _p({"line": lineno, "do": do, "result": r})
    _p({"session_complete": True, "errors": errors,
        "chain": hub.audit.verify_chain(), "anchor": hub.audit.anchor(),
        "dead_letters": len(hub.queues["dead.letter"])})
    return 1 if errors else 0


def cmd_verify(args):
    from dispatcher.core import AuditLog
    log = AuditLog(args.audit_log or AUDIT_PATH)
    r = {"chain": log.verify_chain(), "anchor": log.anchor()}
    if args.anchor:
        r["anchor_check"] = log.verify_anchor(json.loads(args.anchor))
    _p(r)
    return 0 if r["chain"]["ok"] else 1


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    s = sub.add_parser("session")
    s.add_argument("script")
    s.add_argument("--audit-log", default=None)
    v = sub.add_parser("verify")
    v.add_argument("--audit-log", default=None)
    v.add_argument("--anchor", default=None,
                   help="a previously printed anchor JSON to verify against")
    args = ap.parse_args()
    return {"init": cmd_init, "session": cmd_session,
            "verify": cmd_verify}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
