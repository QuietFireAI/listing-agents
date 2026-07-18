# OPERATOR_TESTING_MANUAL.md — Test It Like a Customer Would

This is the script for operational testing: you, set up as a listing
agent the way any buyer of this product would be, driving the swarm
through the console with no Python — and a camera running. Every
scenario below names its **setup**, its **action**, and its **expected
observable outcome** — the exact JSON lines you should see on screen.
If what you see differs from what's written here, that difference is a
finding: capture it, don't work around it.

Two ground rules for the recordings. First, never cut between action
and outcome — the whole point of the footage is the unbroken line from
"I did this" to "the log shows this." Second, end every session with
`verify` on camera: the chain check and the anchor are the proof the
footage wasn't staged.

## One-time setup (Scene 0)

```
git clone https://github.com/QuietFireAI/listing-agents.git
cd listing-agents
pip install -r requirements.txt
python -m pytest tests_listing/          # expect: 420 passed
python tools/console.py init             # generates YOUR signing key
```

Expected on camera: the test count, then
`{"initialized": ..., "operator_key": "generated (0600)", ...}`.
That key is what makes you *you* to the swarm — every authority action
in the scenes below is cryptographically yours.

Console sessions run from JSON-Lines scripts. Put your synthetic data
into the payloads below (the placeholders are marked `<...>`); each
scene is one script file you run with
`python tools/console.py session <file>`.

**A limitation, stated rather than hidden:** each console session is
one continuous swarm. The audit log accumulates across sessions —
your record survives — but live spoke state (open waits, pending
timelines) does not yet rehydrate between invocations. Multi-day
scenarios therefore run inside one session using `sweep` with
advancing dates, which is exactly how the clock works in production
anyway. State rehydration from the log is named future work.

---

## Scene 1 — Identity: prove the signature matters (P-security)

Action script (`scene1.jsonl`):
```
{"do": "send", "from": "human", "to": "05", "intent": "listing.change.authorized", "ctx": "S1", "payload": {"field": "price", "value": 1}}
{"do": "authorize", "to": "05", "ctx": "S1", "payload": {"new_listing": {"beds": 3, "price": 450000, "features": ["garage"], "photo_detected_features": ["garage"]}, "authorize_go_live": true, "today": "<today>"}}
```
Expected: line 1 — `"result": {"status": "reject", "reason":
"unverified signature on authority intent"}`. Line 2 (same intent,
signed by your key) — `"status": "ack"`. **This is the opening shot of
any demo: the unsigned command bounces, yours lands.**

## Scene 2 — Configure your business (rubric, ruleset, roster)

Fill from your synthetic config:
```
{"do": "config", "to": "02", "ctx": "setup", "payload": {"rubric": {"budget_threshold": <n>, "budget_weight": 40, "timeline_days_threshold": 30, "timeline_weight": 40, "financing_weight": 20, "hot_threshold": 70, "warm_threshold": 40}, "version": "v1"}}
{"do": "config", "to": "17", "ctx": "setup", "payload": {"ruleset": {"prohibited_phrases": [{"phrase": "<your test phrase>", "rule_id": "FH-1"}], "state_rules": {}}, "version": "r1"}}
```
Expected: both `ack`. Then break it on purpose — resend the 17 config
with a phrase entry missing its `rule_id`. Expected: the malformed
ruleset is **rejected**, the prior one stays active, and a
clarification lands with the named problems. A config system that
swallows garbage is an approve-by-omission machine; film it refusing.

## Scene 3 — Speed to lead, hot path (P11)

Feed one HOT synthetic lead (budget over threshold, short timeline,
preapproved, full consent) as `{"do": "send", "from": "20", "to":
"01", "intent": "lead.signal", ...}`.
Expected, in order on screen: nothing sent to any client for this
context; a `DELIVERED_TO_HUMAN` escalation carrying the hot-lead
trigger and the SLA; and in a following `{"do": "show", "what":
"briefing"}` — the `hot_lead_human_response` wait sitting in
*currently_waiting* with your name on it. Then feed the SAME lead
again (same phone/email): expected — deduped against 14, never
double-counted. Then a lead with `"consent": {"call": "no", ...}`:
expected — captured, consent state recorded, and no outbound ever
fires for it in any later scene.

## Scene 4 — New listing, end to end (P01)

