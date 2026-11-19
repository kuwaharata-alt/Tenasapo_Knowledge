/**
 * Outbound-only Nexus customer application bridge. No web-app deployment.
 * Set Script Properties: NEXUS_BASE_URL (HTTPS origin, no trailing slash/path),
 * NEXUS_GAS_SECRET (32-4096 characters), NEXUS_ENVIRONMENT (production/development),
 * NEXUS_GAS_MAILBOX (exact effective-user/server mailbox).
 * Copy appsscript.customer_application.json into the GAS project's manifest.
 * Run testNexusBridge first; installNexusBridgeTrigger is an EXPLICIT opt-in.
 * GmailApp needs the FULL https://mail.google.com/ scope (broad mailbox access).
 * No advanced Gmail service or Google Cloud API enablement is required.
 * This code NEVER sends mail, sets CC/BCC, approves users, or deploys endpoints.
 * Responses are NOT server-signed: trust valid HTTPS certificates, never redirects.
 * Keep local attempt properties: deleting them weakens recovery evidence.
 */
var NEXUS_BRIDGE_ = Object.freeze({
  spreadsheetId: '1JaWW0c_CE9W5_egLvoqrWcnwuIbb6QCERBRrQvAE5Pg',
  sheetId: 1583871824,
  headers: ['タイムスタンプ', 'メールアドレス', '会社名', '部署名', '契約者氏名', '契約メールアドレス'],
  paths: {
    ping: '/api/customer-applications/gas/status/',
    import: '/api/customer-applications/gas/import/',
    claim: '/api/customer-applications/gas/claim/',
    ack: '/api/customer-applications/gas/ack/'
  },
  error: 'Nexus連携を完了できません。設定と処理状態を確認してください。',
  uuid: /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?![\s\S])/,
  draftId: /^[A-Za-z0-9_-]{1,255}(?![\s\S])/
});

/** Only a signed ping: no spreadsheet, Gmail, or effective-user access. */
function testNexusBridge() {
  return nexusGuard_(function () {
    nexusPing_(nexusConfig_());
    return {ok: true};
  }, false);
}

/** Import one page, at most 200 A-F rows; never approves users. */
function syncNexusApplications() {
  return nexusGuard_(function () {
    var config = nexusConfig_();
    nexusMailbox_(config);
    return nexusSync_(config, nexusPing_(config));
  }, true);
}

/** Process at most ten claims, excluding already visited applications. */
function processNexusInvitationDrafts() {
  return nexusGuard_(function () {
    var config = nexusConfig_();
    nexusMailbox_(config);
    nexusPing_(config);
    return nexusJobs_(config);
  }, true);
}

/** One ScriptLock covers both import and draft processing. */
function runNexusBridge() {
  return nexusGuard_(function () {
    var config = nexusConfig_();
    nexusMailbox_(config);
    var imported = nexusSync_(config, nexusPing_(config));
    var drafts = nexusJobs_(config);
    if (drafts.uncertain) return {ok: false, imported: imported.imported, processed: drafts.processed, uncertain: true};
    return {ok: true, imported: imported.imported, processed: drafts.processed};
  }, true);
}

/** Explicit opt-in only. Existing unrelated triggers are never modified. */
function installNexusBridgeTrigger() {
  return nexusGuard_(function () {
    var exists = ScriptApp.getProjectTriggers().some(function (trigger) {
      return trigger.getHandlerFunction() === 'runNexusBridge';
    });
    if (!exists) ScriptApp.newTrigger('runNexusBridge').timeBased().everyMinutes(5).create();
    return {ok: true};
  }, true);
}

function removeNexusBridgeTrigger() {
  return nexusGuard_(function () {
    ScriptApp.getProjectTriggers().forEach(function (trigger) {
      if (trigger.getHandlerFunction() === 'runNexusBridge') ScriptApp.deleteTrigger(trigger);
    });
    return {ok: true};
  }, true);
}

function nexusFail_() {
  throw new Error(NEXUS_BRIDGE_.error);
}

// Every public entry point masks remote exceptions and never logs sensitive data.
function nexusGuard_(operation, locked) {
  var lock = null;
  var result;
  try {
    if (locked) {
      lock = LockService.getScriptLock();
      if (!lock.tryLock(1000)) nexusFail_();
    }
    result = operation();
  } catch (ignored) {
    nexusFail_();
  } finally {
    if (lock) {
      try { lock.releaseLock(); } catch (ignoredRelease) { nexusFail_(); }
    }
  }
  if (result && result.uncertain === true) console.log('NEXUS_DRAFT_UNCERTAIN');
  else if (result && result.ok === true) console.log('NEXUS_BRIDGE_OK');
  if (result && Number.isSafeInteger(result.imported) && result.imported >= 0) console.log('IMPORTED', result.imported);
  if (result && Number.isSafeInteger(result.processed) && result.processed >= 0) console.log('PROCESSED', result.processed);
  return result;
}

