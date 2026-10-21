#!/usr/bin/env bash
# Extract SMARD + weather locally, upload them to the shared UC landing volume and verify the upload.
# Bash equivalent of sync_landing.ps1, used by .github/workflows/daily-refresh.yml.
#
# Auth comes from env vars (no -p flag): DATABRICKS_HOST, DATABRICKS_CLIENT_ID,
# DATABRICKS_CLIENT_SECRET (service principal OAuth M2M). Locally you can set
# DATABRICKS_CONFIG_PROFILE=free instead.
#
# Settings (env, optional): LANDING_ROOT (/Volumes/workspace/energy_raw/landing, shared by all
# targets; must already exist), LOCAL_DIR (data/landing), SMARD_WEEKS (8), PAST_DAYS (63).
# The extractor log goes to $LOCAL_DIR/logs/.
#
# Uploads use --overwrite so the re-downloaded newest SMARD chunks replace older copies; bronze
# re-ingests overwritten files (cloudFiles.allowOverwrites) and silver dedupes them.
#
#   bash scripts/sync_landing.sh
#   LANDING_ROOT=/Volumes/workspace/energy_raw/landing bash scripts/sync_landing.sh
set -euo pipefail

LANDING_ROOT="${LANDING_ROOT:-/Volumes/workspace/energy_raw/landing}"
LOCAL_DIR="${LOCAL_DIR:-data/landing}"
SMARD_WEEKS="${SMARD_WEEKS:-8}"
PAST_DAYS="${PAST_DAYS:-63}"
SOURCES=(smard weather)

fail() {
  echo "FAILED: $*" >&2
  exit 1
}

# Run the databricks CLI; on a non-zero exit stop with the step name. Prints the CLI's stdout.
dbx() {
  local step="$1"
  shift
  local out
  if ! out="$(databricks "$@")"; then
    fail "$step (databricks $* exited non-zero)"
  fi
  printf '%s\n' "$out"
}

command -v databricks >/dev/null || fail "databricks CLI not found on PATH."
command -v uv >/dev/null || fail "uv not found on PATH."
if [[ -z "${DATABRICKS_CONFIG_PROFILE:-}" ]]; then
  for var in DATABRICKS_HOST DATABRICKS_CLIENT_ID DATABRICKS_CLIENT_SECRET; do
    [[ -n "${!var:-}" ]] || fail "env var $var is not set."
  done
fi

cd "$(dirname "${BASH_SOURCE[0]}")/.."
volume_base="dbfs:${LANDING_ROOT%/}"

# 1. Extract (both sources). pipefail makes a failed extractor fail the script despite tee.
mkdir -p "${LOCAL_DIR}/logs"
log_file="${LOCAL_DIR}/logs/extract_$(date -u +%Y%m%dT%H%M%SZ).log"
echo "extract: weeks=${SMARD_WEEKS} past_days=${PAST_DAYS} -> ${LOCAL_DIR} (log ${log_file})"
uv run python -m energy_lakehouse.extract_local --source all --out "${LOCAL_DIR}" \
  --weeks "${SMARD_WEEKS}" --past-days "${PAST_DAYS}" 2>&1 | tee "${log_file}"

# 2. Check every local source before uploading anything.
for source in "${SOURCES[@]}"; do
  local_path="${LOCAL_DIR}/${source}"
  [[ -d "${local_path}" ]] || fail "local folder ${local_path} not found."
  count="$(find "${local_path}" -type f -name '*.json' | wc -l | tr -d ' ')"
  [[ "${count}" -gt 0 ]] || fail "local folder ${local_path} contains no .json files."
  echo "local  ${source} : ${count} files in ${local_path}"
done

# 3. Upload.
dbx "check landing volume ${volume_base} exists" fs ls "${volume_base}" >/dev/null
for source in "${SOURCES[@]}"; do
  target="${volume_base}/${source}"
  dbx "create ${target}" fs mkdir "${target}" >/dev/null
  echo "upload ${source} : ${LOCAL_DIR}/${source} -> ${target}"
  dbx "upload ${source}" fs cp -r --overwrite "${LOCAL_DIR}/${source}" "${target}" >/dev/null
done

# 4. Verify: each listing must succeed and return at least one entry.
for source in "${SOURCES[@]}"; do
  target="${volume_base}/${source}"
  entries="$(dbx "list ${target}" fs ls "${target}")"
  n="$(grep -c '[^[:space:]]' <<<"${entries}" || true)"
  [[ "${n}" -gt 0 ]] || fail "${target} is empty after upload."
  echo "verify ${source} : ${n} entries in ${target} ($(tr '\n' ' ' <<<"${entries}"))"
done

echo "OK: synced ${SOURCES[*]} to ${volume_base}"
