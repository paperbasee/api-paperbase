#!/usr/bin/env bash
set -euo pipefail

# Disaster recovery helper for Paperbase Postgres backups on S3-compatible storage (e.g. R2).
# Requires: aws CLI, psql, pg_restore, gzip; same AWS_* / BACKUP_* env as backup jobs.
#
# Usage:
#   ./scripts/restore.sh <DATABASE_URL> [--type full|snapshot] [--dry-run]
#   ./scripts/restore.sh <DATABASE_URL> --s3-uri s3://bucket/path/to/file.sql.gz [--dry-run]
#   ./scripts/restore.sh <DATABASE_URL> --s3-uri s3://bucket/path/to/file.dump [--dry-run]
#
# Snapshot (.dump) files are full logical dumps (not WAL deltas). Restore one artifact:
# full OR snapshot, never both in a single run.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
. "$ROOT/backup/lib.sh"

usage() {
  cat <<'EOF'
Usage:
  scripts/restore.sh <DATABASE_URL> [--type full|snapshot] [--dry-run]
  scripts/restore.sh <DATABASE_URL> --s3-uri s3://bucket/path/file.sql.gz|file.dump [--dry-run]

Env: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_S3_ENDPOINT_URL.
For latest pointer resolution: also BACKUP_S3_BUCKET or AWS_STORAGE_BUCKET_NAME.
EOF
  exit "${1:-0}"
}

if [[ $# -lt 1 ]]; then
  paperbase_log "ERROR: You must provide a database URL"
  usage 1
fi
if [[ "$1" == "-h" || "$1" == "--help" ]]; then
  usage 0
fi

target_url="$1"
shift

dry_run=0
restore_type="full"
s3_uri=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --type)
      if [[ -z "${2:-}" ]]; then
        paperbase_log "ERROR: --type requires a value: full or snapshot"
        exit 1
      fi
      restore_type="$2"
      shift 2
      ;;
    --s3-uri)
      if [[ -z "${2:-}" ]]; then
        paperbase_log "ERROR: --s3-uri requires a value"
        exit 1
      fi
      s3_uri="$2"
      shift 2
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    -h | --help) usage 0 ;;
    *)
      paperbase_log "ERROR: unknown option: $1"
      usage 1
      ;;
  esac
done

if [[ "$restore_type" != "full" && "$restore_type" != "snapshot" ]]; then
  paperbase_log "ERROR: --type must be one of: full, snapshot"
  exit 1
fi

paperbase_require_env AWS_ACCESS_KEY_ID || exit 1
paperbase_require_env AWS_SECRET_ACCESS_KEY || exit 1
paperbase_require_env AWS_S3_ENDPOINT_URL || exit 1

bucket="$(paperbase_backup_bucket)"
if [[ -z "$s3_uri" && -z "$bucket" ]]; then
  paperbase_log "ERROR: set BACKUP_S3_BUCKET or AWS_STORAGE_BUCKET_NAME (required when --s3-uri is not provided)"
  exit 1
fi

if [[ -z "$target_url" ]]; then
  paperbase_log "ERROR: You must provide a database URL"
  exit 1
fi

tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/paperbase-restore.XXXXXX")"
cleanup() {
  rm -rf "$tmp_root"
}
trap cleanup EXIT INT TERM

resolve_uri_from_latest_json() {
  local latest_file="${tmp_root}/latest.json"
  local pointer_value=""

  if ! paperbase_try_read_latest_json "$bucket" "$latest_file"; then
    paperbase_log "ERROR: failed to fetch latest pointer s3://${bucket}/$(paperbase_latest_json_key)"
    exit 1
  fi

  if [[ "$restore_type" == "full" ]]; then
    pointer_value="$(paperbase_json_get_field "$latest_file" latest_full || true)"
  else
    pointer_value="$(paperbase_json_get_field "$latest_file" latest_snapshot || true)"
  fi

  if [[ -z "$pointer_value" ]]; then
    paperbase_log "ERROR: latest pointer for type '${restore_type}' is empty in latest.json"
    exit 1
  fi

  if [[ "$pointer_value" == s3://* ]]; then
    printf '%s' "$pointer_value"
    return
  fi
  printf 's3://%s/%s' "$bucket" "${pointer_value#/}"
}

if [[ -n "$s3_uri" ]]; then
  uri="$s3_uri"
else
  uri="$(resolve_uri_from_latest_json)"
fi
paperbase_log "Resolved object: $uri"

fname="${uri##*/}"
local_path="${tmp_root}/${fname}"

if ((dry_run)); then
  paperbase_log "DRY-RUN: would download $uri -> $local_path"
  if [[ "$fname" == *.sql.gz ]]; then
    paperbase_log "DRY-RUN: would run: gunzip -c ... | psql <target>"
  elif [[ "$fname" == *.dump ]]; then
    paperbase_log "DRY-RUN: would run: pg_restore ... $local_path"
  else
    paperbase_log "ERROR: unsupported file type (expected .sql.gz or .dump)"
    exit 1
  fi
  exit 0
fi

paperbase_log "Downloading..."
paperbase_aws_s3_download_with_retry "$uri" "$local_path"

restore_pg_args=(--no-owner --no-acl --verbose)
if [[ "${RESTORE_PGRESTORE_CLEAN:-}" == "1" ]]; then
  restore_pg_args+=(--clean --if-exists)
fi
if [[ -n "${PG_RESTORE_EXTRA_ARGS:-}" ]]; then
  read -r -a ra <<<"$PG_RESTORE_EXTRA_ARGS"
  restore_pg_args+=("${ra[@]}")
fi

if [[ "$fname" == *.sql.gz ]]; then
  paperbase_log "Restoring plain SQL (gzip) via psql..."
  set -o pipefail
  gunzip -c "$local_path" | psql "$target_url"
elif [[ "$fname" == *.dump ]]; then
  paperbase_log "Restoring custom-format dump via pg_restore..."
  pg_restore "${restore_pg_args[@]}" -d "$target_url" "$local_path"
else
  paperbase_log "ERROR: unsupported file type (expected .sql.gz or .dump)"
  exit 1
fi

paperbase_log "OK: restore finished."
