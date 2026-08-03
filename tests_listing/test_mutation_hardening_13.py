"""Mutation-hardening — spoke 13 (buyer search & match).

Closes the 12 spoke-13 true gaps. positive+negative per decision, each
verified to KILL its mutation.

Target lines (listing_spokes_13.py:line):
  135 136 137   (missing-hard-criteria detection)
  160           (unverified_financing flag)
  209           (budget match: price<=value + two None guards)
  291           (verdict==approved -> criteria added; resolved flag)
  367 368       (buyer-agreement gate + resolved flag)
  392           (requester_identity_verified relay flag)
  395           (data.package intent dispatch)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.signatures import Ed25519Signer, Ed25519Verifier
from dispatcher.listing_spokes_13 import Spoke13BuyerSearchMatch

IDENTITY_ROUTES = os.path.join(os.path.dirname(__file__), "..", "identity",
                               "routes.json")


def make_hub(tmp_path):
    signer = Ed25519Signer()
    verifier = Ed25519Verifier(signer.public_key_bytes())
    return Hub(Routes(IDENTITY_ROUTES),
               AuditLog(os.path.join(tmp_path, "a.jsonl")),
               signature_verifier=verifier.verifier())


def spoke(tmp_path):
    return Spoke13BuyerSearchMatch(make_hub(tmp_path))


def env(frm, intent, ctx, payload):
    return Envelope(from_agent=frm, to_agent="13", intent=intent,
                    client_context_id=ctx, payload=payload,
                    provenance={"source": "t"})


def persisted(hub, intent=None):
    return [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"
            and (intent is None or e["intent"] == intent)]


def clar(hub):
    return persisted(hub, "clarification.request")


def legal(hub):
    return hub.queues.get("escalation.legal_line", [])


# ------------------------------------------------- 135/136/137 missing hard
def test_13_missing_hard_criterion_holds_present_one_matches(tmp_path):
    """A hard criterion the listing lacks (or has as None) holds the match
    for clarification; a listing satisfying it does not. Kills the
    `k not in listing` / `is None` detection and the hard-filter."""
    s = spoke(str(tmp_path))
    s.buyer_criteria["b-1"] = [{"field": "garage", "value": True,
                               "hard": True}]
    s.hub.on_turn_start()
    # listing missing the hard 'garage' field -> hold
    s._match_one("b-1", {"listing_id": "L1", "price": 400000}, env("10", "x", "b-1", {}))
    assert any("incomplete on hard" in c["payload"]["reason"] for c in clar(s.hub)), \
        "a missing hard criterion must hold the match"

    s2 = spoke(str(tmp_path) + "_b")
    s2.buyer_criteria["b-2"] = [{"field": "garage", "value": True,
                                "hard": True}]
    s2.hub.on_turn_start()
    # listing HAS garage -> no incomplete-hold
    s2._match_one("b-2", {"listing_id": "L2", "price": 400000, "garage": True},
                  env("10", "x", "b-2", {}))
    assert not any("incomplete on hard" in c["payload"]["reason"]
                   for c in clar(s2.hub)), \
        "a satisfied hard criterion must not hold the match"

    s3 = spoke(str(tmp_path) + "_c")
    s3.buyer_criteria["b-3"] = [{"field": "garage", "value": True,
                                "hard": True}]
    s3.hub.on_turn_start()
    # listing has garage present but None -> still incomplete (137 is None)
    s3._match_one("b-3", {"listing_id": "L3", "price": 400000, "garage": None},
                  env("10", "x", "b-3", {}))
    assert any("incomplete on hard" in c["payload"]["reason"] for c in clar(s3.hub)), \
        "a hard criterion present-but-None must still hold"


# ------------------------------------------------------------ 160
def test_13_match_record_flags_expired_preapproval(tmp_path):
    """The match record's unverified_financing reflects preapproval_expired
    for the ctx. Set it True and confirm the flag rides through; default
    False otherwise."""
    s = spoke(str(tmp_path))
    s.buyer_criteria["pf-1"] = [{"field": "area", "value": "north",
                                "hard": False}]
    s.preapproval_expired["pf-1"] = True
    s.hub.on_turn_start()
    s._match_one("pf-1", {"listing_id": "L1", "price": 400000, "area": "north"},
                 env("10", "x", "pf-1", {}))
    rec = s.match_history["pf-1"][0]
    assert rec["unverified_financing"] is True, \
        "expired preapproval must mark the match record"

    s2 = spoke(str(tmp_path) + "_b")
    s2.buyer_criteria["pf-2"] = [{"field": "area", "value": "north",
                                 "hard": False}]
    s2.hub.on_turn_start()
    s2._match_one("pf-2", {"listing_id": "L2", "price": 400000, "area": "north"},
                  env("10", "x", "pf-2", {}))
    assert s2.match_history["pf-2"][0]["unverified_financing"] is False


# ------------------------------------------------------------ 209
def test_13_budget_criterion_counts_within_not_over(tmp_path):
    """_criteria_met_count: a listing at/under budget counts the budget
    criterion; over budget does not; a None price or None value never
    counts. Kills price<=value and both None guards."""
    s = spoke(str(tmp_path))
    s.buyer_criteria["c-1"] = [{"field": "budget", "value": 500000}]
    assert s._criteria_met_count("c-1", {"price": 450000}) == 1, \
        "under budget must count"
    assert s._criteria_met_count("c-1", {"price": 500000}) == 1, \
        "at budget (<=) must count"
    assert s._criteria_met_count("c-1", {"price": 550000}) == 0, \
        "over budget must NOT count (kills <= -> <)"
    assert s._criteria_met_count("c-1", {"price": None}) == 0, \
        "no price must not count (None guard)"
    s.buyer_criteria["c-2"] = [{"field": "budget", "value": None}]
    assert s._criteria_met_count("c-2", {"price": 450000}) == 0, \
        "no budget value must not count (None guard)"


# ------------------------------------------------------------ 291
def test_13_approved_verdict_adds_criteria_flagged_does_not(tmp_path):
    """content.verdict approved -> pending criteria are added to the buyer's
    filtering set; a non-approved verdict adds nothing. Plus the resolved
    status is emitted either way."""
    s = spoke(str(tmp_path))
    s.hub.on_turn_start()
    s.criteria_pending_review["v-1"] = [{"field": "pool", "value": True}]
    s.handle(env("17", "content.verdict", "v-1", {"verdict": "approved"}))
    assert any(c["field"] == "pool" for c in s.buyer_criteria.get("v-1", [])), \
        "approved verdict must add the pending criteria"

    s2 = spoke(str(tmp_path) + "_b")
    s2.hub.on_turn_start()
    s2.criteria_pending_review["v-2"] = [{"field": "pool", "value": True}]
    s2.handle(env("17", "content.verdict", "v-2", {"verdict": "flagged"}))
    assert not s2.buyer_criteria.get("v-2"), \
        "a flagged verdict must NOT add criteria to the filtering set"


# ------------------------------------------------------- 367/368/392
def test_13_showing_requires_buyer_agreement_and_relays_flags(tmp_path):
    """record.response with no buyer agreement -> legal escalation, no
    showing.request. WITH agreement -> showing.request to 06 carrying
    buyer_agreement_on_file True and the requester_identity_verified flag
    (392). Also emits the resolved status (367)."""
    s = spoke(str(tmp_path))
    s.hub.on_turn_start()
    s.pending_showing["sh-1"] = {"listing_id": "L1"}
    s.handle(env("14", "record.response", "sh-1",
                 {"buyer_agreement_on_file": False}))
    assert any("no signed" in str(e.get("trigger", "")) for e in legal(s.hub)), \
        "missing buyer agreement must escalate to legal"
    assert not persisted(s.hub, "showing.request"), \
        "no showing.request may go out without a buyer agreement"

    s2 = spoke(str(tmp_path) + "_b")
    s2.hub.on_turn_start()
    s2.pending_showing["sh-2"] = {"listing_id": "L2"}
    s2.handle(env("14", "record.response", "sh-2",
                  {"buyer_agreement_on_file": True,
                   "requester_identity_verified": True,
                   "entries": [{"payload": {"tier": "HOT"}}]}))
    sr = persisted(s2.hub, "showing.request")
    assert sr, "a valid agreement must release the showing.request"
    assert sr[0]["payload"]["buyer_agreement_on_file"] is True
    assert sr[0]["payload"]["requester_identity_verified"] is True, \
        "the identity-verified flag must relay (kills the 392 default flip)"
    assert sr[0]["payload"]["lead_tier"] == "HOT"


def test_13_showing_identity_flag_defaults_false(tmp_path):
    """392: absent requester_identity_verified defaults to False in the
    relayed showing.request."""
    s = spoke(str(tmp_path))
    s.hub.on_turn_start()
    s.pending_showing["sh-3"] = {"listing_id": "L3"}
    s.handle(env("14", "record.response", "sh-3",
                 {"buyer_agreement_on_file": True}))  # no identity flag
    sr = persisted(s.hub, "showing.request")
    assert sr and sr[0]["payload"]["requester_identity_verified"] is False, \
        "absent identity verification must default to False"


def test_13_buyer_agreement_defaults_false_when_absent(tmp_path):
    """368: with buyer_agreement_on_file ABSENT, the default is False, so the
    gate fires and escalates. Flip the default to True and no escalation
    happens -> this fails."""
    s = spoke(str(tmp_path))
    s.hub.on_turn_start()
    s.pending_showing["sh-abs"] = {"listing_id": "L9"}
    s.handle(env("14", "record.response", "sh-abs", {}))  # key absent
    assert any("no signed" in str(e.get("trigger", "")) for e in legal(s.hub)), \
        "absent buyer agreement must default to False and escalate"
    assert not persisted(s.hub, "showing.request"), \
        "absent agreement must not release a showing"


# ------------------------------------------------------------ 395
def test_13_data_package_intent_routes_wrong_intent_ignored(tmp_path):
    """data.package -> a neighborhood_package client.message.request to 11;
    a different intent does not enter that branch. Kills the 395 intent flip."""
    s = spoke(str(tmp_path))
    s.hub.on_turn_start()
    s.handle(env("10", "data.package", "dp-1",
                 {"neighborhood_data": {"schools": "sourced"}}))
    msgs = [e for e in persisted(s.hub, "client.message.request")
            if e["payload"].get("template") == "neighborhood_package"]
    assert msgs, "data.package must produce a neighborhood_package message to 11"
    assert msgs[0]["to_agent"] == "11"

    s2 = spoke(str(tmp_path) + "_b")
    s2.hub.on_turn_start()
    s2.handle(env("10", "record.response", "dp-2", {}))  # different intent
    assert not [e for e in persisted(s2.hub, "client.message.request")
                if e["payload"].get("template") == "neighborhood_package"], \
        "a non-data.package intent must not emit the neighborhood package"


# ------------------------------------------------------------ 135 (hard default)
def test_13_criterion_without_hard_key_is_not_hard(tmp_path):
    """The `hard` default is False: a criterion with NO 'hard' key must NOT
    be treated as a hard criterion, so a listing missing it is not held.
    The sweep flips this default False->True; a soft criterion exercises it."""
    s = spoke(str(tmp_path))
    # criterion with NO 'hard' key -> soft by default
    s.buyer_criteria["sd-1"] = [{"field": "pool", "value": True}]
    s.hub.on_turn_start()
    # listing missing 'pool' entirely; because pool is SOFT, it must NOT hold
    s._match_one("sd-1", {"listing_id": "L1", "price": 400000},
                 env("10", "x", "sd-1", {}))
    assert not any("incomplete on hard" in c["payload"]["reason"]
                   for c in clar(s.hub)), \
        "a criterion without hard=True must not force a hard-incomplete hold " \
        "(kills the 135 default False->True flip)"


# ------------------------------------------------------------ 291 (resolved flag)
def test_13_verdict_emits_resolved_review_status(tmp_path):
    """content.verdict emits an agent.status resolving the
    criteria_compliance_review wait with resolved=True. Kills the 291 flip."""
    s = spoke(str(tmp_path))
    s.hub.on_turn_start()
    s.criteria_pending_review["vr-1"] = [{"field": "pool", "value": True}]
    s.handle(env("17", "content.verdict", "vr-1", {"verdict": "approved"}))
    statuses = [e for e in persisted(s.hub, "agent.status")
                if e["payload"].get("waiting_on") == "criteria_compliance_review"]
    assert statuses and statuses[0]["payload"]["resolved"] is True, \
        "content.verdict must resolve the review wait (kills 291)"


# ------------------------------------------------------------ 367 (resolved flag)
def test_13_showing_response_emits_resolved_agreement_status(tmp_path):
    """record.response emits an agent.status resolving the
    buyer_agreement_verification wait with resolved=True. Kills the 367 flip."""
    s = spoke(str(tmp_path))
    s.hub.on_turn_start()
    s.pending_showing["sr-1"] = {"listing_id": "L1"}
    s.handle(env("14", "record.response", "sr-1",
                 {"buyer_agreement_on_file": True,
                  "requester_identity_verified": True}))
    statuses = [e for e in persisted(s.hub, "agent.status")
                if e["payload"].get("waiting_on") == "buyer_agreement_verification"]
    assert statuses and statuses[0]["payload"]["resolved"] is True, \
        "record.response must resolve the agreement-verification wait (kills 367)"
