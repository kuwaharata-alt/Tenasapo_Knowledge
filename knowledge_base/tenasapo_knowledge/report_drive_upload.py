"""One signed upload to a dedicated GAS app; no Chat or public-sharing API."""

import base64
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import re
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


MAX_PDF_BYTES = 10 * 1024 * 1024
ACTION = 'upload_reserved_report_pdf'


@dataclass(frozen=True)
class UploadConfig:
    url: str
    secret: str = field(repr=False)
    folder_id: str


def report_pdf_enabled():
    return getattr(settings, 'GOOGLE_CHAT_REPORT_PDF_ENABLED', False) is True


def get_upload_config():
    url = getattr(settings, 'GOOGLE_DRIVE_REPORT_UPLOAD_URL', '')
    secret = getattr(settings, 'GOOGLE_DRIVE_REPORT_UPLOAD_SECRET', '')
    folder_id = getattr(settings, 'GOOGLE_DRIVE_REPORT_FOLDER_ID', '')
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.netloc != 'script.google.com'
            or not re.fullmatch(r'/(?:a/macros/[^/]+|macros)/s/[A-Za-z0-9_-]+/exec', parsed.path)
            or parsed.query or parsed.fragment or len(secret) < 32
            or not re.fullmatch(r'[A-Za-z0-9_-]{10,200}', folder_id)):
        raise ImproperlyConfigured('PDF保存用GASのURL・専用秘密鍵・フォルダIDを設定してください。')
    return UploadConfig(url, secret, folder_id)


def validate_pdf(pdf):
    if not isinstance(pdf, bytes) or not pdf.startswith(b'%PDF-') or len(pdf) > MAX_PDF_BYTES:
        raise ValueError('PDFの形式またはサイズ（10MB以下）が不正です。')


def upload_report_pdf(pdf, filename, config):
    """Never retry: an unconfirmed response may have already created a file."""
    validate_pdf(pdf)
    if (not isinstance(filename, str) or not 5 <= len(filename) <= 180
            or not filename.endswith('.pdf') or filename != filename.strip()
            or re.search(r'[\\/\x00-\x1f\x7f-\x9f<>:"|?*]', filename)):
        raise ValueError('PDFファイル名が不正です。')
    request_id = uuid4().hex
    payload = json.dumps({
        'action': ACTION, 'timestamp': int(time.time()), 'request_id': request_id,
        'filename': filename, 'folder_id': config.folder_id,
        'pdf_base64': base64.b64encode(pdf).decode('ascii'),
    }, ensure_ascii=True, separators=(',', ':'))
    signature = hmac.new(config.secret.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()
    envelope = json.dumps({'payload': payload, 'signature': signature}).encode('utf-8')
    request = Request(config.url, data=envelope,
                      headers={'Content-Type': 'application/json; charset=UTF-8'}, method='POST')
    try:
        with urlopen(request, timeout=60) as response:
            status = response.getcode()
            data = json.loads(response.read(4096).decode('utf-8'))
        file_id = data.get('file_id') if isinstance(data, dict) else None
        if (not 200 <= status < 300 or not isinstance(data, dict)
                or data.get('ok') is not True or data.get('action') != ACTION
                or data.get('request_id') != request_id or data.get('folder_id') != config.folder_id
                or not isinstance(file_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{10,200}', file_id)):
            raise ValueError('unconfirmed upload')
    except Exception:
        raise RuntimeError('PDFのDrive保存結果を確認できません。再試行しません。') from None
    return f'https://drive.google.com/file/d/{file_id}/view'