FROM postgres:18-alpine

RUN apk add --no-cache \
    aws-cli \
    bash \
    gettext \
    gzip \
    util-linux

COPY backup/lib.sh /usr/local/lib/paperbase-backup/lib.sh
COPY backup/backup-full.sh /usr/local/bin/backup-full.sh
COPY backup/backup-snapshot.sh /usr/local/bin/backup-snapshot.sh
COPY backup/entrypoint.sh /usr/local/bin/backup-entrypoint.sh
COPY backup/crontab.tpl /etc/paperbase/crontab.tpl

RUN chmod +x \
    /usr/local/bin/backup-full.sh \
    /usr/local/bin/backup-snapshot.sh \
    /usr/local/bin/backup-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/backup-entrypoint.sh"]
