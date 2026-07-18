#!/usr/bin/env python3
"""run_sweeps.py - the production caller for the clock layer.

The last gap in the timing chain: dispatcher/sweep_runner.py existed
and was e2e-proven, but nothing in production invoked it - "the caller
of the caller" (SESSION_HANDOFF_6, open item 2). This is that caller.

Deployment (any ONE of these):
  cron:     0 7 * * *  cd /path/to/deployment && python tools/run_sweeps.py --state state.json
  systemd:  a timer unit invoking the same command
  console:  the operator console's own loop calling main() directly

Clock discipline holds: THIS process reads the wall clock exactly once,
at the top, and hands that single timestamp to run_daily_sweeps -
sweeps themselves never invent time, same as always. Pass --today /
--now-iso to override for replay or testing.

State: a live deployment holds spoke state in a long-running process;
this CLI is the reference wiring for one. Invoked standalone it builds
the swarm, restores nothing (state restoration is the deployment's
persistence layer, honestly out of scope here and SAID SO rather than
faked with a pickle), runs the sweeps against whatever the process
holds, prints the per-sweep results as JSON lines, and exits nonzero if
any sweep errored - so the scheduler's own failure alerting fires.
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def build_default_swarm(audit_path: str):
    """Reference construction - a real deployment imports its own
    already-running hub+spokes and calls run_daily_sweeps directly."""
    from dispatcher.core import Routes, AuditLog
    from dispatcher.hub import Hub
    from dispatcher.listing_spokes import Spoke01LeadCapture, Spoke14CRMPipeline
    from dispatcher.listing_spokes_06 import Spoke06ShowingScheduler
    from dispatcher.listing_spokes_07 import Spoke07TransactionCoordinator
    from dispatcher.listing_spokes_08 import Spoke08DocumentCollection
    from dispatcher.listing_spokes_13 import Spoke13BuyerSearchMatch
    from dispatcher.listing_spokes_16 import Spoke16AfterCloseReferral
    from dispatcher.listing_spokes_17 import Spoke17ComplianceFairHousing
    from dispatcher.listing_spokes_18 import Spoke18CalendarTask

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    hub = Hub(Routes(os.path.join(root, "identity", "routes.json")),
              AuditLog(audit_path))
    hub.register("human", lambda env: None)
    hub.register("external", lambda env: None)
    spokes = {"01": Spoke01LeadCapture(hub), "14": Spoke14CRMPipeline(hub),
              "06": Spoke06ShowingScheduler(hub),
              "07": Spoke07TransactionCoordinator(hub),
              "08": Spoke08DocumentCollection(hub),
              "13": Spoke13BuyerSearchMatch(hub),
              "16": Spoke16AfterCloseReferral(hub),
              "17": Spoke17ComplianceFairHousing(hub),
              "18": Spoke18CalendarTask(hub)}
    hub.on_turn_start()
    return hub, spokes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit-log", default="sweep-audit.jsonl")
    ap.add_argument("--today", default=None, help="ISO date override")
    ap.add_argument("--now-iso", default=None, help="ISO datetime override")
    args = ap.parse_args(argv)

    # The single wall-clock read.
    now = datetime.datetime.now()
    today = args.today or now.date().isoformat()
    now_iso = args.now_iso or now.replace(microsecond=0).isoformat()

    from dispatcher.sweep_runner import run_daily_sweeps
    hub, spokes = build_default_swarm(args.audit_log)
    results = run_daily_sweeps(hub, spokes, today=today, now_iso=now_iso)
    errors = 0
    for r in results:
        print(json.dumps(r))
        if r.get("error"):
            errors += 1
    print(json.dumps({"sweeps": len(results), "errors": errors,
                      "today": today, "anchor": hub.audit.anchor()}))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
