# Tuning Manual — listing-agents

Every numeric/timing parameter in this codebase that a human might
reasonably want to retune, in one place. Compiled by a direct code sweep
(constructor signatures + inline threshold comparisons), not from memory —
if a parameter existed and wasn't listed here on the first pass, that was
a real gap, not an intentional omission.

**Re-swept 2026-07-17** (3rd full pass): found 6 more constructor-level
parameters missed in the first sweep because they're dict/set-shaped, not
simple numerics - the original grep pattern only caught `int`/`float`
defaults. Also found 2 core-level (dispatcher-agents, not listing-agents)
parameters that were self-flagged in their own code comments as
provisional and never actually ratified - discussed directly with the
owner the same day and ratified as deliberate placeholders (see their own
section below), rather than either silently treated as settled or left
open.

**How to retune any of these:** each one is a constructor keyword argument.
Change it at the point where the agent is instantiated (wherever your
deployment wires up the swarm), e.g.:

```python
Spoke02LeadQualification(hub, hot_lead_sla_seconds=180)
```

No source-code edits required for anything in this table — that's the
point of exposing them as constructor parameters instead of hardcoding
them inline.

---

## TOP OF LIST — Deliberate placeholders & unratified configs (read before deployment)

Full stub sweep 2026-07-18: every placeholder in the codebase is listed
here. Nothing else is stubbed, faked, or silently defaulted. If it's not
in this table it's real, wired code.

| Item | Where | Status | What blocks / what to do |
|---|---|---|---|
| Twilio credentials | `dispatcher/notifier.py` (`TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` env vars) | **PLACEHOLDER — declared** | Real Twilio implementation; with placeholder creds every send fails with a genuine 401, never a fake success. Set both env vars for production. |
| SMS destination | `dispatcher/notifier.py` (`+1-555-555-0100`) | **PLACEHOLDER — declared** | NANP reserved-for-fiction block, cannot reach a real phone. Replace with the operator's number at deployment. |
| `loop_threshold=20` | core `dispatcher/hub.py` | **RATIFIED as deliberate placeholder (owner, 2026-07-17)** | No empirical basis existed to pick a better number; revisit after production traffic. |
| MANNERS `N=10` | core `dispatcher/hub.py` | **RATIFIED as deliberate placeholder (owner, 2026-07-17)** | Same: deliberate, revisit with real data. |
| `config/authority_signers.json` | both repos | **UNRATIFIED — fails closed** | Genuinely empty template; needs your actual IdP logins. Loader refuses to arm while the `_status` UNRATIFIED line stands. No signing authority exists until you edit, date, and sign off. |
| `config/vendor_panel.json` | both repos | **UNRATIFIED — fails closed** | Genuinely empty template; needs your actual vendor relationships. Same fail-closed rule. |
| External adapters (`vendor.schedule`, `client.message.send`, `campaign.publish`) | seam contracts in INTEGRATIONS.md | **SEAM-COMPLETE, NOT WIRED** | Tracked in the adapter table below until connected to real providers. |
| `approved_by: <owner name/date>` fields | `config/message_templates.json` (both repos) | **UNRATIFIED — awaiting owner sign-off** | Drafted template bodies are proposals; fill in name/date per template to ratify the wording. |

Ratification path for the config files: review content → edit → change
`_status` to `RATIFIED` with date + sign-off → load. Fail-closed until
then is the invariant, not an inconvenience.

---

## Ratified (owner-approved 2026-07-16)

