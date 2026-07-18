"""sweep_runner.py - the clock layer the swarm never had.

Gap named 2026-07-18: every time-based protection in this identity -
dedupe timeouts, deadline checks, no-show detection, SLA enforcement,
chase timeouts, post-close check-ins, digest flushes - was a check_*
method WAITING FOR A CALLER. Tests called them directly, so every suite
was green while a deployed swarm would never have noticed a stuck lead
or a missed showing. Real code that never ran.

This module is that caller. One entry point, run_daily_sweeps(hub,
spokes, today), walks every registered sweep against every live context.
Clock discipline is preserved: the runner never invents time - the
deployment's scheduler (cron, systemd timer, the console's own loop)
supplies `today`/`now_iso`, exactly the way tests always did. What
changes is that production now has the same caller tests had.

Every sweep runs inside a guard: one failing sweep NEVER silences the
rest (a broken check in agent 07 must not stop agent 18's no-show
detection), and every failure is declared on the audit log - never
swallowed. Results are returned per-sweep for the caller's own logging.

Wiring: SPOKE ATTRIBUTE NAMES are the contract. Each entry names the
attribute the spoke object must expose. Context discovery reads each
spoke's own state dicts - a sweep runs for the contexts its spoke
actually tracks, never a guessed universe.
"""
from __future__ import annotations


def _ctxs(*dicts) -> list[str]:
    out = []
    for d in dicts:
        for k in d:
            if k not in out:
                out.append(k)
    return out


def run_daily_sweeps(hub, spokes: dict, today: str,
                     now_iso: str | None = None) -> list[dict]:
    """spokes: {"01": spoke01, "06": spoke06, ...} - whichever subset is
    deployed. today: ISO date from the deployment's scheduler. now_iso:
    ISO datetime for intra-day sweeps (no-show grace); defaults to
    today's end-of-day so a daily-only scheduler still detects every
    slot that passed during the day."""
    now_iso = now_iso or f"{today}T23:59:59"
    results: list[dict] = []

    def run(agent: str, name: str, fn, *args):
        try:
            results.append({"agent": agent, "sweep": name,
                            "result": fn(*args)})
        except Exception as exc:  # one broken sweep never silences the rest
            hub.audit.append("sweep.error",
                             {"agent": agent, "sweep": name,
                              "error": f"{type(exc).__name__}: {exc}"})
            results.append({"agent": agent, "sweep": name,
                            "result": None, "error": str(exc)})

    s01 = spokes.get("01")
    if s01 is not None:
        for ctx in list(getattr(s01, "pending", {})):
            run("01", "record_response_timeout",
                s01.check_record_response_timeout, ctx, today)

    s06 = spokes.get("06")
    if s06 is not None:
        for ctx in list(getattr(s06, "confirmed_showings", {})):
            run("06", "showing_feedback",
                s06.request_showing_feedback, ctx, today)

    s07 = spokes.get("07")
    if s07 is not None:
        for ctx in list(getattr(s07, "timelines", {})):
            run("07", "deadlines", s07.check_deadlines, ctx, today)
            run("07", "vendor_holdups", s07.check_vendor_holdups, ctx, today)

    s08 = spokes.get("08")
    if s08 is not None:
        for ctx, docs in list(getattr(s08, "pending_requests", {}).items()):
            for doc_type in list(docs):
                run("08", "chase_timeout",
                    s08.check_chase_timeout, ctx, doc_type)

    s13 = spokes.get("13")
    if s13 is not None:
        for ctx in list(getattr(s13, "matches_today", {})):
            run("13", "match_digest_flush", s13.flush_match_digest, ctx)

    s16 = spokes.get("16")
    if s16 is not None:
        for ctx in list(getattr(s16, "closed_transactions", {})):
            run("16", "post_close_milestones",
                s16.check_post_close_milestones, ctx, today)

    s17 = spokes.get("17")
    if s17 is not None:
        for ctx in list(getattr(s17, "pending_reviews", {})):
            run("17", "review_sla", s17.check_sla, ctx, today)

    s18 = spokes.get("18")
    if s18 is not None:
        for ctx in list(getattr(s18, "showings", {})):
            run("18", "showing_no_show",
                s18.check_showing_no_show, ctx, now_iso)
        run("18", "morning_briefing", s18.generate_briefing, "morning")

    hub.audit.append("sweep.completed",
                     {"today": today, "sweeps_run": len(results),
                      "errors": sum(1 for r in results if r.get("error"))})
    return results
