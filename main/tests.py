from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse


class AuthenticationTests(TestCase):
    def setUp(self):
        self.username = "pengguna_uji_auth"
        self.password = "SandiAman-PBP-2026!"

    def test_register_accepts_deployed_https_origin_through_proxy(self):
        host = "reshandy-taftazani-savebite.pws.cs.ui.ac.id"
        client = Client(enforce_csrf_checks=True, HTTP_HOST=host)
        client.get(reverse("register"))
        data = {
            "username": self.username,
            "password1": self.password,
            "password2": self.password,
            "csrfmiddlewaretoken": client.cookies["csrftoken"].value,
        }
        response = client.post(reverse("register"), data, HTTP_ORIGIN=f"https://{host}")
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)
        self.assertTrue(User.objects.filter(username=self.username).exists())

    def test_register_rejects_untrusted_origin(self):
        client = Client(enforce_csrf_checks=True)
        client.get(reverse("register"))
        response = client.post(
            reverse("register"),
            {"csrfmiddlewaretoken": client.cookies["csrftoken"].value},
            HTTP_ORIGIN="https://example.invalid",
        )
        self.assertEqual(response.status_code, 403)

    def test_register_creates_user_and_shows_success_message(self):
        response = self.client.post(
            reverse("register"),
            {
                "username": self.username,
                "password1": self.password,
                "password2": self.password,
            },
        )

        self.assertRedirects(
            response, reverse("login"), fetch_redirect_response=False
        )
        user = User.objects.get(username=self.username)
        self.assertTrue(user.check_password(self.password))

        response = self.client.get(reverse("login"))
        self.assertContains(response, "Akun berhasil dibuat. Silakan login.")

    def test_register_rejects_different_passwords(self):
        response = self.client.post(
            reverse("register"),
            {
                "username": self.username,
                "password1": self.password,
                "password2": "Password-yang-berbeda-2026!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username=self.username).exists())

    def test_login_accepts_correct_password(self):
        User.objects.create_user(username=self.username, password=self.password)
        response = self.client.post(
            reverse("login"),
            {"username": self.username, "password": self.password},
        )

        self.assertRedirects(
            response, reverse("landing_page"), fetch_redirect_response=False
        )
        self.assertIn("_auth_user_id", self.client.session)

    def test_login_rejects_wrong_password(self):
        User.objects.create_user(username=self.username, password=self.password)
        response = self.client.post(
            reverse("login"),
            {"username": self.username, "password": "password-salah"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_uses_safe_next_address(self):
        User.objects.create_user(username=self.username, password=self.password)
        next_url = reverse("request_board:request_board")
        response = self.client.post(
            reverse("login"),
            {
                "username": self.username,
                "password": self.password,
                "next": next_url,
            },
        )
        self.assertRedirects(response, next_url, fetch_redirect_response=False)

        self.client.logout()
        response = self.client.post(
            reverse("login"),
            {
                "username": self.username,
                "password": self.password,
                "next": "https://example.invalid/",
            },
        )
        self.assertRedirects(
            response, reverse("landing_page"), fetch_redirect_response=False
        )

    def test_logout_needs_post_and_ends_session(self):
        user = User.objects.create_user(username=self.username, password=self.password)
        self.client.force_login(user)

        response = self.client.get(reverse("logout"))
        self.assertEqual(response.status_code, 405)
        self.assertIn("_auth_user_id", self.client.session)

        response = self.client.post(reverse("logout"))
        self.assertRedirects(
            response, reverse("landing_page"), fetch_redirect_response=False
        )
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_navbar_uses_main_login_and_shows_logout_when_signed_in(self):
        response = self.client.get(reverse("landing_page"))
        self.assertContains(response, 'href="/login/"')
        self.assertNotContains(response, 'href="/board/login/"')
        self.assertContains(response, 'href="/register/"')
        self.assertNotContains(response, "Logout")

        user = User.objects.create_user(username=self.username, password=self.password)
        self.client.force_login(user)
        response = self.client.get(reverse("landing_page"))
        self.assertContains(response, self.username)
        self.assertContains(response, 'action="/logout/"')
        self.assertContains(response, 'method="post"')
        self.assertNotContains(response, 'href="/register/"')
