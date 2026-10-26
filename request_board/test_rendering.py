from html.parser import HTMLParser
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import re
from urllib.parse import parse_qs, urlencode, urlsplit

from django.contrib.auth import SESSION_KEY, get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import connection
from django.template.loader import get_template
from django.template.response import TemplateResponse
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.utils.html import escape, strip_tags
from django.utils.translation import get_language, override

from request_board.forms import RequestForm
from request_board.models import Request, RequestOffer
from request_board.services import fulfill_request, offer_request


class LoginMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def attributes(self, tag):
        return [attrs for name, attrs in self.elements if name == tag]


class BoardLoginRenderingTests(TestCase):
    """T3-01: production templates, password sessions and real CSRF middleware."""

    @classmethod
    def setUpTestData(cls):
        cls.password = "Synthetic-board-login-847!"
        cls.user = get_user_model().objects.create_user(
            username="board-login-reader", password=cls.password,
        )
        cls.login_url = reverse("login")
        cls.board_url = reverse("request_board:request_board")
        cls.create_url = reverse("request_board:request_create")

    def login_page(self, client, next_url=None):
        url = self.login_url
        if next_url is not None:
            url += "?" + urlencode({"next": next_url})
        response = client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response, TemplateResponse)
        self.assertTrue(response.is_rendered)
        self.assertTemplateUsed(response, "login.html")
        self.assertTemplateUsed(response, "base.html")
        markup = LoginMarkup(response.content.decode())
        tokens = [attrs for attrs in markup.attributes("input")
                  if attrs.get("name") == "csrfmiddlewaretoken"]
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0].get("type"), "hidden")
        token = tokens[0].get("value", "")
        self.assertTrue(token)
        self.assertIn("csrftoken", client.cookies)
        return response, markup, token

    def credentials(self, token):
        return {
            "username": self.user.get_username(),
            "password": self.password,
            "csrfmiddlewaretoken": token,
        }

    def test_navbar_login_on_landing_and_board_opens_production_login(self):
        client = Client(enforce_csrf_checks=True)
        for page_url in (reverse("landing_page"), self.board_url):
            with self.subTest(page_url=page_url):
                response = client.get(page_url)
                self.assertEqual(response.status_code, 200)
                markup = BoardMarkup(response.content.decode())
                links = [node for node in markup.select("a")
                         if node["text"].strip() == "Login"
                         and any(parent["tag"] == "nav"
                                 and parent["attrs"].get("aria-label") == "Navigasi utama"
                                 for parent in node["parents"])]
                self.assertEqual(len(links), 1)
                self.assertEqual(links[0]["attrs"].get("href"), self.login_url)

                login, login_markup, _ = self.login_page(client)
                self.assert_labelled_fields(login, login_markup)

    def assert_labelled_fields(self, response, markup):
        html = response.content.decode()
        for name in ("username", "password"):
            field = response.context_data["form"][name]
            inputs = [attrs for attrs in markup.attributes("input")
                      if attrs.get("name") == name]
            self.assertEqual(len(inputs), 1)
            self.assertEqual(inputs[0].get("id"), field.id_for_label)
            labels = re.findall(
                r'<label\b[^>]*\bfor=[\"\']' + re.escape(field.id_for_label)
                + r'[\"\'][^>]*>(.*?)</label\s*>', html, flags=re.S | re.I,
            )
            self.assertTrue(any(strip_tags(label).strip() for label in labels), name)
        password = next(attrs for attrs in markup.attributes("input")
                        if attrs.get("name") == "password")
        self.assertEqual(password.get("type"), "password")
        self.assertFalse(password.get("value"))

    def test_guest_create_redirects_to_production_login_with_labelled_post_form(self):
        client = Client(enforce_csrf_checks=True)
        redirect = client.get(self.create_url)
        self.assertEqual(redirect.status_code, 302)
        self.assertEqual(redirect.headers["Location"],
                         self.login_url + "?" + urlencode({"next": self.create_url}, safe="/"))

        response, markup, _ = self.login_page(client, self.create_url)

        forms = markup.attributes("form")
        self.assertEqual(len(forms), 1)
        self.assertEqual(forms[0].get("method", "").lower(), "post")
        self.assertEqual(forms[0].get("action"), self.login_url)
        self.assert_labelled_fields(response, markup)
        self.assertIn({"type": "hidden", "name": "next", "value": self.create_url},
                      [{key: attrs.get(key) for key in ("type", "name", "value")}
                       for attrs in markup.attributes("input")])
        self.assertTrue(any(attrs.get("type", "submit") == "submit"
                            for attrs in markup.attributes("button")))
        self.assertTrue(any(attrs.get("href") == reverse("register")
                            for attrs in markup.attributes("a")))
        self.assertEqual(len(markup.attributes("main")), 1)
        self.assertTrue(any("auth" in attrs.get("class", "").split()
                            and attrs.get("lang") == "id" for _, attrs in markup.elements))
        self.assertNotIn(SESSION_KEY, client.session)

    def test_valid_password_sets_session_and_uses_default_or_safe_next(self):
        for transport in ("post", "query"):
            for next_url in (None, self.create_url + "?page=2&source=login",
                             "http://testserver" + self.board_url):
                with self.subTest(transport=transport, next_url=next_url):
                    client = Client(enforce_csrf_checks=True)
                    _, _, token = self.login_page(client, next_url)
                    data = self.credentials(token)
                    destination = self.login_url
                    if next_url is not None:
                        if transport == "post":
                            data["next"] = next_url
                        else:
                            destination += "?" + urlencode({"next": next_url})

                    response = client.post(destination, data)

                    self.assertEqual(response.status_code, 302)
                    self.assertEqual(response.headers["Location"], next_url or self.board_url)
                    self.assertEqual(client.session[SESSION_KEY], str(self.user.pk))
                    board = client.get(self.board_url)
                    self.assertTrue(board.wsgi_request.user.is_authenticated)
                    self.assertEqual(board.wsgi_request.user.pk, self.user.pk)

    def test_unsafe_next_falls_back_to_board_after_real_password_login(self):
        for next_url in ("https://evil.invalid/", "//evil.invalid/",
                         "https://testserver\\@evil.invalid/", "javascript:alert(1)"):
            for transport in ("post", "query"):
                with self.subTest(next_url=next_url, transport=transport):
                    client = Client(enforce_csrf_checks=True)
                    _, _, token = self.login_page(client, next_url)
                    data = self.credentials(token)
                    destination = self.login_url
                    if transport == "post":
                        data["next"] = next_url
                    else:
                        destination += "?" + urlencode({"next": next_url})

                    response = client.post(destination, data)

                    self.assertEqual(response.status_code, 302)
                    self.assertEqual(response.headers["Location"], self.board_url)
                    self.assertEqual(client.session[SESSION_KEY], str(self.user.pk))

    def test_invalid_password_renders_bound_errors_and_preserves_safe_next(self):
        client = Client(enforce_csrf_checks=True)
        _, _, token = self.login_page(client, self.create_url)
        data = self.credentials(token)
        data.update(password="incorrect-synthetic-password", next=self.create_url)

        response = client.post(self.login_url, data)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "login.html")
        form = response.context_data["form"]
        self.assertTrue(form.is_bound)
        self.assertTrue(form.non_field_errors())
        self.assertEqual(form["username"].value(), self.user.get_username())
        markup = LoginMarkup(response.content.decode())
        self.assert_labelled_fields(response, markup)
        for error in form.non_field_errors():
            self.assertContains(response, escape(error))
        self.assertTrue(any(attrs.get("role") == "alert"
                            or "errorlist" in attrs.get("class", "").split()
                            for _, attrs in markup.elements))
        self.assertTrue(any(attrs.get("name") == "next"
                            and attrs.get("value") == self.create_url
                            for attrs in markup.attributes("input")))
        self.assertContains(response, self.user.get_username())
        self.assertNotContains(response, data["password"])
        self.assertNotIn(SESSION_KEY, client.session)

    def test_required_field_errors_are_rendered_without_authentication(self):
        client = Client(enforce_csrf_checks=True)
        _, _, token = self.login_page(client)

        response = client.post(self.login_url, {"csrfmiddlewaretoken": token})

        self.assertEqual(response.status_code, 200)
        form = response.context_data["form"]
        self.assertTrue(form.is_bound)
        self.assert_labelled_fields(response, LoginMarkup(response.content.decode()))
        for name in ("username", "password"):
            self.assertTrue(form[name].errors)
            for error in form[name].errors:
                self.assertContains(response, escape(error))
        self.assertNotIn(SESSION_KEY, client.session)

    def test_missing_and_mismatched_csrf_cannot_create_login_session(self):
        for token_case in ("missing", "mismatch"):
            with self.subTest(token_case=token_case):
                client = Client(enforce_csrf_checks=True)
                _, _, token = self.login_page(client)
                data = self.credentials(token)
                if token_case == "missing":
                    del data["csrfmiddlewaretoken"]
                else:
                    data["csrfmiddlewaretoken"] = ("a" if token[0] != "a" else "b") + token[1:]

                response = client.post(self.login_url, data)

                self.assertEqual(response.status_code, 403)
                self.assertNotIn(SESSION_KEY, client.session)

    def test_expired_session_requires_login_then_returns_to_create(self):
        client = Client(enforce_csrf_checks=True)
        _, _, token = self.login_page(client)
        response = client.post(self.login_url, self.credentials(token))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(client.session[SESSION_KEY], str(self.user.pk))
        # Expire the server-side session while retaining the stale browser cookie.
        session = client.session
        session.set_expiry(-1)
        session.save()

        response = client.get(self.create_url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"],
                         self.login_url + "?" + urlencode({"next": self.create_url}, safe="/"))
        self.assertNotIn(SESSION_KEY, client.session)
        _, _, token = self.login_page(client, self.create_url)
        data = self.credentials(token)
        data["next"] = self.create_url
        response = client.post(self.login_url, data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], self.create_url)
        self.assertEqual(client.session[SESSION_KEY], str(self.user.pk))

    def test_local_next_is_escaped_in_hidden_input(self):
        client = Client(enforce_csrf_checks=True)
        next_url = self.create_url + '?q=\"><script>alert(1)</script>&source=login'

        response, markup, _ = self.login_page(client, next_url)

        self.assertEqual(response.context_data["next"], next_url)
        self.assertTrue(any(attrs.get("name") == "next" and attrs.get("value") == next_url
                            for attrs in markup.attributes("input")))
        self.assertNotContains(response, "<script>alert(1)</script>")
        self.assertContains(response, escape(next_url))


