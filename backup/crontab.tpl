# Paperbase PostgreSQL backups (UTC unless TZ is set on the container)
${BACKUP_CRON_FULL} /usr/local/bin/backup-full.sh >> /proc/1/fd/1 2>&1
${BACKUP_CRON_SNAPSHOT} /usr/local/bin/backup-snapshot.sh >> /proc/1/fd/1 2>&1
