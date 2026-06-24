"""
Parsing-method catalog dispatch (miner/extractor/registry.py) + seed cache.
"""
import json
from pathlib import Path

import pytest

from miner.extractor.registry import (
    PARSER_METHODS,
    build_extractor,
    effective_parser,
    resolve_methods,
)

SEED = Path("data/aip_sources_seed.json")

SOURCE_TYPES = ["ARINC424", "AIXM", "eAIP-HTML", "PDF-text", "PDF-scan",
                "mixed", "paper", "restricted", "unknown"]
ACCESS = ["open", "free-account", "paid", "agreement", "restricted", "unknown"]
AIXM = ["nil", "5.2", "5.1", "4.5"]


def test_resolve_total_function():
    """Every setting resolves to a known (acquisition, parser) pair."""
    for st in SOURCE_TYPES:
        for ac in ACCESS:
            for av in AIXM:
                acq, parser = resolve_methods(st, ac, av)
                assert parser in PARSER_METHODS
                assert acq in {"urllib_direct", "authenticated", "sentinel_manual"}


def test_aixm_override_preempts_source_type():
    """A coded AIXM export beats chart-based source_type (except ARINC424)."""
    assert resolve_methods("eAIP-HTML", "open", "5.1")[1] == "aixm_upconvert"
    assert resolve_methods("PDF-text", "open", "4.5")[1] == "aixm_upconvert"
    assert resolve_methods("mixed", "open", "5.2")[1] == "aixm_native"


def test_arinc424_wins_over_aixm():
    """Explicit open coded ARINC 424 (US CIFP) stays arinc424 even with AIXM."""
    assert resolve_methods("ARINC424", "open", "5.1")[1] == "arinc424"


def test_chart_tiers_and_sentinel():
    assert resolve_methods("eAIP-HTML", "open", "nil")[1] == "eaip_html"
    assert resolve_methods("PDF-text", "open", "nil")[1] == "pdf_text"
    assert resolve_methods("PDF-scan", "free-account", "nil")[1] == "pdf_scan_ocr"
    assert resolve_methods("paper", "restricted", "nil")[1] == "sentinel"
    assert resolve_methods("restricted", "restricted", "nil")[1] == "sentinel"
    assert resolve_methods("unknown", "unknown", "nil")[1] == "sentinel"


def test_acquisition_axis():
    assert resolve_methods("eAIP-HTML", "open", "nil")[0] == "urllib_direct"
    assert resolve_methods("eAIP-HTML", "free-account", "nil")[0] == "authenticated"
    assert resolve_methods("PDF-text", "paid", "nil")[0] == "sentinel_manual"
    assert resolve_methods("PDF-text", "restricted", "nil")[0] == "sentinel_manual"


def test_effective_parser_honours_override():
    entry = {"source_type": "PDF-text", "access": "open", "aixm_version": "nil",
             "parser_method_override": "eaip_html"}
    assert effective_parser(entry) == "eaip_html"
    del entry["parser_method_override"]
    assert effective_parser(entry) == "pdf_text"


def test_seed_cache_consistent_with_resolver():
    """Every seed entry's cached parser_method matches resolve_methods unless an
    override is present (guards the materialized cache against drift)."""
    seed = json.loads(SEED.read_text())["countries"]
    for iso, e in seed.items():
        if e.get("parser_method") is None:
            continue  # unscaffolded
        if e.get("parser_method_override"):
            continue
        acq, parser = resolve_methods(
            e.get("source_type", "unknown"), e.get("access", "unknown"),
            e.get("aixm_version", "nil"))
        assert e["parser_method"] == parser, f"{iso} parser drift"
        assert e["acquisition_method"] == acq, f"{iso} acquisition drift"


def test_build_extractor_dispatch():
    assert build_extractor(("eAIP-HTML", "open", "nil")).__class__.__name__ == "EaipHtmlExtractor"
    assert build_extractor(("eAIP-HTML", "open", "5.1")).__class__.__name__ == "AixmExtractor"
    assert build_extractor(("paper", "restricted", "nil")).__class__.__name__ == "SentinelExtractor"
    # deferred tiers are catalogued but error rather than silently mis-parsing
    with pytest.raises(NotImplementedError):
        build_extractor(("PDF-text", "open", "nil"))
