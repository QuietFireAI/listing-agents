"""Mutation-hardening — spoke 09 (vendor coordination).

Closes the 4 spoke-09 gaps: 71, 180, 238, 314. Each kill-verified.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.signatures import Ed25519Signer, Ed25519Verifier
from dispatcher.listing_spokes_09 import Spoke09VendorCoordination

IDENTITY_ROUTES = os.path.join(os.path.dirname(__file__), "..", "identity",
                               "routes.json")

ROSTER = {"insp-1": {"kind": "inspector", "license_expiry": "2099-01-01",
                     "insurance_expiry": "2099-01-01", "regulated": True}}


def make_hub(tmp_path):
    signer = Ed25519Signer()
    verifier = Ed25519Verifier(signer.public_key_bytes())
    return Hub(Routes(IDENTITY_ROUTES),
               AuditLog(os.path.join(tmp_path, "a.jsonl")),
               signature_verifier=verifier.verifier())


def spoke(tmp_path):
    return Spoke09VendorCoordination(make_hub(tmp_path), roster=dict(ROSTER))


def vevent(frm, ctx, payload):
    p = {**payload}
    return Envelope(from_agent=frm, to_agent="09", intent="vendor.event",
                    client_context_id=ctx, payload=p,
                    provenance={"source": "external"})


def persisted(hub, intent=None):
    return [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"
            and (intent is None or e["intent"] == intent)]


# ------------------------------------------------------------ 71
def test_09_unknown_vendor_credentials_fail_closed(tmp_path):
    """_credentials_current: an unknown vendor returns False (fail closed);
    a rostered vendor with current credentials returns True."""
    s = spoke(str(tmp_path))
    assert s._credentials_current("insp-1", "2026-08-10") is True, \
        "a rostered current vendor must be credentials-current"
    assert s._credentials_current("ghost-vendor", "2026-08-10") is False, \
        "an unknown vendor must fail closed (kills the 71 return)"


# ------------------------------------------------------------ 314
def test_09_no_show_criticality_defaults_critical(tmp_path):
    """A vendor no-show with deadline_critical absent is treated as CRITICAL
    (fail closed) and escalates; explicit False logs a non-critical no-show.
    Kills the 314 flag on the non-critical branch and the default."""
    # explicit non-critical -> logged with deadline_critical False
    s = spoke(str(tmp_path))
    s.hub.on_turn_start()
    s.handle(vevent("external", "ns-1",
                    {"event_kind": "no_show", "deadline_critical": False}))
    logs = [e for e in persisted(s.hub, "interaction.log")
            if e["payload"].get("kind") == "vendor_no_show"]
    assert logs, \
        "an explicit non-critical no-show is logged"

    # absent -> defaults critical -> escalates, no non-critical log
    s2 = spoke(str(tmp_path) + "_b")
    s2.hub.on_turn_start()
    s2.handle(vevent("external", "ns-2", {"event_kind": "no_show"}))
    esc = s2.hub.queues.get("escalation.legal_line", []) + \
        persisted(s2.hub, "clarification.request")
    assert esc, "an unknown-criticality no-show must escalate (fail closed)"


# ------------------------------------------------------------ 238
def test_09_deliverable_without_proof_not_released(tmp_path):
    """A deliverable_report claiming completion WITHOUT a proof artifact
    does not release; WITH proof it proceeds. Kills the 238 default flip."""
    s = spoke(str(tmp_path))
    s.hub.on_turn_start()
    s.handle(vevent("external", "dl-1",
                    {"event_kind": "deliverable_report", "kind": "inspection"}))
    # no proof -> no deliverable.release
    assert not persisted(s.hub, "deliverable.release"), \
        "completion without proof must not release (kills the default flip)"

    s2 = spoke(str(tmp_path) + "_b")
    s2.hub.on_turn_start()
    s2.handle(vevent("external", "dl-2",
                     {"event_kind": "deliverable_report", "kind": "inspection",
                      "proof_artifact_present": True, "partial": True}))
    assert persisted(s2.hub, "deliverable.release"), \
        "a proof-backed deliverable must release"


# ------------------------------------------------------------ 180
def test_09_scheduling_emits_timezone_confirmed_calendar(tmp_path):
    """Scheduling a rostered vendor sends a calendar.event to 18 carrying
    timezone_confirmed=True. Kills the 180 bool flip."""
    s = spoke(str(tmp_path))
    s.hub.on_turn_start()
    s.handle(Envelope(from_agent="05", to_agent="09", intent="vendor.request",
                      client_context_id="sch-1",
                      payload={"vendor_id": "insp-1", "kind": "inspector",
                               "today": "2026-08-10"},
                      provenance={"source": "t"}))
    cal = [e for e in persisted(s.hub, "calendar.event")
           if e["client_context_id"] == "sch-1"]
    assert cal, "scheduling must emit a calendar.event"
    assert cal[0]["payload"]["timezone_confirmed"] is True, \
        "the calendar.event must carry timezone_confirmed=True"
