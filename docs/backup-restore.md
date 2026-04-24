# Backup and Restore Runbook

This runbook describes the production backup and restore flow for PostgreSQL using S3-compatible storage (Cloudflare R2).

## Required Environment Variables

- `DIRECT_DATABASE_URL` (backup source, direct Postgres connection)
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `AWS_DEFAULT_REGION` (R2 commonly uses `auto`)
- `AWS_S3_ENDPOINT_URL`
- `BACKUP_S3_BUCKET`
- `BACKUP_PREFIX_FULL` (default: `backups/full`)
- `BACKUP_PREFIX_SNAPSHOT` (default: `backups/snapshot`)
- `TZ` (optional cron timezone; defaults to UTC behavior when unset)

Compatibility:
- `BACKUP_PREFIX_WAL` is still accepted if `BACKUP_PREFIX_SNAPSHOT` is unset.

## Storage Layout

- Full backups: `backups/full/YYYY/MM/DD/file.sql.gz`
- Snapshot backups: `backups/snapshot/YYYY/MM/DD/file.dump`
- Latest pointer: `meta/latest.json`

Example `meta/latest.json`:

```json
{
  "latest_full": "backups/full/2026/04/24/paperbase_20260424_020000.sql.gz",
  "latest_snapshot": "backups/snapshot/2026/04/24/paperbase_20260424_121000.dump",
  "timestamp": "2026-04-24T12:10:07Z"
}
```

## Backup Flow

1. Backup container cron triggers `backup-full.sh` and `backup-snapshot.sh`.
2. Script creates one artifact:
   - full: `pg_dump "$DIRECT_DATABASE_URL" | gzip` -> `.sql.gz`
   - snapshot: `pg_dump -Fc "$DIRECT_DATABASE_URL"` -> `.dump`
3. Artifact is uploaded to R2 in the standardized prefix path.
4. After successful upload, script updates `meta/latest.json`.
5. If `latest.json` update fails, backup still succeeds and only logs a warning.

This makes backup data path authoritative while keeping pointer updates failure-safe.

## Restore Flow

1. Create a new PostgreSQL database (any host/environment).
2. Choose the target DB URL explicitly (first positional argument).
3. Run:
   - `scripts/restore.sh postgresql://user:pass@host:5432/db --type full`
   - or `scripts/restore.sh postgresql://user:pass@host:5432/db --type snapshot`
   - or `scripts/restore.sh postgresql://user:pass@host:5432/db --s3-uri s3://bucket/path/file.sql.gz|file.dump` (manual override)
4. Without `--s3-uri`, restore fetches `meta/latest.json`.
5. Restore picks exactly one object:
   - `--type full` -> `latest_full`
   - `--type snapshot` -> `latest_snapshot`
6. Restore downloads the object and executes:
   - `.sql.gz` -> `gunzip -c | psql`
   - `.dump` -> `pg_restore`
7. Database is recovered from that single backup artifact.

## Restore Strategy Rules

- FULL backup is the primary recovery source.
- SNAPSHOT backup is an alternative recovery point.
- Restores are exclusive: FULL or SNAPSHOT, never chained.

Correct:
- restore FULL
- restore SNAPSHOT

Incorrect:
- restore FULL then SNAPSHOT in one recovery workflow
- combine incremental layers from these artifacts

## Determinism and Safety

- One restore target per execution.
- Manual `--s3-uri` always overrides pointer selection.
- Restore fails fast if no target DB URL argument is provided.
- No secrets are hardcoded in scripts.
- Backup upload is treated as critical; pointer update is best-effort.
