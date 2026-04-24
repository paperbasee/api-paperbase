#!/usr/bin/env bash
set -euo pipefail

export BACKUP_CRON_FULL="${BACKUP_CRON_FULL:-0 2 * * *}"
export BACKUP_CRON_SNAPSHOT="${BACKUP_CRON_SNAPSHOT:-*/10 * * * *}"

mkdir -p /var/run/paperbase-backup

if ! command -v envsubst >/dev/null 2>&1; then
  echo "envsubst not found" >&2
  exit 1
fi

envsubst '$BACKUP_CRON_FULL $BACKUP_CRON_SNAPSHOT' </etc/paperbase/crontab.tpl >/etc/crontabs/root
chmod 600 /etc/crontabs/root

# BusyBox crond: foreground; -d sends logs to stderr (Docker log driver)
exec crond -f -d 6
