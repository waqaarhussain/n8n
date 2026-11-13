"""Integration test only, against a dedicated disposable PostgreSQL container."""
import importlib.util
import os
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('stats_helper', root/'scripts/stats.py')
stats = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stats)
container = os.environ['STATS_TEST_CONTAINER']
assert container == 'n8n-stats-test', 'This smoke test only runs against its disposable test container.'


def sql(value):
    return subprocess.run(['docker', 'exec', '-i', container, 'psql', '-X', '-q', '-A', '-t',
                           '-v', 'ON_ERROR_STOP=1', '-U', 'postgres'], input=value,
                          text=True, check=True, stdout=subprocess.PIPE).stdout.strip()


stats.sql = sql
sql('''CREATE TABLE workflow_entity(id TEXT PRIMARY KEY,name TEXT);
CREATE TABLE insights_metadata("metaId" INTEGER PRIMARY KEY,"workflowId" TEXT);
CREATE TABLE insights_raw(id SERIAL PRIMARY KEY,"metaId" INTEGER,"timestamp" TIMESTAMPTZ,value BIGINT);
CREATE TABLE insights_by_period(id SERIAL PRIMARY KEY,"metaId" INTEGER,value BIGINT);
CREATE TABLE execution_entity(id INTEGER);
INSERT INTO workflow_entity VALUES ('a','Workflow A'),('b','Workflow B');
INSERT INTO insights_metadata VALUES (1,'a'),(2,'b');
INSERT INTO execution_entity VALUES(99);
INSERT INTO insights_raw("metaId","timestamp",value) VALUES (1,now()-interval '1 hour',1),(2,now()-interval '1 hour',1);
INSERT INTO insights_by_period("metaId",value) VALUES (1,20),(2,30);''')
stats.install()
stats.toggle({'id':'a', 'name':'Workflow A', 'paused':False})
sql('INSERT INTO insights_raw("metaId","timestamp",value) VALUES (1,now(),1),(2,now(),1);')
assert sql('SELECT sum(value) FROM insights_raw WHERE "metaId"=1;') == '1'
assert sql('SELECT sum(value) FROM insights_raw WHERE "metaId"=2;') == '2'
stats.toggle({'id':'a', 'name':'Workflow A', 'paused':True})
# A buffered event completed during pause must remain excluded after resume.
sql('''INSERT INTO insights_raw("metaId","timestamp",value)
SELECT 1, started_at + interval '0.000001 seconds', 1 FROM school_stats_pause_windows LIMIT 1;
INSERT INTO insights_raw("metaId","timestamp",value) VALUES (1,now(),1);''')
assert sql('SELECT sum(value) FROM insights_raw WHERE "metaId"=1;') == '2'
stats.install()  # Idempotent; pause history survives reinstalls.
sql(stats.reset_query('a'))
assert sql('SELECT count(*) FROM insights_raw WHERE "metaId"=1;') == '0'
assert sql('SELECT sum(value) FROM insights_raw WHERE "metaId"=2;') == '2'
assert sql('SELECT count(*) FROM insights_by_period WHERE "metaId"=1;') == '0'
assert sql('SELECT count(*) FROM execution_entity;') == '1'
assert sql('SELECT count(*) FROM insights_metadata;') == '2'
sql(stats.reset_query(None))
assert sql('SELECT count(*) FROM insights_raw;') == '0'
assert sql('SELECT count(*) FROM insights_by_period;') == '0'
print('Stats integration passed: workflow scoping, pause, delayed flush, resume, reset and retained execution logs.')
