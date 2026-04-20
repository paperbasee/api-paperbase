from __future__ import annotations

from django.utils.deprecation import MiddlewareMixin

from engine.apps.stores.models import Store
from engine.apps.stores.store_activity import touch_store_activity
from engine.core.request_context import RequestContext, user_enters_platform_scope
from engine.core.tenancy import get_active_store
from engine.core.tenant_context import _clear_tenant_context, _set_tenant_context


class TenantContextMiddleware(MiddlewareMixin):
    """
    Resolve execution context once per request and mirror it to ContextVar for ORM code.
    """

    def process_request(self, request):
        if request.path in {"/health", "/health/"}:
            request.context = RequestContext(tenant=None, is_platform_admin=False)
            _set_tenant_context(store=None, is_platform_admin=False)
            return None
        if user_enters_platform_scope(request.user):
            request.context = RequestContext(tenant=None, is_platform_admin=True)
            _set_tenant_context(store=None, is_platform_admin=True)
        else:
            ctx = get_active_store(request)
            if getattr(request, "store", None) is None and ctx.store is not None:
                request.store = ctx.store
            user = getattr(request, "user", None)
            if (
                ctx.store
                and ctx.store.status == Store.Status.ACTIVE
                and user is not None
                and getattr(user, "is_authenticated", False)
            ):
                touch_store_activity(ctx.store)
            request.context = RequestContext(
                tenant=ctx.store,
                is_platform_admin=False,
            )
            _set_tenant_context(store=ctx.store, is_platform_admin=False)
        return None

    def process_response(self, request, response):
        _clear_tenant_context()
        return response

    def process_exception(self, request, exception):
        _clear_tenant_context()
        return None
