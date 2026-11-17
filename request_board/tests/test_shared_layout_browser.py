"""Check that board pages preserve the shared navigation and footer styling."""
import os
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse

from request_board.models import Request

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@skipUnless(sync_playwright, "Optional Playwright browser tooling is not installed")
class SharedLayoutBrowserTests(StaticLiveServerTestCase):
    def test_board_pages_match_landing_navigation_and_footer(self):
        owner = get_user_model().objects.create_user(username="layout-owner")
        item = Request.objects.create(
            owner=owner, title="Beras", food_item="Beras", quantity=1, unit="kg",
        )
        client = Client()
        client.force_login(owner)
        session = client.cookies["sessionid"].value
        board_paths = [reverse("request_board:request_board")]
        owner_paths = [reverse("request_board:request_create"),
                       reverse("request_board:request_offers", args=[item.pk])]

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                channel=os.environ.get("PLAYWRIGHT_CHROMIUM_CHANNEL") or None,
            )
            try:
                for width in (375, 900, 1280):
                    for authenticated in (False, True):
                        with self.subTest(width=width, authenticated=authenticated):
                            context = browser.new_context(viewport={"width": width, "height": 900})
                            try:
                                if authenticated:
                                    context.add_cookies([{"name": "sessionid", "value": session,
                                                          "url": self.live_server_url}])
                                page = context.new_page()

                                def layout(path):
                                    page.goto(self.live_server_url + path)
                                    page.evaluate("document.fonts.ready")
                                    if width <= 768:
                                        page.locator("#navToggle").click()
                                    return page.evaluate("""() => Object.fromEntries(
                                        ['.navbar__inner', '.navbar__logo img', '.navbar__links .btn',
                                         '.navbar__actions .btn', '.footer', '.footer-logo', '.footer-text']
                                        .map(selector => {
                                            const element = document.querySelector(selector);
                                            const style = getComputedStyle(element);
                                            const rect = element.getBoundingClientRect();
                                            return [selector, {
                                                width: rect.width, height: rect.height,
                                                fontSize: style.fontSize, padding: style.padding,
                                                display: style.display, gap: style.gap
                                            }];
                                        }))""")

                                expected = layout(reverse("landing_page"))
                                for path in board_paths + (owner_paths if authenticated else []):
                                    with self.subTest(path=path):
                                        self.assertEqual(layout(path), expected)
                            finally:
                                context.close()
            finally:
                browser.close()