| Agent | Parameter | Value | Controls |
|---|---|---|---|
| 01 (Lead Capture) | `record_response_timeout_days` | `1` | Days a dedupe check (record.request to 14) can go unanswered before retrying once, then holding with handoff.failed |
| 02 (Lead Qualification) | `hot_lead_sla_seconds` | `300` (5 min) | Response-time SLA for a HOT-tier lead before an alert fires |
| 03 (Lead Nurture) | `frequency_cap_per_week` | `3` | Max nurture-sequence touches per client per calendar week |
| 03 (Lead Nurture) | `legal_contact_hours` | `(8, 21)` | Allowed daily window (24h clock) for sending a scheduled nurture touch - outside it, or on a flagged legal holiday, the touch holds rather than sending |
| 04 (Listing Description) | `mls_char_limit` | `800` | Character budget before length-cut logic engages (adjectives cut before facts, attributions never cut) |
| 06 (Showing Scheduler) | `feedback_ask_cap` | `2` | Max times a showing-feedback request is sent before giving up |
| 06 (Showing Scheduler) | `no_show_pattern_threshold` | `2` | No-shows from the same showing agent before flagging a pattern |
| 07 (Transaction Coordinator) | `vendor_holdup_days` | `7` | Days a vendor-scheduling wait can sit unconfirmed before escalating |
| 08 (Document Collection) | `document_chase_cap` | `3` | Chase attempts on a missing document before escalating as genuinely missing |
| 10 (Market Data) | `comp_minimum` | `5` | Comps required before a set is *not* flagged "thin" |
| 10 (Market Data) | `staleness_days` | `30` | Age at which a comp is dropped as stale rather than shipped |
| 10 (Market Data) | `retention_days` | `730` (2 yrs) | Historic-data window beyond which "absent" is the answer, never reconstructed |
| 10 (Market Data) | `opinion_press_threshold` | `2` | Repeated pricing-opinion asks before escalating (1st ask = refuse only) |
| 11 (Client Communication) | `quiet_hours` | `(21, 8)` | Window (24h clock) during which non-exempt sends queue instead of going out |
| 17 (Compliance) | `sla_days` | `1` | Turnaround SLA for a compliance verdict before an SLA-breach alert fires |
| 17 (Compliance) | `near_miss_pattern_threshold` | `3` | Flagged verdicts from the same submitting agent before a pattern report goes to the owner |
| 18 (Calendar & Task) | `max_events_per_day` | `8` | Calendar capacity before a day is treated as overloaded |
| 18 (Calendar & Task) | `no_show_grace_minutes` | `60` | Minutes past a showing slot with no post-showing signal from 06 before 18 reports a calendar-detected `showing.no_show` (invoked by `dispatcher/sweep_runner.py` `run_daily_sweeps`, which now calls every `check_*` sweep in production - added 2026-07-18). PROVISIONAL - no empirical basis, revisit with after-action data |

## Found 2026-07-17, not yet reviewed by owner (dict/set-shaped, missed in the first sweep)

These are real constructor parameters, already live with working defaults
- but unlike the table above, they were never individually walked through
with you on 2026-07-16. Flagging them as such rather than silently
folding them into "Ratified," since a dict-shaped default is a business
rule, not just a number, and deserves its own look.

| Agent | Parameter | Current default | Controls |
|---|---|---|---|
| 03 (Lead Nurture) | `spike_threshold` (in `behavioral.signal` payload, not a constructor arg - see "Per-message defaults" below) | `50` | Engagement-score threshold that pauses a nurture sequence and triggers a rescore (tuples 5/8) - a **separate** value from Agent 12's own `spike_threshold` below, despite the identical name and default |
| 05 (MLS & Listing Mgmt) | `required_artifacts` | `{"sold": "closing_artifact", "pending": "signed_contract_artifact"}` | Which artifact must be on file before a status change to that value is allowed |
| 06 (Showing Scheduler) | `min_notice_hours` | `{"default": 24}` | Minimum notice required for a showing on an occupied property; per-state overrides go in this dict (e.g. `{"CA": 48, "default": 24}`) |
| 08 (Document Collection) | `expected_senders` | `{}` (empty) | Per-context allowlist of who may submit a sensitive document type (preapproval letter, proof of funds, closing statement). **Empty by default means every sensitive document is quarantined until a context has an explicit allowlist entry** - correct, fail-closed behavior, but means this agent quarantines everything sensitive out of the box until populated. |
| 09 (Vendor Coordination) | `roster` | `{}` (empty) | Approved-vendor list (id -> kind/license/insurance/regulated). **Empty by default means every single vendor request is refused** (tuple 6: not on approved list) until this is populated - this agent does nothing useful out of the box. |
| 11 (Client Communication) | `exempt_alert_classes` | `set()` (empty) | Alert classes exempt from the quiet-hours gate (e.g. a genuinely time-critical alert type that should wake the human instead of queuing) - none configured means nothing is exempt, everything queues during quiet hours |

## Per-message defaults (not agent-level config — legitimately vary by caller/platform)

These aren't retuned at agent construction time; each caller can override
them per envelope. The values below are just the fallback when a caller
doesn't specify one.

