'use strict';

// Standalone, offline contract tests: node scripts/test_google_drive_report_upload.cjs
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'google_drive_report_upload.gs'), 'utf8');
const ACTION = 'upload_reserved_report_pdf';
const SECRET = 'offline-test-secret-with-at-least-32-characters';
const FOLDER = 'configured_folder_123';
const NOW = 1800000000;
const MAX_PDF = 10 * 1024 * 1024;
const NONCE_PREFIX = 'REPORT_UPLOAD_NONCE_';
const rejection = { ok: false, action: ACTION, error: 'PDF upload rejected' };

function harness(options = {}) {
  const state = { writes: [], folders: [], sharing: 0, traffic: 0, locked: false,
    nonces: new Map(), propertyWrites: [], propertyDeletes: [], cacheAccesses: 0, now: NOW };
  const properties = options.propertyStore ||
    Object.assign({ REPORT_UPLOAD_SECRET: SECRET, REPORT_FOLDER_ID: FOLDER }, options.properties);
  state.properties = properties;
  const forbidden = () => {
    state.traffic += 1;
    throw new Error('External traffic forbidden');
  };
  const sharing = () => {
    state.sharing += 1;
    throw new Error('Sharing forbidden');
  };
  const context = vm.createContext({
    Date: class extends Date { static now() { return state.now * 1000; } },
    PropertiesService: { getScriptProperties: () => ({
      getProperty(key) {
        if (key.startsWith(NONCE_PREFIX)) assert.equal(state.locked, true);
        return properties[key] ?? null;
      },
      getProperties() { assert.equal(state.locked, true); return { ...properties }; },
      setProperty(key, value) {
        assert.equal(state.locked, true);
        assert.ok(key.startsWith(NONCE_PREFIX));
        assert.equal(value, String(state.now + 601));
        if (options.failedPropertyWrite) throw new Error('Properties write failed');
        state.propertyWrites.push(key);
        properties[key] = value;
      },
      deleteProperty(key) {
        assert.equal(state.locked, true);
        assert.ok(key.startsWith(NONCE_PREFIX));
        state.propertyDeletes.push(key);
        delete properties[key];
      }
    }) },
    Utilities: {
      Charset: { UTF_8: 'UTF-8' },
      computeHmacSha256Signature(payload, secret, charset) {
        assert.equal(charset, 'UTF-8');
        return Array.from(crypto.createHmac('sha256', secret).update(payload, 'utf8').digest(),
          byte => byte > 127 ? byte - 256 : byte);
      },
      base64Decode: value => Array.from(Buffer.from(value, 'base64'), byte => byte > 127 ? byte - 256 : byte),
      newBlob: (bytes, mime, filename) => ({ bytes, mime, filename })
    },
    LockService: { getScriptLock: () => ({
      tryLock(milliseconds) {
        assert.equal(milliseconds, 5000);
        if (options.lockUnavailable) return false;
        assert.equal(state.locked, false);
        state.locked = true;
        return true;
      },
      releaseLock() { assert.equal(state.locked, true); state.locked = false; }
    }) },
    CacheService: { getScriptCache() {
      state.cacheAccesses += 1;
      throw new Error('Cache must not be used for replay protection');
    } },
    DriveApp: { getFolderById(id) {
      assert.ok(state.propertyWrites.length > 0, 'Nonce must be persisted before any Drive effect');
      assert.equal(properties[state.propertyWrites.at(-1)], String(state.now + 601));
      state.folders.push(id);
      assert.equal(id, FOLDER);
      if (options.invalidFolder) throw new Error('Folder unavailable');
      return {
        isTrashed: () => Boolean(options.trashedFolder),
        setSharing: sharing, addViewer: sharing, addEditor: sharing,
        createFile(blob) {
          if (options.failedWrite) throw new Error('Drive write failed');
          state.writes.push(blob);
          return { getId: () => 'created_file_123', setSharing: sharing, addViewer: sharing, addEditor: sharing };
        }
      };
    } },
    ContentService: {
      MimeType: { JSON: 'application/json' },
      createTextOutput: text => ({ setMimeType(mime) { assert.equal(mime, 'application/json'); return { text }; } })
    },
    UrlFetchApp: { fetch: forbidden, fetchAll: forbidden },
    fetch: forbidden,
    console: { log() { throw new Error('Logging forbidden'); }, error() { throw new Error('Logging forbidden'); } },
    Logger: { log() { throw new Error('Logging forbidden'); } }
  });
  vm.runInContext(source, context, { filename: 'google_drive_report_upload.gs' });
  return {
    state,
    post(envelope) {
      const event = { postData: { contents: typeof envelope === 'string' ? envelope : JSON.stringify(envelope) } };
      return JSON.parse(context.doPost(event).text);
    },
    get: () => JSON.parse(context.doGet().text)
  };
}

