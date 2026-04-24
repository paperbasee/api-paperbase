#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=/dev/null
. /usr/local/lib/paperbase-backup/lib.sh

if [ -z "${DIRECT_DATABASE_URL:-}" ]; then
  echo "ERROR: DIRECT_DATABASE_URL is not set"
  exit 1
fi
paperbase_require_env AWS_ACCESS_KEY_ID || exit 1
paperbase_require_env AWS_SECRET_ACCESS_KEY || exit 1
paperbase_require_env AWS_S3_ENDPOINT_URL || exit 1

bucket="$(paperbase_backup_bucket)"
if [[ -z "$bucket" ]]; then
  paperbase_log "ERROR: set BACKUP_S3_BUCKET or AWS_STORAGE_BUCKET_NAME"
  exit 1
fi

prefix="${BACKUP_PREFIX_FULL:-backups/full}"
prefix="${prefix#/}"
prefix="${prefix%/}"

lock_dir="$(paperbase_tmpdir)"
mkdir -p "$lock_dir"
exec 200>"$lock_dir/full.lock"
if ! flock -n 200; then
  paperbase_log "SKIP: another full backup is running."
  exit 0
fi

paperbase_wait_for_postgres "$DIRECT_DATABASE_URL" || exit 1

stamp="$(date -u +"%Y%m%d_%H%M%S")"
path_date="$(date -u +"%Y/%m/%d")"
remote_key="${prefix}/${path_date}/paperbase_${stamp}.sql.gz"

tmp_dir="$(mktemp -d "$(paperbase_tmpdir)/full.XXXXXX")"
cleanup() {
  rm -rf "$tmp_dir"
}
trap cleanup EXIT INT TERM

tmp_sql_gz="${tmp_dir}/dump.sql.gz"
paperbase_log "Starting pg_dump (plain SQL, gzip) -> ${tmp_sql_gz##*/}"

extra_args=()
if [[ -n "${PG_DUMP_EXTRA_ARGS:-}" ]]; then
  read -r -a extra_args <<<"$PG_DUMP_EXTRA_ARGS"
fi

set -o pipefail
paperbase_run_nice pg_dump "${extra_args[@]}" "$DIRECT_DATABASE_URL" | gzip -1 >"$tmp_sql_gz"

paperbase_log "Uploading s3://${bucket}/${remote_key}"
paperbase_aws_s3_cp_with_retry "$tmp_sql_gz" "s3://${bucket}/${remote_key}"

paperbase_try_update_latest_pointer "$bucket" "$remote_key" ""
paperbase_log "OK: full backup uploaded."
