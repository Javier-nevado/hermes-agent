/**
 * WhatsApp message store + message normalization.
 *
 * Extracted from bridge.js so the storage/cap/dedup logic and the pure
 * WAMessage → record normalization are unit-testable with `node --test`
 * without booting the express server or importing Baileys.
 *
 * The store is an in-memory `Map<jid, record[]>` (ascending by timestamp)
 * populated from TWO sources in bridge.js:
 *   1. the `messaging-history.set` event  (companion history-sync on connect)
 *   2. live `messages.upsert`              (ongoing traffic)
 * It persists to a JSON file under the Baileys session dir so history
 * survives bridge restarts.
 */

import { existsSync, readFileSync, writeFileSync } from 'fs';

// --- Pure message helpers (moved from bridge.js) ---------------------------

export function normalizeWhatsAppId(value) {
  if (!value) return '';
  return String(value).replace(':', '@');
}

export function getMessageContent(msg) {
  const content = msg?.message || {};
  if (content.ephemeralMessage?.message) return content.ephemeralMessage.message;
  if (content.viewOnceMessage?.message) return content.viewOnceMessage.message;
  if (content.viewOnceMessageV2?.message) return content.viewOnceMessageV2.message;
  if (content.documentWithCaptionMessage?.message) return content.documentWithCaptionMessage.message;
  if (content.templateMessage?.hydratedTemplate) return content.templateMessage.hydratedTemplate;
  if (content.buttonsMessage) return content.buttonsMessage;
  if (content.listMessage) return content.listMessage;
  return content;
}

export function getContextInfo(messageContent) {
  if (!messageContent || typeof messageContent !== 'object') return {};
  for (const value of Object.values(messageContent)) {
    if (value && typeof value === 'object' && value.contextInfo) {
      return value.contextInfo;
    }
  }
  return {};
}

/**
 * Normalize a raw Baileys WAMessage into the record shape consumed by the
 * message queue AND the history store. Pure + synchronous: it extracts body
 * and infers media type from the message SHAPE but does NOT download media —
 * `mediaUrls` is always `[]`. The caller (bridge.js) downloads media for live
 * `messages.upsert` only; history-sync records keep `mediaUrls: []` (avoids
 * re-downloading a large backfill on connect).
 *
 * Works identically for `messages.upsert` and `messaging-history.set` payloads
 * (both carry the same WAMessage shape).
 */
export function normalizeMessage(msg, { botIds = [], messageContent = null } = {}) {
  const chatId = msg?.key?.remoteJid;
  if (!chatId) return null;

  const content = messageContent || getMessageContent(msg);
  const contextInfo = getContextInfo(content);
  const mentionedIds = Array.from(
    new Set((contextInfo?.mentionedJid || []).map(normalizeWhatsAppId).filter(Boolean)),
  );
  const quotedMessageId = contextInfo?.stanzaId || null;
  const quotedParticipant = normalizeWhatsAppId(contextInfo?.participant || '') || null;
  const quotedRemoteJid = normalizeWhatsAppId(contextInfo?.remoteJid || '') || null;
  const hasQuotedMessage = !!contextInfo?.quotedMessage;

  const senderId = msg.key.participant || chatId;
  const isGroup = chatId.endsWith('@g.us');
  const senderNumber = senderId.replace(/@.*/, '');

  let body = '';
  let hasMedia = false;
  let mediaType = '';

  if (content.conversation) {
    body = content.conversation;
  } else if (content.extendedTextMessage?.text) {
    body = content.extendedTextMessage.text;
  } else if (content.imageMessage) {
    body = content.imageMessage.caption || '';
    hasMedia = true;
    mediaType = 'image';
  } else if (content.videoMessage) {
    body = content.videoMessage.caption || '';
    hasMedia = true;
    mediaType = 'video';
  } else if (content.audioMessage || content.pttMessage) {
    hasMedia = true;
    mediaType = content.pttMessage ? 'ptt' : 'audio';
  } else if (content.documentMessage) {
    body = content.documentMessage.caption || '';
    hasMedia = true;
    mediaType = 'document';
  }

  // Media without a caption still needs a non-empty body for downstream display.
  if (hasMedia && !body) {
    body = `[${mediaType} received]`;
  }

  return {
    messageId: msg.key.id,
    chatId,
    senderId,
    fromMe: !!msg.key.fromMe,
    senderName: msg.pushName || senderNumber,
    chatName: isGroup ? (chatId.split('@')[0]) : (msg.pushName || senderNumber),
    isGroup,
    body,
    hasMedia,
    mediaType,
    mediaUrls: [],
    mentionedIds,
    quotedMessageId,
    quotedParticipant,
    quotedRemoteJid,
    hasQuotedMessage,
    botIds,
    timestamp: msg.messageTimestamp,
  };
}

// --- MessageStore ----------------------------------------------------------

const STORE_VERSION = 1;

