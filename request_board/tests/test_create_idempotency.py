from uuid import uuid4
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from request_board.models import Request


class CreateIdempotencyTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username='idempotent-owner')
        self.other = get_user_model().objects.create_user(username='idempotent-other')
        self.client.force_login(self.owner)
        self.create_url = reverse('request_board:request_create')
        self.data = {'title': 'Beras', 'food_item': 'Beras', 'quantity': '1', 'unit': 'kg'}
        self.key = str(uuid4())

    def post(self, data=None, key=None):
        return self.client.post(self.create_url, data or self.data,
                                HTTP_X_REQUESTED_WITH='XMLHttpRequest',
                                HTTP_IDEMPOTENCY_KEY=key or self.key)

    def test_retry_returns_same_request_without_duplicate(self):
        first = self.post()
        second = self.post()
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(Request.objects.filter(owner=self.owner, title='Beras').count(), 1)
        self.assertEqual(first.json()['request_id'], second.json()['request_id'])

    def test_same_key_with_different_draft_is_rejected(self):
        self.assertEqual(self.post().status_code, 200)
        response = self.post({**self.data, 'title': 'Roti'})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Request.objects.filter(owner=self.owner).count(), 1)

    def test_deleted_request_cannot_be_recreated_with_same_key(self):
        request_id = self.post().json()['request_id']
        Request.objects.get(pk=request_id).delete()
        self.assertEqual(self.post().status_code, 409)
        self.assertFalse(Request.objects.filter(owner=self.owner).exists())

    def test_key_is_scoped_to_authenticated_owner(self):
        first = self.post()
        self.client.force_login(self.other)
        second = self.post()
        self.assertEqual(second.status_code, 200)
        self.assertNotEqual(first.json()['request_id'], second.json()['request_id'])

    def test_invalid_key_does_not_create_request(self):
        response = self.post(key='invalid')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Request.objects.filter(owner=self.owner).exists())

    def test_owner_can_check_committed_create_by_key(self):
        request_id = self.post().json()['request_id']
        url = reverse('request_board:request_create_status', args=[self.key])
        response = self.client.get(url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.json()['request_id'], request_id)
        self.client.force_login(self.other)
        self.assertFalse(self.client.get(url, HTTP_X_REQUESTED_WITH='XMLHttpRequest').json()['found'])

    def test_status_response_is_not_cacheable(self):
        url = reverse('request_board:request_create_status', args=[self.key])
        response = self.client.get(url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertIn('no-store', response.headers.get('Cache-Control', ''))

    def test_replay_does_not_revalidate_deadline_after_commit(self):
        now = timezone.now()
        data = {**self.data, 'expires_at': timezone.localtime(now + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M')}
        first = self.post(data)
        self.assertEqual(first.status_code, 200)
        with patch('request_board.forms.timezone.now', return_value=now + timedelta(hours=2)):
            second = self.post(data)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()['request_id'], first.json()['request_id'])

    def test_invalid_draft_does_not_consume_key(self):
        self.assertEqual(self.post({'title': 'incomplete'}).status_code, 400)
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(Request.objects.filter(owner=self.owner).count(), 1)

    def test_retry_after_account_switch_cannot_create_for_another_user(self):
        self.client.force_login(self.other)
        response = self.client.post(self.create_url, self.data,
                                    HTTP_X_REQUESTED_WITH='XMLHttpRequest',
                                    HTTP_IDEMPOTENCY_KEY=self.key,
                                    HTTP_X_CREATE_OWNER=str(self.owner.pk))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Request.objects.filter(owner=self.other).exists())

    def test_keyed_create_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        headers = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest', 'HTTP_IDEMPOTENCY_KEY': self.key}
        self.assertEqual(client.post(self.create_url, self.data, **headers).status_code, 403)
        self.assertFalse(Request.objects.filter(owner=self.owner).exists())
        client.get(reverse('request_board:request_board'))
        data = {**self.data, 'csrfmiddlewaretoken': client.cookies['csrftoken'].value}
        self.assertEqual(client.post(self.create_url, data, **headers).status_code, 200)
