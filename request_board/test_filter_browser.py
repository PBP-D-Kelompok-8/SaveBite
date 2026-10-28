"""Optional browser checks; use PLAYWRIGHT_CHROMIUM_CHANNEL=msedge on Windows."""
import mimetypes
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest import skipUnless

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from request_board.models import Request

try:
    from playwright.sync_api import sync_playwright, expect
except ImportError:
    sync_playwright = None


@skipUnless(sync_playwright, "Optional Playwright browser tooling is not installed")
class BoardFilterBrowserTests(TestCase):
    def test_responsive_filters_apply_reset_and_restore_focus(self):
        owner = get_user_model().objects.create_user(username="browser-filter-owner")
        for title, location in (("Beras untuk berbagi", "Beji, Depok"),
                                ("Sayur untuk dapur", "Kukusan, Depok")):
            Request.objects.create(owner=owner, title=title, food_item="Bahan", quantity=1,
                                   unit="kg", location=location, status="pending")
        board_url = reverse("request_board:request_board")
        initial = self.client.get(board_url).content.decode()
        filtered = self.client.get(board_url, {"status": "pending", "location": "Beji, Depok",
                                              "sort": "oldest"}).content.decode()
        initial_partial = self.client.get(board_url, HTTP_X_REQUESTED_WITH="XMLHttpRequest").content.decode()
        filtered_partial = self.client.get(board_url, {"status": "pending", "location": "Beji, Depok",
                                                       "sort": "oldest"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest").content.decode()
        errors = []

        def serve(route):
            url = urlsplit(route.request.url)
            if url.path.startswith("/static/"):
                asset = Path(settings.BASE_DIR) / url.path.lstrip("/")
                if asset.is_file():
                    route.fulfill(body=asset.read_bytes(), content_type=mimetypes.guess_type(asset)[0] or "application/octet-stream")
                else:
                    route.fulfill(status=404, body="Missing static file")
            else:
                query = parse_qs(url.query)
                ajax = route.request.headers.get('x-requested-with') == 'XMLHttpRequest'
                body = (filtered_partial if ajax else filtered) if query.get("location") == ["Beji, Depok"] else (initial_partial if ajax else initial)
                route.fulfill(body=body, content_type="text/html; charset=utf-8")

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get("PLAYWRIGHT_CHROMIUM_CHANNEL") or None)
            try:
                for width in (1440, 1024, 768, 375, 320):
                    with self.subTest(width=width):
                        page = browser.new_page(viewport={"width": width, "height": 850})
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.route("http://testserver/**", serve)
                        page.goto("http://testserver" + board_url)
                        form = page.locator('[data-filter-form]')
                        trigger = page.locator('[data-filter-open]')
                        dialog = page.get_by_role("dialog", name="Filter permintaan")
                        if width <= 768:
                            expect(trigger).to_be_visible()
                            expect(form).not_to_be_visible()
                            trigger.click()
                            expect(dialog).to_be_visible()
                            expect(page.get_by_role("button", name="Tutup filter")).to_be_focused()
                            page.locator('#id_status').select_option('fulfilled')
                            page.keyboard.press("Escape")
                            expect(dialog).not_to_be_visible()
                            expect(trigger).to_be_focused()
                            trigger.click()
                            expect(page.locator('#id_status')).to_have_value('')
                        else:
                            expect(trigger).not_to_be_visible()
                            expect(form).to_be_visible()
                        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)
                        bounds = form.bounding_box()
                        self.assertGreaterEqual(bounds["x"], 0)
                        self.assertLessEqual(bounds["x"] + bounds["width"], width)
                        if width <= 768:
                            self.assertLessEqual(dialog.bounding_box()["y"] + dialog.bounding_box()["height"], 851)
                        screenshot_dir = os.environ.get("FILTER_SCREENSHOT_DIR")
                        if screenshot_dir and width in (1440, 375):
                            page.screenshot(path=str(Path(screenshot_dir) / f"request-board-filter-{width}.png"))
                        page.locator('#id_status').select_option('pending')
                        page.locator('#id_location').select_option('Beji, Depok')
                        page.locator('#id_sort').select_option('oldest')
                        page.evaluate('window.filterMarker = 42')
                        page.get_by_role('button', name='Terapkan').click()
                        expect(page.locator('.request-board__card-title')).to_have_text(['Beras untuk berbagi'])
                        query = parse_qs(urlsplit(page.url).query)
                        self.assertEqual(query['status'], ['pending'])
                        self.assertEqual(query['location'], ['Beji, Depok'])
                        self.assertEqual(query['sort'], ['oldest'])
                        self.assertEqual(page.evaluate('window.filterMarker'), 42)
                        if width <= 768:
                            page.locator('[data-filter-open]').click()
                        page.get_by_role('link', name='Reset', exact=True).filter(visible=True).click()
                        expect(page.locator('.request-board__card-title')).to_have_count(2)
                        self.assertEqual(page.evaluate('window.filterMarker'), 42)
                        page.close()
                self.assertEqual(errors, [])
            finally:
                browser.close()
