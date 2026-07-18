#!/usr/bin/env python3
"""sync_core.py - the mechanism that replaces hand-copying.

Found live 2026-07-17: dispatcher-agents core received fixes
(queue_and_notify, resume_loop_suspension, the unarmed pillar path) that
this repo's vendored dispatcher/ never got - 105 diff lines of fork
drift in hub.py alone, four silent-notification bugs still live here
after they were "fixed". The doctrine says vendored copies are generated
from core, never hand-copied; until this file existed there was no
generator, so every sync WAS a hand-copy.

Usage:
  python tools/sync_core.py --core ../dispatcher-agents          # sync
  python tools/sync_core.py --core ../dispatcher-agents --check  # CI gate

--check exits 1 on any drift and prints per-file diff-line counts.
Never syncs identity-owned files (listing_spokes*.py stay untouched);
never deletes; core is the single source for exactly the files below.
"""
import argparse
import filecmp
import os
import shutil
import subprocess
import sys

# The vendored core surface. Identity files (listing_spokes*.py) are
# deliberately absent - they are owned here, not in core.
CORE_FILES = [
    "__init__.py", "after_action.py", "analysis.py", "attestation.py",
    "core.py", "hub.py", "kpi.py", "loader.py", "notifier.py",
    "pillars.py", "priority.py", "runs.py", "signatures.py",
    "signer_registry.py", "territory.py",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--core", required=True,
                    help="path to a dispatcher-agents checkout")
    ap.add_argument("--check", action="store_true",
                    help="report drift and exit 1; change nothing")
    args = ap.parse_args()

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src_dir = os.path.join(os.path.abspath(args.core), "dispatcher")
    dst_dir = os.path.join(here, "dispatcher")
    if not os.path.isdir(src_dir):
        print(f"FATAL: {src_dir} is not a directory - fail closed, "
              f"syncing from nothing is not a sync", file=sys.stderr)
        return 2

    drifted = []
    for name in CORE_FILES:
        src, dst = os.path.join(src_dir, name), os.path.join(dst_dir, name)
        if not os.path.exists(src):
            print(f"FATAL: core is missing {name} - refusing to continue "
                  f"(a shrinking core surface is a question for a human, "
                  f"not a silent skip)", file=sys.stderr)
            return 2
        if not os.path.exists(dst) or not filecmp.cmp(src, dst, shallow=False):
            n = "new"
            if os.path.exists(dst):
                diff = subprocess.run(
                    ["diff", dst, src], capture_output=True, text=True)
                n = str(len(diff.stdout.splitlines()))
            drifted.append((name, n))

    if args.check:
        if drifted:
            print("CORE DRIFT DETECTED (vendored copy lags core):")
            for name, n in drifted:
                print(f"  {name}: {n} diff lines")
            print("Run: python tools/sync_core.py --core <path>")
            return 1
        print(f"sync_core --check: {len(CORE_FILES)} files, zero drift")
        return 0

    for name, _ in drifted:
        shutil.copy2(os.path.join(src_dir, name), os.path.join(dst_dir, name))
        print(f"synced: {name}")
    if not drifted:
        print("already in sync - nothing copied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
