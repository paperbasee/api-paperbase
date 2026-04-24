from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from django.conf import settings

from config.celery import app

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


@app.task(
    bind=True,
    autoretry_for=(subprocess.TimeoutExpired, OSError),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
    acks_late=True,
    soft_time_limit=7800,
    time_limit=8400,
    name="engine.apps.backup.run_full_backup",
)
def run_full_backup(self) -> None:
    logger.info("Starting full backup task", extra={"task": self.name, "queue": "backup"})
    try:
        stdout, stderr = _run_script("backup-full.sh")
    except subprocess.CalledProcessError as exc:
        logger.error(
            "Full backup script failed",
            extra={
                "task": self.name,
                "queue": "backup",
                "returncode": exc.returncode,
                "stdout": (exc.stdout or "").strip()[-4000:],
                "stderr": (exc.stderr or "").strip()[-4000:],
            },
        )
        raise self.retry(exc=exc, countdown=120)

    logger.info(
        "Full backup task completed",
        extra={"task": self.name, "queue": "backup", "stdout": stdout[-1000:], "stderr": stderr[-1000:]},
    )


@app.task(
    bind=True,
    autoretry_for=(subprocess.TimeoutExpired, OSError),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
    acks_late=True,
    soft_time_limit=4200,
    time_limit=4800,
    name="engine.apps.backup.run_snapshot_backup",
)
def run_snapshot_backup(self) -> None:
    logger.info("Starting snapshot backup task", extra={"task": self.name, "queue": "backup"})
    try:
        stdout, stderr = _run_script("backup-snapshot.sh")
    except subprocess.CalledProcessError as exc:
        logger.error(
            "Snapshot backup script failed",
            extra={
                "task": self.name,
                "queue": "backup",
                "returncode": exc.returncode,
                "stdout": (exc.stdout or "").strip()[-4000:],
                "stderr": (exc.stderr or "").strip()[-4000:],
            },
        )
        raise self.retry(exc=exc, countdown=90)

    logger.info(
        "Snapshot backup task completed",
        extra={"task": self.name, "queue": "backup", "stdout": stdout[-1000:], "stderr": stderr[-1000:]},
    )
