import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, "/home/claude/pillars_pth")

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.listing_spokes_04 import Spoke04ListingDescription

IDENTITY_ROUTES = os.path.join(os.path.dirname(__file__), "..", "identity",
                               "routes.json")


def make_hub(tmp_path, **kw):
    audit_path = os.path.join(tmp_path, f"audit-{uuid.uuid4().hex[:8]}.jsonl")
    return Hub(Routes(IDENTITY_ROUTES), AuditLog(audit_path), **kw)


def listing_data(ctx, payload):
    return Envelope(from_agent="05", to_agent="04", intent="listing.data",
                    client_context_id=ctx, payload=payload,
                    provenance={"source": "spoke-05", "captured_at": "runtime",
                                "verbatim_available": True})


def verdict(ctx, payload):
    return Envelope(from_agent="17", to_agent="04", intent="content.verdict",
                    client_context_id=ctx, payload=payload,
                    provenance={"source": "spoke-17", "captured_at": "runtime",
                                "verbatim_available": True})


def persisted(hub, intent=None):
    events = [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"]
    return [e for e in events if intent is None or e["intent"] == intent]


def test_only_supplied_features_appear(tmp_path):
    hub = make_hub(str(tmp_path))
    spoke = Spoke04ListingDescription(hub)
    hub.on_turn_start()
    hub.send(listing_data("d-001", {"beds": 3, "baths": 2, "features": ["pool"]}))

    draft = spoke.drafts["d-001"]
    texts = [f["text"] for f in draft["facts"]]
    assert "3 bedrooms" in texts
    assert "pool" in texts
    assert not any("granite" in t for t in texts)  # never invented


def test_sqft_discrepancy_publishes_tax_record_never_averages(tmp_path):
    hub = make_hub(str(tmp_path))
    spoke = Spoke04ListingDescription(hub)
    hub.on_turn_start()
    hub.send(listing_data("d-002", {"sqft_tax_record": 1800, "sqft_seller_claim": 2000}))

    draft = spoke.drafts["d-002"]
    texts = [f["text"] for f in draft["facts"]]
    assert "1800 sq ft" in texts
    assert "1900 sq ft" not in texts  # never averaged
    assert any("discrepancy" in n for n in draft["notes"])


def test_unverifiable_feature_omitted_no_bare_claim(tmp_path):
    hub = make_hub(str(tmp_path))
    spoke = Spoke04ListingDescription(hub)
    hub.on_turn_start()
    hub.send(listing_data("d-003", {"roof_age": "5 years", "roof_age_source": None}))

    draft = spoke.drafts["d-003"]
    texts = [f["text"] for f in draft["facts"]]
    assert not any("roof" in t for t in texts)
    assert any("omitted" in n for n in draft["notes"])


def test_unverifiable_feature_per_seller_included_with_attribution(tmp_path):
    hub = make_hub(str(tmp_path))
    spoke = Spoke04ListingDescription(hub)
    hub.on_turn_start()
    hub.send(listing_data("d-004", {"roof_age": "5 years", "roof_age_source": "per_seller"}))

    draft = spoke.drafts["d-004"]
    roof_fact = next(f for f in draft["facts"] if "roof" in f["text"])
    assert roof_fact["attribution"] == "per seller (unverified)"


def test_photo_contradicts_data_halts_with_clarification(tmp_path):
    hub = make_hub(str(tmp_path))
    spoke = Spoke04ListingDescription(hub)
    hub.on_turn_start()
    hub.send(listing_data("d-005", {
        "features": ["no pool"],
        "photo_data_contradictions": ["photo shows a pool; data sheet omits it"],
    }))
    clar = persisted(hub, "clarification.request")
    assert clar
    assert "d-005" not in spoke.drafts


def test_photo_reveals_protected_class_flags_before_use(tmp_path):
    hub = make_hub(str(tmp_path))
    spoke = Spoke04ListingDescription(hub)
    hub.on_turn_start()
    hub.send(listing_data("d-006", {"photo_reveals_protected_class": True}))
    assert hub.queues["escalation.legal_line"]
    assert "d-006" not in spoke.drafts


def test_human_supplied_copy_fair_housing_flagged_not_rewritten(tmp_path):
    hub = make_hub(str(tmp_path))
    spoke = Spoke04ListingDescription(hub)
    hub.on_turn_start()
    hub.send(listing_data("d-007", {
        "beds": 2, "human_supplied_copy": "great schools nearby, family-friendly area",
    }))
    assert hub.queues["escalation.legal_line"]
    # still drafted and sent to compliance - not silently dropped either
    assert "d-007" in spoke.drafts
    assert persisted(hub, "content.review")


def test_approved_verdict_releases_to_12_and_05(tmp_path):
    hub = make_hub(str(tmp_path))
    spoke = Spoke04ListingDescription(hub)
    hub.on_turn_start()
    hub.send(listing_data("d-008", {"beds": 3}))
    hub.send(verdict("d-008", {"verdict": "approved"}))

    releases = persisted(hub, "asset.release")
    assert {r["to_agent"] for r in releases} == {"12", "05"}


def test_flagged_verdict_removes_exactly_cited_phrase_then_resubmits(tmp_path):
    hub = make_hub(str(tmp_path))
    spoke = Spoke04ListingDescription(hub)
    hub.on_turn_start()
    hub.send(listing_data("d-009", {"beds": 3, "features": ["pool", "spa"]}))
    hub.send(verdict("d-009", {"verdict": "flagged",
                              "findings": [{"phrase": "spa", "rule": "steering-adjacent"}]}))

    draft = spoke.drafts["d-009"]
    texts = [f["text"] for f in draft["facts"]]
    assert "spa" not in texts
    assert "pool" in texts
    reviews = persisted(hub, "content.review")
    assert len(reviews) == 2  # original + resubmit


def test_unrecognized_verdict_type_holds_rather_than_guesses(tmp_path):
    hub = make_hub(str(tmp_path))
    spoke = Spoke04ListingDescription(hub)
    hub.on_turn_start()
    hub.send(listing_data("d-011", {"beds": 3}))
    hub.send(verdict("d-011", {"verdict": "something_else"}))
    clar = persisted(hub, "clarification.request")
    assert any("unrecognized verdict" in c["payload"]["reason"] for c in clar)


def test_over_limit_cuts_adjectives_before_facts_never_cuts_attribution(tmp_path):
    hub = make_hub(str(tmp_path))
    spoke = Spoke04ListingDescription(hub, mls_char_limit=60)
    hub.on_turn_start()
    hub.send(listing_data("d-010", {
        "beds": 3, "baths": 2,
        "sqft_tax_record": 1800,
        "features": ["gorgeous", "stunning", "breathtaking views", "chef's kitchen"],
    }))
    draft = spoke.drafts["d-010"]
    texts = [f["text"] for f in draft["facts"]]
    assert "1800 sq ft" in texts, "attributed fact must survive cuts"
    assert draft["cut_for_length"] is True
