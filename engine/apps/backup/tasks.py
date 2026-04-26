from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from django.conf import settings

from config.celery import app

from engine.apps.backup.prune import prune_noncritical_tables, prune_summary

logger = logging.getLogger(__name__)

BACKUP_SUBPROCESS_TIMEOUT_SECONDS = 3 * 60 * 60


def _backup_script_path(script_name: str) -> str:
    script = Path(settings.BASE_DIR) / "backup" / script_name
    return str(script.resolve())


def _run_script(script_name: str) -> tuple[str, str]:
    script = _backup_script_path(script_name)
    proc = subprocess.run(
        ["/bin/bash", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=BACKUP_SUBPROCESS_TIMEOUT_SECONDS,
        cwd=str(settings.BASE_DIR),
    )
    return (proc.stdout or "").strip(), (proc.stderr or "").strip()


def _execute_base_backup(task) -> None:
    logger.info("Starting base backup task", extra={"task": task.name, "queue": "backup"})
    try:
        try:
            counts = prune_noncritical_tables()
            logger.info(
                "Pre-base-backup prune finished",
                extra={"task": task.name, "prune": prune_summary(counts)},
            )
        except Exception:
            logger.exception(
                "Pre-base-backup prune failed; continuing with pg_basebackup",
                extra={"task": task.name},
            )
        stdout, stderr = _run_script("backup-base.sh")
    except subprocess.CalledProcessError as exc:
        logger.error(
            "Base backup script failed",
            extra={
                "task": task.name,
                "queue": "backup",
                "returncode": exc.returncode,
                "stdout": (exc.stdout or "").strip()[-4000:],
                "stderr": (exc.stderr or "").strip()[-4000:],
            },
        )
        raise task.retry(exc=exc, countdown=120)

    logger.info(
        "Base backup task completed",
        extra={
            "task": task.name,
            "queue": "backup",
            "stdout": stdout[-1000:],
            "stderr": stderr[-1000:],
        },
    )


@app.task(
    bind=True,
    autoretry_for=(subprocess.TimeoutExpired, OSError),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
    acks_late=True,
    soft_time_limit=7800,
    time_limit=8400,
    name="engine.apps.backup.run_base_backup",
)
def run_base_backup(self) -> None:
    _execute_base_backup(self)
