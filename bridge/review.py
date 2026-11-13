"""Show recent cached event proposals without reading credentials or writing events."""
import json
from app import database


def main():
    with database() as db:
        rows = db.execute('SELECT rowid, extraction FROM mail ORDER BY rowid DESC LIMIT 10').fetchall()
        if not rows:
            print('No cached email extractions found.')
        for row in rows:
            value = json.loads(row['extraction'])
            print(f"\nSaved email #{row['rowid']}")
            print('Reason: ' + str(value.get('reason', '')))
            for event in value.get('events', []):
                print('Title: ' + str(event.get('title', '')))
                print('Proposed start: ' + str(event.get('start', '')))
                print('Supporting quote: ' + str(event.get('evidence', '')))


if __name__ == '__main__':
    main()
