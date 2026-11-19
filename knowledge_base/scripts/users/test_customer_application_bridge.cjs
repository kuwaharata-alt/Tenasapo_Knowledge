'use strict';

// Optional, dependency-free: node scripts/users/test_customer_application_bridge.cjs
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, 'customer_application_bridge.gs'), 'utf8');
const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, 'appsscript.customer_application.json'), 'utf8'));
const application = '11111111-2222-4333-8444-555555555555';
const attempt = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee';
const mailbox = 'bridge@example.com';
const recipient = 'invitee@example.com';
const marker = 'Nexus連携ID: <nexus-application-' + application + '@customer-applications.invalid>';
const signedBytes = buffer => Array.from(buffer, byte => byte > 127 ? byte - 256 : byte);
const plain = value => JSON.parse(JSON.stringify(value));
const fixtures = [];
const serverCodes = ['unauthorized', 'replay', 'invalid_configuration', 'backend_disabled',
  'body_too_large', 'invalid_page', 'cursor_conflict', 'https_required',
  'transaction_not_supported', 'invalid_request', 'service_unavailable'];
const privateError = 'PRIVATE REMOTE ERROR PRIVATE ACK ERROR ' + mailbox + ' ' + recipient +
  ' https://nexus.example.com ' + 's'.repeat(32) + ' ' + marker;

function safeLogs(state) {
  for (const entry of state.logs) {
    const [label, value] = entry;
    if (['NEXUS_BRIDGE_OK', 'NEXUS_DRAFT_UNCERTAIN', 'NETWORK_ERROR'].includes(label)) {
      assert.equal(entry.length, 1);
    } else if (['HTTP_STATUS', 'IMPORTED', 'PROCESSED'].includes(label)) {
      assert.equal(entry.length, 2);
      assert(Number.isSafeInteger(value) && value >= 0);
      if (label === 'HTTP_STATUS') assert(value >= 100 && value <= 599);
    } else {
      assert.equal(label, 'HTTP_ERROR'); assert.equal(entry.length, 2);
      assert([...serverCodes, 'UNKNOWN_ERROR'].includes(value));
    }
  }
  const text = JSON.stringify(state.logs);
  for (const forbidden of [mailbox, recipient, marker, application, attempt, 's'.repeat(32),
    'https://', 'http://', 'PRIVATE', 'Invitation', 'Application ', 'X-Nexus-', 'draft-1']) {
    assert(!text.includes(forbidden), 'Sensitive value must not be logged');
  }
}

