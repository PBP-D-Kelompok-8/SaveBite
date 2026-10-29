import json
from queue import Queue
from threading import Event, Thread
from time import monotonic
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection, connections, transaction
from django.test import Client, TransactionTestCase
from django.urls import reverse

from request_board.models import Request, RequestOffer
from request_board.services import delete_request, fulfill_request, offer_request, select_request_offer


@skipUnless(connection.vendor == "postgresql", "Requires isolated PostgreSQL and real row locks")
class RequestConcurrencyTests(TransactionTestCase):
    def setUp(self):
        users = get_user_model().objects
        self.owner = users.create_user(username="race-owner")
        self.donor = users.create_user(username="race-donor")
        self.food_request = Request.objects.create(
            owner=self.owner, title="Beras", food_item="Beras", quantity="1.00", unit="kg",
        )

    def _connection_pid(self, conn):
        with conn.cursor() as cursor:
            cursor.execute("SET lock_timeout = '10s'")
            cursor.execute("SET statement_timeout = '10s'")
            cursor.execute(
                "SELECT pg_backend_pid(), current_database(), "
                "current_setting('transaction_isolation')"
            )
            pid, database, isolation = cursor.fetchone()
        self.assertEqual(database, connection.settings_dict["NAME"])
        self.assertTrue(database.startswith("test_savebite_t4_"))
        self.assertEqual(isolation, "read committed")
        return pid

    def _run_race(self, leader_action, contender_action, snapshot, committed):
        """Run the leader, then the contender, and prove a real row-lock wait.

        `snapshot(role, result)` builds one scenario's evidence from the worker's own
        transaction; `committed()` re-reads committed state on the test connection.
        Synchronization, blocker proof, bounded release, join, error transfer and
        session cleanup stay here so those invariants cannot drift per scenario.
        """
        ready, release, contender_ready = Event(), Event(), Event()
        outcomes, errors = Queue(), Queue()
        pids = {"observer": self._connection_pid(connection)}
        before = committed()

        def worker(role, action):
            conn = connections["default"]
            try:
                pids[role] = self._connection_pid(conn)
                with transaction.atomic():
                    if role == "contender":
                        contender_ready.set()
                    result = action()
                    evidence = snapshot(role, result)
                    if role == "leader":
                        ready.set()
                        if not release.wait(20):
                            raise TimeoutError("Leader release timed out")
                outcomes.put((role, evidence))
            except BaseException as error:
                # Transfer the exception to the test thread; no worker failure is ignored.
                errors.put(error)
            finally:
                conn.close()

        threads = [Thread(target=worker, args=("leader", leader_action)),
                   Thread(target=worker, args=("contender", contender_action))]
        blockers = []
        try:
            threads[0].start()
            self.assertTrue(ready.wait(20), "Leader did not complete its service call")
            threads[1].start()
            self.assertTrue(contender_ready.wait(20), "Contender did not connect")
            self.assertEqual(len(set(pids.values())), 3)
            deadline = monotonic() + 5
            while monotonic() < deadline:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_blocking_pids(%s)", [pids["contender"]])
                    blockers = cursor.fetchone()[0]
                if pids["leader"] in blockers:
                    break
                release.wait(0.02)
            self.assertIn(pids["leader"], blockers, "No real leader blocker within 5s")
            # The leader's changes are still uncommitted while the contender waits.
            self.assertEqual(committed(), before)
            self.assertEqual(outcomes.qsize(), 0)
        finally:
            release.set()
            for thread in threads:
                if thread.ident is not None:
                    thread.join(20)
            self.assertFalse(any(thread.is_alive() for thread in threads), "Worker remains")
            if not errors.empty():
                raise errors.get()

        self.assertEqual(outcomes.qsize(), 2)
        snapshots = dict(outcomes.get() for _ in range(2))
        self.assertEqual(set(snapshots), {"leader", "contender"})
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM pg_stat_activity WHERE pid IN (%s, %s)",
                           [pids["leader"], pids["contender"]])
            self.assertEqual(cursor.fetchone()[0], 0, "Worker connection remains")
        print("RACE_EVIDENCE " + json.dumps({
            "test": self._testMethodName, "pids": pids, "blockers": blockers,
            "before_release": before, "results": snapshots,
            "worker_errors": [], "workers_joined": True, "worker_sessions_remaining": 0,
        }, default=str), flush=True)
        return snapshots["leader"], snapshots["contender"]

    def _concurrent_offers(self, contender):
        request_id = self.food_request.pk

        def offer(actor):
            saved, created = offer_request(actor=actor, request_id=request_id)
            return {"offer_id": saved.pk, "created": created,
                    "offer_created_at": saved.created_at}

        def snapshot(role, result):
            row = Request.objects.get(pk=request_id)
            return {**result, "role": role, "status": row.status,
                    "updated_at": row.updated_at, "created_at": row.created_at,
                    "count": row.offers.count()}

        def committed():
            # An offer may only exist after the leader commits and releases the lock.
            state = self._request_snapshot()
            self.assertEqual(state["status"], Request.STATUS_PENDING)
            self.assertEqual(state["offers"], [])
            return state

        return self._run_race(lambda: offer(self.donor), lambda: offer(contender),
                              snapshot, committed)

    def _request_snapshot(self):
        self.food_request.refresh_from_db()
        return {
            "status": self.food_request.status,
            "created_at": self.food_request.created_at,
            "updated_at": self.food_request.updated_at,
            "offers": list(self.food_request.offers.order_by("pk").values_list(
                "pk", "offered_by_id", "created_at",
            )),
        }

    def _ordered_services(self, leader_action, contender_action):
        def snapshot(role, result):
            row = Request.objects.get(pk=self.food_request.pk)
            return {
                **result, "status": row.status, "created_at": row.created_at,
                "updated_at": row.updated_at,
                "offers": list(row.offers.order_by("pk").values_list(
                    "pk", "offered_by_id", "created_at",
                )),
            }

        return self._run_race(leader_action, contender_action, snapshot,
                              self._request_snapshot)

    def _assert_fulfilled_rejects_new_offer(self):
        before = self._request_snapshot()
        self.assertEqual(before["status"], Request.STATUS_FULFILLED)
        new_donor = get_user_model().objects.create_user(username="race-late-donor")
        with self.assertRaises(ValidationError) as caught:
            offer_request(actor=new_donor, request_id=self.food_request.pk)
        self.assertEqual(caught.exception.code, "request_fulfilled")
        self.assertFalse(self.food_request.offers.filter(offered_by=new_donor).exists())
        self.assertEqual(self._request_snapshot(), before)
        print("FINAL_EVIDENCE " + json.dumps({
            "test": self._testMethodName, "final": before,
            "new_offer_error": caught.exception.code, "rejected_offer_wrote_nothing": True,
        }, default=str), flush=True)

    def test_offer_commits_before_fulfill(self):
        initial = self._request_snapshot()
        self.assertEqual(initial["status"], Request.STATUS_PENDING)
        with self.assertRaises(ValidationError) as caught:
            fulfill_request(actor=self.owner, request_id=self.food_request.pk)
        self.assertEqual(caught.exception.code, "offer_selection_required")
        self.assertEqual(self._request_snapshot(), initial)

        def offer():
            saved, created = offer_request(actor=self.donor, request_id=self.food_request.pk)
            return {"offer_id": saved.pk, "created": created}

        def fulfill():
            with self.assertRaises(ValidationError) as error:
                fulfill_request(actor=self.owner, request_id=self.food_request.pk)
            return {"error_code": error.exception.code}

        leader, contender = self._ordered_services(offer, fulfill)

        final = self._request_snapshot()
        self.assertEqual(leader["status"], Request.STATUS_OFFERED)
        self.assertIs(leader["created"], True)
        self.assertEqual(len(final["offers"]), 1)
        self.assertEqual(final["offers"][0][:2], (leader["offer_id"], self.donor.pk))
        self.assertEqual(final["offers"], leader["offers"])
        self.assertEqual(final["offers"], contender["offers"])
        self.assertEqual(final["status"], Request.STATUS_OFFERED)
        self.assertEqual(contender["error_code"], "offer_selection_required")
        self.assertEqual(final["updated_at"], contender["updated_at"])
        self.assertGreater(leader["updated_at"], initial["updated_at"])
        self.assertEqual(final["updated_at"], leader["updated_at"])
        for snapshot in (leader, contender, final):
            self.assertEqual(snapshot["created_at"], initial["created_at"])

    def test_fulfill_commits_before_offer(self):
        initial_offer, created = offer_request(
            actor=self.donor, request_id=self.food_request.pk,
        )
        self.assertIs(created, True)
        select_request_offer(actor=self.owner, request_id=self.food_request.pk, offer_id=initial_offer.pk)
        initial = self._request_snapshot()
        self.assertEqual(initial["status"], Request.STATUS_OFFERED)
        self.assertEqual(initial["offers"], [
            (initial_offer.pk, self.donor.pk, initial_offer.created_at),
        ])
        new_donor = get_user_model().objects.create_user(username="race-new-donor")

        def fulfill():
            saved = fulfill_request(actor=self.owner, request_id=self.food_request.pk)
            return {"result_updated_at": saved.updated_at, "result_status": saved.status}

        def rejected_offer():
            with self.assertRaises(ValidationError) as caught:
                offer_request(actor=new_donor, request_id=self.food_request.pk)
            self.assertEqual(caught.exception.code, "request_fulfilled")
            return {"error_type": type(caught.exception).__name__,
                    "error_code": caught.exception.code}

        leader, contender = self._ordered_services(fulfill, rejected_offer)

        final = self._request_snapshot()
        self.assertEqual(final["status"], Request.STATUS_FULFILLED)
        self.assertEqual(leader["result_status"], Request.STATUS_FULFILLED)
        self.assertEqual(contender["error_type"], "ValidationError")
        self.assertEqual(contender["error_code"], "request_fulfilled")
        self.assertGreater(final["updated_at"], initial["updated_at"])
        self.assertEqual(final["updated_at"], leader["result_updated_at"])
        for snapshot in (leader, contender, final):
            self.assertEqual(snapshot["status"], Request.STATUS_FULFILLED)
            self.assertEqual(snapshot["updated_at"], leader["updated_at"])
            self.assertEqual(snapshot["created_at"], initial["created_at"])
            self.assertEqual(snapshot["offers"], initial["offers"])
        self.assertFalse(self.food_request.offers.filter(offered_by=new_donor).exists())
        self._assert_fulfilled_rejects_new_offer()

    def test_concurrent_fulfill_is_idempotent(self):
        initial_offer, created = offer_request(
            actor=self.donor, request_id=self.food_request.pk,
        )
        self.assertIs(created, True)
        select_request_offer(actor=self.owner, request_id=self.food_request.pk, offer_id=initial_offer.pk)
        initial = self._request_snapshot()
        self.assertEqual(initial["status"], Request.STATUS_OFFERED)
        self.assertEqual(initial["offers"], [
            (initial_offer.pk, self.donor.pk, initial_offer.created_at),
        ])

        def fulfill():
            saved = fulfill_request(actor=self.owner, request_id=self.food_request.pk)
            return {"result_updated_at": saved.updated_at, "result_status": saved.status}

        leader, contender = self._ordered_services(fulfill, fulfill)

        final = self._request_snapshot()
        self.assertEqual(final["status"], Request.STATUS_FULFILLED)
        self.assertGreater(final["updated_at"], initial["updated_at"])
        for snapshot in (leader, contender):
            self.assertEqual(snapshot["result_status"], Request.STATUS_FULFILLED)
            self.assertEqual(snapshot["status"], Request.STATUS_FULFILLED)
            self.assertEqual(snapshot["result_updated_at"], final["updated_at"])
            self.assertEqual(snapshot["updated_at"], final["updated_at"])
            self.assertEqual(snapshot["created_at"], initial["created_at"])
            self.assertEqual(snapshot["offers"], initial["offers"])
        self.assertEqual(final["updated_at"], leader["result_updated_at"])
        self.assertEqual(final["created_at"], initial["created_at"])
        self.assertEqual(final["offers"], initial["offers"])
        self._assert_fulfilled_rejects_new_offer()

    def _board_snapshot(self):
        return {
            "requests": list(Request.objects.order_by("pk").values()),
            "offers": list(RequestOffer.objects.order_by("pk").values()),
        }

    def _ordered_delete_services(self, leader_action, contender_action):
        def snapshot(role, result):
            return {**result, "board": self._board_snapshot()}

        return self._run_race(leader_action, contender_action, snapshot,
                              self._board_snapshot)

    def _delete_fixture(self):
        other = Request.objects.create(
            owner=self.owner, title="Unrelated", food_item="Roti", quantity="2.00", unit="kg",
        )
        offer_request(actor=self.donor, request_id=other.pk)
        initial_offer, created = offer_request(
            actor=self.donor, request_id=self.food_request.pk,
        )
        self.assertIs(created, True)
        new_donor = get_user_model().objects.create_user(username="race-delete-donor")
        return initial_offer, new_donor, self._board_snapshot()

    def _assert_deleted_board(self, before):
        request_id = self.food_request.pk
        expected = {
            "requests": [row for row in before["requests"] if row["id"] != request_id],
            "offers": [row for row in before["offers"] if row["request_id"] != request_id],
        }
        self.assertEqual(self._board_snapshot(), expected)
        self.assertFalse(Request.objects.filter(pk=request_id).exists())
        self.assertFalse(RequestOffer.objects.filter(request_id=request_id).exists())
        self.assertFalse(RequestOffer.objects.exclude(request_id__in=Request.objects.values("pk")).exists())
        # Deferred PostgreSQL foreign keys must be checked before claiming no orphan.
        connection.check_constraints()
        with self.assertRaises(Request.DoesNotExist) as caught:
            offer_request(actor=self.donor, request_id=request_id)
        self.assertIs(type(caught.exception), Request.DoesNotExist)
        self.assertEqual(self._board_snapshot(), expected)
        print("FINAL_EVIDENCE " + json.dumps({
            "test": self._testMethodName, "final": expected,
            "target_request_count": Request.objects.filter(pk=request_id).count(),
            "target_offer_count": RequestOffer.objects.filter(request_id=request_id).count(),
            "request_count": Request.objects.count(), "offer_count": RequestOffer.objects.count(),
            "late_offer_error": "Request.DoesNotExist", "no_orphans": True,
            "foreign_keys_checked": True, "no_resurrection": True,
        }, default=str), flush=True)
        return expected

    def test_offer_commits_before_delete(self):
        initial_offer, new_donor, before = self._delete_fixture()

        def offer():
            saved, created = offer_request(actor=new_donor, request_id=self.food_request.pk)
            return {"offer_id": saved.pk, "created": created, "created_at": saved.created_at}

        def delete():
            result = delete_request(actor=self.owner, request_id=self.food_request.pk)
            self.assertIsNone(result)
            return {"deleted": True}

        leader, contender = self._ordered_delete_services(offer, delete)

        self.assertIs(leader["created"], True)
        expected = self._assert_deleted_board(before)
        self.assertEqual(contender["board"], expected)
        self.assertIs(contender["deleted"], True)
        self.assertEqual(leader["board"]["requests"], before["requests"])
        self.assertEqual(leader["board"]["offers"], before["offers"] + [{
            "id": leader["offer_id"], "request_id": self.food_request.pk,
            "offered_by_id": new_donor.pk, "created_at": leader["created_at"],
        }])
        self.assertIn(initial_offer.pk, [row["id"] for row in before["offers"]])

    def test_delete_commits_before_offer(self):
        initial_offer, new_donor, before = self._delete_fixture()

        def delete():
            result = delete_request(actor=self.owner, request_id=self.food_request.pk)
            self.assertIsNone(result)
            return {"deleted": True}

        def rejected_offer():
            with self.assertRaises(Request.DoesNotExist) as caught:
                offer_request(actor=new_donor, request_id=self.food_request.pk)
            self.assertIs(type(caught.exception), Request.DoesNotExist)
            return {"error_type": "Request.DoesNotExist"}

        leader, contender = self._ordered_delete_services(delete, rejected_offer)

        expected = self._assert_deleted_board(before)
        self.assertIs(leader["deleted"], True)
        self.assertEqual(contender["error_type"], "Request.DoesNotExist")
        self.assertEqual(leader["board"], expected)
        self.assertEqual(contender["board"], expected)
        self.assertIn(initial_offer.pk, [row["id"] for row in before["offers"]])

    def test_same_donor_concurrent_offers_create_one_offer(self):
        initial_created = self.food_request.created_at
        initial_updated = self.food_request.updated_at

        leader, contender = self._concurrent_offers(self.donor)

        self.food_request.refresh_from_db()
        offer = RequestOffer.objects.get(request=self.food_request, offered_by=self.donor)
        self.assertIs(leader["created"], True)
        self.assertIs(contender["created"], False)
        self.assertEqual(leader["offer_id"], contender["offer_id"])
        self.assertEqual(offer.pk, leader["offer_id"])
        self.assertEqual(RequestOffer.objects.count(), 1)
        self.assertEqual(self.food_request.status, Request.STATUS_OFFERED)
        self.assertGreater(self.food_request.updated_at, initial_updated)
        self.assertEqual(self.food_request.created_at, initial_created)
        for result in (leader, contender):
            self.assertEqual(result["count"], 1)
            self.assertEqual(result["status"], Request.STATUS_OFFERED)
            self.assertEqual(result["created_at"], initial_created)
            self.assertEqual(result["updated_at"], self.food_request.updated_at)
            self.assertEqual(result["offer_created_at"], offer.created_at)

    def test_different_donors_concurrent_offers_are_both_saved(self):
        second_donor = get_user_model().objects.create_user(username="race-second-donor")
        initial_created = self.food_request.created_at
        initial_updated = self.food_request.updated_at

        leader, contender = self._concurrent_offers(second_donor)

        self.food_request.refresh_from_db()
        offers = RequestOffer.objects.filter(request=self.food_request)
        self.assertEqual(offers.count(), 2)
        self.assertEqual(set(offers.values_list("offered_by_id", flat=True)),
                         {self.donor.pk, second_donor.pk})
        self.assertEqual(set(offers.values_list("pk", flat=True)),
                         {leader["offer_id"], contender["offer_id"]})
        self.assertNotEqual(leader["offer_id"], contender["offer_id"])
        self.assertEqual(self.food_request.status, Request.STATUS_OFFERED)
        self.assertGreater(self.food_request.updated_at, initial_updated)
        self.assertEqual(self.food_request.created_at, initial_created)
        for result, expected_count in ((leader, 1), (contender, 2)):
            self.assertIs(result["created"], True)
            self.assertEqual(result["count"], expected_count)
            self.assertEqual(result["status"], Request.STATUS_OFFERED)
            self.assertEqual(result["created_at"], initial_created)
            self.assertEqual(result["updated_at"], self.food_request.updated_at)
            self.assertEqual(offers.get(pk=result["offer_id"]).created_at,
                             result["offer_created_at"])

        public_fields = {
            "id", "title", "food_item", "quantity", "unit", "description", "location",
            "owner", "status", "created_at", "updated_at", "offer_count",
        }
        for actor in (None, self.owner):
            with self.subTest(api_actor="guest" if actor is None else "owner"):
                client = Client(enforce_csrf_checks=True)
                if actor is not None:
                    client.force_login(actor)
                response = client.get(reverse("request_board:api_request_collection"))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(response.json()["data"]), 1)
                item = response.json()["data"][0]
                self.assertEqual(set(item), public_fields)
                self.assertEqual(item["id"], self.food_request.pk)
                self.assertEqual(item["owner"], {"display_name": self.owner.username})
                self.assertEqual(item["status"], Request.STATUS_OFFERED)
                self.assertEqual(item["offer_count"], 2)
                self.assertIs(type(item["offer_count"]), int)
                self.assertEqual(item["created_at"], initial_created.isoformat())
                self.assertEqual(item["updated_at"], self.food_request.updated_at.isoformat())
                self.assertNotContains(response, self.donor.username)
                self.assertNotContains(response, second_donor.username)
