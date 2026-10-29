from urllib.parse import urlencode

from django.contrib.auth import SESSION_KEY, get_user_model
from django.test import Client, TestCase
from django.urls import reverse


class SaveBiteLoginIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.password = "Existing-SaveBite-password-847!"
        cls.user = get_user_model().objects.create_user(
            username="savebite-member", password=cls.password,
        )

    def test_main_login_uses_existing_account_and_shared_board_session(self):
        original_hash = self.user.password
        response = self.client.post(reverse("login"), {
            "username": self.user.username,
            "password": self.password,
            "next": "/board/new/",
        })

        self.assertRedirects(response, "/board/new/")
        self.assertEqual(self.client.session[SESSION_KEY], str(self.user.pk))
        page = self.client.get("/board/new/")
        self.assertEqual(page.wsgi_request.user.pk, self.user.pk)
        self.assertEqual(get_user_model().objects.count(), 1)
        self.user.refresh_from_db()
        self.assertEqual(self.user.password, original_hash)

    def test_board_guests_use_main_login_and_return_to_original_destination(self):
        destination = "/board/new/?source=board"
        response = self.client.get(destination)

        self.assertRedirects(
            response, "/login/?" + urlencode({"next": destination}, safe="/"),
        )
        login = self.client.get(response.url)
        self.assertTemplateUsed(login, "login.html")
        self.assertContains(login, 'action="/login/"')
        self.assertContains(login, 'name="next" value="' + destination + '"')

    def test_legacy_board_login_redirects_to_main_login_preserving_next(self):
        query = urlencode({"next": "/board/new/?source=legacy"})
        response = self.client.get(reverse("request_board:login") + "?" + query)

        self.assertRedirects(response, "/login/?" + query)
        self.assertTemplateUsed(self.client.get(response.url), "login.html")

    def test_invalid_password_displays_error_without_session_or_password_echo(self):
        response = self.client.post(reverse("login"), {
            "username": self.user.username,
            "password": "incorrect-private-password",
            "next": "/board/new/",
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'role="alert"')
        self.assertContains(response, self.user.username)
        self.assertContains(response, 'name="next" value="/board/new/"')
        self.assertNotContains(response, "incorrect-private-password")
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_unsafe_next_uses_landing_fallback(self):
        for destination in ("https://evil.invalid/", "//evil.invalid/"):
            with self.subTest(destination=destination):
                client = Client()
                response = client.post(reverse("login"), {
                    "username": self.user.username,
                    "password": self.password,
                    "next": destination,
                })
                self.assertRedirects(response, "/")

    def test_inactive_account_cannot_login(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        response = self.client.post(reverse("login"), {
            "username": self.user.username, "password": self.password,
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'role="alert"')
        self.assertNotIn(SESSION_KEY, self.client.session)

    def test_login_links_use_main_login(self):
        for destination in ("/", "/board/"):
            with self.subTest(destination=destination):
                response = self.client.get(destination)
                self.assertNotContains(response, 'href="/board/login/')
                self.assertContains(response, 'href="/login/')