| Agent | Field | Default | Where it's set | Controls |
|---|---|---|---|---|
| 03 (Lead Nurture) | `spike_threshold` (in `behavioral.signal` payload) | `50` | `listing_spokes_03.py` `behavioral.signal` branch | See note in the table above - distinct from Agent 12's, same name and default by coincidence, not shared code |
| 06 (Showing Scheduler) | `buffer_minutes` (in `showing.request` payload) | `30` | `listing_spokes_06.py` `_schedule()` | Minimum spacing enforced between two showings on the same context before they're treated as conflicting. Missed in the original sweep - became load-bearing only once the buffer-enforcement fix landed (2026-07-16); before that it was captured but never compared against anything, so its default value had no functional effect. |
| 12 (Marketing Campaign) | `spike_threshold` (in `platform.metrics` payload) | `50` | `listing_spokes_12.py` `platform.metrics` branch | Engagement value above which a spike feeds back into nurture (Agent 03) |
| 19 (Prospecting) | `rank_threshold` (in `discovery.feed` payload) | `0.5` | `listing_spokes_19.py` `discovery.feed` branch | Minimum rank-basis-strength before a rank is included rather than presented unranked |

To change the *fallback* value for any of these (not a one-off
override), edit the source line directly — there's no single agent-level
constructor knob for these, by design, since different platforms may
legitimately warrant different defaults.

## Ratified as deliberate placeholders (owner, 2026-07-17) — core, not this repo

These two live in **dispatcher-agents** (the shared core, not this repo),
which is why they weren't caught in a listing-agents-only sweep before.
Both were previously self-flagged in their own code/doc comments as never
having been truly decided - "PROVISIONAL AND ARBITRARY, no empirical
basis" was the literal language at the source. **Discussed directly with
the owner 2026-07-17: both ratified as deliberate placeholders, not left
open.** Neither number has empirical backing - that was true before
ratification and remains true now - but "nobody has decided" and "we've
decided to accept this placeholder" are different states, and these are
now the second one. Revisit when real after-action/fade-rate data exists.

| Location | Parameter | Value | Controls |
|---|---|---|---|
| `dispatcher-agents/dispatcher/hub.py` `Hub.__init__` | `loop_threshold` | `20` | Max envelopes per `(client_context_id, intent)` pair before the hub suspends the loop into `clarification.request`, treating it as a possible runaway |
| `dispatcher-agents/MANNERS.md` §Re-injection | backstop `N` | `10` | Agent turns allowed with no other MANNERS re-injection trigger (phase gate, post-compaction) before a mandatory turn-backstop re-injection fires anyway |

## Ratified 2026-07-17 (owner decisions #1, #3, #4, #6 — this session)