function data(overrides = {}) {
  return Object.assign({
    action: ACTION,
    timestamp: NOW,
    request_id: crypto.randomBytes(16).toString('hex'),
    filename: '予約レポート.pdf',
    folder_id: FOLDER,
    pdf_base64: Buffer.from('%PDF-1.7\nOffline test\n%%EOF\n').toString('base64')
  }, overrides);
}

function signed(value) {
  const payload = typeof value === 'string' ? value : JSON.stringify(value);
  return { payload, signature: crypto.createHmac('sha256', SECRET).update(payload, 'utf8').digest('hex') };
}

let passed = 0;
function test(name, run) {
  run();
  passed += 1;
  process.stdout.write(`PASS ${name}\n`);
}

function rejected(name, value, options = {}, envelopeTransform = value => value) {
  test(name, () => {
    const app = harness(options);
    assert.deepEqual(app.post(envelopeTransform(signed(value))), rejection);
    assert.equal(app.state.writes.length, 0);
    assert.equal(app.state.sharing, 0);
    assert.equal(app.state.traffic, 0);
    assert.equal(app.state.locked, false);
  });
}

test('success uses configured folder, UTF-8 HMAC and original PDF without sharing or traffic', () => {
  const app = harness();
  const input = data();
  assert.deepEqual(app.post(signed(input)), {
    ok: true, action: ACTION, request_id: input.request_id, folder_id: FOLDER, file_id: 'created_file_123'
  });
  assert.deepEqual(app.state.folders, [FOLDER]);
  assert.equal(app.state.writes.length, 1);
  const blob = app.state.writes[0];
  assert.equal(blob.mime, 'application/pdf');
  assert.equal(blob.filename, input.filename);
  assert.equal(Buffer.from(blob.bytes).toString('base64'), input.pdf_base64);
  assert.equal(app.state.sharing, 0);
  assert.equal(app.state.traffic, 0);
});

test('replay is rejected before a second Drive lookup or write', () => {
  const app = harness();
  const envelope = signed(data());
  assert.equal(app.post(envelope).ok, true);
  assert.deepEqual(app.post(envelope), rejection);
  assert.equal(app.state.writes.length, 1);
  assert.equal(app.state.folders.length, 1);
});

test('cache eviction cannot permit replay of a persisted nonce', () => {
  const app = harness();
  const input = data();
  assert.equal(app.post(signed(input)).ok, true);
  assert.equal(app.state.properties[NONCE_PREFIX + input.request_id], String(NOW + 601));
  app.state.nonces.set('report-upload:' + input.request_id, '1');
  app.state.nonces.clear(); // Simulate early eviction of every cache entry.
  assert.deepEqual(app.post(signed(input)), rejection);
  assert.equal(app.state.cacheAccesses, 0);
  assert.equal(app.state.propertyWrites.length, 1);
  assert.equal(app.state.folders.length, 1);
});

test('fresh execution and newly signed request with the same active ID remain rejected', () => {
  const first = harness();
  const input = data();
  const envelope = signed(input);
  assert.equal(first.post(envelope).ok, true);
  const next = harness({ propertyStore: first.state.properties });
  next.state.now = NOW + 100;
  assert.deepEqual(next.post(envelope), rejection);
  assert.deepEqual(next.post(signed({ ...input, timestamp: NOW + 100 })), rejection);
  assert.equal(next.state.folders.length, 0);
  assert.equal(next.state.propertyWrites.length, 0);
  assert.equal(next.state.locked, false);
});

test('expiry cleanup permits expired ID, retains active entries and unrelated properties', () => {
  const input = data();
  const expiredKey = NONCE_PREFIX + '1'.repeat(32);
  const activeKey = NONCE_PREFIX + '2'.repeat(32);
  const app = harness({ properties: {
    [NONCE_PREFIX + input.request_id]: String(NOW),
    [expiredKey]: String(NOW - 1),
    [activeKey]: String(NOW + 1),
    OTHER_SETTING: 'preserve-me',
    OTHER_EXPIRATION: String(NOW - 100)
  } });
  assert.equal(app.post(signed(input)).ok, true);
  assert.deepEqual(app.state.properties, {
    REPORT_UPLOAD_SECRET: SECRET, REPORT_FOLDER_ID: FOLDER,
    [NONCE_PREFIX + input.request_id]: String(NOW + 601),
    [activeKey]: String(NOW + 1),
    OTHER_SETTING: 'preserve-me', OTHER_EXPIRATION: String(NOW - 100)
  });
  assert.deepEqual(new Set(app.state.propertyDeletes), new Set([NONCE_PREFIX + input.request_id, expiredKey]));
});

