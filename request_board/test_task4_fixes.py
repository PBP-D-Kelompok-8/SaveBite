"""Regression coverage for session recovery and modal error visibility."""
import os
from unittest import skipUnless
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, TestCase
from django.urls import reverse

from request_board.models import Request, RequestOffer

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:
    sync_playwright = None


class SessionRecoveryTests(TestCase):
    def test_create_session_recovery_still_returns_to_form(self):
        url = reverse('request_board:request_create')
        response = self.client.post(url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(parse_qs(urlsplit(response.json()['login_url']).query)['next'], [url])

    def test_expired_actions_login_returns_to_safe_get_without_mutation(self):
        users = get_user_model()
        owner = users.objects.create_user(username='recovery-owner', password='test-password')
        donor = users.objects.create_user(username='recovery-donor', password='test-password')
        other_donor = users.objects.create_user(username='other-donor', password='test-password')
        for action in ('offer', 'select', 'fulfill', 'delete'):
            with self.subTest(action=action):
                item = Request.objects.create(owner=owner, title=action, food_item='Roti', quantity=1, unit='porsi', status='offered')
                offer = RequestOffer.objects.create(request=item, offered_by=other_donor)
                if action == 'fulfill':
                    item.selected_offer = offer
                    item.save(update_fields=['selected_offer'])
                actor = donor if action == 'offer' else owner
                args = [item.pk, offer.pk] if action == 'select' else [item.pk]
                url = reverse('request_board:request_select_offer' if action == 'select' else 'request_board:request_' + action, args=args)
                before = (item.status, item.selected_offer_id, item.updated_at, item.offers.count())
                client = Client(enforce_csrf_checks=True)
                client.force_login(actor)
                client.get(reverse('request_board:request_board'))
                token = client.cookies['csrftoken'].value
                del client.cookies['sessionid']
                expired = client.post(url, {'csrfmiddlewaretoken': token}, HTTP_X_REQUESTED_WITH='XMLHttpRequest', HTTP_REFERER='https://evil.example/path')
                self.assertEqual(expired.status_code, 401)
                login_url = expired.json()['login_url']
                next_url = parse_qs(urlsplit(login_url).query)['next'][0]
                self.assertEqual(next_url, reverse('request_board:request_board'))
                login_page = client.get(login_url)
                self.assertEqual(login_page.status_code, 200)
                logged_in = client.post(urlsplit(login_url).path, {
                    'username': actor.username, 'password': 'test-password', 'next': next_url,
                    'csrfmiddlewaretoken': client.cookies['csrftoken'].value,
                }, follow=True)
                self.assertEqual(logged_in.status_code, 200)
                self.assertEqual(logged_in.redirect_chain[-1][0], next_url)
                item.refresh_from_db()
                self.assertEqual((item.status, item.selected_offer_id, item.updated_at, item.offers.count()), before)


@skipUnless(sync_playwright, 'Optional Playwright browser tooling is not installed')
class ModalErrorRecoveryTests(StaticLiveServerTestCase):
    def setUp(self):
        super().setUp()
        self.owner = get_user_model().objects.create_user(username='modal-owner')
        Request.objects.bulk_create([
            Request(owner=self.owner, title=f'Kartu {i}', food_item='Roti', quantity=1, unit='porsi')
            for i in range(8)
        ])
        client = Client()
        client.force_login(self.owner)
        self.session = client.cookies['sessionid'].value

    def assert_visible_alert(self, page, dialog, text):
        alert = dialog.locator('[data-ajax-notice][role=alert]')
        expect(alert).to_contain_text(text, timeout=3000)
        expect(alert).to_be_visible()
        box = alert.bounding_box()
        self.assertIsNotNone(box)
        self.assertGreaterEqual(box['y'], 0)
        self.assertLessEqual(box['y'] + box['height'], 812)
        self.assertTrue(alert.evaluate('el => el.closest("dialog").matches(":modal")'))
        self.assertTrue(alert.evaluate('el => document.activeElement === el'))

    def test_delete_bottom_card_errors_are_in_active_dialog_and_retryable(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                for index, error in enumerate(('csrf', 'session', 'network')):
                    with self.subTest(error=error):
                        context = browser.new_context(viewport={'width': 375, 'height': 812})
                        context.add_cookies([{'name': 'sessionid', 'value': self.session, 'url': self.live_server_url}])
                        page = context.new_page()
                        page.goto(self.live_server_url + reverse('request_board:request_board'))
                        page.locator('.request-board__delete-summary').last.click()
                        dialog = page.locator('dialog:modal')
                        form = dialog.locator('form')
                        token = form.locator('[name=csrfmiddlewaretoken]')
                        original_token = token.input_value()
                        if error == 'csrf':
                            token.evaluate("el => el.value = 'invalid'")
                        elif error == 'session':
                            context.clear_cookies(name='sessionid')
                        else:
                            page.route('**/delete/', lambda route: route.abort())
                        button = dialog.get_by_role('button', name='Ya, hapus')
                        button.click()
                        self.assert_visible_alert(page, dialog, {'csrf': 'CSRF', 'session': 'masuk', 'network': 'Gagal'}[error])
                        expect(button).to_be_enabled()
                        expect(page.locator('.request-board__card')).to_have_count(8 - index)
                        if error == 'session':
                            expect(dialog.get_by_role('link', name='Masuk')).to_be_visible()
                        if error == 'csrf':
                            self.assertEqual(token.input_value(), 'invalid')
                            token.evaluate('(el, value) => el.value = value', original_token)
                        elif error == 'session':
                            context.add_cookies([{'name': 'sessionid', 'value': self.session, 'url': self.live_server_url}])
                        else:
                            page.unroute('**/delete/')
                        button.click()
                        expect(page.locator('.request-board__card')).to_have_count(7 - index)
                        context.close()
            finally:
                browser.close()

    def test_mobile_filter_errors_are_in_sheet_and_preserve_inputs_for_retry(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                for error in ('forbidden', 'session', 'network'):
                    with self.subTest(error=error):
                        context = browser.new_context(viewport={'width': 375, 'height': 812})
                        context.add_cookies([{'name': 'sessionid', 'value': self.session, 'url': self.live_server_url}])
                        page = context.new_page()
                        page.goto(self.live_server_url + reverse('request_board:request_board'))
                        page.locator('[data-filter-open]').click()
                        dialog = page.locator('#board-filter-dialog')
                        dialog.locator('summary').click()
                        dialog.locator('[name=q]').fill('Kartu')
                        if error == 'session':
                            dialog.locator('[name=mine]').check()
                            context.clear_cookies(name='sessionid')
                        elif error == 'network':
                            page.route('**/board/?*', lambda route: route.abort())
                        else:
                            page.route('**/board/?*', lambda route: route.fulfill(status=403, body='CSRF'))
                        button = dialog.get_by_role('button', name='Terapkan')
                        button.click()
                        self.assert_visible_alert(page, dialog, {'forbidden': 'tidak valid', 'session': 'masuk', 'network': 'Gagal'}[error])
                        expect(button).to_be_enabled()
                        expect(dialog.locator('[name=q]')).to_have_value('Kartu')
                        if error == 'session':
                            expect(dialog.locator('[name=mine]')).to_be_checked()
                            context.add_cookies([{'name': 'sessionid', 'value': self.session, 'url': self.live_server_url}])
                        else:
                            page.unroute('**/board/?*')
                        button.click()
                        expect(dialog).not_to_be_visible()
                        self.assertIn('q=Kartu', page.url)
                        context.close()
            finally:
                browser.close()
