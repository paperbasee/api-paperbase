"""Django admin forms: dynamic extra fields aligned with dashboard StoreSettings.extra_field_schema."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from django import forms

from engine.apps.stores.models import Store

from .extra_schema import form_field_name_for_schema_item, get_product_extra_schema
from .models import Product


def build_product_extra_form_fields(schema: list[dict[str, Any]]) -> dict[str, forms.Field]:
    """
    Build Django form fields for product extra_field_schema items.

    Important: Django admin validates `fieldsets` against *form.base_fields* at form-class
    construction time. So these fields must be attachable to the form class (not only in __init__).
    """
    fields: dict[str, forms.Field] = {}
    for item in schema:
        fid = str(item.get("id") or item.get("name") or "")
        fname = form_field_name_for_schema_item(fid)
        label = (item.get("name") or "").strip() or "Field"
        required = bool(item.get("required"))
        field_type = (item.get("fieldType") or item.get("field_type") or "text").lower()
        options = item.get("options") if isinstance(item.get("options"), list) else []

        if field_type == "number":
            fields[fname] = forms.CharField(
                label=label,
                required=required,
                help_text="Numeric value (matches dashboard dynamic fields).",
            )
        elif field_type == "boolean":
            fields[fname] = forms.BooleanField(label=label, required=False)
        elif field_type == "dropdown" and options:
            choices = [("", "---------")] + [(str(o), str(o)) for o in options]
            fields[fname] = forms.ChoiceField(label=label, choices=choices, required=required)
        else:
            fields[fname] = forms.CharField(
                label=label,
                required=required,
                widget=forms.Textarea(attrs={"rows": 2}) if field_type == "text" else forms.TextInput(),
            )
    return fields


class ProductAdminForm(forms.ModelForm):
    """
    Adds one form field per product extra_field_schema row (same keys in extra_data as dashboard).
    """

    class Meta:
        model = Product
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._extra_schema: list[dict[str, Any]] = []
        self._resolve_store_and_build_extra_fields()

    def _resolve_store_and_build_extra_fields(self) -> None:
        store: Store | None = None
        if self.instance.pk and self.instance.store_id:
            store = self.instance.store
        elif self.data and self.data.get("store"):
            try:
                store = Store.objects.get(pk=self.data["store"])
            except (Store.DoesNotExist, ValueError, TypeError):
                store = None

        if not store:
            return

        self._extra_schema = get_product_extra_schema(store)
        extra_data = self.instance.extra_data if isinstance(self.instance.extra_data, dict) else {}

        for item in self._extra_schema:
            fid = str(item.get("id") or item.get("name") or "")
            fname = form_field_name_for_schema_item(fid)
            label = (item.get("name") or "").strip() or "Field"
            required = bool(item.get("required"))
            field_type = (item.get("fieldType") or item.get("field_type") or "text").lower()
            default_raw = item.get("defaultValue") or item.get("default_value")
            options = item.get("options") if isinstance(item.get("options"), list) else []

            initial = extra_data.get(label)
            if initial is None and default_raw is not None and default_raw != "":
                initial = default_raw

            # Field might already be attached to the form class by ProductAdmin.get_form().
            if fname not in self.fields:
                self.fields.update(build_product_extra_form_fields([item]))

            if field_type == "number":
                self.fields[fname].initial = "" if initial in (None, "") else str(initial)
            elif field_type == "boolean":
                self.fields[fname].initial = bool(initial) if initial is not None else False
            elif field_type == "dropdown" and options:
                if initial is not None and str(initial) in [str(o) for o in options]:
                    self.fields[fname].initial = str(initial)
            else:
                if initial is not None:
                    self.fields[fname].initial = initial if isinstance(initial, str) else str(initial)

    def clean(self):
        cleaned = super().clean()
        for item in self._extra_schema:
            fid = str(item.get("id") or item.get("name") or "")
            fname = form_field_name_for_schema_item(fid)
            label = (item.get("name") or "").strip()
            field_type = (item.get("fieldType") or item.get("field_type") or "text").lower()
            if fname not in self.fields:
                continue
            raw = cleaned.get(fname)
            if field_type == "number" and raw not in (None, ""):
                try:
                    cleaned[fname] = Decimal(str(raw))
                except (InvalidOperation, TypeError, ValueError):
                    self.add_error(fname, "Enter a valid number.")
            elif field_type == "number" and raw in ("", None):
                cleaned[fname] = None if not item.get("required") else raw
        return cleaned

    def save(self, commit=True):
        previous_extra: dict[str, Any] = (
            dict(self.instance.extra_data)
            if self.instance.pk and isinstance(self.instance.extra_data, dict)
            else {}
        )
        instance = super().save(commit=False)
        merged: dict[str, Any] = dict(previous_extra)

        for item in self._extra_schema:
            fid = str(item.get("id") or item.get("name") or "")
            fname = form_field_name_for_schema_item(fid)
            label = (item.get("name") or "").strip()
            if not label or fname not in self.fields or fname not in self.cleaned_data:
                continue
            val = self.cleaned_data[fname]
            field_type = (item.get("fieldType") or item.get("field_type") or "text").lower()

            if field_type == "number":
                if val is None or val == "":
                    if not item.get("required"):
                        merged.pop(label, None)
                else:
                    num = float(val) if isinstance(val, Decimal) else float(val)
                    merged[label] = int(num) if num == int(num) else num
            elif field_type == "boolean":
                merged[label] = bool(val)
            elif field_type == "dropdown":
                if val in (None, ""):
                    if not item.get("required"):
                        merged.pop(label, None)
                else:
                    merged[label] = str(val)
            else:
                if val in (None, "") and not item.get("required"):
                    merged.pop(label, None)
                else:
                    merged[label] = str(val) if val is not None else ""

        instance.extra_data = merged
        if commit:
            instance.save()
        return instance
