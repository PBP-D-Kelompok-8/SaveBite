from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from . import api_auth
from .models import FoodLog

User = get_user_model()


class FoodLogAPITestBase(APITestCase):
    def setUp(self):
        cache.clear()  # throttle counters live in the cache
        self.user = User.objects.create_user(username="alice", password="test-password")
        self.other = User.objects.create_user(username="bob", password="test-password")
        self.log = FoodLog.objects.create(
            user=self.user, food_name="Nasi goreng", quantity=2, unit="porsi",
            status="terselamatkan", notes="Dibagikan ke tetangga",
        )
        self.other_log = FoodLog.objects.create(
            user=self.other, food_name="Roti", quantity=1, unit="pcs", status="terbuang",
        )
        self.list_url = reverse("jurnal:jurnal_api:food-log-list")
        self.summary_url = reverse("jurnal:jurnal_api:food-log-summary")
        self.detail_url = reverse("jurnal:jurnal_api:food-log-detail", args=[self.log.pk])
        self.other_detail_url = reverse("jurnal:jurnal_api:food-log-detail", args=[self.other_log.pk])
        self.payload = {
            "food_name": "Sayur sop", "quantity": 3, "unit": "porsi",
            "status": "terselamatkan", "notes": "Untuk makan malam",
        }
        self.client.force_authenticate(user=self.user)