One `authorize` line with your synthetic property package and
`authorize_go_live: true`, then the external world answers:
```
{"do": "send", "from": "external", "to": "09", "intent": "vendor.event", "ctx": "<listing>", "payload": {"kind": "photography", "event_kind": "deliverable_report", "doc_type": "photo_package", "proof_artifact_present": true, "content_hash": "<hash>"}}
{"do": "show", "what": "external"}
```
Expected in `external`, in this order: `vendor.schedule` (the booking
left the swarm), then after the deliverable — `campaign.publish` and
`client.message.send` (the seller's live notice). Between them, on the
log: `content.review` to 17, an `approved` verdict, `asset.release` to
both 05 and 12, and `status.update: active` fanning to 11/12/14.
Also film the negative: send the vendor deliverable with
`"proof_artifact_present": false` first — expected, **nothing
releases**. The claim isn't the artifact.

## Scene 5 — Compliance catches your planted phrase (P01's HITL gate)

Put your Scene-2 test phrase into a listing's `features`. Expected:
`content.verdict: flagged` citing the exact phrase and rule; then a
second review of the corrected draft, `approved`; and no
`asset.release` payload anywhere containing the phrase. The loop is
flag → exact fix → approve — never "approved with a note."

## Scene 6 — Photos vs. data sheet (Agent 04's contradiction gate)

Send a listing where `features` claims something
`photo_detected_features` doesn't show (your synthetic "pool that
isn't there"). Expected: the asset **halts** into
`clarification.request` with both feature sets attached, and no draft
reaches 17.

## Scene 7 — Under contract to close (P03 → P09 → P10)

In one session: file the executed contract through 08
(`document.submission`, `opens_correctly: true`), flip status pending
(signed, with `signed_contract_artifact`), load the timeline (signed
`timeline_init` with your synthetic dates), then advance the clock:
```
{"do": "sweep", "today": "<day after a deadline you left unsatisfied>"}
```
Expected: `deadline.alert` with `"overdue": true` — once, not daily —
and if the milestone you left open is `financing_contingency`, a
legal-line escalation the same sweep. Then submit the settlement
statement with your synthetic `sale_price`; expected: 16 holds the
close date, 15's commission record shows
`sale_price × 0.08` labeled `computed_at_default_rate_0.08` (or your
explicit amount winning over it if you supplied one). Sweep to
close-date-plus-31: expected — the 30-day check-in fires, from the
clock, with nobody asking.

## Scene 8 — Showing bump needs both hotness AND you (P07 + decision #2)

Book a showing, then request a conflicting one claiming
`protected_deadline: true` with no `lead_tier`. Expected: **no bump
offer** — plain conflict sequencing, reason naming the tier gate.
Repeat with `lead_tier: "HOT"`: expected — the claim *holds* in the
human queue; the original showing stands. Then send your signed
`{"do": "config", "to": "06", "ctx": "<ctx>", "payload":
{"confirm_protected_bump": true}}`: expected — now the bump executes,
the displaced party gets a notice, and the whole two-gate chain is on
the log.

## Scene 9 — Complaint containment (P14)

Send a synthetic social complaint through 20 (`social.mention`,
`sentiment: "complaint"`, `is_viral: true`, your verbatim text).
Expected: a `DELIVERED_TO_HUMAN` escalation with the verbatim text and
viral priority — and then try to send that client a scheduled touch.
Expected: **held**, logged as `touch_held_complaint_hold`, nothing in
`external` for that context. Release it with your signed
`resolve_complaint_hold` and show the next touch flowing.

## Scene 10 — The books don't lie (P16/P17 + the chain)

```
{"do": "show", "what": "briefing"}
{"do": "show", "what": "eod"}
{"do": "show", "what": "anchor"}
```
Expected: the briefing lists every wait you left open across the
scenes above; the EOD report's `logging_gaps` is honest about anything
with state but no entries. Close the recording with:
```
python tools/console.py verify
```
`{"chain": {"ok": true, ...}}` — then, off camera, tamper with any
line of `console-state/console-audit.jsonl` and run verify again ON
camera: expected, `"ok": false` with the exact line named. That's the
closing shot: the record defends itself.

---

## Filing what you find

Anything that deviates from an Expected above: file it with the scene
number, the script line, the actual JSON output, and the anchor from
that session — that tuple is a complete, replayable bug report. The
absence of deviations across all ten scenes, on camera, with the chain
verified at the end, is the operational sign-off this manual exists to
produce.
