from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from request_board.models import Request, RequestOffer
from request_board.services import create_request, offer_request, select_request_offer, fulfill_request


class ExpirationTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username='expiry_owner')
        self.donor = get_user_model().objects.create_user(username='expiry_donor')
        self.data = {'title': 'Beras', 'food_item': 'Beras', 'quantity': '1', 'unit': 'kg'}

    def make_request(self, **kwargs):
        return Request.objects.create(owner=self.owner, **self.data, **kwargs)

    def test_default_custom_and_invalid_deadlines(self):
        before = timezone.now()
        default = create_request(actor=self.owner, data=self.data)
        self.assertTrue(before + timedelta(hours=24) <= default.expires_at <= timezone.now() + timedelta(hours=24))
        future = timezone.localtime() + timedelta(days=2)
        custom = create_request(actor=self.owner, data={**self.data, 'expires_at': future.strftime('%Y-%m-%dT%H:%M')})
        self.assertEqual(timezone.localtime(custom.expires_at).strftime('%Y-%m-%dT%H:%M'), future.strftime('%Y-%m-%dT%H:%M'))
        with self.assertRaises(ValidationError):
            create_request(actor=self.owner, data={**self.data, 'expires_at': '2000-01-01T00:00'})

    def test_expired_requests_are_filtered_displayed_and_closed(self):
        expired = self.make_request(expires_at=timezone.now() - timedelta(seconds=1), status='offered')
        offer = RequestOffer.objects.create(request=expired, offered_by=self.donor)
        self.client.force_login(self.owner)
        response = self.client.get(reverse('request_board:request_board'), {'status': 'expired'})
        self.assertContains(response, 'Kedaluwarsa')
        self.assertContains(response, expired.title)
        self.assertNotContains(response, 'Tandai Terpenuhi')
        offers_page = self.client.get(reverse('request_board:request_offers', args=[expired.pk]))
        self.assertNotContains(offers_page, 'Pilih penawar')
        for action in (lambda: offer_request(actor=self.donor, request_id=expired.pk),
                       lambda: select_request_offer(actor=self.owner, request_id=expired.pk, offer_id=offer.pk),
                       lambda: fulfill_request(actor=self.owner, request_id=expired.pk)):
            with self.assertRaises(ValidationError) as error:
                action()
            self.assertEqual(error.exception.code, 'request_expired')
        expired.refresh_from_db()
        self.assertEqual(expired.status, 'offered')
        self.assertEqual(expired.offers.count(), 1)

    def test_fulfilled_and_legacy_requests_keep_status(self):
        fulfilled = self.make_request(status='fulfilled', expires_at=timezone.now()-timedelta(days=1))
        legacy = self.make_request(expires_at=None)
        self.assertEqual(fulfilled.effective_status, 'fulfilled')
        self.assertEqual(legacy.effective_status, 'pending')
    def test_api_deadline_filters_and_conflicts(self):
        expired = self.make_request(expires_at=timezone.now() - timedelta(seconds=1), status='offered')
        offer = RequestOffer.objects.create(request=expired, offered_by=self.donor)
        self.make_request(expires_at=timezone.now() + timedelta(days=1))
        self.make_request(expires_at=None, status='fulfilled')
        url = reverse('request_board:api_request_collection')
        response = self.client.get(url, {'status': 'expired'})
        self.assertEqual(response.json()['pagination']['total_items'], 1)
        item = response.json()['data'][0]
        self.assertEqual(item['status'], 'expired')
        self.assertIsNotNone(item['expires_at'])
        self.assertEqual(self.client.get(url, {'status': 'offered'}).json()['pagination']['total_items'], 0)
        self.client.force_login(self.donor)
        response = self.client.post(reverse('request_board:api_request_offer', args=[expired.pk]), data='{}', content_type='application/json')
        self.assertEqual((response.status_code, response.json()['error']['code']), (409, 'request_expired'))
        response = self.client.post(reverse('request_board:request_offer', args=[expired.pk]), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 409)
        self.client.force_login(self.owner)
        response = self.client.post(reverse('request_board:request_select_offer', args=[expired.pk, offer.pk]), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 409)
        response = self.client.put(reverse('request_board:api_request_status', args=[expired.pk]), data='{"status":"fulfilled"}', content_type='application/json')
        self.assertEqual((response.status_code, response.json()['error']['code']), (409, 'request_expired'))

    def test_deadline_boundary_and_jakarta_input(self):
        from datetime import datetime, timezone as dt_timezone
        from unittest.mock import patch
        instant = datetime(2030, 1, 1, 5, 0, tzinfo=dt_timezone.utc)
        with patch('django.utils.timezone.now', return_value=instant):
            item = self.make_request(expires_at=instant)
            self.assertEqual(item.effective_status, 'expired')
            custom = create_request(actor=self.owner, data={**self.data, 'expires_at': '2030-01-01T13:00'})
            self.assertEqual(custom.expires_at.astimezone(dt_timezone.utc), datetime(2030, 1, 1, 6, 0, tzinfo=dt_timezone.utc))
    def test_deadline_crossing_during_create_returns_form_error(self):
        from unittest.mock import patch
        self.client.force_login(self.owner)
        deadline = (timezone.localtime() + timedelta(minutes=1)).strftime('%Y-%m-%dT%H:%M')
        error = ValidationError({'expires_at': [ValidationError('Batas waktu harus di masa mendatang.')]})
        with patch('request_board.views.create_request', side_effect=error):
            response = self.client.post(reverse('request_board:request_create'), {**self.data, 'expires_at': deadline})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Batas waktu harus di masa mendatang.')
        self.assertContains(response, 'aria-describedby="id_expires_at_hint id_expires_at_error"')
        self.assertEqual(Request.objects.count(), 0)
