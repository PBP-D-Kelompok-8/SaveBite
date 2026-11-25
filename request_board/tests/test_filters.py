from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
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

    def test_title_search_does_not_match_food_or_location(self):
        title = self.item(title="Butuh NaSi", food_item="Roti", location="Depok")
        self.item(title="Lain", food_item="nasi", location="Depok")
        self.item(title="Lain", food_item="Roti", location="Nasi")
        self.assert_matches({"title": " nasi "}, {title.pk})

    def test_title_and_food_controls_are_uniquely_labeled_and_legacy_hidden(self):
        self.item(food_item="Nasi")
        self.client.force_login(self.owner)
        response = self.client.get(self.board, {"q": "nasi", "mine": "on"})
        html = response.content.decode()
        markup = FilterMarkup(html)
        self.assertEqual(sum("data-filter-form" in form for form in markup.forms), 1)
        self.assertEqual(html.count('id="id_title"'), 1)
        self.assertEqual(html.count('id="id_food_item"'), 1)
        self.assertIn('for="id_title">Cari judul permintaan</label>', html)
        self.assertIn('for="id_food_item">Bahan</label>', html)
        self.assertNotIn('Filter lainnya', html)
        for name in ("q", "mine"):
            field = next(field for field in markup.inputs if field.get("name") == name)
            self.assertEqual(field.get("type"), "hidden")
            self.assertTrue(field.get("value"))

    def test_food_item_filter_matches_full_value_case_insensitively(self):
        one = self.item(food_item="Nasi")
        two = self.item(food_item="nAsI")
        self.item(food_item="Nasi putih")
        self.assert_matches({"food_item": " nasi "}, {one.pk, two.pk})

    def test_title_and_food_combine_with_existing_filters(self):
        first = self.item(title="Bantuan Nasi pertama", food_item="Nasi", status="offered", location="Depok")
        second = self.item(title="Bantuan Nasi kedua", food_item="nAsI", status="offered", location="DEPOK")
        self.item(title="Bantuan Nasi", food_item="Nasi putih", status="offered", location="Depok")
        self.item(title="Bantuan lain", food_item="Nasi", status="offered", location="Depok")
        self.item(title="Bantuan Nasi", food_item="Nasi", status="pending", location="Depok")
        self.item(title="Bantuan Nasi", food_item="Nasi", status="offered", location="Bogor")
        self.item(owner=self.other, title="Bantuan Nasi", food_item="Nasi", status="offered", location="Depok")
        self.client.force_login(self.owner)
        params = {"title": "bantuan nasi", "food_item": "NASI", "status": "offered", "location": "depok", "mine": "on"}
        for sort, order in (("newest", [second.pk, first.pk]), ("oldest", [first.pk, second.pk])):
            with self.subTest(sort=sort):
                self.assert_matches({**params, "sort": sort}, order)
                html = self.client.get(self.board, {**params, "sort": sort})
                api = self.client.get(self.api, {**params, "sort": sort})
                self.assertEqual([item.pk for item in html.context["page_obj"]], order)
                self.assertEqual([item["id"] for item in api.json()["data"]], order)

    def test_legacy_query_and_title_combine_with_and(self):
        expected = self.item(title="Bantuan sayur", food_item="Nasi")
        self.item(title="Bantuan nasi", food_item="Roti")
        self.item(title="Lain", food_item="Nasi")
        self.assert_matches({"q": "nasi", "title": "sayur"}, {expected.pk})

    def test_new_filters_apply_before_pagination(self):
        matches = [self.item(title=f"Target {i}", food_item="Nasi") for i in range(21)]
        for i in range(25):
            self.item(title=f"Target lain {i}", food_item="Roti")
        params = {"title": "target", "food_item": "nasi", "page": 2}
        self.assert_matches(params, {matches[0].pk})
        html = self.client.get(self.board, params)
        api = self.client.get(self.api, params)
        self.assertEqual(html.context["page_obj"].paginator.count, 21)
        self.assertEqual(api.json()["pagination"]["total_items"], 21)

    def test_food_options_are_global_nonempty_and_stale_value_is_preserved(self):
        self.item(food_item="Nasi", status="pending")
        self.item(owner=self.other, food_item="Susu & \"Roti\"", status="offered")
        self.item(food_item="")
        self.client.force_login(self.owner)
        response = self.client.get(self.board, {"title": "tidak ada", "status": "pending", "mine": "on"})
        choices = list(response.context["filter_form"].fields["food_item"].widget.choices)
        self.assertEqual(choices, [("", "Semua bahan"), ("Nasi", "Nasi"),
                                   ('Susu & "Roti"', 'Susu & "Roti"')])
        rendered = str(response.context["filter_form"]["food_item"])
        self.assertIn('Susu &amp; &quot;Roti&quot;', rendered)
        stale = 'Hilang & "lama"'
        self.assert_matches({"food_item": stale}, set())
        response = self.client.get(self.board, {"food_item": stale})
        self.assertIn((stale, stale), list(response.context["filter_form"].fields["food_item"].widget.choices))
        self.assertIn('value="Hilang &amp; &quot;lama&quot;" selected', str(response.context["filter_form"]["food_item"]))

    def test_trimmed_food_value_remains_selected_in_widget(self):
        item = self.item(food_item="Nasi")
        for value in (" Nasi ", " Hilang "):
            with self.subTest(value=value):
                expected = {item.pk} if value.strip() == "Nasi" else set()
                self.assert_matches({"food_item": value}, expected)
                response = self.client.get(self.board, {"food_item": value})
                self.assertIn(f'value="{value.strip()}" selected', str(response.context["filter_form"]["food_item"]))

    def test_new_filter_limits_and_querystring_escaping(self):
        for field in ("title", "food_item"):
            for value in ("x" * 121, "bad\x00value"):
                for url in (self.board, self.api):
                    with self.subTest(field=field, value=repr(value), url=url):
                        response = self.client.get(url, {field: value})
                        self.assertEqual(response.status_code, 400)
                        errors = (response.context["filter_form"].errors if url == self.board
                                  else response.json()["error"]["fields"])
                        self.assertIn(field, errors)
        items = [self.item(title="Susu & Roti", food_item="Susu & Roti") for _ in range(21)]
        self.assert_matches({"title": "   ", "food_item": "  ", "page": 2}, {items[0].pk})
        self.assert_matches({"title": "x" * 120, "food_item": "x" * 120}, set())
        response = self.client.get(self.board, {"title": " Susu & Roti ", "food_item": " Susu & Roti "})
        links = [parse_qs(urlsplit(link.get("href", "")).query) for link in FilterMarkup(response.content.decode()).links]
        self.assertIn({"title": ["Susu & Roti"], "food_item": ["Susu & Roti"], "page": ["2"]}, links)

    def test_active_new_filters_remain_resettable_after_last_request_is_deleted(self):
        item = self.item()
        item.delete()
        for params in ({"title": "Roti"}, {"food_item": "Roti"}, {"sort": "oldest"}):
            with self.subTest(params=params):
                response = self.client.get(self.board, params)
                self.assertContains(response, 'aria-label="Filter permintaan"')
                self.assertContains(response, "Reset Filter")
                self.assertTrue(response.context["filters_active"])
        response = self.client.get(self.board)
        self.assertContains(response, "Belum Ada Permintaan")
        self.assertContains(response, 'aria-label="Filter permintaan"')

    def test_api_does_not_load_food_or_location_dropdown_options(self):
        self.item(food_item="Nasi")
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.api, {"food_item": "nasi"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["data"]), 1)
        for query in queries:
            sql = query["sql"].upper()
            self.assertNotIn('SELECT DISTINCT "REQUEST_BOARD_REQUEST"."FOOD_ITEM"', sql)
            self.assertNotIn('SELECT DISTINCT "REQUEST_BOARD_REQUEST"."LOCATION"', sql)

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

    def test_location_combines_with_status_in_html_and_api(self):
        expected = self.item(location="Beji, Depok", status="offered")
        self.item(location="Beji, Depok", status="pending")
        self.item(location="Kukusan, Depok", status="offered")
        self.assert_matches({"location": "beji, depok", "status": "offered"}, {expected.pk})

    def test_oldest_sort_is_applied_before_pagination_in_html_and_api(self):
        items = [self.item(location="Beji, Depok") for _ in range(21)]
        for url in (self.board, self.api):
            response = self.client.get(url, {"location": "Beji, Depok", "sort": "oldest", "page": 2})
            self.assertEqual(response.status_code, 200)
            ids = ([item.pk for item in response.context["page_obj"]] if url == self.board
                   else [item["id"] for item in response.json()["data"]])
            self.assertEqual(ids, [items[-1].pk])

    def test_invalid_sort_and_oversized_location_are_rejected(self):
        for params, field in (({"sort": "random"}, "sort"), ({"location": "x" * 121}, "location")):
            for url in (self.board, self.api):
                response = self.client.get(url, params)
                self.assertEqual(response.status_code, 400)
                errors = (response.context["filter_form"].errors if url == self.board
                          else response.json()["error"]["fields"])
                self.assertIn(field, errors)

    def test_empty_board_and_filtered_empty_result_keep_filters_and_reset(self):
        response = self.client.get(self.board)
        self.assertContains(response, "Belum Ada Permintaan")
        self.assertContains(response, 'aria-label="Filter permintaan"')
        self.item()
        response = self.client.get(self.board, {"status": "fulfilled"})
        self.assertContains(response, 'aria-label="Filter permintaan"')
        self.assertContains(response, "Reset Filter")

    def test_location_options_come_from_all_requests_and_survive_filtering(self):
        self.item(location="Beji, Depok", status="pending")
        self.item(location="Kukusan, Depok", status="offered")
        self.item(location="")
        response = self.client.get(self.board, {"status": "pending"})
        choices = list(response.context["filter_form"].fields["location"].widget.choices)
        self.assertEqual(choices, [("", "Semua lokasi"), ("Beji, Depok", "Beji, Depok"),
                                   ("Kukusan, Depok", "Kukusan, Depok")])

    def test_pagination_preserves_location_and_sort(self):
        for _ in range(21):
            self.item(location="Beji & Kukusan")
        response = self.client.get(self.board, {"location": "Beji & Kukusan", "sort": "oldest"})
        markup = FilterMarkup(response.content.decode())
        links = [parse_qs(urlsplit(link.get("href", "")).query) for link in markup.links]
        self.assertIn({"location": ["Beji & Kukusan"], "sort": ["oldest"], "page": ["2"]}, links)
