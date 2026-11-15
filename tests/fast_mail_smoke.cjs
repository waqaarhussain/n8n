'use strict';
// Runs inside the real, pinned n8n image. No mail credentials or network are used.
process.env.N8N_FAST_MAIL_TEST = '1';
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { loadTransport, install } = require('/opt/n8n-fast-mail.cjs');
const ImapSimple = loadTransport();
let intervalCallback;
let cancelled = false;
install(ImapSimple, {
  schedule(fn, milliseconds) {
    assert.equal(milliseconds, 10000);
    intervalCallback = fn;
    return { unref() {} };
  },
  cancel() { cancelled = true; },
  warn(message) { throw new Error(message); },
});

class Transport extends EventEmitter {
  usable = true;
  mailbox = { path: 'INBOX', uidNext: 100, uidValidity: 1n, exists: 10 };
  available = [99];
  bodiesFetched = 0;
  noopCount = 0;
  async connect() {}
  async mailboxOpen() { return this.mailbox; }
  async noop() { this.noopCount++; }
  async search() { return this.available; }
  async *fetch() { this.bodiesFetched++; yield { uid: 100 }; }
  async logout() { this.emit('close'); }
  close() { this.emit('close'); }
}
(async () => {
  const client = new Transport();
  const connection = await ImapSimple.connect({}, { mailbox: 'INBOX' }, () => client);
  assert.equal(typeof intervalCallback, 'function', 'The real n8n transport was not hooked');
  let arrivals = 0;
  connection.onArrival(async () => { arrivals++; });
  intervalCallback();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(arrivals, 0);
  assert.equal(client.bodiesFetched, 0);
  client.available = [99, 100]; // no exists event: simulate a missed/delayed push
  intervalCallback();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(arrivals, 1, 'UID catch-up did not reach the native arrival handler');
  intervalCallback();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(arrivals, 1, 'Already observed UIDs caused another catch-up');
  assert.equal(client.bodiesFetched, 0, 'Idle polling fetched a message body');
  assert.equal(client.noopCount, 3);
  connection.end();
  assert.equal(cancelled, true);
  console.log('Real n8n IMAP smoke passed: module resolution, ten-second checks, missed-push catch-up, no idle body downloads and native shutdown.');
})().catch(error => { console.error(error); process.exitCode = 1; });
