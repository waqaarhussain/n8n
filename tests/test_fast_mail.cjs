'use strict';
process.env.N8N_FAST_MAIL_TEST = '1';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { install } = require('../engine/fast-mail.cjs');

function fixture({ next = 10, validity = 1n } = {}) {
  const calls = { noop: 0, search: [], catchUp: 0, ended: 0, timers: [], cancelled: [] };
  const client = {
    usable: true,
    mailbox: { uidNext: next, uidValidity: validity },
    async noop() { calls.noop++; },
    async search(criteria, options) {
      calls.search.push({ criteria, options });
      return client.found;
    },
    found: [next - 1],
  };
  const connection = {
    client, queue: Promise.resolve(), closed: false, endedByCaller: false,
    catchUp() { calls.catchUp++; },
    end() { this.endedByCaller = true; calls.ended++; },
  };
  class Transport {
    static async connect() { return connection; }
    catchUp() {}
  }
  install(Transport, {
    schedule(fn, interval) { calls.timers.push({ fn, interval }); return { unref() {} }; },
    cancel(timer) { calls.cancelled.push(timer); },
  });
  return { Transport, connection, client, calls };
}
const flush = () => new Promise(resolve => setImmediate(resolve));

// Actual time is controlled by injected interval callbacks; no sleeping/network.
test('installs a ten-second check only on continuous mailbox listeners', async () => {
  const f = fixture();
  await f.Transport.connect({}, undefined);
  assert.equal(f.calls.timers.length, 0);
  await f.Transport.connect({}, { mailbox: 'INBOX' });
  assert.equal(f.calls.timers[0].interval, 10000);
});

test('idle UID checks never fetch message bodies or queue duplicate catch-ups', async () => {
  const f = fixture();
  await f.Transport.connect({}, { mailbox: 'INBOX' });
  f.calls.timers[0].fn();
  await flush();
  f.calls.timers[0].fn();
  await flush();
  assert.equal(f.calls.noop, 2);
  assert.equal(f.calls.catchUp, 0);
  assert.deepEqual(f.calls.search[0], { criteria: { uid: '9:*' }, options: { uid: true } });
});

test('a new UID triggers the original workflow catch-up once', async () => {
  const f = fixture();
  await f.Transport.connect({}, { mailbox: 'INBOX' });
  f.client.found = [9, 10];
  f.calls.timers[0].fn();
  await flush();
  assert.equal(f.calls.catchUp, 1);
  f.calls.timers[0].fn();
  await flush();
  assert.equal(f.calls.catchUp, 1);
  f.client.found = [10, 11];
  f.calls.timers[0].fn();
  await flush();
  assert.equal(f.calls.catchUp, 2);
});

test('overlapping checks are coalesced while an attachment downloads', async () => {
  const f = fixture();
  let release;
  f.connection.queue = new Promise(resolve => { release = resolve; });
  await f.Transport.connect({}, { mailbox: 'INBOX' });
  f.client.found = [10];
  f.calls.timers[0].fn();
  await flush();
  f.calls.timers[0].fn();
  f.calls.timers[0].fn();
  await flush();
  assert.equal(f.calls.noop, 1);
  assert.equal(f.calls.catchUp, 1);
  release();
  await flush();
  f.calls.timers[0].fn();
  await flush();
  assert.equal(f.calls.noop, 2);
});

test('ending/deactivating the workflow cancels the timer', async () => {
  const f = fixture();
  await f.Transport.connect({}, { mailbox: 'INBOX' });
  f.connection.end();
  f.calls.timers[0].fn();
  await flush();
  assert.equal(f.calls.cancelled.length, 1);
  assert.equal(f.calls.ended, 1);
  assert.equal(f.calls.noop, 0);
});

test('a permanently closed transport cancels the timer', async () => {
  const f = fixture();
  await f.Transport.connect({}, { mailbox: 'INBOX' });
  f.connection.closed = true;
  f.calls.timers[0].fn();
  await flush();
  assert.equal(f.calls.cancelled.length, 1);
  assert.equal(f.calls.noop, 0);
});

test('native connection replacement uses the new client without creating logins', async () => {
  const f = fixture();
  await f.Transport.connect({}, { mailbox: 'INBOX' });
  const replacement = { ...f.client, found: [10] };
  let usedReplacement = false;
  replacement.noop = async () => { usedReplacement = true; };
  replacement.search = async () => replacement.found;
  f.connection.client = replacement;
  f.calls.timers[0].fn();
  await flush();
  assert.equal(usedReplacement, true);
  assert.equal(f.calls.catchUp, 1);
  assert.equal(f.calls.noop, 0);
});

test('network errors leave recovery to n8n, then future checks recover', async () => {
  const f = fixture();
  await f.Transport.connect({}, { mailbox: 'INBOX' });
  f.client.noop = async () => { throw new Error('Never log credentials'); };
  f.calls.timers[0].fn();
  await flush();
  f.client.noop = async () => {};
  f.client.found = [10];
  f.calls.timers[0].fn();
  await flush();
  assert.equal(f.calls.catchUp, 1);
});

test('does not run against a discarded transport after NOOP completes', async () => {
  const f = fixture();
  await f.Transport.connect({}, { mailbox: 'INBOX' });
  f.client.noop = async () => { f.connection.client = { ...f.client }; };
  f.calls.timers[0].fn();
  await flush();
  assert.equal(f.calls.search.length, 0);
  assert.equal(f.calls.catchUp, 0);
});

test('unsupported internals preserve the native listener and warn without secrets', async () => {
  const f = fixture();
  f.connection.queue = undefined;
  const result = await f.Transport.connect({}, { mailbox: 'INBOX' });
  assert.equal(result, f.connection);
  assert.equal(f.calls.timers.length, 0);
});

test('hook installation is idempotent and supports explicit disable', async () => {
  const f = fixture();
  const connect = f.Transport.connect;
  assert.equal(install(f.Transport), true);
  assert.equal(f.Transport.connect, connect);
  class Disabled { static async connect() {} catchUp() {} }
  const original = Disabled.connect;
  assert.equal(install(Disabled, { seconds: 0 }), false);
  assert.equal(Disabled.connect, original);
});
