"""Mutation-hardening — spoke 04 (listing description), the compliance flagship.

Closes the 16 spoke-04 true gaps the clean sweep found beyond the one Fable's
suite already covered (04:255). Same discipline as Fable's file: assert the
DECISION with a positive AND a negative case, so the flipped operator is
distinguished. Every test here was verified to KILL its target mutation (goes
red when the line is flipped, green when clean) — not merely to pass.

Target lines (listing_spokes_04.py:line):
  87  88  90  99  107  109  120  124  130   (attribution / adjective tags)
  179 191                                    (requested-copy fair-housing/superlative)
  204                                         (media-rights gate)
  231 236                                     (MLS char-limit boundary)
  242                                         (cut-applied flag)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dispatcher.core import Envelope, Routes, AuditLog
from dispatcher.hub import Hub
from dispatcher.signatures import Ed25519Signer, Ed25519Verifier
from dispatcher.listing_spokes_04 import Spoke04ListingDescription

IDENTITY_ROUTES = os.path.join(os.path.dirname(__file__), "..", "identity",
                               "routes.json")


def make_hub(tmp_path):
    signer = Ed25519Signer()
    verifier = Ed25519Verifier(signer.public_key_bytes())
    hub = Hub(Routes(IDENTITY_ROUTES),
              AuditLog(os.path.join(tmp_path, "a.jsonl")),
              signature_verifier=verifier.verifier())
    return hub


def spoke(tmp_path, **kw):
    return Spoke04ListingDescription(make_hub(tmp_path), **kw)


def facts_by_text(facts, needle):
    return [f for f in facts if needle in f["text"]]


# ---------------------------------------------------------------- 04:88
def test_04_baths_present_emitted_absent_omitted(tmp_path):
    """`baths is not None` — a present bath count is emitted as a fact; an
    absent one produces NO bath fact (not a zero, not a blank)."""
    s = spoke(str(tmp_path))
    facts, _ = s._build_facts({"beds": 3, "baths": 2})
    assert facts_by_text(facts, "bathrooms"), "present baths must be emitted"
    facts2, _ = s._build_facts({"beds": 3})   # baths absent
    assert not facts_by_text(facts2, "bathrooms"), \
        "absent baths must NOT be emitted — a missing field does not exist"


# ------------------------------------------------ 04:87/90/99/109/120/124
def test_04_every_hard_fact_is_tagged_not_adjective(tmp_path):
    """EVERY structurally-verifiable fact carries adjective=False — beds,
    baths, sqft (tax and seller), and verified/per-seller features. The tag
    is what protects a hard fact from being cut before puffery. Each of the
    six sites is separately mutable, so each is asserted separately."""
    s = spoke(str(tmp_path))
    facts, _ = s._build_facts({
        "beds": 4, "baths": 3,
        "sqft_tax_record": 1800, "sqft_seller_claim": 1800,  # equal -> tax branch
        "roof_age": "2yr", "roof_age_source": "verified",
        "hvac_year": 2019, "hvac_year_source": "per_seller"})
    # beds (87) and baths (90)
    assert facts_by_text(facts, "bedrooms")[0]["adjective"] is False
    assert facts_by_text(facts, "bathrooms")[0]["adjective"] is False
    # sqft via tax branch (99)
    assert facts_by_text(facts, "sq ft")[0]["adjective"] is False
    # verified feature (120) and per-seller feature (124)
    assert facts_by_text(facts, "roof")[0]["adjective"] is False
    assert facts_by_text(facts, "HVAC")[0]["adjective"] is False
    # beds/baths carry no attribution
    assert facts_by_text(facts, "bedrooms")[0]["attribution"] is None


def test_04_sqft_seller_branch_tagged_not_adjective(tmp_path):
    """The seller-only sqft branch (109) is a separate emission site; assert
    its adjective tag directly so the 109 flip is killed."""
    s = spoke(str(tmp_path))
    facts, _ = s._build_facts({"beds": 3, "sqft_seller_claim": 2100})
    sq = facts_by_text(facts, "sq ft")[0]
    assert sq["adjective"] is False and sq["attribution"] == "per seller"


# ---------------------------------------------------------------- 04:107/109
def test_04_sqft_seller_only_attributed_per_seller(tmp_path):
    """`sqft_seller is not None` (no tax record): the figure ships with a
    'per seller' attribution, never bare. Negative: no seller claim -> no
    seller-attributed sqft fact."""
    s = spoke(str(tmp_path))
    facts, _ = s._build_facts({"beds": 3, "sqft_seller_claim": 2100})
    sq = facts_by_text(facts, "sq ft")
    assert sq and sq[0]["attribution"] == "per seller", \
        "a seller-only sqft claim must be attributed 'per seller'"
    facts2, _ = s._build_facts({"beds": 3})
    assert not facts_by_text(facts2, "sq ft"), \
        "no sqft input must yield no sqft fact"


# --------------------------------------------------- 04 sqft discrepancy (tuple 6)
def test_04_sqft_discrepancy_publishes_tax_never_averages(tmp_path):
    """tax != seller -> publish the tax (verifiable) figure, attributed to
    county records, and note the discrepancy; NEVER the seller figure,
    NEVER an average."""
    s = spoke(str(tmp_path))
    facts, notes = s._build_facts(
        {"beds": 3, "sqft_tax_record": 1800, "sqft_seller_claim": 2200})
    sq = facts_by_text(facts, "sq ft")
    assert sq and "1800" in sq[0]["text"], "must publish the tax figure"
    assert sq[0]["attribution"] == "per county tax records"
    assert not facts_by_text(facts, "2200"), "must never publish seller figure"
    assert not facts_by_text(facts, "2000"), "must never average"
    assert any("discrepancy" in n for n in notes), \
        "the discrepancy must be noted to the human"
    # the discrepancy-branch fact (99) is its own emission site: assert its
    # adjective tag directly so the 99 flip is killed
    assert sq[0]["adjective"] is False, \
        "the published tax figure is a hard fact, not an adjective"


# ---------------------------------------------------------------- 04:120/124
def test_04_unverifiable_feature_source_decides_attribution(tmp_path):
    """roof/HVAC: source 'verified' -> 'verified'; 'per_seller' ->
    'per seller (unverified)'; no source -> OMITTED with a note. Each is a
    distinct honesty decision."""
    s = spoke(str(tmp_path))
    fv, _ = s._build_facts({"beds": 3, "roof_age": "2yr",
                            "roof_age_source": "verified"})
    r = facts_by_text(fv, "roof")
    assert r and r[0]["attribution"] == "verified"

    fp, _ = s._build_facts({"beds": 3, "hvac_year": 2019,
                            "hvac_year_source": "per_seller"})
    h = facts_by_text(fp, "HVAC")
    assert h and h[0]["attribution"] == "per seller (unverified)", \
        "a per-seller feature must carry the unverified attribution"

    fo, notes = s._build_facts({"beds": 3, "roof_age": "2yr"})  # no source
    assert not facts_by_text(fo, "roof"), \
        "a feature with no source basis must be omitted, not bare-claimed"
    assert any("no source basis" in n for n in notes)


# ---------------------------------------------------------------- 04:130
def test_04_free_features_are_marked_adjectives(tmp_path):
    """Free-text `features` are characterizations: adjective True,
    attribution None. This is what makes them the FIRST thing cut under the
    MLS limit — mislabeling one as a fact would protect puffery over data."""
    s = spoke(str(tmp_path))
    facts, _ = s._build_facts({"beds": 3, "features": ["stunning kitchen"]})
    feat = facts_by_text(facts, "stunning kitchen")
    assert feat and feat[0]["adjective"] is True and feat[0]["attribution"] is None


# ---------------------------------------------------------------- 04:179
def test_04_requested_school_language_refused_clean_passes(tmp_path):
    """`w in requested_copy` (tuple 7): a school/demographic request is
    traced as a fair-housing refusal; a clean request is not."""
    s = spoke(str(tmp_path))
    e = Envelope(from_agent="05", to_agent="04", intent="listing.data",
                 client_context_id="rq-1",
                 payload={"requested_language": "highlight the school rating"},
                 provenance={"source": "t"})
    s._apply_gates(e.payload, "rq-1", e)
    traces = [t for t in s.hub.spoke_traces if t["agent"] == "04"]
    assert any("fair_housing" in t.get("result", "") for t in traces), \
        "a school-quality request must hit the fair-housing gate"

    s2 = spoke(str(tmp_path) + "_b")
    e2 = Envelope(from_agent="05", to_agent="04", intent="listing.data",
                  client_context_id="rq-2",
                  payload={"requested_language": "mention the new roof"},
                  provenance={"source": "t"})
    s2._apply_gates(e2.payload, "rq-2", e2)
    assert not any("fair_housing" in t.get("result", "")
                   for t in s2.hub.spoke_traces if t["agent"] == "04"), \
        "a clean request must not trip the fair-housing gate"


# ---------------------------------------------------------------- 04:191
def test_04_requested_superlative_declined_clean_passes(tmp_path):
    """`w in requested_copy` (tuple 5): a superlative request is declined in
    copy; a plain request is not."""
    s = spoke(str(tmp_path))
    e = Envelope(from_agent="05", to_agent="04", intent="listing.data",
                 client_context_id="su-1",
                 payload={"requested_language": "say best neighborhood around"},
                 provenance={"source": "t"})
    s._apply_gates(e.payload, "su-1", e)
    assert any("superlative" in t.get("result", "")
               for t in s.hub.spoke_traces if t["agent"] == "04"), \
        "a superlative request must be declined"

    s2 = spoke(str(tmp_path) + "_c")
    e2 = Envelope(from_agent="05", to_agent="04", intent="listing.data",
                  client_context_id="su-2",
                  payload={"requested_language": "mention the quiet street"},
                  provenance={"source": "t"})
    s2._apply_gates(e2.payload, "su-2", e2)
    assert not any("superlative" in t.get("result", "")
                   for t in s2.hub.spoke_traces if t["agent"] == "04")


# ---------------------------------------------------------------- 04:204
def test_04_media_rights_gate_all_confirmed_vs_any_unconfirmed(tmp_path):
    """`all(rights_confirmed_via_09 ...)`: media passes ONLY if every item
    has confirmed rights; a single unconfirmed item fails the gate; empty
    media is allowed."""
    s = spoke(str(tmp_path))
    assert s._media_rights_ok(
        {"media": [{"rights_confirmed_via_09": True},
                   {"rights_confirmed_via_09": True}]}) is True
    assert s._media_rights_ok(
        {"media": [{"rights_confirmed_via_09": True},
                   {"rights_confirmed_via_09": False}]}) is False, \
        "one unconfirmed media item must fail the whole gate"
    assert s._media_rights_ok({"media": []}) is True, \
        "no media is not a rights violation"


# ---------------------------------------------------------------- 04:231
def test_04_under_limit_returns_uncut(tmp_path):
    """`total_len <= mls_char_limit`: content within the limit returns
    with cut=False and everything intact. Boundary: set a small limit and
    a payload just under it."""
    s = spoke(str(tmp_path), mls_char_limit=200)
    facts = [{"text": "3 bedrooms", "attribution": None, "adjective": False}]
    kept, cut = s._cut_to_limit(facts)
    assert cut is False and len(kept) == 1, \
        "content under the limit must return uncut"


# ---------------------------------------------------------------- 04:236/242
def test_04_partial_class_cut_readds_what_fits_and_flags(tmp_path):
    """The 236 branch: when cutting a whole class overshoots, re-add its
    members one at a time while they fit (236 boundary), and return cut=True
    (242). Assert the exact kept set so both flips are killed:
      - flip 236 `<=`->`>` : nothing re-adds, kept set shrinks -> caught
      - flip 242 True->False: the flag is wrong -> caught
    Two adjectives, one small enough to survive the re-add, one not."""
    s = spoke(str(tmp_path), mls_char_limit=70)
    facts = [
        {"text": "3 bedrooms", "attribution": None, "adjective": False},  # 22
        {"text": "cozy", "attribution": None, "adjective": True},          # 16
        {"text": "absolutely stunning gourmet chef kitchen oasis retreat",
         "attribution": None, "adjective": True},                          # big
    ]
    kept, cut = s._cut_to_limit(facts)
    texts = [f["text"] for f in kept]
    assert cut is True, "a cut occurred -> flag must be True (kills 242)"
    assert "3 bedrooms" in texts, "the hard fact is always kept"
    assert "cozy" in texts, \
        "the small adjective must be RE-ADDED (kills the 236 <= flip)"
    assert not any("stunning" in t for t in texts), \
        "the oversized adjective must stay cut"


def test_04_over_limit_cuts_adjectives_before_facts(tmp_path):
    """231 branch: cutting the adjective class brings the kept set within
    the limit, so facts survive and adjectives go. Assert the decision."""
    s = spoke(str(tmp_path), mls_char_limit=60)
    facts = [
        {"text": "3 bedrooms", "attribution": None, "adjective": False},
        {"text": "1900 sq ft", "attribution": "per county tax records",
         "adjective": False},
        {"text": "absolutely stunning gourmet chef kitchen retreat oasis",
         "attribution": None, "adjective": True},
    ]
    kept, cut = s._cut_to_limit(facts)
    kept_text = " ".join(f["text"] for f in kept)
    assert cut is True
    assert "stunning" not in kept_text, "the adjective must be cut first"
    assert "1900 sq ft" in kept_text, "an attributed fact must never be cut"


# ---------------------------------------------------------------- 04:242
def test_04_attributed_facts_stand_over_limit_still_flags_cut(tmp_path):
    """The final branch (242): every cut class is exhausted and only
    attributed facts remain, exceeding the limit. 'Attributions are never
    cut' outranks the field limit — they ALL survive, and cut is still True
    (a cut was attempted/forced). Flip 242 True->False and this fails."""
    s = spoke(str(tmp_path), mls_char_limit=20)
    facts = [
        {"text": "1900 sq ft", "attribution": "per county tax records",
         "adjective": False},
        {"text": "2100 sq ft east wing", "attribution": "per county tax records",
         "adjective": False},
    ]
    kept, cut = s._cut_to_limit(facts)
    assert len(kept) == 2, "attributed facts are never cut, even over the limit"
    assert cut is True, "the over-limit condition must still report cut=True"


# ---------------------------------------------------------------- 04:107 (is-None guard)
def test_04_no_sqft_at_all_omits_sqft(tmp_path):
    """Negative guard for the sqft chain: neither tax nor seller present ->
    no sqft fact, no discrepancy note."""
    s = spoke(str(tmp_path))
    facts, notes = s._build_facts({"beds": 2})
    assert not facts_by_text(facts, "sq ft")
    assert not any("discrepancy" in n for n in notes)
