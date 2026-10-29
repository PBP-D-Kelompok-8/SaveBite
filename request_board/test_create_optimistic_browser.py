"""Delayed create responses exercise optimistic state and safe recovery."""
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
class CreateOptimisticBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        super().setUp()
        self.owner = get_user_model().objects.create_user(username='modal-create-owner')
        Request.objects.create(owner=self.owner, title='Hasil lama', food_item='Roti', quantity=1, unit='porsi')
        client = Client()
        client.force_login(self.owner)
        self.session = client.cookies['sessionid'].value

    def open_form(self, page, title):
        page.get_by_role('link', name='Buat Permintaan').first.click()
        dialog = page.locator('[data-create-dialog]')
        expect(dialog).to_be_visible()
        dialog.locator('[name=title]').fill(title)
        dialog.locator('[name=food_item]').fill('Beras')
        dialog.locator('[name=quantity]').fill('2')
        dialog.locator('[name=unit]').select_option('kg')
        return dialog

    def test_double_open_uses_one_form_and_one_fetch(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                page = browser.new_page()
                page.context.add_cookies([{'name': 'sessionid', 'value': self.session, 'url': self.live_server_url}])
                page.goto(self.live_server_url + reverse('request_board:request_board'))
                page.evaluate("""() => {
                    const original = window.fetch;
                    window.formLoads = 0;
                    window.fetch = (url, options) => {
                        window.formLoads++;
                        return new Promise(resolve => { window.releaseForm = () => resolve(original(url, options)); });
                    };
                    const link = document.querySelector('.request-board__header-action a');
                    link.click();
                    link.click();
                }""")
                self.assertEqual(page.evaluate('window.formLoads'), 1)
                page.evaluate('window.releaseForm()')
                expect(page.locator('[data-create-dialog] form')).to_have_count(1)
            finally:
                browser.close()

    def test_timeout_before_commit_keeps_draft_and_reuses_key(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                page = browser.new_page()
                page.clock.install()
                page.context.add_cookies([{'name': 'sessionid', 'value': self.session, 'url': self.live_server_url}])
                page.goto(self.live_server_url + reverse('request_board:request_board'))
                dialog = self.open_form(page, 'Timeout aman')
                page.evaluate("""() => {
                    const original = window.fetch;
                    window.keys = [];
                    window.fetch = (url, options) => {
                        if (options?.method === 'POST') {
                            window.keys.push(options.headers['Idempotency-Key']);
                            if (window.keys.length === 1) return new Promise((resolve, reject) => {
                                options.signal.addEventListener('abort', () => reject(new DOMException('Timeout', 'AbortError')));
                            });
                        }
                        return original(url, options);
                    };
                }""")
                dialog.get_by_role('button', name='Simpan Permintaan').click()
                page.wait_for_function('window.keys.length === 1')
                page.clock.fast_forward(15000)
                expect(dialog.locator('[data-create-recovery]')).to_contain_text('belum dapat dipastikan')
                expect(dialog.locator('[name=title]')).to_have_value('Timeout aman')
                dialog.get_by_role('button', name='Coba simpan lagi').click()
                expect(dialog).not_to_be_visible()
                keys = page.evaluate('window.keys')
                self.assertEqual(len(keys), 2)
                self.assertEqual(keys[0], keys[1])
            finally:
                browser.close()
        self.assertEqual(Request.objects.filter(owner=self.owner, title='Timeout aman').count(), 1)

    def test_uncertain_draft_warns_before_leaving_or_reloading(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                page = browser.new_page()
                page.context.add_cookies([{'name': 'sessionid', 'value': self.session, 'url': self.live_server_url}])
                page.goto(self.live_server_url + reverse('request_board:request_board'))
                dialog = self.open_form(page, 'Jangan hilang')
                page.evaluate("""() => {
                    const original = window.fetch;
                    window.fetch = (url, options) => options?.method === 'POST' || String(url).includes('/new/status/')
                        ? Promise.reject(new TypeError('offline')) : original(url, options);
                }""")
                dialog.get_by_role('button', name='Simpan Permintaan').click()
                expect(dialog.locator('[data-create-recovery]')).to_contain_text('belum dapat dipastikan')
                dialogs = []
                def dismiss(warning):
                    dialogs.append(warning.type)
                    warning.dismiss()
                page.once('dialog', dismiss)
                page.evaluate('window.location.reload()')
                expect(dialog.locator('[name=title]')).to_have_value('Jangan hilang')
                self.assertEqual(dialogs, ['beforeunload'])
            finally:
                browser.close()

    def test_uncertain_create_with_expired_session_offers_safe_login_in_new_tab(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                page = browser.new_page()
                page.context.add_cookies([{'name': 'sessionid', 'value': self.session, 'url': self.live_server_url}])
                page.goto(self.live_server_url + reverse('request_board:request_board'))
                dialog = self.open_form(page, 'Draft sesi')
                page.evaluate("""() => {
                    const original = window.fetch;
                    window.fetch = (url, options) => {
                        if (options?.method === 'POST') return Promise.reject(new TypeError('offline'));
                        if (String(url).includes('/new/status/')) return Promise.resolve(new Response(
                            JSON.stringify({ok: false, message: 'Silakan masuk kembali.', login_url: '/login/?next=/board/'}),
                            {status: 401, headers: {'Content-Type': 'application/json'}}));
                        return original(url, options);
                    };
                }""")
                dialog.get_by_role('button', name='Simpan Permintaan').click()
                recovery = dialog.locator('[data-create-recovery]')
                expect(recovery).to_contain_text('belum dapat dipastikan')
                login = recovery.get_by_role('link', name='Masuk kembali')
                expect(login).to_have_attribute('target', '_blank')
                expect(login).to_have_attribute('href', '/login/?next=/board/')
                expect(dialog.locator('[name=title]')).to_have_value('Draft sesi')
            finally:
                browser.close()

    def test_explicit_session_rejection_preserves_draft_and_login_opens_new_tab(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                page = browser.new_page()
                page.context.add_cookies([{'name': 'sessionid', 'value': self.session, 'url': self.live_server_url}])
                page.goto(self.live_server_url + reverse('request_board:request_board'))
                dialog = self.open_form(page, 'Draft sesi ditolak')
                page.evaluate("""() => {
                    window.fetch = () => Promise.resolve(new Response(
                        JSON.stringify({ok: false, message: 'Silakan masuk kembali.', login_url: '/login/?next=/board/'}),
                        {status: 401, headers: {'Content-Type': 'application/json'}}));
                }""")
                dialog.get_by_role('button', name='Simpan Permintaan').click()
                notice = dialog.locator('[data-ajax-notice]')
                expect(notice).to_contain_text('Silakan masuk kembali')
                expect(notice.get_by_role('link', name='Masuk')).to_have_attribute('target', '_blank')
                expect(dialog.locator('[name=title]')).to_have_value('Draft sesi ditolak')
                expect(dialog.locator('[name=title]')).to_be_enabled()
            finally:
                browser.close()

    def test_retry_after_explicit_session_rejection_refreshes_csrf(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                page = browser.new_page()
                page.context.add_cookies([{'name': 'sessionid', 'value': self.session, 'url': self.live_server_url}])
                page.goto(self.live_server_url + reverse('request_board:request_board'))
                dialog = self.open_form(page, 'CSRF baru')
                page.evaluate("""() => {
                    const original = window.fetch;
                    let first = true;
                    window.fetch = (url, options) => {
                        if (options?.method === 'POST' && first) {
                            first = false;
                            return Promise.resolve(new Response(JSON.stringify({ok: false, message: 'Silakan masuk kembali.'}),
                                {status: 401, headers: {'Content-Type': 'application/json'}}));
                        }
                        return original(url, options);
                    };
                }""")
                dialog.get_by_role('button', name='Simpan Permintaan').click()
                expect(dialog.locator('[data-ajax-notice]')).to_contain_text('Silakan masuk kembali')
                page.evaluate("document.cookie = 'csrftoken=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA; path=/'")
                dialog.get_by_role('button', name='Simpan Permintaan').click()
                expect(dialog).not_to_be_visible()
                expect(page.locator('[data-board-results]')).to_contain_text('CSRF baru')
            finally:
                browser.close()
        self.assertEqual(Request.objects.filter(owner=self.owner, title='CSRF baru').count(), 1)

    def test_uncertain_retry_requires_original_account(self):
        other = get_user_model().objects.create_user(username='switched-account')
        other_client = Client()
        other_client.force_login(other)
        other_session = other_client.cookies['sessionid'].value
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                page = browser.new_page()
                page.context.add_cookies([{'name': 'sessionid', 'value': self.session, 'url': self.live_server_url}])
                page.goto(self.live_server_url + reverse('request_board:request_board'))
                dialog = self.open_form(page, 'Draft akun')
                page.evaluate("""() => {
                    const original = window.fetch;
                    let firstPost = true, firstCheck = true;
                    window.fetch = (url, options) => {
                        if (options?.method === 'POST' && firstPost) {
                            firstPost = false;
                            return Promise.reject(new TypeError('offline'));
                        }
                        if (String(url).includes('/new/status/') && firstCheck) {
                            firstCheck = false;
                            return Promise.resolve(new Response(JSON.stringify({ok: false, login_url: '/login/?next=/board/'}),
                                {status: 401, headers: {'Content-Type': 'application/json'}}));
                        }
                        return original(url, options);
                    };
                }""")
                dialog.get_by_role('button', name='Simpan Permintaan').click()
                expect(dialog.locator('[data-create-recovery]')).to_contain_text('tab lain')
                page.context.add_cookies([{'name': 'sessionid', 'value': other_session, 'url': self.live_server_url}])
                dialog.get_by_role('button', name='Periksa status').click()
                expect(dialog.locator('[data-create-recovery]')).to_contain_text('akun pembuat draft')
                expect(dialog.get_by_role('button', name='Coba simpan lagi')).to_be_disabled()
                page.context.add_cookies([{'name': 'sessionid', 'value': self.session, 'url': self.live_server_url}])
                dialog.get_by_role('button', name='Periksa status').click()
                expect(dialog.get_by_role('button', name='Coba simpan lagi')).to_be_enabled()
                dialog.get_by_role('button', name='Coba simpan lagi').click()
                expect(dialog).not_to_be_visible()
            finally:
                browser.close()
        self.assertEqual(Request.objects.filter(owner=self.owner, title='Draft akun').count(), 1)
        self.assertFalse(Request.objects.filter(owner=other, title='Draft akun').exists())

    def test_create_pending_before_response_preserves_filter_and_url(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                for width in (375, 1280):
                    page = browser.new_page(viewport={'width': width, 'height': 800})
                    page.context.add_cookies([{'name': 'sessionid', 'value': self.session, 'url': self.live_server_url}])
                    url = self.live_server_url + reverse('request_board:request_board') + '?title=Hasil'
                    page.goto(url)
                    dialog = self.open_form(page, f'Draft {width}')
                    bounds = dialog.bounding_box()
                    self.assertGreaterEqual(bounds['x'], 0)
                    self.assertLessEqual(bounds['x'] + bounds['width'], width)
                    self.assertLessEqual(bounds['height'], 800)
                    page.evaluate("""() => {
                        const original = window.fetch;
                        window.fetch = (url, options) => options?.method === 'POST'
                            ? new Promise(resolve => { window.releaseCreate = () => resolve(original(url, options)); })
                            : original(url, options);
                    }""")
                    dialog.get_by_role('button', name='Simpan Permintaan').click()
                    page.wait_for_function('typeof window.releaseCreate === "function"')
                    expect(page.locator('[data-create-pending]')).to_contain_text('Sedang menyimpan', timeout=2000)
                    expect(page.locator('[data-create-pending]')).to_contain_text(f'Draft {width}')
                    self.assertRegex(page.locator('[data-create-pending]').get_attribute('data-local-id') or '', r'^temp-')
                    expect(page.locator('[data-create-pending] form, [data-create-pending] button')).to_have_count(0)
                    expect(page.locator('[data-board-results]')).not_to_contain_text(f'Draft {width}')
                    expect(page.locator('[data-toast-region] [role=status]')).to_have_count(0)
                    page.evaluate('window.releaseCreate()')
                    expect(dialog).not_to_be_visible()
                    expect(page.locator('[data-toast-region]')).to_contain_text('berhasil dibuat')
                    self.assertEqual(page.url, url)
                    expect(page.locator('[data-board-results]')).not_to_contain_text(f'Draft {width}')
                    page.close()
            finally:
                browser.close()

    def test_stale_filter_response_cannot_overwrite_pending_create(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                page = browser.new_page()
                page.context.add_cookies([{'name': 'sessionid', 'value': self.session, 'url': self.live_server_url}])
                board = self.live_server_url + reverse('request_board:request_board')
                page.goto(board)
                page.evaluate("""() => {
                    const original = window.fetch;
                    window.fetch = (url, options) => {
                        if (options?.method === 'POST') return new Promise(resolve => {
                            window.releaseCreate = () => resolve(original(url, options));
                        });
                        if (options?.headers?.['X-Requested-With'] && String(url).includes('title=Hasil')) {
                            return original(url, options).then(response => new Promise(resolve => {
                                window.releaseOldFilter = () => resolve(response);
                            }));
                        }
                        return original(url, options);
                    };
                }""")
                page.locator('[data-filter-form]').evaluate("f => { f.elements.title.value = 'Hasil'; f.requestSubmit(); }")
                page.wait_for_function('typeof window.releaseOldFilter === "function"')
                dialog = self.open_form(page, 'Baru')
                dialog.get_by_role('button', name='Simpan Permintaan').click()
                page.wait_for_function('typeof window.releaseCreate === "function"')
                page.evaluate('window.releaseOldFilter()')
                expect(page.locator('[data-create-pending]')).to_contain_text('Sedang menyimpan')
                self.assertEqual(page.url, board)
                page.evaluate('window.releaseCreate()')
                expect(dialog).not_to_be_visible()
                expect(page.locator('[data-board-results]')).to_contain_text('Baru')
                self.assertEqual(page.url, board)
            finally:
                browser.close()

    def test_network_loss_after_commit_allows_safe_retry_with_same_key(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                page = browser.new_page()
                page.context.add_cookies([{'name': 'sessionid', 'value': self.session, 'url': self.live_server_url}])
                page.goto(self.live_server_url + reverse('request_board:request_board'))
                dialog = self.open_form(page, 'Draft aman')
                page.evaluate("""() => {
                    const original = window.fetch;
                    let first = true;
                    window.fetch = async (url, options) => {
                        if (String(url).includes('/new/status/')) throw new TypeError('offline');
                        const response = await original(url, options);
                        if (options?.method === 'POST' && first) { first = false; throw new TypeError('lost response'); }
                        return response;
                    };
                }""")
                dialog.get_by_role('button', name='Simpan Permintaan').click()
                expect(dialog.locator('[data-create-recovery]')).to_contain_text('belum dapat dipastikan')
                expect(dialog.locator('[name=title]')).to_have_value('Draft aman')
                dialog.get_by_role('button', name='Coba simpan lagi').click()
                expect(dialog).not_to_be_visible()
                expect(page.locator('[data-toast-region]')).to_contain_text('berhasil dibuat')
            finally:
                browser.close()
        self.assertEqual(Request.objects.filter(owner=self.owner, title='Draft aman').count(), 1)

    def test_lost_response_after_commit_is_reconciled_automatically(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                page = browser.new_page()
                page.context.add_cookies([{'name': 'sessionid', 'value': self.session, 'url': self.live_server_url}])
                page.goto(self.live_server_url + reverse('request_board:request_board'))
                dialog = self.open_form(page, 'Hasil tersimpan')
                page.evaluate("""() => {
                    const original = window.fetch;
                    window.fetch = async (url, options) => {
                        const response = await original(url, options);
                        if (options?.method === 'POST') throw new TypeError('response lost');
                        return response;
                    };
                }""")
                dialog.get_by_role('button', name='Simpan Permintaan').click()
                expect(dialog).not_to_be_visible()
                expect(page.locator('[data-board-results]')).to_contain_text('Hasil tersimpan')
                expect(page.locator('[data-toast-region]')).to_contain_text('berhasil dibuat')
            finally:
                browser.close()
        self.assertEqual(Request.objects.filter(owner=self.owner, title='Hasil tersimpan').count(), 1)

    def test_confirmed_create_with_failed_refresh_reports_success(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                page = browser.new_page()
                page.context.add_cookies([{'name': 'sessionid', 'value': self.session, 'url': self.live_server_url}])
                page.goto(self.live_server_url + reverse('request_board:request_board'))
                dialog = self.open_form(page, 'Refresh gagal')
                page.evaluate("""() => {
                    const original = window.fetch;
                    window.fetch = (url, options) => options?.method === 'POST'
                        ? original(url, options) : Promise.reject(new TypeError('offline'));
                }""")
                dialog.get_by_role('button', name='Simpan Permintaan').click()
                expect(dialog).not_to_be_visible()
                expect(page.get_by_role('alert')).to_contain_text('berhasil dibuat, tetapi papan gagal diperbarui')
            finally:
                browser.close()
        self.assertEqual(Request.objects.filter(owner=self.owner, title='Refresh gagal').count(), 1)

    def test_server_validation_keeps_draft_focus_and_escape_restores_trigger(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                page = browser.new_page()
                page.context.add_cookies([{'name': 'sessionid', 'value': self.session, 'url': self.live_server_url}])
                page.goto(self.live_server_url + reverse('request_board:request_board'))
                trigger = page.get_by_role('link', name='Buat Permintaan').first
                dialog = self.open_form(page, 'Draft tetap')
                deadline = dialog.locator('[name=expires_at]')
                deadline.fill('2020-01-01T10:00')
                dialog.get_by_role('button', name='Simpan Permintaan').click()
                expect(deadline).to_have_attribute('aria-invalid', 'true')
                expect(deadline).to_be_focused()
                expect(dialog.locator('[name=title]')).to_have_value('Draft tetap')
                expect(page.locator('[data-create-pending]')).to_be_hidden()
                page.keyboard.press('Escape')
                expect(dialog).not_to_be_visible()
                expect(trigger).to_be_focused()
                trigger.click()
                expect(dialog.locator('[name=title]')).to_have_value('Draft tetap')
            finally:
                browser.close()
        self.assertFalse(Request.objects.filter(title='Draft tetap').exists())

    def test_deleted_committed_create_disables_retry_for_same_key(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                page = browser.new_page()
                page.context.add_cookies([{'name': 'sessionid', 'value': self.session, 'url': self.live_server_url}])
                page.goto(self.live_server_url + reverse('request_board:request_board'))
                dialog = self.open_form(page, 'Sudah dihapus')
                page.evaluate("""() => {
                    const original = window.fetch;
                    window.fetch = async (url, options) => {
                        const response = await original(url, options);
                        if (options?.method === 'POST' && String(url).endsWith('/new/')) {
                            const data = await response.json();
                            await original(`/board/${data.request_id}/delete/`, {
                                method: 'POST', body: options.body, headers: {'X-Requested-With': 'XMLHttpRequest'},
                                credentials: 'same-origin'
                            });
                            throw new TypeError('lost response');
                        }
                        return response;
                    };
                }""")
                dialog.get_by_role('button', name='Simpan Permintaan').click()
                expect(dialog.locator('[data-create-recovery]')).to_contain_text('kemudian dihapus')
                expect(dialog.get_by_role('button', name='Coba simpan lagi')).to_be_disabled()
            finally:
                browser.close()
        self.assertFalse(Request.objects.filter(title='Sudah dihapus').exists())

    def test_create_refresh_cannot_unlock_another_pending_mutation(self):
        other = get_user_model().objects.create_user(username='other-create-race')
        for title in ('Tawaran satu', 'Tawaran dua'):
            Request.objects.create(owner=other, title=title, food_item='Roti', quantity=1, unit='porsi')
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                page = browser.new_page()
                page.context.add_cookies([{'name': 'sessionid', 'value': self.session, 'url': self.live_server_url}])
                page.goto(self.live_server_url + reverse('request_board:request_board'))
                dialog = self.open_form(page, 'Create race')
                page.evaluate("""() => {
                    const original = window.fetch;
                    let firstGet = true;
                    window.offerPosts = 0;
                    window.fetch = (url, options) => {
                        if (options?.method === 'POST' && String(url).endsWith('/offer/')) {
                            window.offerPosts++;
                            return new Promise(resolve => { window.releaseOffer = () => resolve(original(url, options)); });
                        }
                        if (options?.method !== 'POST' && firstGet) {
                            firstGet = false;
                            return original(url, options).then(response => new Promise(resolve => {
                                window.releaseCreateRefresh = () => resolve(response);
                            }));
                        }
                        return original(url, options);
                    };
                }""")
                dialog.get_by_role('button', name='Simpan Permintaan').click()
                page.wait_for_function('typeof window.releaseCreateRefresh === "function"')
                page.locator('.request-board__card').filter(has_text='Tawaran satu').get_by_role('button', name='Saya Punya!').click()
                page.wait_for_function('typeof window.releaseOffer === "function"')
                page.evaluate('window.releaseCreateRefresh()')
                expect(page.locator('[data-toast-region]')).to_contain_text('berhasil dibuat')
                page.locator('.request-board__card').filter(has_text='Tawaran dua').get_by_role('button', name='Saya Punya!').click()
                self.assertEqual(page.evaluate('window.offerPosts'), 1)
                page.evaluate('window.releaseOffer()')
            finally:
                browser.close()

    def test_late_refresh_from_first_create_does_not_unlock_second_create(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                page = browser.new_page()
                page.context.add_cookies([{'name': 'sessionid', 'value': self.session, 'url': self.live_server_url}])
                page.goto(self.live_server_url + reverse('request_board:request_board'))
                dialog = self.open_form(page, 'Pertama')
                page.evaluate("""() => {
                    const original = window.fetch;
                    let createCount = 0;
                    let firstGet = true;
                    window.fetch = (url, options) => {
                        if (options?.method === 'POST') {
                            createCount++;
                            if (createCount === 2) return new Promise(resolve => {
                                window.releaseSecondCreate = () => resolve(original(url, options));
                            });
                            return original(url, options);
                        }
                        if (firstGet) {
                            firstGet = false;
                            return original(url, options).then(response => new Promise(resolve => {
                                window.releaseFirstRefresh = () => resolve(response);
                            }));
                        }
                        return original(url, options);
                    };
                }""")
                dialog.get_by_role('button', name='Simpan Permintaan').click()
                page.wait_for_function('typeof window.releaseFirstRefresh === "function"')
                dialog = self.open_form(page, 'Kedua')
                button = dialog.get_by_role('button', name='Simpan Permintaan')
                expect(button.locator('img')).to_have_count(1)
                button.click()
                page.wait_for_function('typeof window.releaseSecondCreate === "function"')
                page.evaluate('window.releaseFirstRefresh()')
                page.wait_for_timeout(100)
                expect(button).to_be_disabled()
                page.evaluate('window.releaseSecondCreate()')
                expect(dialog).not_to_be_visible()
            finally:
                browser.close()

    def test_without_javascript_create_link_and_form_still_work(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                context = browser.new_context(java_script_enabled=False)
                context.add_cookies([{'name': 'sessionid', 'value': self.session, 'url': self.live_server_url}])
                page = context.new_page()
                page.goto(self.live_server_url + reverse('request_board:request_board'))
                page.get_by_role('link', name='Buat Permintaan').first.click()
                page.locator('[name=title]').fill('Fallback')
                page.locator('[name=food_item]').fill('Beras')
                page.locator('[name=quantity]').fill('1')
                page.locator('[name=unit]').select_option('kg')
                page.get_by_role('button', name='Simpan Permintaan').click()
                page.wait_for_url(self.live_server_url + reverse('request_board:request_board'))
                expect(page.locator('[data-board-results]')).to_contain_text('Fallback')
            finally:
                browser.close()
        self.assertEqual(Request.objects.filter(title='Fallback').count(), 1)

    def test_standalone_form_with_javascript_does_not_navigate_after_success(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                page = browser.new_page()
                page.context.add_cookies([{'name': 'sessionid', 'value': self.session, 'url': self.live_server_url}])
                url = self.live_server_url + reverse('request_board:request_create')
                page.goto(url)
                page.locator('[name=title]').fill('Mandiri')
                page.locator('[name=food_item]').fill('Beras')
                page.locator('[name=quantity]').fill('1')
                page.locator('[name=unit]').select_option('kg')
                page.get_by_role('button', name='Simpan Permintaan').click()
                expect(page.locator('[data-toast-region]')).to_contain_text('berhasil dibuat', timeout=3000)
                self.assertEqual(page.url, url)
                expect(page.locator('[name=title]')).to_have_value('')
            finally:
                browser.close()
        self.assertEqual(Request.objects.filter(title='Mandiri').count(), 1)
