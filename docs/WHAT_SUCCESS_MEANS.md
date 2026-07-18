# WHAT_SUCCESS_MEANS.md

What each playbook delivers when it completes, what human work it
displaces, and what the human still owns. Written against each
playbook's ratified completion criteria — success here means the
criteria the swarm actually verifies, never a marketing paraphrase of
them.

**A note on numbers, stated once so it never has to be hedged again:**
this document names the tasks the swarm takes off a human's plate. It
does not quote hours-saved figures, because no deployment has produced
that data yet. When after-action reports from live use exist, measured
figures replace the task descriptions below. A confident-looking number
without a basis is a fabrication — the same rule the agents themselves
run on.

**The one sentence that covers everything:** the swarm does the
coordination, chasing, logging, drafting, and vigilance; the human
keeps every fiduciary decision (price, offers, legal questions), every
signature, and every judgment call the tuples route to them. Nothing
ships to a client, a platform, or the MLS without either a compliance
verdict, a signed authorization, or both.

---

## The listing lifecycle

### P01 — New Listing Onboarding
**Success:** MLS entry live-verified, syndication live-verified,
marketing campaign published *after* a compliance verdict, seller
informed, every step acked and logged. Verified live, not push-logged —
"we sent it" never counts as "it's up."
**Deliverables:** a live, marketed, fair-housing-reviewed listing:
MLS entry, portal syndication, description/captions/flyer/tour script,
launched campaign, seller notification, vendor bookings (photography),
calendar blocks, and a complete audit trail from signed agreement to
go-live.
**Human work displaced:** the entire go-to-market coordination chain —
ordering and chasing the photographer, moving photos to the MLS,
writing compliant listing copy, submitting it for review, entering the
listing, checking it actually appears on portals, launching ads,
telling the seller. One signed envelope in; a live listing out.
**The human still owns:** the list price (made before the playbook
starts, untouched by every step), the signed authorization, and any
flagged content.

### P02 — Price Adjustment
**Success:** new price live-verified on MLS and syndication, downstream
acks on file, seller informed.
**Deliverables:** consistent price everywhere at once — MLS, portals,
refreshed marketing assets (re-reviewed by compliance), seller
confirmation.
**Human work displaced:** the error-prone fan-out after a price
decision: updating every surface, catching the portal that didn't take,
rewriting assets that quote the old number.
**The human still owns:** the price decision itself. The swarm never
produces or suggests a number (see P23 for how it supports the
decision).

### P03 — Under-Contract Transition
**Success:** pending status live-verified, full deadline timeline
loaded and dated, initial document requests acked, client informed.
**Deliverables:** a dated deadline map (inspection, appraisal,
financing, closing) that Agent 07 then enforces, opened document-chase
threads, status consistency across platforms.
**Human work displaced:** building the transaction calendar by hand and
remembering to start every paper chase on day one.

### P08 — Offer to Acceptance
**Success:** the offer resolved — accepted (hands off to P03), rejected,
or expired — with every status transition and artifact on file.
**Deliverables:** a clean offer record with nothing verbal: every state
change backed by an artifact.
**Human work displaced:** offer status bookkeeping and the "what did we
ever hear back on that one" archaeology.
**The human still owns:** the negotiation and the accept/reject/counter
decision — every one of them, every time.

### P09 — Contract to Close
**Success:** every deadline satisfied-by-artifact or explicitly
human-resolved, closing executed by the human, `transaction.closed`
acked by CRM, finance, and after-close.
**Deliverables:** deadline enforcement with proof (a milestone isn't
"done" until the artifact is on file), vendor holdup escalation,
settlement figures passed verbatim into the books, and the close
fan-out that arms P10.
**Human work displaced:** transaction coordination — the daily "is the
appraisal back? did the lender confirm? who's chasing the survey?"
vigilance that otherwise consumes a coordinator or the agent's own
evenings. This is the playbook where missed-deadline risk lives, and
its success measure is the absence of surprises.

### P10 — Close & Post-Close Handoff
**Success:** the after-close program scheduled (30/90/365 check-ins),
the financial reconciliation record open, CRM in past-client state,
just-sold marketing either published post-verdict or explicitly skipped.
**Deliverables:** the transition from transaction to relationship,
armed automatically at close instead of "when someone remembers."
**Human work displaced:** the follow-up program that famously never
happens — the check-ins that produce referrals, scheduled by the clock
layer, not by memory.

### P05 — Expired/Withdrawn Wind-down
**Success:** status live-verified, **zero active marketing verified per
platform**, seller informed, record annotated, human disposition logged.
**Deliverables:** a clean stop. Marketing for a dead listing is a
compliance exposure and a seller-relationship wound; success is
verified absence, per platform.
**Human work displaced:** hunting down every ad, post, and syndication
of a listing that must stop being marketed today.

---

## The buyer side

### P06 — New Buyer Onboarding
**Success:** verified buyer agreement on file, profile live with the
buyer's criteria recorded verbatim, first matches delivered, consent
enforced.
**Deliverables:** a working buyer profile that produces matches from
day one — with the agreement verified before any showing can be
requested (a structural rule, not a habit).
**Human work displaced:** intake paperwork sequencing and the first
manual portal searches.

### P07 — Tour Day Coordination
**Success:** all tours executed or explicitly cancelled, confirmations
and feedback logged, buyer profile updated **only from explicit
statements** — never from inferred enthusiasm.
**Deliverables:** a sequenced tour day (buffer-enforced, notice-rule
compliant), confirmations, captured feedback.
**Human work displaced:** the phone-and-spreadsheet choreography of
lining up five showings with five listing agents in one afternoon.

