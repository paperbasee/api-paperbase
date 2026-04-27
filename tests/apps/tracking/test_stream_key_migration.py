"""Regression guards: no legacy CAPI Redis stream key or consumer group names."""

from __future__ import annotations

from pathlib import Path

from django.test import SimpleTestCase

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _tracking_py_files() -> list[Path]:
    root = _PROJECT_ROOT / "engine" / "apps" / "tracking"
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


class StreamKeyMigrationGuardsTests(SimpleTestCase):
    def test_no_legacy_stream_prefix_in_tracking_sources(self):
        bad = "capi:stream:"
        for path in _tracking_py_files():
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(
                bad,
                text,
                msg=f"{path.relative_to(_PROJECT_ROOT)} must not reference legacy prefix {bad!r}",
            )

    def test_no_legacy_consumer_group_in_tracking_sources(self):
        bad = "capi-workers"
        for path in _tracking_py_files():
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(
                bad,
                text,
                msg=f"{path.relative_to(_PROJECT_ROOT)} must not reference legacy group {bad!r}",
            )

    def test_buffer_module_defines_split_pipeline_constants(self):
        buf = _PROJECT_ROOT / "engine" / "apps" / "tracking" / "buffer.py"
        text = buf.read_text(encoding="utf-8")
        for needle in (
            "capi:meta:stream:",
            "capi:tiktok:stream:",
            "capi:meta:active_stores",
            "capi:tiktok:active_stores",
            "capi-meta-workers",
            "capi-tiktok-workers",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)
