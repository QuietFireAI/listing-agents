# SWARM.md - Framework Manifest + Swarm-Level Decisions (v0.1 (ratified 2026-07-11))

Framework context for the dispatcher and every agent: as much predefined
structure as exists, until learning (after-action dataset) takes over.
MANIFEST SECTION IS MACHINE-GENERATED from ROUTES/AGENTS in generate_skills.py
 -  regenerate via gen_meta.py; hand-edits here will be overwritten and are a
defect, not a change.

## Manifest (generated)
- Agents: 21 (00-dispatcher + 20 spokes)
- Routes: 35 entries, 34 distinct intents
- Playbooks: P01-P15 (playbooks/)
- Layer stack: MANNERS.md → DISPATCHER_CORE.md → identity/ → DECISIONS.md
  (per agent) → playbooks/ → agent SKILL.md files
- Track principle: the ROUTE-SPACE IS CLOSED. Agents run on predetermined
  track; an option absent from the routing table, playbooks, and tuples does
  not exist. Trains request routes; only the hub lines switches. Content-space
  is BOUNDED (manners, compliance verdicts) but not closed - generative freight
  is why inspection exists (17, verify_swarm, after-action).
- Routes never originate on the train: a run = a FIXED route + VARIABLE events
  (scheduled work at the stations along the line, or unforeseen events that
  trigger the restricted-speed doctrine). Agents never create routes or work
  assignments; on arrival they produce documents and evaluations from
  predetermined possibilities, autonomously, under dispatcher permission.
- Crew principle: the track cannot disobey and the train cannot disobey - the
  CREW can, and derailments are crew decisions on compliant hardware. In this
  swarm the model is the crew, not the train. Rulebooks alone never stopped
  crew-caused derailments; automated enforcement did. Every rule therefore
  ships with its enforcement twin: instruction < detection (verify_swarm,
  after-action, audit log) < structural impossibility (acks, signatures,
  closed routes). Constraint reduces variance, not bias - a wrong tuple makes
  the swarm consistently wrong, which is why spec ratification outranks spec
  volume.
- Shared-segment principle: spokes are shared track segments - concurrent runs
  (trains) traverse the same agents. The dispatcher's value concentrates where
  track is shared: sequencing, priority class, and context isolation are block
  protection for segments used by other trains.
- Spokes:
- 01 Lead Capture Agent
- 02 Lead Qualification Agent
- 03 Lead Nurture Agent
- 04 Listing Description Agent
- 05 MLS & Listing Management Agent
- 06 Showing Scheduler Agent
- 07 Transaction Coordinator Agent
- 08 Document Collection Agent
- 09 Vendor Coordination Agent
- 10 Market Data Agent
- 11 Client Communication Agent
- 12 Marketing Campaign Agent
- 13 Buyer Search & Match Agent
- 14 CRM & Pipeline Agent
- 15 Financial Tracking Agent
- 16 After-Close & Referral Agent
- 17 Compliance & Fair Housing Agent
- 18 Calendar & Task Management Agent
- 19 Prospecting Agent
- 20 Social Media Monitoring Agent
- Intents: `asset.release`, `calendar.event`, `campaign.publish`, `clarification.request`, `client.message.request`, `client.message.send`, `config.update`, `content.review`, `content.verdict`, `data.package`, `data.request`, `date.trigger`, `deadline.alert`, `deliverable.release`, `doc.request`, `doc.status`, `escalation.*`, `integrity.violation`, `interaction.log`, `lead.captured`, `lead.nurture`, `lead.rescored`, `lead.signal`, `listing.change.authorized`, `listing.data`, `prospect.opportunity`, `record.request`, `record.response`, `report.package`, `showing.request`, `status.update`, `transaction.closed`, `vendor.request`, `vendor.schedule`

## Swarm-level decision tuples (predictable scenarios, pre-deliberated)
- (two playbooks match one trigger, run neither; clarification.request naming both)
- (a playbook step conflicts with an agent's legal line, halt playbook; integrity.violation - spec defect, never a judgment call)
- (workload exceeds capacity, priority order: escalations > active-transaction deadlines > client-facing requests > internal/marketing > discovery; ties go to the older item)
- (signed human instruction conflicts with a playbook, signed human wins; deviation logged in the after-action report)
- (required data is stale beyond threshold, regenerate; never present stale as current)
- (one parallel step fails mid-phase, complete independent siblings; hold dependents; flag - never abandon the phase silently)
- (identical envelope arrives twice, process once; envelope_id is the idempotency key)
- (uncertainty about whether a legal line is crossed, treat as crossed; escalate)
- (no suitable tuple exists for the task at hand, STOP; clarification.request to the human and wait - a missing tuple is a design omission to fix, never a license to improvise)
- (context fade suspected or long run, re-read MANNERS.md and own SKILL.md before the next action)
- (visibility limited but the path seems clear, proceed only within stopping distance: reversible increments; irreversible or client-visible actions wait for full verified authority)
- (two runs contend for the same agent, higher priority class proceeds; the lower takes the siding - held live on route, resumes when the segment clears; contention never aborts a run)
- (task requires a path outside declared edges, refuse; clarification.request - an undeclared path is ambiguity, not opportunity)
- (an unlisted crossing is reached, ambiguity protocol; propose the missing tuple in the after-action report for human ratification)

Status: v0.1 (ratified 2026-07-11) - manifest verified against generator data at generation
time; not runtime-tested.
