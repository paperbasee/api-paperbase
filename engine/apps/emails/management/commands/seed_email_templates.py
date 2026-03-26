from django.core.management.base import BaseCommand

from engine.apps.emails.models import EmailTemplate
from engine.apps.emails.template_catalog import DEFAULT_EMAIL_TEMPLATES


class Command(BaseCommand):
    help = "Create or update default transactional EmailTemplate rows."

    def add_arguments(self, parser):
        parser.add_argument(
            "--update-existing",
            action="store_true",
            help="Update subject/body/is_active for existing template types.",
        )

    def handle(self, *args, **options):
        update_existing = options.get("update_existing", False)
        created = 0
        updated = 0
        skipped = 0

        for template_type, payload in DEFAULT_EMAIL_TEMPLATES.items():
            defaults = {
                "subject": payload["subject"],
                "html_body": payload["html_body"],
                "text_body": payload["text_body"],
                "is_active": True,
            }
            obj, was_created = EmailTemplate.objects.get_or_create(
                type=template_type,
                defaults=defaults,
            )
            if was_created:
                created += 1
                self.stdout.write(self.style.SUCCESS(f"Created template: {template_type}"))
                continue

            if not update_existing:
                skipped += 1
                self.stdout.write(f"Skipped existing template: {template_type}")
                continue

            obj.subject = defaults["subject"]
            obj.html_body = defaults["html_body"]
            obj.text_body = defaults["text_body"]
            obj.is_active = True
            obj.save(update_fields=["subject", "html_body", "text_body", "is_active", "updated_at"])
            updated += 1
            self.stdout.write(self.style.WARNING(f"Updated template: {template_type}"))

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Created={created}, Updated={updated}, Skipped={skipped}"
            )
        )