function fixture() {
  const properties = new Map(Object.entries({
    NEXUS_BASE_URL: 'https://nexus.example.com', NEXUS_GAS_SECRET: 's'.repeat(32),
    NEXUS_ENVIRONMENT: 'development', NEXUS_GAS_MAILBOX: mailbox
  }));
  const state = {
    calls: [], creates: [], drafts: [], writes: [], triggers: [], deleted: [],
    locked: false, authorized: false, claimed: false, ackFailure: false,
    createFailure: false, createPersistsOnFailure: false, noJob: false,
    effective: mailbox, sheetCalls: 0, gmailCalls: 0, sessionCalls: 0,
    nextRow: 2, rows: [], lastRow: 1, acked: [], logs: []
  };
  fixtures.push(state);
  state.job = {
    application_id: application, attempt_id: attempt, mailbox,
    message_id: '<nexus-application-' + application + '@customer-applications.invalid>',
    allow_create: true, to: recipient, subject: 'Invitation', body: 'Application ' + application
  };
  function draft(id, body, to = recipient, cc = '', bcc = '') {
    return {
      getId: () => id,
      getMessage: () => ({getTo: () => to, getCc: () => cc, getBcc: () => bcc,
        getPlainBody: () => body, getBody: () => body})
    };
  }
  state.draft = draft;
  const context = {
    console: {log: (...args) => state.logs.push(args)},
    Utilities: {
      DigestAlgorithm: {SHA_256: 'sha256'}, Charset: {UTF_8: 'UTF-8'},
      computeDigest: (_, text) => signedBytes(crypto.createHash('sha256').update(text, 'utf8').digest()),
      computeHmacSha256Signature: (text, secret) => signedBytes(crypto.createHmac('sha256', secret).update(text, 'utf8').digest()),
      newBlob: text => ({getBytes: () => signedBytes(Buffer.from(text, 'utf8'))}),
      getUuid: () => '01234567-89ab-4cde-8fab-0123456789ab'
    },
    PropertiesService: {getScriptProperties: () => ({
      getProperty: key => properties.has(key) ? properties.get(key) : null,
      setProperty: (key, value) => {
        state.writes.push({key, value}); properties.set(key, value);
      }
    })},
    LockService: {getScriptLock: () => ({
      tryLock: () => { assert.equal(state.locked, false); state.locked = true; return true; },
      releaseLock: () => { state.locked = false; }
    })},
    Session: {getEffectiveUser: () => { state.sessionCalls++; return {getEmail: () => state.effective}; }},
    GmailApp: {
      getDrafts: () => { assert.equal(state.locked, true); state.gmailCalls++; return state.drafts; },
      createDraft: (...args) => {
        assert.equal(args.length, 3); assert.equal(state.locked, true);
        assert.equal(args[0], recipient);
        const evidence = JSON.parse(state.writes[state.writes.length - 1].value);
        assert.equal(evidence.state, 'uncertain'); assert.equal(evidence.mailbox, mailbox);
        assert(state.writes.some(write => JSON.parse(write.value).state === 'attempted'));
        state.creates.push(args);
        const created = draft('draft-1', args[2]);
        if (!state.createFailure || state.createPersistsOnFailure) state.drafts.push(created);
        if (state.createFailure) throw new Error(privateError);
        return created;
      },
      sendEmail: () => assert.fail('Mail must never be sent')
    },
    SpreadsheetApp: {openById: id => {
      state.sheetCalls++;
      assert.equal(id, '1JaWW0c_CE9W5_egLvoqrWcnwuIbb6QCERBRrQvAE5Pg');
      return {getId: () => id, getSheetById: gid => {
        assert.equal(gid, 1583871824);
        return {getSheetId: () => gid, getLastRow: () => state.lastRow,
          getRange: (row, column, count, columns) => {
            assert.equal(column, 1); assert.equal(columns, 6); assert(count <= 200);
            return {getDisplayValues: () => row === 1 ? [['タイムスタンプ', 'メールアドレス', '会社名', '部署名', '契約者氏名', '契約メールアドレス']] : state.rows.slice(0, count)};
          }};
      }};
    }},
    ScriptApp: {
      getProjectTriggers: () => state.triggers.slice(),
      newTrigger: handler => ({timeBased: () => ({everyMinutes: minutes => ({create: () => {
        assert.equal(minutes, 5); state.triggers.push({getHandlerFunction: () => handler});
      }})})}),
      deleteTrigger: trigger => {state.deleted.push(trigger); state.triggers.splice(state.triggers.indexOf(trigger), 1);}
    },
    UrlFetchApp: {fetch: (url, options) => {
      assert.equal(options.followRedirects, false); assert.equal(options.validateHttpsCertificates, true);
      assert.equal(options.method, 'post'); assert.equal(options.contentType, 'application/json');
      const requestPath = new URL(url).pathname;
      const timestamp = options.headers['X-Nexus-Timestamp'];
      const nonce = options.headers['X-Nexus-Nonce'];
      const canonical = ['nexus-gas-v1', properties.get('NEXUS_ENVIRONMENT'), 'POST', requestPath,
        timestamp, nonce, crypto.createHash('sha256').update(options.payload, 'utf8').digest('hex')].join('\n');
      assert.match(timestamp, /^\d+$/); assert.match(nonce, /^[0-9a-f]{32}$/);
      assert.equal(options.headers['X-Nexus-Signature'], crypto.createHmac('sha256', properties.get('NEXUS_GAS_SECRET')).update(canonical).digest('hex'));
      const payload = JSON.parse(options.payload);
      state.calls.push({path: requestPath, payload});
      if (state.networkFailure) throw new Error(privateError);
      if (Object.hasOwn(state, 'httpCode')) return {
        getResponseCode: () => state.httpCode,
        getContentText: () => {
          if (state.responseReadFailure) throw new Error(privateError);
          return state.responseText;
        }
      };
      let result;
      if (requestPath.endsWith('/status/')) {
        assert.deepEqual(payload, {});
        result = {ok: true, service: 'nexus-customer-application-gas-v1', environment: 'development', next_row: state.nextRow, mailbox, ...state.pingOverride};
      } else if (requestPath.endsWith('/claim/')) {
        assert.deepEqual(Object.keys(payload), ['exclude_ids']); assert(payload.exclude_ids.length <= 20);
        if (state.jobs) result = {ok: true, job: state.jobs.find(job => !payload.exclude_ids.includes(job.application_id)) || null};
        else if (state.noJob || payload.exclude_ids.includes(application)) result = {ok: true, job: null};
        else {
          const job = {...state.job};
          if (state.authorized) {job.allow_create = false; delete job.subject; delete job.body;}
          state.authorized = true; result = {ok: true, job};
        }
      } else if (requestPath.endsWith('/ack/')) {
        state.acked.push(payload);
        if (state.ackFailure) throw new Error(privateError);
        result = {ok: true, application_id: payload.application_id, status: payload.status};
      } else if (requestPath.endsWith('/import/')) {
        result = {ok: true, next_row: payload.next_row, imported: payload.applications.length};
      } else assert.fail('Unknown endpoint');
      return {getResponseCode: () => 200, getContentText: () => JSON.stringify(result)};
    }}
  };
  vm.createContext(context); vm.runInContext(source, context);
  return {context, state, properties};
}

