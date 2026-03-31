#!/usr/bin/env bash
# EOPF / GeoZarr GDAL Validation
# Validates GDAL 3.13+ GeoZarr/EOPF Zarr support using CLI tools only.
# Tracks tasks from EOPF-Explorer/coordination#235.
#
# Usage:
#   bash validate_gdal.sh
#   EOPF_DATASET_URL=https://host/file.zarr bash validate_gdal.sh
#   make validate        (Docker)
#   make validate-local  (local, requires GDAL CLI in PATH)

set -euo pipefail

# ANSI colours — disabled when stdout is not a terminal (e.g. log files, CI)
if [[ -t 1 ]]; then
    GREEN=$'\033[0;32m'; RED=$'\033[0;31m'; BOLD=$'\033[1m'; RESET=$'\033[0m'
else
    GREEN=''; RED=''; BOLD=''; RESET=''
fi

# --- Configuration — all overridable via environment variables

EOPF_DATASET_URL="${EOPF_DATASET_URL:-https://s3.explorer.eopf.copernicus.eu/esa-zarr-sentinel-explorer-fra/tests-output/sentinel-2-l2a/S2B_MSIL2A_20260228T114349_N0512_R123_T30VVK_20260228T155602.zarr}"

BAND_DEFAULT="${BAND_DEFAULT:-/measurements/reflectance/r10m/b02}"
BAND_RED="${BAND_RED:-/measurements/reflectance/r10m/b04}"
BAND_GREEN="${BAND_GREEN:-/measurements/reflectance/r10m/b03}"
BAND_BLUE="${BAND_BLUE:-/measurements/reflectance/r10m/b02}"
BAND_R20M="${BAND_R20M:-/measurements/reflectance/r20m/b05}"
BAND_R60M="${BAND_R60M:-/measurements/reflectance/r60m/b01}"

EXPECTED_CRS="${EXPECTED_CRS:-32630}"           # T30VVK = UTM zone 30N
EXPECTED_BLOCK_SIZE="${EXPECTED_BLOCK_SIZE:-244}"  # inner shard chunk size reported by gdalinfo Block=
SRCWIN_SIZE="${SRCWIN_SIZE:-244}"               # window for partial read test (inner shard chunk)
PARTIAL_READ_MAX_KB="${PARTIAL_READ_MAX_KB:-1024}"
MIN_OVERVIEW_COUNT="${MIN_OVERVIEW_COUNT:-3}"

OUTPUT_DIR="${OUTPUT_DIR:-output}"
REPORT_FILE="${OUTPUT_DIR}/report_bash.md"

# --- Results tracking

# Output vars written by each task function; reset by run_task before each call
_STATUS=""
_DETAILS=""
_NETWORK="—"
_ARTIFACT=""

TASK_NAMES=()
TASK_STATUS=()    # "PASS" or "FAIL"
TASK_DURATION=()  # e.g. "3.42s"
TASK_NETWORK=()   # e.g. "128 KB" or "—"
TASK_DETAILS=()
TASK_ARTIFACTS=() # file path or ""
PASS_COUNT=0
FAIL_COUNT=0

record_result() {
    local name="$1" status="$2" duration="$3" network="$4" details="$5" artifact="${6:-}"
    TASK_NAMES+=("$name")
    TASK_STATUS+=("$status")
    TASK_DURATION+=("${duration}s")
    TASK_NETWORK+=("$network")
    TASK_DETAILS+=("$details")
    TASK_ARTIFACTS+=("$artifact")
    local color label
    if [[ "$status" == "PASS" ]]; then
        (( PASS_COUNT++ )) || true
        color="$GREEN" label="PASS"
    else
        (( FAIL_COUNT++ )) || true
        color="$RED" label="FAIL"
    fi
    printf "  ${color}${label}${RESET}  %s (%ss)\n" "$name" "$duration"
}