test('nonce becomes reusable at its stored expiry, but not one second before', () => {
  const app = harness();
  const input = data();
  assert.equal(app.post(signed(input)).ok, true);
  app.state.now = NOW + 600;
  assert.deepEqual(app.post(signed({ ...input, timestamp: app.state.now })), rejection);
  app.state.now = NOW + 601;
  assert.equal(app.post(signed({ ...input, timestamp: app.state.now })).ok, true);
  assert.equal(app.state.properties[NONCE_PREFIX + input.request_id], String(NOW + 1202));
  assert.equal(app.state.writes.length, 2);
});

test('ledger accepts entry 300, rejects entry 301 and recovers after expiry cleanup', () => {
  const properties = {};
  for (let index = 0; index < 299; index += 1) {
    properties[NONCE_PREFIX + index.toString(16).padStart(32, '0')] = String(NOW + 1);
  }
  const app = harness({ properties });
  assert.equal(app.post(signed(data())).ok, true);
  const full = { ...app.state.properties };
  assert.deepEqual(app.post(signed(data())), rejection);
  assert.deepEqual(app.state.properties, full);
  assert.equal(app.state.folders.length, 1);
  app.state.now = NOW + 1;
  assert.equal(app.post(signed(data())).ok, true);
  assert.equal(Object.keys(app.state.properties).filter(key => key.startsWith(NONCE_PREFIX)).length, 2);
  assert.equal(app.state.propertyDeletes.length, 299);
});

test('malformed stored nonce fails closed and is not deleted', () => {
  const input = data();
  const key = NONCE_PREFIX + input.request_id;
  const app = harness({ properties: { [key]: 'not-an-expiration' } });
  assert.deepEqual(app.post(signed(input)), rejection);
  assert.equal(app.state.properties[key], 'not-an-expiration');
  assert.equal(app.state.propertyDeletes.length, 0);
  assert.equal(app.state.propertyWrites.length, 0);
  assert.equal(app.state.folders.length, 0);
});

test('lock failure performs no property mutation or Drive effect, including expiry cleanup', () => {
  const app = harness({ lockUnavailable: true, properties: {
    [NONCE_PREFIX + '3'.repeat(32)]: String(NOW - 1)
  } });
  const before = { ...app.state.properties };
  assert.deepEqual(app.post(signed(data())), rejection);
  assert.deepEqual(app.state.properties, before);
  assert.equal(app.state.propertyWrites.length, 0);
  assert.equal(app.state.propertyDeletes.length, 0);
  assert.equal(app.state.folders.length, 0);
  assert.equal(app.state.locked, false);
});

test('property persistence failure prevents any Drive effect and releases lock', () => {
  const app = harness({ failedPropertyWrite: true });
  assert.deepEqual(app.post(signed(data())), rejection);
  assert.equal(app.state.propertyWrites.length, 0);
  assert.equal(app.state.folders.length, 0);
  assert.equal(app.state.locked, false);
});

