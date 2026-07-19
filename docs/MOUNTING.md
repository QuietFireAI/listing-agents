# Mounting this identity as an MCP server

This identity ships a generic MCP wrapper (`dispatcher/identity_mcp.py`)
that exposes it as a standard **stdio MCP server** — so it mounts
identically on Hermes, OpenClaw, and Claude Desktop with zero per-host
code. The host's agent gets exactly four tools; it pushes intents at the
front door and the governed hub does all routing, signing, sealing, and
logging behind them. The host never routes inside the identity.

The server runs a **boot conformance gate**: it refuses to start unless
the closed track, the signer gate, and the audit chain are intact. The
governance guarantee travels with the server — it cannot be silently
mounted broken.

## The four tools

- `identity.describe` — read-only: the closed track (legal intents),
  playbooks, and governance guarantees. Call first.
- `identity.submit` — push ONE intent at the front door; the hub
  validates against the closed track, runs the playbook, enforces gates,
  returns result or refusal-with-reason.
- `identity.authority` — the signed-money lane; unsigned authority is
  rejected and the rejection is returned so you can see the gate work.
- `identity.audit` — hash-chain verification (entries, chain_verified,
  dead letters) so the host can prove tamper-evidence itself.

## Prerequisites

```
pip install -r requirements.txt   # includes the identity's deps
pip install mcp                   # the MCP SDK (stdio server/client)
```

## Prove it locally first

```
python3 tools/mcp_roundtrip.py
```
Spawns the server over stdio and calls all four tools through the real
MCP protocol — the exact transport the hosts use. You should see the
closed surface, a gated submit, unsigned money rejected, signed money
executed, and the chain verified.

## Hermes (Nous Research)

Hermes reads MCP servers from a profile's `config.yaml` under the
`mcp_servers:` map (keyed by server name). Add:

```yaml
# ~/.hermes/profiles/<profile>/config.yaml
mcp_servers:
  listing:
    command: python3
    args: ["-m", "dispatcher.identity_mcp"]
    env:
      IDENTITY_ROOT: /absolute/path/to/listing-agents
      PYTHONPATH: /absolute/path/to/listing-agents
```

Then in a Hermes session: `/reload-mcp`, and the four `identity.*` tools
are available. (Hermes can also consume the identity as a SKILL.md tap —
`hermes skills tap add QuietFireAI/listing-agents` — but the MCP mount
is what carries the governed runtime, not just the instructions.)

## OpenClaw

OpenClaw treats every skill as an MCP server. Add the server to your
OpenClaw MCP config (via the UI's MCP section or the config file):

```json
{
  "mcpServers": {
    "listing": {
      "command": "python3",
      "args": ["-m", "dispatcher.identity_mcp"],
      "env": {
        "IDENTITY_ROOT": "/absolute/path/to/listing-agents",
        "PYTHONPATH": "/absolute/path/to/listing-agents"
      }
    }
  }
}
```

OpenClaw hot-reloads MCP servers; the `identity.*` tools appear without a
restart.

## Claude Desktop

Same stdio server, in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "listing": {
      "command": "python3",
      "args": ["-m", "dispatcher.identity_mcp"],
      "env": {
        "IDENTITY_ROOT": "/absolute/path/to/listing-agents",
        "PYTHONPATH": "/absolute/path/to/listing-agents"
      }
    }
  }
}
```

## Governance note (why the surface is deliberately narrow)

Both Hermes and OpenClaw extend agents by letting the host LLM decide
when to call a tool. Exposing this identity's 14 internal agents as 14
free tools would hand routing back to the host and dissolve the closed
track — the identity would become a toolbox wearing the name, not a
governed system. The four-tool front door is the fix: the host can only
submit intents; the hub enforces the track, the signatures, the $0.00
rule, and the sealed custody internally. **The MCP shell is the open
socket; the governed hub is the licensed cartridge.**

## License

The MCP wrapper is the mounting socket; the identity it serves is
licensed under the QuietFire Identity License over an AGPL-3.0 floor (see
LICENSE). Running this as a network service in production requires a
commercial license or full AGPL compliance. The supported commercial
operating environment is TelsonBase.
