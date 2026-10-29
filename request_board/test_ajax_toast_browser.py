"""AJAX feedback must use the board's visible, accessible toast UI."""
import os
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse

from request_board.models import Request

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:
    sync_playwright = None


@skipUnless(sync_playwright, 'Optional Playwright browser tooling is not installed')
class AjaxToastBrowserTests(StaticLiveServerTestCase):
    def test_confirmed_offer_still_notifies_when_filter_supersedes_refresh(self):
        users = get_user_model()
        owner = users.objects.create_user(username='toast-race-owner')
        donor = users.objects.create_user(username='toast-race-donor')
        Request.objects.create(owner=owner, title='Beras', food_item='Beras', quantity=1, unit='kg')
        client = Client()
        client.force_login(donor)
        session = client.cookies['sessionid'].value
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                page = browser.new_page()
                page.context.add_cookies([{'name': 'sessionid', 'value': session, 'url': self.live_server_url}])
                page.goto(self.live_server_url + reverse('request_board:request_board'))
                page.evaluate("""() => {
                    const original = window.fetch;
                    let firstGet = true;
                    window.fetch = (url, options) => {
                        if (options?.method === 'POST' || !firstGet) return original(url, options);
                        firstGet = false;
                        return original(url, options).then(response => new Promise(resolve => {
                            window.releaseRefresh = () => resolve(response);
                        }));
                    };
                }""")
                page.get_by_role('button', name='Saya Punya!').click()
                page.wait_for_function('typeof window.releaseRefresh === "function"')
                page.locator('[data-filter-form]').evaluate("form => { form.elements.title.value = 'Beras'; form.requestSubmit(); }")
                page.wait_for_url('**/*title=Beras*')
                page.evaluate('window.releaseRefresh()')
                expect(page.locator('[data-toast-region] [role=status]')).to_contain_text('Penawaran bantuan makanan berhasil dikirim.', timeout=2000)
            finally:
                browser.close()

    def test_session_error_toast_keeps_login_link_and_pauses_while_focused(self):
        users = get_user_model()
        owner = users.objects.create_user(username='toast-session-owner')
        donor = users.objects.create_user(username='toast-session-donor')
        Request.objects.create(owner=owner, title='Sesi', food_item='Nasi', quantity=1, unit='porsi')
        client = Client()
        client.force_login(donor)
        session = client.cookies['sessionid'].value
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                page = browser.new_page()
                page.context.add_cookies([{'name': 'sessionid', 'value': session, 'url': self.live_server_url}])
                page.goto(self.live_server_url + reverse('request_board:request_board'))
                page.evaluate("""() => {
                    window.fetch = () => Promise.resolve(new Response(
                        JSON.stringify({ok: false, message: 'Silakan masuk kembali.', login_url: '/login/?next=/board/'}),
                        {status: 401, headers: {'Content-Type': 'application/json'}}));
                }""")
                page.get_by_role('button', name='Saya Punya!').click()
                toast = page.locator('[data-toast-region] [role=alert]')
                expect(toast).to_contain_text('Silakan masuk kembali.')
                login = toast.get_by_role('link', name='Masuk')
                expect(login).to_have_attribute('href', '/login/?next=/board/')
                login.focus()
                page.wait_for_timeout(6500)
                expect(toast).to_be_visible()
                expect(login).to_be_focused()
                toast.get_by_role('button', name='Tutup notifikasi').click()
                expect(toast).to_have_count(0)
            finally:
                browser.close()

    def test_offer_feedback_is_visible_dismissible_and_waits_for_confirmation(self):
        users = get_user_model()
        owner = users.objects.create_user(username='toast-owner')
        donor = users.objects.create_user(username='toast-donor')
        for width in (375, 1280):
            Request.objects.create(owner=owner, title=f'Kebutuhan {width}', food_item='Nasi', quantity=1, unit='porsi')
        client = Client()
        client.force_login(donor)
        session = client.cookies['sessionid'].value
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                for width in (375, 1280):
                    with self.subTest(width=width):
                        page = browser.new_page(viewport={'width': width, 'height': 800})
                        page.context.add_cookies([{'name': 'sessionid', 'value': session, 'url': self.live_server_url}])
                        page.goto(self.live_server_url + reverse('request_board:request_board') + f'?title=Kebutuhan+{width}')
                        page.evaluate("""() => {
                            const original = window.fetch;
                            window.realFetch = original;
                            window.fetch = (url, options) => options?.method === 'POST'
                                ? new Promise(resolve => { window.rejectOffer = () => resolve(new Response(
                                    JSON.stringify({ok: false, message: 'Penawaran ditolak <b>aman</b>'}),
                                    {status: 409, headers: {'Content-Type': 'application/json'}})); })
                                : original(url, options);
                        }""")
                        page.get_by_role('button', name='Saya Punya!').click()
                        page.wait_for_function('typeof window.rejectOffer === "function"')
                        expect(page.locator('[data-toast-region]')).to_have_count(0)
                        page.evaluate('window.rejectOffer()')
                        error = page.locator('[data-toast-region] [role=alert]')
                        expect(error).to_contain_text('Penawaran ditolak <b>aman</b>', timeout=2000)
                        expect(error.locator('b')).to_have_count(0)
                        expect(error).to_be_in_viewport()
                        error.get_by_role('button', name='Tutup notifikasi').click()
                        page.evaluate("""() => {
                            const original = window.realFetch;
                            window.fetch = (url, options) => options?.method === 'POST'
                                ? new Promise(resolve => { window.releaseOffer = () => resolve(original(url, options)); })
                                : original(url, options);
                        }""")
                        page.get_by_role('button', name='Saya Punya!').click()
                        page.wait_for_function('typeof window.releaseOffer === "function"')
                        expect(page.locator('[data-toast-region]')).to_have_count(0)
                        header = page.locator('.request-board__header-action a')
                        header.focus()
                        page.evaluate('window.releaseOffer()')
                        success = page.locator('[data-toast-region] [role=status]')
                        expect(success).to_contain_text('Penawaran bantuan makanan berhasil dikirim.')
                        expect(success).to_be_in_viewport()
                        expect(header).to_be_focused()
                        success.get_by_role('button', name='Tutup notifikasi').focus()
                        page.keyboard.press('Enter')
                        expect(page.locator('[data-toast-region]')).to_have_count(0)
                        page.close()
            finally:
                browser.close()
