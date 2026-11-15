from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from request_board.models import Request, RequestOffer


AJAX = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


class BoardAjaxTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.owner = users.objects.create_user(username="ajax-owner")
        cls.donor = users.objects.create_user(username="ajax-donor")
        cls.other = users.objects.create_user(username="ajax-other")

    def setUp(self):
        self.item = Request.objects.create(owner=self.owner, title="Bantuan", food_item="Nasi", quantity=1, unit="porsi")
        self.board = reverse("request_board:request_board")
        self.create = reverse("request_board:request_create")

    def action(self, name):
        return reverse("request_board:request_" + name, args=[self.item.pk])

    def test_board_returns_partial_and_varies_by_header(self):
        full = self.client.get(self.board)
        partial = self.client.get(self.board, **AJAX)
        self.assertContains(full, "<html")
        self.assertNotContains(partial, "<html")
        self.assertContains(partial, "Bantuan")
        for response in (full, partial):
            self.assertIn("X-Requested-With", response.headers.get("Vary", ""))

    def test_create_validation_and_success(self):
        self.client.force_login(self.owner)
        invalid = self.client.post(self.create, {"title": "tetap"}, **AJAX)
        self.assertEqual(invalid.status_code, 400)
        self.assertFalse(invalid.json()["ok"])
        self.assertIn("food_item", invalid.json()["fields"])
        valid = self.client.post(self.create, {"title": "Baru", "food_item": "Roti", "quantity": "1", "unit": "porsi"}, **AJAX)
        self.assertEqual(valid.status_code, 200)
        self.assertTrue(valid.json()["ok"])
        self.assertEqual(valid.json()["redirect_url"], self.board)
        self.assertTrue(Request.objects.filter(title="Baru", owner=self.owner).exists())

    def test_session_expiry_and_permissions(self):
        expired = self.client.post(self.action("offer"), **AJAX)
        self.assertEqual(expired.status_code, 401)
        self.assertIn("login_url", expired.json())
        self.client.force_login(self.other)
        denied = self.client.post(self.action("delete"), **AJAX)
        self.assertEqual(denied.status_code, 403)
        self.assertFalse(denied.json()["ok"])
        self.assertTrue(Request.objects.filter(pk=self.item.pk).exists())

    def test_offer_select_fulfill_delete_and_conflicts(self):
        self.client.force_login(self.owner)
        conflict = self.client.post(self.action("fulfill"), **AJAX)
        self.assertEqual(conflict.status_code, 409)
        self.client.force_login(self.donor)
        offered = self.client.post(self.action("offer"), **AJAX)
        self.assertTrue(offered.json()["ok"])
        offer = RequestOffer.objects.get(request=self.item, offered_by=self.donor)
        self.client.force_login(self.owner)
        select = reverse("request_board:request_select_offer", args=[self.item.pk, offer.pk])
        self.assertTrue(self.client.post(select, **AJAX).json()["ok"])
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(self.action("offer"), **AJAX).status_code, 409)
        self.client.force_login(self.owner)
        self.assertTrue(self.client.post(self.action("fulfill"), **AJAX).json()["ok"])
        self.assertTrue(self.client.post(self.action("delete"), **AJAX).json()["ok"])
        self.assertFalse(Request.objects.filter(pk=self.item.pk).exists())

    def test_csrf_enforced_and_html_fallback(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        self.assertEqual(client.post(self.action("delete"), **AJAX).status_code, 403)
        self.assertTrue(Request.objects.filter(pk=self.item.pk).exists())
        client.get(self.board)
        token = client.cookies["csrftoken"].value
        response = client.post(self.action("delete"), {"csrfmiddlewaretoken": token})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Request.objects.filter(pk=self.item.pk).exists())

    def test_unsupported_method_is_rejected(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.action("delete"), **AJAX).status_code, 405)

    def test_ajax_selection_checks_owner_and_foreign_offer(self):
        other = Request.objects.create(owner=self.owner, title="Lain", food_item="Roti", quantity=1, unit="porsi")
        foreign = RequestOffer.objects.create(request=other, offered_by=self.donor)
        url = reverse("request_board:request_select_offer", args=[self.item.pk, foreign.pk])
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(url, **AJAX).status_code, 404)
        own = RequestOffer.objects.create(request=self.item, offered_by=self.donor)
        url = reverse("request_board:request_select_offer", args=[self.item.pk, own.pk])
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(url, **AJAX).status_code, 403)
        self.item.refresh_from_db()
        self.assertIsNone(self.item.selected_offer_id)

    def test_ajax_partial_filters_and_privacy(self):
        RequestOffer.objects.create(request=self.item, offered_by=self.donor)
        response = self.client.get(self.board, {"q": "Bantuan"}, **AJAX)
        self.assertContains(response, "Bantuan")
        self.assertNotContains(response, "ajax-donor")
        self.assertEqual(self.client.get(self.board, {"status": "invalid"}, **AJAX).status_code, 400)
        self.assertEqual(self.client.get(self.board, {"mine": "on"}, **AJAX).status_code, 401)

    def test_partial_escapes_user_content(self):
        self.item.title = '<img src=x onerror=alert(1)>'
        self.item.save(update_fields=['title'])
        response = self.client.get(self.board, **AJAX)
        self.assertContains(response, '&lt;img src=x onerror=alert(1)&gt;', html=False)
        self.assertNotContains(response, '<img src=x onerror=alert(1)>')
