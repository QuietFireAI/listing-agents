# listing-agents

**A 20-agent real-estate listing swarm on a closed track — every message
routed by one hub, every route pre-approved, every action on a
hash-chained audit log.**

This is the listing-agents identity: [dispatcher-agents](https://github.com/QuietFireAI/dispatcher-agents)
wearing the
residential real-estate listing domain. The dispatcher installs the
governance chassis; this identity supplies the domain. Zero runtime code
difference between identities — that's the point.

## What it does

Twenty spoke agents cover the listing lifecycle end to end: lead capture
and qualification, nurture, listing description, MLS management, showing
scheduling, transaction coordination, document collection, vendor
coordination, market data, client communication, marketing, buyer
matching, CRM pipeline, financial tracking, after-close referral,
compliance/fair-housing, calendar and tasks, prospecting, and
social-media monitoring. Twenty-four ratified playbooks (P01 new-listing
onboarding through P24) choreograph them, each with explicit
human-in-the-loop gates. `docs/WHAT_SUCCESS_MEANS.md` states, per
playbook, what completion delivers, what human work it displaces, and
what the human still owns — no invented metrics.

## Why trust it

The railroad doctrine, in code:

- **Closed track.** Only the (sender, intent, receiver) tuples in
  `identity/routes.json` are legal. Anything else is rejected and
  logged, never silently dropped. 51 routes across 21 agents,
  machine-verified by the blueprint's `verify_swarm.py` (0 failures,
  0 warnings).
- **Hash-chained audit log.** Every entry carries SHA-256 prev/entry
  linkage from a GENESIS anchor. `verify_chain()` names any tamper,
  deletion, or reorder by line number. The log is the single source of
  truth.
- **Signed authority, fail-closed.** Money-lane and authority intents
  require a human sender with a cryptographic signature checked against
  a ratified signer registry (crypto signature → registry identity →
  IdP session liveness → hash-chained signer stamp). Unsigned,
  tampered, spoke-forged, expired, and not-yet-effective all DENY.
  Config templates ship UNRATIFIED and refuse to arm until you edit,
  date, and sign them off. Absence of an expected artifact never means
  "proceed" — it means human review.
- **Honest uncertainty.** A detection pillar that can't load declares
  itself UNARMED on the audit log instead of crashing or pretending.
  Placeholders that remain (Twilio credentials, two ratified numeric
  thresholds) are declared in code comments and `docs/TUNING_MANUAL.md`,
  and fail loudly, not silently.

## Install

```
git clone https://github.com/QuietFireAI/listing-agents.git
cd listing-agents
pip install -r requirements.txt
python -m pytest tests_listing/
```

Expect the full suite green from a bare clone — that's a maintained
guarantee, not an aspiration. `requirements.txt` installs the six-pillar
detection tier (open-mind, before-turn, pre-response-selfcheck,
agent-open-mind, sleep-marks, splitvantage) from their own repositories.
Without them the swarm still routes — every absent pillar declares
UNARMED — but production should run fully armed. Do not vendor pillar
code into this repo; import-from-package is the anti-drift mechanism.

## Run it

- `tools/console.py` — the operator console.
- `dispatcher/sweep_runner.py` — the clock layer.
  `run_daily_sweeps(hub, spokes, today)` fires every time-based check
  (deadlines, no-shows, SLAs, chase timeouts, digest flushes) from your
  scheduler; sweep errors are declared as `sweep.error`, never
  swallowed.
- `docs/OPERATOR_TESTING_MANUAL.md` — a filmable, step-by-step operator
  test script.
- External adapters (`vendor.schedule`, `client.message.send`,
  `campaign.publish`) are seam-complete and tracked in
  `docs/TUNING_MANUAL.md` until wired to your providers. The SMS
  notifier is a real Twilio implementation with declared placeholder
  credentials — it fails with a 401, not a fake success, until you
  supply real ones.

## Reading order

New here? `docs/START_HERE.md` is the 60-second version.
`docs/PLAY-BY-PLAY.md` narrates what actually happens, step by step, in
every playbook. `docs/JOB_DESCRIPTIONS.md` is one entry per agent.
`docs/PLAYBOOKS.md` is all 24 playbooks with triggers, agents deployed,
and HITL gates.

## Layout

- `dispatcher/` — vendored dispatcher-agents core (hub, core, pillars,
  analysis, kpi, territory, loader, signer_registry) plus the 20
  `listing_spokes*.py` identity spokes. Kept byte-identical to core via
  `tools/sync_core.py --check` (CI-enforced; exit 1 = drift).
- `identity/routes.json` — the closed track: every legal
  (sender, intent, receiver) tuple.
- `config/` — business content the code can't guess (templates,
  cadences, milestones, vendor panel, authority signers). Edits go to
  **both** this repo and listing-agents-blueprint, or fork drift
  returns.
- `docs/TUNING_MANUAL.md` — every configurable numeric parameter,
  updated in the same commit that introduces any tunable, ENFORCED by
  `tests_listing/test_tuning_manual_freshness.py`.
- `docs/JOB_DESCRIPTIONS.md`, `docs/PLAYBOOKS.md` — GENERATED by the
  blueprint's `gen_docs.py` from the ratified SKILL.mds. Regenerate
  there, never hand-edit here.
- `tests_listing/` — the full suite, including end-to-end runs of all
  24 playbooks driven only by their real triggers and external events.
  Run it from a fresh clone before trusting any "done" claim, including
  this README's.

## Provenance

The ratified source for every agent's SKILL.md, DECISIONS.md, and the
routes themselves is
[listing-agents-blueprint](https://github.com/QuietFireAI/listing-agents-blueprint).
This repo is the working build generated against that blueprint — never
hand-edit one without the other. Fork drift is a named defect class
here, and the tooling exists because it happened.

## License

Dual-licensed under the **QuietFire Identity License** (see `LICENSE`) over
an **AGPL-3.0** floor (see `LICENSE-AGPL`). Evaluation, development, and
internal testing — including cloning, running the suite, and any demo — are
free. **Production and commercial use require a paid license from
QuietFireAI or full AGPL-3.0 compliance.** Building derivative identities
for third parties is not permitted without a commercial license. The
supported commercial operating environment is TelsonBase. The open chassis
this runs on (dispatcher-agents) is Apache-2.0 and separate.

*License text is a placeholder pending counsel review.*