function nexusEmail_(value) {
  // Deliberately accepts only a single bare address, not display-name/list syntax.
  return typeof value === 'string' && value.length <= 254 && !/[\s\x00-\x1f\x7f]/.test(value) &&
    /^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+$/.test(value);
}

function nexusConfig_() {
  var properties = PropertiesService.getScriptProperties();
  var origin = properties.getProperty('NEXUS_BASE_URL');
  var secret = properties.getProperty('NEXUS_GAS_SECRET');
  var environment = properties.getProperty('NEXUS_ENVIRONMENT');
  var mailbox = properties.getProperty('NEXUS_GAS_MAILBOX');
  // No credentials, query, fragment, path, whitespace, or URL normalization.
  var match = typeof origin === 'string' && /^https:\/\/([A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?)(?::([0-9]{1,5}))?$/.exec(origin);
  if (!match || /[\s\x00-\x1f\x7f]/.test(origin) || match[1].length > 253 || match[1].split('.').some(function (label) {
    return !/^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$/.test(label);
  }) || (match[2] && (+match[2] < 1 || +match[2] > 65535)) ||
      typeof secret !== 'string' || secret.length < 32 || secret.length > 4096 ||
      (environment !== 'production' && environment !== 'development') || !nexusEmail_(mailbox)) nexusFail_();
  return {origin: origin, secret: secret, environment: environment, mailbox: mailbox, properties: properties};
}

function nexusMailbox_(config) {
  var effective = Session.getEffectiveUser().getEmail();
  if (!effective || effective !== config.mailbox) nexusFail_();
}

function nexusHex_(bytes) {
  return bytes.map(function (value) { return ('0' + ((value + 256) % 256).toString(16)).slice(-2); }).join('');
}

function nexusSha_(text) {
  return nexusHex_(Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, text, Utilities.Charset.UTF_8));
}

function nexusSignature_(config, path, timestamp, nonce, body) {
  var canonical = ['nexus-gas-v1', config.environment, 'POST', path, timestamp, nonce, nexusSha_(body)].join('\n');
  return nexusHex_(Utilities.computeHmacSha256Signature(canonical, config.secret, Utilities.Charset.UTF_8));
}

function nexusHttpError_(response) {
  // Only locally defined values may reach the log, never a remote string.
  var codes = {
    unauthorized: 'unauthorized', replay: 'replay', invalid_configuration: 'invalid_configuration',
    backend_disabled: 'backend_disabled', body_too_large: 'body_too_large', invalid_page: 'invalid_page',
    cursor_conflict: 'cursor_conflict', https_required: 'https_required',
    transaction_not_supported: 'transaction_not_supported', invalid_request: 'invalid_request',
    service_unavailable: 'service_unavailable'
  };
  var code = 'UNKNOWN_ERROR';
  try {
    var text = response.getContentText('UTF-8');
    if (typeof text === 'string' && text.length <= 1024 * 1024) {
      var result = JSON.parse(text);
      if (result && !Array.isArray(result) && typeof result.error === 'string' &&
          Object.prototype.hasOwnProperty.call(codes, result.error)) code = codes[result.error];
    }
  } catch (ignoredResponse) {
    // Error pages and unreadable responses have the same fixed classification.
  }
  console.log('HTTP_ERROR', code);
}

function nexusRequest_(config, action, payload) {
  var path = NEXUS_BRIDGE_.paths[action];
  if (!path) nexusFail_();
  var body = JSON.stringify(payload);
  if (Utilities.newBlob(body).getBytes().length > 512 * 1024) nexusFail_();
  var timestamp = String(Math.floor(Date.now() / 1000));
  var nonce = Utilities.getUuid().replace(/-/g, '');
  if (!/^[0-9]{1,12}$/.test(timestamp) || !/^[0-9a-f]{32}$/.test(nonce)) nexusFail_();
  var options = {
    method: 'post', contentType: 'application/json', payload: body,
    headers: {
      'X-Nexus-Timestamp': timestamp,
      'X-Nexus-Nonce': nonce,
      'X-Nexus-Signature': nexusSignature_(config, path, timestamp, nonce, body)
    },
    validateHttpsCertificates: true, followRedirects: false, muteHttpExceptions: true
  };
  var response;
  try {
    response = UrlFetchApp.fetch(config.origin + path, options);
  } catch (ignoredNetwork) {
    console.log('NETWORK_ERROR');
    nexusFail_();
  }
  var status = response.getResponseCode();
  if (status !== 200) {
    if (Number.isSafeInteger(status) && status >= 100 && status <= 599) console.log('HTTP_STATUS', status);
    nexusHttpError_(response);
    nexusFail_();
  }
  var text = response.getContentText('UTF-8');
  if (text.length > 1024 * 1024) nexusFail_();
  var result = JSON.parse(text);
  if (!result || typeof result !== 'object' || Array.isArray(result) || result.ok !== true) nexusFail_();
  return result;
}