class BoardDocumentLanguageTests(TestCase):
    """LANG: document language through production templates and middleware."""

    @classmethod
    def setUpTestData(cls):
        cls.password = "Synthetic-LANG-password-847!"
        cls.owner = get_user_model().objects.create_user(
            username="lang-owner", password=cls.password,
        )
        Request.objects.create(owner=cls.owner, title="Beras warga", food_item="Beras",
                               quantity=1, unit="kg")
        cls.board_url = reverse("request_board:request_board")
        cls.login_url = reverse("login")
        cls.create_url = reverse("request_board:request_create")

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)

    def csrf_token(self, response):
        tokens = [attrs for attrs in LoginMarkup(response.content.decode()).attributes("input")
                  if attrs.get("name") == "csrfmiddlewaretoken"]
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0].get("type"), "hidden")
        self.assertTrue(tokens[0].get("value"))
        self.assertIn("csrftoken", self.client.cookies)
        return tokens[0]["value"]

    def login_owner(self):
        page = self.client.get(self.login_url)
        self.assertEqual(page.status_code, 200)
        response = self.client.post(self.login_url, {
            "username": self.owner.username, "password": self.password,
            "csrfmiddlewaretoken": self.csrf_token(page),
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], self.board_url)
        self.assertEqual(self.client.session[SESSION_KEY], str(self.owner.pk))

    def assert_board_document(self, response, template):
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, template)
        self.assertTemplateUsed(response, "base.html")
        markup = LoginMarkup(response.content.decode())
        documents = markup.attributes("html")
        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0].get("lang"), "id")
        root_class = "auth" if template == "login.html" else "request-board"
        roots = [attrs for _, attrs in markup.elements
                 if root_class in attrs.get("class", "").split()]
        self.assertEqual(len(roots), 1)
        self.assertEqual(roots[0].get("lang"), "id")

    def test_guest_board_document_is_indonesian(self):
        response = self.client.get(self.board_url)

        self.assertFalse(response.wsgi_request.user.is_authenticated)
        self.assert_board_document(response, "request_board/index.html")

    def test_owner_board_document_is_indonesian(self):
        self.login_owner()

        response = self.client.get(self.board_url)

        self.assertEqual(response.wsgi_request.user.pk, self.owner.pk)
        self.assert_board_document(response, "request_board/index.html")

    def test_login_get_document_is_indonesian(self):
        response = self.client.get(self.login_url)

        self.assertFalse(response.context_data["form"].is_bound)
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assert_board_document(response, "login.html")

    def test_invalid_login_document_is_indonesian(self):
        page = self.client.get(self.login_url)
        self.assertEqual(page.status_code, 200)

        response = self.client.post(self.login_url, {
            "username": self.owner.username, "password": "incorrect-LANG-password",
            "csrfmiddlewaretoken": self.csrf_token(page),
        })

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context_data["form"].is_bound)
        self.assertTrue(response.context_data["form"].non_field_errors())
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assert_board_document(response, "login.html")

    def test_create_get_document_is_indonesian(self):
        self.login_owner()

        response = self.client.get(self.create_url)

        self.assertEqual(response.wsgi_request.user.pk, self.owner.pk)
        self.assertFalse(response.context_data["form"].is_bound)
        self.assert_board_document(response, "request_board/request_form.html")

    def test_invalid_create_document_is_indonesian(self):
        self.login_owner()
        page = self.client.get(self.create_url)
        self.assertEqual(page.status_code, 200)
        before = list(Request.objects.values()), list(RequestOffer.objects.values())

        response = self.client.post(self.create_url, {
            "csrfmiddlewaretoken": self.csrf_token(page),
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.wsgi_request.user.pk, self.owner.pk)
        self.assertTrue(response.context_data["form"].is_bound)
        self.assertTrue(response.context_data["form"].errors)
        self.assertEqual((list(Request.objects.values()), list(RequestOffer.objects.values())), before)
        self.assert_board_document(response, "request_board/request_form.html")


class DocumentLanguageControlTests(TestCase):
    def assert_document_language(self, html, expected):
        documents = LoginMarkup(html).attributes("html")
        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0].get("lang"), expected)

    def test_base_without_override_defaults_to_indonesian(self):
        response = self.client.get(reverse("landing_page"))
        self.assertEqual(response.status_code, 200)
        template = get_template("base.html")

        html = template.render({}, request=response.wsgi_request)

        self.assert_document_language(html, "id")

    def test_landing_document_remains_indonesian(self):
        response = self.client.get(reverse("landing_page"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "index.html")
        self.assert_document_language(response.content.decode(), "id")


class BoardMarkup(HTMLParser):
    """Collect visible text and ancestry without imposing CSS selectors."""

    def __init__(self, html):
        super().__init__()
        self.nodes = []
        self.stack = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = {"tag": tag, "attrs": dict(attrs), "text": "",
                "parents": tuple(self.stack)}
        self.nodes.append(node)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img",
                       "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if any(node["tag"] in {"script", "style", "template"}
               or "hidden" in node["attrs"]
               or node["attrs"].get("aria-hidden") == "true" for node in self.stack):
            return
        for node in self.stack:
            node["text"] += data

    def select(self, tag, within=None):
        return [node for node in self.nodes if node["tag"] == tag
                and (within is None or any(parent is within for parent in node["parents"]))]


class PublicBoardRenderingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.owner = users.objects.create_user(username="Pemilik-publik")
        cls.reader = users.objects.create_user(username="Pembaca-board")
        donor = users.objects.create_user(username="Donor-fixture")
        cls.fixed_time = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
        cls.records = []
        for index, title in enumerate(("Beras untuk dapur", "Sayur untuk warga", "Buah untuk posko")):
            item = Request.objects.create(
                owner=cls.owner, title=title, food_item=("Beras", "Bayam", "Jeruk")[index],
                quantity=Decimal("2.50"), unit="kg", description=f"Keterangan-{index}",
                location=f"Lokasi-{index}",
            )
            if index >= 1:
                offer_request(actor=cls.reader, request_id=item.pk)
                offer_request(actor=donor, request_id=item.pk)
            if index == 2:
                fulfill_request(actor=cls.owner, request_id=item.pk)
            Request.objects.filter(pk=item.pk).update(created_at=cls.fixed_time)
            item.refresh_from_db()
            cls.records.append(item)
        cls.board_url = reverse("request_board:request_board")

    def board(self):
        response = self.client.get(self.board_url)
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response, TemplateResponse)
        self.assertTrue(response.is_rendered)
        self.assertTemplateUsed(response, "request_board/index.html")
        self.assertTemplateUsed(response, "base.html")
        return response, BoardMarkup(response.content.decode())

    def assert_public_cards(self):
        _, markup = self.board()
        self.assertEqual(len(markup.select("main")), 1)
        # Each posting must be a semantic article or list item, with its own metadata.
        for item, label, count in zip(self.records, ("Pending", "Ditawarkan", "Terpenuhi"), (0, 2, 2)):
            with self.subTest(title=item.title):
                cards = [node for node in markup.nodes if node["tag"] in {"article", "li"}
                         and item.title in node["text"]]
                self.assertTrue(cards, f"Kartu publik belum menampilkan {item.title}")
                card = cards[-1]
                text = " ".join(card["text"].split())
                for value in (item.title, item.food_item, "Pemilik-publik",
                              item.description, item.location, label):
                    self.assertIn(value, text)
                self.assertRegex(text.lower(), r"\bkg\b")
                self.assertRegex(text, r"\b2[.,]50?\b")
                self.assertRegex(text.lower(), rf"(?:\b{count}\s+penawaran\b|\bpenawaran\s*:\s*{count}\b)")
                times = markup.select("time", within=card)
                self.assertTrue(times, "Waktu dibuat harus terbaca dengan datetime semantik")
                self.assertTrue(any(node["text"].strip() and parse_datetime(
                    node["attrs"].get("datetime", "")
                ) == self.fixed_time for node in times))

    def test_guest_sees_real_fields_all_statuses_and_offer_counts(self):
        self.assert_public_cards()

    def test_authenticated_reader_sees_real_fields_all_statuses_and_offer_counts(self):
        self.client.force_login(self.reader)
        self.assert_public_cards()

    def test_user_content_is_escaped_and_remains_readable(self):
        payloads = {
            "title": '<script>alert("judul")</script>',
            "food_item": '<b>Beras & kacang</b>',
            "description": '<img src=x onerror="alert(1)">',
            "location": '<svg onload="alert(2)">Lokasi</svg>',
        }
        Request.objects.filter(pk=self.records[0].pk).update(**payloads)
        username = '<em>Pemilik & publik</em>'
        get_user_model().objects.filter(pk=self.owner.pk).update(username=username)
        response, markup = self.board()
        for value in (*payloads.values(), username):
            with self.subTest(value=value):
                self.assertNotContains(response, value)
                self.assertContains(response, escape(value))
                self.assertTrue(any(value in node["text"] for node in markup.select("main")))


class BoardPaginationRenderingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.reader = get_user_model().objects.create_user(username="Pembaca-pagination")
        fixed_time = datetime(2026, 2, 3, 12, tzinfo=timezone.utc)
        cls.records = []
        for index in range(21):
            item = Request.objects.create(
                owner=cls.reader, title=f"Posting urutan {index:02d}", food_item="Beras",
                quantity=1, unit="kg",
            )
            # Highest ID is oldest; the middle 19 timestamps tie.
            offset = 1 if index == 0 else (-1 if index == 20 else 0)
            Request.objects.filter(pk=item.pk).update(
                created_at=fixed_time + timedelta(days=offset),
            )
            cls.records.append(item)
        cls.board_url = reverse("request_board:request_board")
        cls.create_url = reverse("request_board:request_create")
        cls.login_url = reverse("login")

    def board(self, query=""):
        response = self.client.get(self.board_url + query)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "request_board/index.html")
        return response, BoardMarkup(response.content.decode())

    def test_twenty_then_one_rendered_items_follow_timestamp_and_id_order(self):
        expected = ["Posting urutan 00"] + [f"Posting urutan {index:02d}" for index in range(19, 0, -1)]
        for query, titles in (("", expected), ("?page=2", ["Posting urutan 20"]),
                              ("?page=001&status=fulfilled&sort=id", expected)):
            with self.subTest(query=query):
                response, markup = self.board(query)
                self.assertEqual(response.context_data["page_obj"].paginator.count, 21)
                self.assertEqual(response.context_data["page_obj"].paginator.num_pages, 2)
                headings = [node["text"].strip() for node in markup.nodes
                            if node["tag"] in {"h1", "h2", "h3", "h4", "h5", "h6"}
                            and node["text"].strip().startswith("Posting urutan ")]
                self.assertEqual(headings, titles, "Judul posting harus terbaca dalam urutan halaman")
                for item in self.records:
                    if item.title not in titles:
                        self.assertNotContains(response, item.title)

    def test_pagination_has_named_nav_current_page_and_real_direction_links(self):
        for page_number, target, direction, absent in (
            (1, 2, r"berikut|selanjutnya", r"sebelum"),
            (2, 1, r"sebelum", r"berikut|selanjutnya"),
        ):
            with self.subTest(page=page_number):
                _, markup = self.board(f"?page={page_number}")
                navs = [node for node in markup.select("nav")
                        if any(child["attrs"].get("aria-current") == "page"
                               for child in markup.nodes
                               if any(parent is node for parent in child["parents"]))]
                self.assertEqual(len(navs), 1, "Pagination harus punya nav dan aria-current=page")
                nav = navs[0]
                label = nav["attrs"].get("aria-label", "")
                labelledby = nav["attrs"].get("aria-labelledby", "").split()
                self.assertTrue(label.strip() or any(
                    node["attrs"].get("id") in labelledby and node["text"].strip()
                    for node in markup.nodes
                ), "Nav pagination memerlukan nama aksesibel")
                currents = [node for node in markup.nodes
                            if node["attrs"].get("aria-current") == "page"
                            and any(parent is nav for parent in node["parents"])]
                self.assertEqual(len(currents), 1)
                self.assertRegex(currents[0]["text"], rf"\b{page_number}\b")
                links = markup.select("a", within=nav)
                directional = [node for node in links if re.search(
                    direction, node["text"] + " " + node["attrs"].get("aria-label", ""), re.I,
                )]
                self.assertEqual(len(directional), 1)
                url = urlsplit(directional[0]["attrs"].get("href", ""))
                self.assertFalse(url.scheme or url.netloc or url.fragment)
                self.assertEqual(url.path, self.board_url)
                self.assertEqual(parse_qs(url.query), {"page": [str(target)]})
                self.assertFalse(any(re.search(
                    absent, node["text"] + " " + node["attrs"].get("aria-label", ""), re.I,
                ) for node in links), "Jangan render link arah yang di luar rentang")

    def test_empty_page_one_explains_next_step_and_has_contextual_create_link(self):
        Request.objects.all().delete()
        for actor in (None, self.reader):
            with self.subTest(actor="guest" if actor is None else "authenticated"):
                if actor is not None:
                    self.client.force_login(actor)
                response, markup = self.board()
                mains = markup.select("main")
                self.assertEqual(len(mains), 1)
                text = " ".join(mains[0]["text"].lower().split())
                self.assertRegex(text, r"(?:belum|tidak ada).*permintaan")
                ctas = []
                for link in markup.select("a", within=mains[0]):
                    url = urlsplit(link["attrs"].get("href", ""))
                    if url.scheme or url.netloc or url.fragment:
                        continue
                    if url.path == self.create_url and not url.query:
                        ctas.append(link)
                    elif actor is None and url.path == self.login_url and parse_qs(url.query) == {"next": [self.create_url]}:
                        ctas.append(link)
                self.assertTrue(ctas, "Empty state harus mengarah ke create atau login dengan next=create")
                self.assertTrue(any(re.search(r"buat|tambah|masuk", link["text"], re.I) for link in ctas))
                self.assertEqual(response.context_data["page_obj"].number, 1)
                self.assertEqual(response.context_data["page_obj"].paginator.count, 0)
                self.assertEqual(self.client.get(self.board_url + "?page=2").status_code, 404)

    def test_invalid_and_out_of_range_pages_keep_existing_http_contract(self):
        for query, status in (
            ("?page=0", 400), ("?page=-1", 400), ("?page=", 400),
            ("?page=abc", 400), ("?page=1.5", 400), ("?page=١", 400),
            ("?page=1&page=2", 400), ("?page=3", 404),
            ("?page=" + "9" * 5000, 404),
        ):
            with self.subTest(query=query[:60]):
                self.assertEqual(self.client.get(self.board_url + query).status_code, status)


class RequestCreateRenderingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.actor = get_user_model().objects.create_user(username="Pembuat-permintaan")
        cls.create_url = reverse("request_board:request_create")
        cls.board_url = reverse("request_board:request_board")

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.actor)
        # A pre-existing row makes unintended updates/deletes observable too.
        Request.objects.create(owner=self.actor, title="Tetap utuh", food_item="Beras",
                               quantity=1, unit="kg")

    def snapshot(self):
        return (list(Request.objects.order_by("pk").values()),
                list(RequestOffer.objects.order_by("pk").values()))

    def valid_data(self):
        return {"title": "Bahan untuk dapur", "food_item": "Beras merah",
                "quantity": "2.50", "unit": "kg", "description": "Untuk warga",
                "location": "Kecamatan sekitar"}

    def create_page(self):
        response = self.client.get(self.create_url)
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response, TemplateResponse)
        self.assertTrue(response.is_rendered)
        self.assertTemplateUsed(response, "request_board/request_form.html")
        self.assertTemplateUsed(response, "base.html")
        markup = BoardMarkup(response.content.decode())
        forms = [node for node in markup.select("form")
                 if node["attrs"].get("method", "").lower() == "post"
                 and node["attrs"].get("action") == self.create_url]
        self.assertEqual(len(forms), 1, "Form create harus POST ke route existing")
        tokens = [node for node in markup.select("input", within=forms[0])
                  if node["attrs"].get("name") == "csrfmiddlewaretoken"]
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0]["attrs"].get("type"), "hidden")
        token = tokens[0]["attrs"].get("value", "")
        self.assertTrue(token)
        self.assertIn("csrftoken", self.client.cookies)
        return response, markup, forms[0], token

    def controls(self, markup):
        controls = {}
        for node in markup.nodes:
            if node["tag"] in {"input", "select", "textarea"}:
                name = node["attrs"].get("name")
                if name in {"title", "food_item", "quantity", "unit", "description", "location"}:
                    self.assertNotIn(name, controls, "Field tidak boleh terduplikasi")
                    controls[name] = node
        self.assertEqual(set(controls),
                         {"title", "food_item", "quantity", "unit", "description", "location"})
        ids = [node["attrs"].get("id") for node in markup.nodes if node["attrs"].get("id")]
        self.assertEqual(len(ids), len(set(ids)), "ID dokumen harus unik untuk association")
        for name, control in controls.items():
            control_id = control["attrs"].get("id")
            labels = [node for node in markup.select("label")
                      if node["attrs"].get("for") == control_id and node["text"].strip()]
            self.assertTrue(control_id and labels, f"Label {name} belum terhubung")
        return controls

    def assert_control_value(self, markup, control, expected):
        if control["tag"] == "textarea":
            actual = control["text"]
        elif control["tag"] == "select":
            options = markup.select("option", within=control)
            selected = [node for node in options if "selected" in node["attrs"]]
            self.assertEqual(len(selected), 1)
            actual = selected[0]["attrs"].get("value")
        else:
            actual = control["attrs"].get("value", "")
        self.assertEqual(actual, expected)

    def test_authenticated_get_renders_production_form_without_writes(self):
        before = self.snapshot()
        response, markup, form_node, _ = self.create_page()

        self.assertEqual(self.snapshot(), before)
        self.assertIsInstance(response.context_data["form"], RequestForm)
        self.assertFalse(response.context_data["form"].is_bound)
        self.assertEqual(len(markup.select("main")), 1)
        self.assertEqual(len(markup.select("h1")), 1)
        self.assertTrue(any("request-board" in node["attrs"].get("class", "").split()
                            and node["attrs"].get("lang") == "id" for node in markup.nodes))
        self.assertTrue(any(node["attrs"].get("type", "submit") == "submit"
                            and node["text"].strip()
                            for node in markup.select("button", within=form_node)))
        self.assertTrue(any(node["attrs"].get("href") == self.board_url
                            and re.search(r"batal|kembali", node["text"], re.I)
                            for node in markup.select("a", within=markup.select("main")[0])))

    def test_six_labelled_fields_required_optional_and_existing_units(self):
        _, markup, form_node, _ = self.create_page()
        controls = self.controls(markup)
        names = [node["attrs"].get("name") for node in markup.nodes
                 if node["tag"] in {"input", "select", "textarea"}
                 and any(parent is form_node for parent in node["parents"])]
        self.assertEqual(set(names), set(controls) | {"csrfmiddlewaretoken"})
        for name, control in controls.items():
            with self.subTest(field=name):
                self.assertNotEqual(control["attrs"].get("type"), "hidden")
                self.assertNotIn("disabled", control["attrs"])
                self.assertNotIn("readonly", control["attrs"])
                self.assertEqual("required" in control["attrs"],
                                 name not in {"description", "location"})
                if name in {"description", "location"}:
                    related_ids = control["attrs"].get("aria-describedby", "").split()
                    text = " ".join(node["text"] for node in markup.nodes
                                    if node["attrs"].get("for") == control["attrs"]["id"]
                                    or node["attrs"].get("id") in related_ids)
                    self.assertRegex(text.lower(), r"opsional|tidak wajib|boleh.*kosong")
        self.assertEqual(controls["unit"]["tag"], "select")
        choices = [(node["attrs"].get("value"), node["text"].strip())
                   for node in markup.select("option", within=controls["unit"])
                   if node["attrs"].get("value")]
        self.assertEqual(choices, [("gram", "Gram"), ("kg", "Kg"), ("ml", "Ml"),
                                   ("liter", "Liter"), ("buah", "Buah"), ("butir", "Butir"),
                                   ("ikat", "Ikat"), ("porsi", "Porsi")])
        self.assertFalse(any(node["attrs"].get("name") in {"owner", "owner_id", "status"}
                             for node in markup.nodes))

    def test_valid_submit_uses_actor_pending_prg_and_visible_success_message(self):
        for empty_optional in (False, True):
            with self.subTest(empty_optional=empty_optional):
                _, _, _, token = self.create_page()
                data = self.valid_data()
                if empty_optional:
                    data.update(description="", location="")
                old_ids = set(Request.objects.values_list("pk", flat=True))
                offers_before = list(RequestOffer.objects.values())

                response = self.client.post(self.create_url, {**data, "csrfmiddlewaretoken": token})

                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.headers["Location"], self.board_url)
                created = Request.objects.exclude(pk__in=old_ids).get()
                self.assertEqual(created.owner_id, self.actor.pk)
                self.assertEqual(created.status, "pending")
                for name, value in data.items():
                    self.assertEqual(getattr(created, name), Decimal(value) if name == "quantity" else value)
                self.assertEqual(Request.objects.count(), len(old_ids) + 1)
                self.assertEqual(list(RequestOffer.objects.values()), offers_before)
                board = self.client.get(response.headers["Location"])
                self.assertEqual(board.status_code, 200)
                self.assertEqual(board.wsgi_request.method, "GET")
                self.assertTemplateUsed(board, "request_board/index.html")
                feedback = [message for message in board.context["messages"] if message.level_tag == "success"]
                self.assertTrue(feedback, "Pesan sukses harus tersedia setelah redirect")
                markup = BoardMarkup(board.content.decode())
                for message in feedback:
                    self.assertTrue(any(str(message) in node["text"] and
                                        (node["attrs"].get("role") in {"status", "alert"}
                                         or node["attrs"].get("aria-live") in {"polite", "assertive"})
                                        for node in markup.nodes), "Feedback harus terlihat dan semantik")
                before_refresh = self.snapshot()
                refreshed = self.client.get(self.board_url)
                self.assertEqual(self.snapshot(), before_refresh)
                for message in feedback:
                    self.assertNotContains(refreshed, escape(str(message)))

    def test_invalid_bound_form_preserves_values_and_associates_errors_without_writes(self):
        cases = [(name, "") for name in ("title", "food_item", "quantity", "unit")]
        cases += [("quantity", value) for value in ("0", "-1", "abc", "1.001", "1000000")]
        cases += [("unit", "ember")]
        cases += [(name, "x" * length) for name, length in
                  (("title", 121), ("food_item", 121), ("description", 1001), ("location", 121))]
        for name, invalid in cases:
            with self.subTest(field=name, value=invalid[:25]):
                _, _, _, token = self.create_page()
                data = {**self.valid_data(), name: invalid}
                before = self.snapshot()

                response = self.client.post(self.create_url, {**data, "csrfmiddlewaretoken": token})

                self.assertEqual(self.snapshot(), before)
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "request_board/request_form.html")
                form = response.context_data["form"]
                self.assertTrue(form.is_bound)
                self.assertIn(name, form.errors)
                markup = BoardMarkup(response.content.decode())
                controls = self.controls(markup)
                for field_name, value in data.items():
                    self.assertEqual(form[field_name].value(), value)
                    if field_name != "unit" or value in {"kg", ""}:
                        self.assert_control_value(markup, controls[field_name], value)
                for field_name in form.errors:
                    if field_name == "__all__":
                        continue
                    control = controls[field_name]
                    self.assertEqual(control["attrs"].get("aria-invalid"), "true")
                    error_ids = control["attrs"].get("aria-describedby", "").split()
                    associated = [node for node in markup.nodes if node["attrs"].get("id") in error_ids]
                    self.assertTrue(associated, f"Error {field_name} belum terhubung")
                    for error in form[field_name].errors:
                        self.assertTrue(any(str(error) in node["text"] for node in associated))
                    self.assertTrue(any(node["attrs"].get("href") == "#" + control["attrs"]["id"]
                                        and node["text"].strip() for node in markup.select("a")),
                                    "Ringkasan error harus menaut ke field")
                for error in form.non_field_errors():
                    self.assertContains(response, escape(error))

    def test_bound_user_values_are_escaped_without_injecting_markup(self):
        _, _, _, token = self.create_page()
        payloads = {"title": '<script>alert("judul")</script>',
                    "food_item": '<b>Beras & kacang</b>',
                    "description": '</textarea><img src=x onerror="alert(1)">',
                    "location": '<svg onload="alert(2)">Area</svg>'}
        data = {**self.valid_data(), **payloads, "quantity": "0", "csrfmiddlewaretoken": token}
        before = self.snapshot()

        response = self.client.post(self.create_url, data)

        self.assertEqual(self.snapshot(), before)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context_data["form"].is_bound)
        markup = BoardMarkup(response.content.decode())
        controls = self.controls(markup)
        for name, payload in payloads.items():
            with self.subTest(field=name):
                self.assertNotContains(response, payload)
                self.assertContains(response, escape(payload))
                self.assert_control_value(markup, controls[name], payload)

    def test_missing_and_mismatched_html_csrf_tokens_reject_without_writes(self):
        for token_case in ("missing", "mismatch"):
            with self.subTest(token_case=token_case):
                _, _, _, token = self.create_page()
                data = self.valid_data()
                if token_case == "mismatch":
                    data["csrfmiddlewaretoken"] = ("a" if token[0] != "a" else "b") + token[1:]
                before = self.snapshot()

                response = self.client.post(self.create_url, data)

                self.assertEqual(response.status_code, 403)
                self.assertEqual(self.snapshot(), before)


class ToastNotificationRenderingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.actor = get_user_model().objects.create_user(username="Pembaca-toast")
        cls.board_url = reverse("request_board:request_board")
        cls.create_url = reverse("request_board:request_create")

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.actor)

    def snapshot(self):
        return (list(Request.objects.order_by("pk").values()),
                list(RequestOffer.objects.order_by("pk").values()))

    def create_form(self):
        response = self.client.get(self.create_url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "request_board/request_form.html")
        markup = BoardMarkup(response.content.decode())
        forms = [node for node in markup.select("form")
                 if node["attrs"].get("method", "").lower() == "post"
                 and node["attrs"].get("action") == self.create_url]
        self.assertEqual(len(forms), 1)
        tokens = [node for node in markup.select("input", within=forms[0])
                  if node["attrs"].get("name") == "csrfmiddlewaretoken"]
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0]["attrs"].get("type"), "hidden")
        self.assertIn("csrftoken", self.client.cookies)
        token = tokens[0]["attrs"].get("value", "")
        self.assertTrue(token)
        return response, token

    def queue_messages(self, response, entries):
        # Persist through the actual configured storage; no fake view or context.
        for level, text in entries:
            messages.add_message(response.wsgi_request, level, text)
        response.wsgi_request._messages.update(response)
        self.client.cookies.update(response.cookies)

    def valid_data(self):
        return {"title": "Bahan dapur toast", "food_item": "Beras merah",
                "quantity": "2.50", "unit": "kg", "description": "Untuk warga",
                "location": "Kecamatan sekitar"}

    def assert_message_rendered_once(self, response, markup, text, level):
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "request_board/_messages.html")
        html = response.content.decode()
        self.assertEqual(html.count(str(escape(text))), 1,
                         "Pesan global harus sekali di seluruh HTML, termasuk hidden DOM")
        mains = markup.select("main")
        self.assertEqual(len(mains), 1)
        self.assertEqual(mains[0]["text"].count(text), 1)
        role = "alert" if level == messages.ERROR else "status"
        feedback = [node for node in markup.nodes
                    if node["attrs"].get("role") == role and text in node["text"]]
        self.assertEqual(len(feedback), 1)
        if level != messages.ERROR:
            self.assertEqual(feedback[0]["attrs"].get("aria-live"), "polite")
        return markup, feedback[0]

    def assert_toast_area(self, markup, feedback):
        candidates = [node for node in markup.nodes
                      if "data-toast-region" in node["attrs"] or re.search(
                          r"(?:^|[\s_-])toasts?(?:$|[\s_-])",
                          node["attrs"].get("id", "") + " "
                          + node["attrs"].get("class", ""), re.I)]
        areas = [node for node in candidates if not any(
            any(parent is candidate for parent in node["parents"])
            for candidate in candidates)]
        self.assertEqual(len(areas), 1,
                         "Notifikasi global memerlukan satu area toast teridentifikasi di DOM")
        area = areas[0]
        self.assertTrue(area["attrs"].get("aria-label", "").strip()
                        or area["attrs"].get("aria-labelledby", "").strip())
        self.assertTrue(any(parent is area for parent in feedback["parents"])
                        or feedback is area)
        self.assertTrue(any("request-board" in node["attrs"].get("class", "").split()
                            for node in area["parents"]))
        for node in [area, *[node for node in markup.nodes
                            if any(parent is area for parent in node["parents"])]]:
            self.assertNotIn("autofocus", node["attrs"])
            self.assertNotIn("hidden", node["attrs"])
            if node["tag"] == "svg":
                # The close icon is decorative; its button supplies the accessible name.
                self.assertTrue(any(parent["tag"] == "button"
                                    and parent["attrs"].get("aria-label") == "Tutup notifikasi"
                                    for parent in node["parents"]))
            else:
                self.assertNotEqual(node["attrs"].get("aria-hidden"), "true")
        return area

    def test_success_info_error_are_escaped_once_in_shared_toast_on_board_and_create(self):
        for url in (self.board_url, self.create_url):
            for level in (messages.SUCCESS, messages.INFO, messages.ERROR):
                with self.subTest(url=url, level=level):
                    text = f'Pesan uji {level}: <img src=x onerror="alert(1)"> & selesai'
                    seed, _ = self.create_form()
                    self.queue_messages(seed, [(level, text)])
                    before = self.snapshot()

                    response = self.client.get(url)

                    self.assertEqual(self.snapshot(), before)
                    self.assertNotContains(response, text)
                    markup = BoardMarkup(response.content.decode())
                    markup, feedback = self.assert_message_rendered_once(response, markup, text, level)
                    self.assertFalse(any("onerror" in node["attrs"] for node in markup.nodes))
                    self.assert_toast_area(markup, feedback)

    def test_each_notification_has_a_labelled_non_submit_close_button(self):
        for url in (self.board_url, self.create_url):
            with self.subTest(url=url):
                seed, _ = self.create_form()
                self.queue_messages(seed, [(messages.SUCCESS, "Berhasil disimpan."),
                                           (messages.ERROR, "Periksa kembali isian.")])
                markup = BoardMarkup(self.client.get(url).content.decode())
                notifications = [node for node in markup.nodes
                                 if "request-board__toast" in node["attrs"].get("class", "").split()]
                self.assertEqual(len(notifications), 2)
                for notification in notifications:
                    buttons = markup.select("button", within=notification)
                    self.assertEqual(len(buttons), 1)
                    self.assertEqual(buttons[0]["attrs"].get("type"), "button")
                    self.assertEqual(buttons[0]["attrs"].get("aria-label"), "Tutup notifikasi")
                    self.assertFalse(any(parent["tag"] == "a" for parent in buttons[0]["parents"]))

    def test_queued_messages_share_one_area_and_are_consumed_after_first_render(self):
        entries = [(messages.SUCCESS, "Penyimpanan uji berhasil."),
                   (messages.INFO, "Informasi uji tersedia."),
                   (messages.ERROR, "Kesalahan uji perlu diperiksa.")]
        seed, _ = self.create_form()
        self.queue_messages(seed, entries)
        before = self.snapshot()

        response = self.client.get(self.board_url)
        refreshed = self.client.get(self.board_url)

        self.assertEqual(self.snapshot(), before)
        self.assertEqual(refreshed.status_code, 200)
        self.assertEqual([(message.level, str(message))
                          for message in response.context["messages"]], entries)
        markup = BoardMarkup(response.content.decode())
        feedbacks = []
        for level, text in entries:
            self.assertNotContains(refreshed, escape(text))
            markup, feedback = self.assert_message_rendered_once(response, markup, text, level)
            feedbacks.append(feedback)
        area = self.assert_toast_area(markup, feedbacks[0])
        for feedback in feedbacks[1:]:
            self.assertTrue(any(parent is area for parent in feedback["parents"]))

    def test_real_create_prg_persists_pending_and_shows_success_toast_once(self):
        _, token = self.create_form()
        data = self.valid_data()

        response = self.client.post(self.create_url, {**data, "csrfmiddlewaretoken": token})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], self.board_url)
        created = Request.objects.get()
        self.assertEqual(created.owner_id, self.actor.pk)
        self.assertEqual(created.status, "pending")
        for name, value in data.items():
            self.assertEqual(getattr(created, name), Decimal(value) if name == "quantity" else value)
        self.assertEqual(RequestOffer.objects.count(), 0)
        board = self.client.get(response.headers["Location"])
        self.assertTemplateUsed(board, "request_board/index.html")
        self.assertEqual(board.wsgi_request.method, "GET")
        self.assertContains(board, created.title)
        self.assertContains(board, "Pending")
        feedback = [(message.level, str(message)) for message in board.context["messages"]]
        self.assertEqual(feedback, [(messages.SUCCESS, "Permintaan bantuan makanan berhasil dibuat.")])
        before_refresh = self.snapshot()
        refreshed = self.client.get(self.board_url)
        self.assertEqual(refreshed.status_code, 200)
        self.assertEqual(self.snapshot(), before_refresh)
        self.assertNotContains(refreshed, escape(feedback[0][1]))
        markup = BoardMarkup(board.content.decode())
        markup, notification = self.assert_message_rendered_once(board, markup, feedback[0][1], messages.SUCCESS)
        self.assert_toast_area(markup, notification)

    def test_invalid_form_keeps_field_errors_and_linked_summary_beside_global_toast(self):
        seed, token = self.create_form()
        text = "Notifikasi global uji; periksa isian formulir."
        self.queue_messages(seed, [(messages.ERROR, text)])
        data = {**self.valid_data(), "quantity": "0", "title": '<b>Judul & uji</b>'}
        before = self.snapshot()

        response = self.client.post(self.create_url, {**data, "csrfmiddlewaretoken": token})

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Location", response.headers)
        self.assertEqual(self.snapshot(), before)
        form = response.context_data["form"]
        self.assertTrue(form.is_bound)
        self.assertIn("quantity", form.errors)
        for name, value in data.items():
            self.assertEqual(form[name].value(), value)
        self.assertNotContains(response, data["title"])
        self.assertContains(response, escape(data["title"]))
        markup = BoardMarkup(response.content.decode())
        quantity = next(node for node in markup.select("input")
                        if node["attrs"].get("name") == "quantity")
        self.assertEqual(quantity["attrs"].get("aria-invalid"), "true")
        error_ids = quantity["attrs"].get("aria-describedby", "").split()
        errors = [node for node in markup.nodes if node["attrs"].get("id") in error_ids]
        self.assertTrue(errors)
        links = [node for node in markup.select("a")
                 if node["attrs"].get("href") == "#" + quantity["attrs"]["id"]]
        self.assertTrue(links)
        for error in form["quantity"].errors:
            self.assertTrue(any(str(error) in node["text"] for node in errors))
            self.assertTrue(any(str(error) in node["text"] for node in links))
        markup, feedback = self.assert_message_rendered_once(response, markup, text, messages.ERROR)
        area = self.assert_toast_area(markup, feedback)
        for node in [*errors, *links]:
            self.assertFalse(any(parent is area for parent in node["parents"]))

    def test_missing_and_mismatched_production_tokens_keep_403_without_create_or_success(self):
        for token_case in ("missing", "mismatch"):
            with self.subTest(token_case=token_case):
                _, token = self.create_form()
                data = self.valid_data()
                if token_case == "mismatch":
                    data["csrfmiddlewaretoken"] = ("a" if token[0] != "a" else "b") + token[1:]
                before = self.snapshot()

                response = self.client.post(self.create_url, data)

                self.assertEqual(response.status_code, 403)
                self.assertNotIn("Location", response.headers)
                self.assertEqual(self.snapshot(), before)
                board = self.client.get(self.board_url)
                self.assertEqual(list(board.context["messages"]), [])
                self.assertNotContains(board, "Permintaan bantuan makanan berhasil dibuat.")
class BoardActionRenderingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.owner = users.objects.create_user(username="Pemohon-aksi")
        cls.donor = users.objects.create_user(username="Penawar-aksi")
        cls.second_donor = users.objects.create_user(username="Penawar-kedua")
        cls.board_url = reverse("request_board:request_board")
        cls.pending = Request.objects.create(
            owner=cls.owner, title="Beras kontrol offer", food_item="Beras",
            quantity=2, unit="kg", description="Untuk dapur", location="Sekitar kampus",
        )
        cls.offered = Request.objects.create(
            owner=cls.owner, title="Sayur kontrol offer", food_item="Bayam",
            quantity=3, unit="kg",
        )
        offer_request(actor=cls.donor, request_id=cls.offered.pk)
        cls.fulfilled = Request.objects.create(
            owner=cls.owner, title="Buah kontrol offer", food_item="Jeruk",
            quantity=1, unit="kg",
        )
        offer_request(actor=cls.donor, request_id=cls.fulfilled.pk)
        fulfill_request(actor=cls.owner, request_id=cls.fulfilled.pk)

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)

    def snapshot(self):
        return (list(Request.objects.order_by("pk").values()),
                list(RequestOffer.objects.order_by("pk").values()))

    def board(self):
        before = self.snapshot()
        response = self.client.get(self.board_url)
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response, TemplateResponse)
        self.assertTemplateUsed(response, "request_board/index.html")
        self.assertTrue(response.is_rendered)
        self.assertEqual(self.snapshot(), before)
        return response, BoardMarkup(response.content.decode())

    def card(self, markup, item):
        cards = [node for node in markup.select("article")
                 if any(heading["text"].strip() == item.title
                        for heading in markup.select("h2", within=node))]
        self.assertEqual(len(cards), 1)
        return cards[0]

    def offer_form(self, markup, item):
        card = self.card(markup, item)
        url = reverse("request_board:request_offer", args=[item.pk])
        forms = [node for node in markup.select("form", within=card)
                 if node["attrs"].get("action") == url]
        self.assertEqual(len(forms), 1, "Kartu harus menyediakan form offer produksi")
        form = forms[0]
        self.assertEqual(form["attrs"].get("method", "").lower(), "post")
        tokens = [node for node in markup.select("input", within=form)
                  if node["attrs"].get("name") == "csrfmiddlewaretoken"]
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0]["attrs"].get("type"), "hidden")
        token = tokens[0]["attrs"].get("value", "")
        self.assertTrue(token)
        self.assertIn("csrftoken", self.client.cookies)
        submits = [node for node in markup.select("button", within=form)
                   if node["attrs"].get("type", "submit") == "submit"
                   and node["text"].strip() == "Saya Punya!"]
        self.assertEqual(len(submits), 1)
        for node in [form, submits[0], *form["parents"]]:
            self.assertNotIn("hidden", node["attrs"])
            self.assertNotIn("disabled", node["attrs"])
            self.assertNotEqual(node["attrs"].get("aria-hidden"), "true")
        return form["attrs"]["action"], {"csrfmiddlewaretoken": token}

    def assert_no_action(self, markup, item, actions):
        card = self.card(markup, item)
        urls = {reverse(f"request_board:request_{action}", args=[item.pk])
                for action in actions}
        controls = [node for node in markup.nodes
                    if any(parent is card for parent in node["parents"])
                    and (node["attrs"].get("action") in urls
                         or node["attrs"].get("formaction") in urls
                         or node["attrs"].get("href") in urls)]
        self.assertEqual(controls, [])

    def assert_latest_card(self, markup, item, status, count):
        text = " ".join(self.card(markup, item)["text"].split())
        self.assertIn(status, text)
        self.assertRegex(text.lower(), rf"\b{count}\s+penawaran\b")

    def assert_offer_prg(self, response, level, text):
        self.assertRedirects(response, self.board_url, fetch_redirect_response=False)
        response, markup = self.board()
        stored = list(response.context["messages"])
        self.assertEqual([(entry.level, str(entry)) for entry in stored], [(level, text)])
        # Reuse the toast acceptance on the same parsed tree as the action response.
        _, feedback = ToastNotificationRenderingTests.assert_message_rendered_once(
            self, response, markup, text, level,
        )
        ToastNotificationRenderingTests.assert_toast_area(self, markup, feedback)
        before = self.snapshot()
        refresh, _ = self.board()
        self.assertNotContains(refresh, text)
        self.assertEqual(list(refresh.context["messages"]), [])
        self.assertEqual(self.snapshot(), before)
        return markup

    def test_guest_has_login_link_and_no_mutation_forms_in_all_states(self):
        _, markup = self.board()
        login_url = reverse("login")
        links = [node for node in markup.select("a")
                 if urlsplit(node["attrs"].get("href", "")).path == login_url
                 and node["text"].strip()]
        self.assertTrue(links, "Guest memerlukan link login untuk berpartisipasi")
        for item in (self.pending, self.offered, self.fulfilled):
            with self.subTest(state=item.status, item=item.title):
                self.assertEqual(markup.select("form", within=self.card(markup, item)), [])
                self.assert_no_action(markup, item, ("offer", "fulfill", "delete"))

    def test_owner_never_has_offer_control_in_any_state(self):
        self.client.force_login(self.owner)
        _, markup = self.board()
        for item in (self.pending, self.offered, self.fulfilled):
            with self.subTest(item=item.title):
                self.assert_no_action(markup, item, ("offer",))
                self.assertNotIn("Saya Punya!", self.card(markup, item)["text"])

    def test_nonowner_pending_and_offered_controls_follow_has_offered(self):
        self.client.force_login(self.second_donor)
        for item in (self.pending, self.offered):
            for has_offered in (False, True):
                with self.subTest(item=item.title, has_offered=has_offered):
                    if has_offered:
                        # Exercise the pending/True presentation branch without a fake context.
                        # Normal service flow atomically changes pending to offered.
                        RequestOffer.objects.create(request=item, offered_by=self.second_donor)
                    _, markup = self.board()
                    self.assert_no_action(markup, item, ("fulfill", "delete"))
                    if has_offered:
                        self.assert_no_action(markup, item, ("offer",))
                        self.assertIn("Sudah menawarkan", self.card(markup, item)["text"])
                    else:
                        self.offer_form(markup, item)
                        self.assertNotIn("Sudah menawarkan", self.card(markup, item)["text"])

    def test_fulfilled_has_no_nonowner_mutation_controls_even_for_existing_donor(self):
        for actor in (self.donor, self.second_donor):
            with self.subTest(actor=actor.username):
                self.client.force_login(actor)
                _, markup = self.board()
                self.assert_latest_card(markup, self.fulfilled, "Terpenuhi", 1)
                self.assert_no_action(markup, self.fulfilled, ("offer", "fulfill", "delete"))
                self.assertNotIn("Saya Punya!", self.card(markup, self.fulfilled)["text"])

    def test_first_offer_and_replayed_form_are_prg_success_then_idempotent_info(self):
        self.client.force_login(self.donor)
        _, markup = self.board()
        url, payload = self.offer_form(markup, self.pending)
        unrelated = self.snapshot()[0][1:]

        response = self.client.post(url, payload)

        markup = self.assert_offer_prg(
            response, messages.SUCCESS, "Penawaran bantuan makanan berhasil dikirim.",
        )
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.status, Request.STATUS_OFFERED)
        offers = RequestOffer.objects.filter(request=self.pending)
        self.assertEqual(list(offers.values_list("offered_by_id", flat=True)), [self.donor.pk])
        self.assertEqual(self.snapshot()[0][1:], unrelated)
        self.assert_latest_card(markup, self.pending, "Ditawarkan", 1)
        self.assertIn("Sudah menawarkan", self.card(markup, self.pending)["text"])
        self.assert_no_action(markup, self.pending, ("offer", "fulfill", "delete"))
        before = self.snapshot()

        response = self.client.post(url, payload)

        markup = self.assert_offer_prg(
            response, messages.INFO,
            "Anda sudah mengirimkan penawaran untuk permintaan ini sebelumnya.",
        )
        self.assertEqual(self.snapshot(), before, "Replay tidak boleh mengubah timestamp/data")
        self.assert_latest_card(markup, self.pending, "Ditawarkan", 1)

    def test_second_donor_can_offer_offered_request_without_replacing_first_offer(self):
        self.client.force_login(self.second_donor)
        _, markup = self.board()
        url, payload = self.offer_form(markup, self.offered)
        before = self.snapshot()

        response = self.client.post(url, payload)

        markup = self.assert_offer_prg(
            response, messages.SUCCESS, "Penawaran bantuan makanan berhasil dikirim.",
        )
        self.assertEqual(self.snapshot()[0], before[0])
        self.assertEqual(self.snapshot()[1][:-1], before[1])
        self.assertEqual(set(RequestOffer.objects.filter(request=self.offered).values_list(
            "offered_by_id", flat=True)), {self.donor.pk, self.second_donor.pk})
        self.assert_latest_card(markup, self.offered, "Ditawarkan", 2)
        self.assertIn("Sudah menawarkan", self.card(markup, self.offered)["text"])
        self.assert_no_action(markup, self.offered, ("offer", "fulfill", "delete"))

    def test_stale_offer_form_after_fulfillment_shows_error_toast_without_write(self):
        self.client.force_login(self.second_donor)
        _, markup = self.board()
        url, payload = self.offer_form(markup, self.offered)
        fulfill_request(actor=self.owner, request_id=self.offered.pk)
        before = self.snapshot()

        response = self.client.post(url, payload)

        markup = self.assert_offer_prg(response, messages.ERROR, "Request sudah terpenuhi.")
        self.assertEqual(self.snapshot(), before)
        self.assert_latest_card(markup, self.offered, "Terpenuhi", 1)
        self.assert_no_action(markup, self.offered, ("offer", "fulfill", "delete"))

    def test_missing_and_mismatched_offer_tokens_reject_without_any_data_change(self):
        self.client.force_login(self.second_donor)
        _, markup = self.board()
        url, payload = self.offer_form(markup, self.pending)
        foreign = Client(enforce_csrf_checks=True)
        login = foreign.get(reverse("login"))
        tokens = [attrs["value"] for attrs in LoginMarkup(login.content.decode()).attributes("input")
                  if attrs.get("name") == "csrfmiddlewaretoken"]
        self.assertEqual(len(tokens), 1)
        self.assertNotEqual(tokens[0], payload["csrfmiddlewaretoken"])
        before = self.snapshot()
        for data in ({}, {"csrfmiddlewaretoken": tokens[0]}):
            with self.subTest(data="missing" if not data else "mismatch"):
                response = self.client.post(url, data)
                self.assertEqual(response.status_code, 403)
                self.assertNotIn("Location", response)
                self.assertEqual(self.snapshot(), before)
                board, _ = self.board()
                self.assertEqual(list(board.context["messages"]), [])

    def test_forged_offer_requests_with_valid_production_token_still_enforce_guards(self):
        # Forbidden controls need not exist: obtain a real token from the login HTML.
        for actor, item, expected in (
            (None, self.pending, 302),
            (None, self.offered, 302),
            (None, self.fulfilled, 302),
            (self.owner, self.pending, 403),
            (self.owner, self.offered, 403),
            (self.owner, self.fulfilled, 403),
            (self.donor, self.fulfilled, 302),
            (self.second_donor, self.fulfilled, 302),
            (self.second_donor, None, 404),
        ):
            with self.subTest(actor=actor, item=item):
                self.client = Client(enforce_csrf_checks=True)
                if actor is not None:
                    self.client.force_login(actor)
                login = self.client.get(reverse("login"))
                tokens = [attrs["value"] for attrs in LoginMarkup(login.content.decode()).attributes("input")
                          if attrs.get("name") == "csrfmiddlewaretoken"]
                self.assertEqual(len(tokens), 1)
                url = reverse("request_board:request_offer", args=[item.pk if item else 999999])
                before = self.snapshot()

                response = self.client.post(url, {"csrfmiddlewaretoken": tokens[0]})

                self.assertEqual(response.status_code, expected)
                self.assertEqual(self.snapshot(), before)
                if actor is None:
                    target = urlsplit(response["Location"])
                    self.assertEqual(target.path, reverse("login"))
                    self.assertEqual(parse_qs(target.query), {"next": [url]})
                elif item is self.fulfilled and actor != self.owner:
                    markup = self.assert_offer_prg(response, messages.ERROR, "Request sudah terpenuhi.")
                    self.assert_latest_card(markup, item, "Terpenuhi", 1)
                else:
                    self.assertNotIn("Location", response)
                    board, _ = self.board()
                    self.assertEqual(list(board.context["messages"]), [])
                if actor is not None:
                    get_response = self.client.get(url)
                    self.assertEqual(get_response.status_code, 405)
                    self.assertEqual(get_response["Allow"], "POST")
                    self.assertEqual(self.snapshot(), before)

    def fulfill_form(self, markup, item):
        card = self.card(markup, item)
        url = reverse("request_board:request_fulfill", args=[item.pk])
        forms = [node for node in markup.select("form", within=card)
                 if node["attrs"].get("action") == url]
        self.assertEqual(len(forms), 1, "Kartu owner offered harus menyediakan form fulfill produksi")
        form = forms[0]
        self.assertEqual(form["attrs"].get("method", "").lower(), "post")
        tokens = [node for node in markup.select("input", within=form)
                  if node["attrs"].get("name") == "csrfmiddlewaretoken"]
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0]["attrs"].get("type"), "hidden")
        token = tokens[0]["attrs"].get("value", "")
        self.assertTrue(token)
        self.assertIn("csrftoken", self.client.cookies)
        submits = [node for node in markup.select("button", within=form)
                   if node["attrs"].get("type", "submit") == "submit"
                   and node["text"].strip() == "Tandai Terpenuhi"]
        self.assertEqual(len(submits), 1)
        for node in [form, submits[0], *submits[0]["parents"]]:
            self.assertNotIn("hidden", node["attrs"])
            self.assertNotIn("disabled", node["attrs"])
            self.assertNotEqual(node["attrs"].get("aria-hidden"), "true")
        return form["attrs"]["action"], {"csrfmiddlewaretoken": token}

    def fulfill_direct_token(self, client):
        # Forbidden controls stay absent; login HTML supplies a real same-client token.
        response = client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        tokens = [attrs["value"] for attrs in LoginMarkup(response.content.decode()).attributes("input")
                  if attrs.get("name") == "csrfmiddlewaretoken"]
        self.assertEqual(len(tokens), 1)
        self.assertIn("csrftoken", client.cookies)
        return tokens[0]

    def test_fulfill_control_is_only_available_to_owner_on_offered_request(self):
        for actor in (None, self.donor, self.second_donor, self.owner):
            self.client = Client(enforce_csrf_checks=True)
            if actor is not None:
                self.client.force_login(actor)
            _, markup = self.board()
            for item in (self.pending, self.fulfilled, self.offered):
                with self.subTest(actor=actor, item=item.title):
                    if actor == self.owner and item is self.offered:
                        self.fulfill_form(markup, item)
                    else:
                        self.assert_no_action(markup, item, ("fulfill",))
                        self.assertNotIn("Tandai Terpenuhi", self.card(markup, item)["text"])

    def test_fulfill_post_and_replayed_production_form_preserve_offers_and_show_latest_card(self):
        offer_request(actor=self.second_donor, request_id=self.offered.pk)
        self.client.force_login(self.owner)
        _, markup = self.board()
        self.assert_latest_card(markup, self.offered, "Ditawarkan", 2)
        url, payload = self.fulfill_form(markup, self.offered)
        before = self.snapshot()

        response = self.client.post(url, payload)

        self.assertEqual(response.status_code, 302)
        self.offered.refresh_from_db()
        self.assertEqual(self.offered.status, Request.STATUS_FULFILLED)
        self.assertGreater(self.offered.updated_at, next(
            row["updated_at"] for row in before[0] if row["id"] == self.offered.pk))
        after = self.snapshot()
        self.assertEqual(after[1], before[1])
        for old, new in zip(before[0], after[0]):
            if old["id"] == self.offered.pk:
                self.assertEqual({key: value for key, value in new.items()
                                  if key not in {"status", "updated_at"}},
                                 {key: value for key, value in old.items()
                                  if key not in {"status", "updated_at"}})
            else:
                self.assertEqual(new, old)
        self.assertEqual(len(after[0]), len(before[0]))
        # The existing PRG helper checks Board GET, real toast semantics and consumption.
        markup = self.assert_offer_prg(
            response, messages.SUCCESS, "Permintaan makanan berhasil diselesaikan.",
        )
        self.assert_latest_card(markup, self.offered, "Terpenuhi", 2)
        self.assert_no_action(markup, self.offered, ("fulfill",))
        before_replay = self.snapshot()

        response = self.client.post(url, payload)

        self.assertEqual(self.snapshot(), before_replay, "Replay tidak boleh mengubah timestamp/data")
        markup = self.assert_offer_prg(
            response, messages.SUCCESS, "Permintaan makanan berhasil diselesaikan.",
        )
        self.assert_latest_card(markup, self.offered, "Terpenuhi", 2)
        self.assert_no_action(markup, self.offered, ("fulfill",))

    def test_missing_and_mismatched_fulfill_form_tokens_reject_without_writes_or_messages(self):
        self.client.force_login(self.owner)
        _, markup = self.board()
        url, payload = self.fulfill_form(markup, self.offered)
        foreign_token = self.fulfill_direct_token(Client(enforce_csrf_checks=True))
        self.assertNotEqual(foreign_token, payload["csrfmiddlewaretoken"])
        before = self.snapshot()

        for data in ({}, {"csrfmiddlewaretoken": foreign_token}):
            with self.subTest(token="missing" if not data else "mismatch"):
                response = self.client.post(url, data)

                self.assertEqual(response.status_code, 403)
                self.assertNotIn("Location", response)
                self.assertEqual(self.snapshot(), before)
                board, markup = self.board()
                self.assertEqual(list(board.context["messages"]), [])
                self.assert_latest_card(markup, self.offered, "Ditawarkan", 1)
                self.fulfill_form(markup, self.offered)

    def test_forged_fulfill_valid_token_enforces_actor_state_and_missing_target_guards(self):
        cases = [(actor, item, 302 if actor is None else 403)
                 for actor in (None, self.donor, self.second_donor)
                 for item in (self.pending, self.offered, self.fulfilled)]
        cases += [(self.owner, self.pending, 302),
                  (self.owner, None, 404), (self.second_donor, None, 404)]
        for actor, item, expected in cases:
            with self.subTest(actor=actor, item=item):
                self.client = Client(enforce_csrf_checks=True)
                if actor is not None:
                    self.client.force_login(actor)
                token = self.fulfill_direct_token(self.client)
                url = reverse("request_board:request_fulfill", args=[item.pk if item else 999999])
                before = self.snapshot()

                response = self.client.post(url, {"csrfmiddlewaretoken": token})

                self.assertEqual(response.status_code, expected)
                self.assertEqual(self.snapshot(), before)
                if actor is None:
                    target = urlsplit(response["Location"])
                    self.assertEqual(target.path, reverse("login"))
                    self.assertEqual(parse_qs(target.query), {"next": [url]})
                elif actor == self.owner and item is self.pending:
                    markup = self.assert_offer_prg(response, messages.ERROR, "Request belum ditawarkan.")
                    self.assert_latest_card(markup, item, "Pending", 0)
                    self.assert_no_action(markup, item, ("fulfill",))
                else:
                    self.assertNotIn("Location", response)
                    board, _ = self.board()
                    self.assertEqual(list(board.context["messages"]), [])
                self.assertEqual(self.snapshot(), before)
                if actor is not None:
                    get_response = self.client.get(url)
                    self.assertEqual(get_response.status_code, 405)
                    self.assertEqual(get_response["Allow"], "POST")
                    self.assertEqual(self.snapshot(), before)

    def test_forged_fulfill_missing_and_foreign_tokens_never_reach_mutation(self):
        foreign_token = self.fulfill_direct_token(self.client)
        for actor in (None, self.owner, self.donor, self.second_donor):
            self.client = Client(enforce_csrf_checks=True)
            if actor is not None:
                self.client.force_login(actor)
            token = self.fulfill_direct_token(self.client)
            self.assertNotEqual(token, foreign_token)
            for item in (self.pending, self.offered, self.fulfilled, None):
                url = reverse("request_board:request_fulfill", args=[item.pk if item else 999999])
                for data in ({}, {"csrfmiddlewaretoken": foreign_token}):
                    with self.subTest(actor=actor, item=item, token="missing" if not data else "mismatch"):
                        before = self.snapshot()

                        response = self.client.post(url, data)

                        self.assertEqual(response.status_code, 403)
                        self.assertNotIn("Location", response)
                        self.assertEqual(self.snapshot(), before)
                        board, _ = self.board()
                        self.assertEqual(list(board.context["messages"]), [])

    def test_owner_direct_fulfill_reconfirmation_is_success_without_timestamp_or_data_change(self):
        self.client.force_login(self.owner)
        token = self.fulfill_direct_token(self.client)
        url = reverse("request_board:request_fulfill", args=[self.fulfilled.pk])
        before = self.snapshot()

        for attempt in range(2):
            with self.subTest(attempt=attempt):
                response = self.client.post(url, {"csrfmiddlewaretoken": token})

                self.assertEqual(self.snapshot(), before)
                markup = self.assert_offer_prg(
                    response, messages.SUCCESS, "Permintaan makanan berhasil diselesaikan.",
                )
                self.assert_latest_card(markup, self.fulfilled, "Terpenuhi", 1)
                self.assert_no_action(markup, self.fulfilled, ("fulfill",))
                self.assertEqual(self.snapshot(), before)
