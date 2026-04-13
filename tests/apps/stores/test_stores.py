import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from engine.apps.basic_analytics.models import StoreDashboardStatsSnapshot
from engine.apps.billing.models import Plan
from engine.apps.billing.services import activate_subscription
from engine.apps.customers.models import Customer
from engine.apps.orders.models import Order
from engine.apps.shipping.models import ShippingZone
from engine.apps.inventory.models import Inventory
from engine.apps.products.models import Category, Product
from engine.apps.stores.models import (
    Store,
    StoreDeletionJob,
    StoreLifecycleAuditLog,
    StoreMembership,
    StoreSettings,
)
from engine.apps.stores.tasks import hard_delete_store
from engine.apps.stores.services import allocate_unique_store_code, normalize_store_code_base_from_name
from engine.core.tenant_execution import tenant_scope_from_store


User = get_user_model()


def _make_user(email: str, password: str = "pass1234"):
    return User.objects.create_user(email=email, password=password, is_verified=True)


def _auth_client(client: APIClient, email: str, password: str = "pass1234", store_public_id: str | None = None):
    extra = {}
    if store_public_id:
        extra["HTTP_X_STORE_PUBLIC_ID"] = store_public_id
    resp = client.post(
        "/api/v1/auth/token/",
        {"email": email, "password": password},
        format="json",
        **extra,
    )
    assert resp.status_code == 200
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access']}")
    return resp.data["access"]


def _set_default_plan(*, premium_order_emails: bool = False):
    Plan.objects.all().update(is_default=False)
    plan_name = "premium" if premium_order_emails else "basic"

    plan = Plan.objects.filter(name=plan_name).first()
    if not plan:
        plan = Plan.objects.create(
            name=plan_name,
            price="0.00",
            billing_cycle="monthly",
            is_active=True,
            is_default=True,
            features={
                "limits": {"max_products": 100},
                "features": {"order_email_notifications": premium_order_emails},
            },
        )
    else:
        features = plan.features or {}
        features["limits"] = {**(features.get("limits") or {}), "max_products": 500 if premium_order_emails else 100}
        features["features"] = {
            **(features.get("features") or {}),
            "order_email_notifications": premium_order_emails,
        }
        plan.features = features
        plan.is_default = True
        plan.save(update_fields=["features", "is_default"])


def _make_store(name: str, domain: str, owner_email: str):
    base = normalize_store_code_base_from_name(name) or normalize_store_code_base_from_name(
        domain.split(".")[0]
    )
    if not base:
        base = "T"
    owner = User.objects.get(email=owner_email)
    store = Store.objects.create(
        owner=owner,
        name=name,
        code=allocate_unique_store_code(base),
        owner_name=f"{name} Owner",
        owner_email=owner_email,
    )
    StoreMembership.objects.get_or_create(
        user=owner,
        store=store,
        defaults={
            "role": StoreMembership.Role.OWNER,
            "is_active": True,
        },
    )
    return store


def _make_owner_membership(user: User, store: Store):
    return StoreMembership.objects.create(
        user=user,
        store=store,
        role=StoreMembership.Role.OWNER,
        is_active=True,
    )


def _make_catalog_data(store: Store, user: User):
    with tenant_scope_from_store(store=store, reason="test fixture"):
        cat = Category.objects.create(
            store=store,
            name="Electronics",
            slug="",
        )
        product = Product.objects.create(
            store=store,
            category=cat,
            name="Product Alpha",
            price=10,
            stock=5,
            status=Product.Status.ACTIVE,
            is_active=True,
        )
        Inventory.objects.get_or_create(
            product=product,
            variant=None,
            defaults={"quantity": 5},
        )
        zone = ShippingZone.objects.create(store=store, name="Store Zone", is_active=True)
        order = Order.objects.create(store=store, email="cust@example.com", shipping_zone=zone)
        customer = Customer.objects.create(store=store, user=user)

    today = datetime.date.today()
    StoreDashboardStatsSnapshot.objects.create(
        store=store,
        start_date=today,
        end_date=today,
        bucket=StoreDashboardStatsSnapshot.BUCKET_DAY,
        payload={},
    )
    return {"product": product, "order": order, "customer": customer}


class DeleteStoreEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_delete_legacy_endpoint_returns_410(self):
        _set_default_plan(premium_order_emails=False)
        user = _make_user("owner@legacy.example.com")
        store = _make_store("Legacy Store", "legacy.local", owner_email=user.email)
        _auth_client(self.client, user.email, store_public_id=store.public_id)
        resp = self.client.post(
            "/api/v1/store/settings/delete/",
            {"account_email": user.email, "store_name": store.name},
            format="json",
        )
        self.assertEqual(resp.status_code, 410)
        self.assertEqual(resp.data.get("code"), "delete_requires_otp")

    @patch("engine.apps.stores.store_lifecycle._generate_otp_code", return_value="445566")
    def test_delete_store_otp_flow_schedules_deletion(self, _mock_otp):
        _set_default_plan(premium_order_emails=False)

        user = _make_user("owner@example.com")
        other_user = _make_user("other@example.com")

        store = _make_store("Store A", "store-a.local", owner_email=user.email)
        store.contact_email = user.email
        store.save(update_fields=["contact_email"])
        _make_owner_membership(other_user, store)

        _make_catalog_data(store, user)

        _auth_client(self.client, user.email, store_public_id=store.public_id)

        # Wrong email (exact match should fail)
        resp = self.client.post(
            "/api/v1/store/settings/delete/send-otp/",
            {
                "account_email": "owner@example.com ",
                "store_name": store.name,
                "confirmation_phrase": "delete my store",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Store.objects.filter(id=store.id).exists())
        store.refresh_from_db()
        self.assertTrue(store.is_active)

        send = self.client.post(
            "/api/v1/store/settings/delete/send-otp/",
            {
                "account_email": user.email,
                "store_name": store.name,
                "confirmation_phrase": "delete my store",
            },
            format="json",
        )
        self.assertEqual(send.status_code, 200)
        self.assertIn("challenge_public_id", send.data)
        self.assertIn("expires_at", send.data)
        self.assertTrue(
            StoreLifecycleAuditLog.objects.filter(
                store=store,
                action=StoreLifecycleAuditLog.Action.STORE_DELETE_OTP_SENT,
            ).exists()
        )

        bad_otp = self.client.post(
            "/api/v1/store/settings/delete/confirm/",
            {"challenge_public_id": send.data["challenge_public_id"], "otp": "000000"},
            format="json",
        )
        self.assertEqual(bad_otp.status_code, 400)

        resp = self.client.post(
            "/api/v1/store/settings/delete/confirm/",
            {"challenge_public_id": send.data["challenge_public_id"], "otp": "445566"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["redirect_route"], "/recover")
        self.assertIn("scheduled_delete_at", resp.data)
        self.assertTrue(
            StoreLifecycleAuditLog.objects.filter(
                store=store,
                action=StoreLifecycleAuditLog.Action.STORE_DELETE_SCHEDULED,
            ).exists()
        )

        job_id = resp.data["job_id"]
        job = StoreDeletionJob.objects.get(public_id=job_id, user=user)

        store.refresh_from_db()
        self.assertEqual(store.status, Store.Status.PENDING_DELETE)
        self.assertFalse(store.is_active)

        # Hard deletion must not occur before delete_at in production semantics.
        # Simulate the worker running after the scheduled time.
        self.assertIsNotNone(store.delete_at)
        with patch(
            "engine.apps.stores.tasks.timezone.now",
            return_value=store.delete_at + datetime.timedelta(seconds=1),
        ):
            hard_delete_store(job.public_id)
        job.refresh_from_db()
        self.assertEqual(job.status, StoreDeletionJob.Status.SUCCESS)
        self.assertFalse(Store.objects.filter(id=store.id).exists())
        self.assertEqual(Order.objects.filter(store_id=store.id).count(), 0)
        self.assertEqual(Customer.objects.filter(store_id=store.id).count(), 0)
        self.assertEqual(StoreDashboardStatsSnapshot.objects.filter(store_id=store.id).count(), 0)

    @patch("engine.apps.stores.store_lifecycle._generate_otp_code", return_value="112233")
    def test_hard_delete_store_enforces_delete_at_unless_forced(self, _mock_otp):
        _set_default_plan(premium_order_emails=False)

        user = _make_user("owner-strict@example.com")
        store = _make_store("Strict Store", "strict.local", owner_email=user.email)
        _make_catalog_data(store, user)
        _auth_client(self.client, user.email, store_public_id=store.public_id)

        send = self.client.post(
            "/api/v1/store/settings/delete/send-otp/",
            {
                "account_email": user.email,
                "store_name": store.name,
                "confirmation_phrase": "delete my store",
            },
            format="json",
        )
        self.assertEqual(send.status_code, 200)

        resp = self.client.post(
            "/api/v1/store/settings/delete/confirm/",
            {"challenge_public_id": send.data["challenge_public_id"], "otp": "112233"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        job = StoreDeletionJob.objects.get(public_id=resp.data["job_id"], user=user)
        store.refresh_from_db()
        self.assertIsNotNone(store.delete_at)

        # Before delete_at: must refuse deletion.
        with patch(
            "engine.apps.stores.tasks.timezone.now",
            return_value=store.delete_at - datetime.timedelta(seconds=1),
        ):
            hard_delete_store(job.public_id)
        job.refresh_from_db()
        self.assertEqual(job.status, StoreDeletionJob.Status.SKIPPED_NOT_DUE)
        self.assertTrue(Store.objects.filter(id=store.id).exists())

        # Forced deletion bypasses delete_at but must still work through the job.
        hard_delete_store(job.public_id, force=True, reason="test")
        job.refresh_from_db()
        self.assertEqual(job.status, StoreDeletionJob.Status.SUCCESS)
        self.assertFalse(Store.objects.filter(id=store.id).exists())

    def test_delete_status_is_user_scoped(self):
        _set_default_plan(premium_order_emails=False)

        user = _make_user("owner@example.com")
        other_user = _make_user("other@example.com")

        store = _make_store("Store A", "store-a.local", owner_email=user.email)

        _make_catalog_data(store, user)
        _auth_client(self.client, user.email, store_public_id=store.public_id)

        with patch("engine.apps.stores.store_lifecycle._generate_otp_code", return_value="778899"):
            send = self.client.post(
                "/api/v1/store/settings/delete/send-otp/",
                {
                    "account_email": user.email,
                    "store_name": store.name,
                    "confirmation_phrase": "delete my store",
                },
                format="json",
            )
        self.assertEqual(send.status_code, 200)

        with patch("engine.apps.stores.store_lifecycle._generate_otp_code", return_value="778899"):
            resp = self.client.post(
                "/api/v1/store/settings/delete/confirm/",
                {"challenge_public_id": send.data["challenge_public_id"], "otp": "778899"},
                format="json",
            )
        self.assertEqual(resp.status_code, 200)
        job_id = resp.data["job_id"]

        other_client = APIClient()
        _auth_client(other_client, other_user.email, store_public_id=store.public_id)

        status_resp = other_client.get(
            "/api/v1/store/settings/delete-status/?job_id=" + str(job_id)
        )
        self.assertEqual(status_resp.status_code, 404)

    def test_delete_send_otp_blocks_without_contact_email(self):
        _set_default_plan(premium_order_emails=False)
        user = _make_user("missing-contact@example.com")
        store = _make_store("No Contact Store", "no-contact.local", owner_email=user.email)
        store.contact_email = ""
        store.save(update_fields=["contact_email"])
        _auth_client(self.client, user.email, store_public_id=store.public_id)

        with patch("engine.apps.stores.store_lifecycle._generate_otp_code", return_value="112233"):
            resp = self.client.post(
                "/api/v1/store/settings/delete/send-otp/",
                {
                    "account_email": user.email,
                    "store_name": store.name,
                    "confirmation_phrase": "delete my store",
                },
                format="json",
            )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data.get("detail"), "Add a store email before deleting your store.")

    def test_hard_delete_task_blocks_without_contact_email(self):
        _set_default_plan(premium_order_emails=False)
        user = _make_user("hard-delete-missing-contact@example.com")
        store = _make_store("Hard Delete Store", "hard-delete.local", owner_email=user.email)
        store.contact_email = ""
        store.status = Store.Status.INACTIVE
        store.save(update_fields=["contact_email", "status"])

        job = StoreDeletionJob.objects.create(
            user=user,
            store_public_id_snapshot=store.public_id,
            store_id_snapshot=store.id,
            delete_at_snapshot=store.delete_at,
            lifecycle_version_snapshot=store.lifecycle_version,
            status=StoreDeletionJob.Status.PENDING,
            current_step=StoreDeletionJob.STEP_REMOVING_ORDERS,
        )

        hard_delete_store(job.public_id)
        job.refresh_from_db()
        self.assertEqual(job.status, StoreDeletionJob.Status.FAILED)
        self.assertEqual(job.error_message, "Add a store email before deleting your store.")
        self.assertTrue(Store.objects.filter(id=store.id).exists())

    def test_store_delete_http_method_not_allowed(self):
        _set_default_plan(premium_order_emails=False)
        user = _make_user("delete-method@example.com")
        store = _make_store("Delete Method Store", "delete-method.local", owner_email=user.email)
        store.contact_email = user.email
        store.save(update_fields=["contact_email"])
        _auth_client(self.client, user.email, store_public_id=store.public_id)

        resp = self.client.delete(f"/api/v1/store/{store.public_id}/", format="json")
        self.assertEqual(resp.status_code, 405)


class RemoveStoreEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_remove_requires_json_body(self):
        _set_default_plan(premium_order_emails=False)
        user = _make_user("remove@example.com")
        store = _make_store("Rem Store", "rem.local", owner_email=user.email)
        store.contact_email = user.email
        store.save(update_fields=["contact_email"])
        _auth_client(self.client, user.email, store_public_id=store.public_id)
        resp = self.client.post("/api/v1/store/remove/", {}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_remove_rejects_bad_confirmation(self):
        _set_default_plan(premium_order_emails=False)
        user = _make_user("remove2@example.com")
        store = _make_store("Rem Store 2", "rem2.local", owner_email=user.email)
        store.contact_email = user.email
        store.save(update_fields=["contact_email"])
        _auth_client(self.client, user.email, store_public_id=store.public_id)
        resp = self.client.post(
            "/api/v1/store/remove/",
            {
                "store_name": store.name,
                "confirmation_phrase": "remove my shop",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_remove_success(self):
        _set_default_plan(premium_order_emails=False)
        user = _make_user("remove3@example.com")
        store = _make_store("Rem Store 3", "rem3.local", owner_email=user.email)
        store.contact_email = user.email
        store.save(update_fields=["contact_email"])
        _auth_client(self.client, user.email, store_public_id=store.public_id)
        resp = self.client.post(
            "/api/v1/store/remove/",
            {
                "store_name": store.name,
                "confirmation_phrase": "remove my store",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["redirect_route"], "/recover")
        store.refresh_from_db()
        self.assertEqual(store.status, Store.Status.INACTIVE)
        self.assertTrue(
            StoreLifecycleAuditLog.objects.filter(
                store=store,
                action=StoreLifecycleAuditLog.Action.STORE_REMOVE,
            ).exists()
        )


class StoreSettingsOrderEmailTests(TestCase):
    """Order email notification flags: premium + owner-only writes."""

    def setUp(self):
        self.client = APIClient()
        _set_default_plan(premium_order_emails=True)
        self.owner = _make_user("owner@order-email.test")
        self.staff_user = _make_user("staff@order-email.test")
        self.store = _make_store("Order Email Store", "order-email.test", self.owner.email)
        StoreMembership.objects.create(
            user=self.staff_user,
            store=self.store,
            role=StoreMembership.Role.STAFF,
            is_active=True,
        )
        StoreSettings.objects.get_or_create(store=self.store)

    def test_staff_cannot_patch_order_email_flags(self):
        premium = Plan.objects.filter(name="premium").first()
        self.assertIsNotNone(premium)
        activate_subscription(self.owner, premium, source="manual", amount=0, provider="manual")
        _auth_client(self.client, self.staff_user.email, store_public_id=self.store.public_id)
        resp = self.client.patch(
            "/api/v1/store/settings/current/",
            {"email_notify_owner_on_order_received": True},
            format="json",
            HTTP_X_STORE_PUBLIC_ID=self.store.public_id,
        )
        self.assertEqual(resp.status_code, 400)

    def test_owner_basic_cannot_enable_order_email_flags(self):
        basic = Plan.objects.filter(name="basic").first()
        if not basic:
            basic = Plan.objects.create(
                name="basic",
                price="0.00",
                billing_cycle="monthly",
                is_active=True,
                is_default=True,
                features={"limits": {"max_products": 100}, "features": {}},
            )
        activate_subscription(self.owner, basic, source="manual", amount=0, provider="manual")
        _auth_client(self.client, self.owner.email, store_public_id=self.store.public_id)
        resp = self.client.patch(
            "/api/v1/store/settings/current/",
            {"email_notify_owner_on_order_received": True},
            format="json",
            HTTP_X_STORE_PUBLIC_ID=self.store.public_id,
        )
        self.assertEqual(resp.status_code, 400)

    def test_owner_premium_can_toggle_order_email_flags(self):
        premium = Plan.objects.filter(name="premium").first()
        self.assertIsNotNone(premium)
        activate_subscription(self.owner, premium, source="manual", amount=0, provider="manual")
        _auth_client(self.client, self.owner.email, store_public_id=self.store.public_id)
        resp = self.client.patch(
            "/api/v1/store/settings/current/",
            {
                "email_notify_owner_on_order_received": True,
                "email_customer_on_order_confirmed": True,
            },
            format="json",
            HTTP_X_STORE_PUBLIC_ID=self.store.public_id,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["email_notify_owner_on_order_received"])
        self.assertTrue(resp.data["email_customer_on_order_confirmed"])


class LeaseRecoveryTests(TestCase):
    """Execution lease: stale RUNNING jobs are recovered via lease expiry."""

    def setUp(self):
        _set_default_plan(premium_order_emails=False)
        self.user = _make_user("lease@example.com")
        self.store = _make_store("Lease Store", "lease.local", owner_email=self.user.email)
        _make_catalog_data(self.store, self.user)

    def test_expired_lease_transitions_to_failed(self):
        job = StoreDeletionJob.objects.create(
            user=self.user,
            store_public_id_snapshot=self.store.public_id,
            store_id_snapshot=self.store.id,
            delete_at_snapshot=None,
            lifecycle_version_snapshot=self.store.lifecycle_version,
            status=StoreDeletionJob.Status.RUNNING,
            started_at=datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc),
        )

        hard_delete_store(job.public_id)

        job.refresh_from_db()
        self.assertEqual(job.status, StoreDeletionJob.Status.FAILED)
        self.assertIn("Lease expired", job.error_message)
        self.assertIsNone(job.started_at)
        self.assertEqual(job.celery_task_id, "")

        self.assertTrue(
            StoreLifecycleAuditLog.objects.filter(
                store_public_id=self.store.public_id,
                action="STORE_DELETE_FAILED",
                metadata__reason="lease_expired",
            ).exists()
        )

    def test_valid_lease_blocks_duplicate_execution(self):
        from django.utils import timezone as tz

        now = tz.now()
        job = StoreDeletionJob.objects.create(
            user=self.user,
            store_public_id_snapshot=self.store.public_id,
            store_id_snapshot=self.store.id,
            delete_at_snapshot=None,
            lifecycle_version_snapshot=self.store.lifecycle_version,
            status=StoreDeletionJob.Status.RUNNING,
            started_at=now,
        )

        hard_delete_store(job.public_id)

        job.refresh_from_db()
        self.assertEqual(job.status, StoreDeletionJob.Status.RUNNING)
        self.assertTrue(Store.objects.filter(id=self.store.id).exists())

    def test_running_without_started_at_treated_as_stale(self):
        job = StoreDeletionJob.objects.create(
            user=self.user,
            store_public_id_snapshot=self.store.public_id,
            store_id_snapshot=self.store.id,
            delete_at_snapshot=None,
            lifecycle_version_snapshot=self.store.lifecycle_version,
            status=StoreDeletionJob.Status.RUNNING,
            started_at=None,
        )

        hard_delete_store(job.public_id)

        job.refresh_from_db()
        self.assertEqual(job.status, StoreDeletionJob.Status.FAILED)
        self.assertIn("Lease expired", job.error_message)


class SchedulerReenqueueTests(TestCase):
    """Scheduler can re-dispatch SKIPPED_NOT_DUE / FAILED jobs after celery_task_id is cleared."""

    def setUp(self):
        _set_default_plan(premium_order_emails=False)
        self.user = _make_user("sched@example.com")
        self.store = _make_store("Sched Store", "sched.local", owner_email=self.user.email)
        _make_catalog_data(self.store, self.user)

    @patch("engine.apps.stores.store_lifecycle._generate_otp_code", return_value="998877")
    def test_skipped_not_due_clears_celery_task_id(self, _mock_otp):
        client = APIClient()
        _auth_client(client, self.user.email, store_public_id=self.store.public_id)

        send = client.post(
            "/api/v1/store/settings/delete/send-otp/",
            {
                "account_email": self.user.email,
                "store_name": self.store.name,
                "confirmation_phrase": "delete my store",
            },
            format="json",
        )
        resp = client.post(
            "/api/v1/store/settings/delete/confirm/",
            {"challenge_public_id": send.data["challenge_public_id"], "otp": "998877"},
            format="json",
        )
        job = StoreDeletionJob.objects.get(public_id=resp.data["job_id"])
        self.store.refresh_from_db()

        with patch(
            "engine.apps.stores.tasks.timezone.now",
            return_value=self.store.delete_at - datetime.timedelta(seconds=1),
        ):
            hard_delete_store(job.public_id)

        job.refresh_from_db()
        self.assertEqual(job.status, StoreDeletionJob.Status.SKIPPED_NOT_DUE)
        self.assertEqual(job.celery_task_id, "")

    def test_failed_clears_celery_task_id(self):
        self.store.status = Store.Status.PENDING_DELETE
        self.store.delete_at = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
        self.store.save(update_fields=["status", "delete_at"])

        job = StoreDeletionJob.objects.create(
            user=self.user,
            store_public_id_snapshot=self.store.public_id,
            store_id_snapshot=self.store.id,
            delete_at_snapshot=self.store.delete_at,
            lifecycle_version_snapshot=self.store.lifecycle_version + 999,
            celery_task_id="old-celery-id",
        )

        hard_delete_store(job.public_id)

        job.refresh_from_db()
        self.assertEqual(job.status, StoreDeletionJob.Status.FAILED)
        self.assertEqual(job.celery_task_id, "")


class MissingStoreAuditTests(TestCase):
    """Missing store during execution writes STORE_DELETE_ALREADY_MISSING, not SUCCESS."""

    def setUp(self):
        _set_default_plan(premium_order_emails=False)
        self.user = _make_user("missing@example.com")

    def test_missing_store_writes_already_missing_audit(self):
        job = StoreDeletionJob.objects.create(
            user=self.user,
            store_public_id_snapshot="str_nonexistent",
            store_id_snapshot=999999,
            delete_at_snapshot=None,
            lifecycle_version_snapshot=0,
        )

        hard_delete_store(job.public_id)

        job.refresh_from_db()
        self.assertEqual(job.status, StoreDeletionJob.Status.SUCCESS)

        audit = StoreLifecycleAuditLog.objects.filter(
            store_public_id="str_nonexistent",
            action="STORE_DELETE_ALREADY_MISSING",
        ).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.metadata["reason"], "store not found during execution")
        self.assertEqual(audit.metadata["store_public_id"], "str_nonexistent")
        self.assertEqual(audit.metadata["job_public_id"], job.public_id)

        self.assertFalse(
            StoreLifecycleAuditLog.objects.filter(
                store_public_id="str_nonexistent",
                action="STORE_DELETE_SUCCESS",
            ).exists()
        )

