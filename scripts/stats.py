"""Pause/resume n8n overview counters without disabling workflows or execution logs."""
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone


def sql(query):
    result = subprocess.run(['docker', 'compose', 'exec', '-T', 'postgres', 'psql',
                             '-X', '-q', '-A', '-t', '-v', 'ON_ERROR_STOP=1', '-U', 'n8n', '-d', 'n8n'],
                            input=query, text=True, check=True, stdout=subprocess.PIPE)
    return result.stdout.strip()


def literal(value):
    return "'" + value.replace("'", "''") + "'"


def install():
    required = {'insights_raw': {'metaId', 'timestamp', 'value'},
                'insights_metadata': {'metaId', 'workflowId'},
                'insights_by_period': {'metaId', 'value'}}
    columns = json.loads(sql("SELECT coalesce(json_agg(json_build_object('table',table_name,'column',column_name)), '[]') FROM information_schema.columns WHERE table_schema='public' AND table_name IN ('insights_raw','insights_metadata','insights_by_period');"))
    for table, expected in required.items():
        actual = {row['column'] for row in columns if row['table'] == table}
        if not expected <= actual:
            raise SystemExit('Stats schema differs from the verified n8n version. No stats changes made.')
    sql(Path('scripts/stats.sql').read_text())


def choose(allow_all=False):
    rows = json.loads(sql('''SELECT coalesce(json_agg(r), '[]') FROM
        (SELECT w.id, w.name, EXISTS(SELECT 1 FROM school_stats_pause_windows p
         WHERE p.workflow_id=w.id AND p.ended_at IS NULL) AS paused
         FROM workflow_entity w ORDER BY w.name, w.id) r;'''))
    if not rows:
        raise SystemExit('No workflows found.')
    if allow_all:
        print('0) All workflows')
    for number, row in enumerate(rows, 1):
        print(f"{number}) {row['name']} | stats {'PAUSED' if row['paused'] else 'COUNTING'}")
    while True:
        value = input('Workflow number (Enter cancels): ').strip()
        if not value:
            raise SystemExit('Cancelled.')
        if value == '0' and allow_all:
            return None
        if value.isdigit() and 1 <= int(value) <= len(rows):
            return rows[int(value) - 1]
        print('Choose a listed workflow number.')


def toggle(row):
    identity = literal(row['id'])
    # Store time windows to catch buffered insights flushed after resume as well.
    if row['paused']:
        sql(f'UPDATE school_stats_pause_windows SET ended_at=now() WHERE workflow_id={identity} AND ended_at IS NULL;')
        print('Stats resumed for: ' + row['name'])
    else:
        sql(f'INSERT INTO school_stats_pause_windows(workflow_id) VALUES ({identity});')
        print('Stats paused for: ' + row['name'])
    print('Workflow stays enabled. Execution logs and calendar processing are unchanged.')
    print('Refresh the n8n Overview page to see counters. Completed runs during the pause are excluded.')


def reset_query(workflow_id):
    condition = '' if workflow_id is None else ' WHERE "metaId" IN (SELECT "metaId" FROM insights_metadata WHERE "workflowId"=' + literal(workflow_id) + ')'
    return 'BEGIN;\nDELETE FROM insights_raw' + condition + ';\nDELETE FROM insights_by_period' + condition + ';\nCOMMIT;'


def reset(row):
    print('Resetting overview counters. n8n will briefly stop to flush buffered stats.')
    subprocess.run(['docker', 'compose', 'stop', 'n8n'], check=True)
    try:
        target = Path('backups') / ('stats-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.dump')
        target.parent.mkdir(exist_ok=True)
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as stream:
            subprocess.run(['docker', 'compose', 'exec', '-T', 'postgres', 'pg_dump', '-U', 'n8n', '-d', 'n8n',
                            '-Fc', '-t', 'insights_raw', '-t', 'insights_by_period', '-t', 'insights_metadata'],
                           stdout=stream, check=True)
        sql(reset_query(row['id'] if row else None))
        print('Overview stats reset for: ' + (row['name'] if row else 'all workflows'))
        print('Execution logs, workflows, credentials and event duplicate history are preserved.')
        print('Stats backup: ' + str(target))
    finally:
        subprocess.run(['docker', 'compose', 'up', '-d', '--wait', '--wait-timeout', '180', 'n8n'], check=True)


def main():
    install()
    reset_mode = len(sys.argv) > 1 and sys.argv[1] == 'reset'
    row = choose(allow_all=reset_mode)
    if reset_mode:
        reset(row)
    else:
        toggle(row)


if __name__ == '__main__':
    main()
