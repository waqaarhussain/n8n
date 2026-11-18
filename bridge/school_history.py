"""Local maintenance for school audience settings and a fresh manual test."""
import argparse
from datetime import datetime, timezone
import os
import sqlite3

from app import STATE, database, config
from event_rules import VALID_GROUPS, init_index, policy

LABELS = {'nursery': 'Nursery', 'reception': 'Reception', 'pre_prep': 'Pre-Prep',
          **{f'year_{n}': f'Year {n}' for n in range(1, 7)}}


def reset_history():
    db = database()
    try:
        init_index(db)
        backup_path = STATE.parent / ('school-history-before-reset-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.sqlite3')
        descriptor = os.open(backup_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        destination = sqlite3.connect(backup_path)
        try:
            db.backup(destination)
        finally:
            destination.close()
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        db.execute('BEGIN IMMEDIATE')
        for table in ('mail', 'events', 'event_index', 'event_aliases', 'note_merges', 'image_transcripts'):
            if table in tables:
                db.execute('DELETE FROM ' + table)
        db.commit()
        print('School processing/duplicate history and cached image reading cleared.')
        print('Duplicate protection remains ON for the next email.')
        print('Private history backup: ' + str(backup_path))
        print('No iCloud emails or calendar events were deleted. Stats, Telegram choices and credentials are preserved.')
        print('Remove old TEST entries from the School calendar before forwarding the test emails again.')
    finally:
        db.close()


def configure_groups():
    from setup import save
    settings = config()
    current = policy(settings)['groups']
    print('School audience groups: ' + ', '.join(LABELS[value] for value in current))
    for number, value in enumerate(VALID_GROUPS, 1):
        print(f'{number}) {LABELS[value]}' + (' [selected]' if value in current else ''))
    print('Whole-school/family events and events with NO specified year group are also included.')
    while True:
        choice = input('Allowed group numbers, separated by commas (Enter keeps current): ').strip()
        if not choice:
            print('Year-group settings unchanged.')
            return
        values = [part.strip() for part in choice.split(',')]
        if values and all(value.isdigit() and 1 <= int(value) <= len(VALID_GROUPS) for value in values):
            selected = sorted({VALID_GROUPS[int(value) - 1] for value in values})
            break
        print('Enter listed numbers, for example 1,2,3.')
    settings['school_audience'] = {'groups': selected, 'include_whole_school': True}
    save(settings)
    print('Saved: ' + ', '.join(LABELS[value] for value in selected))
    print('Applies to new emails without a workflow reimport or republish.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('reset', 'groups'))
    args = parser.parse_args()
    if args.action == 'reset':
        reset_history()
    else:
        configure_groups()


if __name__ == '__main__':
    main()