run_task() {
    # Handles timing and record_result; tasks only need to set _STATUS/_DETAILS/_NETWORK/_ARTIFACT.
    local label="$1"; shift
    _STATUS="FAIL" _DETAILS="" _NETWORK="—" _ARTIFACT=""
    local t_start t_end
    t_start=$(now_ns)
    "$@" || true
    t_end=$(now_ns)
    record_result "$label" "$_STATUS" "$(duration_s "$t_start" "$t_end")" "$_NETWORK" "$_DETAILS" "$_ARTIFACT"
}

# --- Helpers

now_ns() {
    # Nanoseconds — GNU date on Linux/Docker; python3 fallback for macOS
    date +%s%N 2>/dev/null || python3 -c "import time; print(int(time.time()*1e9))"
}

duration_s() {
    local start_ns="$1" end_ns="$2"
    awk "BEGIN { printf \"%.2f\", ($end_ns - $start_ns) / 1000000000 }"
}

file_size_bytes() {
    # GNU stat (Linux/Docker) and BSD stat (macOS)
    stat -c%s "$1" 2>/dev/null || stat -f%z "$1" 2>/dev/null || echo 0
}

make_zarr_url() {
    # Build GDAL ZARR connection string: ZARR:"/vsicurl/https://...":/array/path
    # The embedded double-quotes are consumed by GDAL, not the shell.
    printf 'ZARR:"/vsicurl/%s":%s' "${EOPF_DATASET_URL}" "$1"
}

parse_network_bytes() {
    # Extract the top-level "downloaded_bytes" total from CPL_VSIL_SHOW_NETWORK_STATS output.
    # The JSON nests the same value at multiple levels; the first occurrence is the top-level total.
    local text="$1"
    local bytes
    bytes=$(printf '%s\n' "$text" \
        | grep -oE '"downloaded_bytes":[0-9]+' \
        | head -1 \
        | grep -oE '[0-9]+$' || true)
    printf '%d' "${bytes:-0}"
}

# --- Prerequisites

