import os
from unittest.mock import patch
from unittest import skipUnless
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from django.utils import timezone
from request_board.models import Request, RequestOffer

try:
    from playwright.sync_api import sync_playwright, expect
except ImportError:
    sync_playwright = None


class BoardRefinementTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="refinement-owner")
        self.donor = get_user_model().objects.create_user(username="refinement-donor")
        self.item = Request.objects.create(owner=self.owner, title="Beras warga", food_item="Beras", quantity=2, unit="kg", status="offered")
        self.offer = RequestOffer.objects.create(request=self.item, offered_by=self.donor)
        self.item.selected_offer = self.offer
        self.item.save()

    def test_selected_status_and_preview_match_initial_and_ajax_for_all_viewers(self):
        for actor in (self.owner, self.donor, None):
            if actor:
                self.client.force_login(actor)
            else:
                self.client.logout()
            for ajax in (False, True):
                with self.subTest(actor=actor, ajax=ajax):
                    response = self.client.get(reverse("request_board:request_board"), **({"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"} if ajax else {}))
                    self.assertContains(response, "Menunggu pemenuhan", count=1)
                    self.assertContains(response, "Penawaran baru ditutup karena penawar sudah dipilih.", count=1)
                    self.assertNotContains(response, "Penawaran terbaru")
                    self.assertNotContains(response, "Penawaran sudah ditutup.")
                    if actor == self.owner:
                        self.assertContains(response, "Penawar terpilih: refinement-donor", count=1)
                        self.assertContains(response, "Lihat semua penawaran")
                        self.assertContains(response, "Tandai Terpenuhi")
                    else:
                        self.assertNotContains(response, "Penawar terpilih:")
                        self.assertNotContains(response, "Saya Punya!")

    def test_terminal_status_takes_priority(self):
        self.client.force_login(self.owner)
        for status, expires_at, label in (("fulfilled", timezone.now()-timedelta(days=1), "Terpenuhi"), ("offered", timezone.now()-timedelta(days=1), "Kedaluwarsa")):
            self.item.status = status
            self.item.expires_at = expires_at
            self.item.save()
            for ajax in (False, True):
                response = self.client.get(reverse("request_board:request_board"), **({"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"} if ajax else {}))
                self.assertContains(response, label)
                self.assertNotContains(response, "Menunggu pemenuhan")
                self.assertNotContains(response, "Penawaran baru ditutup karena penawar sudah dipilih.")
                self.assertNotContains(response, "Tandai Terpenuhi")

    def test_filters_are_inline_even_on_empty_board(self):
        Request.objects.all().delete()
        response = self.client.get(reverse("request_board:request_board"))
        self.assertContains(response, "data-filter-form")
        self.assertContains(response, "data-filter-open")
        self.assertNotContains(response, "board-filter-dialog")