export class MessageStore {
  constructor({
    storeFile,
    maxPerChat = 1000,
    maxTotal = 50000,
    flushDebounceMs = 3000,
  } = {}) {
    this.storeFile = storeFile || null;
    this.maxPerChat = maxPerChat;
    this.maxTotal = maxTotal;
    this.flushDebounceMs = flushDebounceMs;
    this.chats = new Map(); // jid -> record[] (ascending by timestamp)
    this._saveTimer = null;
    this._dirty = false;
  }

  /**
   * Insert (or ignore, if the messageId already exists) a normalized record,
   * keeping each chat's array ascending by timestamp, enforcing the per-chat
   * and global caps, and scheduling a debounced persistence flush.
   */
  upsert(record) {
    if (!record || !record.chatId || !record.messageId) return false;

    let arr = this.chats.get(record.chatId);
    if (!arr) {
      arr = [];
      this.chats.set(record.chatId, arr);
    }

    // Dedupe by messageId (history-sync + live upsert can overlap).
    if (arr.some((r) => r.messageId === record.messageId)) return false;

    arr.push(record);
    arr.sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0));

    // Per-chat cap: drop oldest from the front.
    if (arr.length > this.maxPerChat) {
      arr.splice(0, arr.length - this.maxPerChat);
    }

    this._enforceTotalCap();
    this._scheduleSave();
    return true;
  }

  /**
   * If the total record count exceeds the cap, evict the globally-oldest
   * records (smallest timestamp across all chats) until under the cap.
   */
  _enforceTotalCap() {
    let total = 0;
    for (const arr of this.chats.values()) total += arr.length;

    while (total > this.maxTotal && this.chats.size > 0) {
      // Find the chat holding the single oldest record.
      let oldestJid = null;
      let oldestTs = Infinity;
      for (const [jid, arr] of this.chats.entries()) {
        if (arr.length && (arr[0].timestamp || 0) < oldestTs) {
          oldestTs = arr[0].timestamp || 0;
          oldestJid = jid;
        }
      }
      if (oldestJid === null) break;

      const arr = this.chats.get(oldestJid);
      arr.shift();
      total -= 1;
      if (arr.length === 0) this.chats.delete(oldestJid);
    }
  }

  /**
   * Return up to `limit` records for `jid`, NEWEST first.
   * The stored array is ascending, so take the tail and reverse.
   */
  getHistory(jid, limit = 50) {
    const arr = this.chats.get(jid);
    if (!arr || arr.length === 0) return [];
    const slice = arr.slice(-limit);
    slice.reverse();
    return slice;
  }

  /**
   * Return a recency-sorted chat list for discovery. `resolveName(jid)` and
   * `resolvePhone(jid)` are optional callbacks supplied by bridge.js (contact
   * names from history-sync + the LID→phone map).
   */
  listChats({ resolveName = null, resolvePhone = null } = {}) {
    const out = [];
    for (const [jid, recs] of this.chats.entries()) {
      const last = recs[recs.length - 1] || null;
      const isGroup = jid.endsWith('@g.us');
      const fallbackName = isGroup ? jid.split('@')[0] : jid.replace(/@.*/, '');
      out.push({
        jid,
        name: (resolveName ? resolveName(jid) : null) || fallbackName,
        phone: resolvePhone ? (resolvePhone(jid) || null) : null,
        isGroup,
        messageCount: recs.length,
        lastTimestamp: last?.timestamp || null,
        lastPreview: last ? String(last.body || '').slice(0, 120) : null,
      });
    }
    out.sort((a, b) => (b.lastTimestamp || 0) - (a.lastTimestamp || 0));
    return out;
  }

  /** Load persisted records from disk. No-op if the file is absent/malformed. */
  load() {
    if (!this.storeFile || !existsSync(this.storeFile)) return;
    try {
      const parsed = JSON.parse(readFileSync(this.storeFile, 'utf8'));
      const chats = parsed && parsed.chats ? parsed.chats : {};
      this.chats = new Map();
      for (const [jid, recs] of Object.entries(chats)) {
        if (!Array.isArray(recs)) continue;
        const arr = recs
          .filter((r) => r && r.messageId)
          .sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0));
        if (arr.length) this.chats.set(jid, arr);
      }
      this._enforceTotalCap();
    } catch {
      // Malformed store file — start empty rather than crashing the bridge.
      this.chats = new Map();
    }
  }

  /** Write the store to disk synchronously (used by the debounced timer + shutdown). */
  flush() {
    if (!this.storeFile) return;
    if (this._saveTimer) {
      clearTimeout(this._saveTimer);
      this._saveTimer = null;
    }
    const chats = {};
    for (const [jid, recs] of this.chats.entries()) chats[jid] = recs;
    try {
      writeFileSync(this.storeFile, JSON.stringify({ version: STORE_VERSION, chats }));
      this._dirty = false;
    } catch {
      // Non-fatal: keep the in-memory store; retry on next mutation.
    }
  }

  _scheduleSave() {
    this._dirty = true;
    if (!this.storeFile || this._saveTimer) return;
    this._saveTimer = setTimeout(() => {
      this._saveTimer = null;
      if (this._dirty) this.flush();
    }, this.flushDebounceMs);
  }
}
