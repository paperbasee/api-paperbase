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

prefix="${BACKUP_PREFIX_SNAPSHOT:-${BACKUP_PREFIX_WAL:-backups/snapshot}}"
prefix="${prefix#/}"
prefix="${prefix%/}"

lock_dir="$(paperbase_tmpdir)"
mkdir -p "$lock_dir"
exec 200>"$lock_dir/snapshot.lock"
if ! flock -n 200; then
  paperbase_log "SKIP: another snapshot is running (overlapping cron tick)."
  exit 0
fi

paperbase_wait_for_postgres "$DIRECT_DATABASE_URL" || exit 1

stamp="$(date -u +"%Y%m%d_%H%M%S")"
path_date="$(date -u +"%Y/%m/%d")"
remote_key="${prefix}/${path_date}/paperbase_${stamp}.dump"

tmp_dir="$(mktemp -d "$(paperbase_tmpdir)/snap.XXXXXX")"
cleanup() {
  rm -rf "$tmp_dir"
}
trap cleanup EXIT INT TERM

tmp_dump="${tmp_dir}/snapshot.dump"
paperbase_log "Starting pg_dump -Fc -> ${tmp_dump##*/}"

extra_args=()
if [[ -n "${PG_DUMP_EXTRA_ARGS:-}" ]]; then
  read -r -a extra_args <<<"$PG_DUMP_EXTRA_ARGS"
fi

set -o pipefail
paperbase_run_nice pg_dump -Fc -Z6 "${extra_args[@]}" -f "$tmp_dump" "$DIRECT_DATABASE_URL"

paperbase_log "Uploading s3://${bucket}/${remote_key}"
paperbase_aws_s3_cp_with_retry "$tmp_dump" "s3://${bucket}/${remote_key}"

paperbase_try_update_latest_pointer "$bucket" "" "$remote_key"
paperbase_log "OK: snapshot uploaded."
