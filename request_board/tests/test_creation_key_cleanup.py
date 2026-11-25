from datetime import timedelta
from io import StringIO
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from unittest.mock import patch

from request_board.models import Request, RequestCreationKey


class CreationKeyCleanupTests(TestCase):
    def test_cleanup_expires_old_keys_but_keeps_recent_tombstones_and_requests(self):
        owner = get_user_model().objects.create_user(username='cleanup-owner')
        food_request = Request.objects.create(
            owner=owner, title='Beras', food_item='Beras', quantity=1, unit='kg',
        )
        now = timezone.now()
        records = [RequestCreationKey.objects.create(
            owner=owner, key=uuid4(), payload_digest='a' * 64, request=request,
        ) for request in (food_request, None, None)]
        RequestCreationKey.objects.filter(pk__in=[r.pk for r in records[:2]]).update(
            created_at=now - timedelta(days=31),
        )
        RequestCreationKey.objects.filter(pk=records[2].pk).update(
            created_at=now - timedelta(days=30),
        )
        with patch('request_board.management.commands.cleanup_request_creation_keys.timezone.now', return_value=now):
            call_command('cleanup_request_creation_keys', dry_run=True, stdout=StringIO())
            self.assertEqual(RequestCreationKey.objects.count(), 3)
            call_command('cleanup_request_creation_keys', stdout=StringIO())
        self.assertEqual(list(RequestCreationKey.objects.values_list('pk', flat=True)), [records[2].pk])
        self.assertTrue(Request.objects.filter(pk=food_request.pk).exists())
        call_command('cleanup_request_creation_keys', stdout=StringIO())
