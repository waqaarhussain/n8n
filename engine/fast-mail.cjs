'use strict';

// A small, reversible preload for n8n's existing IMAP transport. It never opens
// another mail connection, parses messages, writes state or receives credentials.
const { createRequire } = require('node:module');
const HOOKED = Symbol.for('waqaar.n8n.fastMail');

function install(ImapSimple, options = {}) {
  const seconds = options.seconds ?? 10;
  const schedule = options.schedule ?? setInterval;
  const cancel = options.cancel ?? clearInterval;
  const warn = options.warn ?? (() => {});
  if (seconds === 0) return false;
  if (!Number.isFinite(seconds) || seconds < 5 || seconds > 60) {
    throw new Error('Fast mail interval must be 0 (off) or between 5 and 60 seconds');
  }
  if (!ImapSimple || typeof ImapSimple.connect !== 'function' ||
      typeof ImapSimple.prototype.catchUp !== 'function') {
    throw new Error('Unsupported n8n IMAP transport');
  }
  if (ImapSimple[HOOKED]) return true;
  const originalConnect = ImapSimple.connect;
  ImapSimple.connect = async function (...args) {
    const connection = await originalConnect.apply(this, args);
    const mailbox = args[1]?.mailbox;
    // Credential checks and one-off IMAP actions are not continuous listeners.
    if (!mailbox) return connection;
    const supports = client => client && typeof client.noop === 'function' &&
      typeof client.search === 'function';
    if (!supports(connection.client) || !connection.queue ||
        typeof connection.queue.then !== 'function') {
      warn('Fast mail unavailable for this transport; native listener preserved.');
      return connection;
    }
    let client = connection.client;
    let highestUid = Math.max(0, Number(client.mailbox?.uidNext || 1) - 1);
    let validity = String(client.mailbox?.uidValidity ?? '');
    let pending = false;
    let stopped = false;
    let timer;
    const stop = () => {
      if (stopped) return;
      stopped = true;
      if (timer) cancel(timer);
    };
    const originalEnd = connection.end;
    connection.end = function (...endArgs) {
      stop();
      return originalEnd.apply(this, endArgs);
    };
    const tick = async () => {
      if (stopped || connection.endedByCaller || connection.closed) {
        stop();
        return;
      }
      if (pending) return;
      pending = true;
      try {
        const current = connection.client;
        if (!supports(current) || !current.usable) return;
        const currentValidity = String(current.mailbox?.uidValidity ?? '');
        if (current !== client || currentValidity !== validity) {
          // Preserve the watermark across reconnects; only a new UIDVALIDITY
          // epoch calls for a rescan. Native n8n owns its message deduplication.
          if (currentValidity !== validity) highestUid = 0;
          client = current;
          validity = currentValidity;
        }
        // NOOP asks the selected mailbox for updates without replacing its
        // connection. SEARCH is UID-only: idle checks never fetch old bodies.
        await current.noop();
        if (stopped || current !== connection.client) return;
        const found = await current.search({ uid: `${Math.max(1, highestUid)}:*` }, { uid: true });
        if (stopped || current !== connection.client || !Array.isArray(found)) return;
        let newest = highestUid;
        for (const value of found) {
          const uid = Number(value);
          if (Number.isSafeInteger(uid) && uid > newest) newest = uid;
        }
        if (newest > highestUid) {
          connection.catchUp();
          // Do not stack rescan jobs while n8n fetches/parses an attachment.
          await connection.queue;
          if (current === connection.client && !stopped) highestUid = newest;
        }
      } catch {
        // Native n8n/ImapFlow owns transport recovery. Never log an IMAP error
        // object, credentials, message metadata or body content here.
      } finally {
        pending = false;
      }
    };
    timer = schedule(() => { void tick(); }, seconds * 1000);
    timer?.unref?.();
    return connection;
  };
  Object.defineProperty(ImapSimple, HOOKED, { value: true });
  return true;
}

function loadTransport() {
  const n8nRoot = '/usr/local/lib/node_modules/n8n/package.json';
  const rootRequire = createRequire(n8nRoot);
  const nodesPath = rootRequire.resolve('n8n-nodes-base/package.json');
  const nodesRequire = createRequire(nodesPath);
  return nodesRequire('@n8n/imap').ImapSimple;
}

function bootstrap() {
  const seconds = Number(process.env.N8N_FAST_MAIL_SECONDS ?? 10);
  if (seconds === 0) return false;
  try {
    install(loadTransport(), { seconds, warn: message => console.warn(message) });
    console.info(`Fast mail enabled: live IMAP plus ${seconds}-second UID catch-up checks.`);
    return true;
  } catch {
    // An engine update must not take the editor or existing workflows offline.
    console.warn('Fast mail preload unavailable; native n8n mail behaviour preserved.');
    return false;
  }
}

module.exports = { install, loadTransport, bootstrap };
if (process.env.N8N_FAST_MAIL_TEST !== '1') bootstrap();
