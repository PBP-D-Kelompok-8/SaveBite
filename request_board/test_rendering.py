from html.parser import HTMLParser
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import re
from urllib.parse import parse_qs, urlencode, urlsplit

from django.contrib.auth import SESSION_KEY, get_user_model
from django.contrib import messages
from django.template.response import TemplateResponse
from django.test import Client, TestCase
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.utils.html import escape, strip_tags

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
        cls.login_url = reverse("request_board:login")
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
        self.assertTemplateUsed(response, "request_board/login.html")
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
        self.assertTrue(any(attrs.get("href") == self.board_url
                            for attrs in markup.attributes("a")))
        self.assertEqual(len(markup.attributes("main")), 1)
        self.assertTrue(any("request-board" in attrs.get("class", "").split()
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
        self.assertTemplateUsed(response, "request_board/login.html")
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
        cls.login_url = reverse("request_board:login")

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
        login_url = reverse("request_board:login")
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
        login = foreign.get(reverse("request_board:login"))
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
                login = self.client.get(reverse("request_board:login"))
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
                    self.assertEqual(target.path, reverse("request_board:login"))
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
        response = client.get(reverse("request_board:login"))
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
                    self.assertEqual(target.path, reverse("request_board:login"))
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