function nexusRowNumber_(number) {
  return Number.isSafeInteger(number) && number >= 2;
}

function nexusPing_(config) {
  var result = nexusRequest_(config, 'ping', {});
  if (result.service !== 'nexus-customer-application-gas-v1' || result.environment !== config.environment ||
      result.mailbox !== config.mailbox || !nexusRowNumber_(result.next_row)) nexusFail_();
  return result;
}

function nexusSync_(config, status) {
  var spreadsheet = SpreadsheetApp.openById(NEXUS_BRIDGE_.spreadsheetId);
  if (spreadsheet.getId() !== NEXUS_BRIDGE_.spreadsheetId) nexusFail_();
  var sheet = spreadsheet.getSheetById(NEXUS_BRIDGE_.sheetId);
  if (!sheet || sheet.getSheetId() !== NEXUS_BRIDGE_.sheetId) nexusFail_();
  var headers = sheet.getRange(1, 1, 1, 6).getDisplayValues();
  if (JSON.stringify(headers) !== JSON.stringify([NEXUS_BRIDGE_.headers])) nexusFail_();
  var start = status.next_row;
  var last = sheet.getLastRow();
  if (!Number.isSafeInteger(last) || last < 1) nexusFail_();
  var count = Math.min(200, Math.max(0, last - start + 1));
  var end = start + count;
  if (!nexusRowNumber_(end)) nexusFail_();
  var rows = count ? sheet.getRange(start, 1, count, 6).getDisplayValues() : [];
  if (rows.length !== count) nexusFail_();
  var applications = [];
  rows.forEach(function (row, index) {
    if (!Array.isArray(row) || row.length !== 6 || row.some(function (cell) { return typeof cell !== 'string'; })) nexusFail_();
    if (row.every(function (cell) { return cell.trim() === ''; })) return;
    applications.push({
      source_key: NEXUS_BRIDGE_.spreadsheetId + ':' + NEXUS_BRIDGE_.sheetId + ':' + (start + index),
      submitted_at: row[0], applicant_email: row[1], company_name: row[2],
      department: row[3], display_name: row[4], email: row[5]
    });
  });
  var result = nexusRequest_(config, 'import', {
    start_row: start, next_row: end, headers: NEXUS_BRIDGE_.headers,
    spreadsheet_id: NEXUS_BRIDGE_.spreadsheetId, sheet_id: String(NEXUS_BRIDGE_.sheetId),
    applications: applications
  });
  if (!nexusRowNumber_(result.next_row) || result.next_row < end ||
      !Number.isSafeInteger(result.imported) || result.imported < 0 || result.imported > applications.length) nexusFail_();
  return {ok: true, imported: result.imported};
}

function nexusJob_(job, config) {
  if (!job || typeof job !== 'object' || Array.isArray(job) ||
      typeof job.application_id !== 'string' || !NEXUS_BRIDGE_.uuid.test(job.application_id) ||
      typeof job.attempt_id !== 'string' || !NEXUS_BRIDGE_.uuid.test(job.attempt_id) ||
      job.mailbox !== config.mailbox || typeof job.allow_create !== 'boolean' || !nexusEmail_(job.to) ||
      job.message_id !== '<nexus-application-' + job.application_id + '@customer-applications.invalid>') nexusFail_();
  if (job.allow_create && (typeof job.subject !== 'string' || !job.subject || job.subject.length > 250 ||
      /[\x00-\x1f\x7f]/.test(job.subject) || Utilities.newBlob(job.subject).getBytes().length > 998 ||
      typeof job.body !== 'string' || !job.body || /[\x00\x7f]/.test(job.body) ||
      Utilities.newBlob(job.body + '\n\n' + nexusMarker_(job)).getBytes().length > 200000)) nexusFail_();
  return job;
}