let passed = 0;
function test(name, fn) {
  fixtures.length = 0;
  fn(); fixtures.forEach(safeLogs);
  passed++; console.log('PASS ' + name);
}
function fixedFailure(fn, context) {
  assert.throws(fn, error => error.message === context.NEXUS_BRIDGE_.error);
}

test('HMAC SHA256 published vector and UTF-8 request canonicalization', () => {
  const {context: c} = fixture();
  assert.equal(c.nexusHex_(c.Utilities.computeHmacSha256Signature('Hi There', '\x0b'.repeat(20))),
    'b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7');
  const body = JSON.stringify({text: '日本語'});
  const config = c.nexusConfig_();
  const canonical = ['nexus-gas-v1', 'development', 'POST', c.NEXUS_BRIDGE_.paths.import, '1795046400',
    '0123456789ab4cde8fab0123456789ab', crypto.createHash('sha256').update(body).digest('hex')].join('\n');
  assert.equal(c.nexusSignature_(config, c.NEXUS_BRIDGE_.paths.import, '1795046400', '0123456789ab4cde8fab0123456789ab', body),
    crypto.createHmac('sha256', 's'.repeat(32)).update(canonical).digest('hex'));
});

test('ping has no sheet/Gmail/Session side effects', () => {
  const {context: c, state: s} = fixture();
  assert.deepEqual(plain(c.testNexusBridge()), {ok: true});
  assert.equal(s.calls.length, 1); assert.equal(s.gmailCalls + s.sheetCalls + s.sessionCalls, 0);
  assert.equal(s.writes.length, 0); assert.equal(s.triggers.length, 0);
  assert.deepEqual(s.logs, [['NEXUS_BRIDGE_OK']]);
});

test('invalid origins and redirects fail closed', () => {
  for (const origin of ['http://example.com', 'https://example.com/', 'https://a.com/path',
    'https://user@a.com', 'https://a.com?x=1', 'https://a.com#x', 'https://a.com\n', 'https://a.com:99999']) {
    const {context: c, state: s, properties: p} = fixture(); p.set('NEXUS_BASE_URL', origin);
    fixedFailure(() => c.testNexusBridge(), c); assert.equal(s.calls.length, 0);
  }
  const {context: c, state: s} = fixture(); s.httpCode = 302;
  fixedFailure(() => c.processNexusInvitationDrafts(), c); assert.equal(s.gmailCalls, 0);
});

test('mailbox and server identity checked before import/claim', () => {
  for (const override of [{mailbox: 'other@example.com'}, {environment: 'production'}, {service: 'other'}, {next_row: 1}]) {
    const {context: c, state: s} = fixture(); s.pingOverride = override;
    fixedFailure(() => c.runNexusBridge(), c); assert.equal(s.sheetCalls + s.gmailCalls, 0);
    assert.equal(s.calls.length, 1);
  }
  for (const effective of ['', 'OTHER@example.com']) {
    const {context: c, state: s} = fixture(); s.effective = effective;
    fixedFailure(() => c.runNexusBridge(), c); assert.equal(s.calls.length, 0);
  }
});