class BoardDeleteConfirmationTests(TestCase):
    """Delete markup/HTTP/data only; disclosure interaction belongs to T3-05."""

    board = BoardActionRenderingTests.board
    card = BoardActionRenderingTests.card
    assert_no_action = BoardActionRenderingTests.assert_no_action
    assert_delete_prg = BoardActionRenderingTests.assert_offer_prg
    direct_token = BoardActionRenderingTests.fulfill_direct_token

    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.owner = users.objects.create_user(username="Pemohon-delete")
        cls.donor = users.objects.create_user(username="Donor-delete")
        cls.other_owner = users.objects.create_user(username="Pemohon-lain-delete")
        cls.board_url = reverse("request_board:request_board")
        cls.items = []
        for state in (Request.STATUS_PENDING, Request.STATUS_OFFERED, Request.STATUS_FULFILLED):
            item = Request.objects.create(
                owner=cls.owner, title=f"Target hapus {state} <b>jelas</b>",
                food_item="Beras", quantity=2, unit="kg",
            )
            if state != Request.STATUS_PENDING:
                offer_request(actor=cls.donor, request_id=item.pk)
                offer_request(actor=cls.other_owner, request_id=item.pk)
            if state == Request.STATUS_FULFILLED:
                fulfill_request(actor=cls.owner, request_id=item.pk)
            item.refresh_from_db()
            cls.items.append(item)
        cls.unrelated = Request.objects.create(
            owner=cls.other_owner, title="Posting lain tetap ada", food_item="Sayur",
            quantity=1, unit="kg",
        )
        offer_request(actor=cls.donor, request_id=cls.unrelated.pk)

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)

    def snapshot(self):
        return (list(Request.objects.order_by("pk").values()),
                list(RequestOffer.objects.order_by("pk").values()),
                list(get_user_model().objects.order_by("pk").values()))

    def confirmation(self, markup, item):
        card = self.card(markup, item)
        disclosures = markup.select("details", within=card)
        self.assertEqual(len(disclosures), 1, "Owner memerlukan konfirmasi delete no-JS details/summary")
        details = disclosures[0]
        self.assertNotIn("open", details["attrs"])
        summaries = markup.select("summary", within=details)
        self.assertEqual(len(summaries), 1)
        self.assertIs(summaries[0]["parents"][-1], details)
        self.assertIn("hapus", summaries[0]["text"].lower())
        warnings = [node for tag in ("p", "strong") for node in markup.select(tag, within=details)
                    if not any(parent is summaries[0] for parent in node["parents"])
                    and item.title in node["text"]
                    and "hapus" in node["text"].lower()]
        self.assertTrue(warnings, "Peringatan harus menyebut judul target dan penghapusan")
        self.assertRegex(" ".join(details["text"].lower().split()),
                         r"tidak (?:dapat|bisa) (?:dibatalkan|dikembalikan|dipulihkan)")
        url = reverse("request_board:request_delete", args=[item.pk])
        forms = markup.select("form", within=details)
        self.assertEqual(len(forms), 1)
        form = forms[0]
        self.assertEqual(form["attrs"].get("action"), url)
        self.assertEqual(form["attrs"].get("method", "").lower(), "post")
        self.assertEqual([node for node in markup.select("form", within=card)
                          if node["attrs"].get("action") == url], [form])
        self.assertFalse(any(parent is summaries[0] for parent in form["parents"]))
        tokens = [node for node in markup.select("input", within=form)
                  if node["attrs"].get("name") == "csrfmiddlewaretoken"]
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0]["attrs"].get("type"), "hidden")
        token = tokens[0]["attrs"].get("value", "")
        self.assertTrue(token)
        self.assertIn("csrftoken", self.client.cookies)
        submits = [node for node in markup.select("button", within=form)
                   if node["attrs"].get("type", "submit") == "submit"
                   and "hapus" in node["text"].lower()]
        self.assertEqual(len(submits), 1)
        self.assertFalse(any(parent is summaries[0] for parent in submits[0]["parents"]))
        cancel = [node for node in markup.select("a", within=details)
                  if node["text"].strip() == "Batal"]
        self.assertEqual(len(cancel), 1)
        self.assertEqual(cancel[0]["attrs"].get("href"), self.board_url)
        for node in (summaries[0], form, submits[0], cancel[0]):
            for ancestor in (node, *node["parents"]):
                self.assertNotIn("hidden", ancestor["attrs"])
                self.assertNotIn("disabled", ancestor["attrs"])
                self.assertNotEqual(ancestor["attrs"].get("aria-hidden"), "true")
            self.assertFalse(any(key.startswith("on") for key in node["attrs"]))
        self.assertFalse(any(node["attrs"].get("href") == url for node in markup.select("a", within=card)))
        return url, {"csrfmiddlewaretoken": token}, cancel[0]["attrs"]["href"]

    def assert_deleted_only(self, response, before, item):
        expected = ([row for row in before[0] if row["id"] != item.pk],
                    [row for row in before[1] if row["request_id"] != item.pk], before[2])
        self.assertEqual(self.snapshot(), expected)
        markup = self.assert_delete_prg(
            response, messages.SUCCESS, "Permintaan makanan berhasil dihapus.",
        )
        self.assertFalse(any(node["text"].strip() == item.title for node in markup.select("h2")))
        self.card(markup, self.unrelated)
        self.assertEqual(self.snapshot(), expected)

    def test_owner_has_closed_confirmation_for_each_state_with_escaped_target(self):
        self.client.force_login(self.owner)
        response, markup = self.board()
        for item in self.items:
            with self.subTest(state=item.status):
                self.confirmation(markup, item)
                self.assertContains(response, escape(item.title))
                self.assertNotContains(response, item.title)

    def test_guest_and_nonowners_have_no_delete_controls_or_disclosures(self):
        for actor in (None, self.donor, self.other_owner):
            self.client = Client(enforce_csrf_checks=True)
            if actor is not None:
                self.client.force_login(actor)
            _, markup = self.board()
            for item in self.items:
                with self.subTest(actor=actor, state=item.status):
                    self.assert_no_action(markup, item, ("delete",))
                    card = self.card(markup, item)
                    self.assertEqual(markup.select("details", within=card), [])
                    self.assertFalse(any("hapus" in node["text"].lower()
                                         for tag in ("a", "button", "summary")
                                         for node in markup.select(tag, within=card)))

    def test_cancel_link_get_preserves_all_records_and_confirmation_remains(self):
        self.client.force_login(self.owner)
        for item in self.items:
            with self.subTest(state=item.status):
                _, markup = self.board()
                _, _, cancel_url = self.confirmation(markup, item)
                before = self.snapshot()
                response = self.client.get(cancel_url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(list(response.context["messages"]), [])
                self.confirmation(BoardMarkup(response.content.decode()), item)

    def test_confirm_production_post_cascades_only_target_then_toast_and_board_absence(self):
        self.client.force_login(self.owner)
        for item in self.items:
            with self.subTest(state=item.status):
                _, markup = self.board()
                url, payload, _ = self.confirmation(markup, item)
                before = self.snapshot()
                response = self.client.post(url, payload)
                self.assert_deleted_only(response, before, item)

    def test_missing_and_foreign_confirmation_tokens_reject_without_writes_or_toast(self):
        self.client.force_login(self.owner)
        foreign = self.direct_token(Client(enforce_csrf_checks=True))
        for item in self.items:
            with self.subTest(state=item.status):
                _, markup = self.board()
                url, payload, _ = self.confirmation(markup, item)
                self.assertNotEqual(payload["csrfmiddlewaretoken"], foreign)
                for data in ({}, {"csrfmiddlewaretoken": foreign}):
                    with self.subTest(token="missing" if not data else "mismatch"):
                        before = self.snapshot()
                        response = self.client.post(url, data)
                        self.assertEqual(response.status_code, 403)
                        self.assertNotIn("Location", response)
                        self.assertEqual(self.snapshot(), before)
                        board, markup = self.board()
                        self.assertEqual(list(board.context["messages"]), [])
                        self.confirmation(markup, item)

    def test_forged_valid_token_rejects_guest_nonowner_and_missing_target(self):
        for actor in (None, self.donor, self.other_owner, self.owner):
            self.client = Client(enforce_csrf_checks=True)
            if actor is not None:
                self.client.force_login(actor)
            token = self.direct_token(self.client)
            targets = [None] if actor == self.owner else [*self.items, None]
            for item in targets:
                with self.subTest(actor=actor, item=item):
                    url = reverse("request_board:request_delete", args=[item.pk if item else 999999])
                    before = self.snapshot()
                    response = self.client.post(url, {"csrfmiddlewaretoken": token})
                    self.assertEqual(response.status_code, 302 if actor is None else 403 if item else 404)
                    self.assertEqual(self.snapshot(), before)
                    if actor is None:
                        target = urlsplit(response["Location"])
                        self.assertEqual(target.path, reverse("login"))
                        self.assertEqual(parse_qs(target.query), {"next": [url]})
                    else:
                        self.assertNotIn("Location", response)
                    board, _ = self.board()
                    self.assertEqual(list(board.context["messages"]), [])

    def test_forged_missing_and_foreign_tokens_reject_all_actors_states_and_missing_id(self):
        foreign = self.direct_token(Client(enforce_csrf_checks=True))
        for actor in (None, self.owner, self.donor, self.other_owner):
            self.client = Client(enforce_csrf_checks=True)
            if actor is not None:
                self.client.force_login(actor)
            self.assertNotEqual(self.direct_token(self.client), foreign)
            for item in (*self.items, None):
                url = reverse("request_board:request_delete", args=[item.pk if item else 999999])
                for data in ({}, {"csrfmiddlewaretoken": foreign}):
                    with self.subTest(actor=actor, item=item, token="missing" if not data else "mismatch"):
                        before = self.snapshot()
                        response = self.client.post(url, data)
                        self.assertEqual(response.status_code, 403)
                        self.assertNotIn("Location", response)
                        self.assertEqual(self.snapshot(), before)
                        board, _ = self.board()
                        self.assertEqual(list(board.context["messages"]), [])

    def test_direct_get_never_deletes_or_opens_a_get_delete_endpoint(self):
        for actor in (None, self.owner, self.donor, self.other_owner):
            self.client = Client(enforce_csrf_checks=True)
            if actor is not None:
                self.client.force_login(actor)
            for item in (*self.items, None):
                with self.subTest(actor=actor, item=item):
                    url = reverse("request_board:request_delete", args=[item.pk if item else 999999])
                    before = self.snapshot()
                    response = self.client.get(url)
                    self.assertEqual(response.status_code, 302 if actor is None else 405)
                    if actor is not None:
                        self.assertEqual(response["Allow"], "POST")
                    self.assertEqual(self.snapshot(), before)

    def test_owner_direct_valid_token_deletes_all_states_and_replay_is_404(self):
        self.client.force_login(self.owner)
        token = self.direct_token(self.client)
        for item in self.items:
            with self.subTest(state=item.status):
                url = reverse("request_board:request_delete", args=[item.pk])
                before = self.snapshot()
                response = self.client.post(url, {"csrfmiddlewaretoken": token})
                self.assert_deleted_only(response, before, item)
                remaining = self.snapshot()
                response = self.client.post(url, {"csrfmiddlewaretoken": token})
                self.assertEqual(response.status_code, 404)
                self.assertNotIn("Location", response)
                self.assertEqual(self.snapshot(), remaining)
                board, _ = self.board()
                self.assertEqual(list(board.context["messages"]), [])


class OwnerOfferPrivacyRenderingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.owner = users.objects.create_user(username="Publik-owner-A")
        cls.other_owner = users.objects.create_user(username="Publik-owner-B")
        cls.reader = users.objects.create_user(username="Pembaca-tanpa-posting")
        cls.donors = [users.objects.create_user(
            username=f"DONOR_SENTINEL_{index:02d}_END",
            email=f"private-donor-{index:02d}@example.invalid",
            first_name=f"CONTACT_PRIVATE_{index:02d}_END",
            last_name=f"PHONE_PRIVATE_08170000{index:02d}",
        ) for index in range(24)]
        cls.dangerous_name = '<img src=x onerror="alert(1)">DONOR_SENTINEL_XSS_END&\'"'
        cls.donors[21].username = cls.dangerous_name
        cls.donors[21].save(update_fields=["username"])
        cls.board_url = reverse("request_board:request_board")
        cls.fixed_time = datetime(2026, 10, 23, 9, tzinfo=timezone.utc)
        cls.items = []
        cls.offers = {}
        for title, owner, donors in (
            ("Privasi A pertama", cls.owner, cls.donors[:21]),
            ("Privasi A kedua", cls.owner, list(reversed(cls.donors[:22]))),
            ("Privasi B terpisah", cls.other_owner, cls.donors[22:]),
        ):
            item = Request.objects.create(
                owner=owner, title=title, food_item="Beras", quantity=2,
                unit="kg", status=Request.STATUS_OFFERED,
            )
            cls.items.append(item)
            cls.offers[item.pk] = []
            for index, donor in enumerate(donors):
                offer = RequestOffer.objects.create(request=item, offered_by=donor)
                # Reverse chronological groups disagree with insertion order; ties
                # also straddle the preview cutoff, so both ordering keys matter.
                timestamp = cls.fixed_time - timedelta(minutes=index // 3)
                RequestOffer.objects.filter(pk=offer.pk).update(created_at=timestamp)
                cls.offers[item.pk].append((timestamp, offer.pk, donor.username))

    def board(self, client):
        response = client.get(self.board_url)
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response, TemplateResponse)
        self.assertTrue(response.is_rendered)
        self.assertTemplateUsed(response, "request_board/index.html")
        self.assertTemplateUsed(response, "request_board/_request_card.html")
        self.assertTemplateUsed(response, "base.html")
        return response, BoardMarkup(response.content.decode())

    def card(self, markup, item):
        cards = [node for node in markup.select("article")
                 if any(heading["text"].strip() == item.title
                        for heading in markup.select("h2", within=node))]
        self.assertEqual(len(cards), 1)
        return cards[0]

    def expected(self, item):
        return sorted(self.offers[item.pk], reverse=True)[:20]

    def assert_private_fields_absent(self, response):
        for donor in self.donors:
            for value in (donor.email, donor.first_name, donor.last_name):
                self.assertNotIn(value.encode(), response.content)

    def test_guest_and_nonowner_have_no_donor_identity_anywhere_in_response(self):
        for actor in (None, self.reader, self.donors[0]):
            with self.subTest(actor=getattr(actor, "username", "guest")):
                client = Client(enforce_csrf_checks=True)
                if actor is not None:
                    client.force_login(actor)
                response, markup = self.board(client)
                self.assertEqual(response.context_data["owner_offer_previews"], {})
                # Inspect all bytes: hidden DOM, attributes, comments, scripts and
                # embedded JSON are not filtered by a visible-text parser.
                self.assertNotIn(b"DONOR_SENTINEL_", response.content)
                self.assert_private_fields_absent(response)
                for item in self.items:
                    text = self.card(markup, item)["text"]
                    self.assertIn(item.owner.username, text)
                    self.assertIn(f"{len(self.offers[item.pk])} penawaran", text)

    def test_each_owner_response_excludes_other_owners_donor_sentinels(self):
        for actor, forbidden in ((self.owner, self.donors[22:]),
                                 (self.other_owner, self.donors[:22])):
            with self.subTest(actor=actor.username):
                client = Client(enforce_csrf_checks=True)
                client.force_login(actor)
                response, _ = self.board(client)
                owned_ids = {item.pk for item in self.items if item.owner_id == actor.pk}
                self.assertEqual(set(response.context_data["owner_offer_previews"]), owned_ids)
                for donor in forbidden:
                    sentinel = re.search(r"DONOR_SENTINEL_\w+", donor.username).group()
                    self.assertNotIn(sentinel.encode(), response.content)
                self.assert_private_fields_absent(response)

    def test_owner_sees_latest_twenty_in_each_owned_card_with_id_tie_break(self):
        for actor in (self.owner, self.other_owner):
            self.client.force_login(actor)
            _, markup = self.board(self.client)
            for item in self.items:
                if item.owner_id != actor.pk:
                    continue
                with self.subTest(item=item.title):
                    text = self.card(markup, item)["text"]
                    actual = re.findall(r"DONOR_SENTINEL_(?:\d{2}|XSS)_END", text)
                    expected = [re.search(r"DONOR_SENTINEL_\w+", name).group()
                                for _, _, name in self.expected(item)]
                    self.assertEqual(actual, expected,
                                     "Preview produksi harus 20 donor terbaru per kartu, -created_at/-id")
                    omitted = set(name for _, _, name in self.offers[item.pk]) - set(
                        name for _, _, name in self.expected(item))
                    for name in omitted:
                        self.assertNotIn(name, text)

    def test_preview_times_belong_to_each_rendered_offer(self):
        self.client.force_login(self.owner)
        _, markup = self.board(self.client)
        for item in self.items[:2]:
            with self.subTest(item=item.title):
                card = self.card(markup, item)
                # The card already has its posting time; preview must add one
                # semantic timestamp per displayed donor, in preview order.
                times = markup.select("time", within=card)
                self.assertEqual(len(times), 21)
                self.assertEqual([parse_datetime(node["attrs"].get("datetime", ""))
                      for node in times if "request-board__donor-time" in node["attrs"].get("class", "").split()],
                                 [timestamp for timestamp, _, _ in self.expected(item)])
                self.assertTrue(all(node["text"].strip() for node in times))

    def test_preview_context_matches_cap_total_and_has_more_at_boundaries(self):
        self.client.force_login(self.owner)
        item = self.items[0]
        for count in (0, 1, 19, 20, 21, 22):
            RequestOffer.objects.filter(request=item).delete()
            RequestOffer.objects.bulk_create([
                RequestOffer(request=item, offered_by=donor) for donor in self.donors[:count]
            ])
            RequestOffer.objects.filter(request=item).update(created_at=self.fixed_time)
            response, _ = self.board(self.client)
            with self.subTest(count=count):
                preview = response.context_data["owner_offer_previews"][item.pk]
                self.assertEqual(preview["total"], count)
                self.assertIs(preview["has_more"], count > 20)
                self.assertEqual(preview["items"], [
                    {"display_name": self.donors[index].username, "created_at": self.fixed_time}
                    for index in reversed(range(max(0, count - 20), count))
                ])

    def test_boundary_counts_and_more_indicator_are_rendered_on_target_card(self):
        self.client.force_login(self.owner)
        item = self.items[0]
        for count in (0, 1, 19, 20, 21, 22):
            RequestOffer.objects.filter(request=item).delete()
            RequestOffer.objects.bulk_create([
                RequestOffer(request=item, offered_by=donor) for donor in self.donors[:count]
            ])
            _, markup = self.board(self.client)
            text = self.card(markup, item)["text"]
            with self.subTest(count=count, assertion="total"):
                self.assertRegex(text, rf"\b{count}\s+penawaran\b")
            with self.subTest(count=count, assertion="preview-size"):
                self.assertEqual(len(re.findall(r"DONOR_SENTINEL_(?:\d{2}|XSS)_END", text)),
                                 min(count, 20))
            with self.subTest(count=count, assertion="has-more"):
                more = re.search(r"lainnya|lebih banyak|selengkapnya", text, re.I)
                self.assertEqual(bool(more), count > 20,
                                 "has_more harus punya penanda Indonesia terbaca hanya bila total > 20")

    def test_dangerous_donor_name_is_escaped_and_visible_only_in_owners_preview(self):
        item = self.items[1]
        dangerous_offer = next(pk for _, pk, name in self.offers[item.pk]
                               if name == self.dangerous_name)
        RequestOffer.objects.filter(pk=dangerous_offer).update(
            created_at=self.fixed_time + timedelta(days=1))
        self.client.force_login(self.owner)
        response, markup = self.board(self.client)
        self.assertNotIn(self.dangerous_name.encode(), response.content)
        self.assert_private_fields_absent(response)
        with self.subTest(assertion="escaped-response"):
            self.assertIn(str(escape(self.dangerous_name)).encode(), response.content)
        with self.subTest(assertion="visible-owner-card"):
            self.assertIn(self.dangerous_name, self.card(markup, item)["text"])
        self.assertFalse(any("onerror" in node["attrs"] for node in markup.nodes))
        for node in markup.nodes:
            self.assertFalse(any("DONOR_SENTINEL_XSS_END" in value
                                 for value in node["attrs"].values() if value))

    def test_email_and_private_contact_never_appear_even_for_owner(self):
        for actor in (None, self.reader, self.owner, self.other_owner):
            with self.subTest(actor=getattr(actor, "username", "guest")):
                client = Client(enforce_csrf_checks=True)
                if actor is not None:
                    client.force_login(actor)
                response, _ = self.board(client)
                self.assert_private_fields_absent(response)

    def test_production_html_queries_stay_constant_from_one_to_twenty_cards(self):
        Request.objects.all().delete()
        clients = {"guest": Client(enforce_csrf_checks=True),
                   "nonowner": Client(enforce_csrf_checks=True),
                   "owner": Client(enforce_csrf_checks=True)}
        clients["nonowner"].force_login(self.reader)
        clients["owner"].force_login(self.owner)
        profiles = {}
        for count in (1, 20):
            for index in range(Request.objects.count(), count):
                item = Request.objects.create(
                    owner=self.owner, title=f"Query privasi {index}", food_item="Beras",
                    quantity=1, unit="kg", status=Request.STATUS_OFFERED,
                )
                RequestOffer.objects.create(request=item, offered_by=self.donors[0])
            for actor, client in clients.items():
                self.board(client)  # Warm template/session infrastructure outside capture.
                with CaptureQueriesContext(connection) as captured:
                    response = client.get(self.board_url)
                    content = response.content
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.is_rendered)
                self.assertTemplateUsed(response, "request_board/_request_card.html")
                self.assertEqual(len(BoardMarkup(content.decode()).select("article")), count)
                auth, builder = [], []
                for query in captured:
                    sql = query["sql"]
                    if '"django_session"' in sql or (
                        '"auth_user"' in sql and '"requests"' not in sql
                        and '"request_offers"' not in sql
                    ):
                        auth.append(sql)
                    else:
                        builder.append(sql)
                profiles[(actor, count)] = (len(auth), len(builder))
                print(f"PRIVACY_QUERY actor={actor} cards={count} total={len(captured)} "
                      f"auth_session={len(auth)} builder_render={len(builder)}")
                self.assertEqual(len(auth), 0 if actor == "guest" else 2)
                self.assertEqual(len(builder), 3 if actor == "owner" else 2)
        for actor in clients:
            with self.subTest(actor=actor):
                self.assertEqual(profiles[(actor, 1)], profiles[(actor, 20)])


