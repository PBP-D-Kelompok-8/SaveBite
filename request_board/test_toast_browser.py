"""Optional browser regression: pip install playwright, then playwright install chromium.

Run with python manage.py test request_board.test_toast_browser.
Set PLAYWRIGHT_CHROMIUM_CHANNEL=msedge to use an installed Microsoft Edge.
"""
import os
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.contrib.messages.storage.base import Message
from django.template.loader import render_to_string
from django.test import SimpleTestCase

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@skipUnless(sync_playwright, "Optional Playwright browser tooling is not installed")
class ToastBrowserTests(SimpleTestCase):
    def test_notifications_float_pause_and_close_individually_with_keyboard(self):
        html = render_to_string("request_board/_messages.html", {
            "messages": [Message(25, "Permintaan berhasil dibuat."),
                         Message(40, "Periksa kembali isian.")],
        })
        css = (Path(settings.BASE_DIR) / "static/css/request-board.css").read_text()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                channel=os.environ.get("PLAYWRIGHT_CHROMIUM_CHANNEL") or None,
            )
            try:
                for width in (1280, 375):
                    with self.subTest(width=width):
                        page = browser.new_page(viewport={"width": width, "height": 800})
                        page.set_content(
                            '<style>body {margin: 0} :root {--accent-dark-green: #23361a}</style>'
                            f'<style>{css}</style><main class="request-board">{html}'
                            '<button id="content-action">Aksi halaman</button></main>'
                        )
                        region = page.get_by_role("region", name="Notifikasi")
                        self.assertEqual(region.evaluate("el => getComputedStyle(el).position"), "fixed")
                        bounds = region.bounding_box()
                        self.assertGreaterEqual(bounds["x"], 0)
                        self.assertLessEqual(bounds["x"] + bounds["width"], width)
                        self.assertLessEqual(bounds["y"], 32)
                        self.assertNotEqual(page.locator(":focus").count(), 1,
                                            "Notifikasi tidak boleh merebut fokus saat muncul")
                        # Reading a focused success toast pauses its 4-second expiry.
                        page.get_by_role("button", name="Tutup notifikasi").first.focus()
                        page.wait_for_timeout(5500)
                        close = page.get_by_role("button", name="Tutup notifikasi")
                        self.assertEqual(close.count(), 2)
                        before = page.locator("#content-action").bounding_box()
                        close.first.focus()
                        page.keyboard.press("Enter")
                        self.assertEqual(close.count(), 1)
                        self.assertTrue(page.get_by_role("alert").is_visible())
                        self.assertTrue(close.first.evaluate("el => el === document.activeElement"))
                        close.first.click()
                        self.assertEqual(region.count(), 0)
                        self.assertEqual(page.locator("#content-action").bounding_box(), before)
                        page.close()
            finally:
                browser.close()

    def test_figma_expiry_and_keyboard_reading_pause(self):
        html = render_to_string("request_board/_messages.html", {
            "messages": [Message(25, "Berhasil."), Message(30, "Peringatan."), Message(40, "Kesalahan.")],
        })
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                channel=os.environ.get("PLAYWRIGHT_CHROMIUM_CHANNEL") or None,
            )
            try:
                page = browser.new_page()
                page.clock.install(time="2030-01-01T00:00:00")
                page.clock.pause_at("2030-01-01T00:00:01")
                document = '<main>' + html + '<button id="next">Lanjut</button></main>'
                page.set_content(document)
                page.clock.fast_forward(3999)
                self.assertEqual(page.locator('.request-board__toast').count(), 3)
                page.clock.fast_forward(1)
                self.assertEqual(page.locator('.request-board__toast').count(), 2)
                page.clock.fast_forward(2000)
                self.assertEqual(page.locator('.request-board__toast').count(), 0)
                page.set_content(document)
                close = page.get_by_role('button', name='Tutup notifikasi')
                close.first.focus()
                page.clock.fast_forward(10000)
                self.assertEqual(close.count(), 1)
                self.assertTrue(close.first.evaluate('el => el === document.activeElement'))
                page.locator('#next').focus()
                page.clock.fast_forward(3999)
                self.assertEqual(close.count(), 1)
                page.clock.fast_forward(1)
                self.assertEqual(close.count(), 0)
            finally:
                browser.close()
