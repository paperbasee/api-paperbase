import os

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from qstash import Receiver

def _verify_qstash(request) -> bool:
    signature = (request.headers.get("Upstash-Signature") or "").strip()
    current_key = (os.getenv("QSTASH_CURRENT_SIGNING_KEY") or "").strip()
    next_key = (os.getenv("QSTASH_NEXT_SIGNING_KEY") or "").strip()

    if not signature or not current_key:
        return False

    receiver = Receiver(
        current_signing_key=current_key,
        next_signing_key=next_key,
    )
    try:
        receiver.verify(
            body=request.body.decode("utf-8") if request.body else "",
            signature=signature,
            url=request.build_absolute_uri(request.path),
        )
    except Exception:
        return False
    return True


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