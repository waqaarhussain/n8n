"""Telegram opt-in integration checks against the disposable stats test database."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root/'scripts'))
spec = importlib.util.spec_from_file_location('telegram_smoke_helper', root/'scripts/telegram.py')
tg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tg)
container = os.environ['STATS_TEST_CONTAINER']
assert container == 'n8n-stats-test', 'Never use a production database for this test.'
real_run = subprocess.run


def fixture(query):
    return real_run(['docker', 'exec', '-i', container, 'psql', '-X', '-q', '-A', '-t',
                     '-v', 'ON_ERROR_STOP=1', '-U', 'postgres'], input=query,
                    text=True, check=True, stdout=subprocess.PIPE).stdout.strip()


fixture('''DROP TABLE execution_entity;
CREATE TABLE execution_entity (
  id SERIAL PRIMARY KEY, "workflowId" TEXT, "createdAt" TIMESTAMPTZ DEFAULT now(),
  "startedAt" TIMESTAMPTZ DEFAULT now(), "stoppedAt" TIMESTAMPTZ DEFAULT now(),
  status TEXT, "deletedAt" TIMESTAMPTZ);
INSERT INTO execution_entity("workflowId",status) VALUES ('a','error');
''')


def route(command, **kwargs):
    # Keep the production read-only transaction and result decoding unchanged.
    assert command[:4] == ['docker', 'compose', 'exec', '-T']
    return real_run(['docker', 'exec', '-i', container, 'psql', '-X', '-q', '-A', '-t',
                     '-v', 'ON_ERROR_STOP=1', '-U', 'postgres'], **kwargs)


tg.subprocess.run = route
tg.public_url = lambda: 'https://n8n.example.com'
tg.time.sleep = lambda _: None
sent = []
tg.api = lambda token, method, payload: sent.append(payload)
settings = {'token': 'test-only', 'chat_id': '123'}
with tempfile.TemporaryDirectory() as directory:
    tg.STATE = Path(directory)/'telegram.sqlite3'
    db = tg.database()
    tg.verify_schema()
    assert len(tg.workflows()) == 2
    assert tg.tick(db, settings) == 0
    tg.set_subscription(db, 'a', True)
    fixture('''INSERT INTO execution_entity("workflowId",status) VALUES
      ('a','running'), ('a','error'), ('a','crashed'), ('a','success'), ('a','canceled'), ('b','error');
      INSERT INTO execution_entity("workflowId",status,"deletedAt") VALUES ('a','error',now());''')
    assert tg.tick(db, settings) == 2, sent
    assert tg.tick(db, settings) == 0
    fixture("UPDATE execution_entity SET status='error' WHERE status='running';")
    assert tg.tick(db, settings) == 1  # lower-ID run fails late
    tg.set_subscription(db, 'a', False)
    fixture("INSERT INTO execution_entity(\"workflowId\",status) VALUES ('a','error');")
    assert tg.tick(db, settings) == 0
    tg.set_subscription(db, 'a', True)
    assert tg.tick(db, settings) == 0  # no history replay after re-enable
    fixture("INSERT INTO execution_entity(\"workflowId\",status) VALUES ('a','error');")
    assert tg.tick(db, settings) == 1
    try:
        tg.sql("DELETE FROM workflow_entity RETURNING to_json(id);")
        raise AssertionError('Monitor unexpectedly allowed a write')
    except subprocess.CalledProcessError:
        pass
    assert len(tg.workflows()) == 2
    # Actual n8n-produced flatted data, delivered only for opted-in successful maintenance.
    fixture('''CREATE TABLE execution_data ("executionId" INTEGER PRIMARY KEY, data TEXT);
      INSERT INTO workflow_entity(id,name) VALUES ('VpsWeeklyUpdatesV1','VPS weekly updates');''')
    tg.set_subscription(db, tg.MAINTENANCE_WORKFLOW, True)
    serialized = Path('/tmp/vps-execution.json').read_text()
    assert tg.reboot_names(serialized) == ['Stored VPS name']
    identity = fixture("INSERT INTO execution_entity(\"workflowId\",status) VALUES ('VpsWeeklyUpdatesV1','success') RETURNING id;").splitlines()[0]
    fixture('INSERT INTO execution_data VALUES (' + identity + ',' + tg.literal(serialized) + ');')
    assert tg.tick(db, settings) == 1
    assert sent[-1] == {'chat_id': '123', 'text': 'Stored VPS name updated successfully ✅ reboot required'}
    assert tg.tick(db, settings) == 0
    tg.set_subscription(db, tg.MAINTENANCE_WORKFLOW, False)
    assert tg.tick(db, settings) == 0
    db.close()
print('Telegram integration passed: default OFF, per-workflow opt-in, saved failures/crashes, no historical replay, late failures, persistent deduplication and read-only database access.')
