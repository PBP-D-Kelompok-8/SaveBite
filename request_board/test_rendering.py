from html.parser import HTMLParser
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import re
from urllib.parse import parse_qs, urlencode, urlsplit

from django.contrib.auth import SESSION_KEY, get_user_model
from django.template.response import TemplateResponse
from django.test import Client, TestCase
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.utils.html import escape, strip_tags

from request_board.models import Request
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
