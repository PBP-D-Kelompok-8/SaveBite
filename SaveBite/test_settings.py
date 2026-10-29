"""Security regression tests that load settings without local credentials."""

import os
from pathlib import Path
import runpy
from unittest import TestCase
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured


class SecuritySettingsTests(TestCase):
    def load_settings(self, **environment):
        # Prevent the developer's .env from influencing isolated test cases.
        with patch.dict(os.environ, environment, clear=True), patch('dotenv.load_dotenv'):
            return runpy.run_path(str(Path(__file__).with_name('settings.py')))

    def test_secret_key_comes_from_environment(self):
        settings = self.load_settings(SECRET_KEY='test-only-environment-key')
        self.assertEqual(settings['SECRET_KEY'], 'test-only-environment-key')
        self.assertFalse(settings.get('SECRET_KEY_FALLBACKS', []))

    def test_missing_secret_key_uses_development_fallback(self):
        settings = self.load_settings()
        self.assertFalse(settings['PRODUCTION'])
        self.assertEqual(settings['SECRET_KEY'], 'django-insecure-fallback-key-for-dev-only')

    def test_blank_secret_key_prevents_startup_in_all_environments(self):
        for production in ('False', 'True'):
            for key in ('', '   '):
                with self.subTest(production=production, key=key):
                    with self.assertRaises(ImproperlyConfigured):
                        self.load_settings(PRODUCTION=production, SECRET_KEY=key)

    def test_missing_secret_key_prevents_production_startup(self):
        with self.assertRaises(ImproperlyConfigured):
            self.load_settings(PRODUCTION='True')

    def test_development_fallback_prevents_production_startup(self):
        fallback = 'django-insecure-fallback-key-for-dev-only'
        for key in (fallback, f'  {fallback}  '):
            with self.subTest(key=key):
                with self.assertRaises(ImproperlyConfigured):
                    self.load_settings(PRODUCTION='True', SECRET_KEY=key)

    def test_production_uses_configured_secret_key(self):
        settings = self.load_settings(PRODUCTION='True', SECRET_KEY='production-test-only-key')
        self.assertTrue(settings['PRODUCTION'])
        self.assertEqual(settings['SECRET_KEY'], 'production-test-only-key')
        self.assertFalse(settings.get('SECRET_KEY_FALLBACKS', []))

    def test_debug_is_disabled_by_default(self):
        settings = self.load_settings(SECRET_KEY='test-only-key')
        self.assertFalse(settings['DEBUG'])

    def test_debug_requires_explicit_true(self):
        for value, expected in (('true', True), ('TRUE', True), ('false', False),
                                ('False', False), ('', False), ('invalid', False)):
            with self.subTest(value=value):
                settings = self.load_settings(SECRET_KEY='test-only-key', DEBUG=value)
                self.assertIs(settings['DEBUG'], expected)

    def test_production_disables_debug_even_when_requested(self):
        settings = self.load_settings(
            SECRET_KEY='test-only-key', PRODUCTION='true', DEBUG='true',
        )
        self.assertTrue(settings['PRODUCTION'])
        self.assertFalse(settings['DEBUG'])
