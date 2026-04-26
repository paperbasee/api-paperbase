import inngest
from config.inngest import inngest_client


@inngest_client.create_function(
    fn_id="purge-expired-trash",
    trigger=inngest.TriggerCron(cron="15 3 * * *"),  # daily 3:15am UTC
    retries=2,
)
def purge_expired_trash(ctx: inngest.ContextSync) -> str:
    from engine.core.tasks import purge_expired_trash_task
    result = purge_expired_trash_task()
    return f"purged: {result}"


@inngest_client.create_function(
    fn_id="cleanup-event-logs",
    trigger=inngest.TriggerCron(cron="30 3 * * *"),  # daily 3:30am UTC
    retries=2,
)
def cleanup_event_logs(ctx: inngest.ContextSync) -> str:
    from engine.apps.tracking.tasks import cleanup_old_event_logs
    result = cleanup_old_event_logs()
    return f"deleted: {result}"


@inngest_client.create_function(
    fn_id="cleanup-order-exports",
    trigger=inngest.TriggerCron(cron="0 */2 * * *"),  # every 2 hours
    retries=2,
)
def cleanup_order_exports(ctx: inngest.ContextSync) -> str:
    from engine.apps.orders.export_cleanup import cleanup_expired_order_exports
    result = cleanup_expired_order_exports()
    return f"cleaned: {result}"