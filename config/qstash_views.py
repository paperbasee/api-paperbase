import hashlib
import hmac
import os

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

QSTASH_CURRENT_SIGNING_KEY = os.getenv("QSTASH_CURRENT_SIGNING_KEY", "")
QSTASH_NEXT_SIGNING_KEY = os.getenv("QSTASH_NEXT_SIGNING_KEY", "")


def _verify_qstash(request) -> bool:
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    return token in (QSTASH_CURRENT_SIGNING_KEY, QSTASH_NEXT_SIGNING_KEY)


@csrf_exempt
@require_POST
def qstash_inventory_sync(request):
    if not _verify_qstash(request):
        return JsonResponse({"error": "unauthorized"}, status=401)
    from engine.apps.inventory.tasks import schedule_product_stock_cache_all_stores
    schedule_product_stock_cache_all_stores.delay()
    return JsonResponse({"status": "queued"})


@csrf_exempt
@require_POST
def qstash_base_backup(request):
    if not _verify_qstash(request):
        return JsonResponse({"error": "unauthorized"}, status=401)
    from engine.apps.backup.tasks import run_base_backup
    run_base_backup.apply_async(queue="backup")
    return JsonResponse({"status": "queued"})