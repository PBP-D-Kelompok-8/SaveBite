"""Read-only test endpoint diagnosis; never print connection parameters."""
import hashlib
import json
import os
from pathlib import Path
import psycopg2

out = Path(__file__).resolve().parent
facts = {'read_only': True, 'database_writes': 0}
config = {key: os.environ['SAVEBITE_TEST_PG_' + key]
          for key in ('HOST', 'PORT', 'USER', 'PASSWORD', 'CONTROL_DB')}
conn = None
try:
    conn = psycopg2.connect(
        host=config['HOST'], port=config['PORT'], user=config['USER'],
        password=config['PASSWORD'], dbname=config['CONTROL_DB'],
        connect_timeout=15, passfile=os.devnull, sslmode='prefer',
        application_name='savebite_board_postmerge_probe',
        options='-c default_transaction_read_only=on -c statement_timeout=5000',
    )
    conn.set_session(readonly=True)
    with conn.cursor() as cursor:
        cursor.execute('SELECT current_user, inet_server_addr()::text, inet_server_port(), version()')
        digest = hashlib.sha256(repr(cursor.fetchone()).encode()).hexdigest()
        baseline = json.loads((out.parent / 't4-01-runtime-evidence-20261024/results.json').read_text())
        facts['identity_matches_previous_test_server'] = digest == baseline['stages'][0]['identity_sha256']
    conn.rollback()
    facts['connected'] = True
except psycopg2.OperationalError as error:
    message = str(error).lower()
    facts.update(connected=False, exception_type=type(error).__name__,
                 timeout='timeout' in message, authentication_failure='authentication failed' in message,
                 refused='refused' in message, recovery='recovery' in message,
                 sqlstate=error.pgcode)
finally:
    if conn is not None:
        conn.close()
    (out / 'connection-diagnosis.json').write_text(json.dumps(facts, indent=2) + '\n')
    print(json.dumps(facts))
