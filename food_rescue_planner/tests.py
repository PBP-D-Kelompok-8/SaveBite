from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import FoodRescuePlan
from jurnal.models import FoodLog


User = get_user_model()


class FoodRescuePlannerTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='mutia_test',
            password='test-password-123',
        )
        self.other_user = User.objects.create_user(
            username='other_test',
            password='test-password-123',
        )

        self.client.force_login(self.user)

        self.index_url = reverse('food_rescue_planner:index')
        self.create_url = reverse('food_rescue_planner:create')

        self.plan = FoodRescuePlan.objects.create(
            user=self.user,
            nama_bahan='Pisang',
            jumlah=3,
            satuan='pcs',
            target_date=date(2026, 11, 1),
            metode_aksi='cook',
            notes='Dibuat menjadi kue pisang',
        )

    def test_read_displays_only_own_plans(self):
        FoodRescuePlan.objects.create(
            user=self.other_user,
            nama_bahan='Apel',
            jumlah=2,
            satuan='pcs',
            target_date=date(2026, 11, 2),
            metode_aksi='share',
        )

        response = self.client.get(self.index_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Pisang')
        self.assertNotContains(response, 'Apel')

    def test_create_plan_successfully(self):
        response = self.client.post(
            self.create_url,
            {
                'nama_bahan': 'Wortel',
                'jumlah': '5',
                'satuan': 'kg',
                'target_date': '2026-11-05',
                'metode_aksi': 'preserve',
                'notes': 'Disimpan dengan benar',
            },
        )

        self.assertRedirects(response, self.index_url)

        self.assertTrue(
            FoodRescuePlan.objects.filter(
                user=self.user,
                nama_bahan='Wortel',
                jumlah=5,
                satuan='kg',
            ).exists()
        )

    def test_create_rejects_invalid_quantity(self):
        initial_count = FoodRescuePlan.objects.count()

        response = self.client.post(
            self.create_url,
            {
                'nama_bahan': 'Wortel',
                'jumlah': '-2',
                'satuan': 'kg',
                'target_date': '2026-11-05',
                'metode_aksi': 'preserve',
                'notes': '',
            },
        )

        self.assertRedirects(response, self.index_url)
        self.assertEqual(
            FoodRescuePlan.objects.count(),
            initial_count,
        )

    def test_edit_page_displays_own_plan(self):
        response = self.client.get(
            reverse(
                'food_rescue_planner:edit',
                kwargs={'plan_id': self.plan.pk},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Pisang')

    def test_update_plan_successfully(self):
        response = self.client.post(
            reverse(
                'food_rescue_planner:edit',
                kwargs={'plan_id': self.plan.pk},
            ),
            {
                'nama_bahan': 'Pisang Matang',
                'jumlah': '4',
                'satuan': 'pcs',
                'target_date': '2026-11-10',
                'metode_aksi': 'preserve',
                'status': 'ongoing',
                'notes': 'Akan dibekukan',
            },
        )

        self.assertRedirects(response, self.index_url)

        self.plan.refresh_from_db()
        self.assertEqual(self.plan.nama_bahan, 'Pisang Matang')
        self.assertEqual(self.plan.jumlah, 4)
        self.assertEqual(self.plan.metode_aksi, 'preserve')
        self.assertEqual(self.plan.status, 'ongoing')

    def test_cannot_edit_another_users_plan(self):
        other_plan = FoodRescuePlan.objects.create(
            user=self.other_user,
            nama_bahan='Apel',
            jumlah=2,
            satuan='pcs',
            target_date=date(2026, 11, 2),
            metode_aksi='share',
        )

        response = self.client.get(
            reverse(
                'food_rescue_planner:edit',
                kwargs={'plan_id': other_plan.pk},
            )
        )

        self.assertEqual(response.status_code, 404)

    def test_delete_requires_post(self):
        response = self.client.get(
            reverse(
                'food_rescue_planner:delete',
                kwargs={'plan_id': self.plan.pk},
            )
        )

        self.assertRedirects(response, self.index_url)
        self.assertTrue(
            FoodRescuePlan.objects.filter(pk=self.plan.pk).exists()
        )

    def test_delete_plan_successfully(self):
        response = self.client.post(
            reverse(
                'food_rescue_planner:delete',
                kwargs={'plan_id': self.plan.pk},
            )
        )

        self.assertRedirects(response, self.index_url)
        self.assertFalse(
            FoodRescuePlan.objects.filter(pk=self.plan.pk).exists()
        )

    def test_cannot_delete_another_users_plan(self):
        other_plan = FoodRescuePlan.objects.create(
            user=self.other_user,
            nama_bahan='Apel',
            jumlah=2,
            satuan='pcs',
            target_date=date(2026, 11, 2),
            metode_aksi='share',
        )

        response = self.client.post(
            reverse(
                'food_rescue_planner:delete',
                kwargs={'plan_id': other_plan.pk},
            )
        )

        self.assertEqual(response.status_code, 404)
        self.assertTrue(
            FoodRescuePlan.objects.filter(pk=other_plan.pk).exists()
        )

    def test_success_status_creates_food_log(self):
        response = self.client.post(
            reverse(
                'food_rescue_planner:edit',
                kwargs={'plan_id': self.plan.pk},
            ),
            {
                'nama_bahan': 'Pisang',
                'jumlah': '3',
                'satuan': 'pcs',
                'target_date': '2026-11-01',
                'metode_aksi': 'cook',
                'status': 'success',
                'notes': 'Berhasil diolah',
            },
        )

        self.assertRedirects(response, self.index_url)

        self.plan.refresh_from_db()
        self.assertIsNotNone(self.plan.completed_at)

        log = FoodLog.objects.get(rescue_plan_id=self.plan.pk)
        self.assertEqual(log.user, self.user)
        self.assertEqual(log.food_name, 'Pisang')
        self.assertEqual(log.status, 'terselamatkan')

    def test_changing_completed_status_updates_existing_log(self):
        self.plan.status = 'success'
        self.plan.save()

        from jurnal.services import create_log_from_rescue_plan
        create_log_from_rescue_plan(self.plan)

        response = self.client.post(
            reverse(
                'food_rescue_planner:edit',
                kwargs={'plan_id': self.plan.pk},
            ),
            {
                'nama_bahan': 'Pisang',
                'jumlah': '3',
                'satuan': 'pcs',
                'target_date': '2026-11-01',
                'metode_aksi': 'cook',
                'status': 'failed',
                'notes': 'Tidak berhasil diselamatkan',
            },
        )

        self.assertRedirects(response, self.index_url)

        log = FoodLog.objects.get(rescue_plan_id=self.plan.pk)
        self.assertEqual(log.status, 'terbuang')
        self.assertEqual(
            FoodLog.objects.filter(rescue_plan_id=self.plan.pk).count(),
            1,
        )

    def test_anonymous_user_cannot_create_plan(self):
        self.client.logout()

        response = self.client.post(
            self.create_url,
            {
                'nama_bahan': 'Wortel',
                'jumlah': '2',
                'satuan': 'kg',
                'target_date': '2026-11-05',
                'metode_aksi': 'cook',
                'notes': '',
            },
        )

        self.assertRedirects(response, self.index_url)
        self.assertFalse(
            FoodRescuePlan.objects.filter(nama_bahan='Wortel').exists()
        )

    
    def test_returning_to_ongoing_deletes_existing_food_log(self):
        from jurnal.services import create_log_from_rescue_plan

        self.plan.status = 'success'
        self.plan.save()
        create_log_from_rescue_plan(self.plan)

        self.assertTrue(
            FoodLog.objects.filter(
                rescue_plan_id=self.plan.pk
            ).exists()
        )

        response = self.client.post(
            reverse(
                'food_rescue_planner:edit',
                kwargs={'plan_id': self.plan.pk},
            ),
            {
                'nama_bahan': 'Pisang',
                'jumlah': '3',
                'satuan': 'pcs',
                'target_date': '2026-11-01',
                'metode_aksi': 'cook',
                'status': 'ongoing',
                'notes': '',
            },
        )

        self.assertRedirects(response, self.index_url)
        self.assertFalse(
            FoodLog.objects.filter(
                rescue_plan_id=self.plan.pk
            ).exists()
        )

    def test_completing_plan_again_creates_food_log(self):
        from jurnal.services import create_log_from_rescue_plan

        self.plan.status = 'success'
        self.plan.save()
        create_log_from_rescue_plan(self.plan)

        FoodLog.objects.filter(
            rescue_plan_id=self.plan.pk
        ).delete()

        response = self.client.post(
            reverse(
                'food_rescue_planner:edit',
                kwargs={'plan_id': self.plan.pk},
            ),
            {
                'nama_bahan': 'Pisang',
                'jumlah': '3',
                'satuan': 'pcs',
                'target_date': '2026-11-01',
                'metode_aksi': 'cook',
                'status': 'success',
                'notes': 'Selesai diolah lagi',
            },
        )

        self.assertRedirects(response, self.index_url)
        self.assertTrue(
            FoodLog.objects.filter(
                rescue_plan_id=self.plan.pk,
                status='terselamatkan',
            ).exists()
        )
