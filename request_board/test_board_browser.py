"""Live browser coverage for progressive enhancement (optional Playwright)."""
import os
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse

from request_board.models import Request

try:
    from playwright.sync_api import sync_playwright, expect
except ImportError:
    sync_playwright = None


@skipUnless(sync_playwright, "Optional Playwright browser tooling is not installed")
class BoardAjaxBrowserTests(StaticLiveServerTestCase):
    def test_offers_long_title_wraps_without_horizontal_overflow(self):
        owner = get_user_model().objects.create_user(username='browser-long-title-owner')
        title = 'Bantuan ' + 'sayuransegar' * 9
        item = Request.objects.create(owner=owner, title=title, food_item='Roti', quantity=1, unit='porsi')
        client = Client()
        client.force_login(owner)
        session = client.cookies['sessionid'].value
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                for width in (375, 768, 1280):
                    with self.subTest(width=width):
                        context = browser.new_context(viewport={'width': width, 'height': 900})
                        context.add_cookies([{'name': 'sessionid', 'value': session, 'url': self.live_server_url}])
                        page = context.new_page()
                        response = page.goto(self.live_server_url + reverse('request_board:request_offers', args=[item.pk]))
                        self.assertEqual(response.status, 200)
                        heading = page.locator('[data-board-offers] h1')
                        expect(heading).to_have_text('Penawaran untuk ' + title)
                        geometry = heading.evaluate('''element => {
                            const range = document.createRange();
                            range.selectNodeContents(element);
                            const bounds = element.getBoundingClientRect();
                            const rects = Array.from(range.getClientRects());
                            return {
                                viewport: document.documentElement.clientWidth,
                                scroll: document.documentElement.scrollWidth,
                                textFits: rects.every(rect => rect.left >= bounds.left - 1 && rect.right <= bounds.right + 1),
                                visible: getComputedStyle(element).overflow === 'visible' && getComputedStyle(element).textOverflow === 'clip',
                            };
                        }''')
                        self.assertLessEqual(geometry['scroll'], geometry['viewport'])
                        self.assertTrue(geometry['textFits'])
                        self.assertTrue(geometry['visible'])
                        context.close()
            finally:
                browser.close()

    def test_old_get_cannot_replace_newer_reset(self):
        owner = get_user_model().objects.create_user(username='browser-stale-owner')
        Request.objects.create(owner=owner, title='Hasil asli', food_item='Roti', quantity=1, unit='porsi')
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                page = browser.new_page(viewport={'width': 1280, 'height': 900})
                page.goto(self.live_server_url + reverse('request_board:request_board'))
                page.evaluate("""() => {
                    const original = window.fetch;
                    window.fetch = (url, options) => String(url).includes('q=lama')
                      ? new Promise(resolve => { window.resolveOld = resolve; })
                      : original(url, options).then(response => { window.resetResolved = true; return response; });
                    const form = document.querySelector('[data-filter-form]');
                    form.elements.q.value = 'lama';
                    form.requestSubmit();
                }""")
                page.locator('[data-filter-form] [data-board-reset]').first.click()
                page.wait_for_function("window.resetResolved && document.querySelector('[name=q]').value === ''")
                expect(page.locator('.request-board__card-title')).to_have_text('Hasil asli')
                page.evaluate("""async () => {
                    window.resolveOld(new Response('<p id=stale>kadaluwarsa</p>', {status: 200}));
                    await new Promise(requestAnimationFrame);
                }""")
                expect(page.locator('#stale')).to_have_count(0)
                expect(page.locator('.request-board__card-title')).to_have_text('Hasil asli')
            finally:
                browser.close()

    def test_create_without_javascript_uses_html_fallback(self):
        owner = get_user_model().objects.create_user(username='browser-no-js-owner')
        client = Client()
        client.force_login(owner)
        session = client.cookies['sessionid'].value
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                context = browser.new_context(java_script_enabled=False)
                context.add_cookies([{'name': 'sessionid', 'value': session, 'url': self.live_server_url}])
                page = context.new_page()
                page.goto(self.live_server_url + reverse('request_board:request_create'))
                page.locator('[name=title]').fill('Fallback HTML')
                page.locator('[name=food_item]').fill('Nasi')
                page.locator('[name=quantity]').fill('1')
                page.locator('[name=unit]').select_option('porsi')
                page.get_by_role('button', name='Simpan Permintaan').click()
                expect(page.locator('.request-board__card-title')).to_have_text('Fallback HTML')
            finally:
                browser.close()

    def test_delete_last_page_keeps_filter_and_blocks_double_submit(self):
        owner = get_user_model().objects.create_user(username="browser-page-owner")
        Request.objects.bulk_create([
            Request(owner=owner, title=f"Khusus {index}", food_item="Roti", quantity=1, unit="porsi")
            for index in range(41)
        ])
        client = Client()
        client.force_login(owner)
        session = client.cookies['sessionid'].value
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                context = browser.new_context()
                context.add_cookies([{'name': 'sessionid', 'value': session, 'url': self.live_server_url}])
                page = context.new_page()
                posts = []
                page.on('request', lambda request: posts.append(request.url) if request.method == 'POST' and request.url.endswith('/delete/') else None)
                page.goto(self.live_server_url + reverse('request_board:request_board') + '?q=Khusus&page=3')
                expect(page.locator('.request-board__card')).to_have_count(1)
                page.locator('.request-board__delete-summary').click()
                page.locator('dialog form').evaluate('(form) => { form.requestSubmit(); form.requestSubmit(); }')
                expect(page.locator('.request-board__card')).to_have_count(20)
                self.assertIn('page=2', page.url)
                self.assertIn('q=Khusus', page.url)
                self.assertEqual(len(posts), 1)
            finally:
                browser.close()

    def test_create_errors_network_csrf_and_expired_session(self):
        owner = get_user_model().objects.create_user(username="browser-errors-owner")
        client = Client()
        client.force_login(owner)
        session = client.cookies["sessionid"].value
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get("PLAYWRIGHT_CHROMIUM_CHANNEL") or None)
            try:
                context = browser.new_context()
                context.add_cookies([{"name": "sessionid", "value": session, "url": self.live_server_url}])
                page = context.new_page()
                page.goto(self.live_server_url + reverse('request_board:request_create'))
                page.locator('[name=title]').fill('<b>tetap</b>')
                page.get_by_role('button', name='Simpan Permintaan').click()
                expect(page.locator('[name=food_item]')).to_have_attribute('aria-invalid', 'true')
                expect(page.locator('[name=title]')).to_have_value('<b>tetap</b>')
                page.locator('[name=food_item]').fill('Roti')
                page.get_by_role('button', name='Simpan Permintaan').click()
                expect(page.locator('[name=quantity]')).to_have_attribute('aria-invalid', 'true')
                self.assertFalse(page.locator('[name=food_item]').evaluate("el => (el.getAttribute('aria-describedby') || '').includes('ajax-error-')"))
                page.locator('[name=quantity]').fill('1')
                page.locator('[name=unit]').select_option('porsi')
                page.route('**/board/new/', lambda route: route.abort())
                page.get_by_role('button', name='Simpan Permintaan').click()
                expect(page.locator('[data-ajax-notice]')).to_contain_text('Gagal')
                expect(page.get_by_role('button', name='Simpan Permintaan')).to_be_enabled()
                expect(page.locator('[name=title]')).to_have_value('<b>tetap</b>')
                page.unroute('**/board/new/')
                page.locator('[data-board-create] [name=csrfmiddlewaretoken]').evaluate("el => el.value = 'invalid'")
                page.get_by_role('button', name='Simpan Permintaan').click()
                expect(page.locator('[data-ajax-notice]')).to_contain_text('CSRF')
                page.reload()
                cookies = [cookie for cookie in context.cookies() if cookie['name'] != 'sessionid']
                context.clear_cookies()
                context.add_cookies(cookies)
                page.get_by_role('button', name='Simpan Permintaan').click()
                expect(page.locator('[data-ajax-notice] a')).to_have_text('Masuk')
            finally:
                browser.close()

    def test_owner_and_donors_complete_flow_without_board_navigation(self):
        users = get_user_model()
        owner = users.objects.create_user(username="browser-ajax-owner")
        first = users.objects.create_user(username="browser-ajax-first")
        second = users.objects.create_user(username="browser-ajax-second")
        sessions = []
        third = users.objects.create_user(username="browser-ajax-third")
        for user in (owner, first, second, third):
            client = Client()
            client.force_login(user)
            sessions.append(client.cookies["sessionid"].value)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get("PLAYWRIGHT_CHROMIUM_CHANNEL") or None)
            try:
                pages = []
                for session in sessions:
                    context = browser.new_context()
                    context.add_cookies([{"name": "sessionid", "value": session, "url": self.live_server_url}])
                    pages.append(context.new_page())
                owner_page, first_page, second_page, third_page = pages
                owner_page.goto(self.live_server_url + reverse("request_board:request_create"))
                owner_page.locator('[name=title]').fill('Bantuan browser')
                owner_page.locator('[name=food_item]').fill('Roti')
                owner_page.locator('[name=quantity]').fill('1')
                owner_page.locator('[name=unit]').select_option('porsi')
                owner_page.get_by_role('button', name='Simpan Permintaan').click()
                expect(owner_page.locator('.request-board__card-title')).to_have_text('Bantuan browser')
                for page in (first_page, second_page):
                    page.goto(self.live_server_url + reverse('request_board:request_board'))
                    page.get_by_role('button', name='Saya Punya!').click()
                    expect(page.get_by_text('Sudah menawarkan')).to_be_visible()
                owner_page.reload()
                owner_page.get_by_role('link', name='Lihat semua penawaran').click()
                second_row = owner_page.locator('li.request-board__donor').filter(has_text='browser-ajax-second')
                second_row.get_by_role('button', name='Pilih penawar').click()
                expect(second_row.get_by_text('Terpilih')).to_be_visible()
                third_page.goto(self.live_server_url + reverse('request_board:request_board'))
                expect(third_page.get_by_text('Penawaran ditutup', exact=True)).to_be_visible()
                owner_page.get_by_role('link', name='Kembali ke papan permintaan').click()
                owner_page.evaluate('window.boardMarker = 17')
                owner_page.get_by_role('button', name='Tandai Terpenuhi').click()
                expect(owner_page.get_by_text('Penawar terpilih: browser-ajax-second')).to_be_visible()
                self.assertEqual(owner_page.evaluate('window.boardMarker'), 17)
                owner_page.locator('#id_status').select_option('fulfilled')
                owner_page.get_by_role('button', name='Terapkan').click()
                expect(owner_page.locator('.request-board__card-title')).to_have_text('Bantuan browser')
                owner_page.locator('.request-board__delete-summary').click()
                owner_page.get_by_role('button', name='Ya, hapus').click()
                expect(owner_page.get_by_text('Tidak Ada Hasil')).to_be_visible()
                self.assertEqual(owner_page.evaluate('window.boardMarker'), 17)
                self.assertIn('status=fulfilled', owner_page.url)
            finally:
                browser.close()
