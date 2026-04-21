from __future__ import annotations

import logging
from collections.abc import Iterable

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings

from config.celery import app
from engine.core.trash_service import purge_expired_trash

logger = logging.getLogger(__name__)

R2_DELETE_BATCH_SIZE = 1000


def _chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _get_r2_delete_client():
    return boto3.client(
        "s3",
        aws_access_key_id=getattr(settings, "AWS_ACCESS_KEY_ID", ""),
        aws_secret_access_key=getattr(settings, "AWS_SECRET_ACCESS_KEY", ""),
        endpoint_url=getattr(settings, "AWS_S3_ENDPOINT_URL", None),
        region_name=getattr(settings, "AWS_S3_REGION_NAME", None),
    )


def _media_storage_location_prefix() -> str:
    storages = getattr(settings, "STORAGES", {}) or {}
    default_cfg = storages.get("default", {}) or {}
    options = default_cfg.get("OPTIONS", {}) or {}
    location = str(options.get("location", "") or "").strip().strip("/")
    return location


def _key_for_bucket(raw_key: str) -> str:
    key = (raw_key or "").strip().lstrip("/")
    if not key:
        return ""
    location = _media_storage_location_prefix()
    if not location:
        return key
    if key == location or key.startswith(f"{location}/"):
        return key
    return f"{location}/{key}"


@app.task(
    name="engine.core.purge_expired_trash",
    soft_time_limit=480,
    time_limit=540,
)
def purge_expired_trash_task() -> int:
    """Celery beat: permanently remove expired trash rows and orphan media."""
    return purge_expired_trash()


@app.task(
    bind=True,
    autoretry_for=(BotoCoreError,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
    soft_time_limit=300,
    time_limit=330,
    name="engine.core.delete_r2_objects",
)
def delete_r2_objects(self, keys: list[str]) -> int:
    """
    Delete media objects from Cloudflare R2 in safe batches.

    Idempotent by design:
    - Duplicate keys are de-duplicated.
    - Missing objects are ignored by S3 DeleteObjects.
    """
    normalized = list(dict.fromkeys([(k or "").strip() for k in (keys or []) if (k or "").strip()]))
    if not normalized:
        return 0
    normalized = list(dict.fromkeys([_key_for_bucket(k) for k in normalized if _key_for_bucket(k)]))
    if not normalized:
        return 0
    bucket = (getattr(settings, "AWS_STORAGE_BUCKET_NAME", "") or "").strip()
    if not bucket:
        logger.warning("R2 cleanup skipped: AWS_STORAGE_BUCKET_NAME is not configured")
        return 0

    client = _get_r2_delete_client()
    deleted_count = 0
    for batch in _chunks(normalized, R2_DELETE_BATCH_SIZE):
        try:
            resp = client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
            )
        except ClientError as exc:
            error_code = (exc.response or {}).get("Error", {}).get("Code", "")
            # Permanent configuration/runtime issue: retries only add queue noise.
            if error_code == "NoSuchBucket":
                logger.error(
                    "R2 cleanup skipped: configured bucket does not exist",
                    extra={"bucket": bucket},
                )
                return deleted_count
            raise
        deleted_count += len(resp.get("Deleted", []) or [])
        # Log and continue: delete must remain idempotent even with partial failures.
        errors = resp.get("Errors", []) or []
        if errors:
            logger.warning(
                "R2 cleanup partial delete errors",
                extra={"error_count": len(errors)},
            )
    return deleted_count
