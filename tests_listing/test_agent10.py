import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.listing_spokes_10 import Spoke10MarketData

IDENTITY_ROUTES = os.path.join(os.path.dirname(__file__), "..", "identity",
                               "routes.json")


def make_hub(tmp_path, **kw):
    audit_path = os.path.join(tmp_path, f"audit-{uuid.uuid4().hex[:8]}.jsonl")
    return Hub(Routes(IDENTITY_ROUTES), AuditLog(audit_path), **kw)


def data_req(ctx, payload, frm="03"):
    payload = {"license_scope": "internal", **payload}
    return Envelope(from_agent=frm, to_agent="10", intent="data.request",
                    client_context_id=ctx, payload=payload,
                    provenance={"source": f"spoke-{frm}", "captured_at": "runtime",
                                "verbatim_available": True})


def persisted(hub, intent=None):
    events = [e for e in hub.audit.read() if e["kind"] == "envelope.persisted"]
    return [e for e in events if intent is None or e["intent"] == intent]


def make_comp(addr, price, source="mls", date="2026-07-01"):
    return {"address": addr, "sold_price": price, "source": source,
           "retrieval_date": date}


def test_comp_package_drops_datums_without_provenance(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke10MarketData(hub, comp_minimum=1)
    hub.on_turn_start()
    comps = [make_comp("1 Main St", 400_000),
             {"address": "2 Main St", "sold_price": 410_000}]  # no source/date
    hub.send(data_req("m-001", {"mode": "comp", "comps": comps, "today": "2026-07-15"}))
    pkg = persisted(hub, "data.package")[0]["payload"]
    assert pkg["comp_count"] == 1
    assert pkg["dropped_no_provenance"] == 1


def test_thin_comp_set_reported_never_widened(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke10MarketData(hub, comp_minimum=5)
    hub.on_turn_start()
    comps = [make_comp("1 Main St", 400_000)]
    hub.send(data_req("m-002", {"mode": "comp", "comps": comps, "today": "2026-07-15"}))
    pkg = persisted(hub, "data.package")[0]["payload"]
    assert pkg["thin"] is True
    assert pkg["comp_count"] == 1  # never padded/widened


def test_stale_comp_dropped_never_reshipped(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke10MarketData(hub, staleness_days=30, comp_minimum=1)
    hub.on_turn_start()
    comps = [make_comp("1 Main St", 400_000, date="2026-05-01")]  # 75 days old
    hub.send(data_req("m-003", {"mode": "comp", "comps": comps, "today": "2026-07-15"}))
    pkg = persisted(hub, "data.package")[0]["payload"]
    assert pkg["comp_count"] == 0


def test_conflicting_sold_prices_reported_both_never_averaged(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke10MarketData(hub, comp_minimum=1)
    hub.on_turn_start()
    comps = [make_comp("1 Main St", 400_000, source="mls_a"),
            make_comp("1 Main St", 405_000, source="mls_b")]
    hub.send(data_req("m-004", {"mode": "comp", "comps": comps, "today": "2026-07-15"}))
    pkg = persisted(hub, "data.package")[0]["payload"]
    assert "1 Main St" in pkg["conflicts"]
    assert len(pkg["conflicts"]["1 Main St"]) == 2


def test_opinion_request_refused_escalates_on_second_press(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke10MarketData(hub)
    hub.on_turn_start()
    hub.send(data_req("m-005", {"mode": "comp", "message": "what would you price it at"}))
    assert not hub.queues["escalation.legal_line"]
    hub.send(data_req("m-005", {"mode": "comp", "message": "just give me your opinion"}))
    assert hub.queues["escalation.legal_line"]
    pkgs = persisted(hub, "data.package")
    assert all(p["payload"].get("refused") for p in pkgs)


def test_appraisal_substitution_smell_notes_and_informs_17(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke10MarketData(hub, comp_minimum=1)
    hub.on_turn_start()
    hub.send(data_req("m-006", {"mode": "comp",
                                "message": "is this worth more than list",
                                "comps": [make_comp("1 Main St", 400_000)],
                                "today": "2026-07-15"}))
    pkg = persisted(hub, "data.package")[0]["payload"]
    assert "not_an_appraisal_note" in pkg
    notice = persisted(hub, "compliance.notice")
    assert notice and notice[0]["to_agent"] == "17"


def test_external_license_scope_delivers_to_human_only(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke10MarketData(hub, comp_minimum=1)
    hub.on_turn_start()
    hub.send(data_req("m-007", {"mode": "comp", "license_scope": "external",
                                "comps": [], "today": "2026-07-15"}, frm="11"))
    pkgs = persisted(hub, "data.package")
    assert pkgs[0]["to_agent"] == "human"


def test_historic_data_beyond_retention_absent_never_reconstructed(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke10MarketData(hub, retention_days=730)
    hub.on_turn_start()
    hub.send(data_req("m-008", {"mode": "comp", "years_back": 5}))
    pkg = persisted(hub, "data.package")[0]["payload"]
    assert pkg["absent"] is True


def test_neighborhood_package_never_characterizes(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke10MarketData(hub)
    hub.on_turn_start()
    data = {"school_rating": {"value": 8, "source": "greatschools.org",
                              "retrieval_date": "2026-07-01",
                              "link": "https://example.com/schools"},
           "crime_index": {"value": "unsourced-editorial-claim"}}  # no provenance
    hub.send(data_req("m-009", {"mode": "neighborhood", "data": data}))
    pkg = persisted(hub, "data.package")[0]["payload"]
    assert "school_rating" in pkg["figures"]
    assert "crime_index" in pkg["dropped_no_provenance"]
    # structural: no key anywhere resembling a characterization/opinion field
    assert "characterization" not in pkg and "opinion" not in pkg and "recommendation" not in pkg


# ------------------------------ THE FIX: neighborhood staleness (tuple 4)
def test_stale_neighborhood_figure_dropped_never_reshipped(tmp_path):
    """Was: no staleness check existed for neighborhood figures at all -
    a 3-year-old crime stat would ship with no flag, ever."""
    hub = make_hub(str(tmp_path))
    Spoke10MarketData(hub, staleness_days=30)
    hub.on_turn_start()
    data = {"crime_index": {"value": 42, "source": "police-dept",
                            "retrieval_date": "2024-01-01"}}  # ancient
    hub.send(data_req("m-010", {"mode": "neighborhood", "data": data,
                                "today": "2026-07-01"}))
    pkg = persisted(hub, "data.package")[0]["payload"]
    assert "crime_index" not in pkg["figures"]
    assert "crime_index" in pkg["dropped_no_provenance"]


def test_fresh_neighborhood_figure_ships_normally(tmp_path):
    hub = make_hub(str(tmp_path))
    Spoke10MarketData(hub, staleness_days=30)
    hub.on_turn_start()
    data = {"crime_index": {"value": 42, "source": "police-dept",
                            "retrieval_date": "2026-06-15"}}
    hub.send(data_req("m-011", {"mode": "neighborhood", "data": data,
                                "today": "2026-07-01"}))
    pkg = persisted(hub, "data.package")[0]["payload"]
    assert pkg["figures"]["crime_index"]["value"] == 42


# ---------------------------- THE FIX: neighborhood conflicts (tuple 2)
def test_conflicting_neighborhood_sources_reported_never_averaged(tmp_path):
    """Was: the payload schema only allowed one source per figure -
    structurally unable to represent a conflict at all for neighborhood
    data, even though the ambiguity protocol names this exact scenario
    ('two crime stats for one area') as an anticipated case."""
    hub = make_hub(str(tmp_path))
    Spoke10MarketData(hub)
    hub.on_turn_start()
    data = {"crime_index": [
        {"value": 42, "source": "police-dept", "retrieval_date": "2026-07-01"},
        {"value": 58, "source": "neighborhoodscout.com", "retrieval_date": "2026-07-01"},
    ]}
    hub.send(data_req("m-012", {"mode": "neighborhood", "data": data}))
    pkg = persisted(hub, "data.package")[0]["payload"]
    assert "crime_index" not in pkg["figures"], "a conflict must not be averaged into figures"
    assert len(pkg["conflicts"]["crime_index"]) == 2
    sources = {c["source"] for c in pkg["conflicts"]["crime_index"]}
    assert sources == {"police-dept", "neighborhoodscout.com"}


# ---------------------- THE FIX: appraisal-substitution note (tuple 10)
def test_appraisal_substitution_smell_fires_for_neighborhood_mode_too(tmp_path):
    """Was: the smell check was computed once but only ever passed to the
    comp-mode builder - the same phrasing in a neighborhood-mode request
    never triggered the note or the 17 notification."""
    hub = make_hub(str(tmp_path))
    Spoke10MarketData(hub)
    hub.on_turn_start()
    data = {"school_rating": {"value": 8, "source": "greatschools.org",
                              "retrieval_date": "2026-07-01"}}
    hub.send(data_req("m-013", {"mode": "neighborhood", "data": data,
                                "message": "what's it actually worth "
                                          "instead of an appraisal"}))
    pkg = persisted(hub, "data.package")[0]["payload"]
    assert "not_an_appraisal_note" in pkg
    notices = persisted(hub, "compliance.notice")
    assert any(n["to_agent"] == "17" for n in notices)


def test_REGRESSION_missing_license_scope_fails_closed_to_human(tmp_path):
    """The actual bug: license_scope defaulted to 'internal' (permissive)
    when unspecified, so the gate was trivially skipped by omission. Must
    now default to routing to human when the field is simply absent."""
    hub = make_hub(str(tmp_path))
    Spoke10MarketData(hub, comp_minimum=1)
    hub.on_turn_start()
    env = Envelope(from_agent="03", to_agent="10", intent="data.request",
                  client_context_id="m-010",
                  payload={"mode": "comp", "comps": [], "today": "2026-07-15"},
                  provenance={"source": "spoke-03", "captured_at": "runtime",
                              "verbatim_available": True})
    hub.send(env)
    pkgs = persisted(hub, "data.package")
    assert pkgs[0]["to_agent"] == "human", \
        "omitted license_scope must fail closed to human, not default to internal"