class BoardIndonesianErrorRenderingTests(TestCase):
    """D02: localized errors from real forms, routes and CSRF-protected POSTs."""

    @classmethod
    def setUpTestData(cls):
        cls.password = "Synthetic-D02-password-847!"
        cls.actor = get_user_model().objects.create_user(
            username="d02-owner", password=cls.password,
        )
        cls.inactive = get_user_model().objects.create_user(
            username="d02-inactive", password=cls.password, is_active=False,
        )
        cls.login_url = reverse("login")
        cls.create_url = reverse("request_board:request_create")

    def csrf_token(self, client, url):
        response = client.get(url)
        self.assertEqual(response.status_code, 200)
        return next(attrs["value"] for attrs in LoginMarkup(response.content.decode()).attributes("input")
                    if attrs.get("name") == "csrfmiddlewaretoken")

    def test_create_errors_render_indonesian_messages_with_original_validation_codes(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.actor)
        token = self.csrf_token(client, self.create_url)
        valid = {"title": "Beras warga", "food_item": "Beras", "quantity": "2.50", "unit": "kg"}
        required = "Kolom ini wajib diisi."
        cases = [
            ({}, {name: ("required", required) for name in valid}),
            ({**valid, "quantity": "abc"}, {"quantity": ("invalid", "Masukkan nilai yang valid.")}),
            ({**valid, "quantity": "0"}, {"quantity": ("min_value", "Pastikan nilai minimal 0.01.")}),
            ({**valid, "quantity": "1234567.89"}, {"quantity": ("max_digits", "Gunakan paling banyak 8 digit.")}),
            ({**valid, "quantity": "1.234"}, {"quantity": ("max_decimal_places", "Gunakan paling banyak 2 angka di belakang koma.")}),
            ({**valid, "quantity": "1234567"}, {"quantity": ("max_whole_digits", "Gunakan paling banyak 6 digit sebelum koma.")}),
            ({**valid, "unit": "invalid"}, {"unit": ("invalid_choice", "Pilih salah satu pilihan yang tersedia.")}),
            ({**valid, "title": "a\x00b"}, {"title": ("null_characters_not_allowed", "Karakter null tidak diperbolehkan.")}),
        ]
        for name, limit in [("title", 120), ("food_item", 120), ("location", 120), ("description", 1000)]:
            cases.append(({**valid, name: "x" * (limit + 1)},
                          {name: ("max_length", f"Gunakan paling banyak {limit} karakter.")}))
        before = list(Request.objects.values()), list(RequestOffer.objects.values())

        for data, expected in cases:
            with self.subTest(fields=expected):
                response = client.post(self.create_url, {**data, "csrfmiddlewaretoken": token})
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "request_board/request_form.html")
                form = response.context["form"]
                self.assertEqual(set(form.errors), set(expected))
                for name, (code, message) in expected.items():
                    self.assertEqual([error.code for error in form.errors.as_data()[name]], [code])
                    self.assertEqual(list(form.errors[name]), [message])
                    # Both the linked summary and inline field error must render the backend message.
                    self.assertContains(response, escape(message), count=2 * len(expected) if data == {} else 2)
                self.assertEqual((list(Request.objects.values()), list(RequestOffer.objects.values())), before)

    def test_login_required_and_null_character_errors_are_indonesian_without_session(self):
        client = Client(enforce_csrf_checks=True)
        token = self.csrf_token(client, self.login_url)
        for data, names, message in [
            ({}, ["username", "password"], "Kolom ini wajib diisi."),
            ({"username": "a\x00b", "password": self.password}, ["username"], "Karakter null tidak diperbolehkan."),
        ]:
            with self.subTest(fields=names):
                response = client.post(self.login_url, {**data, "csrfmiddlewaretoken": token})
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "login.html")
                form = response.context_data["form"]
                self.assertEqual(set(form.errors), set(names))
                for name in names:
                    self.assertEqual(list(form.errors[name]), [message])
                self.assertContains(response, message, count=len(names))
                self.assertNotIn(SESSION_KEY, client.session)

    def test_login_rejections_share_indonesian_message_and_preserve_safe_next(self):
        message = "Nama pengguna atau kata sandi tidak benar. Periksa kembali keduanya."
        for username, password in [(self.actor.username, "wrong"), ("unknown-d02", self.password),
                                   (self.inactive.username, self.password)]:
            with self.subTest(username=username):
                client = Client(enforce_csrf_checks=True)
                token = self.csrf_token(client, self.login_url)
                response = client.post(self.login_url, {
                    "username": username, "password": password, "next": self.create_url,
                    "csrfmiddlewaretoken": token,
                })
                self.assertEqual(response.status_code, 200)
                form = response.context_data["form"]
                self.assertEqual(list(form.non_field_errors()), [message])
                self.assertEqual(form.errors.as_data()["__all__"][0].code, "invalid_login")
                self.assertEqual(response.context_data["next"], self.create_url)
                self.assertContains(response, message)
                self.assertNotContains(response, password)
                self.assertNotIn(SESSION_KEY, client.session)

    def test_inactive_guard_is_localized_without_changing_default_auth_or_language(self):
        client = Client(enforce_csrf_checks=True)
        original_language = get_language()
        with override("en-us"):
            response = client.get(self.login_url)
            form = response.context_data["form"]
            with self.assertRaises(ValidationError) as caught:
                form.confirm_login_allowed(self.inactive)
            self.assertEqual(caught.exception.code, "inactive")
            self.assertEqual(caught.exception.messages, ["Akun ini tidak aktif."])
            self.assertIsNone(form.confirm_login_allowed(self.actor))
            default = AuthenticationForm(data={})
            self.assertFalse(default.is_valid())
            self.assertEqual(list(default.errors["username"]), ["This field is required."])
            self.assertEqual(default.error_messages["inactive"], "This account is inactive.")
            model_field = Request._meta.get_field("title").formfield()
            self.assertEqual(model_field.error_messages["required"], "This field is required.")
            self.assertEqual(get_language(), "en-us")
        self.assertEqual(get_language(), original_language)
