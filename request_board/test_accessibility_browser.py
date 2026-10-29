import os
from unittest import skipUnless
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse
from request_board.models import Request, RequestOffer
try:
    from playwright.sync_api import sync_playwright, expect
except ImportError:
    sync_playwright = None


@skipUnless(sync_playwright, 'Playwright unavailable')
class AccessibilityBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username='access-owner')
        self.donor = get_user_model().objects.create_user(username='access-donor')
        self.item = Request.objects.create(owner=self.owner, title='Beras warga', food_item='Beras', quantity=1, unit='kg', status='offered')
        RequestOffer.objects.create(request=self.item, offered_by=self.donor)
        Request.objects.bulk_create([Request(owner=self.owner, title=f'Item {n}', food_item='Beras', quantity=1, unit='kg') for n in range(21)])
        self.client.force_login(self.owner)
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
        self.context = self.browser.new_context(viewport={'width': 1280, 'height': 900})
        self.context.add_cookies([{'name': 'sessionid', 'value': self.client.cookies['sessionid'].value, 'url': self.live_server_url}])
        self.page = self.context.new_page()

    def tearDown(self):
        self.context.close()
        self.browser.close()
        self.playwright.stop()

    def test_hints_and_ajax_errors_keep_focus_and_descriptions(self):
        p = self.page
        p.goto(self.live_server_url + reverse('request_board:request_create'))
        for name in ('title', 'food_item', 'quantity', 'description', 'location', 'expires_at'):
            control = p.locator(f'[name="{name}"]')
            ids = (control.get_attribute('aria-describedby') or '').split()
            self.assertTrue(any(p.locator(f'#{id}').count() and p.locator(f'#{id}').text_content().strip() for id in ids), name)
        p.locator('[name=title]').fill('')
        p.get_by_role('button', name='Simpan Permintaan').click()
        expect(p.locator('[name=title]')).to_be_focused()
        self.assertIn('Kolom ini wajib diisi', p.locator('#ajax-error-title').text_content())
        ids = p.locator('[name=title]').get_attribute('aria-describedby').split()
        self.assertTrue(any('hint' in id for id in ids))
        self.assertIn('ajax-error-title', ids)

    def test_delete_dialog_name_and_offer_button_include_person(self):
        p = self.page
        p.goto(self.live_server_url + reverse('request_board:request_board') + '?q=Beras+warga')
        p.locator('.request-board__card').filter(has_text='Beras warga').locator('.request-board__delete-summary').click()
        dialog = p.get_by_role('dialog', name='Hapus Beras warga?')
        self.assertTrue(dialog.is_visible())
        self.assertIn('tidak dapat dibatalkan', dialog.get_attribute('aria-describedby') and p.locator('#'+dialog.get_attribute('aria-describedby')).text_content())
        p.keyboard.press('Escape')
        expect(p.locator('.request-board__card').filter(has_text='Beras warga').locator('.request-board__delete-summary')).to_be_focused()
        p.goto(self.live_server_url + reverse('request_board:request_offers', args=[self.item.pk]))
        self.assertEqual(p.get_by_role('button', name='Pilih penawar access-donor').count(), 1)

    def test_pagination_focus_and_live_result_counts(self):
        p = self.page
        p.goto(self.live_server_url + reverse('request_board:request_board'))
        region = p.locator('[data-board-status]')
        self.assertEqual(region.count(), 1)
        self.assertEqual(region.get_attribute('aria-live'), 'polite')
        p.get_by_role('link', name='Berikutnya').click()
        expect(p.locator('[data-board-results-focus]')).to_be_focused()
        expect(region).to_contain_text('Halaman 2')
        self.assertEqual(p.locator('[data-board-results]').get_attribute('aria-busy'), 'false')

    def test_focus_indicator_has_two_contrasting_colors(self):
        p = self.page
        p.goto(self.live_server_url + reverse('request_board:request_create'))
        p.locator('[name=title]').focus()
        style = p.locator('[name=title]').evaluate('(e) => { let s=getComputedStyle(e); return {outline:s.outlineColor, shadow:s.boxShadow}; }')
        self.assertIn('rgb(35, 54, 26)', style['outline'])
        self.assertNotEqual(style['shadow'], 'none')
    def test_mobile_filter_announces_loading_and_ignores_stale_response(self):
        p = self.page
        p.set_viewport_size({'width': 375, 'height': 800})
        p.goto(self.live_server_url + reverse('request_board:request_board'))
        p.evaluate('''() => {
          const original = window.fetch;
          window.release = [];
          window.fetch = (...args) => original(...args).then(response => new Promise(resolve => window.release.push(() => resolve(response))));
        }''')
        p.locator('[data-filter-open]').click()
        form = p.locator('[data-filter-form]')
        form.locator('[name=title]').fill('Item')
        form.get_by_role('button', name='Terapkan', exact=True).filter(visible=True).click()
        p.wait_for_function('window.release.length === 1')
        expect(form.locator('[data-filter-status]')).to_have_text('Memuat permintaan.')
        self.assertEqual(p.locator('[data-board-results]').get_attribute('aria-busy'), 'true')
        form.locator('[name=title]').fill('missing')
        form.get_by_role('button', name='Terapkan', exact=True).filter(visible=True).click()
        p.wait_for_function('window.release.length === 2')
        p.evaluate('window.release[0]()')
        self.assertEqual(p.locator('[data-board-results]').get_attribute('aria-busy'), 'true')
        p.evaluate('window.release[1]()')
        expect(p.locator('[data-board-status]')).to_have_text('Tidak ada permintaan yang cocok.')
        expect(p.locator('[data-board-results]')).to_have_attribute('aria-busy', 'false')
        self.assertTrue(p.get_by_role('heading', name='Tidak Ada Hasil').is_visible())

    def test_server_validation_preserves_hint_and_error_relationship(self):
        context = self.browser.new_context(java_script_enabled=False)
        context.add_cookies([{'name': 'sessionid', 'value': self.client.cookies['sessionid'].value, 'url': self.live_server_url}])
        p = context.new_page()
        p.goto(self.live_server_url + reverse('request_board:request_create'))
        p.locator('[name=title]').fill('')
        p.get_by_role('button', name='Simpan Permintaan').click()
        expect(p.locator('[name=title]')).to_have_attribute('aria-invalid', 'true')
        control = p.locator('[name=title]')
        ids = control.get_attribute('aria-describedby').split()
        self.assertIn('id_title_hint', ids)
        self.assertIn('id_title_error', ids)
        self.assertIn('Kolom ini wajib diisi', p.locator('#id_title_error').text_content())
        context.close()

    def test_nonfield_and_network_errors_receive_focus(self):
        p = self.page
        p.goto(self.live_server_url + reverse('request_board:request_create'))
        p.locator('[name=title]').fill('Draft aksesibel')
        p.locator('[name=food_item]').fill('Beras')
        p.locator('[name=quantity]').fill('1')
        p.locator('[name=unit]').select_option('kg')
        p.route('**/board/new/', lambda route: route.fulfill(status=400, content_type='application/json', body='{"ok":false,"fields":{"__all__":["Tidak dapat menyimpan sekarang."]}}'))
        p.get_by_role('button', name='Simpan Permintaan').click()
        notice = p.locator('[data-ajax-notice]')
        expect(notice).to_be_focused()
        expect(notice).to_contain_text('Tidak dapat menyimpan sekarang.')
        p.unroute('**/board/new/')
        p.route('**/board/new/', lambda route: route.abort())
        p.get_by_role('button', name='Simpan Permintaan').click()
        expect(p.locator('[data-create-recovery]')).to_contain_text('belum dapat dipastikan')
        expect(p.locator('[name=title]')).to_have_value('Draft aksesibel')