check_prerequisites() {
    local missing=()
    for tool in gdalinfo gdal_translate gdalwarp gdalbuildvrt; do
        command -v "$tool" &>/dev/null || missing+=("$tool")
    done
    if (( ${#missing[@]} > 0 )); then
        printf 'ERROR: Missing GDAL tools: %s\n' "${missing[*]}" >&2
        printf 'Run inside Docker: make validate\n' >&2
        exit 1
    fi
    local gdal_ver
    gdal_ver=$(gdalinfo --version 2>/dev/null | head -1)
    printf "${BOLD}GDAL:${RESET} %s\n" "$gdal_ver"
}

# --- Task 1: Metadata (gdalinfo)

task_metadata() {
    local url info_out
    url=$(make_zarr_url "${BAND_DEFAULT}")

    if info_out=$(gdalinfo "$url" 2>&1); then
        local crs_ok ovr_ok block_ok ovr_count

        # CRS: match AUTHORITY["EPSG","N"] or ID["EPSG",N]
        grep -qE 'EPSG[",]+'"${EXPECTED_CRS}" <<< "$info_out" \
            && crs_ok=true || crs_ok=false

        # Overview count: one "Overviews: NxN, NxN, ..." line per band
        ovr_count=$(grep 'Overviews:' <<< "$info_out" \
            | head -1 \
            | grep -oP '[0-9]+x[0-9]+' \
            | wc -l || true)
        (( ovr_count >= MIN_OVERVIEW_COUNT )) && ovr_ok=true || ovr_ok=false

        # Block size: gdalinfo prints "Block=NxN"
        grep -q "Block=${EXPECTED_BLOCK_SIZE}x${EXPECTED_BLOCK_SIZE}" <<< "$info_out" \
            && block_ok=true || block_ok=false

        if $crs_ok && $ovr_ok && $block_ok; then
            _STATUS="PASS"
            _DETAILS="CRS=EPSG:${EXPECTED_CRS} overviews=${ovr_count}>=${MIN_OVERVIEW_COUNT} block=${EXPECTED_BLOCK_SIZE}x${EXPECTED_BLOCK_SIZE}"
        else
            _STATUS="FAIL"
            _DETAILS="crs=${crs_ok} overviews=${ovr_ok}(${ovr_count}) block=${block_ok}"
        fi
    else
        _STATUS="FAIL"
        _DETAILS="gdalinfo failed: $(head -1 <<< "$info_out")"
    fi
}

# --- Task 2: Partial read / shard efficiency

task_partial_read() {
    local url out_file tmpstdout stdout_content net_bytes
    url=$(make_zarr_url "${BAND_DEFAULT}")
    out_file="${OUTPUT_DIR}/out_partial.tif"
    tmpstdout=$(mktemp)
    trap "rm -f '${tmpstdout}'" RETURN

    # CPL_VSIL_SHOW_NETWORK_STATS prints to stdout; capture it, suppress stderr noise
    if CPL_VSIL_SHOW_NETWORK_STATS=YES \
        gdal_translate "$url" "$out_file" \
        -srcwin 0 0 "${SRCWIN_SIZE}" "${SRCWIN_SIZE}" \
        -q >"$tmpstdout" 2>/dev/null; then

        stdout_content=$(cat "$tmpstdout")
        net_bytes=$(parse_network_bytes "$stdout_content")
        local max_bytes net_kb
        max_bytes=$(( PARTIAL_READ_MAX_KB * 1024 ))
        net_kb=$(( net_bytes / 1024 ))

        if (( net_bytes == 0 )); then
            _STATUS="PASS"
            _DETAILS="${SRCWIN_SIZE}x${SRCWIN_SIZE} window read OK (network stats=0, cache hit or not captured)"
        elif (( net_bytes < max_bytes )); then
            _STATUS="PASS"
            _DETAILS="${SRCWIN_SIZE}x${SRCWIN_SIZE} window read ${net_kb} KB (< ${PARTIAL_READ_MAX_KB} KB limit)"
            _NETWORK="${net_kb} KB"
        else
            _STATUS="FAIL"
            _DETAILS="${net_kb} KB downloaded exceeds ${PARTIAL_READ_MAX_KB} KB limit"
            _NETWORK="${net_kb} KB"
        fi
    else
        _STATUS="FAIL"
        _DETAILS="gdal_translate -srcwin failed"
    fi
    _ARTIFACT="$out_file"
}

# --- Task 3: Full band export to GeoTIFF

task_export() {
    local url out_file
    url=$(make_zarr_url "${BAND_DEFAULT}")
    out_file="${OUTPUT_DIR}/band.tif"

    local translate_out
    if translate_out=$(gdal_translate "$url" "$out_file" -q 2>&1); then
        if [[ -f "$out_file" ]]; then
            local info_out
            info_out=$(gdalinfo "$out_file" 2>&1)
            if grep -qE 'EPSG[",]+'"${EXPECTED_CRS}" <<< "$info_out"; then
                _STATUS="PASS"
                _DETAILS="Exported to $(basename "$out_file"), CRS=EPSG:${EXPECTED_CRS} verified"
            else
                _STATUS="FAIL"
                _DETAILS="Output exists but CRS=EPSG:${EXPECTED_CRS} not found in gdalinfo"
            fi
        else
            _STATUS="FAIL"
            _DETAILS="gdal_translate exited 0 but output file missing"
        fi
    else
        _STATUS="FAIL"
        _DETAILS="gdal_translate failed: $(head -1 <<< "$translate_out")"
    fi
    _ARTIFACT="$out_file"
}

# --- Task 4: Reprojection to EPSG:4326

task_reproject() {
    local url out_file
    url=$(make_zarr_url "${BAND_DEFAULT}")
    out_file="${OUTPUT_DIR}/b02_4326.tif"

    local warp_out
    if warp_out=$(gdalwarp -t_srs EPSG:4326 "$url" "$out_file" -q 2>&1); then
        if [[ -f "$out_file" ]]; then
            local info_out
            info_out=$(gdalinfo "$out_file" 2>&1)
            if grep -qE 'EPSG[",]+4326' <<< "$info_out"; then
                _STATUS="PASS"
                _DETAILS="Reprojected to EPSG:4326, output=$(basename "$out_file")"
            else
                _STATUS="FAIL"
                _DETAILS="Output exists but EPSG:4326 not found in gdalinfo"
            fi
        else
            _STATUS="FAIL"
            _DETAILS="gdalwarp exited 0 but output file missing"
        fi
    else
        _STATUS="FAIL"
        _DETAILS="gdalwarp failed: $(head -1 <<< "$warp_out")"
    fi
    _ARTIFACT="$out_file"
}

# --- Task 5: RGB composite

task_composite() {
    local url_r url_g url_b vrt_file png_file
    url_r=$(make_zarr_url "${BAND_RED}")
    url_g=$(make_zarr_url "${BAND_GREEN}")
    url_b=$(make_zarr_url "${BAND_BLUE}")
    vrt_file="${OUTPUT_DIR}/rgb.vrt"
    png_file="${OUTPUT_DIR}/images/rgb_composite_bash.png"

    local vrt_out
    if vrt_out=$(gdalbuildvrt -separate "$vrt_file" "$url_r" "$url_g" "$url_b" -q 2>&1); then
        # -outsize 10% 10% forces GDAL to use an overview for speed
        # -scale with no args auto-stretches to full 8-bit range
        local png_out
        if png_out=$(gdal_translate -of PNG -scale -outsize 10% 10% \
            "$vrt_file" "$png_file" -q 2>&1); then
            if [[ -f "$png_file" ]] && [[ -s "$png_file" ]]; then
                local size_kb
                size_kb=$(( $(file_size_bytes "$png_file") / 1024 ))
                _STATUS="PASS"
                _DETAILS="RGB PNG written (${size_kb} KB): $(basename "$png_file")"
            else
                _STATUS="FAIL"
                _DETAILS="gdal_translate PNG exited 0 but file missing or empty"
            fi
        else
            _STATUS="FAIL"
            _DETAILS="gdal_translate to PNG failed: $(head -1 <<< "$png_out")"
        fi
    else
        _STATUS="FAIL"
        _DETAILS="gdalbuildvrt failed: $(head -1 <<< "$vrt_out")"
    fi
    _ARTIFACT="$png_file"
}

# --- Task 6: Overview reading

task_overviews() {
    local url out_file tmpstdout stdout_content net_bytes
    url=$(make_zarr_url "${BAND_DEFAULT}")
    out_file="${OUTPUT_DIR}/overview.tif"
    tmpstdout=$(mktemp)
    trap "rm -f '${tmpstdout}'" RETURN

    # Check overview count first
    local info_out ovr_count
    info_out=$(gdalinfo "$url" 2>&1)
    ovr_count=$(grep 'Overviews:' <<< "$info_out" \
        | head -1 \
        | grep -oP '[0-9]+x[0-9]+' \
        | wc -l || true)

    if (( ovr_count == 0 )); then
        _STATUS="FAIL"
        _DETAILS="No overviews present"
        return
    fi

    # -ovr 0 = coarsest overview in gdal_translate CLI
    # CPL_VSIL_SHOW_NETWORK_STATS prints to stdout; capture it, suppress stderr noise
    if CPL_VSIL_SHOW_NETWORK_STATS=YES \
        gdal_translate "$url" "$out_file" -ovr 0 -q >"$tmpstdout" 2>/dev/null; then

        stdout_content=$(cat "$tmpstdout")
        net_bytes=$(parse_network_bytes "$stdout_content")
        local net_kb size_kb
        net_kb=$(( net_bytes / 1024 ))
        size_kb=$(( $(file_size_bytes "$out_file") / 1024 ))

        if [[ "$net_bytes" -eq 0 ]]; then
            _NETWORK="—"
        else
            _NETWORK="${net_kb} KB"
        fi
        _STATUS="PASS"
        _DETAILS="${ovr_count} overview levels; coarsest: ${size_kb} KB; network: ${_NETWORK}"
        _ARTIFACT="$out_file"
    else
        _STATUS="FAIL"
        _DETAILS="gdal_translate -ovr 0 failed"
    fi
}

# --- Task 7: Multiple resolutions (r10m / r20m / r60m)

task_resolutions() {
    _STATUS="PASS"
    _DETAILS=""

    local -a bands=("${BAND_DEFAULT}" "${BAND_R20M}" "${BAND_R60M}")
    local -a labels=("r10m" "r20m" "r60m")
    local -a expected_px=("10" "20" "60")

    for i in 0 1 2; do
        local url info_out pixel_x band_ok
        url=$(make_zarr_url "${bands[$i]}")
        if info_out=$(gdalinfo "$url" 2>&1); then
            # "Pixel Size = (10.000000000,-10.000000000)"
            pixel_x=$(grep -oP 'Pixel Size = \(\K[0-9.]+' <<< "$info_out" | head -1 || true)
            if [[ -n "$pixel_x" ]]; then
                band_ok=$(awk "BEGIN {
                    diff = $pixel_x - ${expected_px[$i]};
                    if (diff < 0) diff = -diff;
                    print (diff <= 1) ? \"true\" : \"false\"
                }")
                if [[ "$band_ok" == "true" ]]; then
                    _DETAILS+="${labels[$i]}=${pixel_x}m(ok) "
                else
                    _STATUS="FAIL"
                    _DETAILS+="${labels[$i]}=${pixel_x}m(expected ${expected_px[$i]}m) "
                fi
            else
                _STATUS="FAIL"
                _DETAILS+="${labels[$i]}=pixel_size_not_found "
            fi
        else
            _STATUS="FAIL"
            _DETAILS+="${labels[$i]}=gdalinfo_failed "
        fi
    done
    _DETAILS="${_DETAILS% }"
}

# --- Task 8: GeoZarr conventions compliance

task_conventions() {
    local url info_out
    url=$(make_zarr_url "${BAND_DEFAULT}")

    if info_out=$(gdalinfo -json "$url" 2>&1); then
        local driver_ok crs_ok gt_ok

        # Driver name must contain "Zarr"
        grep -qi '"driverShortName".*[Zz]arr\|"driverLongName".*[Zz]arr' <<< "$info_out" \
            && driver_ok=true || driver_ok=false

        # CRS block must be present and non-empty
        if grep -q '"coordinateSystem"' <<< "$info_out"; then
            grep -qE '"coordinateSystem"[[:space:]]*:[[:space:]]*\{\}' <<< "$info_out" \
                && crs_ok=false || crs_ok=true
        else
            crs_ok=false
        fi

        # GeoTransform origin X must be non-zero (i.e. not the [0,1,0,0,0,1] default)
        # gdalinfo -json splits the array across lines; take the line after "geoTransform"
        gt_ok=false
        local origin_x
        origin_x=$(grep -A1 '"geoTransform"' <<< "$info_out" | tail -1 | grep -oE '[0-9]+\.[0-9]+' | head -1 || true)
        if [[ -n "$origin_x" ]]; then
            awk "BEGIN { exit ($origin_x == 0) }" && gt_ok=true || gt_ok=false
        fi

        if $driver_ok && $crs_ok && $gt_ok; then
            _STATUS="PASS"
            _DETAILS="driver=Zarr CRS=present GeoTransform=non-default"
        else
            _STATUS="FAIL"
            _DETAILS="driver=${driver_ok} crs=${crs_ok} geotransform=${gt_ok}"
        fi
    else
        _STATUS="FAIL"
        _DETAILS="gdalinfo -json failed: $(head -1 <<< "$info_out")"
    fi
}

# --- Report generation

generate_report() {
    local gdal_ver total
    gdal_ver=$(gdalinfo --version 2>/dev/null | head -1 || echo "unknown")
    total=$(( PASS_COUNT + FAIL_COUNT ))

    {
        printf '# EOPF / GeoZarr Validation Report (Bash)\n\n'

        printf '## Environment\n\n'
        printf -- '- **GDAL version**: %s\n' "$gdal_ver"
        printf -- '- **Dataset URL**: %s\n' "$EOPF_DATASET_URL"
        printf -- '- **Date**: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        printf '\n'

        printf '## Results\n\n'
        printf '| Task | Status | Duration | Network | Details |\n'
        printf '|------|--------|----------|---------|----------|\n'

        local i
        for (( i=0; i<${#TASK_NAMES[@]}; i++ )); do
            local icon safe_details
            [[ "${TASK_STATUS[$i]}" == "PASS" ]] && icon="✅ PASS" || icon="❌ FAIL"
            safe_details="${TASK_DETAILS[$i]//|/\\|}"
            printf '| %s | %s | %s | %s | %s |\n' \
                "${TASK_NAMES[$i]}" \
                "$icon" \
                "${TASK_DURATION[$i]}" \
                "${TASK_NETWORK[$i]}" \
                "$safe_details"
        done
        printf '\n'

        printf '## Summary\n\n'
        printf '**%d/%d tasks passed**\n\n' "$PASS_COUNT" "$total"

        # Artifacts: embed any PNG outputs
        local has_artifacts=false
        for (( i=0; i<${#TASK_ARTIFACTS[@]}; i++ )); do
            local art="${TASK_ARTIFACTS[$i]:-}"
            if [[ -n "$art" && -f "$art" ]]; then
                case "$art" in *.png|*.jpg|*.jpeg) has_artifacts=true; break ;; esac
            fi
        done

        if $has_artifacts; then
            printf '## Artifacts\n\n'
            for (( i=0; i<${#TASK_ARTIFACTS[@]}; i++ )); do
                local art="${TASK_ARTIFACTS[$i]:-}"
                if [[ -n "$art" && -f "$art" ]]; then
                    case "$art" in
                        *.png|*.jpg|*.jpeg)
                            printf '### %s\n' "${TASK_NAMES[$i]}"
                            printf '![%s](%s)\n\n' "$(basename "$art")" "$art"
                            ;;
                    esac
                fi
            done
        fi
    } > "${REPORT_FILE}"

    printf '\nReport: %s\n' "${REPORT_FILE}"
}

# --- Entry point

main() {
    printf "${BOLD}EOPF / GeoZarr GDAL Validation${RESET}\n"
    printf 'Dataset: %s\n\n' "$EOPF_DATASET_URL"

    check_prerequisites
    mkdir -p "${OUTPUT_DIR}/images"

    printf '\nRunning 8 validation tasks...\n\n'

    run_task "1. Metadata"            task_metadata
    run_task "2. Partial Read"        task_partial_read
    run_task "3. Export -> GeoTIFF"   task_export
    run_task "4. Reproject -> 4326"   task_reproject
    run_task "5. RGB Composite"       task_composite
    run_task "6. Overview Read"       task_overviews
    run_task "7. Resolutions"         task_resolutions
    run_task "8. GeoZarr Conventions" task_conventions

    generate_report

    local total=$(( PASS_COUNT + FAIL_COUNT ))
    printf "\n${BOLD}%d/%d tasks passed${RESET}\n" "$PASS_COUNT" "$total"

    # Non-zero exit if any task failed (useful for CI)
    (( FAIL_COUNT == 0 ))
}

main "$@"
