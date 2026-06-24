"""
Parsing-method catalog — the dispatch over each country's "setting".

A country's setting is the tuple ``(source_type, access, aixm_version)`` recorded
in ``data/aip_sources_seed.json`` by the AIPhunter agent. Onboarding a country is
then "look up its method and run it" rather than bespoke code each time.

Two orthogonal axes (see CLAUDE.md and the project plan):

  * **Acquisition** — keyed by ``access`` — *how to download the files*
      open          -> urllib_direct   (direct download)
      free-account  -> authenticated   (EAD/login token from env)
      paid/agreement/restricted -> sentinel_manual (operator-supplied file)

  * **Parser** — keyed by ``source_type`` (with ``aixm_version`` as an override
    that pre-empts it, because coded AIXM beats chart extraction) —
    *how to parse the format*
      ARINC424   -> arinc424        (coded; miner/extractor/arinc424.py)
      AIXM 5.1/4.5 -> aixm_upconvert (miner/extractor/aixm_upconverter.py)
      AIXM 5.2     -> aixm_native    (same module, no up-conversion)
      eAIP-HTML  -> eaip_html        (miner/extractor/eaip_html.py)
      PDF-text   -> pdf_text         (render -> llm_router)        [deferred]
      PDF-scan   -> pdf_scan_ocr     (llm_router vision)           [deferred]
      mixed      -> mixed            (ordered AIXM->HTML->PDF)      [deferred]
      paper/restricted/unknown -> sentinel (no automated parser)

``resolve_methods`` is the deterministic source of truth; the per-country
``parser_method``/``acquisition_method`` keys in the seed are a materialized
cache of it (see scripts/backfill_parser_methods.py). AIPhunter may set a
``parser_method_override`` when it has high-confidence evidence the mapping is
wrong for a specific country; ``effective_parser`` honours that escape hatch.
"""
from __future__ import annotations

from typing import Optional

from miner.extractor.base import ExtractedProcedure

# ── method-name vocabularies ─────────────────────────────────────────────────
ACQUISITION_METHODS = {"urllib_direct", "authenticated", "sentinel_manual"}
PARSER_METHODS = {
    "arinc424", "aixm_upconvert", "aixm_native", "eaip_html",
    "pdf_text", "pdf_scan_ocr", "mixed", "sentinel",
}

_AIXM_CODED = {"5.1", "4.5", "5.2"}  # an AIXM export exists -> prefer it

_PARSER_BY_SOURCE = {
    "ARINC424": "arinc424",
    "AIXM": "aixm_upconvert",
    "eAIP-HTML": "eaip_html",
    "PDF-text": "pdf_text",
    "PDF-scan": "pdf_scan_ocr",
    "mixed": "mixed",
    "paper": "sentinel",
    "restricted": "sentinel",
    "unknown": "sentinel",
}

_ACQUISITION_BY_ACCESS = {
    "open": "urllib_direct",
    "free-account": "authenticated",
    "paid": "sentinel_manual",
    "agreement": "sentinel_manual",
    "restricted": "sentinel_manual",
    "unknown": "sentinel_manual",
}

# Parser tiers implemented in this increment; others are catalogued but deferred.
_IMPLEMENTED_PARSERS = {"arinc424", "aixm_upconvert", "aixm_native", "eaip_html", "sentinel"}


def resolve_methods(source_type: str, access: str,
                    aixm_version: str = "nil") -> tuple[str, str]:
    """Return ``(acquisition_method, parser_method)`` for a country's setting.

    Deterministic precedence: a coded AIXM export (``aixm_version`` 5.1/4.5/5.2)
    pre-empts ``source_type``; otherwise ``source_type`` selects the parser.
    Unknown values degrade safely to the sentinel/manual path.
    """
    av = (aixm_version or "nil").strip()
    if source_type == "ARINC424":
        # Explicit open coded ARINC 424 (US CIFP, NAV CANADA) is the primary,
        # proven path even when an AIXM export also exists — keep it.
        parser = "arinc424"
    elif av in _AIXM_CODED:
        # Coded AIXM export available -> prefer it over chart extraction.
        parser = "aixm_native" if av == "5.2" else "aixm_upconvert"
    else:
        parser = _PARSER_BY_SOURCE.get(source_type, "sentinel")
    acquisition = _ACQUISITION_BY_ACCESS.get(access, "sentinel_manual")
    return acquisition, parser


def effective_parser(entry: dict) -> str:
    """Resolve a seed entry's parser, honouring a human ``parser_method_override``.

    The deterministic mapping is authoritative; the override is the documented
    escape hatch for countries where AIPhunter has high-confidence evidence.
    """
    override = entry.get("parser_method_override")
    if override:
        return override
    return resolve_methods(
        entry.get("source_type", "unknown"),
        entry.get("access", "unknown"),
        entry.get("aixm_version", "nil"),
    )[1]


def is_implemented(parser_method: str) -> bool:
    """True if a parser tier is built (vs. catalogued-but-deferred)."""
    return parser_method in _IMPLEMENTED_PARSERS


# ── sentinel extractor (paper / restricted / unresearched) ───────────────────
class SentinelExtractor:
    """No automated parser: yields an empty, flagged structure for an airport.

    The operator-run sentinel miner fills these manually (spec §2.5); we never
    fabricate procedure content for a country we cannot parse.
    """

    def __init__(self, airport_icao: str = ""):
        self.airport_icao = airport_icao

    def extract(self, source_ref: Optional[str] = None) -> ExtractedProcedure:
        return ExtractedProcedure(
            record_type="SID",
            airport_icao=self.airport_icao,
            procedure_name="",
            transitions=[],
            common_route=[],
        )


def build_extractor(setting: tuple[str, str, str], country_cfg: Optional[dict] = None):
    """Return an object with ``.extract(source_ref) -> ExtractedProcedure`` for the
    structure-only parser tiers (eaip_html, aixm, sentinel).

    Coded ARINC 424 and the coordinate-bearing AIXM up-converter additionally feed
    the waypoint index; their PoC drivers (scripts/poc_*_eaip.py) call those
    modules directly, as the FAA PoC calls faa_dtpp. Deferred tiers raise a clear
    error so callers never silently get a wrong parser.
    """
    source_type, access, aixm_version = setting
    _, parser = resolve_methods(source_type, access, aixm_version)
    cfg = country_cfg or {}

    if parser == "sentinel":
        return SentinelExtractor(cfg.get("airport_icao", ""))
    if parser == "eaip_html":
        from miner.extractor.eaip_html import EaipHtmlExtractor
        return EaipHtmlExtractor()
    if parser in ("aixm_upconvert", "aixm_native"):
        from miner.extractor.aixm_upconverter import AixmExtractor
        return AixmExtractor()
    if parser == "arinc424":
        raise NotImplementedError(
            "arinc424 is a coded tier: use miner.extractor.arinc424.parse_sid "
            "with a waypoint index (see scripts/poc_extraction.py)."
        )
    raise NotImplementedError(
        f"parser_method {parser!r} is catalogued but not yet implemented "
        "(deferred tier: pdf_text / pdf_scan_ocr / mixed)."
    )
