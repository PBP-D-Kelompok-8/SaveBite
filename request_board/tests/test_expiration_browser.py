import os
from datetime import timedelta
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from request_board.models import Request

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@skipUnless(sync_playwright, 'Optional Playwright browser tooling is not installed')
class ExpirationBrowserTests(StaticLiveServerTestCase):
    def test_deadline_field_and_expired_card_on_mobile_and_desktop(self):
        owner = get_user_model().objects.create_user(username='expiry_browser_owner')
        Request.objects.create(owner=owner, title='Beras kedaluwarsa', food_item='Beras', quantity=1, unit='kg', expires_at=timezone.now() - timedelta(hours=1))
        client = Client()
        client.force_login(owner)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                for width in (375, 1280):
                    with self.subTest(width=width):
                        context = browser.new_context(viewport={'width': width, 'height': 900})
                        context.add_cookies([{'name': 'sessionid', 'value': client.cookies['sessionid'].value, 'url': self.live_server_url}])
                        page = context.new_page()
                        page.goto(self.live_server_url + reverse('request_board:request_create'))
                        field = page.get_by_label('Batas waktu permintaan')
                        self.assertTrue(field.is_visible())
                        self.assertTrue(field.input_value())
                        self.assertEqual(field.get_attribute('type'), 'datetime-local')
                        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width)
                        page.goto(self.live_server_url + reverse('request_board:request_board'))
                        card = page.locator('.request-board__card').filter(has_text='Beras kedaluwarsa')
                        self.assertTrue(card.get_by_text('Kedaluwarsa', exact=True).is_visible())
                        self.assertTrue(card.get_by_text('Batas waktu:', exact=False).is_visible())
                        self.assertEqual(card.get_by_role('button', name='Tandai Terpenuhi').count(), 0)
                        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width)
                        context.close()
            finally:
                browser.close()