function nexusStateKey_(job) {
  return 'NEXUS_GAS_ATTEMPT_' + nexusSha_(job.application_id + ':' + job.attempt_id);
}

function nexusLocal_(config, job) {
  var raw = config.properties.getProperty(nexusStateKey_(job));
  if (raw === null) return null;
  var state = JSON.parse(raw);
  if (!state || state.mailbox !== config.mailbox ||
      ['attempted', 'uncertain', 'created'].indexOf(state.state) < 0 ||
      (state.state === 'created' && (typeof state.draft_id !== 'string' || !NEXUS_BRIDGE_.draftId.test(state.draft_id)))) nexusFail_();
  return state;
}

function nexusRemember_(config, job, state, draftId) {
  config.properties.setProperty(nexusStateKey_(job), JSON.stringify({
    mailbox: config.mailbox, state: state, draft_id: draftId || ''
  }));
}

function nexusMarker_(job) {
  return 'Nexus連携ID: ' + job.message_id;
}

function nexusFindDraft_(job) {
  var drafts = GmailApp.getDrafts();
  // Never search a partial mailbox and then assume the draft does not exist.
  if (drafts.length > 1000) return {safe: false, id: ''};
  var found = [];
  var marker = nexusMarker_(job);
  drafts.forEach(function (draft) {
    var message = draft.getMessage();
    if (message.getTo() !== job.to || message.getCc() !== '' || message.getBcc() !== '') return;
    var plain = message.getPlainBody();
    var html = message.getBody();
    // Plain-text line equality prevents substring/UUID-prefix matches. HTML-only
    // literal markers are accepted only as an entire line as well.
    var matches = [plain, html].some(function (body) {
      return typeof body === 'string' && body.split(/\r?\n/).indexOf(marker) !== -1;
    });
    if (matches) {
      var id = draft.getId();
      if (typeof id !== 'string' || !NEXUS_BRIDGE_.draftId.test(id)) nexusFail_();
      found.push(id);
    }
  });
  return {safe: found.length <= 1, id: found.length === 1 ? found[0] : ''};
}

function nexusAck_(config, job, draftId) {
  var status = draftId ? 'created' : 'uncertain';
  var result = nexusRequest_(config, 'ack', {
    application_id: job.application_id, attempt_id: job.attempt_id, status: status, draft_id: draftId || ''
  });
  if (result.application_id !== job.application_id || result.status !== status) nexusFail_();
}

function nexusDraft_(config, job) {
  // A stored successful ID is authoritative even if the draft was deleted/sent.
  var local = nexusLocal_(config, job);
  if (local && local.state === 'created') {
    nexusAck_(config, job, local.draft_id);
    return true;
  }
  var existing = nexusFindDraft_(job);
  if (existing.safe && existing.id) {
    nexusRemember_(config, job, 'created', existing.id);
    nexusAck_(config, job, existing.id);
    return true;
  }
  if (!existing.safe || !job.allow_create || local) {
    nexusRemember_(config, job, 'uncertain', '');
    nexusAck_(config, job, '');
    return false;
  }
  // Persist BEFORE any remote create, and mark the outcome uncertain BEFORE the
  // call can time out. Even a hard execution termination cannot authorize retry.
  nexusRemember_(config, job, 'attempted', '');
  nexusRemember_(config, job, 'uncertain', '');
  var id;
  try {
    id = GmailApp.createDraft(job.to, job.subject, job.body + '\n\n' + nexusMarker_(job)).getId();
    if (typeof id !== 'string' || !NEXUS_BRIDGE_.draftId.test(id)) nexusFail_();
  } catch (ignoredCreate) {
    // No scan/recreate in this execution: a timed-out create may still complete.
    nexusAck_(config, job, '');
    return false;
  }
  nexusRemember_(config, job, 'created', id);
  nexusAck_(config, job, id);
  return true;
}

function nexusJobs_(config) {
  var excluded = [];
  var uncertain = false;
  for (var index = 0; index < 10; index++) {
    if (excluded.length > 20) nexusFail_();
    var result = nexusRequest_(config, 'claim', {exclude_ids: excluded.slice()});
    if (result.job === null) break;
    var job = nexusJob_(result.job, config);
    if (excluded.indexOf(job.application_id) !== -1) nexusFail_();
    excluded.push(job.application_id);
    if (!nexusDraft_(config, job)) uncertain = true;
  }
  if (uncertain) return {ok: false, processed: excluded.length, uncertain: true};
  return {ok: true, processed: excluded.length};
}