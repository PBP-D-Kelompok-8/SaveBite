"""Browser checks for observable state before a mutation reaches the server."""
import os
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse

from request_board.models import Request, RequestOffer

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:
    sync_playwright = None


@skipUnless(sync_playwright, "Optional Playwright browser tooling is not installed")
class OptimisticOfferBrowserTests(StaticLiveServerTestCase):
    def test_selection_blocks_pagination_until_rejection_is_resolved(self):
        users = get_user_model()
        owner = users.objects.create_user(username='selection-page-owner')
        item = Request.objects.create(owner=owner, title='Pilih', food_item='Nasi', quantity=1, unit='porsi', status=Request.STATUS_OFFERED)
        for index in range(21):
            donor = users.objects.create_user(username=f'selection-page-donor-{index}')
            RequestOffer.objects.create(request=item, offered_by=donor)
        client = Client()
        client.force_login(owner)
        session = client.cookies['sessionid'].value
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                page = browser.new_page()
                page.context.add_cookies([{'name': 'sessionid', 'value': session, 'url': self.live_server_url}])
                page.goto(self.live_server_url + reverse('request_board:request_offers', args=[item.pk]))
                page.evaluate("""() => {
                    window.fetch = () => new Promise(resolve => { window.rejectSelection = () => resolve(new Response(
                        JSON.stringify({ok: false, message: 'Pilihan ditolak'}),
                        {status: 409, headers: {'Content-Type': 'application/json'}})); });
                }""")
                page.get_by_role('button', name='Pilih penawar', exact=False).first.click()
                page.wait_for_function('typeof window.rejectSelection === "function"')
                pagination = page.locator('.request-board__offer-pagination')
                expect(pagination).to_have_attribute('inert', '', timeout=1500)
                page.evaluate('window.rejectSelection()')
                expect(pagination).not_to_have_attribute('inert', '')
            finally:
                browser.close()

    def test_timeout_while_reading_response_body_is_uncertain(self):
        users = get_user_model()
        owner = users.objects.create_user(username='body-timeout-owner')
        donor = users.objects.create_user(username='body-timeout-donor')
        Request.objects.create(owner=owner, title='Body timeout', food_item='Nasi', quantity=1, unit='porsi')
        client = Client()
        client.force_login(donor)
        session = client.cookies['sessionid'].value
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                page = browser.new_page()
                page.clock.install()
                page.context.add_cookies([{'name': 'sessionid', 'value': session, 'url': self.live_server_url}])
                page.goto(self.live_server_url + reverse('request_board:request_board'))
                page.evaluate("""() => {
                    const original = window.fetch;
                    window.fetch = (url, options) => {
                        if (options?.method !== 'POST') return original(url, options);
                        const stream = new ReadableStream({start(controller) {
                            options.signal.addEventListener('abort', () => controller.error(new DOMException('Timeout', 'AbortError')));
                        }});
                        window.bodyWaiting = true;
                        return Promise.resolve(new Response(stream, {status: 200, headers: {'Content-Type': 'application/json'}}));
                    };
                }""")
                page.get_by_role('button', name='Saya Punya!').click()
                page.wait_for_function('window.bodyWaiting === true')
                page.clock.fast_forward(15000)
                expect(page.get_by_role('alert')).to_contain_text('belum dapat dipastikan', timeout=2000)
                expect(page.locator('.request-board__card')).to_contain_text('0 penawaran')
            finally:
                browser.close()

    def test_refresh_timeout_after_success_does_not_report_post_failure(self):
        users = get_user_model()
        owner = users.objects.create_user(username='timeout-refresh-owner')
        donor = users.objects.create_user(username='timeout-refresh-donor')
        Request.objects.create(owner=owner, title='Timeout refresh', food_item='Nasi', quantity=1, unit='porsi')
        client = Client()
        client.force_login(donor)
        session = client.cookies['sessionid'].value
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                page = browser.new_page()
                page.clock.install()
                page.context.add_cookies([{'name': 'sessionid', 'value': session, 'url': self.live_server_url}])
                page.goto(self.live_server_url + reverse('request_board:request_board'))
                page.evaluate("""() => {
                    const original = window.fetch;
                    window.fetch = (url, options) => {
                        if (options?.method === 'POST') return original(url, options);
                        window.refreshWaiting = true;
                        return new Promise((resolve, reject) => options.signal?.addEventListener('abort',
                            () => reject(new DOMException('Timeout', 'AbortError'))));
                    };
                }""")
                page.get_by_role('button', name='Saya Punya!').click()
                page.wait_for_function('window.refreshWaiting === true')
                page.clock.fast_forward(15000)
                expect(page.get_by_role('alert')).to_contain_text('Aksi berhasil, tetapi papan gagal diperbarui', timeout=2000)
                expect(page.get_by_role('button', name='Penawaran tersimpan')).to_be_disabled()
            finally:
                browser.close()

    def test_offer_timeout_reports_unknown_and_reconciles(self):
        users = get_user_model()
        owner = users.objects.create_user(username='timeout-offer-owner')
        donor = users.objects.create_user(username='timeout-offer-donor')
        Request.objects.create(owner=owner, title='Timeout', food_item='Nasi', quantity=1, unit='porsi')
        client = Client()
        client.force_login(donor)
        session = client.cookies['sessionid'].value
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                page = browser.new_page()
                page.clock.install()
                page.context.add_cookies([{'name': 'sessionid', 'value': session, 'url': self.live_server_url}])
                page.goto(self.live_server_url + reverse('request_board:request_board'))
                page.evaluate("""() => {
                    const original = window.fetch;
                    window.fetch = (url, options) => options?.method === 'POST'
                        ? new Promise((resolve, reject) => options.signal?.addEventListener('abort',
                            () => reject(new DOMException('Timeout', 'AbortError'))))
                        : original(url, options);
                }""")
                page.get_by_role('button', name='Saya Punya!').click()
                expect(page.locator('.request-board__card')).to_contain_text('Sedang menyimpan')
                page.clock.fast_forward(15000)
                expect(page.get_by_role('alert')).to_contain_text('belum dapat dipastikan', timeout=2000)
                expect(page.locator('.request-board__card')).to_contain_text('0 penawaran')
            finally:
                browser.close()

    def test_immediate_delete_rejection_does_not_steal_new_focus(self):
        owner = get_user_model().objects.create_user(username='focus-delete-owner')
        Request.objects.create(owner=owner, title='Fokus', food_item='Nasi', quantity=1, unit='porsi')
        client = Client()
        client.force_login(owner)
        session = client.cookies['sessionid'].value
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                page = browser.new_page()
                page.context.add_cookies([{'name': 'sessionid', 'value': session, 'url': self.live_server_url}])
                page.goto(self.live_server_url + reverse('request_board:request_board'))
                page.evaluate("""() => {
                    window.fetch = () => Promise.resolve(new Response(JSON.stringify({ok: false, message: 'Ditolak'}),
                        {status: 403, headers: {'Content-Type': 'application/json'}}));
                    document.addEventListener('submit', () => document.querySelector('.request-board__header-action a').focus());
                }""")
                page.locator('.request-board__delete-summary').click()
                page.get_by_role('button', name='Ya, hapus').click()
                expect(page.get_by_role('alert')).to_contain_text('Ditolak')
                page.wait_for_timeout(100)
                expect(page.locator('.request-board__header-action a')).to_be_focused()
            finally:
                browser.close()

    def test_existing_offer_does_not_increment_twice_and_double_submit_is_blocked(self):
        users = get_user_model()
        owner = users.objects.create_user(username='existing-owner')
        donor = users.objects.create_user(username='existing-donor')
        Request.objects.create(owner=owner, title='Bantuan', food_item='Nasi', quantity=1, unit='porsi')
        client = Client()
        client.force_login(donor)
        session = client.cookies['sessionid'].value
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                page = browser.new_page()
                page.context.add_cookies([{'name': 'sessionid', 'value': session, 'url': self.live_server_url}])
                page.goto(self.live_server_url + reverse('request_board:request_board'))
                page.evaluate("""async () => {
                    const form = document.querySelector('.request-board__card form');
                    await fetch(form.action, {method: 'POST', body: new FormData(form),
                        headers: {'X-Requested-With': 'XMLHttpRequest'}});
                    const original = window.fetch;
                    window.postCount = 0;
                    window.fetch = (url, options) => {
                        if (options?.method !== 'POST') return original(url, options);
                        window.postCount++;
                        return original(url, options).then(response => new Promise(resolve => {
                            window.releaseOffer = () => resolve(response);
                        }));
                    };
                }""")
                page.get_by_role('button', name='Saya Punya!').click()
                page.wait_for_function('typeof window.releaseOffer === "function"')
                page.locator('.request-board__card form').evaluate('form => form.requestSubmit()')
                self.assertEqual(page.evaluate('window.postCount'), 1)
                expect(page.locator('[data-offer-count]')).to_have_text('1 penawaran')
                page.evaluate('window.releaseOffer()')
                expect(page.locator('.request-board__card')).to_contain_text('Sudah menawarkan')
                expect(page.locator('[data-offer-count]')).to_have_text('1 penawaran')
            finally:
                browser.close()

    def test_delete_last_item_on_page_reconciles_to_previous_page(self):
        owner = get_user_model().objects.create_user(username='page-delete-owner')
        Request.objects.bulk_create([
            Request(owner=owner, title=f'Permintaan {index}', food_item='Roti', quantity=1, unit='porsi')
            for index in range(21)
        ])
        client = Client()
        client.force_login(owner)
        session = client.cookies['sessionid'].value
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                page = browser.new_page()
                page.context.add_cookies([{'name': 'sessionid', 'value': session, 'url': self.live_server_url}])
                board = self.live_server_url + reverse('request_board:request_board')
                page.goto(board + '?page=2')
                page.locator('.request-board__delete-summary').click()
                page.get_by_role('button', name='Ya, hapus').click()
                expect(page.locator('.request-board__card')).to_have_count(20)
                self.assertEqual(page.url, board)
                self.assertEqual(page.locator('[data-board-result-summary]').get_attribute('data-total'), '20')
            finally:
                browser.close()

    def test_selection_shows_temporary_choice_and_restores_previous_choice(self):
        users = get_user_model()
        owner = users.objects.create_user(username='select-owner')
        first = users.objects.create_user(username='first-donor')
        second = users.objects.create_user(username='second-donor')
        item = Request.objects.create(owner=owner, title='Kebutuhan', food_item='Nasi', quantity=1, unit='porsi', status=Request.STATUS_OFFERED)
        first_offer = RequestOffer.objects.create(request=item, offered_by=first)
        RequestOffer.objects.create(request=item, offered_by=second)
        item.selected_offer = first_offer
        item.save(update_fields=['selected_offer'])
        client = Client()
        client.force_login(owner)
        session = client.cookies['sessionid'].value
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                page = browser.new_page()
                page.context.add_cookies([{'name': 'sessionid', 'value': session, 'url': self.live_server_url}])
                page.goto(self.live_server_url + reverse('request_board:request_offers', args=[item.pk]))
                page.evaluate("""() => {
                    window.fetch = () => new Promise(resolve => { window.rejectSelection = () => resolve(new Response(
                        JSON.stringify({ok: false, message: 'Pilihan ditolak'}),
                        {status: 409, headers: {'Content-Type': 'application/json'}})); });
                }""")
                page.get_by_role('button', name='Ganti pilihan second-donor').click()
                page.wait_for_function('typeof window.rejectSelection === "function"')
                expect(page.locator('[data-board-offers]')).to_contain_text('second-donor (sedang menyimpan)', timeout=1500)
                page.evaluate('window.rejectSelection()')
                expect(page.locator('[data-offer-selection]')).to_contain_text('first-donor')
                expect(page.get_by_role('button', name='Ganti pilihan second-donor')).to_be_enabled()
                expect(page.get_by_role('alert')).to_contain_text('Pilihan ditolak')
            finally:
                browser.close()

    def test_fulfill_shows_temporary_state_and_rolls_back_on_conflict(self):
        users = get_user_model()
        owner = users.objects.create_user(username="fulfill-owner")
        donor = users.objects.create_user(username="fulfill-donor")
        item = Request.objects.create(owner=owner, title="Untuk Dipenuhi", food_item="Nasi", quantity=1, unit="porsi", status=Request.STATUS_OFFERED)
        offer = RequestOffer.objects.create(request=item, offered_by=donor)
        item.selected_offer = offer
        item.save(update_fields=['selected_offer'])
        client = Client()
        client.force_login(owner)
        session = client.cookies['sessionid'].value
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_CHROMIUM_CHANNEL') or None)
            try:
                page = browser.new_page()
                page.context.add_cookies([{'name': 'sessionid', 'value': session, 'url': self.live_server_url}])
                page.goto(self.live_server_url + reverse('request_board:request_board'))
                page.evaluate("""() => {
                    window.fetch = () => new Promise(resolve => { window.rejectFulfill = () => resolve(new Response(
                        JSON.stringify({ok: false, message: 'Sudah kedaluwarsa'}),
                        {status: 409, headers: {'Content-Type': 'application/json'}})); });
                }""")
                card = page.locator('.request-board__card')
                card.get_by_role('button', name='Tandai Terpenuhi').click()
                page.wait_for_function('typeof window.rejectFulfill === "function"')
                expect(card).to_contain_text('Terpenuhi (sedang menyimpan)', timeout=1500)
                page.evaluate('window.rejectFulfill()')
                expect(card).to_contain_text('Ditawarkan')
                expect(card.get_by_role('button', name='Tandai Terpenuhi')).to_be_enabled()
                expect(page.get_by_role('alert')).to_contain_text('Sudah kedaluwarsa')
            finally:
                browser.close()

    def test_lost_offer_response_after_commit_reconciles_from_server(self):
        users = get_user_model()
        owner = users.objects.create_user(username="lost-response-owner")
        donor = users.objects.create_user(username="lost-response-donor")
        Request.objects.create(owner=owner, title="Beras", food_item="Beras", quantity=1, unit="kg")
        client = Client()
        client.force_login(donor)
        session = client.cookies["sessionid"].value
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get("PLAYWRIGHT_CHROMIUM_CHANNEL") or None)
            try:
                page = browser.new_page()
                page.context.add_cookies([{"name": "sessionid", "value": session, "url": self.live_server_url}])
                page.goto(self.live_server_url + reverse("request_board:request_board"))
                page.evaluate("""() => {
                    const original = window.fetch;
                    window.fetch = async (url, options) => {
                        const response = await original(url, options);
                        if (options?.method === 'POST') throw new TypeError('connection lost');
                        return response;
                    };
                }""")
                page.get_by_role('button', name='Saya Punya!').click()
                expect(page.locator('.request-board__card')).to_contain_text('Sudah menawarkan')
                expect(page.locator('.request-board__card')).to_contain_text('1 penawaran')
                expect(page.get_by_role('alert')).to_contain_text('belum dapat dipastikan')
            finally:
                browser.close()

    def test_confirmed_offer_with_failed_refresh_is_not_reported_as_failure(self):
        users = get_user_model()
        owner = users.objects.create_user(username="refresh-owner")
        donor = users.objects.create_user(username="refresh-donor")
        Request.objects.create(owner=owner, title="Roti", food_item="Roti", quantity=1, unit="porsi")
        client = Client()
        client.force_login(donor)
        session = client.cookies["sessionid"].value
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get("PLAYWRIGHT_CHROMIUM_CHANNEL") or None)
            try:
                page = browser.new_page()
                page.context.add_cookies([{"name": "sessionid", "value": session, "url": self.live_server_url}])
                page.goto(self.live_server_url + reverse("request_board:request_board"))
                page.evaluate("""() => {
                    const original = window.fetch;
                    window.fetch = (url, options) => options?.method === 'POST'
                        ? original(url, options) : Promise.reject(new TypeError('offline'));
                }""")
                page.get_by_role('button', name='Saya Punya!').click()
                expect(page.get_by_role('alert')).to_contain_text('Aksi berhasil, tetapi papan gagal diperbarui')
                expect(page.get_by_role('button', name='Penawaran tersimpan')).to_be_disabled()
            finally:
                browser.close()

    def test_delete_is_removed_before_response_and_restored_on_rejection(self):
        owner = get_user_model().objects.create_user(username="delete-optimistic-owner")
        Request.objects.create(owner=owner, title="Hapus Saya", food_item="Nasi", quantity=1, unit="porsi")
        client = Client()
        client.force_login(owner)
        session = client.cookies["sessionid"].value
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get("PLAYWRIGHT_CHROMIUM_CHANNEL") or None)
            try:
                page = browser.new_page()
                page.context.add_cookies([{"name": "sessionid", "value": session, "url": self.live_server_url}])
                page.goto(self.live_server_url + reverse("request_board:request_board"))
                page.evaluate("""() => {
                    const original = window.fetch;
                    window.fetch = (url, options) => options?.method === 'POST'
                        ? new Promise(resolve => { window.rejectDelete = () => resolve(new Response(
                            JSON.stringify({ok: false, message: 'Hapus ditolak'}),
                            {status: 403, headers: {'Content-Type': 'application/json'}})); })
                        : original(url, options);
                }""")
                card = page.locator('.request-board__card')
                card.locator('summary').click()
                page.get_by_role('button', name='Ya, hapus').click()
                page.wait_for_function('typeof window.rejectDelete === "function"')
                expect(card).to_have_count(0, timeout=1500)
                page.get_by_role('link', name='Buat Permintaan').first.focus()
                page.evaluate('window.rejectDelete()')
                expect(card).to_have_count(1)
                expect(card).to_contain_text('Hapus Saya')
                expect(page.get_by_role('alert')).to_contain_text('Hapus ditolak')
                expect(page.get_by_role('link', name='Buat Permintaan').first).to_be_focused()
            finally:
                browser.close()

    def test_offer_updates_before_response_and_restores_on_rejection(self):
        users = get_user_model()
        owner = users.objects.create_user(username="optimistic-owner")
        donor = users.objects.create_user(username="optimistic-donor")
        Request.objects.create(owner=owner, title="Nasi", food_item="Nasi", quantity=1, unit="porsi")
        client = Client()
        client.force_login(donor)
        session = client.cookies["sessionid"].value
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get("PLAYWRIGHT_CHROMIUM_CHANNEL") or None)
            try:
                for width in (375, 1280):
                    page = browser.new_page(viewport={"width": width, "height": 800})
                    page.context.add_cookies([{"name": "sessionid", "value": session, "url": self.live_server_url}])
                    page.goto(self.live_server_url + reverse("request_board:request_board"))
                    page.evaluate("""() => {
                        const original = window.fetch;
                        window.fetch = (url, options) => options?.method === 'POST'
                            ? new Promise(resolve => { window.rejectOffer = () => resolve(new Response(
                                JSON.stringify({ok: false, message: 'Penawaran ditolak'}),
                                {status: 409, headers: {'Content-Type': 'application/json'}})); })
                            : original(url, options);
                    }""")
                    card = page.locator('.request-board__card')
                    card.get_by_role('button', name='Saya Punya!').click()
                    page.wait_for_function('typeof window.rejectOffer === "function"')
                    expect(card).to_contain_text('1 penawaran', timeout=1500)
                    expect(card).to_contain_text('Sedang menyimpan', timeout=1500)
                    page.evaluate('window.rejectOffer()')
                    expect(card).to_contain_text('0 penawaran')
                    expect(card.get_by_role('button', name='Saya Punya!')).to_be_enabled()
                    expect(page.get_by_role('alert')).to_contain_text('Penawaran ditolak')
                    page.close()
            finally:
                browser.close()
