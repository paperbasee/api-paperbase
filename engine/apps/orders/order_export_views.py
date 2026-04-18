"""Create / poll / download async order CSV exports (multi-tenant)."""

from __future__ import annotations

import uuid
from typing import Any

from django.http import FileResponse, Http404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from config.permissions import DenyAPIKeyAccess, IsDashboardUser, IsStoreAdmin
from engine.core.tenancy import get_active_store
from engine.core.tenant_drf import ProvenTenantContextMixin

from .export_queryset import normalize_export_filters
from .models import Order, OrderExportJob


def _export_job_for_store(*, job_id: uuid.UUID, store_id: int) -> OrderExportJob:
    job = OrderExportJob.objects.filter(id=job_id, store_id=store_id).first()
    if job is None:
        raise Http404()
    return job


def _download_path(job_id: uuid.UUID) -> str:
    return f"admin/orders/export/{job_id}/download/"


def _order_export_download_filename(*, file_path: str, store_public_id: str, job: OrderExportJob) -> str:
    """Attachment name without job id: order_{public_id}_{YYYY-MM-DD}.csv."""
    base = (file_path or "").strip().rsplit("/", 1)[-1]
    prefix = f"order_{store_public_id}_"
    if base.startswith(prefix) and "__" in base and base.lower().endswith(".csv"):
        try:
            rest = base[len(prefix) :]
            date_part, _sep, _rest = rest.partition("__")
            if len(date_part) == 10 and date_part[4:5] == "-" and date_part[7:8] == "-":
                return f"order_{store_public_id}_{date_part}.csv"
        except (IndexError, ValueError):
            pass
    d = (job.updated_at or timezone.now()).date()
    return f"order_{store_public_id}_{d.isoformat()}.csv"


class OrderExportCreateView(ProvenTenantContextMixin, APIView):
    permission_classes = [IsAuthenticated, DenyAPIKeyAccess, IsStoreAdmin]

    def post(self, request, *args, **kwargs):
        ctx = get_active_store(request)
        if not ctx.store:
            return Response({"detail": "No active store."}, status=status.HTTP_403_FORBIDDEN)

        body = request.data if isinstance(request.data, dict) else {}
        select_all = bool(body.get("select_all"))
        raw_filters = body.get("filters")
        if raw_filters is not None and not isinstance(raw_filters, dict):
            return Response({"filters": "Must be an object."}, status=status.HTTP_400_BAD_REQUEST)
        order_ids = body.get("order_ids")
        if order_ids is not None and not isinstance(order_ids, list):
            return Response({"order_ids": "Must be a list."}, status=status.HTTP_400_BAD_REQUEST)

        if select_all:
            filters = normalize_export_filters(raw_filters or {})
            selected: list[str] | None = None
        else:
            filters = {}
            if not order_ids:
                return Response(
                    {"order_ids": "Required when select_all is false."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            selected = [str(x).strip() for x in order_ids if str(x).strip()]
            if not selected:
                return Response(
                    {"order_ids": "At least one order id is required."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            unique = set(selected)
            if len(unique) != len(selected):
                return Response(
                    {"order_ids": "Duplicate order ids are not allowed."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            found = Order.objects.filter(store=ctx.store, public_id__in=unique).values_list(
                "public_id", flat=True
            )
            found_set = set(found)
            if found_set != unique:
                return Response(
                    {"order_ids": "One or more orders are invalid or not in this store."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        job = OrderExportJob.objects.create(
            store=ctx.store,
            user=request.user,
            status=OrderExportJob.Status.PENDING,
            select_all=select_all,
            filters=filters,
            selected_order_ids=selected,
        )

        from .export_tasks import export_orders_csv

        export_orders_csv.delay(str(job.id))

        return Response(
            {"job_id": str(job.id), "status": job.status.upper()},
            status=status.HTTP_201_CREATED,
        )


class OrderExportStatusView(ProvenTenantContextMixin, APIView):
    permission_classes = [IsAuthenticated, DenyAPIKeyAccess, IsDashboardUser]

    def get(self, request, job_id, *args, **kwargs):
        ctx = get_active_store(request)
        if not ctx.store:
            return Response({"detail": "No active store."}, status=status.HTTP_403_FORBIDDEN)
        try:
            jid = uuid.UUID(str(job_id))
        except (ValueError, TypeError):
            raise Http404()
        job = _export_job_for_store(job_id=jid, store_id=ctx.store.id)

        download_url: str | None = None
        if job.status == OrderExportJob.Status.COMPLETED and job.expires_at:
            if timezone.now() <= job.expires_at:
                download_url = _download_path(job.id)

        payload: dict[str, Any] = {
            "status": job.status.upper(),
            "progress": job.progress,
            "download_url": download_url,
            "expires_at": job.expires_at.isoformat() if job.expires_at else None,
        }
        if job.status == OrderExportJob.Status.FAILED and job.error_message:
            payload["error_message"] = job.error_message[:500]
        return Response(payload)


class OrderExportDownloadView(ProvenTenantContextMixin, APIView):
    permission_classes = [IsAuthenticated, DenyAPIKeyAccess, IsDashboardUser]

    def get(self, request, job_id, *args, **kwargs):
        from django.core.files.storage import default_storage

        ctx = get_active_store(request)
        if not ctx.store:
            return Response({"detail": "No active store."}, status=status.HTTP_403_FORBIDDEN)
        try:
            jid = uuid.UUID(str(job_id))
        except (ValueError, TypeError):
            raise Http404()
        job = _export_job_for_store(job_id=jid, store_id=ctx.store.id)

        if job.status != OrderExportJob.Status.COMPLETED:
            return Response({"detail": "Export is not ready."}, status=status.HTTP_400_BAD_REQUEST)
        if not job.file_path or not job.expires_at or timezone.now() > job.expires_at:
            return Response({"detail": "Export has expired."}, status=status.HTTP_410_GONE)

        if not default_storage.exists(job.file_path):
            return Response({"detail": "File missing."}, status=status.HTTP_410_GONE)

        fh = default_storage.open(job.file_path, "rb")
        download_name = _order_export_download_filename(
            file_path=job.file_path, store_public_id=ctx.store.public_id, job=job
        )
        resp = FileResponse(
            fh,
            as_attachment=True,
            filename=download_name,
            content_type="text/csv; charset=utf-8",
        )
        return resp