| Agent | Parameter | Value | Controls |
|---|---|---|---|
| 15 (Financial Tracking) | `commission_rate` | `0.08` | DEFAULT rate used ONLY when a close carries `sale_price` but no explicit `commission_amount`. Explicit amount always wins; computed amounts are labeled `computed_at_default_rate_*`, never presented as recorded figures. Owner-ratified starting point (decision #1); revisit against real closings |
| 04 (Listing Description) | `config/description_cut_priority.json` | `["adjectives", "unattributed_facts"]` | Cut order when remarks exceed `mls_char_limit` (first entry cut first). Attributions never cut — structurally absent from the cut classes (decision #3). File finally exists; tuple 11 always named it |
| 04 (Listing Description) | photo/data contradiction detection | symmetric set difference | When `photo_detected_features` is present, ANY feature in the data sheet not in the photos, or in the photos not in the data sheet, halts the asset into clarification (decision #4, delegated). A caller-supplied `photo_data_contradictions` flag also halts; neither path suppresses the other. Not a numeric knob — documented here so its behavior is known outside the source |
| dispatcher-agents `SignerRegistry.check` | `effective` date enforcement | temporal, fail-closed | A signer entry with no effective date, an unparseable one, or a future-dated one DENIES (decision #8 answer: the field was schema-present, enforcement-absent — `check()` never read it). `today` defaults to the real system date; tests pass it explicitly |

### Added 2026-07-18 (owner decisions #2 refinement, #7)

| Agent | Parameter | Value | Controls |
|---|---|---|---|
| 13 (Buyer Search & Match) | `max_matches_per_buyer_per_day` | `5` | Individual match pings per buyer per day. Beyond it, matches join a ranked end-of-day digest (`flush_match_digest`, sweep-called): ranked by stated-criteria-met desc, ties newest-first. Nothing silently dropped — every match logs to 14 individually (decision #7) |
| 06 (Showing Scheduler) | protected-deadline bump tier gate | `HOT` only | A protected-deadline claim only earns a HITL bump OFFER when `lead_tier` (relayed by 13 from 14's CRM records, where 02 logged it — never client-writable) is HOT. Not hot or unknown: plain conflict sequencing, no offer (decision #2). The bump itself still executes only on the human's signed `confirm_protected_bump` |

## External adapters — NOT YET WIRED (owner decision #6: tracked here until clean)

Three intents route to the virtual destination `external` and currently
**dead-letter at runtime** (benign `no handler for external` path — audited,
not notified). Nothing leaves the swarm until a real adapter registers a
handler for `"external"` on the hub:

| Intent | Sender | What's dark until an adapter exists |
|---|---|---|
| `vendor.schedule` | 09 | Every vendor booking (photography, inspector, appraiser…) |
| `client.message.send` | 11 | Every client-facing message the swarm composes |
| `campaign.publish` | 12 | Every campaign publish AND retract action |

Update this table (and delete rows) as each adapter lands. A deployment
with these dark is a swarm that talks to itself.

## Also worth knowing about (not tuning-manual material, but adjacent)

- **`dispatcher-agents/dispatcher/notifier.py`**: `TWILIO_ACCOUNT_SID` /
  `TWILIO_AUTH_TOKEN` default to literal placeholder strings, and the
  destination number is a fictional NANP placeholder
  (`+1-555-555-0100`). This isn't a tunable threshold, it's a real
  production dependency that doesn't exist yet - already flagged
  previously as the actual last-mile gap in the escalation/notification
  path (everything upstream works; nobody is listening on the other end
  yet). Restating it here because it's the same *category* of "not yet
  a real decision" as the two items above, even though it's a credential,
  not a number.
- **`listing-agents-blueprint/TESTING_MANUAL.md`** (v2, supersedes
  TESTING_MANUAL_v1.md): a separate artifact from this file - it's a
  prompting-based test protocol for adopting this identity's spec on a
  different runtime (Hermes/OpenRouter/NVIDIA-hosted model), not a
  tuning reference for the Python dispatcher code. Verified 2026-07-17,
  still accurate: its Phase 1 static-state claims (227 tuples across 21
  agents, `verify_swarm.py` clean) check out against the current repo.
  Mentioned here only so it doesn't get lost/forgotten as a separate
  "one place" that nobody remembers exists.

## Config files (separate from code — business content, not tuning knobs)

Six JSON files under `config/` hold business content that genuinely
can't be guessed by code: `vendor_panel.json`, `authority_signers.json`,
`cadence_settings.json`, `message_templates.json`, `milestone_map.json`,
`transaction_milestones.json`. Five of the six now have proposed draft
content grounded in what the code actually implements or the playbooks
already ratified — review, edit, and change `_status` to `RATIFIED` when
approved. `vendor_panel.json` and `authority_signers.json` remain
genuinely empty — they need your actual vendor relationships and IdP
logins, which no amount of drafting on my end can substitute for.

`cadence_settings.json` (2026-07-17): now covers all 15 tunables in the
Ratified table above, not 9 - added `no_show_pattern`, `pricing_opinion_
press`, and `near_miss_pattern`, the three "N occurrences before X"
thresholds missed in the first draft.

`transaction_milestones.json` (new, 2026-07-17): covers Agent 07's
post-contract transaction milestones (inspection, appraisal, title,
etc.) - deliberately split from `milestone_map.json`, which only ever
covered the pre-contract *listing* lifecycle (intake through
showing_active). These aren't overlapping duplicates of the same thing;
they're genuinely two different milestone concepts, now each with their
own real config. Unlike the other five, this one **actually wires into
running code** - `Spoke07TransactionCoordinator` now accepts
`transaction_milestone_config` as a real constructor parameter (default
reproduces the exact prior hardcoded values), and this file's `entries`
are exactly that default's content in ratifiable JSON form. Editing the
JSON alone doesn't change agent behavior yet - whoever wires the swarm
still needs to load this file and pass its content into the constructor,
same as every other config file in this list - but there's now a real
parameter to receive it, not just documentation describing what's
hardcoded. Not covered: the per-milestone business logic in Agent 07's
doc.status handling and the financing_contingency deadline case - see
the file's own `_not_covered_here` field and the class docstring for why.

## Maintenance note

Found and fixed 2026-07-17: `cadence_settings.json`, `message_templates.json`,
and `milestone_map.json` had real drafted content here in listing-agents
that was never propagated back to listing-agents-blueprint (the supposed
ratified source) - blueprint still had the bare `<example>` placeholder
template for all three. `vendor_panel.json`/`authority_signers.json` were
correctly identical in both (genuinely empty in both, as intended). Synced
all four config files (including the new `transaction_milestones.json`)
to blueprint. If you edit any `config/*.json` file going forward, it needs
updating in **both** repos, or the same drift happens again.

If you add a new agent or a new threshold, add it to this table in the
same commit — that's the whole point of keeping this centralized instead
of scattered across code comments. A parameter not in this table is a
parameter nobody outside the source code knows exists.
