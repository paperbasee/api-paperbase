#!/usr/bin/env bash
# Shared helpers for Paperbase backup scripts (sourced, not executed).
# shellcheck shell=bash

paperbase_log() {
  echo "[paperbase-backup] $(date -u +"%Y-%m-%dT%H:%M:%SZ") $*" >&2
}

paperbase_require_env() {
  local name="$1"
  local val="${!name:-}"
  val="${val//[[:space:]]/}"
  if [[ -z "$val" ]]; then
    paperbase_log "ERROR: missing required environment variable: $name"
    return 1
  fi
  return 0
}

paperbase_backup_bucket() {
  local b="${BACKUP_S3_BUCKET:-}"
  b="${b//[[:space:]]/}"
  if [[ -z "$b" ]]; then
    b="${AWS_STORAGE_BUCKET_NAME:-}"
    b="${b//[[:space:]]/}"
  fi
  printf '%s' "$b"
}

paperbase_aws_region() {
  local r="${AWS_DEFAULT_REGION:-}"
  r="${r//[[:space:]]/}"
  if [[ -z "$r" ]]; then
    r="${AWS_S3_REGION_NAME:-}"
    r="${r//[[:space:]]/}"
  fi
  if [[ -z "$r" ]]; then
    r="auto"
  fi
  printf '%s' "$r"
}

paperbase_wait_for_postgres() {
  local db_url="${1:-}"
  if [[ -z "$db_url" ]]; then
    paperbase_log "ERROR: direct database URL is required for readiness check."
    return 1
  fi
  local deadline=$((SECONDS + ${BACKUP_PG_WAIT_SECONDS:-120}))
  paperbase_log "Waiting for PostgreSQL..."
  while ((SECONDS < deadline)); do
    if pg_isready -q -d "$db_url" 2>/dev/null; then
      paperbase_log "PostgreSQL is accepting connections."
      return 0
    fi
    if psql "$db_url" -c "select 1" >/dev/null 2>&1; then
      paperbase_log "PostgreSQL is accepting connections (via psql)."
      return 0
    fi
    sleep 2
  done
  paperbase_log "ERROR: PostgreSQL not ready after timeout."
  return 1
}

paperbase_aws_s3_cp_with_retry() {
  local src="$1"
  local dst="$2"
  local attempts="${BACKUP_AWS_MAX_ATTEMPTS:-3}"
  local i=1
  local sleep_s=2

  while ((i <= attempts)); do
    if aws s3 cp "$src" "$dst" \
      --endpoint-url "$AWS_S3_ENDPOINT_URL" \
      --region "$(paperbase_aws_region)"; then
      return 0
    fi
    paperbase_log "WARN: aws s3 cp failed (attempt $i/$attempts), retrying in ${sleep_s}s..."
    sleep "$sleep_s"
    i=$((i + 1))
    sleep_s=$((sleep_s * 2))
  done
  paperbase_log "ERROR: aws s3 cp failed after $attempts attempts"
  return 1
}

# Download from S3/R2 (used by scripts/restore.sh when sourced from repo root).
paperbase_aws_s3_download_with_retry() {
  local src="$1"
  local dst="$2"
  local attempts="${BACKUP_AWS_MAX_ATTEMPTS:-3}"
  local i=1
  local sleep_s=2

  while ((i <= attempts)); do
    if aws s3 cp "$src" "$dst" \
      --endpoint-url "$AWS_S3_ENDPOINT_URL" \
      --region "$(paperbase_aws_region)"; then
      return 0
    fi
    paperbase_log "WARN: aws s3 download failed (attempt $i/$attempts), retrying in ${sleep_s}s..."
    sleep "$sleep_s"
    i=$((i + 1))
    sleep_s=$((sleep_s * 2))
  done
  paperbase_log "ERROR: aws s3 download failed after $attempts attempts"
  return 1
}

paperbase_run_nice() {
  if command -v nice >/dev/null 2>&1; then
    nice -n 10 "$@"
  else
    "$@"
  fi
}

paperbase_tmpdir() {
  mkdir -p "${BACKUP_TMP_DIR:-/tmp/paperbase-backup}"
  printf '%s' "${BACKUP_TMP_DIR:-/tmp/paperbase-backup}"
}

paperbase_latest_json_key() {
  local key="${BACKUP_META_LATEST_KEY:-meta/latest.json}"
  key="${key#/}"
  key="${key%/}"
  printf '%s' "$key"
}

paperbase_json_get_field() {
  local file="$1"
  local field="$2"
  sed -n "s/.*\"${field}\"[[:space:]]*:[[:space:]]*\"\\([^\"]*\\)\".*/\\1/p" "$file" | sed -n '1p'
}

paperbase_write_latest_json() {
  local dst="$1"
  local latest_full="$2"
  local latest_snapshot="$3"
  local timestamp="${4:-$(date -u +"%Y-%m-%dT%H:%M:%SZ")}"
  local tmp="${dst}.tmp"

  cat >"$tmp" <<EOF
{
  "latest_full": "${latest_full}",
  "latest_snapshot": "${latest_snapshot}",
  "timestamp": "${timestamp}"
}
EOF
  mv "$tmp" "$dst"
}

paperbase_try_read_latest_json() {
  local bucket="$1"
  local dst="$2"
  local uri="s3://${bucket}/$(paperbase_latest_json_key)"

  if paperbase_aws_s3_download_with_retry "$uri" "$dst"; then
    return 0
  fi
  return 1
}

paperbase_try_update_latest_pointer() {
  local bucket="$1"
  local new_latest_full="${2:-}"
  local new_latest_snapshot="${3:-}"
  local tmp_dir
  tmp_dir="$(mktemp -d "$(paperbase_tmpdir)/meta.XXXXXX")"
  local latest_local="${tmp_dir}/latest.json"
  local existing_full=""
  local existing_snapshot=""
  local final_full=""
  local final_snapshot=""
  local target_uri="s3://${bucket}/$(paperbase_latest_json_key)"

  if paperbase_try_read_latest_json "$bucket" "$latest_local"; then
    existing_full="$(paperbase_json_get_field "$latest_local" latest_full || true)"
    existing_snapshot="$(paperbase_json_get_field "$latest_local" latest_snapshot || true)"
  fi

  final_full="${new_latest_full:-$existing_full}"
  final_snapshot="${new_latest_snapshot:-$existing_snapshot}"
  paperbase_write_latest_json "$latest_local" "$final_full" "$final_snapshot"

  if ! paperbase_aws_s3_cp_with_retry "$latest_local" "$target_uri"; then
    paperbase_log "WARN: latest pointer update failed for ${target_uri} (backup kept)."
    rm -rf "$tmp_dir"
    return 0
  fi

  rm -rf "$tmp_dir"
  paperbase_log "Updated latest pointer: ${target_uri}"
  return 0
}
