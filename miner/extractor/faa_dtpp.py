"""
FAA d-TPP extractor — render chart PDF to images, then delegate to the LLM
router for structure-only extraction.

Chart filenames are resolved from the official d-TPP metadata index
(``https://nfdc.faa.gov/webContent/dtpp/current.xml``) — never guessed — per
docs/SOURCES.md §2. If pymupdf or the network is unavailable, this returns no
images and the LLM router falls back to the committed fixture.
"""
from __future__ import annotations

import os
import urllib.request
from typing import Optional

from miner.extractor.base import ExtractedProcedure
from miner.extractor.llm_router import extract_structure

DTPP_INDEX_URL = "https://nfdc.faa.gov/webContent/dtpp/current.xml"
DTPP_PDF_BASE = "https://aeronav.faa.gov/d-tpp/{cycle}"


def resolve_chart_pdf(airport_icao: str, chart_name_contains: str,
                      cycle: str, timeout: int = 20) -> Optional[str]:
    """
    Resolve the real chart PDF URL from the d-TPP metadata index. Returns a URL
    or None (offline / not found). Matching is best-effort and case-insensitive.
    """
    try:
        import xml.etree.ElementTree as ET

        with urllib.request.urlopen(DTPP_INDEX_URL, timeout=timeout) as resp:
            tree = ET.parse(resp)
    except Exception:
        return None

    needle = chart_name_contains.upper()
    icao = airport_icao.upper()
    for apt in tree.iter("airport_name"):
        if apt.get("icao_ident", "").upper() != icao and apt.get("apt_ident", "").upper() != icao:
            continue
        for rec in apt.iter("record"):
            name = (rec.findtext("chart_name") or "").upper()
            pdf = (rec.findtext("pdf_name") or "").strip()
            if needle in name and pdf:
                return f"{DTPP_PDF_BASE.format(cycle=cycle)}/{pdf}"
    return None


def render_pdf_to_images(pdf_path_or_url: str, timeout: int = 30) -> list[bytes]:
    """Render a d-TPP PDF to PNG bytes per page. Empty list if pymupdf absent."""
    try:
        import fitz  # PyMuPDF
    except Exception:
        return []

    data: bytes
    if pdf_path_or_url.startswith(("http://", "https://")):
        try:
            with urllib.request.urlopen(pdf_path_or_url, timeout=timeout) as resp:
                data = resp.read()
        except Exception:
            return []
    else:
        if not os.path.exists(pdf_path_or_url):
            return []
        with open(pdf_path_or_url, "rb") as fh:
            data = fh.read()

    images: list[bytes] = []
    try:
        doc = fitz.open(stream=data, filetype="pdf")
        for page in doc:
            pix = page.get_pixmap(dpi=200)
            images.append(pix.tobytes("png"))
    except Exception:
        return []
    return images


def extract_procedure(airport: str, chart_name_contains: str,
                      cycle: Optional[str] = None,
                      fixture_path: Optional[str] = None) -> ExtractedProcedure:
    """
    End-to-end structure extraction for an FAA terminal procedure. Attempts the
    live d-TPP chart; falls back to a committed extraction fixture (Phase 0).
    """
    cycle = cycle or os.environ.get("AIRAC_CYCLE", "2606")
    images: list[bytes] = []
    pdf_url = resolve_chart_pdf(airport, chart_name_contains, cycle)
    if pdf_url:
        images = render_pdf_to_images(pdf_url)
    return extract_structure(images=images, fixture_path=fixture_path)


def extract_klax_dotss2(cycle: Optional[str] = None,
                        fixture_path: Optional[str] = None) -> ExtractedProcedure:
    """KLAX DOTSS2 RNAV SID — the Phase 0 PoC procedure."""
    return extract_procedure("KLAX", "DOTSS", cycle=cycle, fixture_path=fixture_path)
