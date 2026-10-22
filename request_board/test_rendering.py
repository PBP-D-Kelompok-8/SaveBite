from html.parser import HTMLParser
import re
from urllib.parse import urlencode

from django.contrib.auth import SESSION_KEY, get_user_model
from django.template.response import TemplateResponse
from django.test import Client, TestCase
from django.urls import reverse
from django.utils.html import escape, strip_tags


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
