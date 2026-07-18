"""Agent 04 - Listing Description, built against the full spec.

Produces MLS descriptions/captions/flyer copy/tour scripts from property
data. Describes only what's in the data. Nothing publishes without a
Compliance (17) 'approved' verdict.
"""
from __future__ import annotations

from .core import Envelope

SOURCE_VERIFIED = "source_verified"
STATED_BY_PARTY = "stated_by_party"
UNKNOWN = "unknown"

_SUPERLATIVE_WORDS = ("best block", "best in town", "best neighborhood",
                     "safest", "most desirable", "top school")
_PROTECTED_CLASS_REQUEST_WORDS = ("school quality", "school rating",
                                 "neighborhood demographics", "family-friendly",
                                 "great schools", "school district rating")


def _env(frm, to, intent, ctx, payload, confidence=UNKNOWN, in_reply_to=None):
    return Envelope(from_agent=frm, to_agent=to, intent=intent,
                    client_context_id=ctx, payload=payload,
                    confidence=confidence, in_reply_to=in_reply_to,
                    provenance={"source": f"spoke-{frm}", "captured_at": "runtime",
                                "verbatim_available": True})


class Spoke04ListingDescription:
    """DECISIONS.md tuples implemented directly:
      1. photos contradict data sheet -> halt, clarification, both attached
      2. feature cannot be verified -> omit, never hedge with 'may include'
      3. human-supplied copy appears to violate fair housing -> flag to 17
         AND human, never silently rewrite
      4. space forces cuts -> adjectives before facts; attributions never cut
      5. superlatives requested -> decline in copy
      6. sq ft differs tax vs seller -> publish verifiable (tax) source,
         note discrepancy to human, never average
      7. seller requests school-quality/demographics language -> refuse,
         fair-housing gate, 17 verdict required regardless
      8. photo reveals protected-class info -> flag to human before any use
      9. feature unverifiable (roof age, HVAC year) -> include only as
         per-seller w/ attribution, or omit; no bare claims
      10. 17 requires changes -> apply exactly, no negotiation
      11. remarks exceed MLS field limits -> cut adjectives before facts,
          attributions never cut
    """

    # TUNABLE (owner-ratified 2026-07-16): mls_char_limit=800.
    # TUNABLE (owner-ratified 2026-07-17): cut priority now genuinely reads
    # config/description_cut_priority.json - tuple 11 always said "cut by
    # priority list from the identity config" and the config never existed;
    # the order was hardcoded. Fail closed: a present-but-unreadable config
    # raises rather than silently reverting to a hardcoded order.
    # See docs/TUNING_MANUAL.md to change.
    _DEFAULT_CUT_PRIORITY = ["adjectives", "unattributed_facts"]

    def __init__(self, hub, mls_char_limit: int = 800,
                 cut_priority_config: str | None = None):
        self.hub = hub
        self.drafts: dict[str, dict] = {}  # ctx -> draft asset pending verdict
        self.mls_char_limit = mls_char_limit
        import json, os
        path = cut_priority_config or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config", "description_cut_priority.json")
        if os.path.exists(path):
            self.cut_priority = json.load(open(path))["cut_priority"]
        else:
            # config absent (e.g. bare-core install): documented default,
            # identical to the ratified file's content
            self.cut_priority = list(self._DEFAULT_CUT_PRIORITY)
        hub.register("04", self.handle)

    def _build_facts(self, data: dict) -> tuple[list[dict], list[str]]:
        """Returns (facts, human_notes). Each fact: {text, attribution,
        adjective: bool}. Only fields present in `data` are ever emitted -
        a feature not in the data does not exist."""
        facts = []
        notes = []

        beds = data.get("beds")
        baths = data.get("baths")
        if beds is not None:
            facts.append({"text": f"{beds} bedrooms", "attribution": None,
                         "adjective": False})
        if baths is not None:
            facts.append({"text": f"{baths} bathrooms", "attribution": None,
                         "adjective": False})

        # tuple 6: sq ft discrepancy -> publish verifiable (tax) source,
        # note to human, never average
        sqft_tax = data.get("sqft_tax_record")
        sqft_seller = data.get("sqft_seller_claim")
        if sqft_tax is not None and sqft_seller is not None and sqft_tax != sqft_seller:
            facts.append({"text": f"{sqft_tax} sq ft",
                         "attribution": "per county tax records",
                         "adjective": False})
            notes.append(f"sq ft discrepancy: tax record={sqft_tax}, "
                        f"seller claim={sqft_seller} - published the "
                        f"verifiable source, never averaged")
        elif sqft_tax is not None:
            facts.append({"text": f"{sqft_tax} sq ft",
                         "attribution": "per county tax records",
                         "adjective": False})
        elif sqft_seller is not None:
            facts.append({"text": f"{sqft_seller} sq ft",
                         "attribution": "per seller", "adjective": False})

        # tuple 9: unverifiable feature (roof age, HVAC year) -> per-seller
        # attribution or omit entirely; no bare claims
        for field, label in (("roof_age", "roof"), ("hvac_year", "HVAC system")):
            val = data.get(field)
            source = data.get(f"{field}_source")  # "verified" / "per_seller" / None
            if val is None:
                continue
            if source == "verified":
                facts.append({"text": f"{label}: {val}",
                             "attribution": "verified", "adjective": False})
            elif source == "per_seller":
                facts.append({"text": f"{label}: {val}",
                             "attribution": "per seller (unverified)",
                             "adjective": False})
            else:
                notes.append(f"{label} value {val!r} has no source basis - "
                            f"omitted (tuple: no bare claims)")

        for feat in data.get("features", []):
            facts.append({"text": feat, "attribution": None, "adjective": True})

        return facts, notes

    def _apply_gates(self, data: dict, ctx: str, env: Envelope) -> str | None:
        """Returns an escalation reason if a hard gate fires, else None."""
        # tuple 1: photos contradict data sheet -> halt, clarification.
        # Owner decision #4 (2026-07-17, delegated): detection is now REAL,
        # not caller-dependent. Before: photo_features/data_features were
        # computed and merely ATTACHED to the clarification - the actual
        # comparison never ran, so the halt only fired if the caller
        # pre-computed photo_data_contradictions itself (decorative-check
        # class). Now: when photo detection ran at all (the field is
        # present), the symmetric difference IS the contradiction - a
        # feature the data claims that photos don't show, or a photo-
        # visible feature absent from the data sheet, both halt. An
        # explicit caller flag still halts too; neither path suppresses
        # the other.
        photo_features = set(data.get("photo_detected_features", []))
        data_features = set(data.get("features", []))
        contradictions = data.get("photo_data_contradictions")
        if not contradictions and "photo_detected_features" in data:
            claimed_not_shown = sorted(data_features - photo_features)
            shown_not_claimed = sorted(photo_features - data_features)
            if claimed_not_shown or shown_not_claimed:
                contradictions = {"claimed_in_data_not_in_photos": claimed_not_shown,
                                  "in_photos_not_in_data": shown_not_claimed}
        if contradictions:
            self.hub.send(_env("04", "queue", "clarification.request", ctx,
                               {"reason": "photos contradict data sheet",
                                "contradictions": contradictions,
                                "photo_features": list(photo_features),
                                "data_features": list(data_features)}))
            return "photo_data_contradiction"

        # tuple 8: photo reveals protected-class info -> flag to human first
        if data.get("photo_reveals_protected_class"):
            self.hub.escalate("escalation.legal_line",
                              {"client_context_id": ctx,
                               "trigger": "photo may reveal protected-class "
                                         "information - human review before "
                                         "any use",
                               "agent": "04"})
            return "photo_protected_class"

        # tuple 7: seller requests school/demographic language -> refuse,
        # fair-housing gate, but STILL goes to compliance regardless
        requested_copy = str(data.get("requested_language", "")).lower()
        for w in _PROTECTED_CLASS_REQUEST_WORDS:
            if w in requested_copy:
                self.hub.ingest_spoke_trace(
                    "04", env.envelope_id,
                    thought=f"requested language {w!r} - fair-housing gate, "
                            f"refusing this content regardless of 17's "
                            f"eventual verdict; drafted asset excludes it "
                            f"and still goes to compliance review",
                    result="refused: fair_housing_language")
                break

        # tuple 5: superlatives requested -> decline in copy
        for w in _SUPERLATIVE_WORDS:
            if w in requested_copy:
                self.hub.ingest_spoke_trace(
                    "04", env.envelope_id,
                    thought=f"superlative {w!r} requested - characterization "
                            f"is a steering vector, declining in copy",
                    result="declined: superlative")
                break

        return None

    def _media_rights_ok(self, data: dict) -> bool:
        # tuple: only use media with confirmed usage rights (via 09)
        media = data.get("media", [])
        return all(m.get("rights_confirmed_via_09") for m in media) if media else True

    def _cut_to_limit(self, facts: list[dict]) -> tuple[list[dict], bool]:
        """Tuple 4/11: cut by the priority list from the identity config
        (config/description_cut_priority.json), first entry cut first.
        Attributions are never cut - structurally: no cut class ever
        matches an attributed fact. Never truncate mid-claim - drop whole
        fact entries only."""
        def total_len(fs):
            return sum(len(f["text"]) + (len(f["attribution"] or "") + 12) for f in fs)

        def in_class(f, cls):
            if cls == "adjectives":
                return f["adjective"]
            if cls == "unattributed_facts":
                return not f["attribution"] and not f["adjective"]
            raise ValueError(f"unknown cut class {cls!r} in "
                             f"description_cut_priority.json - fail closed, "
                             f"never guessing what to cut")

        if total_len(facts) <= self.mls_char_limit:
            return facts, False

        remaining = list(facts)
        for cls in self.cut_priority:
            kept = [f for f in remaining if not in_class(f, cls)]
            droppable = [f for f in remaining if in_class(f, cls)]
            if total_len(kept) <= self.mls_char_limit:
                # partial cut within this class: keep as many of its
                # members as still fit, whole entries only
                result = list(kept)
                for f in droppable:
                    if total_len(result + [f]) <= self.mls_char_limit:
                        result.append(f)
                return result, True
            remaining = kept
        # every cut class exhausted; attributed facts stand even over the
        # limit - "attributions are never cut" outranks the field limit
        return remaining, True

    def handle(self, env: Envelope):
        ctx = env.client_context_id

        if env.intent == "listing.data":
            data = env.payload

            # human-supplied copy (not this agent's own draft) reviewed for
            # fair-housing violations before anything else - tuple 3
            human_copy = data.get("human_supplied_copy")
            if human_copy:
                flagged_words = [w for w in _PROTECTED_CLASS_REQUEST_WORDS
                                if w in human_copy.lower()]
                if flagged_words:
                    self.hub.ingest_spoke_trace(
                        "04", env.envelope_id,
                        thought=f"human-supplied copy appears to violate "
                                f"fair housing ({flagged_words}) - flagging "
                                f"to compliance AND human, never silently "
                                f"rewriting it myself",
                        result="flagged: human_copy_fair_housing")
                    self.hub.escalate("escalation.legal_line",
                                      {"client_context_id": ctx,
                                       "trigger": f"human-supplied copy flagged: "
                                                 f"{flagged_words}",
                                       "agent": "04"})
                    # still goes to compliance too, doesn't return early

            gate = self._apply_gates(data, ctx, env)
            if gate in ("photo_data_contradiction", "photo_protected_class"):
                return

            if not self._media_rights_ok(data):
                self.hub.ingest_spoke_trace(
                    "04", env.envelope_id,
                    thought="one or more media assets lack confirmed usage "
                            "rights via 09 - excluding unconfirmed media "
                            "from this draft",
                    result="media excluded: rights unconfirmed")

            facts, notes = self._build_facts(data)
            facts, was_cut = self._cut_to_limit(facts)

            draft = {"ctx": ctx, "facts": facts, "notes": notes,
                    "cut_for_length": was_cut}
            self.drafts[ctx] = draft

            # Tuple 6 second half, unclosed since the 2026-07-17 review
            # named it: "note the discrepancy to HUMAN" - notes used to
            # ride only inside the draft payload, reaching 17 and nobody
            # else. Now every note goes in the books (14 -> P16/P17
            # visibility), and a source-conflict note additionally lands
            # on the human queue - two authorities disagreeing about a
            # published number is a human's call to reconcile, not a
            # payload field's.
            if notes:
                self.hub.send(_env("04", "14", "interaction.log", ctx,
                                   {"kind": "drafting_notes",
                                    "notes": notes}))
                discrepancies = [n for n in notes if "discrepancy" in n]
                if discrepancies:
                    self.hub.send(_env("04", "queue", "clarification.request",
                                       ctx, {"reason": "source discrepancy "
                                                       "in published listing "
                                                       "data - human "
                                                       "reconciliation",
                                            "notes": discrepancies}))

            self.hub.ingest_spoke_trace(
                "04", env.envelope_id,
                thought=f"draft built from supplied data only - "
                        f"{len(facts)} facts, notes={notes}; submitting to "
                        f"compliance before any release",
                result="content.review issued")
            self.hub.send(_env("04", "17", "content.review", ctx,
                               {"draft": draft}, in_reply_to=env.envelope_id))
            self.hub.send(_env("04", "18", "agent.status", ctx,
                               {"waiting_on": "compliance_review",
                                "since": env.payload.get("today")}))
            return

        if env.intent == "content.verdict":
            verdict = env.payload.get("verdict")
            draft = self.drafts.get(ctx)
            if verdict == "approved":
                if draft is None:
                    # duplicate/stray approval - the draft already
                    # released and was cleared; re-releasing stale
                    # content on a replayed verdict is how an old asset
                    # resurrects itself. Logged, nothing sent.
                    self.hub.ingest_spoke_trace(
                        "04", env.envelope_id,
                        thought=f"approved verdict for ctx={ctx!r} with no "
                                f"stored draft (already released or never "
                                f"drafted) - releasing nothing",
                        result="ignored: no_stored_draft")
                    return
                self.drafts.pop(ctx, None)
                self.hub.send(_env("04", "18", "agent.status", ctx,
                                   {"waiting_on": "compliance_review",
                                    "resolved": True}))
                self.hub.ingest_spoke_trace(
                    "04", env.envelope_id,
                    thought="compliance approved - releasing to Marketing "
                            "(12) and MLS/Listing Mgmt (05)",
                    result="asset.release issued")
                self.hub.send(_env("04", "12", "asset.release", ctx,
                                   {"draft": draft}))
                self.hub.send(_env("04", "05", "asset.release", ctx,
                                   {"draft": draft}))
                self.hub.send(_env("04", "14", "interaction.log", ctx,
                                   {"kind": "asset_released"}))
                return

            if verdict == "flagged":
                # 17's real contract (verified against its own SKILL.md/
                # DECISIONS.md): 'approved' or 'flagged' with itemized
                # findings - never a rewrite, never prescribed fix
                # instructions. 04 applies the fix implied by each finding
                # itself - exactly, no negotiation (tuple 10) - by removing
                # the specific cited phrase, not by receiving a "remove
                # this" instruction from 17.
                if draft is None:
                    # A verdict with no stored draft (replay, duplicate,
                    # unknown ctx) has nothing to correct - resubmitting
                    # None as a draft would launder an empty asset
                    # through a clean approval. Hold it, named.
                    self.hub.send(_env("04", "queue", "clarification.request",
                                       ctx, {"reason": "content.verdict for "
                                                       "a context with no "
                                                       "stored draft - "
                                                       "nothing to correct, "
                                                       "nothing resubmitted"}))
                    return
                findings = env.payload.get("findings", [])
                # Fresh-eyes finding B (2026-07-18): 'phrase in text' with
                # a MISSING phrase was '"" in text' == True for every
                # fact - one phrase-less finding emptied the whole draft,
                # the empty draft resubmitted, 17 approved the nothing,
                # and an EMPTY listing description released to MLS and
                # marketing. A finding without a citable phrase cannot be
                # applied "exactly" (tuple 10's own word) - it holds for
                # a human instead of guessing at scope.
                uncitable = [f for f in findings if not f.get("phrase")]
                if uncitable:
                    self.hub.send(_env("04", "queue", "clarification.request",
                                       ctx, {"reason": "compliance finding "
                                                       "carries no citable "
                                                       "phrase - cannot "
                                                       "apply a fix "
                                                       "'exactly', holding "
                                                       "for human",
                                            "findings": uncitable}))
                    return
                removed = []
                remaining = []
                for f in draft["facts"]:
                    cited = any(finding["phrase"] in f["text"]
                               for finding in findings)
                    if cited:
                        removed.append(f["text"])
                    else:
                        remaining.append(f)
                draft["facts"] = remaining
                self.hub.ingest_spoke_trace(
                    "04", env.envelope_id,
                    thought=f"compliance flagged {len(findings)} finding(s) "
                            f"with cited phrase + rule: {findings}; removed "
                            f"exactly the cited content ({removed}), no "
                            f"negotiation, resubmitting",
                    result=f"removed={removed}")
                self.hub.send(_env("04", "17", "content.review", ctx,
                                   {"draft": draft}))
                return

            self.hub.ingest_spoke_trace(
                "04", env.envelope_id,
                thought=f"unrecognized verdict {verdict!r} - 17's contract "
                        f"is only 'approved' or 'flagged'; holding rather "
                        f"than guessing at an unknown verdict type",
                result="held: unknown verdict type")
            self.hub.send(_env("04", "queue", "clarification.request", ctx,
                               {"reason": f"unrecognized verdict type: {verdict!r}"}))
            return