@skipUnless(sync_playwright, "Optional Playwright browser tooling is not installed")
class BoardModalBrowserTests(StaticLiveServerTestCase):
    setUp = BoardRefinementTests.setUp
    # Use real rendered responses and static assets without touching the local DB.
    @patch.dict(os.environ, {"DJANGO_ALLOW_ASYNC_UNSAFE": "true"})
    def test_modal_focus_cancel_confirm_and_ajax_replacement(self):
        self.client.force_login(self.owner)
        posts = []
        reject = [False]

        def serve(route):
            if route.request.method == 'POST':
                posts.append(route.request.url)
                if reject[0]:
                    route.fulfill(status=403, json={'ok': False, 'message': 'Hapus ditolak. Coba lagi.'})
                    return
            route.continue_()

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                for width in (375, 1280):
                    page = browser.new_page(viewport={'width': width, 'height': 850})
                    page.context.add_cookies([{'name': 'sessionid', 'value': self.client.cookies['sessionid'].value, 'url': self.live_server_url}])
                    page.route(self.live_server_url + '/**', serve)
                    page.goto(self.live_server_url + reverse('request_board:request_board'))
                    form = page.locator('[data-filter-form]')
                    expect(form).not_to_be_visible()
                    page.get_by_role('button', name='Filter', exact=True).click()
                    expect(form).to_be_visible()
                    # Replace cards through the existing AJAX GET first.
                    page.get_by_role('button', name='Terapkan', exact=True).click()
                    expect(page.locator('[data-board-results]')).to_have_attribute('aria-busy', 'false')
                    trigger = page.locator('.request-board__delete-summary')
                    self.assertEqual(trigger.evaluate('e => getComputedStyle(e).backgroundColor'), 'rgb(255, 136, 136)')
                    self.assertEqual(trigger.evaluate('e => getComputedStyle(e).color'), 'rgb(54, 47, 34)')
                    trigger.click()
                    dialog = page.get_by_role('dialog', name='Hapus Beras warga?')
                    expect(dialog).to_be_visible()
                    self.assertTrue(dialog.evaluate("d => d.matches(':modal')"))
                    rect = dialog.bounding_box()
                    self.assertAlmostEqual(rect['x'] + rect['width']/2, width/2, delta=2)
                    self.assertAlmostEqual(rect['y'] + rect['height']/2, 425, delta=2)
                    expect(dialog.get_by_role('button', name='Batal')).to_be_focused()
                    for _ in range(4):
                        page.keyboard.press('Tab')
                        self.assertTrue(dialog.evaluate('d => d.contains(document.activeElement)'))
                    page.keyboard.press('Escape')
                    expect(dialog).not_to_be_visible()
                    expect(trigger).to_be_focused()
                    self.assertEqual(posts, [])
                    trigger.click()
                    dialog.get_by_role('button', name='Batal').click()
                    expect(trigger).to_be_focused()
                    self.assertEqual(posts, [])
                    self.assertTrue(Request.objects.filter(pk=self.item.pk).exists())
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width)
                    page.close()
                page = browser.new_page()
                page.context.add_cookies([{'name': 'sessionid', 'value': self.client.cookies['sessionid'].value, 'url': self.live_server_url}])
                page.route(self.live_server_url + '/**', serve)
                page.goto(self.live_server_url + reverse('request_board:request_board'))
                reject[0] = True
                page.locator('.request-board__delete-summary').click()
                page.get_by_role('button', name='Ya, hapus').click()
                expect(page.get_by_role('alert')).to_contain_text('Hapus ditolak')
                expect(page.locator('.request-board__card')).to_have_count(1)
                self.assertTrue(Request.objects.filter(pk=self.item.pk).exists())
                reject[0] = False
                page.locator('.request-board__delete-summary').click()
                with page.expect_response(lambda response: response.request.method == 'POST') as deletion:
                    page.get_by_role('button', name='Ya, hapus').evaluate('(button) => { button.click(); button.click(); }')
                self.assertTrue(deletion.value.json()['ok'])
                expect(page.locator('[data-board-results]')).to_have_attribute('aria-busy', 'false')
                expect(page.locator('.request-board__card')).to_have_count(0)
                self.assertEqual(len(posts), 2)
                self.assertFalse(Request.objects.filter(pk=self.item.pk).exists())
            finally:
                browser.close()

    def test_card_states_long_content_owner_and_other_at_mobile_and_desktop(self):
        from pathlib import Path
        title = 'Bantuan ' + 'sayuransegar' * 10
        self.item.title = title
        self.item.location = 'Lokasi' * 24
        self.item.description = 'Kebutuhanbahanmakanan' * 24
        self.item.save()
        for state in ('pending', 'offered', 'fulfilled', 'expired'):
            item = Request.objects.create(owner=self.owner, title='Kartu ' + state, food_item='Beras', quantity=2, unit='kg', status='offered' if state == 'expired' else state, expires_at=timezone.now()-timedelta(hours=1) if state == 'expired' else None)
            if state != 'pending':
                offer = RequestOffer.objects.create(request=item, offered_by=self.donor)
                if state != 'offered':
                    item.selected_offer = offer
                    item.save()
        for actor in (self.owner, self.donor):
            self.client.force_login(actor)
            session = self.client.cookies['sessionid'].value
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
                try:
                    for width in (320, 375, 1280):
                        context = browser.new_context(viewport={'width': width, 'height': 900})
                        context.add_cookies([{'name': 'sessionid', 'value': session, 'url': self.live_server_url}])
                        page = context.new_page()
                        page.goto(self.live_server_url + reverse('request_board:request_board'))
                        cards = page.locator('.request-board__card')
                        expect(cards).to_have_count(5)
                        expect(page.get_by_text('Menunggu pemenuhan', exact=True)).to_have_count(1)
                        expect(cards.locator('.request-board__badge').get_by_text('Terpenuhi', exact=True)).to_have_count(1)
                        expect(cards.locator('.request-board__badge').get_by_text('Kedaluwarsa', exact=True)).to_have_count(1)
                        selected = cards.filter(has=page.locator('.request-board__card-title', has_text=title))
                        expect(selected.locator('.request-board__donors')).to_have_count(0)
                        expect(selected.get_by_role('button', name='Tandai Terpenuhi')).to_have_count(1 if actor == self.owner else 0)
                        expect(cards.locator('.request-board__delete-summary')).to_have_count(5 if actor == self.owner else 0)
                        expect(page.get_by_role('button', name='Saya Punya!')).to_have_count(0 if actor == self.owner else 1)
                        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width)
                        for card in cards.all():
                            self.assertTrue(card.evaluate('e => e.scrollWidth <= e.clientWidth'))
                        screenshot_dir = os.environ.get('BOARD_REFINEMENT_SCREENSHOTS')
                        if screenshot_dir and actor == self.owner and width in (375, 1280):
                            Path(screenshot_dir).mkdir(parents=True, exist_ok=True)
                            page.screenshot(path=str(Path(screenshot_dir) / f'board-{width}.png'), full_page=True)
                        context.close()
                finally:
                    browser.close()
