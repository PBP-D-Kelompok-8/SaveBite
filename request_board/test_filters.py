from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse

from request_board.models import Request, RequestOffer
from request_board.services import get_board_page


class FilterMarkup(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.links = []
        self.forms = []
        self.inputs = []
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            self.links.append(attrs)
        elif tag == "form":
            self.forms.append(attrs)
        elif tag == "input":
            self.inputs.append(attrs)


class BoardFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.owner = users.objects.create_user(username="filter-owner")
        cls.other = users.objects.create_user(username="filter-other")
        cls.donor = users.objects.create_user(username="filter-private-donor")
        cls.board = reverse("request_board:request_board")
        cls.api = reverse("request_board:api_request_collection")

    def item(self, **overrides):
        values = dict(owner=self.owner, title="Permintaan", food_item="Roti",
                      quantity=1, unit="porsi", location="Jakarta", status="pending")
        values.update(overrides)
        return Request.objects.create(**values)

    def assert_matches(self, params, expected):
        html = self.client.get(self.board, params)
        api = self.client.get(self.api, params)
        self.assertEqual(html.status_code, 200)
        self.assertEqual(api.status_code, 200)
        self.assertEqual({item.pk for item in html.context["page_obj"]}, set(expected))
        self.assertEqual({item["id"] for item in api.json()["data"]}, set(expected))

    def test_search_matches_title_food_or_location_case_insensitively(self):
        title = self.item(title="Butuh TeMu")
        food = self.item(food_item="TEMU putih")
        location = self.item(location="Jalan temu")
        self.item(title="lain")
        self.assert_matches({"q": "tEmU"}, {title.pk, food.pk, location.pk})

    def test_status_and_mine_combine_with_search(self):
        expected = self.item(title="Nasi dibutuhkan", status="offered")
        self.item(title="Nasi lain", status="pending")
        self.item(owner=self.other, title="Nasi luar", status="offered")
        self.item(title="Tanpa kecocokan", status="offered")
        self.client.force_login(self.owner)
        self.assert_matches({"q": "nAsI", "status": "offered", "mine": "on"}, {expected.pk})
        self.client.force_login(self.other)
        self.assert_matches({"q": "nasi", "status": "offered", "mine": "on"},
                            {Request.objects.get(title="Nasi luar").pk})

    def test_status_filter_alone(self):
        expected = self.item(status="fulfilled")
        self.item(status="pending")
        self.assert_matches({"status": "fulfilled"}, {expected.pk})

    def test_guest_mine_rejected_for_html_json_and_service(self):
        self.item()
        html = self.client.get(self.board, {"mine": "on"})
        api = self.client.get(self.api, {"mine": "on"})
        self.assertContains(html, "masuk", status_code=401, html=False)
        self.assertEqual(api.status_code, 401)
        self.assertEqual(api.json()["error"]["code"], "authentication_required")
        with self.assertRaises(PermissionDenied):
            get_board_page(actor=AnonymousUser(), filters={"mine": True})

    def test_invalid_status_and_oversized_query_show_errors(self):
        for params, field in (({"status": "unknown"}, "status"), ({"q": "x" * 121}, "q")):
            with self.subTest(field=field):
                html = self.client.get(self.board, params)
                api = self.client.get(self.api, params)
                self.assertEqual(html.status_code, 400)
                self.assertIn(field, html.context["filter_form"].errors)
                self.assertEqual(api.status_code, 400)
                self.assertIn(field, api.json()["error"]["fields"])

    def test_filter_is_applied_before_pagination(self):
        matches = [self.item(title=f"Target {i}") for i in range(21)]
        for i in range(25):
            self.item(title=f"Lain {i}")
        html = self.client.get(self.board, {"q": "target", "page": 2})
        api = self.client.get(self.api, {"q": "target", "page": 2})
        self.assertEqual(html.status_code, 200)
        self.assertEqual(api.status_code, 200)
        self.assertEqual([item.pk for item in html.context["page_obj"]], [matches[0].pk])
        self.assertEqual(api.json()["pagination"]["total_items"], 21)
        self.assertEqual([item["id"] for item in api.json()["data"]], [matches[0].pk])

    def test_pagination_keeps_encoded_filters_and_form_resets_page(self):
        for i in range(21):
            self.item(title=f"Susu & Roti {i}", status="offered")
        self.client.force_login(self.owner)
        params = {"q": "Susu & Roti", "status": "offered", "mine": "on", "page": 2}
        response = self.client.get(self.board, params)
        markup = FilterMarkup(response.content.decode())
        self.assertEqual(response.status_code, 200)
        links = [parse_qs(urlsplit(link.get("href", "")).query) for link in markup.links]
        self.assertIn({"q": ["Susu & Roti"], "status": ["offered"], "mine": ["on"], "page": ["1"]}, links)
        self.assertTrue(any(form.get("method", "").lower() == "get" for form in markup.forms))
        self.assertFalse(any(field.get("name") == "page" for field in markup.inputs))
        self.assertTrue(any(link.get("href") == self.board for link in markup.links))

    def test_empty_filtered_results_distinct_from_empty_board(self):
        self.item()
        response = self.client.get(self.board, {"q": "tidak-cocok"})
        self.assertContains(response, "Tidak Ada Hasil")
        self.assertTemplateUsed(response, "request_board/_board_results.html")

    def test_filtered_results_do_not_expose_other_owners_donors(self):
        item = self.item(title="Rahasia hasil", status="offered")
        offer = RequestOffer.objects.create(request=item, offered_by=self.donor)
        item.selected_offer = offer
        item.save(update_fields=["selected_offer"])
        for actor in (None, self.other):
            with self.subTest(actor=actor):
                self.client.logout()
                if actor:
                    self.client.force_login(actor)
                html = self.client.get(self.board, {"q": "rahasia"})
                api = self.client.get(self.api, {"q": "rahasia"})
                self.assertContains(html, "Rahasia hasil")
                self.assertNotContains(html, "filter-private-donor")
                self.assertNotIn("filter-private-donor", api.content.decode())
                self.assertNotIn("selected_offer", api.json()["data"][0])

    def test_existing_page_validation_remains_in_force_with_filters(self):
        self.item()
        for url in (self.board, self.api):
            for suffix, status in (("?q=roti&page=0", 400), ("?q=roti&page=2", 404),
                                   ("?q=roti&page=1&page=2", 400)):
                with self.subTest(url=url, suffix=suffix):
                    self.assertEqual(self.client.get(url + suffix).status_code, status)

    def test_default_service_signature_remains_compatible(self):
        expected = self.item()
        self.assertEqual(list(get_board_page()), [expected])
