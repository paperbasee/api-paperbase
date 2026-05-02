from django.core.cache import cache
from django.core.management.base import BaseCommand

from engine.apps.theming.cache import PRESETS_CACHE_KEY, invalidate_theme_cache
from engine.apps.theming.models import StorefrontTheme


class Command(BaseCommand):
    help = "Flush all storefront theme caches (and presets list cache)"

    def handle(self, *args, **options):
        themes = StorefrontTheme.objects.select_related("store")
        count = 0
        for theme in themes.iterator():
            invalidate_theme_cache(theme.store.public_id)
            count += 1
        try:
            cache.delete("theme:presets")  # legacy key (pre palette rename)
            cache.delete(PRESETS_CACHE_KEY)
        except Exception:
            self.stdout.write(self.style.WARNING("Presets cache delete failed (non-fatal)"))
        self.stdout.write(self.style.SUCCESS(f"Flushed {count} theme cache(s)"))
