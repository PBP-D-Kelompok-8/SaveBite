"""Delayed create responses exercise optimistic state and safe recovery."""
import os
from contextlib import contextmanager
from pathlib import Path
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

    @contextmanager
    def board_page(self, query='', width=1440):
        """Every scenario runs against Django's isolated test database."""
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                page = browser.new_page(viewport={'width': width, 'height': 900})
                page.context.add_cookies([{'name': 'sessionid', 'value': self.session, 'url': self.live_server_url}])
                page.goto(self.live_server_url + reverse('request_board:request_board') + query)
                yield page
            finally:
                browser.close()

    def pending_card(self, page, state=None):
        cards = page.locator('[data-create-pending] article[data-create-card]')
        expect(cards).to_have_count(1)
        if state:
            expect(cards).to_have_attribute('data-state', state)
        return cards

    def hold_create(self, page, refresh=False):
        page.evaluate("""holdRefresh => {
            const original = window.fetch;
            window.posts = [];
            window.fetch = (url, options) => {
                if (options?.method === 'POST') {
                    window.posts.push({key: options.headers['Idempotency-Key'], body: [...options.body.entries()]});
                    return new Promise(resolve => { window.releaseCreate = () => resolve(original(url, options)); });
                }
                if (holdRefresh && options?.headers?.['X-Requested-With']) {
                    return new Promise(resolve => { window.releaseRefresh = () => resolve(original(url, options)); });
                }
                return original(url, options);
            };
        }""", refresh)

    def test_pending_card_escapes_long_content_and_omits_empty_fields(self):
        owner_text = '<b>Pemohon & aman</b>'
        get_user_model().objects.filter(pk=self.owner.pk).update(username=owner_text)
        for width in (320, 375, 1440):
            with self.subTest(width=width), self.board_page(width=width) as page:
                title = '<img src=x onerror=alert(1)>' + 'W' * 80
                description = '<script>window.injected=true</script>' + 'D' * 900
                dialog = self.open_form(page, title)
                dialog.locator('[name=description]').fill(description)
                self.hold_create(page)
                dialog.get_by_role('button', name='Simpan Permintaan').click()
                card = self.pending_card(page, 'saving')
                expect(card.locator('[data-create-card-title]')).to_have_text(title)
                expect(card.locator('[data-create-card-description]')).to_have_text(description)
                expect(card.locator('[data-create-card-owner]')).to_have_text(owner_text)
                expect(card.locator('script, img[src=x], b')).to_have_count(0)
                self.assertIsNone(page.evaluate('window.injected'))
                expect(card.locator('[data-create-card-location-row]')).to_be_hidden()
                # Production form pre-fills the required deadline; keep that rule.
                expect(card.locator('[data-create-card-expires]')).to_be_visible()
                self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'))
                self.assertTrue(card.evaluate('(e) => e.scrollWidth <= e.clientWidth'))
                page.evaluate('window.releaseCreate()')
                expect(page.locator('[data-create-pending] article')).to_have_count(0)
                # A second draft with all optional fields empty must omit their rows.
                dialog = self.open_form(page, f'Kosong {width}')
                # Exercise an absent snapshot value without changing production
                # validation: an omitted control uses the backend's existing default.
                dialog.locator('[name=expires_at]').evaluate("e => { e.value = ''; e.disabled = true; }")
                dialog.get_by_role('button', name='Simpan Permintaan').click()
                card = self.pending_card(page, 'saving')
                expect(card.locator('[data-create-card-description-row]')).to_be_hidden()
                expect(card.locator('[data-create-card-location-row]')).to_be_hidden()
                expect(card.locator('[data-create-card-expires]')).to_be_hidden()
                page.evaluate('window.releaseCreate()')
                expect(page.locator('[data-create-pending] article')).to_have_count(0)

    def test_pending_card_is_visible_after_modal_closes_before_post_response(self):
        with self.board_page() as page:
            dialog = self.open_form(page, 'Klik ganda')
            dialog.locator('[name=location]').fill('Posko uji')
            dialog.locator('[name=description]').fill('Draft lengkap')
            dialog.locator('[name=expires_at]').fill('2099-12-01T12:30')
            self.hold_create(page)
            # Two submissions in the same event turn also cover rapid double clicks.
            dialog.locator('form').evaluate('f => { f.requestSubmit(); f.requestSubmit(); }')
            expect(dialog).not_to_be_visible()
            card = self.pending_card(page, 'saving')
            expect(card).to_be_visible()
            expect(card.locator('[data-create-card-location]')).to_have_text('Posko uji')
            expect(card.locator('[data-create-card-description]')).to_have_text('Draft lengkap')
            expect(card.locator('time')).to_have_attribute('datetime', '2099-12-01T12:30')
            expect(card.locator('time')).to_have_text('Batas waktu: 01 Dec 2099, 12:30 WIB')
            self.assertEqual(len(page.evaluate('window.posts')), 1)
            expect(dialog.locator('[name=title]')).to_have_value('Klik ganda')
            expect(page.locator('[data-toast-region] [role=status]')).to_have_count(0)
            page.evaluate('window.releaseCreate()')
            expect(page.locator('[data-board-results]')).to_contain_text('Klik ganda')
        self.assertEqual(Request.objects.filter(title='Klik ganda').count(), 1)

    def test_success_reconciles_pending_card_with_server_results(self):
        with self.board_page() as page:
            dialog = self.open_form(page, 'Rekonsiliasi')
            self.hold_create(page, refresh=True)
            dialog.get_by_role('button', name='Simpan Permintaan').click()
            card = self.pending_card(page, 'saving')
            local_id = card.get_attribute('id')
            page.evaluate('window.releaseCreate()')
            card = self.pending_card(page, 'refreshing')
            expect(card.locator('[data-create-card-state]')).to_have_text('Tersimpan, memperbarui daftar…')
            expect(page.locator('[data-board-results]')).not_to_contain_text('Rekonsiliasi')
            expect(page.locator('[data-toast-region] [role=status]')).to_have_count(0)
            page.wait_for_function('typeof window.releaseRefresh === "function"')
            page.evaluate('window.releaseRefresh()')
            expect(page.locator('[data-create-pending] article')).to_have_count(0)
            heading = page.locator('[data-board-results] h2').filter(has_text='Rekonsiliasi')
            self.assertRegex(heading.get_attribute('id'), r'^request-title-\d+$')
            request_id = int(heading.get_attribute('id').removeprefix('request-title-'))
            expect(heading).to_have_text('Rekonsiliasi')
            expect(page.locator(f'#{local_id}')).to_have_count(0)
            expect(page.locator('[data-board-results] article').filter(has_text='Rekonsiliasi')).to_have_count(1)
            self.assertEqual(len(page.evaluate('window.posts')), 1)
        self.assertEqual(Request.objects.get(title='Rekonsiliasi').pk, request_id)

    def test_rejected_create_removes_pending_card_and_restores_form(self):
        for status in (400, 401, 403):
            with self.subTest(status=status), self.board_page() as page:
                dialog = self.open_form(page, f'Ditolak {status}')
                values = {'food_item': 'Beras merah', 'quantity': '2.50', 'unit': 'kg',
                          'location': 'Posko', 'description': 'Jangan hilang', 'expires_at': '2099-12-01T12:30'}
                for name, value in values.items():
                    if name != 'unit':
                        dialog.locator(f'[name={name}]').fill(value)
                page.evaluate("""status => {
                    window.fetch = () => new Promise(resolve => { window.rejectCreate = () => resolve(new Response(
                        JSON.stringify({ok:false, message:'Penolakan uji', fields: status === 400 ? {food_item:['Periksa bahan']} : {}}),
                        {status, headers:{'Content-Type':'application/json'}})); });
                }""", status)
                dialog.get_by_role('button', name='Simpan Permintaan').click()
                self.pending_card(page, 'saving')
                page.evaluate('window.rejectCreate()')
                expect(page.locator('[data-create-pending] article')).to_have_count(0)
                expect(dialog).to_be_visible()
                expect(dialog.locator('[name=title]')).to_have_value(f'Ditolak {status}')
                for name, value in values.items():
                    expect(dialog.locator(f'[name={name}]')).to_have_value(value)
                target = dialog.locator('[name=food_item]' if status == 400 else '[data-create-recovery]')
                expect(target).to_be_focused()
                expect(dialog.locator('[name=title]')).to_be_enabled()
                expect(page.locator('[data-toast-region] [role=status]')).to_have_count(0)
        self.assertFalse(Request.objects.filter(title__startswith='Ditolak').exists())

    def test_uncertain_create_keeps_one_card_and_original_key(self):
        for failure in ('network', '500', 'unreadable'):
            with self.subTest(failure=failure), self.board_page() as page:
                dialog = self.open_form(page, f'Belum pasti {failure}')
                page.evaluate("""failure => {
                    const original = window.fetch;
                    window.posts = [];
                    window.fetch = (url, options) => {
                        if (options?.method === 'POST') {
                            window.posts.push({key:options.headers['Idempotency-Key'], body:[...options.body.entries()]});
                            if (window.posts.length === 1) {
                                if (failure === 'network') return Promise.reject(new TypeError('offline'));
                                return Promise.resolve(new Response(failure === '500' ? 'error' : '{broken',
                                    {status:failure === '500' ? 500 : 200, headers:{'Content-Type':'application/json'}}));
                            }
                            return new Promise(resolve => { window.releaseRetry = () => resolve(original(url, options)); });
                        }
                        return original(url, options);
                    };
                }""", failure)
                dialog.get_by_role('button', name='Simpan Permintaan').click()
                expect(dialog.locator('[data-create-recovery]')).to_contain_text('kunci yang sama')
                card = self.pending_card(page, 'uncertain')
                local_id = card.get_attribute('id')
                expect(card.locator('[data-create-card-state]')).to_have_text('Belum terkonfirmasi')
                expect(dialog.locator('[name=title]')).to_be_disabled()
                expect(page.locator('[data-toast-region] [role=status]')).to_have_count(0)
                # Even a script modifying disabled fields must not alter the captured payload.
                dialog.locator('[name=title]').evaluate("e => { e.value = 'Tidak boleh terkirim'; }")
                dialog.get_by_role('button', name='Coba simpan lagi').click()
                card = self.pending_card(page, 'saving')
                expect(card).to_have_attribute('id', local_id)
                posts = page.evaluate('window.posts')
                self.assertEqual(len(posts), 2)
                self.assertEqual(posts[0], posts[1])
                page.evaluate('window.releaseRetry()')
                expect(page.locator('[data-create-pending] article')).to_have_count(0)
                expect(page.locator('[data-board-results]')).to_contain_text(f'Belum pasti {failure}')
        self.assertFalse(Request.objects.filter(title='Tidak boleh terkirim').exists())

    def test_recovery_does_not_stack_dialogs_or_overwrite_another_attempt(self):
        with self.board_page() as page:
            dialog = self.open_form(page, 'Draft terlambat')
            page.evaluate("""() => {
                const original = window.fetch;
                window.posts = 0;
                window.fetch = (url, options) => {
                    if (options?.method === 'POST') {
                        window.posts++;
                        return new Promise(resolve => { window.rejectCreate = () => resolve(new Response(
                            JSON.stringify({ok:false, message:'Penolakan terlambat'}),
                            {status:400, headers:{'Content-Type':'application/json'}})); });
                    }
                    return original(url, options);
                };
            }""")
            dialog.get_by_role('button', name='Simpan Permintaan').click()
            self.pending_card(page, 'saving')
            # Real delete dialog, opened but never submitted; existing request stays intact.
            page.locator('.request-board__delete-summary').first.click()
            other_dialog = page.locator('dialog[open]:not([data-create-dialog])')
            expect(other_dialog).to_have_count(1)
            page.evaluate('window.rejectCreate()')
            alert = other_dialog.locator('[data-ajax-notice]')
            expect(alert).to_contain_text('Pemulihan permintaan tersedia')
            expect(alert).to_be_focused()
            expect(page.locator('[data-create-pending] article')).to_have_count(0)
            expect(dialog).not_to_be_visible()
            expect(other_dialog).to_have_count(1)
            expect(dialog.locator('[name=title]')).to_have_value('Draft terlambat')
            other_dialog.evaluate('d => d.close()')
            page.get_by_role('link', name='Buat Permintaan').first.click()
            expect(dialog).to_be_visible()
            expect(dialog.locator('[data-create-recovery]')).to_be_focused()
            expect(dialog.locator('[data-create-recovery]')).to_contain_text('Penolakan terlambat')
            expect(dialog.locator('[name=title]')).to_have_value('Draft terlambat')
            self.assertEqual(page.evaluate('window.posts'), 1)
        self.assertEqual(Request.objects.filter(owner=self.owner).count(), 1)

    def test_create_with_filters_and_page_two_keeps_server_order(self):
        for index in range(24):
            Request.objects.create(owner=self.owner, title=f'Filter {index:02d}', food_item='Beras', quantity=1, unit='kg')
        query = '?title=Filter&status=pending&sort=oldest&page=2'
        with self.board_page(query) as page:
            url = page.url
            results = page.locator('[data-board-results]')
            titles = results.locator('h2').all_text_contents()
            summary = results.locator('[data-board-result-summary]')
            before = summary.evaluate('(e) => ({...e.dataset})')
            self.assertEqual(before['total'], '24')
            self.assertEqual(before['page'], '2')
            dialog = self.open_form(page, 'Tidak cocok')
            self.hold_create(page)
            dialog.get_by_role('button', name='Simpan Permintaan').click()
            self.pending_card(page, 'saving')
            self.assertEqual(summary.evaluate('(e) => ({...e.dataset})'), before)
            self.assertEqual(results.locator('h2').all_text_contents(), titles)
            page.evaluate('window.releaseCreate()')
            expect(page.locator('[data-toast-region]')).to_contain_text('Permintaan berhasil dibuat. Daftar mengikuti filter dan halaman aktif.')
            self.assertEqual(results.locator('h2').all_text_contents(), titles)
            self.assertEqual(summary.evaluate('(e) => ({...e.dataset})'), before)
            self.assertEqual(page.url, url)
            expect(page.locator('[name=title]').first).to_have_value('Filter')
            expect(page.locator('[name=sort]').first).to_have_value('oldest')
            expect(results).not_to_contain_text('Tidak cocok')

    def test_pending_create_on_empty_board_does_not_forge_result_counts(self):
        Request.objects.filter(owner=self.owner).delete()
        with self.board_page(width=375) as page:
            results = page.locator('[data-board-results]')
            before = results.inner_html()
            dialog = self.open_form(page, 'Pertama di board kosong')
            self.hold_create(page)
            dialog.get_by_role('button', name='Simpan Permintaan').click()
            self.pending_card(page, 'saving')
            self.assertEqual(results.inner_html(), before)
            expect(results.locator('[data-board-result-summary]')).to_have_attribute('data-total', '0')
            expect(results.locator('nav')).to_have_count(0)
            page.evaluate('window.releaseCreate()')
            expect(results.locator('[data-board-result-summary]')).to_have_attribute('data-total', '1')
            expect(page.locator('[data-create-pending] article')).to_have_count(0)

    def test_client_validation_keyboard_focus_keeps_modal_and_has_no_card(self):
        with self.board_page(width=375) as page:
            dialog = self.open_form(page, '')
            dialog.locator('form').evaluate('f => f.requestSubmit()')
            expect(dialog).to_be_visible()
            expect(dialog.locator('[name=title]')).to_be_focused()
            expect(page.locator('[data-create-pending] article')).to_have_count(0)
            page.keyboard.press('Escape')
            expect(dialog).not_to_be_visible()
            expect(page.get_by_role('link', name='Buat Permintaan').first).to_be_focused()

    def test_final_desktop_mobile_recovery_and_visual_inspection(self):
        """One inspection pass, synthetic data only; optional screenshots for review."""
        for width in (375, 1440):
            for mode in ('success', 'validation', 'session', 'timeout-before', 'lost-after', 'refresh-failed'):
                with self.subTest(width=width, mode=mode), self.board_page(width=width) as page:
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    if mode == 'timeout-before':
                        page.clock.install()
                    title = f'Inspeksi {width} {mode}'
                    dialog = self.open_form(page, title)
                    dialog.locator('[name=description]').fill('<img src=x onerror=alert(1)>' + ' panjang' * 100)
                    page.evaluate("""mode => {
                        const original = window.fetch;
                        window.restoreFetch = () => { window.fetch = original; };
                        window.posts = 0;
                        window.fetch = (url, options) => {
                            if (options?.method === 'POST') {
                                window.posts++;
                                if (mode === 'timeout-before') return new Promise((resolve, reject) => {
                                    options.signal.addEventListener('abort', () => reject(new DOMException('Timeout','AbortError')));
                                });
                                if (mode === 'lost-after') return original(url, options).then(() => { throw new TypeError('lost'); });
                                return new Promise(resolve => { window.releaseInspection = () => {
                                    if (mode === 'validation' || mode === 'session') resolve(new Response(JSON.stringify({
                                        ok:false, message:'Inspeksi ditolak', fields:mode === 'validation' ? {food_item:['Periksa bahan']} : {},
                                        login_url:mode === 'session' ? '/login/' : null}),
                                        {status:mode === 'validation' ? 400 : 401, headers:{'Content-Type':'application/json'}}));
                                    else resolve(original(url, options));
                                }; });
                            }
                            if (mode === 'refresh-failed' && options?.headers?.['X-Requested-With']) return Promise.reject(new TypeError('GET offline'));
                            return original(url, options);
                        };
                    }""", mode)
                    dialog.get_by_role('button', name='Simpan Permintaan').click()
                    if mode not in ('timeout-before', 'lost-after'):
                        card = self.pending_card(page, 'saving')
                        expect(dialog).not_to_be_visible()
                        expect(card.locator('[data-create-card-description]')).to_contain_text('<img src=x onerror=alert(1)>')
                        self.capture_inspection(page, f'{width}-{mode}-saving')
                        page.evaluate('window.releaseInspection()')
                    if mode in ('validation', 'session'):
                        expect(dialog).to_be_visible()
                        expect(page.locator('[data-create-pending] article')).to_have_count(0)
                        expect(dialog.locator('[name=title]')).to_have_value(title)
                        expect(dialog.locator('[name=food_item]' if mode == 'validation' else '[data-create-recovery]')).to_be_focused()
                        self.capture_inspection(page, f'{width}-{mode}-recovery')
                        page.keyboard.press('Escape')
                    elif mode == 'timeout-before':
                        page.wait_for_function('window.posts === 1')
                        page.clock.fast_forward(15000)
                        expect(dialog.locator('[data-create-recovery]')).to_contain_text('kunci yang sama')
                        self.pending_card(page, 'uncertain')
                        self.capture_inspection(page, f'{width}-{mode}-recovery')
                        page.evaluate('window.restoreFetch()')
                        dialog.get_by_role('button', name='Coba simpan lagi').click()
                        expect(page.locator('[data-board-results]')).to_contain_text(title)
                        expect(page.locator('[data-create-pending] article')).to_have_count(0)
                    elif mode == 'refresh-failed':
                        self.pending_card(page, 'saved')
                        expect(page.get_by_role('alert')).to_contain_text('papan gagal diperbarui')
                        self.assertEqual(page.evaluate('window.posts'), 1)
                        self.capture_inspection(page, f'{width}-{mode}-saved')
                    else:
                        expect(page.locator('[data-board-results]')).to_contain_text(title)
                        expect(page.locator('[data-create-pending] article')).to_have_count(0)
                        self.assertEqual(page.evaluate('window.posts'), 1)
                        self.assertTrue(page.evaluate("window.dispatchEvent(new Event('beforeunload', {cancelable:true}))"))
                        self.capture_inspection(page, f'{width}-{mode}-server')
                    # Native filter and enhanced delete still work after these transitions.
                    page.locator('[data-filter-open]').click()
                    expect(page.locator('[data-filter-form]')).to_be_visible()
                    page.locator('[data-filter-open]').click()
                    page.locator('.request-board__delete-summary').first.click()
                    delete_dialog = page.locator('dialog[open]:not([data-create-dialog])')
                    expect(delete_dialog).to_be_visible()
                    page.keyboard.press('Escape')
                    expect(delete_dialog).to_have_count(0)
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                    self.assertEqual(errors, [])

    def capture_inspection(self, page, name):
        directory = os.environ.get('OPTIMISTIC_INSPECTION_DIR')
        if directory:
            target = Path(directory)
            target.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(target / f'{name}.png'), full_page=True)

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
                local_id = self.pending_card(page, 'uncertain').get_attribute('id')
                dialog.get_by_role('button', name='Coba simpan lagi').click()
                expect(dialog).not_to_be_visible()
                keys = page.evaluate('window.keys')
                self.assertEqual(len(keys), 2)
                self.assertEqual(keys[0], keys[1])
                expect(page.locator(f'#{local_id}')).to_have_count(0)
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
                self.pending_card(page, 'uncertain')
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
                self.pending_card(page, 'uncertain')
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
                notice = dialog.locator('[data-create-recovery]')
                expect(notice).to_contain_text('Silakan masuk kembali')
                expect(notice.get_by_role('link', name='Masuk kembali')).to_have_attribute('target', '_blank')
                expect(page.locator('[data-create-pending] article')).to_have_count(0)
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
                expect(dialog.locator('[data-create-recovery]')).to_contain_text('Silakan masuk kembali')
                expect(page.locator('[data-create-pending] article')).to_have_count(0)
                page.evaluate("document.cookie = 'csrftoken=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA; path=/'")
                dialog.get_by_role('button', name='Simpan Permintaan').click()
                expect(dialog).not_to_be_visible()
                expect(page.locator('[data-board-results]')).to_contain_text('CSRF baru')
                expect(page.locator('[data-create-pending] article')).to_have_count(0)
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
                local_id = self.pending_card(page, 'uncertain').get_attribute('id')
                page.context.add_cookies([{'name': 'sessionid', 'value': other_session, 'url': self.live_server_url}])
                dialog.get_by_role('button', name='Periksa status').click()
                expect(dialog.locator('[data-create-recovery]')).to_contain_text('akun pembuat draft')
                expect(dialog.get_by_role('button', name='Coba simpan lagi')).to_be_disabled()
                expect(self.pending_card(page, 'uncertain')).to_have_attribute('id', local_id)
                page.context.add_cookies([{'name': 'sessionid', 'value': self.session, 'url': self.live_server_url}])
                dialog.get_by_role('button', name='Periksa status').click()
                expect(dialog.get_by_role('button', name='Coba simpan lagi')).to_be_enabled()
                dialog.get_by_role('button', name='Coba simpan lagi').click()
                expect(dialog).not_to_be_visible()
                expect(page.locator(f'#{local_id}')).to_have_count(0)
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
                    card = self.pending_card(page, 'saving')
                    self.assertRegex(card.get_attribute('id'), r'^temp-')
                    expect(card.locator('[data-create-card-food]')).to_have_text('Beras')
                    expect(card.locator('[data-create-card-quantity]')).to_have_text('2.00 Kg')
                    expect(card.locator('[data-create-card-owner]')).to_have_text(self.owner.username)
                    expect(dialog).not_to_be_visible()
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
                local_id = self.pending_card(page, 'saving').get_attribute('id')
                page.evaluate('window.releaseOldFilter()')
                expect(page.locator('[data-create-pending]')).to_contain_text('Sedang menyimpan')
                expect(self.pending_card(page, 'saving')).to_have_attribute('id', local_id)
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
                self.pending_card(page, 'uncertain')
                dialog.get_by_role('button', name='Coba simpan lagi').click()
                expect(dialog).not_to_be_visible()
                expect(page.locator('[data-toast-region]')).to_contain_text('berhasil dibuat')
                expect(page.locator('[data-create-pending] article')).to_have_count(0)
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
                expect(page.locator('[data-create-pending] article')).to_have_count(0)
                expect(page.locator('[data-toast-region]')).to_contain_text('berhasil dibuat')
            finally:
                browser.close()
        self.assertEqual(Request.objects.filter(owner=self.owner, title='Hasil tersimpan').count(), 1)

    def test_confirmed_create_keeps_saved_summary_if_board_refresh_fails(self):
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
                card = self.pending_card(page, 'saved')
                expect(card.locator('[data-create-card-state]')).to_have_text('Tersimpan')
                expect(card.locator('a, button, form')).to_have_count(0)
                expect(dialog.locator('[name=title]')).to_have_value('')
                expect(dialog.get_by_role('button', name='Coba simpan lagi')).to_have_count(0)
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
                expect(page.locator('[data-create-pending] article')).to_have_count(0)
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
                self.pending_card(page, 'refreshing')
                page.locator('.request-board__card').filter(has_text='Tawaran satu').get_by_role('button', name='Saya Punya!').click()
                page.wait_for_function('typeof window.releaseOffer === "function"')
                page.evaluate('window.releaseCreateRefresh()')
                self.pending_card(page, 'saved')
                page.locator('.request-board__card').filter(has_text='Tawaran dua').get_by_role('button', name='Saya Punya!').click()
                self.assertEqual(page.evaluate('window.offerPosts'), 1)
                page.evaluate('window.releaseOffer()')
                expect(page.locator('[data-create-pending] article')).to_have_count(0)
            finally:
                browser.close()

    def test_late_refresh_from_first_create_does_not_unlock_second_create(self):
        self.assert_late_create_refresh_is_owned(lose_first_response=False)

    def test_late_status_confirmed_refresh_does_not_unlock_second_create(self):
        self.assert_late_create_refresh_is_owned(lose_first_response=True)

    def assert_late_create_refresh_is_owned(self, lose_first_response):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                page = browser.new_page()
                page.context.add_cookies([{'name': 'sessionid', 'value': self.session, 'url': self.live_server_url}])
                page.goto(self.live_server_url + reverse('request_board:request_board'))
                dialog = self.open_form(page, 'Pertama')
                page.evaluate("""loseFirst => {
                    const original = window.fetch;
                    let createCount = 0;
                    let firstGet = true;
                    window.createKeys = [];
                    window.fetch = (url, options) => {
                        if (options?.method === 'POST') {
                            createCount++;
                            window.createKeys.push(options.headers['Idempotency-Key']);
                            if (createCount === 1 && loseFirst) return original(url, options).then(() => {
                                throw new TypeError('lost committed response');
                            });
                            if (createCount === 2) return new Promise(resolve => {
                                window.releaseSecondCreate = () => resolve(original(url, options));
                            });
                            return original(url, options);
                        }
                        if (firstGet && !String(url).includes('/new/status/')) {
                            firstGet = false;
                            return original(url, options).then(response => new Promise(resolve => {
                                window.releaseFirstRefresh = () => resolve(response);
                            }));
                        }
                        return original(url, options);
                    };
                }""", lose_first_response)
                dialog.get_by_role('button', name='Simpan Permintaan').click()
                page.wait_for_function('typeof window.releaseFirstRefresh === "function"')
                dialog = self.open_form(page, 'Kedua')
                button = dialog.locator('button[type=submit]')
                expect(button.locator('img')).to_have_count(1)
                button.click()
                page.wait_for_function('typeof window.releaseSecondCreate === "function"')
                local_id = page.locator('[data-create-card][data-state=saving]').get_attribute('id')
                keys_before = page.evaluate('window.createKeys')
                self.assertEqual(len(keys_before), 2)
                self.assertNotEqual(keys_before[0], keys_before[1])
                page.evaluate('window.releaseFirstRefresh()')
                expect(page.locator('[data-create-card][data-state=saved]')).to_have_count(1)
                expect(button).to_be_disabled()
                expect(page.locator(f'#{local_id}')).to_have_attribute('data-state', 'saving')
                expect(page.locator(f'#{local_id} [data-create-card-title]')).to_have_text('Kedua')
                self.assertEqual(page.evaluate('window.createKeys'), keys_before)
                page.evaluate('window.releaseSecondCreate()')
                expect(page.locator('[data-board-results]')).to_contain_text('Kedua')
                expect(page.locator('[data-board-results]')).to_contain_text('Pertama')
                expect(page.locator('[data-create-pending] article')).to_have_count(0)
                dialog = self.open_form(page, 'Ketiga belum dikirim')
                expect(dialog.locator('[name=title]')).to_be_enabled()
            finally:
                browser.close()

    def test_superseded_confirmed_offer_stays_locked_after_create_becomes_uncertain(self):
        other = get_user_model().objects.create_user(username='confirmed-offer-owner')
        item = Request.objects.create(owner=other, title='Tawaran sudah tersimpan', food_item='Roti', quantity=1, unit='porsi')
        with self.board_page() as page:
            # Cache the create form before intercepting the offer refresh.
            dialog = self.open_form(page, 'Create belum pasti setelah tawaran')
            page.keyboard.press('Escape')
            page.evaluate("""() => {
                const original = window.fetch;
                let held = false;
                window.offerPosts = 0;
                window.fetch = (url, options) => {
                    if (options?.method === 'POST') {
                        if (String(url).endsWith('/offer/')) {
                            window.offerPosts++;
                            return original(url, options);
                        }
                        return Promise.reject(new TypeError('offline create'));
                    }
                    if (!held && options?.headers?.['X-Requested-With'] && !String(url).includes('/new/status/')) {
                        held = true;
                        return original(url, options).then(response => new Promise(resolve => {
                            window.releaseOfferRefresh = () => resolve(response);
                        }));
                    }
                    return original(url, options);
                };
            }""")
            card = page.locator('[data-board-results] article').filter(has_text=item.title)
            button = card.locator('button[type=submit]').first
            button.click()
            page.wait_for_function('typeof window.releaseOfferRefresh === "function"')
            expect(button).to_have_text('Penawaran tersimpan')
            page.get_by_role('link', name='Buat Permintaan').first.click()
            dialog.get_by_role('button', name='Simpan Permintaan').click()
            expect(dialog.locator('[data-create-recovery]')).to_contain_text('kunci yang sama')
            self.pending_card(page, 'uncertain')
            page.evaluate('window.releaseOfferRefresh()')
            # Completion removes the pending marker, but must not unlock stale controls.
            expect(card.locator('form[data-pending]')).to_have_count(0)
            expect(button).to_be_disabled()
            button.evaluate('e => e.click()')
            self.assertEqual(page.evaluate('window.offerPosts'), 1)
        self.assertEqual(item.offers.count(), 1)

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
