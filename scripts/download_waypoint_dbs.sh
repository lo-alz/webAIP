#!/usr/bin/env bash
#
# download_waypoint_dbs.sh — best-effort fetch of authoritative waypoint/chart
# sources. Verified URLs are documented in docs/SOURCES.md. The script is
# NON-FATAL: if a download fails (offline CI, URL drift, registration gates) it
# warns and continues — the Phase 0 PoC runs on committed fixtures regardless.
#
# Usage: bash scripts/download_waypoint_dbs.sh [AIRAC_EFFECTIVE_YYMMDD]
set -uo pipefail

NASR_DIR="${NASR_DATA_DIR:-data/nasr}"
EAD_DIR="${EAD_DATA_DIR:-data/ead}"
NAVCAN_DIR="${NAVCAN_DATA_DIR:-data/navcanada}"
mkdir -p "$NASR_DIR" "$EAD_DIR" "$NAVCAN_DIR"

# AIRAC effective date drives the CIFP filename (NOT the cycle number).
EFF="${1:-}"

note() { printf '  %s\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }

fetch() {  # fetch <url> <dest>
  local url="$1" dest="$2"
  if curl -fsSL --max-time 120 -o "$dest" "$url" 2>/dev/null; then
    ok "$(basename "$dest")  <-  $url"
    return 0
  fi
  warn "could not fetch $url (continuing)"
  rm -f "$dest" 2>/dev/null
  return 1
}

echo "== United States — FAA NASR CIFP (ARINC 424-18) =="
if [[ -z "$EFF" ]]; then
  warn "no AIRAC effective date given; resolving the latest known CIFP file."
  # CIFP files are published on 28-day boundaries. Probe recent candidates and
  # keep the newest that exists. (See docs/SOURCES.md §1 for the URL pattern.)
  base="https://aeronav.faa.gov/Upload_313-d/cifp"
  for d in $(python3 - <<'PY'
import datetime as dt
# Print the last few 28-day AIRAC effective dates as YYMMDD candidates.
anchor = dt.date(2026, 1, 22)  # a known CIFP effective date
today = dt.date.today()
k = (today - anchor).days // 28
for i in range(k+1, k-4, -1):
    print((anchor + dt.timedelta(days=28*i)).strftime("%y%m%d"))
PY
  ); do
    if curl -fsI --max-time 20 "$base/CIFP_$d.zip" >/dev/null 2>&1; then
      EFF="$d"; note "latest available CIFP effective date: $EFF"; break
    fi
  done
fi
if [[ -n "$EFF" ]]; then
  fetch "https://aeronav.faa.gov/Upload_313-d/cifp/CIFP_${EFF}.zip" "$NASR_DIR/CIFP_${EFF}.zip" \
    && ( cd "$NASR_DIR" && unzip -oq "CIFP_${EFF}.zip" 2>/dev/null && ok "unzipped CIFP" || warn "unzip skipped" )
else
  warn "no CIFP effective date resolved — skipping (PoC uses fixtures)."
fi

echo
echo "== United States — FAA d-TPP chart index =="
fetch "https://nfdc.faa.gov/webContent/dtpp/current.xml" "$NASR_DIR/dtpp_current.xml" \
  && note "resolve specific chart PDFs from this index (see miner/extractor/faa_dtpp.py)"

echo
echo "== Europe — EUROCONTROL EAD (Phase 1, manual) =="
warn "EAD requires registration and provides AIXM 5.1 (not 5.2)."
note "Register: https://www.ead.eurocontrol.int/  → EAD Basic / MY EAD (AIMSL)."
note "Place exports in $EAD_DIR/ ; a 5.1→5.2 mapping step is required (docs/SOURCES.md §3)."

echo
echo "== Canada — NAV CANADA (Phase 1, manual) =="
warn "NAV CANADA publishes PDF AIP — no public ARINC 424 coordinate database."
note "https://www.navcanada.ca/en/aeronautical-information/aip-canada.aspx"
note "Canadian coordinates require chart extraction, not DB lookup (docs/SOURCES.md §4)."

echo
ok "Done. Now build the index:  python scripts/build_waypoint_index.py"
