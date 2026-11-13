/**
 * Dedicated upload-only GAS web app. Deploy independently of the Chat relay.
 * Script Properties: REPORT_UPLOAD_SECRET (at least 32 characters), REPORT_FOLDER_ID.
 * No sharing permissions are changed; the destination folder's policy applies.
 */
var REPORT_UPLOAD_ACTION = 'upload_reserved_report_pdf';
var REPORT_UPLOAD_MAX_PAYLOAD = 15 * 1024 * 1024;
var REPORT_UPLOAD_MAX_PDF = 10 * 1024 * 1024;
var REPORT_UPLOAD_NONCE_PREFIX = 'REPORT_UPLOAD_NONCE_';
var REPORT_UPLOAD_MAX_ACTIVE_NONCES = 300;

function doGet() {
  return reportUploadJson_({
    version: 1,
    capabilities: [REPORT_UPLOAD_ACTION]
  });
}

function doPost(e) {
  try {
    return reportUploadJson_(reportUpload_(e));
  } catch (ignored) {
    // Never return exception details or log secrets, signatures, or PDF content.
    return reportUploadJson_({
      ok: false,
      action: REPORT_UPLOAD_ACTION,
      error: 'PDF upload rejected'
    });
  }
}

function reportUpload_(e) {
  var properties = PropertiesService.getScriptProperties();
  var secret = properties.getProperty('REPORT_UPLOAD_SECRET');
  var folderId = properties.getProperty('REPORT_FOLDER_ID');
  reportUploadRequire_(typeof secret === 'string' && secret.length >= 32);
  reportUploadRequire_(typeof folderId === 'string' && folderId.length > 0 && !/[^A-Za-z0-9_-]/.test(folderId));
  reportUploadRequire_(e && e.postData && typeof e.postData.contents === 'string');

  var envelope = JSON.parse(e.postData.contents);
  reportUploadRequire_(reportUploadExactKeys_(envelope, ['payload', 'signature']));
  var payload = envelope.payload;
  reportUploadRequire_(typeof payload === 'string' && payload.length <= REPORT_UPLOAD_MAX_PAYLOAD);
  reportUploadRequire_(typeof envelope.signature === 'string' && /^[0-9a-f]{64}$/.test(envelope.signature));
  var digest = Utilities.computeHmacSha256Signature(payload, secret, Utilities.Charset.UTF_8);
  var expected = digest.map(function (byte) {
    return ('0' + (byte & 255).toString(16)).slice(-2);
  }).join('');
  reportUploadRequire_(reportUploadConstantTimeEqual_(expected, envelope.signature));

  var data = JSON.parse(payload);
  reportUploadRequire_(reportUploadExactKeys_(data, [
    'action', 'timestamp', 'request_id', 'filename', 'folder_id', 'pdf_base64'
  ]));
  reportUploadRequire_(data.action === REPORT_UPLOAD_ACTION);
  reportUploadRequire_(Number.isSafeInteger(data.timestamp) &&
    Math.abs(Math.floor(Date.now() / 1000) - data.timestamp) <= 300);
  reportUploadRequire_(typeof data.request_id === 'string' && data.request_id.length === 32 &&
    /^[0-9a-f]{32}$/.test(data.request_id));
  reportUploadRequire_(data.folder_id === folderId);
  // Require an already sanitized basename; never silently change signed input.
  reportUploadRequire_(typeof data.filename === 'string' && data.filename.length <= 180 &&
    data.filename.length > 4 && data.filename === data.filename.trim() &&
    /\.pdf$/.test(data.filename) && !/[\\/\x00-\x1f\x7f-\x9f<>:"|?*]/.test(data.filename));
  reportUploadRequire_(typeof data.pdf_base64 === 'string' && data.pdf_base64.length > 0 &&
    data.pdf_base64.length <= 4 * Math.ceil(REPORT_UPLOAD_MAX_PDF / 3) &&
    data.pdf_base64.length % 4 === 0 &&
    !/[^A-Za-z0-9+/=]/.test(data.pdf_base64) &&
    /^[A-Za-z0-9+/]*={0,2}$/.test(data.pdf_base64));
  var bytes = Utilities.base64Decode(data.pdf_base64);
  reportUploadRequire_(bytes.length >= 5 && bytes.length <= REPORT_UPLOAD_MAX_PDF);
  reportUploadRequire_([37, 80, 68, 70, 45].every(function (byte, index) {
    return (bytes[index] & 255) === byte;
  }));

  var lock = LockService.getScriptLock();
  var acquired = false;
  try {
    acquired = lock.tryLock(5000);
    reportUploadRequire_(acquired);
    var now = Math.floor(Date.now() / 1000);
    // Recheck freshness after waiting for the lock.
    reportUploadRequire_(Math.abs(now - data.timestamp) <= 300);
    var ledger = properties.getProperties();
    var activeNonces = 0;
    Object.keys(ledger).forEach(function (key) {
      if (key.indexOf(REPORT_UPLOAD_NONCE_PREFIX) !== 0) {
        return;
      }
      var expiration = Number(ledger[key]);
      if (Number.isSafeInteger(expiration) && expiration > 0 && expiration <= now) {
        properties.deleteProperty(key);
      } else {
        // Keep malformed entries fail-closed rather than allowing a replay.
        activeNonces += 1;
      }
    });
    var nonceKey = REPORT_UPLOAD_NONCE_PREFIX + data.request_id;
    reportUploadRequire_(properties.getProperty(nonceKey) === null);
    reportUploadRequire_(activeNonces < REPORT_UPLOAD_MAX_ACTIVE_NONCES);
    // A future-dated accepted request can remain valid for 600 seconds.
    // Persist under the lock before any Drive effect, even if Drive later fails.
    properties.setProperty(nonceKey, String(now + 601));
  } finally {
    if (acquired) {
      lock.releaseLock();
    }
  }

  var folder = DriveApp.getFolderById(folderId);
  reportUploadRequire_(!folder.isTrashed());
  var file = folder.createFile(Utilities.newBlob(bytes, 'application/pdf', data.filename));
  return {
    ok: true,
    action: REPORT_UPLOAD_ACTION,
    request_id: data.request_id,
    folder_id: folderId,
    file_id: file.getId()
  };
}

function reportUploadExactKeys_(value, keys) {
  return value !== null && typeof value === 'object' && !Array.isArray(value) &&
    Object.keys(value).length === keys.length && keys.every(function (key) {
      return Object.prototype.hasOwnProperty.call(value, key);
    });
}

function reportUploadConstantTimeEqual_(left, right) {
  var difference = left.length ^ right.length;
  for (var index = 0; index < 64; index += 1) {
    difference |= left.charCodeAt(index) ^ right.charCodeAt(index);
  }
  return difference === 0;
}

function reportUploadRequire_(condition) {
  if (!condition) {
    throw new Error('PDF upload rejected');
  }
}

function reportUploadJson_(value) {
  return ContentService.createTextOutput(JSON.stringify(value))
    .setMimeType(ContentService.MimeType.JSON);
}