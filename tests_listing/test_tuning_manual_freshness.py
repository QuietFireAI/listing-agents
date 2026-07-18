"""TUNING_MANUAL freshness gate - the manual's enforcement twin.

Doctrine: every rule ships with its enforcement twin. The standing KPI
says the manual is updated in the same commit as any tunable - this
test makes a stale value a FAILING SUITE, not a hope. Found live
2026-07-18: three line-number references had already rotted silently;
values were still accurate only because the repo is young.

Every documented constructor default below is AST-verified against the
real __init__ signature. Add a row here when you add a row to the
manual - a tunable documented in one place but not the other is exactly
the drift this exists to catch.
"""
import ast
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (file, param, documented default as unparsed-AST string)
CONSTRUCTOR_TUNABLES = [
    ("dispatcher/listing_spokes.py", "record_response_timeout_days", "1"),
    ("dispatcher/listing_spokes_02.py", "hot_lead_sla_seconds", "300"),
    ("dispatcher/listing_spokes_03.py", "frequency_cap_per_week", "3"),
    ("dispatcher/listing_spokes_03.py", "legal_contact_hours", "(8,21)"),
    ("dispatcher/listing_spokes_04.py", "mls_char_limit", "800"),
    ("dispatcher/listing_spokes_06.py", "feedback_ask_cap", "2"),
    ("dispatcher/listing_spokes_06.py", "no_show_pattern_threshold", "2"),
    ("dispatcher/listing_spokes_07.py", "vendor_holdup_days", "7"),
    ("dispatcher/listing_spokes_08.py", "document_chase_cap", "3"),
    ("dispatcher/listing_spokes_10.py", "comp_minimum", "5"),
    ("dispatcher/listing_spokes_10.py", "staleness_days", "30"),
    ("dispatcher/listing_spokes_10.py", "retention_days", "730"),
    ("dispatcher/listing_spokes_10.py", "opinion_press_threshold", "2"),
    ("dispatcher/listing_spokes_11.py", "quiet_hours", "(21,8)"),
    ("dispatcher/listing_spokes_13.py", "max_matches_per_buyer_per_day", "5"),
    ("dispatcher/listing_spokes_15.py", "commission_rate", "0.08"),
    ("dispatcher/listing_spokes_17.py", "sla_days", "1"),
    ("dispatcher/listing_spokes_17.py", "near_miss_pattern_threshold", "3"),
    ("dispatcher/listing_spokes_18.py", "max_events_per_day", "8"),
    ("dispatcher/listing_spokes_18.py", "no_show_grace_minutes", "60"),
]

# (file, regex over a payload.get default, documented value)
PER_MESSAGE_DEFAULTS = [
    ("dispatcher/listing_spokes_03.py", r'spike_threshold",?\s*(\d+)', "50"),
    ("dispatcher/listing_spokes_12.py", r'spike_threshold",?\s*(\d+)', "50"),
    ("dispatcher/listing_spokes_06.py", r'buffer_minutes",?\s*(\d+)', "30"),
    ("dispatcher/listing_spokes_19.py", r'rank_threshold",?\s*([\d.]+)', "0.5"),
]


def _init_default(path, param):
    tree = ast.parse(open(os.path.join(ROOT, path)).read())
    found = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "__init__":
            args, defaults = node.args.args, node.args.defaults
            offset = len(args) - len(defaults)
            for i, a in enumerate(args):
                if a.arg == param and i >= offset:
                    found = ast.unparse(defaults[i - offset]).replace(" ", "")
    return found


def test_every_documented_constructor_default_matches_code():
    stale = []
    for path, param, doc in CONSTRUCTOR_TUNABLES:
        actual = _init_default(path, param)
        if actual != doc.replace(" ", ""):
            stale.append(f"{path} {param}: manual={doc} code={actual}")
    assert not stale, "TUNING_MANUAL is stale:\n" + "\n".join(stale)


def test_every_documented_per_message_default_matches_code():
    stale = []
    for path, pat, doc in PER_MESSAGE_DEFAULTS:
        m = re.search(pat, open(os.path.join(ROOT, path)).read())
        actual = m.group(1) if m else None
        if actual != doc:
            stale.append(f"{path} {pat}: manual={doc} code={actual}")
    assert not stale, "TUNING_MANUAL is stale:\n" + "\n".join(stale)


def test_manual_documents_every_tunable_and_carries_no_line_numbers():
    manual = open(os.path.join(ROOT, "docs", "TUNING_MANUAL.md")).read()
    missing = [p for _, p, _ in CONSTRUCTOR_TUNABLES if f"`{p}`" not in manual]
    assert not missing, f"tunables absent from the manual: {missing}"
    rotted = re.findall(r"listing_spokes_?\d*\.py:\d+", manual)
    assert not rotted, ("line-number references rot by design - use stable "
                        f"anchors (branch/function names): {rotted}")
