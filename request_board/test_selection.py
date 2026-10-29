from importlib import import_module
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from request_board.models import Request, RequestOffer
from request_board.services import fulfill_request, offer_request


class RequestSelectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.owner = users.objects.create_user(username="selection-owner")
        cls.donor = users.objects.create_user(username="selection-donor")
        cls.second = users.objects.create_user(username="selection-second")
        cls.outsider = users.objects.create_user(username="selection-outsider")

    def setUp(self):
        self.item = Request.objects.create(owner=self.owner, title="Bantuan", food_item="Nasi", quantity=1, unit="porsi")
        self.first, _ = offer_request(actor=self.donor, request_id=self.item.pk)
        self.second_offer, _ = offer_request(actor=self.second, request_id=self.item.pk)

    def select(self, offer, actor=None, item=None):
        service = getattr(import_module("request_board.services"), "select_request_offer", None)
        self.assertTrue(callable(service), "select_request_offer belum tersedia")
        return service(actor=actor or self.owner, request_id=(item or self.item).pk, offer_id=offer.pk)

    def test_owner_selects_one_of_two_offers(self):
        result = self.select(self.second_offer)
        self.item.refresh_from_db()
        self.assertEqual(result.selected_offer_id, self.second_offer.pk)
        self.assertEqual(self.item.selected_offer_id, self.second_offer.pk)
        self.assertEqual(self.item.status, "offered")
        self.assertEqual(self.item.offers.count(), 2)

    def test_rejects_foreign_offer(self):
        other = Request.objects.create(owner=self.owner, title="Lain", food_item="Roti", quantity=1, unit="buah")
        foreign, _ = offer_request(actor=self.donor, request_id=other.pk)
        with self.assertRaises((ValidationError, RequestOffer.DoesNotExist)):
            self.select(foreign)
        self.item.refresh_from_db()
        self.assertIsNone(self.item.selected_offer_id)

    def test_guest_and_nonowner_cannot_select(self):
        service = getattr(import_module("request_board.services"), "select_request_offer", None)
        self.assertTrue(callable(service), "select_request_offer belum tersedia")
        for actor in (None, AnonymousUser(), self.outsider):
            with self.subTest(actor=actor), self.assertRaises(PermissionDenied):
                service(actor=actor, request_id=self.item.pk, offer_id=self.first.pk)
        url = f"/board/{self.item.pk}/offers/{self.first.pk}/select/"
        self.assertEqual(self.client.post(url).status_code, 302)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.post(url).status_code, 403)

    def test_repeated_selection_is_idempotent(self):
        self.select(self.first)
        self.item.refresh_from_db()
        updated = self.item.updated_at
        with CaptureQueriesContext(connection) as queries:
            self.select(self.first)
        self.item.refresh_from_db()
        self.assertEqual(self.item.updated_at, updated)
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('UPDATE', 'INSERT', 'DELETE')) for q in queries))

    def test_owner_can_replace_before_fulfilled(self):
        self.select(self.first)
        self.select(self.second_offer)
        self.item.refresh_from_db()
        self.assertEqual(self.item.selected_offer_id, self.second_offer.pk)

    def test_cannot_replace_after_fulfilled(self):
        self.select(self.first)
        fulfill_request(actor=self.owner, request_id=self.item.pk)
        with self.assertRaises(ValidationError):
            self.select(self.second_offer)
        self.item.refresh_from_db()
        self.assertEqual(self.item.selected_offer_id, self.first.pk)

    def test_new_offer_rejected_after_selection(self):
        self.select(self.first)
        with self.assertRaises(ValidationError) as caught:
            offer_request(actor=self.outsider, request_id=self.item.pk)
        self.assertEqual(caught.exception.code, "offers_closed")
        self.assertFalse(self.item.offers.filter(offered_by=self.outsider).exists())
        duplicate, created = offer_request(actor=self.donor, request_id=self.item.pk)
        self.assertFalse(created)
        self.assertEqual(duplicate.pk, self.first.pk)
        self.client.force_login(self.outsider)
        response = self.client.post(reverse("request_board:api_request_offer", args=[self.item.pk]), data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "offers_closed")

    def test_fulfill_requires_selection(self):
        with self.assertRaises(ValidationError) as caught:
            fulfill_request(actor=self.owner, request_id=self.item.pk)
        self.assertEqual(caught.exception.code, "offer_selection_required")
        self.client.force_login(self.owner)
        response = self.client.put(reverse("request_board:api_request_status", args=[self.item.pk]), data='{"status":"fulfilled"}', content_type="application/json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "offer_selection_required")

    def test_legacy_fulfilled_is_preserved(self):
        Request.objects.filter(pk=self.item.pk).update(status="fulfilled")
        before = Request.objects.values().get(pk=self.item.pk)
        with CaptureQueriesContext(connection) as queries:
            fulfill_request(actor=self.owner, request_id=self.item.pk)
        self.assertEqual(Request.objects.values().get(pk=self.item.pk), before)
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('UPDATE', 'INSERT', 'DELETE')) for q in queries))

    def test_owner_can_select_offer_beyond_preview_limit(self):
        for n in range(20):
            donor = get_user_model().objects.create_user(username=f"extra-selection-{n}")
            offer_request(actor=donor, request_id=self.item.pk)
        self.client.force_login(self.owner)
        board = self.client.get('/board/')
        self.assertNotContains(board, self.donor.username)
        url = f"/board/{self.item.pk}/offers/"
        page = self.client.get(url, {"page": 2})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, self.donor.username)
        response = self.client.post(f"/board/{self.item.pk}/offers/{self.first.pk}/select/")
        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.selected_offer_id, self.first.pk)

    def test_offer_list_and_public_projection_are_private(self):
        self.select(self.first)
        url = f"/board/{self.item.pk}/offers/"
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertNotContains(self.client.get('/board/'), self.donor.username)
        payload = self.client.get('/requests').content.decode()
        self.assertNotIn(self.donor.username, payload)
        self.assertNotIn('selected_offer', payload)

    def test_selection_post_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        response = client.post(f"/board/{self.item.pk}/offers/{self.first.pk}/select/")
        self.assertEqual(response.status_code, 403)

    def test_unexpected_uncoded_service_validation_is_not_treated_as_conflict(self):
        self.client.force_login(self.outsider)
        with patch("request_board.views.offer_request", side_effect=ValidationError("unexpected")):
            with self.assertRaises(ValidationError):
                self.client.post(reverse("request_board:api_request_offer", args=[self.item.pk]), data='{}', content_type="application/json")

    def test_owner_selection_form_and_nonowner_privacy(self):
        self.client.force_login(self.owner)
        page = self.client.get(f"/board/{self.item.pk}/offers/")
        self.assertContains(page, f'/board/{self.item.pk}/offers/{self.first.pk}/select/')
        self.assertContains(page, 'csrfmiddlewaretoken')
        self.assertEqual(self.client.get(f"/board/{self.item.pk}/offers/{self.first.pk}/select/").status_code, 405)
        self.client.post(f"/board/{self.item.pk}/offers/{self.first.pk}/select/")
        self.assertContains(self.client.get('/board/'), self.donor.username)
        self.client.force_login(self.outsider)
        response = self.client.get('/board/')
        self.assertContains(response, 'Penawaran sudah ditutup')
        self.assertNotContains(response, self.donor.username)
        self.assertNotContains(response, f'/board/{self.item.pk}/offers/')

    def test_foreign_offer_url_never_changes_selection(self):
        other = Request.objects.create(owner=self.owner, title="Lain", food_item="Roti", quantity=1, unit="buah")
        foreign, _ = offer_request(actor=self.donor, request_id=other.pk)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(f"/board/{self.item.pk}/offers/{foreign.pk}/select/").status_code, 404)
        self.item.refresh_from_db()
        self.assertIsNone(self.item.selected_offer_id)

    def test_offers_summary_and_total_include_all_pages(self):
        for n in range(19):
            donor = get_user_model().objects.create_user(username=f'offer-summary-{n:02}')
            offer_request(actor=donor, request_id=self.item.pk)
        self.item.location = 'Jakarta Selatan'
        self.item.save()
        self.client.force_login(self.owner)
        url = reverse('request_board:request_offers', args=[self.item.pk])
        for number, count in ((1, 20), (2, 1)):
            with self.subTest(page=number):
                response = self.client.get(url, {'page': number})
                self.assertContains(response, '21 penawaran')
                self.assertContains(response, 'Bantuan')
                self.assertContains(response, 'Nasi · 1 porsi · Jakarta Selatan')
                self.assertContains(response, 'Dibuat')
                self.assertEqual(response.content.decode().count('<li class="request-board__donor'), count)
                self.assertContains(response, f'Halaman {number} dari 2')
        self.assertEqual(self.client.get(url, {'page': 'abc'}).status_code, 400)
        self.assertEqual(self.client.get(url, {'page': 3}).status_code, 404)

    def test_selected_offer_banner_is_visible_when_offer_is_on_another_page(self):
        for n in range(19):
            donor = get_user_model().objects.create_user(username=f'offer-other-page-{n:02}')
            offer_request(actor=donor, request_id=self.item.pk)
        self.select(self.first)
        self.client.force_login(self.owner)
        url = reverse('request_board:request_offers', args=[self.item.pk])
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(url)
        self.assertContains(response, 'Penawar terpilih: selection-donor')
        self.assertEqual(response.content.decode().count('<li class="request-board__donor'), 20)
        self.assertLessEqual(len(queries), 6)

    def test_offers_selected_row_has_badge_without_reselect_form(self):
        from request_board.test_rendering import BoardMarkup
        self.client.force_login(self.owner)
        url = reverse('request_board:request_offers', args=[self.item.pk])
        before = self.client.get(url)
        before_markup = BoardMarkup(before.content.decode())
        buttons = [node for node in before_markup.select('button') if node['text'].strip() == 'Pilih penawar']
        self.assertEqual(len(buttons), 2)
        self.assertEqual({button['attrs'].get('aria-label') for button in buttons},
                         {f'Pilih penawar {self.donor.username}', f'Pilih penawar {self.second.username}'})
        self.assertNotContains(before, 'Penawar terpilih:')
        self.select(self.first)
        self.item.refresh_from_db()
        self.assertNotEqual(self.item.status, 'fulfilled')
        markup = BoardMarkup(self.client.get(url).content.decode())
        rows = [n for n in markup.select('li') if 'request-board__donor' in n['attrs'].get('class', '').split()]
        chosen = next(n for n in rows if self.donor.username in n['text'])
        other = next(n for n in rows if self.second.username in n['text'])
        self.assertIn('Terpilih', chosen['text'])
        self.assertEqual(markup.select('form', within=chosen), [])
        self.assertIn('Ganti pilihan', other['text'])
        self.assertEqual(len(markup.select('form', within=other)), 1)

    def test_fulfilled_and_legacy_offers_are_read_only(self):
        from request_board.test_rendering import BoardMarkup
        self.client.force_login(self.owner)
        url = reverse('request_board:request_offers', args=[self.item.pk])
        for selected in (True, False):
            Request.objects.filter(pk=self.item.pk).update(status='fulfilled', selected_offer=self.first if selected else None)
            before = Request.objects.values().get(pk=self.item.pk)
            response = self.client.get(url)
            markup = BoardMarkup(response.content.decode())
            root, = [n for n in markup.nodes if 'data-board-offers' in n['attrs']]
            self.assertEqual(markup.select('form', within=root), [])
            self.assertContains(response, 'Permintaan telah terpenuhi. Daftar ini hanya untuk dilihat.')
            self.assertNotContains(response, 'Data lama')
            if selected:
                self.assertContains(response, 'Penawar terpilih: selection-donor')
            else:
                self.assertNotContains(response, 'Penawar terpilih:')
            self.assertEqual(Request.objects.values().get(pk=self.item.pk), before)
