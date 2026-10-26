ï»¿"""Isolated test bootstrap; only explicitly supplied test PostgreSQL settings."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import sys
import types
import uuid
import socket
import threading
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
label = "full-discovery"
facts = {"label": label, "labels": [], "status": "RUNNING", "started_utc": datetime.now(timezone.utc).isoformat(), "values_logged": False, "tcp_attempts": 0, "discovered": None, "executed": 0, "passed": 0, "failures": None, "errors": None, "skip": None}
owned = set()
maintenance = None
connection = None


def scalar(cursor, sql, args=None):
    cursor.execute(sql, args)
    return cursor.fetchone()[0]


try:
    keys = ("HOST", "PORT", "USER", "PASSWORD", "CONTROL_DB", "MAINTENANCE_DB")
    facts['configuration_present'] = {key: bool(os.environ.get('SAVEBITE_TEST_PG_' + key)) for key in keys}
    assert all(facts['configuration_present'].values())
    config = {key: os.environ["SAVEBITE_TEST_PG_" + key] for key in keys}
    assert all(config.values())
    assert config["CONTROL_DB"] != config["MAINTENANCE_DB"]
    assert config["HOST"] in ("127.0.0.1", "localhost", "::1")
    assert config["PORT"].isdigit()
    for key in list(os.environ):
        if key.startswith("PG"):
            del os.environ[key]
    import psycopg2
    from psycopg2 import sql
    tag = "savebite_t408_" + uuid.uuid4().hex
    facts['application_name'] = tag
    params = dict(host=config["HOST"], port=config["PORT"], user=config["USER"],
                  password=config["PASSWORD"], connect_timeout=5, application_name=tag,
                  sslmode="prefer", passfile=os.devnull,
                  options="-c statement_timeout=10000 -c lock_timeout=10000")
    facts['tcp_attempts'] = 1
    with socket.create_connection((config['HOST'], int(config['PORT'])), timeout=5):
        pass
    facts['tcp_available'] = True
    facts['fingerprints'] = []

    def identity(db):
        with contextlib.closing(psycopg2.connect(dbname=db, **params)) as conn:
            conn.set_session(readonly=True, isolation_level='READ COMMITTED', autocommit=False)
            with conn.cursor() as cursor:
                cursor.execute("SELECT current_database(), current_user, inet_server_addr()::text, inet_server_port(), version()")
                row = cursor.fetchone()
                assert row[0] == db and row[1] == config["USER"]
                cursor.execute("SELECT current_setting('transaction_read_only'), current_setting('transaction_isolation'), current_setting('server_version')")
                mode = cursor.fetchone()
                assert mode[:2] == ('on', 'read committed')
                ref = json.loads((ROOT/'docs/t4-01-runtime-evidence-20261024/results.json').read_text())['stages'][0]
                fp = hashlib.sha256(repr(row[1:]).encode()).hexdigest()
                facts['fingerprints'].append(dict(label='control' if db == config['CONTROL_DB'] else 'maintenance', identity_sha256=fp, matches_T4_01=fp == ref['identity_sha256'], server_version_matches=mode[2] == ref['server_version'], read_only=mode[0], isolation=mode[1]))
                assert fp == ref['identity_sha256'] and mode[2] == ref['server_version']
                cursor.execute("SELECT n.nspname,c.relname,c.relkind,c.relowner FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema' ORDER BY 1,2,3")
                catalog = hashlib.sha256(repr(cursor.fetchall()).encode()).hexdigest()
                conn.rollback()
                return row, catalog

    before = {db: identity(db) for db in (config["CONTROL_DB"], config["MAINTENANCE_DB"])}
    identities = [value[0] for value in before.values()]
    assert identities[0][1:] == identities[1][1:]
    historical = json.loads((ROOT / "docs/t4-01-runtime-evidence-20261024/results.json").read_text())
    fingerprint = hashlib.sha256(repr(identities[0][1:]).encode()).hexdigest()
    assert fingerprint == historical["stages"][0]["identity_sha256"]
    facts["T4_01_identity_match"] = True
    maintenance = psycopg2.connect(dbname=config["MAINTENANCE_DB"], **params)
    maintenance.autocommit = True
    with maintenance.cursor() as cursor:
        facts["server_version"] = scalar(cursor, "SHOW server_version")

    os.environ["PRODUCTION"] = "False"
    os.environ["DJANGO_SETTINGS_MODULE"] = "SaveBite.settings"
    import dotenv
    dotenv.load_dotenv = lambda *args, **kwargs: False
    import SaveBite.settings as isolated
    database = "test_savebite_t4_" + uuid.uuid4().hex
    facts["database"] = database
    isolated.DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql", "NAME": database,
        "HOST": config["HOST"], "PORT": config["PORT"], "USER": config["USER"],
        "PASSWORD": config["PASSWORD"], "CONN_MAX_AGE": 0,
        "OPTIONS": {"connect_timeout": 5, "application_name": tag, "sslmode": "prefer",
                    "passfile": os.devnull,
                    "options": "-c search_path=public -c statement_timeout=10000 -c lock_timeout=10000"},
        "TEST": {"NAME": database, "MIRROR": None, "MIGRATE": True},
    }}
    isolated.ALLOWED_HOSTS = [*isolated.ALLOWED_HOSTS, "testserver"]
    from django.conf import settings
    assert not settings.configured
    import django
    django.setup()
    from django.db import connection, connections
    from django.test.runner import DiscoverRunner

    def exists(db):
        with maintenance.cursor() as cursor:
            return scalar(cursor, "SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=%s)", [db])

    def guarded_create(self, verbosity, autoclobber, keepdb=False):
        # DiscoverRunner requests autoclobber in noninteractive mode; this override
        # never honors it: a collision fails and no DROP/retry path exists here.
        assert not autoclobber and not keepdb
        facts["autoclobber_disabled_by_guard"] = True
        assert self._get_test_db_name() == database
        assert database not in (config["CONTROL_DB"], config["MAINTENANCE_DB"])
        assert not exists(database), "Database collision refused"
        facts["absence_before_create"] = True
        with maintenance.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(database)))
        owned.add(database)
        facts['ownership_after_successful_create'] = True
        return database

    def guarded_destroy(self, db, verbosity):
        assert db in owned and db == database
        self.connection.close()
        with maintenance.cursor() as cursor:
            facts['database_sessions_before_drop'] = scalar(cursor, "SELECT count(*) FROM pg_stat_activity WHERE datname=%s", [db])
            assert facts['database_sessions_before_drop'] == 0
            cursor.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(db)))
        owned.remove(db)
        facts["dropped_absence_verified"] = not exists(db)
        assert facts["dropped_absence_verified"]

    creation = connection.creation
    original_create_test_db = creation.create_test_db
    def no_autoclobber(verbosity=1, autoclobber=False, serialize=None, keepdb=False):
        assert not keepdb
        return original_create_test_db(verbosity=verbosity, autoclobber=False, serialize=serialize, keepdb=False)
    creation.create_test_db = no_autoclobber
    creation._create_test_db = types.MethodType(guarded_create, creation)
    creation._destroy_test_db = types.MethodType(guarded_destroy, creation)

    class EvidenceRunner(DiscoverRunner):
        def build_suite(self, *args, **kwargs):
            suite = super().build_suite(*args, **kwargs)
            facts['suite_count'] = suite.countTestCases()
            return suite

        def setup_databases(self, **kwargs):
            old_config = super().setup_databases(**kwargs)
            assert connection.settings_dict['NAME'] == database
            with connection.cursor() as cursor:
                assert scalar(cursor, 'SELECT current_database()') == database
            from django.core.management import call_command
            call_command('check')
            facts['check_exit'] = 0
            call_command('makemigrations', check=True, dry_run=True)
            facts['drift_exit'] = 0
            call_command('migrate', verbosity=1, interactive=False)
            facts['migrate_exit'] = 0
            call_command('migrate', check=True, interactive=False)
            facts['migrate_check_exit'] = 0
            with connection.cursor() as cursor:
                cursor.execute('SELECT app, name FROM django_migrations ORDER BY app, name')
                facts['applied_migrations'] = cursor.fetchall()
            facts['check_drift_migrate_check_disposable'] = True
            return old_config

        def suite_result(self, suite, result, **kwargs):
            facts.update(discovered=facts["suite_count"], ran=result.testsRun, executed=result.testsRun-len(result.skipped), failures=len(result.failures),
                         errors=len(result.errors), skip=len(result.skipped),
                         passed=result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped))
            return super().suite_result(suite, result, **kwargs)

    runner = EvidenceRunner(verbosity=2, interactive=False, keepdb=False, parallel=1)
    failures = runner.run_tests([])
    assert failures == 0 and facts["discovered"] == facts["ran"] and facts["discovered"] > 0 and facts["skip"] == 0
    facts['worker_threads_remaining'] = len([t for t in threading.enumerate() if t is not threading.main_thread()])
    assert facts['worker_threads_remaining'] == 0
    assert not owned and not exists(database)
    assert before == {db: identity(db) for db in before}
    facts["control_maintenance_catalog_unchanged"] = True
    with maintenance.cursor() as cursor:
        facts["remaining_sessions"] = scalar(cursor, "SELECT count(*) FROM pg_stat_activity WHERE application_name=%s AND pid<>pg_backend_pid()", [tag])
    assert facts["remaining_sessions"] == 0
    facts["status"] = "PASS"
except BaseException as error:
    # Connection exception text can expose credentials; persist only its type/location.
    import traceback
    facts["status"] = "FAIL"
    facts["exception_type"] = type(error).__name__
    facts["frames"] = [{"file": Path(frame.filename).name, "line": frame.lineno}
                       for frame in traceback.extract_tb(error.__traceback__)]
finally:
    try:
        if connection is not None:
            connections.close_all()
        if maintenance is not None:
            try:
                for db in list(owned):
                    guarded_destroy(creation, db, 0)
            finally:
                maintenance.close()
                facts['maintenance_closed'] = bool(maintenance.closed)
    except BaseException as cleanup_error:
        facts['status'] = 'FAIL'
        facts['cleanup_error_type'] = type(cleanup_error).__name__
    facts['owned_remaining'] = sorted(owned)
    facts['finished_utc'] = datetime.now(timezone.utc).isoformat()
    (OUT / "postgres-full.json").write_text(json.dumps(facts, indent=2) + "\n")
    print(json.dumps(facts), flush=True)
sys.exit(0 if facts["status"] == "PASS" else 1)