test('A-F import uses server cursor and includes trailing blank rows in cursor', () => {
  const {context: c, state: s} = fixture(); s.nextRow = 17; s.lastRow = 19;
  s.rows = [['日時', 'applicant@example.com', '会社', '部署', '氏名', recipient], Array(6).fill(''), Array(6).fill('')];
  c.syncNexusApplications();
  const payload = s.calls[1].payload;
  assert.equal(payload.start_row, 17); assert.equal(payload.next_row, 20);
  assert.equal(payload.sheet_id, '1583871824');
  assert.equal(payload.spreadsheet_id, c.NEXUS_BRIDGE_.spreadsheetId);
  assert.deepEqual(payload.headers, plain(c.NEXUS_BRIDGE_.headers));
  assert.equal(payload.applications.length, 1);
  assert.equal(payload.applications[0].email, recipient);
  assert.equal(payload.applications[0].source_key, c.NEXUS_BRIDGE_.spreadsheetId + ':1583871824:17');
  assert.equal(s.creates.length, 0);
  assert.deepEqual(s.logs, [['NEXUS_BRIDGE_OK'], ['IMPORTED', 1]]);
});

test('import page is capped at 200 and never rewinds the server cursor', () => {
  const {context: c, state: s} = fixture(); s.lastRow = 1000;
  s.rows = Array.from({length: 200}, () => Array(6).fill(''));
  c.syncNexusApplications(); assert.equal(s.calls[1].payload.next_row, 202);
  s.nextRow = 2000; c.syncNexusApplications();
  assert.equal(s.calls[3].payload.start_row, 2000); assert.equal(s.calls[3].payload.next_row, 2000);
});

test('only one creation; ACK failure retries stored ID even if draft disappears', () => {
  const {context: c, state: s} = fixture(); s.ackFailure = true;
  fixedFailure(() => c.processNexusInvitationDrafts(), c);
  assert.deepEqual(s.logs, [['NETWORK_ERROR']]);
  assert.equal(s.creates.length, 1); assert(s.creates[0][2].endsWith('\n\n' + marker));
  s.drafts = []; s.ackFailure = false; c.processNexusInvitationDrafts();
  assert.equal(s.creates.length, 1); assert.equal(s.acked[1].draft_id, 'draft-1');
  assert.deepEqual(s.logs, [['NETWORK_ERROR'], ['NEXUS_BRIDGE_OK'], ['PROCESSED', 1]]);
});

test('timeout never recreates; recovery only on subsequent execution', () => {
  for (const persisted of [false, true]) {
    const {context: c, state: s} = fixture(); s.createFailure = true; s.createPersistsOnFailure = persisted;
    assert.deepEqual(plain(c.processNexusInvitationDrafts()), {ok: false, processed: 1, uncertain: true});
    assert.equal(s.creates.length, 1);
    assert.deepEqual(s.logs, [['NEXUS_DRAFT_UNCERTAIN'], ['PROCESSED', 1]]);
    assert.equal(s.acked[0].status, 'uncertain'); assert.equal(s.gmailCalls, 1);
    s.createFailure = false; c.processNexusInvitationDrafts();
    assert.equal(s.creates.length, 1);
    assert.equal(s.acked[1].status, persisted ? 'created' : 'uncertain');
  }
});

test('pre-existing unique draft is reused before first create', () => {
  const {context: c, state: s} = fixture(); s.drafts = [s.draft('existing', 'Text\n' + marker)];
  c.processNexusInvitationDrafts(); assert.equal(s.creates.length, 0);
  assert.equal(s.acked[0].draft_id, 'existing');
  assert.deepEqual(s.logs, [['NEXUS_BRIDGE_OK'], ['PROCESSED', 1]]);
});

test('recovery without local state cannot create; wrong recipient/CC/BCC never match', () => {
  const {context: c, state: s} = fixture(); s.authorized = true;
  s.drafts = [s.draft('wrong-to', marker, 'other@example.com'), s.draft('cc', marker, recipient, 'cc@example.com'),
    s.draft('bcc', marker, recipient, '', 'bcc@example.com'), s.draft('partial', marker + 'suffix')];
  c.processNexusInvitationDrafts(); assert.equal(s.creates.length, 0); assert.equal(s.acked[0].status, 'uncertain');
});

test('duplicate markers and over-1000 drafts refuse creation', () => {
  for (const count of [2, 1001]) {
    const {context: c, state: s} = fixture(); s.drafts = Array.from({length: count}, (_, i) => s.draft('id-' + i, marker));
    c.processNexusInvitationDrafts(); assert.equal(s.creates.length, 0); assert.equal(s.acked[0].status, 'uncertain');
  }
});

