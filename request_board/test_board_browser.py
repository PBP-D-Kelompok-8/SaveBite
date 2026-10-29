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
    def test_reapply_pending_and_after_reset_preserves_latest_full_state(self):
        from urllib.parse import parse_qs, urlsplit

        owner = get_user_model().objects.create_user(username='reapply-full-owner')
        Request.objects.create(owner=owner, title='Target', food_item='Nasi', location='Depok', quantity=1, unit='kg')
        Request.objects.create(owner=owner, title='Distractor', food_item='Roti', location='Bogor', quantity=1, unit='kg')
        client = Client()
        client.force_login(owner)
        session = client.cookies['sessionid'].value
        first = {'title': 'Target', 'food_item': 'Nasi', 'status': 'pending', 'location': 'Depok', 'sort': 'oldest', 'q': 'a', 'mine': 'on'}
        latest = {'title': 'Distractor', 'food_item': 'Roti', 'status': 'pending', 'location': 'Bogor', 'sort': 'oldest', 'q': 'a', 'mine': 'on'}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                for width in (375, 768, 1280):
                    for mode in ('reapply', 'reset_then_apply', 'reset_then_release'):
                        with self.subTest(width=width, mode=mode):
                            context = browser.new_context(viewport={'width': width, 'height': 667})
                            context.add_cookies([{'name': 'sessionid', 'value': session, 'url': self.live_server_url}])
                            page = context.new_page()
                            try:
                                page.goto(self.live_server_url + reverse('request_board:request_board') + '?q=a&mine=on')
                                page.evaluate('''() => {
                                    const original = window.fetch;
                                    window.sent = [];
                                    window.fetch = (...args) => {
                                        window.sent.push(String(args[0]));
                                        return original(...args).then(response => !window.releaseFirst
                                            ? new Promise(resolve => { window.releaseFirst = () => resolve(response); })
                                            : response);
                                    };
                                }''')
                                page.get_by_role('button', name='Filter', exact=True).click()
                                form = page.locator('[data-filter-form]')
                                form.evaluate('(f, values) => { for (const [name, value] of Object.entries(values)) f.elements[name].value = value; }', first)
                                page.locator('#id_title').press('Enter')
                                page.wait_for_function('typeof window.releaseFirst === "function"')
                                if mode != 'reapply':
                                    page.get_by_role('link', name='Reset', exact=True).filter(visible=True).click()
                                    page.wait_for_url(self.live_server_url + reverse('request_board:request_board'))
                                    expect(page.locator('.request-board__card-title')).to_have_count(2)
                                    if mode == 'reset_then_release':
                                        page.evaluate('window.releaseFirst()')
                                        page.wait_for_timeout(250)
                                        self.assertEqual(page.url, self.live_server_url + reverse('request_board:request_board'))
                                        expect(page.locator('.request-board__card-title')).to_have_count(2)
                                        cleared = {'title': '', 'food_item': '', 'status': '', 'location': '', 'sort': 'newest', 'q': '', 'mine': ''}
                                        self.assertEqual(form.evaluate('(f, names) => Object.fromEntries(names.map(n => [n, f.elements[n].value]))', list(cleared)), cleared)
                                form.evaluate('(f, values) => { for (const [name, value] of Object.entries(values)) f.elements[name].value = value; }', latest)
                                buttons = form.locator('button[type=submit]')
                                self.assertEqual(buttons.evaluate_all('es => es.map(e => e.disabled)'), [False])
                                page.get_by_role('button', name='Terapkan', exact=True).filter(visible=True).click()
                                page.wait_for_url('**/*title=Distractor*', timeout=3000)
                                expect(page.locator('.request-board__card-title')).to_have_text(['Distractor'])
                                sent = page.evaluate('window.sent')
                                self.assertEqual(len(sent), 2 if mode == 'reapply' else 3)
                                self.assertEqual(parse_qs(urlsplit(sent[0]).query), {k: [v] for k, v in first.items()})
                                self.assertEqual(parse_qs(urlsplit(sent[-1]).query), {k: [v] for k, v in latest.items()})
                                if mode != 'reset_then_release':
                                    page.evaluate('window.releaseFirst()')
                                page.wait_for_timeout(250)
                                self.assertEqual(parse_qs(urlsplit(page.url).query), {k: [v] for k, v in latest.items()})
                                expect(page.locator('.request-board__card-title')).to_have_text(['Distractor'])
                                self.assertEqual(form.evaluate('(f, names) => Object.fromEntries(names.map(n => [n, f.elements[n].value]))', list(latest)), latest)
                                expect(form).to_be_visible()
                                self.assertEqual(buttons.evaluate_all('es => es.map(e => e.disabled)'), [False])
                            finally:
                                context.close()
            finally:
                browser.close()

    def test_filtered_pagination_and_history_preserve_title_and_food(self):
        owner = get_user_model().objects.create_user(username='board-filter-history')
        Request.objects.bulk_create([
            Request(owner=owner, title=f'Target {index}', food_item='Susu & Roti', quantity=1, unit='kg')
            for index in range(21)
        ])
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                page = browser.new_page(viewport={'width': 1280, 'height': 667})
                page.goto(self.live_server_url + reverse('request_board:request_board'))
                page.get_by_role('button', name='Filter', exact=True).click()
                page.locator('#id_title').fill('Target')
                page.locator('#id_food_item').select_option('Susu & Roti')
                page.get_by_role('button', name='Terapkan', exact=True).click()
                expect(page.locator('.request-board__card')).to_have_count(20)
                page.get_by_role('link', name='Berikutnya').click()
                expect(page.locator('.request-board__card')).to_have_count(1)
                self.assertIn('title=Target', page.url)
                self.assertIn('food_item=Susu+%26+Roti', page.url)
                page.go_back()
                expect(page.locator('.request-board__card')).to_have_count(20)
                page.go_forward()
                expect(page.locator('.request-board__card')).to_have_count(1)
                expect(page.locator('#id_title')).to_have_value('Target')
                expect(page.locator('#id_food_item')).to_have_value('Susu & Roti')
            finally:
                browser.close()

    def test_filter_keeps_draft_on_resize_and_escape(self):
        owner = get_user_model().objects.create_user(username='inline-filter-draft')
        Request.objects.create(owner=owner, title='Bantuan beras', food_item='Nasi', quantity=1, unit='kg')
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                page = browser.new_page(viewport={'width': 375, 'height': 667})
                page.goto(self.live_server_url + reverse('request_board:request_board'))
                page.get_by_role('button', name='Filter', exact=True).click()
                for width in (375, 768, 1280):
                    page.locator('#id_title').fill('draft')
                    page.set_viewport_size({'width': width, 'height': 667})
                    page.keyboard.press('Escape')
                    expect(page.locator('[data-filter-form]')).not_to_be_visible()
                    expect(page.get_by_role('button', name='Filter', exact=True)).to_be_focused()
                    page.get_by_role('button', name='Filter', exact=True).click()
                    expect(page.locator('[data-filter-form]')).to_be_visible()
                    expect(page.locator('#id_title')).to_have_value('draft')
                    expect(page.locator('[data-filter-form]')).to_have_count(1)
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width)
            finally:
                browser.close()

    def test_filter_without_javascript_uses_get_and_reset(self):
        owner = get_user_model().objects.create_user(username='board-filter-no-js')
        Request.objects.create(owner=owner, title='Bantuan beras', food_item='Nasi', quantity=1, unit='kg')
        Request.objects.create(owner=owner, title='Bantuan sayur', food_item='Sayur', quantity=1, unit='kg')
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                context = browser.new_context(java_script_enabled=False, viewport={'width': 375, 'height': 667})
                page = context.new_page()
                page.goto(self.live_server_url + reverse('request_board:request_board'))
                page.get_by_role('button', name='Filter', exact=True).click()
                page.locator('#id_title').fill('beras')
                page.locator('#id_food_item').select_option('Nasi')
                page.locator('#id_title').press('Enter')
                expect(page.locator('.request-board__card-title')).to_have_text(['Bantuan beras'])
                self.assertIn('title=beras', page.url)
                page.get_by_role('button', name='Filter', exact=True).click()
                page.get_by_role('link', name='Reset', exact=True).filter(visible=True).click()
                expect(page.locator('.request-board__card-title')).to_have_count(2)
                response = page.goto(self.live_server_url + reverse('request_board:request_board') + '?title=' + 'x' * 121)
                self.assertEqual(response.status, 400)
                expect(page.locator('.errorlist')).to_be_visible()
            finally:
                browser.close()

    def test_filter_pagination_without_javascript_preserves_values(self):
        owner = get_user_model().objects.create_user(username='board-no-js-page')
        Request.objects.bulk_create([
            Request(owner=owner, title=f'Target {index}', food_item='Nasi', quantity=1, unit='kg')
            for index in range(21)
        ])
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                context = browser.new_context(java_script_enabled=False)
                page = context.new_page()
                page.goto(self.live_server_url + reverse('request_board:request_board'))
                page.get_by_role('button', name='Filter', exact=True).click()
                page.locator('#id_title').fill('Target')
                page.locator('#id_food_item').select_option('Nasi')
                page.locator('#id_title').press('Enter')
                page.get_by_role('link', name='Berikutnya').click()
                expect(page.locator('.request-board__card')).to_have_count(1)
                expect(page.locator('#id_title')).to_have_value('Target')
                expect(page.locator('#id_food_item')).to_have_value('Nasi')
            finally:
                browser.close()

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
                        expect(page.locator('[data-board-offers] h1')).to_have_text('Semua penawaran')
                        heading = page.locator('.request-board__offer-request-title')
                        expect(heading).to_have_text(title)
                        geometry = heading.evaluate('''element => {
                            const range = document.createRange();
                            range.selectNodeContents(element);
                            const bounds = element.getBoundingClientRect();
                            const rects = Array.from(range.getClientRects());
                            return {
                                viewport: document.documentElement.clientWidth,
                                scroll: document.documentElement.scrollWidth,
                                textFits: rects.every(rect => rect.left >= bounds.left - 1 && rect.right <= bounds.right + 1 && rect.top >= bounds.top - 1 && rect.bottom <= bounds.bottom + 1),
                                visible: getComputedStyle(element).overflow === 'visible' && getComputedStyle(element).textOverflow === 'clip',
                                heightFits: element.scrollHeight <= element.clientHeight,
                            };
                        }''')
                        self.assertLessEqual(geometry['scroll'], geometry['viewport'])
                        self.assertTrue(geometry['textFits'])
                        self.assertTrue(geometry['visible'])
                        self.assertTrue(geometry['heightFits'])
                        context.close()
            finally:
                browser.close()

    def test_offers_refresh_failure_reports_success_without_reposting(self):
        from request_board.services import offer_request
        owner = get_user_model().objects.create_user(username='refresh-owner')
        donor = get_user_model().objects.create_user(username='refresh-donor')
        item = Request.objects.create(owner=owner, title='Refresh', food_item='Nasi', quantity=1, unit='porsi')
        offer, _ = offer_request(actor=donor, request_id=item.pk)
        client = Client()
        client.force_login(owner)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                context = browser.new_context()
                context.add_cookies([{'name': 'sessionid', 'value': client.cookies['sessionid'].value, 'url': self.live_server_url}])
                page = context.new_page()
                page.goto(self.live_server_url + reverse('request_board:request_offers', args=[item.pk]))
                posts = []
                page.on('request', lambda req: posts.append(req.url) if req.method == 'POST' and req.url.endswith('/select/') else None)
                page.route('**/board/*/offers/', lambda route: route.abort() if route.request.method == 'GET' else route.continue_())
                page.get_by_role('button', name='Pilih penawar').click()
                expect(page.locator('[data-ajax-notice]')).to_have_text('Aksi berhasil, tetapi daftar penawaran gagal diperbarui. Muat ulang halaman.')
                self.assertEqual(len(posts), 1)
            finally:
                browser.close()
        item.refresh_from_db()
        self.assertEqual(item.selected_offer_id, offer.pk)
        self.assertNotEqual(item.status, 'fulfilled')

    def test_offers_post_csrf_failure_is_not_reported_as_success(self):
        from request_board.services import offer_request
        owner = get_user_model().objects.create_user(username='offers-csrf-owner')
        donor = get_user_model().objects.create_user(username='offers-csrf-donor')
        item = Request.objects.create(owner=owner, title='CSRF', food_item='Nasi', quantity=1, unit='porsi')
        offer_request(actor=donor, request_id=item.pk)
        client = Client()
        client.force_login(owner)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                context = browser.new_context()
                context.add_cookies([{'name': 'sessionid', 'value': client.cookies['sessionid'].value, 'url': self.live_server_url}])
                page = context.new_page()
                page.goto(self.live_server_url + reverse('request_board:request_offers', args=[item.pk]))
                page.locator('[data-board-offers] [name=csrfmiddlewaretoken]').evaluate("el => el.value = 'invalid'")
                page.get_by_role('button', name='Pilih penawar').click()
                expect(page.locator('[data-ajax-notice]')).to_contain_text('CSRF')
                expect(page.locator('[data-ajax-notice]')).not_to_contain_text('Aksi berhasil')
                expect(page.get_by_role('button', name='Pilih penawar')).to_be_enabled()
            finally:
                browser.close()
        item.refresh_from_db()
        self.assertIsNone(item.selected_offer_id)

    def test_offers_refresh_failure_modes_after_real_post(self):
        from request_board.services import offer_request
        owner = get_user_model().objects.create_user(username='refresh-modes-owner')
        donor = get_user_model().objects.create_user(username='refresh-modes-donor')
        items = []
        for mode in ('http', 'redirect', 'missing'):
            item = Request.objects.create(owner=owner, title=mode, food_item='Nasi', quantity=1, unit='porsi')
            offer, _ = offer_request(actor=donor, request_id=item.pk)
            items.append((mode, item, offer))
        client = Client()
        client.force_login(owner)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                for mode, item, offer in items:
                    with self.subTest(mode=mode):
                        context = browser.new_context()
                        context.add_cookies([{'name': 'sessionid', 'value': client.cookies['sessionid'].value, 'url': self.live_server_url}])
                        page = context.new_page()
                        page.goto(self.live_server_url + reverse('request_board:request_offers', args=[item.pk]))
                        posts = []
                        page.on('request', lambda req: posts.append(req.url) if req.method == 'POST' and req.url.endswith('/select/') else None)
                        if mode == 'http':
                            page.route('**/board/*/offers/', lambda route: route.fulfill(status=500, body='error'))
                        elif mode == 'redirect':
                            page.route('**/board/*/offers/', lambda route: route.fulfill(status=302, headers={'location': '/login/'}))
                        else:
                            page.route('**/board/*/offers/', lambda route: route.fulfill(status=200, content_type='text/html', body='<html><body>missing</body></html>'))
                        page.get_by_role('button', name='Pilih penawar').click()
                        expect(page.locator('[data-ajax-notice]')).to_have_text('Aksi berhasil, tetapi daftar penawaran gagal diperbarui. Muat ulang halaman.')
                        self.assertEqual(len(posts), 1)
                        context.close()
            finally:
                browser.close()
        for mode, item, offer in items:
            item.refresh_from_db()
            self.assertEqual(item.selected_offer_id, offer.pk, mode)

    def test_offers_layout_states_and_selection(self):
        from pathlib import Path
        from request_board.services import offer_request, select_request_offer, fulfill_request
        users = get_user_model()
        owner = users.objects.create_user(username='offers-layout-owner')
        donor = users.objects.create_user(username='long-donor-' + 'username' * 12)
        other = users.objects.create_user(username='offers-layout-other')
        title = 'Bantuan ' + 'sayuransegar' * 9
        item = Request.objects.create(owner=owner, title=title, food_item='Nasi', quantity=10, unit='porsi')
        chosen, _ = offer_request(actor=donor, request_id=item.pk)
        offer_request(actor=other, request_id=item.pk)
        legacy = Request.objects.create(owner=owner, title='Legacy', food_item='Nasi', quantity=1, unit='porsi')
        offer_request(actor=other, request_id=legacy.pk)
        Request.objects.filter(pk=legacy.pk).update(status='fulfilled')
        truly_empty = Request.objects.create(owner=owner, title='Belum ada', food_item='Nasi', quantity=1, unit='porsi')
        select_request_offer(actor=owner, request_id=item.pk, offer_id=chosen.pk)
        fulfilled = Request.objects.create(owner=owner, title='Selesai', food_item='Nasi', quantity=1, unit='porsi')
        done, _ = offer_request(actor=other, request_id=fulfilled.pk)
        select_request_offer(actor=owner, request_id=fulfilled.pk, offer_id=done.pk)
        fulfill_request(actor=owner, request_id=fulfilled.pk)
        client = Client()
        client.force_login(owner)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                for width, inset in ((375, 20), (768, 32), (1280, 80)):
                    with self.subTest(width=width):
                        context = browser.new_context(viewport={'width': width, 'height': 900})
                        context.add_cookies([{'name': 'sessionid', 'value': client.cookies['sessionid'].value, 'url': self.live_server_url}])
                        page = context.new_page()
                        page.goto(self.live_server_url + reverse('request_board:request_offers', args=[item.pk]))
                        page.evaluate('document.fonts.ready')
                        expect(page.locator('.request-board__offer-request-title')).to_have_text(title)
                        expect(page.locator('.request-board__offer-banner')).to_contain_text(donor.username)
                        self.assertEqual(page.locator('.request-board__offer-banner p').evaluate('el => getComputedStyle(el).fontSize'), '16px')
                        row = page.locator('li.request-board__donor').filter(has_text=donor.username)
                        badge = row.locator('.request-board__offer-selected')
                        expect(badge).to_have_text('Terpilih')
                        self.assertIsNone(badge.get_attribute('tabindex'))
                        self.assertEqual(row.locator('form').count(), 0)
                        geometry = page.evaluate('''() => {
                            const root = document.querySelector('[data-board-offers]');
                            const badge = document.querySelector('.request-board__offer-selected');
                            const range = document.createRange(); range.selectNodeContents(badge);
                            const b = badge.getBoundingClientRect(), t = range.getBoundingClientRect();
                            return {scroll: document.documentElement.scrollWidth, width: document.documentElement.clientWidth,
                                left: root.getBoundingClientRect().left + parseFloat(getComputedStyle(root).paddingLeft),
                                badge: [b.width,b.height], dx: Math.abs((b.left+b.right-t.left-t.right)/2), dy: Math.abs((b.top+b.bottom-t.top-t.bottom)/2)};
                        }''')
                        self.assertLessEqual(geometry['scroll'], geometry['width'])
                        self.assertAlmostEqual(geometry['left'], inset, delta=1)
                        self.assertEqual(geometry['badge'], [108, 32])
                        self.assertLessEqual(geometry['dx'], 2)
                        self.assertLessEqual(geometry['dy'], 2)
                        if width == 375:
                            self.assertGreater(badge.bounding_box()['y'], row.locator('.request-board__offer-identity').bounding_box()['y'])
                            action = page.get_by_role('button', name='Ganti pilihan')
                            self.assertAlmostEqual(action.bounding_box()['width'], action.locator('..').locator('..').bounding_box()['width'] - 32, delta=1)
                        screenshot_dir = os.environ.get('OFFERS_TASK3_SCREENSHOTS')
                        if screenshot_dir:
                            path = Path(screenshot_dir) / f'offers-selected-long-{width}.png'
                            if path.exists():
                                raise AssertionError(f'Refusing to overwrite {path}')
                            page.screenshot(path=str(path), full_page=True)
                        page.get_by_role('button', name='Ganti pilihan').focus()
                        page.keyboard.press('Enter')
                        expect(page.locator('[data-offer-selection]')).to_be_focused()
                        expect(page.locator('[data-offer-selection]')).to_contain_text(other.username)
                        page.locator('li.request-board__donor').filter(has_text=donor.username).get_by_role('button', name='Ganti pilihan').click()
                        expect(page.locator('[data-offer-selection]')).to_contain_text(donor.username)
                        for obj, text in ((truly_empty, 'Belum ada penawaran'), (legacy, 'Daftar ini hanya untuk dilihat.'), (fulfilled, 'Daftar ini hanya untuk dilihat.')):
                            page.goto(self.live_server_url + reverse('request_board:request_offers', args=[obj.pk]))
                            expect(page.locator('[data-board-offers]')).to_contain_text(text)
                            if obj.pk != truly_empty.pk:
                                self.assertEqual(page.locator('[data-board-offers] form').count(), 0)
                            if screenshot_dir:
                                state = 'empty' if obj.pk == truly_empty.pk else ('legacy' if obj.pk == legacy.pk else 'fulfilled')
                                path = Path(screenshot_dir) / f'offers-{state}-{width}.png'
                                if path.exists():
                                    raise AssertionError(f'Refusing to overwrite {path}')
                                page.screenshot(path=str(path), full_page=True)
                        context.close()
            finally:
                browser.close()

    def test_offers_page_two_ajax_and_native_post_fallback(self):
        from request_board.services import offer_request
        users = get_user_model()
        owner = users.objects.create_user(username='offers-page-owner')
        donors = [users.objects.create_user(username=f'offers-page-donor-{n:02}') for n in range(21)]
        item = Request.objects.create(owner=owner, title='Halaman dua', food_item='Nasi', quantity=1, unit='porsi')
        for donor in donors:
            offer_request(actor=donor, request_id=item.pk)
        client = Client()
        client.force_login(owner)
        url = self.live_server_url + reverse('request_board:request_offers', args=[item.pk])
        session = client.cookies['sessionid'].value
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                context = browser.new_context()
                context.add_cookies([{'name': 'sessionid', 'value': session, 'url': self.live_server_url}])
                page = context.new_page()
                page.goto(url + '?page=2')
                expect(page.locator('li.request-board__donor')).to_have_count(1)
                button = page.get_by_role('button', name='Pilih penawar')
                button.focus()
                page.keyboard.press('Shift+Tab')
                expect(page.get_by_role('link', name='Kembali ke papan', exact=True)).to_be_focused()
                self.assertNotEqual(page.locator(':focus').evaluate('el => getComputedStyle(el).outlineStyle'), 'none')
                page.keyboard.press('Tab')
                expect(button).to_be_focused()
                page.keyboard.press('Space')
                expect(page.locator('[data-offer-selection]')).to_contain_text(donors[0].username)
                self.assertTrue(page.url.endswith('?page=2'))
                page.get_by_role('link', name='Sebelumnya').click()
                expect(page.locator('li.request-board__donor')).to_have_count(20)
                expect(page.locator('[data-offer-selection]')).to_contain_text(donors[0].username)
                context.close()
                context = browser.new_context(java_script_enabled=False)
                context.add_cookies([{'name': 'sessionid', 'value': session, 'url': self.live_server_url}])
                page = context.new_page()
                page.goto(url + '?page=2')
                page.get_by_role('link', name='Sebelumnya').click()
                page.get_by_role('button', name='Ganti pilihan').first.click()
                self.assertEqual(page.url, url)
                expect(page.locator('li.request-board__donor')).to_have_count(20)
                expect(page.locator('[data-offer-selection]')).not_to_contain_text(donors[0].username)
                context.close()
            finally:
                browser.close()
        item.refresh_from_db()
        self.assertNotEqual(item.selected_offer.offered_by_id, donors[0].pk)
        self.assertNotEqual(item.status, 'fulfilled')

    def test_old_get_cannot_replace_newer_reset(self):
        owner = get_user_model().objects.create_user(username='browser-stale-owner')
        Request.objects.create(owner=owner, title='Hasil asli', food_item='Roti', quantity=1, unit='porsi')
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                page = browser.new_page(viewport={'width': 1280, 'height': 900})
                page.goto(self.live_server_url + reverse('request_board:request_board'))
                page.get_by_role('button', name='Filter', exact=True).click()
                page.evaluate("""() => {
                    const original = window.fetch;
                    window.fetch = (url, options) => String(url).includes('q=lama')
                      ? new Promise(resolve => { window.resolveOld = resolve; })
                      : original(url, options).then(response => { window.resetResolved = true; return response; });
                    const form = document.querySelector('[data-filter-form]');
                    form.elements.q.value = 'lama';
                    form.elements.title.value = 'lama';
                    form.elements.food_item.value = 'Roti';
                    form.requestSubmit();
                }""")
                page.locator('[data-filter-form] [data-board-reset]').first.click()
                page.wait_for_function("window.resetResolved && document.querySelector('[name=q]').value === ''")
                expect(page.locator('[name=title]')).to_have_value('')
                expect(page.locator('[name=food_item]')).to_have_value('')
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
                page.goto(self.live_server_url + reverse('request_board:request_board') + '?q=Khusus&title=Khusus&food_item=Roti&page=3')
                expect(page.locator('.request-board__card')).to_have_count(1)
                page.locator('.request-board__delete-summary').click()
                page.locator('dialog form').evaluate('(form) => { form.requestSubmit(); form.requestSubmit(); }')
                expect(page.locator('.request-board__card')).to_have_count(20)
                self.assertIn('page=2', page.url)
                self.assertIn('q=Khusus', page.url)
                self.assertIn('title=Khusus', page.url)
                self.assertIn('food_item=Roti', page.url)
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
                expect(page.locator('[data-create-recovery]')).to_contain_text('belum dapat dipastikan')
                expect(page.get_by_role('button', name='Coba simpan lagi')).to_be_enabled()
                expect(page.locator('[name=title]')).to_have_value('<b>tetap</b>')
                page.unroute('**/board/new/')
                page.once('dialog', lambda warning: warning.accept())
                page.reload()
                page.locator('[name=title]').fill('<b>tetap</b>')
                page.locator('[name=food_item]').fill('Roti')
                page.locator('[name=quantity]').fill('1')
                page.locator('[name=unit]').select_option('porsi')
                page.locator('[data-board-create] [name=csrfmiddlewaretoken]').evaluate("el => el.value = 'invalid'")
                page.get_by_role('button', name='Simpan Permintaan').click()
                expect(page.locator('[data-ajax-notice]')).to_contain_text('CSRF')
                page.reload()
                page.locator('[name=title]').fill('<b>tetap</b>')
                page.locator('[name=food_item]').fill('Roti')
                page.locator('[name=quantity]').fill('1')
                page.locator('[name=unit]').select_option('porsi')
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
                expect(owner_page.locator('[data-toast-region]')).to_contain_text('berhasil dibuat')
                self.assertTrue(owner_page.url.endswith(reverse('request_board:request_create')))
                owner_page.get_by_role('link', name='Batal').click()
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
                expect(third_page.get_by_text('Penawaran baru ditutup karena penawar sudah dipilih.', exact=True)).to_be_visible()
                owner_page.get_by_role('link', name='Kembali ke papan', exact=True).first.click()
                owner_page.evaluate('window.boardMarker = 17')
                owner_page.get_by_role('button', name='Tandai Terpenuhi').click()
                expect(owner_page.get_by_text('Penawar terpilih: browser-ajax-second')).to_be_visible()
                self.assertEqual(owner_page.evaluate('window.boardMarker'), 17)
                owner_page.get_by_role('button', name='Filter', exact=True).click()
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
