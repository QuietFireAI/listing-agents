"""Mutation-hardening — spoke 19 (prospecting), remaining gaps.

Fable covers 19:239. This closes 19:157 (DNC suppression status) and
19:242 / 19:245 (the two resolved flags on the data.package status). Each
kill-verified.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.signatures import Ed25519Signer, Ed25519Verifier
from dispatcher.listing_spokes_19 import Spoke19Prospecting

IDENTITY_ROUTES = os.path.join(os.path.dirname(__file__), "..", "identity",
                               "routes.json")


def make_hub(tmp_path):
    signer = Ed25519Signer()
    verifier = Ed25519Verifier(signer.public_key_bytes())
    return Hub(Routes(IDENTITY_ROUTES),
               AuditLog(os.path.join(tmp_path, "a.jsonl")),
               signature_verifier=verifier.verifier())


def persisted(hub, intent=None):
    return [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"
            and (intent is None or e["intent"] == intent)]


def discovery(ctx, listing_id, contact):
    return Envelope(from_agent="external", to_agent="19",
                    intent="discovery.feed", client_context_id=ctx,
                    payload={"listing_id": listing_id, "zip_code": "44001",
                             "source": "approved_src", "status": "new",
                             "owner_contact": contact, "today": "2026-08-10"},
                    provenance={"source": "external"})


# ------------------------------------------------------------ 157
def test_19_dnc_contact_suppressed_clean_contact_not(tmp_path):
    """An owner contact on the DNC list marks the opportunity dnc_status
    'on_dnc'; a contact not on the list is 'not_on_dnc'. Kills the 157
    `contact in self.dnc_list` flip."""
    hub = make_hub(str(tmp_path))
    s = Spoke19Prospecting(hub)
    s.zip_codes.add("44001")
    s.dnc_list.add("555-0100")
    hub.on_turn_start()
    hub.send(discovery("d-1", "L1", "555-0100"))   # on DNC
    assert s.opportunities["L1"]["dnc_status"] == "on_dnc", \
        "a DNC contact must be marked on_dnc"
    hub.send(discovery("d-1", "L2", "555-0200"))   # not on DNC
    assert s.opportunities["L2"]["dnc_status"] == "not_on_dnc", \
        "a non-DNC contact must be marked not_on_dnc (kills the flip)"

    # NO-contact case: exercises the ternary's `else False` branch (the sweep's
    # actual 157 mutation is that False -> True). A record with no owner
    # contact must be not_on_dnc; flipping the else to True would wrongly
    # suppress a contactless record.
    hub.send(Envelope(from_agent="external", to_agent="19",
                      intent="discovery.feed", client_context_id="d-1",
                      payload={"listing_id": "L3", "zip_code": "44001",
                               "source": "approved_src", "status": "new",
                               "today": "2026-08-10"},  # no owner_contact
                      provenance={"source": "external"}))
    assert s.opportunities["L3"]["dnc_status"] == "not_on_dnc", \
        "a record with no contact must be not_on_dnc (kills the else-False flip)"


# ------------------------------------------------------------ 242/245
def test_19_data_package_resolves_both_waits_true(tmp_path):
    """data.package emits TWO agent.status envelopes to 18, both with
    resolved=True (market_context_enrichment and farm_data_aggregate).
    Assert resolved=True on each so the 242 and 245 flags are both pinned."""
    hub = make_hub(str(tmp_path))
    Spoke19Prospecting(hub)
    hub.on_turn_start()
    hub.send(Envelope(from_agent="10", to_agent="19", intent="data.package",
                      client_context_id="dp-1", payload={"anything": 1},
                      provenance={"source": "t"}))
    statuses = {e["payload"]["waiting_on"]: e["payload"].get("resolved")
                for e in persisted(hub, "agent.status")
                if e["from_agent"] == "19"}
    assert statuses.get("market_context_enrichment") is True, \
        "market_context_enrichment must resolve True (kills 242)"
    assert statuses.get("farm_data_aggregate") is True, \
        "farm_data_aggregate must resolve True (kills 245)"
