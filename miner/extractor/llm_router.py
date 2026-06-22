"""
LLM router — LLM-agnostic structure extraction (spec §2.1).

Miners set ``MINER_LLM`` to any LiteLLM-supported model (default
``ollama/qwen2.5-vl``). This module:

  1. If a usable LLM is configured AND litellm imports AND the model responds,
     send the chart image(s) + a strict structure-only prompt and parse JSON.
  2. Otherwise fall back to a committed extraction fixture so the PoC runs
     deterministically (no GPU/cloud) — exactly the Phase 0 design choice.

The contract is enforced: the prompt forbids coordinates, and any coordinate-ish
keys the model returns are stripped before the result leaves this module.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from miner.extractor.base import ExtractedProcedure

EXTRACTION_PROMPT = """\
You are an aeronautical chart reader. From the attached instrument procedure
chart, extract ONLY the procedure STRUCTURE as JSON. You MUST NOT output any
latitude, longitude, or coordinate of any kind — coordinates come from an
authoritative database, not from you.

Return JSON with this shape:
{
  "record_type": "SID|STAR|IAP",
  "airport_icao": "KLAX",
  "procedure_name": "LOOP6",
  "pbn_nav_spec": "D1",
  "transitions": [
    {"kind": "runway|enroute", "designator": "25R", "transition_id": "...",
     "legs": [ ...legs... ]}
  ],
  "common_route": [
    {"sequence_number": 10, "path_terminator": "TF", "waypoint_id": "DOTSS",
     "waypoint_flag": "FLY_BY", "alt_constraint": {"type": "AT_OR_ABOVE", "lower_ft": 600},
     "speed_constraint": {"type": "AT_OR_BELOW", "value_kt": 250},
     "course_magnetic": 251.0, "distance_nm": 4.2, "turn_direction": "L"}
  ]
}
Use ARINC 424 two-letter path terminators (IF, TF, CF, DF, RF, VA, CA, ...).
"""

# Keys we forcibly strip — coordinates must never originate from the LLM.
_FORBIDDEN_KEYS = {"lat", "lon", "latitude", "longitude", "coordinates", "coord"}


def _strip_coordinates(obj):
    """Recursively remove any coordinate-like keys from the LLM output."""
    if isinstance(obj, dict):
        return {
            k: _strip_coordinates(v)
            for k, v in obj.items()
            if k.lower() not in _FORBIDDEN_KEYS
        }
    if isinstance(obj, list):
        return [_strip_coordinates(v) for v in obj]
    return obj


def _try_llm(model: str, images: list[bytes], prompt: str) -> Optional[dict]:
    """Attempt a real LiteLLM call. Returns parsed JSON or None on any failure."""
    try:
        import base64

        import litellm  # type: ignore
    except Exception:
        return None
    try:
        content = [{"type": "text", "text": prompt}]
        for img in images:
            b64 = base64.b64encode(img).decode()
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                }
            )
        resp = litellm.completion(
            model=model,
            messages=[{"role": "user", "content": content}],
            response_format={"type": "json_object"},
            timeout=60,
        )
        text = resp["choices"][0]["message"]["content"]
        return json.loads(text)
    except Exception:
        return None


def extract_structure(
    images: Optional[list[bytes]] = None,
    *,
    fixture_path: Optional[str | os.PathLike] = None,
    model: Optional[str] = None,
) -> ExtractedProcedure:
    """
    Extract procedure structure. Tries the configured LLM first; falls back to
    ``fixture_path`` (or ``$POC_EXTRACTION_FIXTURE``). Always strips coordinates.
    """
    model = model or os.environ.get("MINER_LLM", "ollama/qwen2.5-vl")
    images = images or []

    result: Optional[dict] = None
    if images:
        result = _try_llm(model, images, EXTRACTION_PROMPT)

    if result is None:
        fixture = fixture_path or os.environ.get(
            "POC_EXTRACTION_FIXTURE",
            "data/fixtures/klax_dotss2_llm_extraction.json",
        )
        fixture = Path(fixture)
        if not fixture.exists():
            raise RuntimeError(
                f"No LLM result and extraction fixture not found at {fixture}. "
                "Set MINER_LLM to a reachable model or provide a fixture."
            )
        result = json.loads(fixture.read_text())

    return _strip_coordinates(result)  # type: ignore[return-value]
