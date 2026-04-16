from django.urls import path

from engine.apps.tracking.views import TrackingEventIngestView

urlpatterns = [
    path("event", TrackingEventIngestView.as_view(), name="tracking-event"),
]

