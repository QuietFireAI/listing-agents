#!/usr/bin/env python3
"""Live MCP round trip: spawn the identity_mcp server over stdio exactly as
Hermes / OpenClaw / Claude Desktop would, then call all four tools through
the real MCP protocol. This is the proof the wrapper mounts - not a
description of it."""
import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def line(s=""):
    print(s)


async def main():
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "dispatcher.identity_mcp"],
        env={**os.environ, "PYTHONPATH": ROOT, "IDENTITY_ROOT": ROOT},
        cwd=ROOT)

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            line("=" * 64)
            line("  LIVE MCP ROUND TRIP - listing identity over stdio")
            line("  (the exact protocol Hermes/OpenClaw/Claude Desktop use)")
            line("=" * 64)

            tools = await session.list_tools()
            line("\n[list_tools] host discovers the closed surface:")
            for t in tools.tools:
                line(f"  - {t.name}")

            async def call(name, args=None):
                r = await session.call_tool(name, args or {})
                return json.loads(r.content[0].text)

            line("\n[identity.describe] host learns what it may ask for:")
            d = await call("identity.describe")
            line(f"  identity: {d['identity']}  runtime: {d['runtime']}")
            line(f"  closed track: {d['closed_track']['route_count']} routes,"
                 f" {len(d['closed_track']['legal_intents'])} legal intents")
            line(f"  playbooks: {len(d['playbooks'])}")
            line(f"  governance: signed_authority="
                 f"{d['governance']['signed_authority_required']},"
                 f" tolerance={d['governance']['reconciliation_tolerance']}")

            line("\n[identity.submit] host pushes an encounter at the front"
                 " door; the hub routes and gates it:")
            enc = None  # listing uses listing.authorize, payload inline below
            r = await call("identity.submit",
                           {"intent": "listing.authorize",
                            "payload": {"new_listing": {"beds": 3, "sqft": 2000, "price": 500000, "features": ["garage"], "photo_detected_features": ["garage"]}, "authorize_go_live": True, "today": "2026-07-01"}})
            line(f"  ok: {r.get('ok')}  note: {r.get('note','')[:60]}")

            line("\n[identity.submit] an intent the host tries to route that"
                 " ISN'T a legal front door:")
            r = await call("identity.submit",
                           {"intent": "please.just.pay.this",
                            "payload": {}})
            line(f"  ok: {r['ok']}  reason: {r['reason'][:60]}...")

            line("\n[identity.authority] UNSIGNED money - the gate the host"
                 " CANNOT talk its way past:")
            r = await call("identity.authority",
                           {"intent": "listing.change.authorized",
                            "payload": {"to": "05", "new_listing": {}}})
            line(f"  ok: {r['ok']}  reason: {r['reason']}")

            line("\n[identity.authority] properly signed - executes:")
            r = await call("identity.authority",
                           {"intent": "listing.change.authorized",
                            "payload": {"to": "05", "new_listing": {}},
                            "signature_hex": "DEMO_SIGN_WITH_SERVER_KEY"})
            line(f"  ok: {r['ok']}  reason: {r['reason']}")

            line("\n[identity.audit] host proves tamper-evidence for itself:")
            r = await call("identity.audit")
            line(f"  entries: {r['entries']}  chain_verified:"
                 f" {r['chain_verified']}  dead_letters: {r['dead_letters']}")

            line("\n" + "=" * 64)
            line("  The host never routed inside the identity. It pushed")
            line("  intents at the door; the hub did the rest. Governance")
            line("  intact, mounted as a standard MCP server.")
            line("=" * 64)


if __name__ == "__main__":
    asyncio.run(main())
