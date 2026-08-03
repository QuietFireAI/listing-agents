# Final Sweep Ledger — a72c398 + provenance hardening

Method: full regenerated mutation sweep (every single-op comparison flip +
every bool-literal flip) across all 19 listing spoke modules = **624 mutations**,
fast-filtered per spoke (agent tests + hardening files), all fast-filter
survivors re-tested against the FULL suite (518 tests, Twilio network test
deselected). PYTHONDONTWRITEBYTECODE=1 throughout; tree verified clean between
every mutation; every restore confirmed via git status.

Result at a72c398: **604 killed by the full suite, 20 full-suite survivors, 0 skips.**

All 20 survivors were the same constant class, previously misclassified as
noise in the Round-1 CrossPol and reclassified here as real provenance-integrity
gaps:
- `verbatim_available: True` in each module's `_env` provenance (19 modules:
  02:35, 03:23, 04:27, 05:27, 06:23, 07:24, 08:36, 09:21, 10:26, 11:28, 12:24,
  13:31, 15:25, 16:29, 17:23, 18:22, 19:22, 20:24, listing_spokes.py:39)
- `escalation_flag=False` default in `listing_spokes._env` (listing_spokes.py:34)

Closed by `test_mutation_hardening_provenance.py` (19 parametrized tests).
Kill-verified: 20/20 mutations RED against the new file, GREEN clean.

Cross-checks also verified this pass, each against the exact mutation and the
claiming test file (per-file RED, clean GREEN, raw pytest output in session log):
- All 105 contract mutations (original_105_gaps.txt): killed by the full suite.
- All 8 fable_found_gaps closures: killed by their claiming files
  (18:228 by hardening_18; 06:83/06:333/11:139 by hardening_undisclosed;
  180x2/323/573 by hardening_01_14).
- 09:314 trap restoration: real assertion back, kills again via hardening_09.

Post-hardening state: **537 passed, 1 deselected** (518 + 19 provenance).
Full-suite survivor count: **0**.