test('local attempted state prevents create even with allow_create true', () => {
  const {context: c, state: s} = fixture(); const config = c.nexusConfig_();
  c.nexusRemember_(config, s.job, 'attempted', '');
  c.processNexusInvitationDrafts(); assert.equal(s.creates.length, 0); assert.equal(s.acked[0].status, 'uncertain');
});

test('invalid job schema rejected before Gmail access', () => {
  for (const override of [{application_id: application + '\n'}, {attempt_id: 'bad'}, {mailbox: 'other@example.com'},
    {message_id: '<other@example.com>'}, {allow_create: 'true'}, {to: recipient + '\n'},
    {to: recipient + ',other@example.com'}, {subject: 'bad\r\nheader'}, {subject: 'x'.repeat(251)},
    {body: 'x'.repeat(200001)}, {body: null}]) {
    const {context: c, state: s} = fixture(); Object.assign(s.job, override);
    fixedFailure(() => c.processNexusInvitationDrafts(), c); assert.equal(s.gmailCalls, 0);
  }
});

test('trigger install is explicit/idempotent; unrelated triggers survive removal', () => {
  const {context: c, state: s} = fixture();
  const unrelated = {getHandlerFunction: () => 'otherHandler'}; s.triggers.push(unrelated);
  c.installNexusBridgeTrigger(); c.installNexusBridgeTrigger(); assert.equal(s.triggers.length, 2);
  c.removeNexusBridgeTrigger(); assert.deepEqual(s.triggers, [unrelated]); assert.equal(s.deleted.length, 1);
  assert.deepEqual(s.logs, Array.from({length: 3}, () => ['NEXUS_BRIDGE_OK']));
});

test('at most ten jobs and bounded exclusions', () => {
  const {context: c, state: s} = fixture();
  s.jobs = Array.from({length: 11}, (_, i) => {
    const id = String(i + 1).padStart(8, '0') + '-2222-4333-8444-555555555555';
    return {...s.job, application_id: id, message_id: '<nexus-application-' + id + '@customer-applications.invalid>'};
  });
  assert.equal(c.processNexusInvitationDrafts().processed, 10);
  assert.equal(s.creates.length, 10);
  const claims = s.calls.filter(call => call.path.endsWith('/claim/'));
  assert.equal(claims.length, 10);
  claims.forEach((claim, index) => assert.equal(claim.payload.exclude_ids.length, index));
});

