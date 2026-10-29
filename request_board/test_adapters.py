from datetime import datetime, timedelta, timezone
from decimal import Decimal
from importlib import import_module
import re

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.middleware.csrf import get_token
from django.test import Client, RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import NoReverseMatch, resolve, reverse
from django.utils.dateparse import parse_datetime

from request_board.models import Request, RequestOffer


class RequestListAdapterTests(TestCase):
    """L01–L06: public reads only; final HTML and mutation phases are separate."""

    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.owner = user_model.objects.create_user(
            username="public_owner", email="private-owner@example.invalid",
            first_name="PrivateOwnerFirst", last_name="PrivateOwnerLast",
        )
        cls.donor = user_model.objects.create_user(
            username="private_donor_one", email="private-donor@example.invalid",
            first_name="PrivateDonorFirst", last_name="PrivateDonorLast",
        )
        cls.other_donor = user_model.objects.create_user(
            username="private_donor_two",
        )
        cls.outsider = user_model.objects.create_user(username="private_outsider")
        cls.fixed_time = datetime(2020, 1, 2, 12, tzinfo=timezone.utc)

    def collection_url(self):
        try:
            url = reverse("request_board:api_request_collection")
        except NoReverseMatch:
            self.fail(
                "Route request_board:api_request_collection belum tersedia; "
                "Task 1B harus memasang GET /requests pada root URLconf."
            )
        self.assertEqual(url, "/requests")
        self.assertEqual(resolve(url).view_name, "request_board:api_request_collection")
        return url

    def make_request(self, *, status="pending", created_at=None, **overrides):
        values = {
            "owner": self.owner,
            "title": "Butuh beras", "food_item": "Beras",
            "quantity": Decimal("2.50"), "unit": "kg",
            "description": "", "location": "", "status": status,
        }
        values.update(overrides)
        food_request = Request.objects.create(**values)
        Request.objects.filter(pk=food_request.pk).update(
            created_at=created_at or self.fixed_time, updated_at=self.fixed_time,
        )
        food_request.refresh_from_db()
        return food_request

    def make_offer(self, food_request, donor):
        return RequestOffer.objects.create(request=food_request, offered_by=donor)

    def csrf_client(self, actor=None):
        client = Client(enforce_csrf_checks=True)
        if actor is not None:
            client.force_login(actor)
        # No production token endpoint or template is needed for method tests.
        token_request = RequestFactory().get("/requests")
        token = get_token(token_request)
        client.cookies["csrftoken"] = token_request.META["CSRF_COOKIE"]
        client.defaults["HTTP_X_CSRFTOKEN"] = token
        return client

    def assert_list_response(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"].split(";")[0], "application/json")
        payload = response.json()
        self.assertEqual(set(payload), {"data", "pagination"})
        self.assertIsInstance(payload["data"], list)
        return payload

    def assert_pagination(self, payload, *, page, total_items):
        total_pages = max(1, (total_items + 19) // 20)
        self.assertEqual(payload["pagination"], {
            "page": page, "page_size": 20, "total_items": total_items,
            "total_pages": total_pages, "has_next": page < total_pages,
            "has_previous": page > 1,
        })

    def assert_public_item(self, item, food_request, *, offer_count):
        self.assertEqual(set(item), {
            "id", "title", "food_item", "quantity", "unit", "description",
            "location", "owner", "status", "expires_at", "created_at", "updated_at", "offer_count",
        })
        for field in ("id", "title", "food_item", "unit", "description", "location", "status"):
            self.assertEqual(item[field], getattr(food_request, field), field)
        self.assertEqual(item["quantity"], format(food_request.quantity, ".2f"))
        self.assertEqual(item["owner"], {"display_name": food_request.owner.get_username()})
        self.assertIs(type(item["offer_count"]), int)
        self.assertEqual(item["offer_count"], offer_count)
        for field in ("created_at", "updated_at"):
            self.assertIsInstance(item[field], str)
            timestamp = parse_datetime(item[field])
            self.assertIsNotNone(timestamp, field)
            self.assertIsNotNone(timestamp.utcoffset(), field)
            self.assertEqual(timestamp, getattr(food_request, field), field)

    def assert_json_error(self, response, *, status, code):
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.headers["Content-Type"].split(";")[0], "application/json")
        payload = response.json()
        self.assertEqual(set(payload), {"error"})
        self.assertEqual(set(payload["error"]), {"code", "message", "fields"})
        self.assertEqual(payload["error"]["code"], code)
        self.assertIsInstance(payload["error"]["message"], str)
        self.assertTrue(payload["error"]["message"])
        self.assertEqual(payload["error"]["fields"], {})

    def database_snapshot(self):
        return {
            model._meta.label: list(model.objects.order_by("pk").values())
            for model in (Request, RequestOffer, get_user_model())
        }

    def test_public_list_is_200_and_ordered(self):
        """L01: all states, timestamp order and descending ID tie-breaker."""
        url = self.collection_url()
        older = self.make_request(status="fulfilled", created_at=self.fixed_time - timedelta(days=1))
        pending = self.make_request()
        offered = self.make_request(status="offered")
        self.make_offer(offered, self.donor)

        payload = self.assert_list_response(self.csrf_client().get(url))

        expected = [(offered, 1), (pending, 0), (older, 0)]
        self.assertEqual([item["id"] for item in payload["data"]], [row.pk for row, _ in expected])
        for item, (food_request, count) in zip(payload["data"], expected):
            self.assert_public_item(item, food_request, offer_count=count)
        self.assert_pagination(payload, page=1, total_items=3)

    def test_empty_and_page_boundaries(self):
        """L02: empty page and 21 rows split 20/1; unrelated query keys ignored."""
        url = self.collection_url()
        client = self.csrf_client()
        empty = self.assert_list_response(client.get(url))
        self.assertEqual(empty["data"], [])
        self.assert_pagination(empty, page=1, total_items=0)
        self.assert_json_error(client.get(url, {"page": "2"}), status=404, code="page_not_found")
        rows = [self.make_request() for _ in range(21)]

        first = self.assert_list_response(client.get(url))
        second = self.assert_list_response(client.get(url, {"page": "2"}))

        self.assertEqual([item["id"] for item in first["data"]], [row.pk for row in reversed(rows[1:])])
        self.assertEqual([item["id"] for item in second["data"]], [rows[0].pk])
        self.assert_pagination(first, page=1, total_items=21)
        self.assert_pagination(second, page=2, total_items=21)
        ignored = self.assert_list_response(client.get(url, {
            "owner": self.outsider.pk, "search": "absent",
            "order_by": "id", "page_size": "1",
        }))
        self.assertEqual(ignored, first)

    def test_invalid_and_repeated_page(self):
        """L03/F03: ASCII positive page, repeated keys and conversion-limit cases."""
        url = self.collection_url()
        self.make_request()
        client = self.csrf_client()
        invalid = ["", "0", "000", "-1", "+1", " 1", "1 ", "1.0", "1e0", "abc", "١", "１"]
        for page in invalid:
            with self.subTest(invalid_page=repr(page)):
                self.assert_json_error(client.get(url, {"page": page}), status=400, code="invalid_page")
        for query in ("page=1&page=1", "page=1&page=2", "page=&page=1"):
            with self.subTest(repeated_page=query):
                self.assert_json_error(client.get(url + "?" + query), status=400, code="invalid_page")
        for label, page in (("leading_zero", "001"), ("long_leading_zeros", "0" * 5000 + "1")):
            with self.subTest(valid_page=label):
                payload = self.assert_list_response(client.get(url, {"page": page}))
                self.assert_pagination(payload, page=1, total_items=1)
                self.assertEqual(len(payload["data"]), 1)
        for label, page in (("next_page", "2"), ("beyond_integer_conversion_limit", "9" * 5000)):
            with self.subTest(out_of_range=label):
                self.assert_json_error(client.get(url, {"page": page}), status=404, code="page_not_found")

    def test_public_projection_never_contains_offer_identity(self):
        """L04: owner and donors get the same exact public projection as guest."""
        url = self.collection_url()
        food_request = self.make_request(status="offered")
        self.make_offer(food_request, self.donor)
        self.make_offer(food_request, self.other_donor)
        guest_payload = None
        for label, actor in (("guest", None), ("owner", self.owner), ("donor", self.donor), ("outsider", self.outsider)):
            with self.subTest(caller=label):
                response = self.csrf_client(actor).get(url)
                payload = self.assert_list_response(response)
                self.assertEqual(len(payload["data"]), 1)
                self.assert_public_item(payload["data"][0], food_request, offer_count=2)
                for private_value in (
                    self.owner.email, self.owner.first_name, self.owner.last_name,
                    self.donor.get_username(), self.other_donor.get_username(),
                    self.donor.email, self.donor.first_name, self.donor.last_name,
                ):
                    self.assertNotIn(private_value, response.content.decode())
                if guest_payload is None:
                    guest_payload = payload
                self.assertEqual(payload, guest_payload)

    def test_counts_and_read_queries_are_constant(self):
        """L05: real HTTP queries stay constant; evaluated builder uses <=2 reads."""
        url = self.collection_url()
        services = import_module("request_board.services")
        first = self.make_request(status="offered")
        self.make_offer(first, self.donor)
        self.make_offer(first, self.other_donor)
        callers = [("guest", None), ("owner", self.owner), ("donor", self.donor)]
        clients = {label: self.csrf_client(actor) for label, actor in callers}
        endpoint_counts = {}
        builder_counts = {}
        expected_counts = {first.pk: 2}
        expected_owners = {first.pk: self.owner.get_username()}
        donor_request_ids = {first.pk}

        for size in (1, 20):
            if size == 20:
                second = self.make_request(status="offered", owner=self.outsider)
                self.make_offer(second, self.donor)
                expected_counts[second.pk] = 1
                expected_owners[second.pk] = self.outsider.get_username()
                donor_request_ids.add(second.pk)
                for _ in range(18):
                    food_request = self.make_request()
                    expected_counts[food_request.pk] = 0
                    expected_owners[food_request.pk] = self.owner.get_username()
            for label, actor in callers:
                with self.subTest(cards=size, caller=label):
                    with CaptureQueriesContext(connection) as endpoint_queries:
                        response = clients[label].get(url)
                        response.content
                    payload = self.assert_list_response(response)
                    self.assertEqual({item["id"]: item["offer_count"] for item in payload["data"]}, expected_counts)
                    endpoint_counts[label, size] = len(endpoint_queries)

                    with CaptureQueriesContext(connection) as builder_queries:
                        page = services.get_board_page(actor=actor or AnonymousUser(), page_value="1")
                        total_items = page.paginator.count
                        rows = list(page.object_list)
                        annotations = {
                            row.pk: (row.offer_count, row.has_offered, row.owner.get_username())
                            for row in rows
                        }
                    builder_counts[label, size] = len(builder_queries)
                    self.assertEqual(total_items, size)
                    self.assertEqual(len(rows), size)
                    self.assertLessEqual(len(builder_queries), 2)
                    self.assertEqual({pk: values[0] for pk, values in annotations.items()}, expected_counts)
                    for row in rows:
                        expected_offered = actor == self.donor and row.pk in donor_request_ids
                        self.assertIs(annotations[row.pk][1], expected_offered)
                        self.assertEqual(annotations[row.pk][2], expected_owners[row.pk])
        for label, _ in callers:
            with self.subTest(constant_queries_for=label):
                self.assertEqual(endpoint_counts[label, 1], endpoint_counts[label, 20])
                self.assertEqual(builder_counts[label, 1], builder_counts[label, 20])

    def test_read_and_wrong_method_never_write(self):
        """L06/F05: permanent invalid methods only; POST phase check belongs to B/C."""
        url = self.collection_url()
        food_request = self.make_request(status="offered")
        self.make_offer(food_request, self.donor)
        self.make_request(owner=self.outsider)
        invalid_methods = ("PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
        for label, actor in (("guest", None), ("owner", self.owner), ("donor", self.donor)):
            client = self.csrf_client(actor)
            for method in ("GET",) + invalid_methods:
                with self.subTest(caller=label, method=method):
                    before = self.database_snapshot()
                    with CaptureQueriesContext(connection) as request_queries:
                        response = client.generic(method, url)
                    self.assertEqual(self.database_snapshot(), before)
                    writes = [query["sql"] for query in request_queries if re.match(
                        r"\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP)\b", query["sql"], re.IGNORECASE,
                    )]
                    self.assertEqual(writes, [])
                    if method == "GET":
                        self.assert_list_response(response)
                        continue
                    self.assertEqual(response.status_code, 405)
                    allowed = {value.strip() for value in response.headers["Allow"].split(",")}
                    self.assertIn("GET", allowed)
                    self.assertTrue(allowed.isdisjoint(invalid_methods))
                    if method == "HEAD":
                        # Django's HTTP client strips HEAD bodies; inspect metadata.
                        self.assertEqual(response.content, b"")
                        self.assertEqual(response.headers["Content-Type"].split(";")[0], "application/json")
                    else:
                        self.assert_json_error(response, status=405, code="method_not_allowed")


class RequestCreateAdapterTests(TestCase):
    """J01-J07: create transport, session actor, domain errors and real CSRF."""

    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.actor = user_model.objects.create_user(
            username="create_actor", email="private-create@example.invalid",
            first_name="PrivateCreateFirst", last_name="PrivateCreateLast",
        )
        cls.other_owner = user_model.objects.create_user(username="other_create_owner")
        cls.donor = user_model.objects.create_user(username="private_create_donor")
        cls.existing_request = Request.objects.create(
            owner=cls.other_owner, title="Permintaan lain", food_item="Telur",
            quantity=Decimal("3.00"), unit="butir", status="offered",
        )
        RequestOffer.objects.create(request=cls.existing_request, offered_by=cls.donor)

    def setUp(self):
        self.url = reverse("request_board:api_request_collection")
        self.assertEqual(self.url, "/requests")
        self.client_with_csrf = self.csrf_client(self.actor)

    def valid_payload(self, **overrides):
        payload = {
            "title": "Butuh beras", "food_item": "Beras",
            "quantity": "2.50", "unit": "kg",
            "description": "Untuk makan bersama", "location": "Depok",
        }
        payload.update(overrides)
        return payload

    def encode_payload(self, payload):
        import json

        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def csrf_client(self, actor=None):
        client = Client(enforce_csrf_checks=True)
        if actor is not None:
            client.force_login(actor)
        # Use Django's real cookie/header pair without adding a token endpoint.
        token_request = RequestFactory().get(self.url)
        client.defaults["HTTP_X_CSRFTOKEN"] = get_token(token_request)
        client.cookies["csrftoken"] = token_request.META["CSRF_COOKIE"]
        return client

    def database_snapshot(self):
        return {
            model._meta.label: list(model.objects.order_by("pk").values())
            for model in (Request, RequestOffer, get_user_model())
        }

    def assert_json_error(self, response, *, status, code, field_names=()):
        self.assertEqual(response.status_code, status)
        self.assertNotIn("Location", response.headers)
        self.assertEqual(response.headers["Content-Type"].split(";")[0], "application/json")
        payload = response.json()
        self.assertEqual(set(payload), {"error"})
        error = payload["error"]
        self.assertEqual(set(error), {"code", "message", "fields"})
        self.assertEqual(error["code"], code)
        self.assertIsInstance(error["message"], str)
        self.assertTrue(error["message"].strip())
        self.assertIsInstance(error["fields"], dict)
        self.assertEqual(set(error["fields"]), set(field_names))
        for messages in error["fields"].values():
            self.assertIsInstance(messages, list)
            self.assertTrue(messages)
            for message in messages:
                self.assertIsInstance(message, str)
                self.assertTrue(message.strip())

    def assert_rejected_without_write(
        self, client, *, body, code, status=400, content_type="application/json",
        method="POST", field_names=(),
    ):
        before = self.database_snapshot()

        with CaptureQueriesContext(connection) as captured_queries:
            # Django omits content_type for empty bodies unless supplied in environ.
            response = client.generic(
                method, self.url, body, content_type=content_type, CONTENT_TYPE=content_type,
            )

        self.assertEqual(response.wsgi_request.META.get("CONTENT_TYPE"), content_type)
        self.assertEqual(self.database_snapshot(), before)
        writes = [query["sql"] for query in captured_queries if re.match(
            r"\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP)\b",
            query["sql"], re.IGNORECASE,
        )]
        self.assertEqual(writes, [], "A rejected request must not issue SQL writes.")
        self.assert_json_error(response, status=status, code=code, field_names=field_names)
        return response

    def assert_created_request(self, *, body, expected, content_type="application/json"):
        before = self.database_snapshot()

        response = self.client_with_csrf.generic(
            "POST", self.url, body, content_type=content_type,
        )

        self.assertEqual(response.status_code, 201)
        self.assertNotIn("Location", response.headers)
        self.assertEqual(response.headers["Content-Type"].split(";")[0], "application/json")
        payload = response.json()
        self.assertEqual(set(payload), {"data"})
        item = payload["data"]
        self.assertEqual(set(item), {
            "id", "title", "food_item", "quantity", "unit", "description",
            "location", "owner", "status", "expires_at", "created_at", "updated_at", "offer_count",
        })
        food_request = Request.objects.get(pk=item["id"])
        self.assertEqual(food_request.owner_id, self.actor.pk)
        self.assertEqual(food_request.status, "pending")
        self.assertEqual(item["owner"], {"display_name": self.actor.get_username()})
        self.assertEqual(item["status"], "pending")
        self.assertIs(type(item["offer_count"]), int)
        self.assertEqual(item["offer_count"], 0)
        for field in ("title", "food_item", "unit", "description", "location"):
            self.assertEqual(getattr(food_request, field), expected.get(field, ""), field)
            self.assertEqual(item[field], getattr(food_request, field), field)
        self.assertEqual(food_request.quantity, Decimal(str(expected["quantity"])))
        self.assertEqual(item["quantity"], format(food_request.quantity, ".2f"))
        for field in ("created_at", "updated_at"):
            self.assertIsInstance(item[field], str)
            timestamp = parse_datetime(item[field])
            self.assertIsNotNone(timestamp, field)
            self.assertIsNotNone(timestamp.utcoffset(), field)
            self.assertEqual(timestamp, getattr(food_request, field), field)
        for private_value in (
            self.actor.email, self.actor.first_name, self.actor.last_name,
            self.donor.get_username(),
        ):
            self.assertNotIn(private_value, response.content.decode())
        after = self.database_snapshot()
        request_label = Request._meta.label
        created_rows = [row for row in after[request_label] if row["id"] == food_request.pk]
        self.assertEqual(len(created_rows), 1)
        after[request_label] = [row for row in after[request_label] if row["id"] != food_request.pk]
        self.assertEqual(after, before, "Only the new request may change domain/user data.")
        return item

    def test_create_returns_201_with_session_owner(self):
        """J01: session owns the new pending item, also visible in public GET."""
        expected = self.valid_payload()

        item = self.assert_created_request(body=self.encode_payload(expected), expected=expected)

        before_read = self.database_snapshot()
        response = Client(enforce_csrf_checks=True).get(self.url)
        self.assertEqual(response.status_code, 200)
        listed = {row["id"]: row for row in response.json()["data"]}
        self.assertEqual(listed[item["id"]], item)
        self.assertEqual(self.database_snapshot(), before_read)

    def test_create_rejects_transport_edge_cases(self):
        """J02/F01: named transport cases lock codes and validation precedence."""
        valid_body = self.encode_payload(self.valid_payload())
        transport_cases = [
            ("empty_body", b"", "application/json", "invalid_json"),
            ("malformed_json", b'{"title":', "application/json", "invalid_json"),
            ("trailing_document", valid_body + b" {}", "application/json", "invalid_json"),
            ("invalid_utf8", b'{"title":"\xff"}', "application/json", "invalid_json"),
            ("duplicate_key", b'{"title":"one","title":"two"}', "application/json", "invalid_json"),
            ("nested_duplicate_before_shape", b'[{"x":1,"x":2}]', "application/json", "invalid_json"),
            ("media_type_before_decode", b"{", "text/plain", "invalid_content_type"),
            ("form_media_type", valid_body, "application/x-www-form-urlencoded", "invalid_content_type"),
            ("vendor_json", valid_body, "application/problem+json", "invalid_content_type"),
            ("missing_media_type", valid_body, "", "invalid_content_type"),
            ("charset_before_decode", b"\xff", "application/json; charset=latin-1", "invalid_content_type"),
            ("unsupported_charset", valid_body, "application/json; charset=utf-16", "invalid_content_type"),
        ]
        for literal in ("NaN", "Infinity", "-Infinity"):
            body = valid_body.replace(b'"2.50"', literal.encode())
            transport_cases.append(("nonfinite_" + literal, body, "application/json", "invalid_json"))
        for name, value in (
            ("array", []), ("string", "text"), ("number", 2), ("null", None), ("boolean", True),
        ):
            transport_cases.append((
                "top_level_" + name, self.encode_payload(value), "application/json", "invalid_payload",
            ))
        for case_name, body, content_type, code in transport_cases:
            with self.subTest(case=case_name, expected_code=code):
                self.assert_rejected_without_write(
                    self.client_with_csrf, body=body, content_type=content_type, code=code,
                )

        invalid_field_types = []
        for field in ("title", "food_item", "unit", "description", "location"):
            for name, value in (("null", None), ("boolean", True), ("number", 5), ("list", []), ("object", {})):
                invalid_field_types.append((field + "_" + name, self.valid_payload(**{field: value})))
        for name, value in (("null", None), ("true", True), ("false", False), ("list", []), ("object", {})):
            invalid_field_types.append(("quantity_" + name, self.valid_payload(quantity=value)))
        invalid_field_types.append(("type_before_domain", self.valid_payload(quantity=False, title="")))
        for case_name, payload in invalid_field_types:
            with self.subTest(case=case_name, expected_code="invalid_payload"):
                self.assert_rejected_without_write(
                    self.client_with_csrf, body=self.encode_payload(payload), code="invalid_payload",
                )

        # Raw numeric lexemes exercise Decimal decoding without a Python float round-trip.
        for case_name, numeric_quantity, content_type in (
            ("integer_absent_charset", b"2", "application/json"),
            ("decimal_utf8", b"2.50", "application/json; charset=UTF-8"),
            ("exponent_utf8", b"25e-1", "application/json; charset=utf-8"),
        ):
            with self.subTest(valid_case=case_name):
                expected = self.valid_payload(quantity=numeric_quantity.decode())
                self.assert_created_request(
                    body=valid_body.replace(b'"2.50"', numeric_quantity),
                    expected=expected, content_type=content_type,
                )

    def test_create_domain_validation_matches_form(self):
        """J03: field errors match RequestForm; optional text may be absent/empty."""
        from request_board.forms import RequestForm

        domain_cases = [
            ("zero", {"quantity": "0"}),
            ("negative", {"quantity": "-1"}),
            ("excess_precision", {"quantity": "1.001"}),
            ("overflow", {"quantity": "1000000.00"}),
            ("nan_string_is_domain", {"quantity": "NaN"}),
            ("infinity_string_is_domain", {"quantity": "Infinity"}),
            ("blank_title", {"title": "   "}),
            ("blank_food_item", {"food_item": ""}),
            ("blank_quantity", {"quantity": ""}),
            ("blank_unit", {"unit": ""}),
            ("invalid_unit", {"unit": "bucket"}),
            ("long_title", {"title": "a" * 121}),
            ("long_food_item", {"food_item": "a" * 121}),
            ("long_description", {"description": "a" * 1001}),
            ("long_location", {"location": "a" * 121}),
            ("multiple_field_errors", {"title": "", "quantity": "-1", "unit": "bucket"}),
        ]
        payload_cases = [(name, self.valid_payload(**changes)) for name, changes in domain_cases]
        for field in ("title", "food_item", "quantity", "unit"):
            payload = self.valid_payload()
            del payload[field]
            payload_cases.append(("missing_" + field, payload))
        for case_name, payload in payload_cases:
            with self.subTest(case=case_name):
                form = RequestForm(data=payload)
                self.assertFalse(form.is_valid(), "The fixture must violate the existing form.")
                self.assert_rejected_without_write(
                    self.client_with_csrf, body=self.encode_payload(payload),
                    code="validation_error", field_names=form.errors.keys(),
                )

        # A float parser would silently round this invalid precision down to 1.23.
        with self.subTest(case="numeric_precision_survives_decode"):
            body = self.encode_payload(self.valid_payload()).replace(b'"2.50"', b"1.23000000000000001")
            self.assert_rejected_without_write(
                self.client_with_csrf, body=body, code="validation_error", field_names=("quantity",),
            )
        for case_name, optional_values in (("absent", None), ("empty", "")):
            with self.subTest(valid_optional_text=case_name):
                payload = self.valid_payload()
                for field in ("description", "location"):
                    if optional_values is None:
                        del payload[field]
                    else:
                        payload[field] = optional_values
                self.assert_created_request(body=self.encode_payload(payload), expected=payload)

    def test_create_rejects_internal_and_unknown_fields(self):
        """J04/F01: spoofed fields never reach persistence; extra keys win first."""
        extra_fields = {
            "owner": self.other_owner.pk, "owner_id": self.other_owner.pk,
            "status": "fulfilled", "offered_by": self.donor.pk,
            "id": self.existing_request.pk, "created_at": "2000-01-01T00:00:00Z",
            "updated_at": "2000-01-01T00:00:00Z", "unexpected": "ignored?",
        }
        for field, value in extra_fields.items():
            for case_name, changes in (
                ("extra_only", {}),
                ("extra_before_type", {"quantity": False}),
                ("extra_before_form", {"title": ""}),
            ):
                with self.subTest(extra_field=field, case=case_name):
                    payload = self.valid_payload(**changes)
                    payload[field] = value
                    self.assert_rejected_without_write(
                        self.client_with_csrf, body=self.encode_payload(payload), code="invalid_fields",
                    )

    def test_guest_valid_csrf_is_401(self):
        """J05: valid CSRF exposes auth guard, including invalid transport/body."""
        guest = self.csrf_client()
        for case_name, body, content_type in (
            ("valid_body", self.encode_payload(self.valid_payload()), "application/json"),
            ("malformed_body", b"{", "application/json"),
            ("unsupported_media_type", b"{", "text/plain"),
            ("spoofed_owner", self.encode_payload(self.valid_payload(owner=self.actor.pk)), "application/json"),
        ):
            with self.subTest(case=case_name):
                self.assert_rejected_without_write(
                    guest, body=body, content_type=content_type,
                    status=401, code="authentication_required",
                )

    def test_create_csrf_enforced(self):
        """J06: missing/mismatched tokens are 403; valid session/token creates."""
        for caller_name, actor in (("guest", None), ("authenticated", self.actor)):
            for token_case in ("no_cookie_or_header", "cookie_without_header", "mismatched_header"):
                with self.subTest(caller=caller_name, token=token_case):
                    client = self.csrf_client(actor)
                    if token_case == "no_cookie_or_header":
                        del client.cookies["csrftoken"]
                        del client.defaults["HTTP_X_CSRFTOKEN"]
                    elif token_case == "cookie_without_header":
                        del client.defaults["HTTP_X_CSRFTOKEN"]
                    else:
                        # Another real token is valid in format but does not match the cookie.
                        client.defaults["HTTP_X_CSRFTOKEN"] = get_token(RequestFactory().get(self.url))
                    before = self.database_snapshot()

                    response = client.generic("POST", self.url, b"{", content_type="application/json")

                    self.assertEqual(self.database_snapshot(), before)
                    self.assertEqual(response.status_code, 403)
                    self.assertNotIn("Location", response.headers)
        expected = self.valid_payload()
        self.assert_created_request(body=self.encode_payload(expected), expected=expected)

    def test_collection_method_allow_and_error_envelope(self):
        """J07/F05: exact Allow GET/POST, method before auth, GET ignores JSON body."""
        invalid_methods = ("PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
        for caller_name, actor in (("guest", None), ("authenticated", self.actor)):
            client = self.csrf_client(actor)
            for method in invalid_methods:
                with self.subTest(caller=caller_name, method=method):
                    before = self.database_snapshot()
                    response = client.generic(method, self.url, b"{", content_type="text/plain")
                    self.assertEqual(self.database_snapshot(), before)
                    self.assertEqual(response.status_code, 405)
                    allowed_methods = [value.strip() for value in response.headers["Allow"].split(",")]
                    self.assertCountEqual(allowed_methods, ["GET", "POST"])
                    if method == "HEAD":
                        # The HTTP client removes HEAD's body; JSON metadata remains testable.
                        self.assertEqual(response.content, b"")
                        self.assertEqual(response.headers["Content-Type"].split(";")[0], "application/json")
                    else:
                        self.assert_json_error(response, status=405, code="method_not_allowed")
            for body in (b"", b"{"):
                with self.subTest(caller=caller_name, get_body=body):
                    before = self.database_snapshot()
                    response = client.generic("GET", self.url, body, content_type="text/plain")
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(set(response.json()), {"data", "pagination"})
                    self.assertEqual(self.database_snapshot(), before)


class RequestOfferAdapterTests(TestCase):
    """O01–O06: real HTTP/DB offers; sequential SQLite does not prove races."""

    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.owner = users.objects.create_user(
            username="offer_owner", email="owner-secret@example.invalid",
            first_name="OwnerSecretFirst", last_name="OwnerSecretLast",
        )
        cls.donor = users.objects.create_user(
            username="secret_donor_one", email="donor-secret@example.invalid",
            first_name="DonorSecretFirst", last_name="DonorSecretLast",
        )
        cls.second_donor = users.objects.create_user(username="secret_donor_two")
        cls.unrelated_owner = users.objects.create_user(username="unrelated_owner")
        cls.food_request = Request.objects.create(
            owner=cls.owner, title="Butuh beras", food_item="Beras",
            quantity=Decimal("2.50"), unit="kg", description="", location="Depok",
        )
        cls.unrelated_request = Request.objects.create(
            owner=cls.unrelated_owner, title="Permintaan lain", food_item="Telur",
            quantity=Decimal("3.00"), unit="butir", status="offered",
        )
        RequestOffer.objects.create(request=cls.unrelated_request, offered_by=cls.donor)
        cls.fixed_time = datetime(2020, 1, 2, 12, tzinfo=timezone.utc)
        Request.objects.filter(pk=cls.food_request.pk).update(
            created_at=cls.fixed_time, updated_at=cls.fixed_time,
        )
        cls.food_request.refresh_from_db()

    def offer_url(self, request_id=None):
        target_id = self.food_request.pk if request_id is None else request_id
        try:
            url = reverse("request_board:api_request_offer", args=[target_id])
        except NoReverseMatch:
            self.fail("Route request_board:api_request_offer belum tersedia (Task 3B).")
        self.assertEqual(url, f"/requests/{target_id}/offers")
        return url

    def csrf_client(self, actor=None):
        client = Client(enforce_csrf_checks=True)
        if actor is not None:
            client.force_login(actor)
        token_request = RequestFactory().get("/requests")
        client.defaults["HTTP_X_CSRFTOKEN"] = get_token(token_request)
        client.cookies["csrftoken"] = token_request.META["CSRF_COOKIE"]
        return client

    def database_snapshot(self):
        return {
            model._meta.label: list(model.objects.order_by("pk").values())
            for model in (Request, RequestOffer, get_user_model())
        }

    def send_offer(self, client, *, body=b"{}", content_type="application/json",
                   method="POST", request_id=None):
        response = client.generic(
            method, self.offer_url(request_id), body,
            content_type=content_type, CONTENT_TYPE=content_type,
        )
        # Django otherwise omits Content-Type on empty data (the J02 fixture fix).
        self.assertEqual(response.wsgi_request.META.get("CONTENT_TYPE"), content_type)
        return response

    def assert_no_sql_writes(self, captured_queries):
        writes = [query["sql"] for query in captured_queries if re.match(
            r"\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP)\b",
            query["sql"], re.IGNORECASE,
        )]
        self.assertEqual(writes, [], "Rejected/duplicate offers must not issue SQL writes.")

    def assert_json_error(self, response, *, status, code):
        self.assertEqual(response.status_code, status)
        self.assertNotIn("Location", response.headers)
        self.assertEqual(response.headers["Content-Type"].split(";")[0], "application/json")
        payload = response.json()
        self.assertEqual(set(payload), {"error"})
        self.assertEqual(set(payload["error"]), {"code", "message", "fields"})
        self.assertEqual(payload["error"]["code"], code)
        self.assertEqual(payload["error"]["fields"], {})
        self.assertIsInstance(payload["error"]["message"], str)
        self.assertTrue(payload["error"]["message"].strip())

    def assert_rejected_offer(self, client, *, status, code=None, **request_options):
        before = self.database_snapshot()
        with CaptureQueriesContext(connection) as queries:
            response = self.send_offer(client, **request_options)
        self.assertEqual(self.database_snapshot(), before)
        self.assert_no_sql_writes(queries)
        self.assertEqual(response.status_code, status)
        self.assertNotIn("Location", response.headers)
        if code is not None:
            if request_options.get("method") == "HEAD":
                # The HTTP client strips HEAD bodies after the view returns.
                self.assertEqual(response.content, b"")
                self.assertEqual(response.headers["Content-Type"].split(";")[0], "application/json")
            else:
                self.assert_json_error(response, status=status, code=code)
        return response

    def assert_public_offer(self, response, *, created, offer_count):
        self.assertEqual(response.status_code, 201 if created else 200)
        self.assertNotIn("Location", response.headers)
        self.assertEqual(response.headers["Content-Type"].split(";")[0], "application/json")
        payload = response.json()
        self.assertEqual(set(payload), {"data"})
        self.assertEqual(set(payload["data"]), {"request", "created"})
        self.assertIs(payload["data"]["created"], created)
        item = payload["data"]["request"]
        self.assertEqual(set(item), {
            "id", "title", "food_item", "quantity", "unit", "description",
            "location", "owner", "status", "expires_at", "created_at", "updated_at", "offer_count",
        })
        self.food_request.refresh_from_db()
        for field in ("id", "title", "food_item", "unit", "description", "location", "status"):
            self.assertEqual(item[field], getattr(self.food_request, field), field)
        self.assertIs(type(item["id"]), int)
        self.assertEqual(item["quantity"], "2.50")
        self.assertEqual(item["owner"], {"display_name": self.owner.get_username()})
        self.assertIs(type(item["offer_count"]), int)
        self.assertEqual(item["offer_count"], offer_count)
        for field in ("created_at", "updated_at"):
            self.assertIsInstance(item[field], str)
            timestamp = parse_datetime(item[field])
            self.assertIsNotNone(timestamp, field)
            self.assertIsNotNone(timestamp.utcoffset(), field)
            self.assertEqual(timestamp, getattr(self.food_request, field), field)
        for private_value in (
            self.owner.email, self.owner.first_name, self.owner.last_name,
            self.donor.get_username(), self.donor.email,
            self.donor.first_name, self.donor.last_name, self.second_donor.get_username(),
        ):
            self.assertNotIn(private_value, response.content.decode())
        return item

    def assert_only_target_changed(self, before):
        after = self.database_snapshot()
        request_label = Request._meta.label
        offer_label = RequestOffer._meta.label
        target_before = next(row.copy() for row in before[request_label] if row["id"] == self.food_request.pk)
        target_after = next(row.copy() for row in after[request_label] if row["id"] == self.food_request.pk)
        for field in ("status", "updated_at"):
            target_before.pop(field)
            target_after.pop(field)
        self.assertEqual(target_after, target_before, "Offer may only change status/updated_at on its request.")

        previous_offers = [row for row in before[offer_label] if row["request_id"] == self.food_request.pk]
        current_offers = [row for row in after[offer_label] if row["request_id"] == self.food_request.pk]
        for row in previous_offers:
            self.assertIn(row, current_offers, "Existing offer rows and timestamps must stay intact.")
        self.assertEqual(len(current_offers), len(previous_offers) + 1)
        for snapshot in (before, after):
            snapshot[Request._meta.label] = [
                row for row in snapshot[Request._meta.label] if row["id"] != self.food_request.pk
            ]
            snapshot[RequestOffer._meta.label] = [
                row for row in snapshot[RequestOffer._meta.label]
                if row["request_id"] != self.food_request.pk
            ]
        self.assertEqual(after, before, "Unrelated requests/offers and all users must stay intact.")

    def test_new_and_second_offer(self):
        """O01: session donors each create one offer; only the first changes state."""
        self.offer_url()
        original_owner_id = self.food_request.owner_id
        original_created_at = self.food_request.created_at
        for donor, expected_count in ((self.donor, 1), (self.second_donor, 2)):
            with self.subTest(donor=donor.get_username()):
                client = self.csrf_client(donor)
                before = self.database_snapshot()
                self.food_request.refresh_from_db()
                previous_updated_at = self.food_request.updated_at

                response = self.send_offer(client)

                self.assert_public_offer(response, created=True, offer_count=expected_count)
                offers = RequestOffer.objects.filter(request=self.food_request)
                self.assertEqual(offers.count(), expected_count)
                self.assertEqual(offers.filter(offered_by=donor).count(), 1)
                self.assertEqual(self.food_request.status, "offered")
                self.assertEqual(self.food_request.owner_id, original_owner_id)
                self.assertEqual(self.food_request.created_at, original_created_at)
                if expected_count == 1:
                    self.assertGreater(self.food_request.updated_at, previous_updated_at)
                else:
                    self.assertEqual(self.food_request.updated_at, previous_updated_at)
                self.assert_only_target_changed(before)

    def test_duplicate_offer_preserves_timestamps(self):
        """O02: duplicate returns 200/false without writes or timestamp changes."""
        client = self.csrf_client(self.donor)
        first_response = self.send_offer(client)
        self.assert_public_offer(first_response, created=True, offer_count=1)
        # Distinct old timestamps expose an accidental auto_now save without sleeping.
        Request.objects.filter(pk=self.food_request.pk).update(updated_at=self.fixed_time)
        RequestOffer.objects.filter(request=self.food_request).update(created_at=self.fixed_time)
        before = self.database_snapshot()

        with CaptureQueriesContext(connection) as queries:
            response = self.send_offer(client)

        self.assert_public_offer(response, created=False, offer_count=1)
        self.assertEqual(self.database_snapshot(), before)
        self.assert_no_sql_writes(queries)
        self.assertEqual(self.food_request.updated_at, self.fixed_time)
        offer = RequestOffer.objects.get(request=self.food_request, offered_by=self.donor)
        self.assertEqual(offer.created_at, self.fixed_time)

    def test_owner_and_fulfilled_rejected(self):
        """O03: owner is forbidden; fulfilled rejects new and returning donors."""
        self.offer_url()
        owner_client = self.csrf_client(self.owner)
        for status in ("pending", "offered", "fulfilled"):
            with self.subTest(owner_state=status):
                Request.objects.filter(pk=self.food_request.pk).update(status=status)
                self.assert_rejected_offer(owner_client, status=403, code="permission_denied")
        RequestOffer.objects.create(request=self.food_request, offered_by=self.donor)
        for donor in (self.donor, self.second_donor):
            with self.subTest(fulfilled_donor=donor.get_username()):
                self.assert_rejected_offer(
                    self.csrf_client(donor), status=409, code="request_fulfilled",
                )

    def test_offer_rejects_identity_payload(self):
        """O04/F01: nonempty body follows media → decode → object → extra keys."""
        import json

        self.offer_url()
        client = self.csrf_client(self.donor)
        invalid_bodies = [
            ("media_before_decode", b"{", "text/plain", "invalid_content_type"),
            ("missing_media", b"{}", "", "invalid_content_type"),
            ("vendor_json", b"{}", "application/problem+json", "invalid_content_type"),
            ("form_media", b"{}", "application/x-www-form-urlencoded", "invalid_content_type"),
            ("charset_before_decode", b"\xff", "application/json; charset=latin-1", "invalid_content_type"),
            ("utf16_charset", b"{}", "application/json; charset=utf-16", "invalid_content_type"),
            ("malformed", b"{", "application/json", "invalid_json"),
            ("whitespace_is_not_empty", b" \n", "application/json", "invalid_json"),
            ("invalid_utf8", b'{"owner":"\xff"}', "application/json", "invalid_json"),
            ("trailing_document", b"{} {}", "application/json", "invalid_json"),
            ("duplicate_before_extra", b'{"owner":1,"owner":2}', "application/json", "invalid_json"),
            ("nested_duplicate_before_shape", b'[{"x":1,"x":2}]', "application/json", "invalid_json"),
        ]
        for literal in (b"NaN", b"Infinity", b"-Infinity"):
            invalid_bodies.append((
                "nonfinite_before_extra_" + literal.decode(), b'{"owner":' + literal + b"}",
                "application/json", "invalid_json",
            ))
        for name, value in (
            ("array", []), ("nested_object_array", [{"owner": 1}]),
            ("string", "text"), ("number", 2), ("null", None), ("boolean", True),
        ):
            invalid_bodies.append((name, json.dumps(value).encode(), "application/json", "invalid_payload"))
        # All keys are extra here: even null/boolean/nested values must yield invalid_fields.
        for field, value in (
            ("offered_by", self.second_donor.pk), ("offered_by_id", self.second_donor.pk),
            ("owner", self.unrelated_owner.pk), ("owner_id", self.unrelated_owner.pk),
            ("status", "fulfilled"), ("id", self.unrelated_request.pk),
            ("created_at", "2000-01-01T00:00:00Z"), ("updated_at", None),
            ("unexpected", {"nested": True}), ("status", False),
        ):
            invalid_bodies.append((
                "extra_" + field + "_" + type(value).__name__,
                json.dumps({field: value}).encode(), "application/json", "invalid_fields",
            ))
        for case_name, body, content_type, code in invalid_bodies:
            with self.subTest(case=case_name, code=code):
                self.assert_rejected_offer(
                    client, body=body, content_type=content_type, status=400, code=code,
                )

        for case_name, body, content_type in (
            ("empty_without_media", b"", ""),
            ("empty_unsupported_media_bypasses_parser", b"", "text/plain"),
            ("empty_json", b"", "application/json"),
            ("empty_object", b"{}", "application/json"),
            ("utf8_object", b" {} \n", "application/json; charset=UTF-8"),
        ):
            with self.subTest(accepted=case_name):
                # Each positive fixture proves creation, rather than reusing a duplicate.
                RequestOffer.objects.filter(request=self.food_request).delete()
                Request.objects.filter(pk=self.food_request.pk).update(status="pending")
                before = self.database_snapshot()
                response = self.send_offer(client, body=body, content_type=content_type)
                self.assert_public_offer(response, created=True, offer_count=1)
                offer = RequestOffer.objects.get(request=self.food_request)
                self.assertEqual(offer.offered_by_id, self.donor.pk)
                self.assertEqual(self.food_request.status, "offered")
                self.assert_only_target_changed(before)

    def test_offer_guest_csrf_missing_and_method(self):
        """O05: real CSRF precedes method/auth; then payload precedes missing ID."""
        self.offer_url()  # Report the known missing route as one intentional RED failure.
        missing_id = max(self.food_request.pk, self.unrelated_request.pk) + 1000
        guest = self.csrf_client()
        for case_name, body, content_type in (
            ("valid", b"{}", "application/json"),
            ("malformed", b"{", "application/json"),
            ("wrong_media", b"{", "text/plain"),
        ):
            for target_id in (self.food_request.pk, missing_id):
                with self.subTest(guest=case_name, target=target_id):
                    self.assert_rejected_offer(
                        guest, body=body, content_type=content_type, request_id=target_id,
                        status=401, code="authentication_required",
                    )
        authenticated = self.csrf_client(self.donor)
        self.assert_rejected_offer(
            authenticated, request_id=missing_id, status=404, code="request_not_found",
        )
        self.assert_rejected_offer(
            authenticated, request_id=missing_id, body=b"{", status=400, code="invalid_json",
        )
        for actor in (None, self.donor):
            for method in ("GET", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
                with self.subTest(caller="guest" if actor is None else "donor", method=method):
                    response = self.assert_rejected_offer(
                        self.csrf_client(actor), method=method, body=b"{", content_type="text/plain",
                        status=405, code="method_not_allowed",
                    )
                    self.assertEqual(response.headers["Allow"], "POST")
            for token_case in ("no_cookie_or_header", "cookie_without_header", "mismatched_header"):
                for method in ("POST", "PUT"):
                    with self.subTest(actor=actor, token=token_case, method=method):
                        client = self.csrf_client(actor)
                        if token_case == "no_cookie_or_header":
                            del client.cookies["csrftoken"]
                            del client.defaults["HTTP_X_CSRFTOKEN"]
                        elif token_case == "cookie_without_header":
                            del client.defaults["HTTP_X_CSRFTOKEN"]
                        else:
                            client.defaults["HTTP_X_CSRFTOKEN"] = get_token(RequestFactory().get("/requests"))
                        self.assert_rejected_offer(
                            client, method=method, body=b"{", content_type="text/plain", status=403,
                        )
        # One client/token transitions from guest to session actor to a bad token.
        guest.force_login(self.donor)
        response = self.send_offer(guest)
        self.assert_public_offer(response, created=True, offer_count=1)
        guest.defaults["HTTP_X_CSRFTOKEN"] = get_token(RequestFactory().get("/requests"))
        self.assert_rejected_offer(guest, status=403)

    def test_offer_uses_existing_service_and_public_output(self):
        """O06: spy wraps the real service; unexpected errors remain visible failures."""
        from unittest.mock import Mock, patch
        from request_board import services, views

        self.offer_url()
        client = self.csrf_client(self.donor)
        before = self.database_snapshot()
        offer_service = Mock(wraps=services.offer_request)
        # Cover module imports and direct imports with the same real-service spy.
        with patch.object(services, "offer_request", offer_service), patch.object(
            views, "offer_request", offer_service, create=True,
        ):
            response = self.send_offer(client)
            self.assert_public_offer(response, created=True, offer_count=1)
            offer_service.assert_called_once_with(actor=self.donor, request_id=self.food_request.pk)
            self.assertEqual(RequestOffer.objects.get(request=self.food_request).offered_by_id, self.donor.pk)
            self.assertEqual(self.food_request.status, "offered")
            self.assert_only_target_changed(before)

            offer_service.reset_mock()
            before_duplicate = self.database_snapshot()
            with CaptureQueriesContext(connection) as queries:
                duplicate = self.send_offer(client)
            self.assert_public_offer(duplicate, created=False, offer_count=1)
            offer_service.assert_called_once_with(actor=self.donor, request_id=self.food_request.pk)
            self.assertEqual(self.database_snapshot(), before_duplicate)
            self.assert_no_sql_writes(queries)

            offer_service.reset_mock()
            offer_service.side_effect = RuntimeError("Unexpected offer service failure")
            before_failure = self.database_snapshot()
            with self.assertRaisesMessage(RuntimeError, "Unexpected offer service failure"):
                self.send_offer(client)
            offer_service.assert_called_once_with(actor=self.donor, request_id=self.food_request.pk)
            self.assertEqual(self.database_snapshot(), before_failure)

        expected_item = duplicate.json()["data"]["request"]
        for actor in (None, self.owner, self.second_donor):
            with self.subTest(public_caller=actor):
                public_client = self.csrf_client(actor)
                before_read = self.database_snapshot()
                response = public_client.get(reverse("request_board:api_request_collection"))
                self.assertEqual(response.status_code, 200)
                listed = {item["id"]: item for item in response.json()["data"]}
                self.assertEqual(listed[self.food_request.pk], expected_item)
                self.assertEqual(self.database_snapshot(), before_read)


class RequestOwnerActionAdapterTests(TestCase):
    """D01-D07: owner HTTP actions; SQLite proves sequential adapter behavior."""

    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.owner = user_model.objects.create_user(
            username="action_owner", email="owner-private@example.invalid",
            first_name="PrivateOwnerFirst", last_name="PrivateOwnerLast",
        )
        cls.donor = user_model.objects.create_user(
            username="action_donor", email="donor-private@example.invalid",
        )
        cls.second_donor = user_model.objects.create_user(username="action_second_donor")
        cls.unrelated_owner = user_model.objects.create_user(username="unrelated_owner")
        cls.fixed_time = datetime(2020, 1, 2, 12, tzinfo=timezone.utc)
        cls.unrelated_request = Request.objects.create(
            owner=cls.unrelated_owner, title="Permintaan lain", food_item="Telur",
            quantity=Decimal("3.00"), unit="butir", status="offered",
        )
        RequestOffer.objects.create(request=cls.unrelated_request, offered_by=cls.donor)

    def make_target(self, *, status="offered"):
        food_request = Request.objects.create(
            owner=self.owner, title="Butuh beras", food_item="Beras",
            quantity=Decimal("2.50"), unit="kg", status=status,
            description="", location="Depok",
        )
        # Pending has no offers; later states retain two real donor offers.
        if status != "pending":
            for donor in (self.donor, self.second_donor):
                RequestOffer.objects.create(request=food_request, offered_by=donor)
        if status == "offered":
            Request.objects.filter(pk=food_request.pk).update(selected_offer=food_request.offers.get(offered_by=self.donor))
        Request.objects.filter(pk=food_request.pk).update(
            created_at=self.fixed_time, updated_at=self.fixed_time,
        )
        RequestOffer.objects.filter(request=food_request).update(created_at=self.fixed_time)
        food_request.refresh_from_db()
        return food_request

    def action_url(self, action, request_id):
        route_name = "api_request_status" if action == "fulfill" else "api_request_delete"
        expected_url = f"/requests/{request_id}" + ("/status" if action == "fulfill" else "")
        try:
            url = reverse(f"request_board:{route_name}", args=[request_id])
        except NoReverseMatch:
            self.fail(f"Route request_board:{route_name} belum tersedia; Task 4B harus memasangnya.")
        self.assertEqual(url, expected_url)
        self.assertEqual(resolve(url).view_name, f"request_board:{route_name}")
        return url

    def require_owner_routes(self):
        # Fail once per method on the known absent route, before testing its behavior.
        for action in ("fulfill", "delete"):
            self.action_url(action, self.unrelated_request.pk)

    def csrf_client(self, actor=None):
        client = Client(enforce_csrf_checks=True)
        if actor is not None:
            client.force_login(actor)
        token_request = RequestFactory().get("/requests")
        client.defaults["HTTP_X_CSRFTOKEN"] = get_token(token_request)
        client.cookies["csrftoken"] = token_request.META["CSRF_COOKIE"]
        return client

    def send_action(self, client, action, request_id, *, body=None,
                    content_type="application/json", method=None, query=""):
        if body is None:
            body = b'{"status":"fulfilled"}' if action == "fulfill" else b""
        if method is None:
            method = "PUT" if action == "fulfill" else "DELETE"
        response = client.generic(
            method, self.action_url(action, request_id) + query, body,
            content_type=content_type, CONTENT_TYPE=content_type,
        )
        # Explicit WSGI override preserves even an absent/empty media header on empty data.
        self.assertEqual(response.wsgi_request.META.get("CONTENT_TYPE"), content_type)
        return response

    def database_snapshot(self):
        return {
            model._meta.label: list(model.objects.order_by("pk").values())
            for model in (Request, RequestOffer, get_user_model())
        }

    def assert_no_sql_writes(self, captured_queries):
        writes = [query["sql"] for query in captured_queries if re.match(
            r"\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP)\b",
            query["sql"], re.IGNORECASE,
        )]
        self.assertEqual(writes, [], "Rejected/repeated owner actions must not issue writes.")

    def assert_json_error(self, response, *, status, code):
        self.assertEqual(response.status_code, status)
        self.assertNotIn("Location", response.headers)
        self.assertEqual(response.headers["Content-Type"].split(";")[0], "application/json")
        payload = response.json()
        self.assertEqual(set(payload), {"error"})
        self.assertEqual(set(payload["error"]), {"code", "message", "fields"})
        self.assertEqual(payload["error"]["code"], code)
        self.assertEqual(payload["error"]["fields"], {})
        self.assertIsInstance(payload["error"]["message"], str)
        self.assertTrue(payload["error"]["message"].strip())
        for private_value in (self.owner.email, self.owner.first_name,
                              self.owner.last_name, self.donor.email):
            self.assertNotIn(private_value, response.content.decode())

    def assert_rejected_action(self, client, action, request_id, *, status, code=None, **options):
        before = self.database_snapshot()
        with CaptureQueriesContext(connection) as queries:
            response = self.send_action(client, action, request_id, **options)
        self.assertEqual(self.database_snapshot(), before)
        self.assert_no_sql_writes(queries)
        self.assertEqual(response.status_code, status)
        self.assertNotIn("Location", response.headers)
        if code is not None:
            if options.get("method") == "HEAD":
                # Django's client strips HEAD bodies after the view returns.
                self.assertEqual(response.content, b"")
                self.assertEqual(response.headers["Content-Type"].split(";")[0], "application/json")
            else:
                self.assert_json_error(response, status=status, code=code)
        return response

    def assert_public_fulfillment(self, response, food_request):
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Location", response.headers)
        self.assertEqual(response.headers["Content-Type"].split(";")[0], "application/json")
        payload = response.json()
        self.assertEqual(set(payload), {"data"})
        item = payload["data"]
        self.assertEqual(set(item), {
            "id", "title", "food_item", "quantity", "unit", "description",
            "location", "owner", "status", "expires_at", "created_at", "updated_at", "offer_count",
        })
        food_request.refresh_from_db()
        self.assertEqual(food_request.status, "fulfilled")
        for field in ("id", "title", "food_item", "unit", "description", "location", "status"):
            self.assertEqual(item[field], getattr(food_request, field), field)
        self.assertIs(type(item["id"]), int)
        self.assertEqual(item["quantity"], "2.50")
        self.assertEqual(item["owner"], {"display_name": self.owner.get_username()})
        self.assertIs(type(item["offer_count"]), int)
        self.assertEqual(item["offer_count"], 2)
        for field in ("created_at", "updated_at"):
            self.assertIsInstance(item[field], str)
            timestamp = parse_datetime(item[field])
            self.assertIsNotNone(timestamp, field)
            self.assertIsNotNone(timestamp.utcoffset(), field)
            self.assertEqual(timestamp, getattr(food_request, field), field)
        for private_value in (
            self.owner.email, self.owner.first_name, self.owner.last_name,
            self.donor.get_username(), self.donor.email, self.second_donor.get_username(),
        ):
            self.assertNotIn(private_value, response.content.decode())
        return item

    def assert_only_fulfillment_changed(self, before, food_request):
        expected = before
        after = self.database_snapshot()
        target_before = next(row for row in expected[Request._meta.label] if row["id"] == food_request.pk)
        target_after = next(row for row in after[Request._meta.label] if row["id"] == food_request.pk)
        self.assertEqual(target_after["status"], "fulfilled")
        self.assertGreater(target_after["updated_at"], target_before["updated_at"])
        target_before["status"] = target_after["status"]
        target_before["updated_at"] = target_after["updated_at"]
        self.assertEqual(after, expected, "Only target status/updated_at may change; offers/users stay intact.")

    def assert_only_target_deleted(self, before, request_id):
        expected = {
            label: [row for row in rows if not (
                (label == Request._meta.label and row["id"] == request_id)
                or (label == RequestOffer._meta.label and row["request_id"] == request_id)
            )]
            for label, rows in before.items()
        }
        self.assertEqual(self.database_snapshot(), expected, "Deletion must preserve all unrelated rows/users.")
        self.assertFalse(Request.objects.filter(pk=request_id).exists())
        self.assertFalse(RequestOffer.objects.filter(request_id=request_id).exists())

    def test_owner_fulfills_only_offered(self):
        """D01: owner gets 200/public data for offered; pending gets 409 without writes."""
        self.require_owner_routes()
        client = self.csrf_client(self.owner)
        pending = self.make_target(status="pending")
        self.assert_rejected_action(
            client, "fulfill", pending.pk, status=409, code="offer_selection_required",
        )
        offered = self.make_target()
        before = self.database_snapshot()

        response = self.send_action(client, "fulfill", offered.pk)

        item = self.assert_public_fulfillment(response, offered)
        self.assert_only_fulfillment_changed(before, offered)
        listed = client.get(reverse("request_board:api_request_collection")).json()["data"]
        self.assertEqual(next(row for row in listed if row["id"] == offered.pk), item)

    def test_repeat_fulfill_preserves_updated_at(self):
        """D02: a real repeat and a preexisting fulfilled target produce no SQL writes."""
        self.require_owner_routes()
        client = self.csrf_client(self.owner)
        offered = self.make_target()
        self.assert_public_fulfillment(self.send_action(client, "fulfill", offered.pk), offered)
        # Old timestamps expose an accidental auto_now save without timing/sleep assumptions.
        for food_request in (offered, self.make_target(status="fulfilled")):
            with self.subTest(target=food_request.pk):
                Request.objects.filter(pk=food_request.pk).update(updated_at=self.fixed_time)
                before = self.database_snapshot()

                with CaptureQueriesContext(connection) as queries:
                    response = self.send_action(client, "fulfill", food_request.pk)

                self.assert_public_fulfillment(response, food_request)
                self.assertEqual(food_request.updated_at, self.fixed_time)
                self.assertEqual(self.database_snapshot(), before)
                self.assert_no_sql_writes(queries)

    def test_status_rejects_arbitrary_transition(self):
        """D03/F01: media -> decode -> object -> extra keys -> exact status target."""
        import json

        self.require_owner_routes()
        food_request = self.make_target()
        client = self.csrf_client(self.owner)
        cases = [
            ("media_before_decode", b"{", "text/plain", "invalid_content_type"),
            ("missing_media", b'{"status":"fulfilled"}', "", "invalid_content_type"),
            ("vendor_media", b"{}", "application/problem+json", "invalid_content_type"),
            ("form_media", b"status=fulfilled", "application/x-www-form-urlencoded", "invalid_content_type"),
            ("charset_before_decode", b"\xff", "application/json; charset=latin-1", "invalid_content_type"),
            ("utf16_charset", b"{}", "application/json; charset=utf-16", "invalid_content_type"),
            ("empty_body", b"", "application/json", "invalid_json"),
            ("empty_wrong_media", b"", "", "invalid_content_type"),
            ("whitespace", b" \n", "application/json", "invalid_json"),
            ("malformed", b"{", "application/json", "invalid_json"),
            ("invalid_utf8", b'{"status":"\xff"}', "application/json", "invalid_json"),
            ("trailing_document", b"{} {}", "application/json", "invalid_json"),
            ("duplicate_status", b'{"status":"pending","status":"fulfilled"}', "application/json", "invalid_json"),
            ("nested_duplicate_before_shape", b'[{"x":1,"x":2}]', "application/json", "invalid_json"),
            ("duplicate_before_extra", b'{"owner":1,"owner":2}', "application/json", "invalid_json"),
            ("missing_status", b"{}", "application/json", "invalid_status"),
        ]
        for literal in (b"NaN", b"Infinity", b"-Infinity"):
            cases.append(("nonfinite_" + literal.decode(), b'{"unknown":' + literal + b"}",
                          "application/json", "invalid_json"))
        for name, value in (("array", []), ("string", "fulfilled"), ("number", 1),
                            ("null", None), ("boolean", True)):
            cases.append((name, json.dumps(value).encode(), "application/json", "invalid_payload"))
        for value in ("pending", "offered", "", "FULFILLED", None, True, 1, [], {}):
            cases.append(("invalid_status_" + repr(value), json.dumps({"status": value}).encode(),
                          "application/json", "invalid_status"))
        for field, value in (
            ("owner", self.unrelated_owner.pk), ("owner_id", self.unrelated_owner.pk),
            ("offered_by", self.donor.pk), ("offered_by_id", self.donor.pk),
            ("id", self.unrelated_request.pk), ("request_id", self.unrelated_request.pk),
            ("created_at", "2000-01-01T00:00:00Z"), ("updated_at", None),
            ("unexpected", {"nested": True}),
        ):
            for status_payload in ({}, {"status": None}, {"status": "fulfilled"}):
                cases.append(("extra_before_status_" + field + "_" + repr(status_payload),
                              json.dumps({**status_payload, field: value}).encode(),
                              "application/json", "invalid_fields"))
        for case_name, body, content_type, code in cases:
            with self.subTest(case=case_name, code=code):
                self.assert_rejected_action(
                    client, "fulfill", food_request.pk, body=body,
                    content_type=content_type, status=400, code=code,
                )
        # Payload validation precedes service ownership and missing-target checks.
        for target_id in (food_request.pk, food_request.pk + 1000):
            self.assert_rejected_action(
                self.csrf_client(self.donor), "fulfill", target_id,
                body=b'{"status":null,"owner":1}', status=400, code="invalid_fields",
            )
        self.assert_rejected_action(
            self.csrf_client(self.donor), "fulfill", food_request.pk,
            status=403, code="permission_denied",
        )
        for content_type in ("application/json", "application/json; charset=UTF-8"):
            with self.subTest(valid_media=content_type):
                target = self.make_target()
                before = self.database_snapshot()
                response = self.send_action(client, "fulfill", target.pk, content_type=content_type)
                self.assert_public_fulfillment(response, target)
                self.assert_only_fulfillment_changed(before, target)

    def test_owner_deletes_all_statuses_and_cascades(self):
        """D04: 204 has empty bytes; cascade removes only target and its offers."""
        self.require_owner_routes()
        client = self.csrf_client(self.owner)
        for status in ("pending", "offered", "fulfilled"):
            for content_type in ("", "application/json", "text/plain"):
                with self.subTest(state=status, empty_body_media=content_type):
                    food_request = self.make_target(status=status)
                    before = self.database_snapshot()

                    response = self.send_action(client, "delete", food_request.pk, content_type=content_type)

                    self.assertEqual(response.status_code, 204)
                    self.assertEqual(response.content, b"")
                    self.assertNotIn("Location", response.headers)
                    self.assert_only_target_deleted(before, food_request.pk)

    def test_repeat_delete_is_404_without_write(self):
        """D05: repeated DELETE and missing PUT/DELETE return 404 with no writes."""
        self.require_owner_routes()
        client = self.csrf_client(self.owner)
        food_request = self.make_target()
        before = self.database_snapshot()
        response = self.send_action(client, "delete", food_request.pk)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")
        self.assert_only_target_deleted(before, food_request.pk)
        for action in ("fulfill", "delete"):
            for target_id in (food_request.pk, food_request.pk + 1000):
                with self.subTest(action=action, missing_target=target_id):
                    self.assert_rejected_action(
                        client, action, target_id, status=404, code="request_not_found",
                    )

    def test_owner_actions_auth_csrf_and_methods(self):
        """D06: real CSRF precedes method/auth; payload precedes owner/missing service checks."""
        self.require_owner_routes()
        for action, allowed_method in (("fulfill", "PUT"), ("delete", "DELETE")):
            for status in ("pending", "offered", "fulfilled"):
                target = self.make_target(status=status)
                for actor in (self.donor, self.second_donor, self.unrelated_owner):
                    with self.subTest(action=action, state=status, nonowner=actor.get_username()):
                        self.assert_rejected_action(
                            self.csrf_client(actor), action, target.pk, status=403, code="permission_denied",
                        )
            missing_id = target.pk + 1000
            for target_id in (target.pk, missing_id):
                for body, content_type in ((None, "application/json"), (b"{", "text/plain")):
                    with self.subTest(action=action, guest_target=target_id, body=body):
                        self.assert_rejected_action(
                            self.csrf_client(), action, target_id, body=body, content_type=content_type,
                            status=401, code="authentication_required",
                        )
            for actor in (None, self.owner):
                for method in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
                    if method == allowed_method:
                        continue
                    with self.subTest(action=action, actor=actor, wrong_method=method):
                        response = self.assert_rejected_action(
                            self.csrf_client(actor), action, target.pk, method=method,
                            body=b"{", content_type="text/plain", status=405, code="method_not_allowed",
                        )
                        self.assertEqual(response.headers["Allow"], allowed_method)
                for token_case in ("no_cookie_or_header", "cookie_without_header", "mismatched_header"):
                    for method in ("POST", "PUT", "PATCH", "DELETE"):
                        with self.subTest(action=action, actor=actor, csrf=token_case, method=method):
                            client = self.csrf_client(actor)
                            if token_case == "no_cookie_or_header":
                                del client.cookies["csrftoken"]
                                del client.defaults["HTTP_X_CSRFTOKEN"]
                            elif token_case == "cookie_without_header":
                                del client.defaults["HTTP_X_CSRFTOKEN"]
                            else:
                                client.defaults["HTTP_X_CSRFTOKEN"] = get_token(RequestFactory().get("/requests"))
                            self.assert_rejected_action(
                                client, action, target.pk, method=method,
                                body=b"{", content_type="text/plain", status=403,
                            )
        for actor in (self.owner, self.donor):
            for target_id in (target.pk, missing_id):
                for body in (b"{}", b"null", b"{", b" \n", b"\xff"):
                    with self.subTest(delete_body=body, actor=actor, target=target_id):
                        self.assert_rejected_action(
                            self.csrf_client(actor), "delete", target_id,
                            body=body, content_type="text/plain", status=400, code="invalid_payload",
                        )

    def test_owner_actions_do_not_swallow_service_failure(self):
        """D07: real-service spies prove one session/URL call; unexpected errors propagate."""
        from unittest.mock import Mock, patch
        from django.core.exceptions import ValidationError
        from django.db import IntegrityError
        from request_board import services, views

        self.require_owner_routes()
        client = self.csrf_client(self.owner)
        for action, service_name in (("fulfill", "fulfill_request"), ("delete", "delete_request")):
            with self.subTest(action=action):
                target = self.make_target()
                service_spy = Mock(wraps=getattr(services, service_name))
                # Both import styles reach the same wrapped service; HTTP and DB remain real.
                with patch.object(services, service_name, service_spy), patch.object(
                    views, service_name, service_spy, create=True,
                ):
                    before = self.database_snapshot()
                    response = self.send_action(
                        client, action, target.pk,
                        query=f"?owner={self.unrelated_owner.pk}&request_id={self.unrelated_request.pk}&status=pending",
                    )
                    service_spy.assert_called_once_with(actor=self.owner, request_id=target.pk)
                    if action == "fulfill":
                        self.assert_public_fulfillment(response, target)
                        self.assert_only_fulfillment_changed(before, target)
                    else:
                        self.assertEqual(response.status_code, 204)
                        self.assertEqual(response.content, b"")
                        self.assert_only_target_deleted(before, target.pk)

                    for failure in (
                        RuntimeError("Unexpected owner action failure"),
                        IntegrityError("Unexpected integrity failure"),
                        ValidationError("Unknown domain error", code="unexpected_action_failure"),
                        ValidationError({"status": ["Unknown field error"]}),
                    ):
                        service_spy.reset_mock()
                        service_spy.side_effect = failure
                        failure_target = self.make_target()
                        before_failure = self.database_snapshot()

                        with CaptureQueriesContext(connection) as queries:
                            with self.assertRaises(type(failure)) as caught:
                                self.send_action(client, action, failure_target.pk)

                        self.assertIs(caught.exception, failure)
                        service_spy.assert_called_once_with(actor=self.owner, request_id=failure_target.pk)
                        self.assertEqual(self.database_snapshot(), before_failure)
                        self.assert_no_sql_writes(queries)


class HtmlAdapterFixtures:
    """Shared fixtures only; no inherited acceptance methods or render stubs."""

    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.owner = users.objects.create_user(username="html_owner")
        cls.donor = users.objects.create_user(
            username="html_donor", email="private-html@example.invalid",
            first_name="PrivateHtmlDonor",
        )
        cls.outsider = users.objects.create_user(username="html_outsider")
        cls.fixed_time = datetime(2020, 1, 2, 12, tzinfo=timezone.utc)

    def html_url(self, name, request_id=None):
        if name == "login":
            return reverse("login")
        kwargs = {} if request_id is None else {"request_id": request_id}
        try:
            return reverse("request_board:" + name, kwargs=kwargs)
        except NoReverseMatch:
            self.fail(f"Task 5B belum menyediakan route request_board:{name}.")

    def make_request(self, *, owner=None, status="pending"):
        food_request = Request.objects.create(
            owner=owner or self.owner, status=status, title="Butuh beras HTML",
            food_item="Beras", quantity=Decimal("2.50"), unit="kg",
        )
        Request.objects.filter(pk=food_request.pk).update(
            created_at=self.fixed_time, updated_at=self.fixed_time,
        )
        food_request.refresh_from_db()
        return food_request

    def csrf_client(self, actor=None):
        client = Client(enforce_csrf_checks=True)
        if actor is not None:
            client.force_login(actor)
        token_request = RequestFactory().get("/board/")
        token = get_token(token_request)
        client.cookies["csrftoken"] = token_request.META["CSRF_COOKIE"]
        client.defaults["HTTP_X_CSRFTOKEN"] = token
        return client

    def database_snapshot(self):
        return {
            model._meta.label: list(model.objects.order_by("pk").values())
            for model in (Request, RequestOffer, get_user_model())
        }

    def assert_no_domain_writes(self, captured):
        writes = [query["sql"] for query in captured if re.search(
            r'\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+["`\[]?(?:requests|request_offers)\b',
            query["sql"], re.IGNORECASE,
        )]
        self.assertEqual(writes, [])

    def assert_board_redirect(self, response, *, error=False):
        from django.contrib.messages import ERROR, get_messages

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], self.html_url("request_board"))
        feedback = list(get_messages(response.wsgi_request))
        self.assertTrue(feedback, "Action must explain its outcome through messages")
        self.assertTrue(all(str(message).strip() for message in feedback))
        if error:
            self.assertTrue(any(message.level == ERROR for message in feedback))
        else:
            self.assertTrue(all(message.level < ERROR for message in feedback))
        for message in feedback:
            self.assertNotIn(self.donor.email, str(message))
            self.assertNotIn(self.donor.first_name, str(message))
        return str(feedback[-1])

    def assert_login_redirect(self, response, requested_url):
        from urllib.parse import parse_qs, urlsplit

        self.assertEqual(response.status_code, 302)
        destination = urlsplit(response.headers["Location"])
        self.assertEqual(destination.path, self.html_url("login"))
        self.assertEqual(destination.netloc, "")
        self.assertEqual(parse_qs(destination.query), {"next": [requested_url]})


class RequestHtmlAdapterTests(HtmlAdapterFixtures, TestCase):
    """H01-H08: real HTTP/session/services; only final rendering is isolated."""

    def setUp(self):
        from unittest.mock import patch

        # Client normally renders TemplateResponse automatically. Keep its context
        # intact at this boundary; no production or dummy template proves T3 here.
        def retain_context_before_render(response):
            # CommonMiddleware otherwise reads response.content for its length.
            # This header belongs only to the isolated test boundary.
            response.headers["Content-Length"] = "0"
            return response

        render_patch = patch(
            "django.template.response.TemplateResponse.render", autospec=True,
            side_effect=retain_context_before_render,
        )
        self.render_boundary = render_patch.start()
        self.addCleanup(render_patch.stop)

    def board_context(self, client, query=None):
        from django.template.response import TemplateResponse

        response = client.get(self.html_url("request_board"), query or {})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response, TemplateResponse)
        self.assertFalse(response.is_rendered)
        self.assertIn("page_obj", response.context_data)
        self.assertIn("owner_offer_previews", response.context_data)
        return response.context_data

    def test_board_context_and_access_without_render_claim(self):
        """H01: public states, stable pages, annotations and invalid page guards."""
        board_url = self.html_url("request_board")
        self.assertEqual(board_url, "/board/")
        guest = self.csrf_client()
        before = self.database_snapshot()
        empty = self.board_context(guest)
        self.assertEqual(list(empty["page_obj"]), [])
        self.assertEqual(empty["page_obj"].number, 1)
        self.assertEqual(empty["owner_offer_previews"], {})
        self.assertEqual(self.database_snapshot(), before)

        rows = [self.make_request(status=status) for status in ("pending", "offered", "fulfilled")]
        RequestOffer.objects.create(request=rows[1], offered_by=self.donor)
        rows.extend(self.make_request() for _ in range(18))
        expected_ids = [row.pk for row in reversed(rows)]
        for actor_name, actor in (("guest", None), ("owner", self.owner), ("donor", self.donor)):
            with self.subTest(actor=actor_name):
                client = self.csrf_client(actor)
                before = self.database_snapshot()
                with CaptureQueriesContext(connection) as captured:
                    context = self.board_context(client)
                    first_page = list(context["page_obj"])
                    second = list(self.board_context(client, {"page": "2"})["page_obj"])
                    for row in first_page:
                        self.assertEqual(row.offer_count, int(row.pk == rows[1].pk))
                        self.assertEqual(row.has_offered, actor == self.donor and row.pk == rows[1].pk)
                        self.assertEqual(row.owner.get_username(), self.owner.get_username())
                self.assertEqual([row.pk for row in first_page + second], expected_ids)
                self.assertEqual(context["page_obj"].paginator.per_page, 20)
                self.assertEqual(context["page_obj"].paginator.count, 21)
                if actor != self.owner:
                    self.assertEqual(context["owner_offer_previews"], {})
                else:
                    previews = context["owner_offer_previews"]
                    self.assertEqual(set(previews), {row.pk for row in first_page})
                    for row in first_page:
                        self.assertEqual(previews[row.pk]["total"], row.offer_count)
                        self.assertEqual(len(previews[row.pk]["items"]), row.offer_count)
                        self.assertFalse(previews[row.pk]["has_more"])
                self.assertEqual(self.database_snapshot(), before)
                self.assert_no_domain_writes(captured)

        for page in ("001", "0" * 5000 + "1"):
            self.assertEqual(self.board_context(guest, {"page": page})["page_obj"].number, 1)
        invalid_queries = [
            "page=", "page=0", "page=-1", "page=%2B1", "page=1.0",
            "page=abc", "page=١", "page=1&page=1", "page=1&page=2",
        ]
        for query in invalid_queries:
            with self.subTest(invalid_query=query):
                self.assertEqual(guest.get(board_url + "?" + query).status_code, 400)
        for page in ("3", "9" * 5000):
            with self.subTest(out_of_range=page[:20]):
                self.assertEqual(guest.get(board_url, {"page": page}).status_code, 404)

    def test_create_html_redirect_and_invalid_bound_form(self):
        """H02/F02: guest first, unbound GET, bound invalid, session owner/PRG."""
        from unittest.mock import patch
        from django.template.response import TemplateResponse
        from request_board.forms import RequestForm
        from request_board.services import create_request

        url = self.html_url("request_create")
        self.assertEqual(url, "/board/new/")
        guest = self.csrf_client()
        for method in ("get", "post", "put", "head", "options"):
            with self.subTest(guest_method=method):
                before = self.database_snapshot()
                self.assert_login_redirect(getattr(guest, method)(url), url)
                self.assertEqual(self.database_snapshot(), before)
        client = self.csrf_client(self.owner)
        response = client.get(url)
        self.assertIsInstance(response, TemplateResponse)
        self.assertFalse(response.is_rendered)
        self.assertIsInstance(response.context_data["form"], RequestForm)
        self.assertFalse(response.context_data["form"].is_bound)
        before = self.database_snapshot()
        response = client.post(url, {"title": "", "food_item": "", "quantity": "0", "unit": "unknown"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response, TemplateResponse)
        self.assertFalse(response.is_rendered)
        form = response.context_data["form"]
        self.assertIsInstance(form, RequestForm)
        self.assertTrue(form.is_bound)
        self.assertEqual(set(form.errors), {"title", "food_item", "quantity", "unit"})
        self.assertEqual(self.database_snapshot(), before)
        data = {"title": "Beras untuk dapur", "food_item": "Beras", "quantity": "2.50", "unit": "kg",
                "owner": self.outsider.pk, "status": "fulfilled", "offered_by": self.donor.pk}
        with patch("request_board.views.create_request", wraps=create_request) as service:
            response = client.post(url, data)
        self.assert_board_redirect(response)
        service.assert_called_once()
        self.assertEqual(service.call_args.kwargs["actor"], self.owner)
        saved = Request.objects.get()
        self.assertEqual(saved.owner, self.owner)
        self.assertEqual(saved.status, "pending")
        self.assertEqual(saved.title, data["title"])
        self.assertEqual(saved.quantity, Decimal("2.50"))
        self.assertFalse(RequestOffer.objects.exists())

    def test_html_actions_use_shared_services(self):
        """H03: wrapped real services, session actor, duplicate/repeat no writes."""
        from unittest.mock import patch
        from request_board import services

        target = self.make_request()
        donor_client = self.csrf_client(self.donor)
        owner_client = self.csrf_client(self.owner)
        for action in ("offer", "fulfill", "delete"):
            self.html_url("request_" + action, target.pk)
        unrelated = self.make_request(owner=self.outsider, status="offered")
        RequestOffer.objects.create(request=unrelated, offered_by=self.owner)
        unrelated_before = (Request.objects.filter(pk=unrelated.pk).values().get(),
                            list(RequestOffer.objects.filter(request=unrelated).values()))
        for action, actor, client, repeated in (
            ("offer", self.donor, donor_client, False),
            ("offer", self.donor, donor_client, True),
            ("fulfill", self.owner, owner_client, False),
            ("fulfill", self.owner, owner_client, True),
            ("delete", self.owner, owner_client, False),
        ):
            with self.subTest(action=action, repeated=repeated):
                if action == "fulfill" and not repeated:
                    services.select_request_offer(actor=self.owner, request_id=target.pk, offer_id=target.offers.get(offered_by=self.donor).pk)
                url = self.html_url("request_" + action, target.pk)
                self.assertEqual(url, f"/board/{target.pk}/{action}/")
                self.assertEqual(resolve(url).func.__name__, "request_action")
                self.assertEqual(resolve(url).kwargs, {"request_id": target.pk, "action": action})
                before = self.database_snapshot()
                with patch("request_board.views." + action + "_request", wraps=getattr(services, action + "_request")) as service:
                    with CaptureQueriesContext(connection) as captured:
                        response = client.post(url, {"owner": self.outsider.pk, "offered_by": self.outsider.pk})
                message = self.assert_board_redirect(response)
                service.assert_called_once_with(actor=actor, request_id=target.pk)
                if repeated:
                    self.assertEqual(self.database_snapshot(), before)
                    self.assert_no_domain_writes(captured)
                if action == "offer":
                    self.assertEqual(RequestOffer.objects.get(request=target).offered_by, self.donor)
                    self.assertEqual(Request.objects.get(pk=target.pk).status, "offered")
                    if not repeated:
                        first_offer_message = message
                    else:
                        self.assertNotEqual(message, first_offer_message)
                elif action == "fulfill":
                    self.assertEqual(Request.objects.get(pk=target.pk).status, "fulfilled")
                else:
                    self.assertFalse(Request.objects.filter(pk=target.pk).exists())
                    self.assertFalse(RequestOffer.objects.filter(request_id=target.pk).exists())
        self.assertEqual(
            (Request.objects.filter(pk=unrelated.pk).values().get(),
             list(RequestOffer.objects.filter(request=unrelated).values())), unrelated_before,
        )

    def test_html_permission_and_missing_remain_errors(self):
        """H04: forbidden/missing differ from authorized business conflicts."""
        pending = self.make_request()
        offered = self.make_request(status="offered")
        fulfilled = self.make_request(status="fulfilled")
        RequestOffer.objects.create(request=offered, offered_by=self.donor)
        for action in ("offer", "fulfill", "delete"):
            self.html_url("request_" + action, pending.pk)
        cases = [("owner_self_offer", self.owner, "offer", pending.pk, 403)]
        for target in (pending, offered, fulfilled):
            cases.extend((f"nonowner_{action}_{target.status}", self.donor, action, target.pk, 403)
                         for action in ("fulfill", "delete"))
        cases.extend(("missing_" + action, self.owner, action, fulfilled.pk + 10000, 404)
                     for action in ("offer", "fulfill", "delete"))
        cases.extend((
            ("pending_conflict", self.owner, "fulfill", pending.pk, 302),
            ("fulfilled_conflict", self.donor, "offer", fulfilled.pk, 302),
        ))
        for label, actor, action, request_id, status in cases:
            with self.subTest(case=label):
                client = self.csrf_client(actor)
                before = self.database_snapshot()
                with CaptureQueriesContext(connection) as captured:
                    response = client.post(self.html_url("request_" + action, request_id))
                self.assertEqual(response.status_code, status)
                if status == 302:
                    self.assert_board_redirect(response, error=True)
                self.assertEqual(self.database_snapshot(), before)
                self.assert_no_domain_writes(captured)

        from unittest.mock import patch
        from django.core.exceptions import ValidationError
        from django.db import IntegrityError

        for action, actor in (("offer", self.donor), ("fulfill", self.owner), ("delete", self.owner)):
            for failure in (RuntimeError("internal failure"), IntegrityError("internal constraint"),
                            ValidationError("internal domain detail", code="unknown_domain_error")):
                with self.subTest(action=action, unexpected_error=type(failure).__name__):
                    client = self.csrf_client(actor)
                    before = self.database_snapshot()
                    with patch("request_board.views." + action + "_request", side_effect=failure):
                        with self.assertRaises(type(failure)) as caught:
                            client.post(self.html_url("request_" + action, offered.pk))
                    self.assertIs(caught.exception, failure)
                    self.assertEqual(self.database_snapshot(), before)

    def test_html_method_and_csrf_guards(self):
        """H05/F02: real CSRF precedes auth; HTML auth precedes method/input."""
        from contextlib import ExitStack
        from unittest.mock import patch

        target = self.make_request()
        board_url = self.html_url("request_board")
        create_url = self.html_url("request_create")
        action_urls = [self.html_url("request_" + action, target.pk) for action in ("offer", "fulfill", "delete")]
        authenticated = self.csrf_client(self.owner)
        guest = self.csrf_client()
        # Login writes last_login normally. Finish all session preparation before
        # measuring whether the rejected Board requests mutate any user/domain data.
        csrf_rejection_clients = []
        for actor_name, actor in (("guest", None), ("owner", self.owner)):
            for csrf_case in ("missing", "mismatch"):
                if csrf_case == "mismatch":
                    client = self.csrf_client(actor)
                    client.defaults["HTTP_X_CSRFTOKEN"] = "a" * 64
                else:
                    client = Client(enforce_csrf_checks=True)
                    if actor is not None:
                        client.force_login(actor)
                csrf_rejection_clients.append((actor_name, csrf_case, client))
        before = self.database_snapshot()
        with ExitStack() as stack:
            services = [stack.enter_context(patch("request_board.views." + action + "_request"))
                        for action in ("create", "offer", "fulfill", "delete")]
            method_cases = [
                (board_url + "?page=invalid", "GET", ("post", "put", "delete", "head", "options")),
                (create_url, "GET, POST", ("put", "delete", "head", "options")),
            ]
            method_cases.extend((url, "POST", ("get", "put", "delete", "head", "options"))
                                for url in action_urls)
            for url, allow, methods in method_cases:
                for method in methods:
                    with self.subTest(url=url, method=method):
                        response = getattr(authenticated, method)(url)
                        self.assertEqual(response.status_code, 405)
                        self.assertEqual(response.headers["Allow"], allow)
                        if method == "head":
                            self.assertEqual(response.content, b"")
                        if url != board_url + "?page=invalid":
                            self.assert_login_redirect(getattr(guest, method)(url), url)
                        else:
                            self.assertEqual(getattr(guest, method)(url).status_code, 405)
            for url in [create_url, *action_urls]:
                self.assert_login_redirect(guest.post(url, {"title": ""}), url)
                for actor_name, csrf_case, client in csrf_rejection_clients:
                    with self.subTest(url=url, actor=actor_name, csrf=csrf_case):
                        self.assertEqual(client.post(url).status_code, 403)
            for service in services:
                service.assert_not_called()
        self.assertEqual(self.database_snapshot(), before)

    def test_owner_preview_is_bounded_and_batched(self):
        """H06/F03: owner-only top 20 in SQL; ties, totals, 1->20 query budget."""
        services = import_module("request_board.services")
        preview_builder = getattr(services, "get_owner_offer_previews", None)
        self.assertTrue(callable(preview_builder), "Task 5B belum menyediakan owner preview builder.")
        donors = [get_user_model().objects.create_user(username=f"preview_donor_{number}") for number in range(23)]
        targets = []
        expected = {}
        endpoint_counts = []
        builder_counts = []
        owner_client = self.csrf_client(self.owner)
        # These clients are reused for the final privacy checks, so their login
        # timestamps belong to fixture setup rather than the read-only assertion.
        preview_clients = (
            ("guest", AnonymousUser(), self.csrf_client()),
            ("donor", self.donor, self.csrf_client(self.donor)),
        )
        before = None
        for size in (1, 20):
            while len(targets) < size:
                target = self.make_request(status="offered")
                targets.append(target)
                # Include fewer/exactly/more than 20, with the oldest ID newest
                # by timestamp, then a timestamp tie requiring descending ID.
                count = 23 if len(targets) % 3 == 1 else (20 if len(targets) % 3 == 2 else 2)
                offers = [RequestOffer.objects.create(request=target, offered_by=donor) for donor in donors[:count]]
                RequestOffer.objects.filter(request=target).update(created_at=self.fixed_time)
                RequestOffer.objects.filter(pk=offers[0].pk).update(created_at=self.fixed_time + timedelta(days=1))
                donor_order = [0, *range(count - 1, 0, -1)]
                expected[target.pk] = {
                    "items": [{"display_name": donors[index].get_username(),
                               "created_at": self.fixed_time + timedelta(days=1) if index == 0 else self.fixed_time}
                              for index in donor_order[:20]],
                    "total": count, "has_more": count > 20,
                }
            if size == 20:
                other_owner_target = self.make_request(owner=self.outsider, status="offered")
                off_page_target = self.make_request(status="offered")
                for target in (other_owner_target, off_page_target):
                    Request.objects.filter(pk=target.pk).update(created_at=self.fixed_time - timedelta(days=1))
                    RequestOffer.objects.create(request=target, offered_by=self.donor)
            before = self.database_snapshot()
            with CaptureQueriesContext(connection) as captured:
                page = services.get_board_page(actor=self.owner, page_value="1")
                page_rows = list(page)
                previews = preview_builder(actor=self.owner, requests=page_rows)
                for row in page_rows:
                    row.owner.get_username()
                self.assertEqual(previews, {row.pk: expected[row.pk] for row in page_rows if row.owner_id == self.owner.pk})
            builder_counts.append(len(captured))
            self.assertLessEqual(len(captured), 3)
            offer_reads = [query["sql"] for query in captured if "ROW_NUMBER()" in query["sql"].upper()]
            self.assertEqual(len(offer_reads), 1, "Preview must be bounded per parent in SQL, not sliced in Python")
            self.assertRegex(offer_reads[0], r"<=\s*20\b")
            self.assert_no_domain_writes(captured)
            with CaptureQueriesContext(connection) as endpoint_queries:
                context = self.board_context(owner_client)
                list(context["page_obj"])
                self.assertEqual(context["owner_offer_previews"], previews)
            endpoint_counts.append(len(endpoint_queries))
            self.assertEqual(self.database_snapshot(), before)
            # Prefetch must not replace the unrestricted offers manager cache.
            large_target = next(row for row in page_rows if row.offer_count > 20)
            with self.assertNumQueries(1):
                self.assertEqual(len(list(large_target.offers.all())), expected[large_target.pk]["total"])
        self.assertEqual(builder_counts[0], builder_counts[1])
        self.assertEqual(endpoint_counts[0], endpoint_counts[1])
        off_page = services.get_board_page(actor=self.owner, page_value="2")
        self.assertNotIn(other_owner_target.pk, previews)
        self.assertNotIn(off_page_target.pk, previews)
        for actor_name, actor, client in preview_clients:
            with self.subTest(preview_actor=actor_name):
                with self.assertNumQueries(0):
                    self.assertEqual(preview_builder(actor=actor, requests=page_rows), {})
                context = self.board_context(client)
                self.assertEqual(context["owner_offer_previews"], {})
        with self.assertNumQueries(0):
            self.assertEqual(preview_builder(actor=self.owner, requests=[]), {})
        off_page_previews = preview_builder(actor=self.owner, requests=list(off_page))
        self.assertEqual(set(off_page_previews), {off_page_target.pk})
        self.assertEqual(off_page_previews[off_page_target.pk]["total"], 1)
        self.assertEqual(self.database_snapshot(), before)

    def test_redirect_destination_is_fixed(self):
        """H07: action Location ignores referrer, query, payload next/redirect."""
        pending = self.make_request()
        offered = self.make_request(status="offered")
        offer = RequestOffer.objects.create(request=offered, offered_by=self.donor)
        Request.objects.filter(pk=offered.pk).update(selected_offer=offer)
        self.html_url("request_board")
        for action, actor, target in (("offer", self.donor, pending), ("fulfill", self.owner, offered),
                                      ("delete", self.owner, offered)):
            with self.subTest(action=action):
                url = self.html_url("request_" + action, target.pk)
                response = self.csrf_client(actor).post(
                    url + "?next=https://evil.invalid/query",
                    {"next": "https://evil.invalid/body", "redirect": "//evil.invalid/redirect"},
                    HTTP_REFERER="https://evil.invalid/referrer",
                )
                self.assert_board_redirect(response)

    def test_login_rejects_external_next(self):
        """H08/F04 config; password/CSRF integration runs in the separate class."""
        from django.conf import settings
        from main.views import login_page

        url = self.html_url("login")
        self.assertEqual(url, "/login/")
        callback = resolve(url).func
        self.assertIs(callback, login_page)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.template_name, "login.html")
        self.assertEqual(reverse(settings.LOGIN_URL), url)
        self.assertEqual(reverse(settings.LOGIN_REDIRECT_URL), reverse("landing_page"))


class RequestLoginIntegrationTests(HtmlAdapterFixtures, TestCase):
    """H08 integration: real passwords/session/CSRF; test loader only, never UI."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.password = "Html-login-test-password-847!"
        cls.owner.set_password(cls.password)
        cls.owner.save(update_fields=["password"])

    def setUp(self):
        from django.test import override_settings

        loader = override_settings(TEMPLATES=[{
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "APP_DIRS": False,
            "OPTIONS": {"loaders": [("django.template.loaders.locmem.Loader", {
                "login.html": "<form method='post'>{% csrf_token %}{{ form.as_p }}</form>",
            })]},
        }])
        loader.enable()
        self.addCleanup(loader.disable)

    def login_client(self):
        from django.template.response import TemplateResponse

        client = Client(enforce_csrf_checks=True)
        response = client.get(self.html_url("login"))
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response, TemplateResponse)
        self.assertTrue(response.is_rendered)
        self.assertIn("csrftoken", client.cookies)
        return client

    def test_password_login_accepts_local_next_and_rejects_external_next(self):
        """H08/F04: safe POST and query next, default landing and session identity."""
        from django.contrib.auth import SESSION_KEY

        url = self.html_url("login")
        landing = reverse("landing_page")
        local_next = self.html_url("request_create") + "?page=2"
        cases = [("absent", None, landing), ("local", local_next, local_next),
                 ("same_host", "http://testserver/board/", "http://testserver/board/"),
                 ("external", "https://evil.invalid/", landing),
                 ("protocol_relative", "//evil.invalid/", landing),
                 ("backslash", "https://testserver\\@evil.invalid/", landing),
                 ("javascript", "javascript:alert(1)", landing)]
        from urllib.parse import urlencode
        for transport in ("post", "query"):
            for label, next_value, expected in cases:
                with self.subTest(transport=transport, next_case=label):
                    client = self.login_client()
                    data = {"username": self.owner.get_username(), "password": self.password,
                            "csrfmiddlewaretoken": client.cookies["csrftoken"].value}
                    destination = url
                    if next_value is not None:
                        if transport == "post":
                            data["next"] = next_value
                        else:
                            destination += "?" + urlencode({"next": next_value})
                    before = list(Request.objects.values()), list(RequestOffer.objects.values())
                    response = client.post(destination, data)
                    self.assertEqual(response.status_code, 302)
                    self.assertEqual(response.headers["Location"], expected)
                    self.assertEqual(client.session[SESSION_KEY], str(self.owner.pk))
                    self.assertEqual((list(Request.objects.values()), list(RequestOffer.objects.values())), before)

    def test_invalid_password_and_csrf_do_not_authenticate(self):
        """H08: wrong password gives bound errors; missing/mismatch CSRF is 403."""
        from django.contrib.auth import SESSION_KEY

        url = self.html_url("login")
        client = self.login_client()
        response = client.post(url, {
            "username": self.owner.get_username(), "password": "wrong-password",
            "csrfmiddlewaretoken": client.cookies["csrftoken"].value,
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context_data["form"].is_bound)
        self.assertTrue(response.context_data["form"].non_field_errors())
        self.assertNotIn(SESSION_KEY, client.session)
        for label in ("missing", "mismatch"):
            with self.subTest(csrf=label):
                client = Client(enforce_csrf_checks=True) if label == "missing" else self.login_client()
                data = {"username": self.owner.get_username(), "password": self.password}
                if label == "mismatch":
                    data["csrfmiddlewaretoken"] = "a" * 64
                self.assertEqual(client.post(url, data).status_code, 403)
                self.assertNotIn(SESSION_KEY, client.session)
