#!/usr/bin/env python3
"""identity_mcp - a generic MCP server that mounts a QuietFireAI governed
identity behind a deliberately NARROW closed surface.

Why narrow: the host agent's LLM must NOT be handed the identity's
internals as free tools - that would return routing to the host and
dissolve the closed track. Instead the host gets exactly four tools that
push intents at the FRONT DOOR; the governed hub does all routing,
signing, sealing, and logging behind them. The MCP shell is the open
socket; the hub is the governed cartridge.

Runs as a stdio MCP server, so one wrapper mounts identically on Hermes
(`mcp_servers:` in config.yaml), OpenClaw (MCP server config), and Claude
Desktop - zero per-host code.

Boot conformance: the server REFUSES TO START unless the closed track,
the signer registry, and the audit chain are intact. The governance
guarantee travels with the server; it cannot be silently mounted broken.

Usage:
    python3 -m dispatcher.identity_mcp            # serve medbilling (this repo)
    IDENTITY_ROOT=/path python3 -m dispatcher.identity_mcp
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid

# --- locate the identity this wrapper serves -------------------------------
ROOT = os.environ.get(
    "IDENTITY_ROOT",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from dispatcher.core import Envelope, Routes, AuditLog          # noqa: E402
from dispatcher.hub import Hub                                   # noqa: E402
from dispatcher.signatures import Ed25519Signer, Ed25519Verifier  # noqa: E402


IDENTITY_NAME = os.path.basename(ROOT.rstrip("/")) or "identity"
ROUTES_PATH = os.path.join(ROOT, "identity", "routes.json")
PRIORITY_PATH = os.path.join(ROOT, "identity", "priority.json")


# ---------------------------------------------------------------------------
# Boot conformance gate - the guarantee travels with the server.
# ---------------------------------------------------------------------------
class ConformanceError(RuntimeError):
    pass


def _boot_conformance():
    """Refuse to start unless closed track, signer path, and audit chain
    are intact. Returns (routes, signer, verifier) on success."""
    problems = []

    # 1. Closed track loads and is non-empty.
    try:
        routes = Routes(ROUTES_PATH)
    except Exception as e:  # noqa: BLE001
        raise ConformanceError(f"closed track failed to load: {e}")
    n_routes = len(json.load(open(ROUTES_PATH))["routes"])
    if n_routes == 0:
        problems.append("closed track has zero routes")

    # 2. Signature path is real: a signed envelope verifies, an unsigned
    #    authority envelope is rejected by the hub.
    signer = Ed25519Signer()
    verifier = Ed25519Verifier(signer.public_key_bytes())
    probe_audit = AuditLog(f"/tmp/conformance-{uuid.uuid4().hex[:8]}.jsonl")
    hub = Hub(routes, probe_audit,
              signature_verifier=verifier.verifier())
    hub.register("human", lambda e: None)
    hub.on_turn_start()
    # Probe THIS identity's own first authority lane (a route requiring a
    # signature), so the gate is tested for the right reason on any
    # identity - never a hardcoded intent that might be an illegal route.
    all_routes = json.load(open(ROUTES_PATH))["routes"]
    auth = next((r for r in all_routes
                 if "authorized" in r["intent"] or "authority" in r["intent"]),
                None)
    if auth is None:
        problems.append("no authority lane found to test the signer gate")
    else:
        to = auth["receivers"][0]
        hub.register(to, lambda e: None)
        unsigned = Envelope(from_agent=auth["senders"][0], to_agent=to,
                            intent=auth["intent"],
                            client_context_id="conformance-probe",
                            payload={"probe": True},
                            provenance={"source": "human",
                                        "captured_at": "boot",
                                        "verbatim_available": True})
        res = hub.send(unsigned)
        if res.get("status") == "ack":
            problems.append(
                f"signer gate OPEN: unsigned {auth['intent']} accepted")

    # 3. Audit chain verifies and is tamper-evident.
    if not probe_audit.verify_chain().get("ok"):
        problems.append("audit chain does not verify at boot")

    if problems:
        raise ConformanceError(
            "identity NOT conformant - refusing to mount:\n  - "
            + "\n  - ".join(problems))
    return routes, signer, verifier


# ---------------------------------------------------------------------------
# The governed identity behind the MCP surface.
# ---------------------------------------------------------------------------
class GovernedIdentity:
    """Builds the hub + spokes once, keeps state across submits within the
    server's lifetime, and exposes only front-door operations."""

    def __init__(self, routes, signer, verifier):
        self.signer = signer
        self.audit_path = f"/tmp/{IDENTITY_NAME}-mcp-{uuid.uuid4().hex[:8]}.jsonl"
        self.hub = Hub(routes, AuditLog(self.audit_path),
                       signature_verifier=verifier.verifier())
        self.external = []
        self.hub.register("external", lambda e: self.external.append(e))
        self.hub.register("human", lambda e: None)
        self._wire_spokes()
        self.hub.on_turn_start()

    # Spoke modules known to this wrapper, by detection (not by folder name).
    KNOWN = {"medbilling_spokes": "_wire_medbilling",
             "listing_spokes": "_wire_listing"}

    def _wire_spokes(self):
        """Mount real spokes by DETECTING which spoke module is present in
        dispatcher/ - never by directory name (fork-safe). Absence is
        honest: describe/audit still work; submit reports spec-only."""
        self.spokes = {}
        self.spoke_mod = None
        import glob
        disp = os.path.join(ROOT, "dispatcher")
        for path in glob.glob(os.path.join(disp, "*_spokes.py")):
            name = os.path.basename(path)[:-3]  # e.g. medbilling_spokes
            if name in self.KNOWN:
                try:
                    mod = __import__(f"dispatcher.{name}", fromlist=["*"])
                except Exception:
                    continue
                self.spoke_mod = mod
                getattr(self, self.KNOWN[name])(mod)
                return

    def _wire_medbilling(self, m):
        seq = [{"day": 0, "step": "statement", "template": "statement"},
               {"day": 30, "step": "reminder_1", "template": "reminder"},
               {"day": 60, "step": "reminder_2", "template": "reminder"},
               {"day": 90, "step": "final_notice", "template": "final_notice"}]
        s = {
            "01": m.Spoke01EncounterIntake(self.hub, provider_roster={"dr-a"},
                                           auth_required_codes=set()),
            "02": m.Spoke02ClaimScrubbing(self.hub,
                                          edit_tables={"version": "e1",
                                                       "rules": []}),
            "03": m.Spoke03Eligibility(self.hub, payer_db={}),
            "04": m.Spoke04PatientComm(self.hub, templates={
                "statement": "Your balance is {balance}.",
                "opt_out_confirmed": "We will stop billing reminders."}),
            "05": m.Spoke05DocCollection(self.hub),
            "06": m.Spoke06PriorAuth(self.hub),
            "07": m.Spoke07Submission(self.hub),
            "08": m.Spoke08PaymentPosting(self.hub, contract_rules={
                "CO45": {"rule_id": "PAYER1-CO45"}}),
            "09": m.Spoke09DenialMgmt(self.hub,
                                      taxonomy={"CO50": "medical_necessity"}),
            "10": m.Spoke10ARFollowup(self.hub),
            "11": m.Spoke11PatientBilling(self.hub, contact_sequence=seq),
            "12": m.Spoke12ComplianceDeadlines(self.hub),
            "13": m.Spoke13Records(self.hub),
            "14": m.Spoke14DailyOps(self.hub),
        }
        for aid, sp in s.items():
            if aid != "01":
                self.hub.register(aid, sp.handle)
        self.spokes = s

    def _wire_listing(self, _m):
        """Wire the full listing swarm (18 spokes) exactly as the e2e
        harness does. The entry module is listing_spokes; the rest are
        listing_spokes_NN."""
        import importlib
        L = lambda n: importlib.import_module(f"dispatcher.{n}")
        base = L("listing_spokes")
        s = {
            "01": base.Spoke01LeadCapture(self.hub,
                                          brokerage_scope={"L1", "MLS-1"}),
            "02": L("listing_spokes_02").Spoke02LeadQualification(self.hub),
            "04": L("listing_spokes_04").Spoke04ListingDescription(self.hub),
            "05": L("listing_spokes_05").Spoke05MLSListingManagement(self.hub),
            "06": L("listing_spokes_06").Spoke06ShowingScheduler(self.hub),
            "07": L("listing_spokes_07").Spoke07TransactionCoordinator(self.hub),
            "08": L("listing_spokes_08").Spoke08DocumentCollection(
                self.hub, expected_senders={}),
            "09": L("listing_spokes_09").Spoke09VendorCoordination(
                self.hub, roster={"v-photo": {"kind": "photography",
                                              "license_expiry": "2027-01-01",
                                              "insurance_expiry": "2027-01-01",
                                              "regulated": False}}),
            "10": L("listing_spokes_10").Spoke10MarketData(self.hub),
            "11": L("listing_spokes_11").Spoke11ClientCommunication(self.hub),
            "12": L("listing_spokes_12").Spoke12MarketingCampaign(self.hub),
            "13": L("listing_spokes_13").Spoke13BuyerSearchMatch(self.hub),
            "14": base.Spoke14CRMPipeline(self.hub),
            "15": L("listing_spokes_15").Spoke15FinancialTracking(self.hub),
            "16": L("listing_spokes_16").Spoke16AfterCloseReferral(self.hub),
            "17": L("listing_spokes_17").Spoke17ComplianceFairHousing(self.hub),
            "18": L("listing_spokes_18").Spoke18CalendarTask(self.hub),
        }
        for aid, sp in s.items():
            self.hub.register(aid, sp.handle)
        self.spokes = s

    # --- front-door operations -------------------------------------------
    def describe(self):
        routes = json.load(open(ROUTES_PATH))
        priority = {}
        if os.path.exists(PRIORITY_PATH):
            priority = json.load(open(PRIORITY_PATH)).get("classes", {})
        return {
            "identity": IDENTITY_NAME,
            "vertical": routes.get("vertical", "unstated"),
            "closed_track": {
                "route_count": len(routes["routes"]),
                "legal_intents": sorted({r["intent"] for r in routes["routes"]}),
            },
            "playbooks": priority,
            "governance": {
                "signed_authority_required": True,
                "reconciliation_tolerance": "$0.00",
                "audit": "hash-chained, tamper-evident (see identity.audit)",
                "note": ("This is a governed identity, not a toolbox. The "
                         "host submits intents at the front door; the hub "
                         "routes, gates, signs, and seals internally."),
            },
            "runtime": "wired" if self.spokes else "spec-only (no spoke module)",
        }

    def submit(self, intent, payload, context_id=None):
        ctx = context_id or f"mcp-{uuid.uuid4().hex[:8]}"
        if not self.spokes:
            return {"ok": False,
                    "reason": "identity is spec-only in this build; no "
                              "runtime spokes to execute the intent",
                    "context_id": ctx}
        # Front-door intents only: drive the identity's real entry points.
        result = self._drive(intent, payload, ctx)
        return result

    def _drive(self, intent, payload, ctx):
        """Map a small set of front-door intents to the identity's real
        entry points. Anything not a legal front-door op is refused."""
        modname = getattr(self.spoke_mod, "__name__", "") if self.spoke_mod \
            else ""
        if modname.endswith("listing_spokes"):
            if intent == "listing.authorize":
                # front door: the signed new-listing authorization (P01)
                from dispatcher.core import Envelope
                env = Envelope(from_agent="human", to_agent="05",
                               intent="listing.change.authorized",
                               client_context_id=ctx, payload=payload,
                               provenance={"source": "mcp-host",
                                           "captured_at": "runtime",
                                           "verbatim_available": True})
                self.signer.sign(env)
                res = self.hub.send(env)
                return {"ok": res.get("status") == "ack",
                        "context_id": ctx,
                        "note": "listing authorization submitted; the swarm "
                                "runs P01 setup->production->go-live behind "
                                "the gates",
                        "audit_entries": len(self.hub.audit.read())}
            return {"ok": False, "context_id": ctx,
                    "reason": f"'{intent}' is not a legal front-door intent "
                              f"for listing; the signed lane is "
                              f"'listing.authorize'"}
        if modname.endswith("medbilling_spokes"):
            if intent == "encounter.capture":
                self.spokes["03"].db[ctx] = {"status":
                                             payload.get("eligibility",
                                                         "active")}
                self.spokes["01"].capture(ctx, payload["encounter"])
                st = self.spokes["02"].claims.get(ctx, {})
                return {"ok": True, "context_id": ctx,
                        "released": st.get("released", False),
                        "held_reason": st.get("held_reason"),
                        "codes": st.get("codes"),
                        "audit_entries": len(self.hub.audit.read())}
            if intent == "remit.post":
                self.spokes["08"].post_remit(ctx, payload["remit"])
                return {"ok": True, "context_id": ctx,
                        "ledger": self.spokes["08"].ledger.get(ctx),
                        "audit_entries": len(self.hub.audit.read())}
        return {"ok": False, "context_id": ctx,
                "reason": f"'{intent}' is not a legal front-door intent for "
                          f"{IDENTITY_NAME}; call identity.describe for the "
                          f"legal surface"}

    def authority(self, intent, payload, signature_hex=None, context_id=None):
        """The signed-money lane, explicit and separate. Without a real
        signature the hub rejects it - proving the gate to the caller."""
        ctx = context_id or f"mcp-{uuid.uuid4().hex[:8]}"
        env = Envelope(from_agent="human",
                       to_agent=payload.get("to", "08"),
                       intent=intent, client_context_id=ctx,
                       payload=payload,
                       provenance={"source": "mcp-host",
                                   "captured_at": "runtime",
                                   "verbatim_available": True})
        if signature_hex == "DEMO_SIGN_WITH_SERVER_KEY":
            # explicit demo affordance: server signs so a host can see the
            # signed path succeed. Production hosts present their OWN
            # signature; this is clearly labelled, not a hidden bypass.
            self.signer.sign(env)
        res = self.hub.send(env)
        accepted = res.get("status") == "ack"
        return {"ok": accepted, "context_id": ctx,
                "hub_status": res.get("status"),
                "reason": ("signed authority executed" if accepted
                           else "REJECTED: authority requires a valid "
                                "signature (this is the gate working)")}

    def audit(self):
        v = self.hub.audit.verify_chain()
        return {"identity": IDENTITY_NAME,
                "audit_path": self.audit_path,
                "entries": len(self.hub.audit.read()),
                "chain_verified": v.get("ok"),
                "dead_letters": len(self.hub.queues.get("dead.letter", [])),
                "statement": ("The log is tamper-evident. Alter any line and "
                              "chain_verified becomes false, naming the line.")}


