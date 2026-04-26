#!/usr/bin/env bash
set -euo pipefail

# shellcheck source=/dev/null
if [[ -f "/usr/local/lib/paperbase-backup/lib.sh" ]]; then
  . /usr/local/lib/paperbase-backup/lib.sh
else
  . "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
fi

if [ -z "${DIRECT_DATABASE_URL:-}" ]; then
  echo "ERROR: DIRECT_DATABASE_URL is not set"
  exit 1
fi
paperbase_require_env AWS_ACCESS_KEY_ID     || exit 1
paperbase_require_env AWS_SECRET_ACCESS_KEY || exit 1
paperbase_require_env AWS_S3_ENDPOINT_URL   || exit 1

bucket="$(paperbase_backup_bucket)"
if [[ -z "$bucket" ]]; then
  paperbase_log "ERROR: set BACKUP_S3_BUCKET or AWS_STORAGE_BUCKET_NAME"
  exit 1
fi

prefix="${BACKUP_PREFIX_BASE:-backups/base}"
prefix="${prefix#/}"
prefix="${prefix%/}"

lock_dir="$(paperbase_tmpdir)"
mkdir -p "$lock_dir"
exec 200>"$lock_dir/base.lock"
if ! flock -n 200; then
  paperbase_log "SKIP: another base backup is running."
  exit 0
fi

paperbase_wait_for_postgres "$DIRECT_DATABASE_URL" || exit 1

stamp="$(date -u +"%Y%m%d_%H%M%S")"
path_date="$(date -u +"%Y/%m/%d")"
remote_key="${prefix}/${path_date}/base_${stamp}.dump"

tmp_dir="$(mktemp -d "$(paperbase_tmpdir)/base.XXXXXX")"
cleanup() {
  rm -rf "$tmp_dir"
}
trap cleanup EXIT INT TERM

tmp_dump="${tmp_dir}/base.dump"
paperbase_log "backup_start type=pg_dump target=${tmp_dump##*/}"

# pg_dump in custom compressed format (-Fc)
# Excludes high-churn table DATA (table structure is kept for restore)
paperbase_run_nice pg_dump \
  "${DIRECT_DATABASE_URL}" \
  --format=custom \
  --compress=9 \
  --no-password \
  --exclude-table-data='django_session' \
  --exclude-table-data='django_admin_log' \
  --exclude-table-data='core_activitylog' \
  --exclude-table-data='emails_emaillog' \
  --exclude-table-data='fraud_check_fraudchecklog' \
  --exclude-table-data='marketing_integrations_storeeventlog' \
  --exclude-table-data='notifications_notificationdismissal' \
  --exclude-table-data='analytics_storedashboardstatssnapshot' \
  --exclude-table-data='django_celery_beat_periodictask' \
  --exclude-table-data='django_celery_beat_crontabschedule' \
  --exclude-table-data='django_celery_beat_intervalschedule' \
  --exclude-table-data='django_celery_beat_solarschedule' \
  --file="${tmp_dump}"

paperbase_log "backup_validate type=pg_dump check=file"
if [[ ! -s "$tmp_dump" ]]; then
  paperbase_log "ERROR: dump file is empty"
  exit 1
fi

# Validate dump is readable
paperbase_log "backup_validate type=pg_dump check=pg_restore"
pg_restore --list "$tmp_dump" > /dev/null

paperbase_log "Uploading s3://${bucket}/${remote_key}"
paperbase_aws_s3_cp_with_retry "$tmp_dump" "s3://${bucket}/${remote_key}"

paperbase_try_update_latest_pointer "$bucket" "$remote_key"
paperbase_log "backup_end type=pg_dump status=ok remote=s3://${bucket}/${remote_key}"
