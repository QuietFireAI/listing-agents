"""Provenance-integrity hardening. Kills the final 20 full-suite survivors
of the 684-mutation sweep at a72c398: the `verbatim_available: True`
provenance constant in every spoke module's `_env` helper (19 modules) and
the `escalation_flag=False` default in `listing_spokes._env`.

Previously dismissed as noise; reclassified: flipping `verbatim_available`
makes every envelope a spoke emits carry falsified provenance, and flipping
the `escalation_flag` default marks every 01/14 emission as escalated.
Provenance honesty is doctrine (QMS is forgeable; provenance fields are the
non-crypto layer of the same claim), so the constants get pinned."""
import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

MODULES = ["dispatcher.listing_spokes"] + [
    f"dispatcher.listing_spokes_{nn:02d}"
    for nn in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 15, 16, 17, 18, 19, 20)]


@pytest.mark.parametrize("modname", MODULES)
def test_env_helper_provenance_is_verbatim_and_unescalated(modname):
    mod = importlib.import_module(modname)
    e = mod._env("99", "human", "interaction.log", "prov-1", {"k": 1})
    assert e.provenance["verbatim_available"] is True, \
        f"{modname}._env must stamp verbatim_available True - anything " \
        f"else falsifies provenance on every envelope this spoke emits"
    assert getattr(e, "escalation_flag", False) is False, \
        f"{modname}._env must not mark ordinary envelopes escalated"
