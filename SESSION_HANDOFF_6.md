# SESSION_HANDOFF_6 — 2026-07-17/18 (Fable 5 session)

## Pushed state (remote-verified at time of push; LOCAL-ONLY commits noted)

**dispatcher-agents**
- `e73fe59` missing agent_open_mind crashed every handler delivery → fail-declared unarmed (PUSHED)
- `aac86f6` signer effective dates temporally enforced — was schema-present, enforcement-absent (PUSHED)
- `cedb61c` external audit anchor (anchor/verify_anchor) + CI workflow (LOCAL — needs push)
- Suite: 103/103.

**listing-agents**
- `0d332d4` core sync (105-line hub drift), 01→18 + 18→06 ratified edges implemented, route prune, requirements.txt + README, stale pillars_pth stripped (PUSHED)
- `9597d2a` 3 dead cross-agent contracts fixed (07→16 close_date, 07→15 commission_amount, 15↔14 request shape), lying test corrected (PUSHED)
- `85e5467` 09 no-vendor_id fail-open closed; AST fail-open sweep clean (PUSHED)
- `c9b5efb` owner decisions #1–#6 + #8 sync (PUSHED)
- `2f6cdbf` decisions #2-clarified (hotness-gated bump offers) + #7 (overload digest) (PUSHED)
- `f692705` sweep layer (sweep_runner.py), tools/sync_core.py, CI: fresh-clone job + core-parity job + unarmed job (LOCAL — needs push)
- `c5e47c1` end-to-end playbook tests + the 3 contract bugs they caught (LOCAL — needs push)
- Suite: 388/388 (from 98-failing at session start). Fresh-clone-from-GitHub verified at 2f6cdbf; re-verify after next push.

**listing-agents-blueprint**
- `58ea736` prune 15 from interaction.log senders (PUSHED)
- `26b9cd3` description_cut_priority.json added (PUSHED)
- verify_swarm: 0 failures, 0 warnings, 21 agents, 51 routes.

## Decisions made (owner-ratified this session)
1. commission_rate=0.08 default (explicit amount wins; computed labeled; neither → None visible). Money chain: 08 passes settlement figures verbatim → 07 forwards → 15.
2. Protected-deadline bumps: lead_tier must be HOT (relayed by 13 from 14's CRM records, where 02 logs it) to earn a HITL bump OFFER; the bump executes only on signed confirm_protected_bump.
3. config/description_cut_priority.json created (both repos), 04 reads it, unknown class raises.
4. (delegated) 04 photo/data contradiction: symmetric set-difference detection, both directions halt, caller flag preserved.
5. 07's human alerts (extension claims, offer status) → human queue, never 11's client template path.
6. External adapters tracked in TUNING_MANUAL until wired.
7. 13 overload: max_matches_per_buyer_per_day=5, ranked digest (stated-criteria-met desc, ties newest), flush via sweep, nothing dropped.
8. Signer `effective` dates: missing/unparseable/future all DENY.

## Audit results
- **Tuple-by-tuple, ALL 20 agents + core** (04–16 on 2026-07-17; 01/02/03/14/17/19/20 + dispatcher-agents core on 07-18): every DECISIONS.md tuple has real code or an honestly-declared structural gap. Core demos executed (all six pillars fire, KPI gate passes, demo chain verifies).
- **Six pillars**: all suites run — 87/87 total; taint_check read directly (fail-closed, correct).
- **Security runtime probes**: unsigned/tampered/forged authority all reject; hash chain names tamper at line; registry refuses to arm on every fail-closed condition; anchor detects wholesale regeneration and truncation.
- **Playbooks**: all 43 machine-extractable edges legal; P24's 3 "illegal" edges are the documented graduation tier.
- **End-to-end runs** (new): P01 three phases from the real signed trigger; HITL flag loop; lead-to-close with sweeps. Caught 3 contract bugs per-agent suites never could:
  - 09↔05 field mismatch (opens_correctly vs verified_openable) — P01 Phase 2 could never open
  - 01→02 wrapper crash (source-attribution dict vs int threshold) — every real lead dead-lettered, no tier
  - 01 never forwarded 02's scoring inputs at all

## Open items, priority order
1. **PUSH cedb61c, f692705, c5e47c1** (PAT was revoked mid-session by design). Fresh-clone verify after.
2. **Wire run_daily_sweeps into a real scheduler** (cron/systemd/console loop). The clock layer now exists and is e2e-proven, but production still needs the caller-of-the-caller.
3. **CI is committed but unproven** — the workflows have never executed on GitHub. First push triggers them; watch the first run (core-parity job clones core@main, so push dispatcher-agents first).
4. **Other nine identity repos still carry pre-sync core.** sync_core.py exists now; run it against each (adjusting CORE_FILES if identity surfaces differ) or accept drift knowingly.
5. **External adapters** (vendor.schedule, client.message.send, campaign.publish) — TUNING_MANUAL table tracks them.
6. Minors logged, not fixed: 14's deletion "freeze" is trace-only (no mechanical write-block); 17's ruleset adoption is unvalidated (no key/shape check); 07's offer_status→18 junk protected block already removed with decision #5.
7. Anchor discipline: adopt the practice — `hub.audit.anchor()` at session end, store the JSON outside the log (handoff doc or signed commit message). This handoff's anchor slot: run it in the next live session.
8. dispatcheragents.com hosting; production users behind INTEGRATIONS seams (unchanged).

## Proven procedures (this session)
- Push: validate PAT via curl (expect 200) → push per-repo with x-access-token URL → curl the remote HEAD SHA → fresh-clone → install → full suite.
- Drift check: `python tools/sync_core.py --core <dispatcher-agents> --check` (exit 1 = drift, per-file diff counts).
- Sweep invocation: `run_daily_sweeps(hub, spokes, today[, now_iso])` — spokes dict keyed by agent id; scheduler supplies the clock; errors declared as sweep.error, never swallowed.
- E2E discipline: drive ONLY the playbook trigger + external events; assert against artifacts; treat every fixture-vs-contract collision as a finding about one side or the other — three were code bugs, five were fixture errors, and the runs distinguished them.