class ReadTests(FoodLogAPITestBase):
    def test_anonymous_is_rejected_everywhere(self):
        self.client.force_authenticate(user=None)
        # Token auth is listed first, so anonymous callers get 401 (not 403).
        for url in (self.list_url, self.detail_url, self.summary_url):
            self.assertEqual(self.client.get(url).status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(self.client.post(self.list_url, self.payload, format="json").status_code, 401)

    def test_list_only_returns_my_logs_in_paginated_shape(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], self.log.pk)
        self.assertEqual(response.data["results"][0]["status_display"], "Terselamatkan")
        self.assertEqual(response.data["results"][0]["unit_display"], "Porsi")
        self.assertNotIn("user", response.data["results"][0])
        self.assertNotIn("rescue_plan_id", response.data["results"][0])

    def test_pagination_uses_six_per_page(self):
        for i in range(6):
            FoodLog.objects.create(user=self.user, food_name=f"Item {i}")
        first = self.client.get(self.list_url)
        self.assertEqual(first.data["count"], 7)
        self.assertEqual(len(first.data["results"]), 6)
        self.assertIsNotNone(first.data["next"])
        second = self.client.get(self.list_url, {"page": 2})
        self.assertEqual(len(second.data["results"]), 1)

    def test_filters_combine(self):
        now = timezone.now()
        FoodLog.objects.create(user=self.user, food_name="Apel", status="terbuang",
                               completed_at=now - timedelta(days=10))
        FoodLog.objects.create(user=self.user, food_name="Apel hijau", status="terselamatkan",
                               completed_at=now)
        r = self.client.get(self.list_url, {"query": "apel", "status": "terselamatkan"})
        self.assertEqual([x["food_name"] for x in r.data["results"]], ["Apel hijau"])
        # search also checks notes
        r = self.client.get(self.list_url, {"query": "tetangga"})
        self.assertEqual(r.data["count"], 1)
        # inclusive date range
        today = timezone.localtime(now).date().isoformat()
        r = self.client.get(self.list_url, {"date_from": today, "date_to": today})
        self.assertTrue(all("Apel hijau" == x["food_name"] or x["id"] == self.log.pk
                            for x in r.data["results"]))
        # empty parameters are ignored, like the HTML page
        r = self.client.get(self.list_url, {"query": "", "status": "", "date_from": ""})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["count"], 3)

    def test_invalid_filters_return_400(self):
        r = self.client.get(self.list_url, {"status": "basi"})
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("status", r.data)
        r = self.client.get(self.list_url, {"date_from": "2026-02-10", "date_to": "2026-02-01"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("date_to", r.data)
        r = self.client.get(self.list_url, {"date_from": "not-a-date"})
        self.assertEqual(r.status_code, 400)

    def test_detail_own_and_others(self):
        self.assertEqual(self.client.get(self.detail_url).data["food_name"], "Nasi goreng")
        self.assertEqual(self.client.get(self.other_detail_url).status_code, status.HTTP_404_NOT_FOUND)

    def test_summary_counts_only_my_logs(self):
        FoodLog.objects.create(user=self.user, food_name="Susu", status="terbuang")
        r = self.client.get(self.summary_url)
        self.assertEqual(r.data, {
            "saved_count": 1, "wasted_count": 1, "total_count": 2,
            "saved_percentage": 50, "wasted_percentage": 50,
        })

    def test_summary_when_empty(self):
        FoodLog.objects.filter(user=self.user).delete()
        r = self.client.get(self.summary_url)
        self.assertEqual(r.data["total_count"], 0)
        self.assertEqual(r.data["saved_percentage"], 0)


class WriteTests(FoodLogAPITestBase):
    def test_create_assigns_current_user_and_ignores_forged_fields(self):
        forged = {**self.payload, "user": self.other.pk, "rescue_plan_id": 99,
                  "completed_at": "2000-01-01T00:00:00Z", "id": 12345}
        r = self.client.post(self.list_url, forged, format="json")
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        created = FoodLog.objects.get(pk=r.data["id"])
        self.assertEqual(created.user, self.user)
        self.assertIsNone(created.rescue_plan_id)
        self.assertNotEqual(created.pk, 12345)
        self.assertGreater(created.completed_at.year, 2000)

    def test_create_validation(self):
        bad_payloads = [
            ({"food_name": "   "}, "food_name"),
            ({"food_name": "<b></b>"}, "food_name"),
            ({"quantity": 0}, "quantity"),
            ({"quantity": -3}, "quantity"),
            ({"quantity": "abc"}, "quantity"),
            ({"unit": "galon"}, "unit"),
            ({"status": "basi"}, "status"),
        ]
        for changes, field in bad_payloads:
            r = self.client.post(self.list_url, {**self.payload, **changes}, format="json")
            self.assertEqual(r.status_code, 400, changes)
            self.assertIn(field, r.data)
        self.assertEqual(FoodLog.objects.filter(user=self.user).count(), 1)

    def test_html_tags_are_stripped(self):
        r = self.client.post(self.list_url, {
            **self.payload, "food_name": "<script>alert(1)</script>Tempe",
            "notes": "<img src=x onerror=alert(1)>aman",
        }, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertNotIn("<", r.data["food_name"])
        self.assertNotIn("<", r.data["notes"])

    def test_patch_changes_only_sent_fields(self):
        r = self.client.patch(self.detail_url, {"status": "terbuang"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.log.refresh_from_db()
        self.assertEqual(self.log.status, "terbuang")
        self.assertEqual(self.log.food_name, "Nasi goreng")

    def test_put_requires_required_fields(self):
        r = self.client.put(self.detail_url, {"quantity": 5}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("food_name", r.data)
        r = self.client.put(self.detail_url, self.payload, format="json")
        self.assertEqual(r.status_code, 200)
        self.log.refresh_from_db()
        self.assertEqual(self.log.food_name, "Sayur sop")

    def test_cannot_change_read_only_fields(self):
        original = self.log.completed_at
        self.client.patch(self.detail_url, {"completed_at": "2000-01-01T00:00:00Z",
                                            "rescue_plan_id": 5, "user": self.other.pk}, format="json")
        self.log.refresh_from_db()
        self.assertEqual(self.log.completed_at, original)
        self.assertIsNone(self.log.rescue_plan_id)
        self.assertEqual(self.log.user, self.user)

    def test_delete_returns_204_and_removes(self):
        r = self.client.delete(self.detail_url)
        self.assertEqual(r.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(r.content, b"")
        self.assertFalse(FoodLog.objects.filter(pk=self.log.pk).exists())

    def test_cannot_modify_or_delete_someone_elses_log(self):
        self.assertEqual(self.client.patch(self.other_detail_url, {"food_name": "Hacked"}, format="json").status_code, 404)
        self.assertEqual(self.client.put(self.other_detail_url, self.payload, format="json").status_code, 404)
        self.assertEqual(self.client.delete(self.other_detail_url).status_code, 404)
        self.other_log.refresh_from_db()
        self.assertEqual(self.other_log.food_name, "Roti")

    def test_wrong_method_is_405(self):
        self.assertEqual(self.client.delete(self.list_url).status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(self.client.post(self.summary_url).status_code, 405)

    def test_rescue_plan_log_exposes_flag_but_not_the_id(self):
        auto = FoodLog.objects.create(user=self.user, food_name="Auto", rescue_plan_id=7)
        r = self.client.get(reverse("jurnal:jurnal_api:food-log-detail", args=[auto.pk]))
        self.assertTrue(r.data["from_rescue_plan"])
        self.assertNotIn("rescue_plan_id", r.data)


class TokenAuthTests(FoodLogAPITestBase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(user=None)
        self.token_url = reverse("jurnal:jurnal_api:token")

    def login(self, username="alice", password="test-password"):
        return self.client.post(
            self.token_url, {"username": username, "password": password}, format="json",
        )

    def token_client(self, token):
        client = APIClient(enforce_csrf_checks=True)  # a token needs no CSRF token
        client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
        return client

    def test_login_returns_token_and_token_works_for_reads_and_writes(self):
        r = self.login()
        self.assertEqual(r.status_code, 200)
        client = self.token_client(r.data["token"])
        self.assertEqual(client.get(self.list_url).data["count"], 1)
        created = client.post(self.list_url, self.payload, format="json")
        self.assertEqual(created.status_code, 201)
        self.assertEqual(FoodLog.objects.get(pk=created.data["id"]).user, self.user)
        self.assertEqual(client.get(self.other_detail_url).status_code, 404)

    def test_wrong_password_and_unknown_user(self):
        self.assertEqual(self.login(password="nope").status_code, 400)
        self.assertEqual(self.login(username="ghost").status_code, 400)
        r = self.client.post(self.token_url, {"username": "alice"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_bad_and_malformed_tokens_are_401(self):
        for header in ("Token not-a-real-token", "Token", "Token a b", "Bearer abc"):
            self.client.credentials(HTTP_AUTHORIZATION=header)
            self.assertEqual(self.client.get(self.list_url).status_code, 401, header)

    def test_expired_token_is_rejected(self):
        token = self.login().data["token"]
        with mock.patch.object(api_auth, "TOKEN_MAX_AGE", -1):
            self.assertEqual(self.token_client(token).get(self.list_url).status_code, 401)

    def test_changing_password_invalidates_token(self):
        token = self.login().data["token"]
        self.user.set_password("new-password")
        self.user.save()
        self.assertEqual(self.token_client(token).get(self.list_url).status_code, 401)

    def test_inactive_user_token_is_rejected(self):
        token = self.login().data["token"]
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.token_client(token).get(self.list_url).status_code, 401)

    def test_login_is_throttled(self):
        for _ in range(10):
            self.assertEqual(self.login(password="nope").status_code, 400)
        self.assertEqual(self.login(password="nope").status_code, 429)


class CsrfTests(FoodLogAPITestBase):
    def test_session_write_without_csrf_is_rejected(self):
        client = APIClient(enforce_csrf_checks=True)
        self.assertTrue(client.login(username="alice", password="test-password"))
        r = client.post(self.list_url, self.payload, format="json")
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(FoodLog.objects.filter(user=self.user).count(), 1)

    def test_session_write_with_csrf_succeeds(self):
        client = APIClient(enforce_csrf_checks=True)
        client.login(username="alice", password="test-password")
        client.get(self.list_url, HTTP_ACCEPT="text/html")  # Browsable API sets the cookie
        token = client.cookies["csrftoken"].value
        r = client.post(self.list_url, self.payload, format="json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)


class ThrottleTests(FoodLogAPITestBase):
    def test_writes_are_limited_to_20_per_minute(self):
        for _ in range(20):
            self.assertEqual(self.client.post(self.list_url, self.payload, format="json").status_code, 201)
        r = self.client.post(self.list_url, self.payload, format="json")
        self.assertEqual(r.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertIn("Retry-After", r)
        # reads still work, and other users are not affected
        self.assertEqual(self.client.get(self.list_url).status_code, 200)
        self.client.force_authenticate(user=self.other)
        self.assertEqual(self.client.post(self.list_url, self.payload, format="json").status_code, 201)