rejected('bad signature', data(), {}, envelope => ({ ...envelope, signature: '0'.repeat(64) }));
rejected('uppercase signature', data(), {}, envelope => ({ ...envelope, signature: envelope.signature.toUpperCase() }));
rejected('expired timestamp', data({ timestamp: NOW - 301 }));
rejected('future timestamp', data({ timestamp: NOW + 301 }));
rejected('fractional timestamp', data({ timestamp: NOW + 0.5 }));
rejected('string timestamp', data({ timestamp: String(NOW) }));
rejected('wrong folder', data({ folder_id: 'attacker_folder' }));
rejected('wrong action', data({ action: 'send_chat' }));
rejected('invalid request ID', data({ request_id: 'A'.repeat(32) }));
rejected('request ID trailing newline', data({ request_id: 'a'.repeat(32) + '\n' }));
rejected('extra inner field', data({ url: 'https://never-contact.invalid' }));
rejected('missing inner field', Object.fromEntries(Object.entries(data()).filter(([key]) => key !== 'filename')));
rejected('extra outer field', data(), {}, envelope => ({ ...envelope, extra: true }));
rejected('payload must be a string', data(), {}, envelope => ({ ...envelope, payload: {} }));
rejected('oversized payload string', ' '.repeat(15 * 1024 * 1024 + 1));
rejected('oversized PDF decoded bytes', data({ pdf_base64: Buffer.concat([
  Buffer.from('%PDF-'), Buffer.alloc(MAX_PDF - 4)
]).toString('base64') }));
rejected('oversized PDF encoded length', data({ pdf_base64: 'A'.repeat(4 * Math.ceil(MAX_PDF / 3) + 4) }));
rejected('invalid PDF header', data({ pdf_base64: Buffer.from('not a PDF').toString('base64') }));
rejected('short PDF', data({ pdf_base64: Buffer.from('%PDF').toString('base64') }));
rejected('invalid base64', data({ pdf_base64: '%PDF-!!!!' }));
rejected('base64 trailing newline', data({ pdf_base64: 'JVBERi0AAAA\n' }));
rejected('base64 data URI', data({ pdf_base64: 'data:application/pdf;base64,JVBERi0=' }));
rejected('empty PDF', data({ pdf_base64: '' }));
for (const filename of ['../report.pdf', 'folder\\report.pdf', 'bad\nname.pdf', 'bad\u007fname.pdf',
  'bad:name.pdf', 'report.txt', '.pdf', ' report.pdf', 'x'.repeat(177) + '.pdf']) {
  rejected(`unsafe filename ${JSON.stringify(filename)}`, data({ filename }));
}
rejected('missing secret', data(), { properties: { REPORT_UPLOAD_SECRET: null } });
rejected('short secret', data(), { properties: { REPORT_UPLOAD_SECRET: 'x'.repeat(31) } });
rejected('missing folder configuration', data(), { properties: { REPORT_FOLDER_ID: null } });
rejected('invalid folder configuration', data(), { properties: { REPORT_FOLDER_ID: '../folder' } });
rejected('folder configuration trailing newline', data(), { properties: { REPORT_FOLDER_ID: FOLDER + '\n' } });
rejected('unavailable Drive folder', data(), { invalidFolder: true });
rejected('trashed Drive folder', data(), { trashedFolder: true });
rejected('lock unavailable', data(), { lockUnavailable: true });

test('failed Drive write still consumes nonce', () => {
  const app = harness({ failedWrite: true });
  const envelope = signed(data());
  assert.deepEqual(app.post(envelope), rejection);
  assert.deepEqual(app.post(envelope), rejection);
  assert.equal(app.state.folders.length, 1);
  assert.equal(app.state.writes.length, 0);
});

test('timestamp tolerance boundaries accepted', () => {
  for (const offset of [-300, 300]) {
    assert.equal(harness().post(signed(data({ timestamp: NOW + offset }))).ok, true);
  }
});

test('10 MiB PDF and 180-character filename accepted', () => {
  const bytes = Buffer.alloc(MAX_PDF, 0xff);
  bytes.write('%PDF-');
  const app = harness();
  assert.equal(app.post(signed(data({ filename: 'x'.repeat(176) + '.pdf', pdf_base64: bytes.toString('base64') }))).ok, true);
  assert.equal(app.state.writes[0].bytes.length, MAX_PDF);
});

test('malformed input returns only generic error', () => {
  const app = harness();
  for (const input of ['{', 'null', '[]', '{}', signed('null'), signed('{')]) {
    assert.deepEqual(app.post(input), rejection);
  }
  assert.equal(app.state.writes.length, 0);
  assert.equal(app.state.traffic, 0);
});

test('GET exposes capabilities only, without configuration', () => {
  const app = harness({ properties: { REPORT_UPLOAD_SECRET: null, REPORT_FOLDER_ID: null } });
  assert.deepEqual(app.get(), { version: 1, capabilities: [ACTION] });
  assert.equal(app.state.folders.length, 0);
  assert.equal(app.state.traffic, 0);
});

test('GAS has no external fetch, Chat, logging or sharing API references', () => {
  assert.doesNotMatch(source, /UrlFetchApp|\bfetch\s*\(|\b(?:setSharing|addViewer|addEditor|addCommenter|setOwner)\s*\(|\b(?:Logger|console)\s*\./);
});

process.stdout.write(`\n${passed} offline tests passed. No uploads or network requests performed.\n`);