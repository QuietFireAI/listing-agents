# Push Log

Every commit pushed to the four in-scope repos, in chronological order.
Created 2026-07-17 per direct instruction, after a legitimate question
about whether pushes were actually landing when claimed. Existing history
below was reconstructed retroactively from each repo's real `git log`
output (timestamps and hashes are actual commit metadata, not
reconstructed from memory). Going forward, a new row is added to this
file in the SAME commit that makes the change it describes - not
after the fact, not in a separate pass.

**Repos in scope:** `listing-agents`, `listing-agents-blueprint`,
`dispatcher-agents`, `dispatcher-agents-blueprint`. No other repos
(freight-agents, freight-agents-blueprint, the six pillar packages)
are tracked here - those are explicitly out of scope per direct
instruction.

**How to verify any row yourself:** `git clone` the repo, `git log
--format="%H %ai %s"`, and confirm the hash/timestamp/message match.
Don't take this file's word for it - that's the whole point of it
existing.

| Timestamp (UTC) | Repo | Commit | What |
|---|---|---|---|
| 2026-07-16 01:52:58 UTC | listing-agents | `af97abb0c2` | Working listing-agents build: agents 01, 14, 02-09 - built and tested against full spec |
| 2026-07-16 01:58:13 UTC | listing-agents | `b969d9d4ff` | Add Agent 10 (Market Data): comp/neighborhood packages, provenance-gated, no-opinion-field structural design |
| 2026-07-16 02:06:00 UTC | listing-agents | `147dd8e2f5` | Fix systemic fail-open defaults across agents 08, 09, 10 |
| 2026-07-16 02:13:13 UTC | listing-agents-blueprint | `707e46fc1e` | Fix 8 real gaps in ratified routes.json, found during build-out |
| 2026-07-16 02:16:58 UTC | listing-agents | `b24272c03b` | Add Agent 11 (Client Communication) - single voice for routine client updates, implements reply-routing-by-content for real |
| 2026-07-16 02:35:49 UTC | listing-agents | `5e8c5dba0f` | Re-review fix: route client-requested showings through 13, not directly to 06 |
| 2026-07-16 02:39:42 UTC | listing-agents | `dc8311a2df` | Fix showing.request properly instead of routing around it |
| 2026-07-16 02:51:30 UTC | listing-agents | `f2387eb028` | Add Agent 18 (Calendar & Task) + real HITL wait-status visibility |
| 2026-07-16 03:01:22 UTC | listing-agents | `990b0c4571` | Complete agent.status retrofit across all remaining agents (02, 03, 06, 09, 11) |
| 2026-07-16 14:00:29 UTC | dispatcher-agents | `e1cdaf0c91` | Fix two real hub-core bugs: clarification.request silently dead-lettering, crashed handlers with no active notification |
| 2026-07-16 14:00:59 UTC | listing-agents | `ee35f80967` | Fix two real hub-core bugs found while addressing HITL error-visibility gap |
| 2026-07-16 14:08:49 UTC | listing-agents | `f91de8b59d` | Add real SMS human_notifier (Twilio), re-review agent 11 fix |
| 2026-07-16 14:08:51 UTC | dispatcher-agents | `61afe89026` | Add real SMS human_notifier (Twilio) - real implementation with a clearly-marked, trivially-replaceable placeholder destination number (NANP 555-01xx reserved-for-fiction range). 6 new tests, 84/84 passing. |
| 2026-07-16 14:14:54 UTC | listing-agents | `702cc2a33d` | Add Agent 12 (Marketing Campaign) - CCP gate, compliance-first publishing, verdict-locked assets |
| 2026-07-16 14:26:06 UTC | listing-agents | `926cfa88f4` | Agent 12 re-review: fix two real bugs found, not just add tests |
| 2026-07-16 14:33:24 UTC | listing-agents | `6c3055939d` | Add Agent 13 (Buyer Search & Match) - verbatim criteria, anti-steering, buyer agreement gate |
| 2026-07-16 14:56:02 UTC | listing-agents | `4bb758b14c` | Add Agent 15 (Financial Tracking) - highest PII sensitivity, wire-adjacent full stop, verified-only figures |
| 2026-07-16 15:01:50 UTC | listing-agents | `a43cf5d68b` | Fix real bugs found by systematic sweep, not by trusting 'clean pass' |
| 2026-07-16 15:09:06 UTC | listing-agents | `5fd208abda` | Add Agent 16 (After-Close & Referral) - all six standing checks actually run and shown this time |
| 2026-07-16 19:27:24 UTC | listing-agents | `334f721f40` | Add Agent 17 (Compliance & Fair Housing) - the contract every other agent has been building against |
| 2026-07-16 19:35:52 UTC | listing-agents | `7a0aa98640` | Agent 17 re-review: real bug found and fixed by tracing the resubmission logic by hand |
| 2026-07-16 19:44:12 UTC | listing-agents | `4ef521f51f` | Add Agent 19 (Prospecting) - deep pass done before reporting done, not after being asked twice |
| 2026-07-16 19:50:56 UTC | listing-agents | `b60a6b9472` | Agent 19 re-review: found and fixed a real cross-agent contract bug, not a false-confidence pass |
| 2026-07-16 19:56:22 UTC | listing-agents | `b84cb5a838` | Add Agent 20 (Social Media Monitoring) - the last of 20. Deep pass done before writing tests, not after. |
| 2026-07-16 20:50:37 UTC | listing-agents | `adb41da8f0` | Wire agent.status as a standard capability across all agents with a real wait, not selectively |
| 2026-07-16 21:24:48 UTC | listing-agents | `354823bf72` | Ratify 15 tunables, expose 6 more that were hardcoded inline, propose real config drafts, add TUNING_MANUAL.md |
| 2026-07-16 21:25:39 UTC | listing-agents-blueprint | `2702ad3297` | Patch identity/routes.json with all routing gaps found and fixed during the working build |
| 2026-07-17 00:18:53 UTC | listing-agents-blueprint | `944406fba5` | Add lead.inbound route: direct call/web-form/text intake into Agent 01 |
| 2026-07-17 00:19:06 UTC | listing-agents | `6ebade48fb` | Add lead.inbound route: direct call/web-form/text intake into Agent 01 |
| 2026-07-17 00:35:33 UTC | dispatcher-agents | `53affa853d` | Fix stale counts and a real path bug found during listing-agents review |
| 2026-07-17 00:42:08 UTC | listing-agents-blueprint | `4a71f02ace` | Reconcile generate_skills.py drift: 22 routes, agent.status entirely missing |
| 2026-07-17 00:42:14 UTC | listing-agents | `aa715c59d1` | Sync SKILL.md files with reconciled generate_skills.py output (blueprint 4a71f02) |
| 2026-07-17 01:13:46 UTC | listing-agents-blueprint | `16178a57d9` | Agent 05 review fixes: add 05->17 compliance.notice route (tuple 11) |
| 2026-07-17 01:13:59 UTC | listing-agents | `64fd128da2` | Fix four real gaps found in Agent 05 review, not just flag them |
| 2026-07-17 01:52:51 UTC | listing-agents | `04667b1f50` | Fix three real gaps found in Agent 02 review |
| 2026-07-17 01:55:08 UTC | listing-agents | `d7cf1c668b` | Fix real bug in Agent 03: frequency complaint silently killed the sequence |
| 2026-07-17 01:57:40 UTC | listing-agents | `79778f9445` | Fix two real bugs in Agent 06's double-booking logic |
| 2026-07-17 02:10:58 UTC | listing-agents | `4ea62f79b3` | Resolve Agent 03 tuples 4/10 ambiguity: confirmed distinct scenarios (owner, 2026-07-16) |
| 2026-07-17 02:58:02 UTC | listing-agents | `0635d49300` | Add missing buffer_minutes entry to TUNING_MANUAL.md (standing KPI miss) |
| 2026-07-17 03:01:45 UTC | listing-agents | `9188b73d15` | Fix real fail-open bug in Agent 08: unconfigured sender allowlist skipped verification entirely |
| 2026-07-17 05:51:28 UTC | listing-agents-blueprint | `0db40869bd` | Add vendor.cancellation_notice route: Agent 09 review, tuple 1 fix |
| 2026-07-17 05:51:48 UTC | listing-agents | `3371d663ce` | Agent 09 review: real fix for tuple 1's dead notification path |
| 2026-07-17 08:02:30 UTC | listing-agents | `377b744277` | Fix three real gaps in Agent 10, document one structural gap honestly |
| 2026-07-17 08:09:11 UTC | listing-agents | `0125671b9b` | Fix two real gaps in Agent 11, flag one genuine playbook conflict |
| 2026-07-17 08:23:22 UTC | listing-agents | `12b5a771a2` | Agent 12 review: fix real retract gap, document a structural one |
| 2026-07-17 08:25:18 UTC | listing-agents | `533e73f8fd` | Agent 13 review: confirm prior fixes solid, document one structural gap |
| 2026-07-17 08:27:49 UTC | listing-agents | `2840d7777f` | Fix real gap in Agent 15: tax-conflict flag never persisted |
| 2026-07-17 08:36:13 UTC | listing-agents | `d98881295b` | Fix real gap in Agent 16: blocked touches were silently swallowed |
| 2026-07-17 08:38:42 UTC | listing-agents | `9fbf56e288` | Fix real gap in Agent 17: near-miss counter never reset |
| 2026-07-17 08:44:40 UTC | listing-agents | `6e63ee87d8` | Fix real gap in Agent 16: 4 of 5 touch types silently swallowed blocks |
| 2026-07-17 08:50:40 UTC | listing-agents | `ad44c1551b` | Agents 17-20 review: verified solid, one structural gap documented |
| 2026-07-17 12:06:37 UTC | listing-agents | `543558c725` | 3rd-pass tuning-manual sweep: 6 more real gaps, 2 core items never actually ratified |
| 2026-07-17 12:08:48 UTC | listing-agents | `b1bfc6c6a0` | Add missing showing_bumped_notice entry to message_templates.json |
| 2026-07-17 12:26:43 UTC | dispatcher-agents | `a857e2c723` | Ratify loop_threshold=20 and MANNERS N=10 as deliberate placeholders (owner, 2026-07-17) |
| 2026-07-17 12:26:59 UTC | listing-agents | `a4dfeeadb3` | Ratify loop_threshold=20 and MANNERS N=10 (owner, 2026-07-17); bump routes.json version |
| 2026-07-17 12:27:05 UTC | listing-agents-blueprint | `eee797c3a9` | Ratify MANNERS N=10 (owner, 2026-07-17); bump routes.json version to 0.19 |
| 2026-07-17 12:48:33 UTC | listing-agents | `69e4c667a7` | Wire Agent 07's transaction milestones for real (owner decision, 2026-07-17) |
| 2026-07-17 12:48:44 UTC | listing-agents-blueprint | `a4c8679540` | Sync config/*.json with listing-agents (mirrors 69e4c66) |
| 2026-07-17 13:18:21 UTC | dispatcher-agents | `bc702d5f78` | Fix real gap in hub.py: suspended loops had no recovery path at all |
| 2026-07-17 14:43:59 UTC | dispatcher-agents | `6a869d123b` | Fix systemic notification gap: 8 call sites silently queued without notifying |
| 2026-07-17 17:54:13 UTC | listing-agents | `c15846eea4` | Agent 01: fix the fail-open bug and close every gap found this time, not just report it |
| 2026-07-17 17:54:19 UTC | listing-agents-blueprint | `ef70b5c345` | Add Agent 01 as legal sender of client.message.request (tuple 15) |
| 2026-07-17 18:13:00 UTC | listing-agents | `2664234c0f` | Agent 14: fix 4 zero-code gaps, correct 1 misleading comment, verify 1 false alarm |
| 2026-07-17 18:26:37 UTC | dispatcher-agents-blueprint | `cc278f6a2a` | Populate dispatcher-agents-blueprint for the first time (owner decision, 2026-07-17) |
| 2026-07-17 18:33:50 UTC | listing-agents | `6e62876e03` | Agent 02 review: verify prior fixes solid, close one real gap in tuple 6 |
| 2026-07-17 18:41:00 UTC | listing-agents | *(this commit)* | Create PUSH_LOG.md, backfilled with every real commit from 2026-07-16 onward across all 4 in-scope repos |
| 2026-07-17 18:48:00 UTC | listing-agents | *(this commit)* | Agent 03 review: fix 3 real gaps (tuples 2/6 missing client confirmations, tuple 7 zero-code legal-hours/holiday gate) |