test('run uses one lock; no advanced service, deployment, sending, or unsafe logging', () => {
  const {context: c, state: s} = fixture(); s.noJob = true;
  assert.equal(c.runNexusBridge().ok, true); assert.equal(s.locked, false);
  assert.doesNotMatch(source, /\bfunction\s+do(?:Get|Post)\s*\(|\bGmail\.|\.send(?:Email)?\s*\(|\bLogger\.|\bconsole\.(?!log\()/);
  const logCalls = source.match(/\bconsole\.log\([^;]+\);/g);
  assert.equal(logCalls.length, 7);
  logCalls.forEach(call => assert.match(call,
    /^console\.log\('(?:NEXUS_BRIDGE_OK|NEXUS_DRAFT_UNCERTAIN|IMPORTED|PROCESSED|HTTP_STATUS|HTTP_ERROR|NETWORK_ERROR)'(?:, (?:result\.(?:imported|processed)|status|code))?\);$/));
  assert.deepEqual(s.logs, [['NEXUS_BRIDGE_OK'], ['IMPORTED', 0], ['PROCESSED', 0]]);
  assert.equal(manifest.dependencies, undefined); assert.equal(manifest.webapp, undefined);
  assert.equal(manifest.oauthScopes.length, 5);
  assert(manifest.oauthScopes.includes('https://mail.google.com/'));
});

test('non-200 responses log numeric status and each whitelisted server code only', () => {
  for (const error of serverCodes) {
    const {context: c, state: s} = fixture(); s.httpCode = 403;
    s.responseText = JSON.stringify({ok: false, error, detail: privateError});
    fixedFailure(() => c.testNexusBridge(), c);
    assert.deepEqual(s.logs, [['HTTP_STATUS', 403], ['HTTP_ERROR', error]]);
  }
});

test('malicious and unknown server codes never escape the fixed generic mapping', () => {
  for (const error of [privateError, 'unauthorized\n' + privateError, 'unauthorized ',
    '__proto__', 'constructor', 'toString', 'UNKNOWN_ERROR', '', null, 403,
    {code: 'unauthorized', secret: privateError}, ['unauthorized']]) {
    const {context: c, state: s} = fixture(); s.httpCode = 500;
    s.responseText = JSON.stringify({error, detail: privateError});
    fixedFailure(() => c.testNexusBridge(), c);
    assert.deepEqual(s.logs, [['HTTP_STATUS', 500], ['HTTP_ERROR', 'UNKNOWN_ERROR']]);
  }
});

test('non-JSON, oversized and unreadable error bodies preserve fixed failure', () => {
  for (const responseText of [undefined, '', privateError, '<html>' + privateError, 'null', '[]',
    '"unauthorized"', '{}', '{"error":', ' '.repeat(1024 * 1024 + 1)]) {
    const {context: c, state: s} = fixture(); s.httpCode = 502; s.responseText = responseText;
    fixedFailure(() => c.testNexusBridge(), c);
    assert.deepEqual(s.logs, [['HTTP_STATUS', 502], ['HTTP_ERROR', 'UNKNOWN_ERROR']]);
  }
  const {context: c, state: s} = fixture(); s.httpCode = 503; s.responseReadFailure = true;
  fixedFailure(() => c.testNexusBridge(), c);
  assert.deepEqual(s.logs, [['HTTP_STATUS', 503], ['HTTP_ERROR', 'UNKNOWN_ERROR']]);
});

test('non-numeric and unsafe HTTP statuses are never logged', () => {
  for (const status of [privateError, '403', NaN, Infinity, -1, 0, 99, 600, 403.5,
    Number.MAX_SAFE_INTEGER + 1, {secret: privateError}]) {
    const {context: c, state: s} = fixture(); s.httpCode = status;
    fixedFailure(() => c.testNexusBridge(), c);
    assert.deepEqual(s.logs, [['HTTP_ERROR', 'UNKNOWN_ERROR']]);
  }
});

test('network exceptions log only fixed classification without request or exception data', () => {
  const {context: c, state: s} = fixture(); s.networkFailure = true;
  fixedFailure(() => c.testNexusBridge(), c);
  assert.deepEqual(s.logs, [['NETWORK_ERROR']]);
});

test('public count logging accepts only non-negative safe integers without coercion', () => {
  for (const value of [privateError, '1', null, undefined, true, NaN, Infinity, -1, 1.5,
    Number.MAX_SAFE_INTEGER + 1, {toString: () => { throw new Error(privateError); }}]) {
    const {context: c, state: s} = fixture();
    c.nexusGuard_(() => ({ok: true, imported: value, processed: value}), false);
    assert.deepEqual(s.logs, [['NEXUS_BRIDGE_OK']]);
  }
  for (const value of [0, 1, Number.MAX_SAFE_INTEGER]) {
    const {context: c, state: s} = fixture();
    c.nexusGuard_(() => ({ok: true, imported: value, processed: value}), false);
    assert.deepEqual(s.logs, [['NEXUS_BRIDGE_OK'], ['IMPORTED', value], ['PROCESSED', value]]);
  }
});

test('combined run reports uncertainty instead of success and counts processed not created', () => {
  const {context: c, state: s} = fixture(); s.authorized = true;
  assert.deepEqual(plain(c.runNexusBridge()), {ok: false, imported: 0, processed: 1, uncertain: true});
  assert.equal(s.creates.length, 0); assert.equal(s.acked[0].status, 'uncertain');
  assert.deepEqual(s.logs, [['NEXUS_DRAFT_UNCERTAIN'], ['IMPORTED', 0], ['PROCESSED', 1]]);
});

test('mixed draft results cannot overwrite an earlier uncertain outcome with success', () => {
  const {context: c, state: s} = fixture();
  const secondId = '22222222-2222-4333-8444-555555555555';
  s.jobs = [{...s.job, allow_create: false}, {...s.job, application_id: secondId,
    message_id: '<nexus-application-' + secondId + '@customer-applications.invalid>'}];
  assert.deepEqual(plain(c.processNexusInvitationDrafts()), {ok: false, processed: 2, uncertain: true});
  assert.equal(s.creates.length, 1);
  assert.deepEqual(s.logs, [['NEXUS_DRAFT_UNCERTAIN'], ['PROCESSED', 2]]);
});

console.log(passed + ' tests passed');