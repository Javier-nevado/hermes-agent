/**
 * Unit tests for the WhatsApp message store + normalization (store.mjs).
 *
 * Run manually (CI has no JS runner):
 *   node --test scripts/whatsapp-bridge/store.test.mjs
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';

import { MessageStore, normalizeMessage, normalizeWhatsAppId } from './store.mjs';

function mkMsg(id, ts, text, { fromMe = false, jid = '35699360043@s.whatsapp.net', pushName = 'Craig' } = {}) {
  return {
    key: { id, remoteJid: jid, fromMe },
    message: { conversation: text },
    messageTimestamp: ts,
    pushName,
  };
}

test('normalizeWhatsAppIdentifier keeps colon→@ form (chat id semantics)', () => {
  assert.equal(normalizeWhatsAppId('participant:5'), 'participant@5');
  assert.equal(normalizeWhatsAppId(''), '');
});

test('normalizeMessage extracts body + infers media type without downloading', () => {
  const rec = normalizeMessage(mkMsg('A', 100, 'hello'));
  assert.equal(rec.messageId, 'A');
  assert.equal(rec.chatId, '35699360043@s.whatsapp.net');
  assert.equal(rec.body, 'hello');
  assert.equal(rec.hasMedia, false);
  assert.equal(rec.mediaType, '');
  assert.deepEqual(rec.mediaUrls, []);
  assert.equal(rec.timestamp, 100);
});

test('normalizeMessage records fromMe and uses pushName for senderName', () => {
  const rec = normalizeMessage(mkMsg('A', 100, 'hi', { fromMe: true }));
  assert.equal(rec.fromMe, true);
  assert.equal(rec.senderName, 'Craig');
});

test('normalizeMessage marks image media and fills placeholder body', () => {
  const msg = {
    key: { id: 'I', remoteJid: 'x@s.whatsapp.net', fromMe: false },
    message: { imageMessage: { mimetype: 'image/jpeg' } },
    messageTimestamp: 1,
  };
  const rec = normalizeMessage(msg);
  assert.equal(rec.hasMedia, true);
  assert.equal(rec.mediaType, 'image');
  assert.equal(rec.body, '[image received]');
  assert.deepEqual(rec.mediaUrls, []);
});

test('normalizeMessage returns null when remoteJid is missing', () => {
  assert.equal(normalizeMessage({ key: { id: 'X' }, message: { conversation: 'x' } }), null);
});

test('MessageStore.upsert dedupes by messageId', () => {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'wa-store-'));
  try {
    const s = new MessageStore({ storeFile: path.join(dir, 'm.json') });
    assert.equal(s.upsert(normalizeMessage(mkMsg('A', 100, 'a'))), true);
    assert.equal(s.upsert(normalizeMessage(mkMsg('A', 100, 'a'))), false);
    assert.equal(s.getHistory('35699360043@s.whatsapp.net', 10).length, 1);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('MessageStore.getHistory returns newest-first within limit', () => {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'wa-store-'));
  try {
    const s = new MessageStore({ storeFile: path.join(dir, 'm.json') });
    for (const [id, ts] of [['1', 100], ['2', 300], ['3', 200]]) {
      s.upsert(normalizeMessage(mkMsg(id, ts, `m${id}`)));
    }
    const out = s.getHistory('35699360043@s.whatsapp.net', 2);
    assert.deepEqual(out.map((r) => r.body), ['m2', 'm3']); // ts 300 then 200
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('MessageStore per-chat cap keeps the newest, drops the oldest', () => {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'wa-store-'));
  try {
    const s = new MessageStore({ storeFile: path.join(dir, 'm.json'), maxPerChat: 3 });
    // Insert 3 (ts 100/200/300), then a 4th that is the OLDEST (ts 50).
    s.upsert(normalizeMessage(mkMsg('1', 100, 'm1')));
    s.upsert(normalizeMessage(mkMsg('2', 200, 'm2')));
    s.upsert(normalizeMessage(mkMsg('3', 300, 'm3')));
    s.upsert(normalizeMessage(mkMsg('4', 50, 'oldest')));
    const out = s.getHistory('35699360043@s.whatsapp.net', 10);
    assert.equal(out.length, 3);
    // Oldest retained is now m1 (ts 100); the new ts-50 msg was evicted.
    assert.deepEqual(out.map((r) => r.body).sort(), ['m1', 'm2', 'm3']);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('MessageStore enforces the global cap by evicting globally-oldest', () => {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'wa-store-'));
  try {
    const s = new MessageStore({ storeFile: path.join(dir, 'm.json'), maxPerChat: 100, maxTotal: 2 });
    s.upsert(normalizeMessage(mkMsg('1', 100, 'a', { jid: 'one@s.whatsapp.net' })));
    s.upsert(normalizeMessage(mkMsg('2', 200, 'b', { jid: 'two@s.whatsapp.net' })));
    s.upsert(normalizeMessage(mkMsg('3', 300, 'c', { jid: 'three@s.whatsapp.net' })));
    // Total capped at 2 → the globally-oldest (ts 100, 'a') is evicted.
    assert.equal(s.getHistory('one@s.whatsapp.net', 10).length, 0);
    assert.equal(s.getHistory('two@s.whatsapp.net', 10).length, 1);
    assert.equal(s.getHistory('three@s.whatsapp.net', 10).length, 1);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('MessageStore.listChats resolves name/phone and sorts by recency', () => {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'wa-store-'));
  try {
    const s = new MessageStore({ storeFile: path.join(dir, 'm.json') });
    s.upsert(normalizeMessage(mkMsg('1', 100, 'old', { jid: 'a@s.whatsapp.net' })));
    s.upsert(normalizeMessage(mkMsg('2', 300, 'new', { jid: 'b@s.whatsapp.net' })));
    const chats = s.listChats({
      resolveName: (jid) => (jid === 'b@s.whatsapp.net' ? 'Bob' : null),
      resolvePhone: (jid) => jid.replace(/@.*/, ''),
    });
    assert.deepEqual(chats.map((c) => c.jid), ['b@s.whatsapp.net', 'a@s.whatsapp.net']); // newest first
    assert.equal(chats[0].name, 'Bob');
    assert.equal(chats[0].phone, 'b');
    assert.equal(chats[0].lastPreview, 'new');
    assert.equal(chats[1].name, 'a'); // fallback when no contact name
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('MessageStore persists and reloads across instances', () => {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'wa-store-'));
  const file = path.join(dir, 'm.json');
  try {
    const s = new MessageStore({ storeFile: file });
    s.upsert(normalizeMessage(mkMsg('1', 100, 'persist-me')));
    s.flush();
    const s2 = new MessageStore({ storeFile: file });
    s2.load();
    const out = s2.getHistory('35699360043@s.whatsapp.net', 10);
    assert.equal(out.length, 1);
    assert.equal(out[0].body, 'persist-me');
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('MessageStore.load tolerates a malformed store file', () => {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'wa-store-'));
  const file = path.join(dir, 'm.json');
  try {
    writeFileSync(file, '{not valid json');
    const s = new MessageStore({ storeFile: file });
    s.load(); // must not throw
    assert.equal(s.listChats({}).length, 0);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

// Regression guard for a real production crash (PR #30): bridge.js called
// `messageStore.load()` at the top level BEFORE the `const messageStore = ...`
// declaration. In ESM that is a temporal-dead-zone ReferenceError and the bridge
// never boots (taking down ALL WhatsApp — send included — on the linked box).
// Assert the call always sits textually AFTER the construction.
test('bridge.js loads the message store only after constructing it (no TDZ on boot)', () => {
  const bridgePath = fileURLToPath(new URL('./bridge.js', import.meta.url));
  const src = readFileSync(bridgePath, 'utf8');
  const decl = src.indexOf('const messageStore = new MessageStore');
  const load = src.indexOf('messageStore.load()');
  assert.notEqual(decl, -1, 'messageStore declaration not found in bridge.js');
  assert.notEqual(load, -1, 'messageStore.load() call not found in bridge.js');
  assert.ok(
    load > decl,
    `messageStore.load() (offset ${load}) must come AFTER the declaration ` +
      `(offset ${decl}) — calling it first is a temporal-dead-zone ReferenceError.`,
  );
});