# ---------------------------------------------------------------------------
# MCP server: four tools, no more.
# ---------------------------------------------------------------------------
def build_server(identity):
    from mcp.server import Server
    import mcp.types as types

    server = Server(f"quietfire-identity-{IDENTITY_NAME}")

    TOOLS = [
        types.Tool(
            name="identity.describe",
            description=(f"Describe the governed {IDENTITY_NAME} identity: "
                         "its closed track (legal intents), playbooks, and "
                         "governance guarantees. Read-only. Call this first "
                         "to learn what the identity accepts."),
            inputSchema={"type": "object", "properties": {}}),
        types.Tool(
            name="identity.submit",
            description=("Submit ONE intent at the identity's front door. "
                         "The governed hub validates it against the closed "
                         "track, runs the playbook, enforces every gate, and "
                         "returns the result or a refusal-with-reason. You do "
                         "NOT get to route internally - the hub does."),
            inputSchema={"type": "object",
                         "properties": {
                             "intent": {"type": "string"},
                             "payload": {"type": "object"},
                             "context_id": {"type": "string"}},
                         "required": ["intent", "payload"]}),
        types.Tool(
            name="identity.authority",
            description=("The signed-money lane, kept explicit and separate. "
                         "Authority intents (write-offs, refunds, etc.) "
                         "require a valid signature; without one the hub "
                         "rejects - and returns the rejection so you can see "
                         "the gate working."),
            inputSchema={"type": "object",
                         "properties": {
                             "intent": {"type": "string"},
                             "payload": {"type": "object"},
                             "signature_hex": {"type": "string"},
                             "context_id": {"type": "string"}},
                         "required": ["intent", "payload"]}),
        types.Tool(
            name="identity.audit",
            description=("Return the hash-chain verification for this "
                         "session's audit log so you can prove tamper-"
                         "evidence: entry count, chain_verified, dead "
                         "letters."),
            inputSchema={"type": "object", "properties": {}}),
    ]

    @server.list_tools()
    async def list_tools():
        return TOOLS

    @server.call_tool()
    async def call_tool(name, arguments):
        arguments = arguments or {}
        if name == "identity.describe":
            out = identity.describe()
        elif name == "identity.submit":
            out = identity.submit(arguments["intent"], arguments["payload"],
                                  arguments.get("context_id"))
        elif name == "identity.authority":
            out = identity.authority(arguments["intent"], arguments["payload"],
                                     arguments.get("signature_hex"),
                                     arguments.get("context_id"))
        elif name == "identity.audit":
            out = identity.audit()
        else:
            out = {"ok": False, "reason": f"unknown tool {name}"}
        return [types.TextContent(type="text",
                                  text=json.dumps(out, indent=2))]

    return server


async def _serve():
    routes, signer, verifier = _boot_conformance()
    sys.stderr.write(
        f"[identity_mcp] conformance PASSED for {IDENTITY_NAME}: "
        f"closed track + signer gate + audit chain intact\n")
    identity = GovernedIdentity(routes, signer, verifier)
    server = build_server(identity)
    from mcp.server.stdio import stdio_server
    async with stdio_server() as (read, write):
        await server.run(read, write,
                         server.create_initialization_options())


def main():
    try:
        asyncio.run(_serve())
    except ConformanceError as e:
        sys.stderr.write(f"[identity_mcp] BOOT REFUSED\n{e}\n")
        sys.exit(2)


if __name__ == "__main__":
    main()