### P22 — Buyer Feedback Match Refresh
**Success:** feedback logged, criteria delta recorded, a refreshed
compliance-cleared match set produced, buyer informed, next showings
requested or explicitly declined.
**Deliverables:** the feedback→criteria→new-matches loop closed the
same day instead of at the next check-in call.

---

## Lead flow

### P11 — Speed to Lead
**Success (per lead):** captured with consent state, deduped against
CRM, tiered with the rubric version recorded, routed, logged — inside
the configured SLA.
**Deliverables:** every inbound lead answered by process, not by
whoever's free: consent captured at first contact (TCPA exposure is
liability, not paperwork), duplicates caught before they double-count
the pipeline, hot leads escalated to a human inside a 5-minute SLA.
**Human work displaced:** being the bottleneck on inbound. The
industry's own speed-to-lead data is why the SLA default is 5 minutes;
the swarm's value is that the SLA holds at 9pm and mid-showing too.
**The human still owns:** every hot lead — the swarm's success is
getting it to them fast, never talking to it for them.

### P21 — Lead Rescore Cycle
**Success:** every lead in the cycle either re-scored with a logged
tier decision or explicitly recorded "no change / no data." **No lead
left in an unknown state.**
**Deliverables:** a pipeline whose tiers mean something this week, not
last quarter.
**Human work displaced:** the periodic pipeline scrub nobody does.

### P13 — Referral & Anniversary Cycle
**Success (per trigger):** touch sent (or held with a logged reason),
responses tiered into intake, **zero touches against opt-out flags —
verifiable from the CRM's own records.**
**Deliverables:** birthdays, anniversaries, and holidays worked as a
system. Long-cycle referral business without long-cycle memory.

### P24 — Prospecting Outreach
**Success (per run):** every surfaced prospect human-validated
(approved/rejected, logged) or dropped on a suppression record; **in
probation, zero autonomous outreach occurred.**
**Deliverables:** a validated prospect list with compliance gates
(expired/FSBO rules, DNC suppression, aggregate-only farm data) applied
before a human ever sees a name.
**The human still owns:** all actual outreach. Discovery is the swarm's
job; contact is not.

### P12 — Geographic Farm Campaign
**Success:** campaign published post-verdict to *general geography*,
responders entering standard intake, **zero targeting derived from
opportunity records — verifiable by audience-parameter logs.**
**Deliverables:** farm marketing with fair-housing discipline built into
the audience parameters, provably.

---

## Operations & vigilance

### P16 — Morning Operations
**Success:** the human has the brief; their verdicts (act / park /
discard) recorded.
**Deliverables:** one morning brief replacing the check-six-systems
ritual: overnight leads, today's deadlines, open waits, calendar
conflicts, anything held for review.

### P17 — End-of-Day Books
**Success:** a dated, hashed books object on the log; tomorrow's P16
precondition satisfied.
**Deliverables:** a daily close that makes "what happened yesterday" a
lookup, not a reconstruction — and makes gaps visible (the report names
logging gaps rather than smoothing over them).

### P18 — Seller Weekly Report
**Success:** send logged with approval reference, next cadence
scheduled.
**Deliverables:** the weekly seller update — showings, feedback,
activity — that sellers judge their agent by and agents dread
compiling. Approved before sending, every week, automatically
rescheduled.

### P19 — Property Access Custody
**Success:** the access register reconciled — every outstanding grant
inside its window, every expiry scheduled.
**Deliverables:** who can get into which property, until when, with
expiry enforced. Key custody as a ledger instead of a memory.

### P20 — Vacant Property Watch
**Success:** continuous until vacancy ends; each cadence cycle completes
**with proof artifacts on the log** — a check without an artifact
didn't happen.
**Deliverables:** documented vigilance over the highest-risk asset class
an agent touches.

### P14 — Complaint Response
**Success:** human resolution logged, holds released only by human
direction, context annotated; if public, **the human's response posted
by the human.**
**Deliverables:** immediate containment — outbound touches to the
complainant held, the thread escalated with the verbatim complaint —
with zero autonomous damage control. The swarm's success in a
complaint is speed of escalation and completeness of the hold, never
its own words in public.

---

## Decision support (the swarm informs, the human decides)

### P15 — CMA / Listing Appointment Prep
**Success:** market and property packages delivered **with full
provenance** and staleness inside threshold, thin data reported as
thin, prep time blocked on the calendar.
**Deliverables:** appointment-ready comp data where every number traces
to a source and a retrieval date — and a five-comp set that's really
three gets called three.
**The human still owns:** the CMA's conclusion and the pitch.

### P23 — Price Review Evidence
**Success:** a market package, an activity summary, and requested
financial context delivered, each verified on the log, the human's
decision recorded. **No number produced by the swarm.**
**Deliverables:** everything a price decision needs, nothing that
pretends to be the decision.

---

## What success means at the swarm level

Every playbook above shares four properties, and they are the actual
product:

1. **Completion is verified, never claimed.** Live-checks over push
   logs, artifacts over assurances, "anything unverified = reported as
   not complete" — the phrase appears in the ratified criteria because
   it is the criteria.
2. **The audit trail is the deliverable under the deliverable.** Every
   envelope acked and hash-chain-logged means every listing, lead, and
   deadline has a provable history — for the broker, for compliance,
   for the E&O carrier.
3. **The human's judgment is structurally protected, not politely
   deferred to.** Prices, offers, legal questions, and flagged content
   don't reach clients because no route exists for them to — the track
   is closed.
4. **The failure mode is a named hold, never a silent drop.** When a
   playbook can't complete, success is that someone knows, with a
   reason, on a queue — which is itself the value: the absence of the
   quiet failures that cost listings and clients.
