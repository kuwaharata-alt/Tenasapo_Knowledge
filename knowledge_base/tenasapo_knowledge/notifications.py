import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from django.conf import settings
from django.utils import timezone


class GoogleChatNotificationError(Exception):
    """Google Chat 通知送信時のアプリ内例外。"""


def _build_gas_candidate_urls(url):
    base_url = (url or '').strip()
    if not base_url:
        return []

    candidates = [base_url]
    parsed = urlsplit(base_url)
    marker = '/a/macros/'
    index = parsed.path.find(marker)
    if index == -1:
        return candidates

    remainder = parsed.path[index + len(marker):]
    parts = remainder.split('/', 1)
    if len(parts) != 2 or not parts[1].startswith('s/'):
        return candidates

    normalized_path = '/macros/' + parts[1]
    normalized_url = urlunsplit(
        (parsed.scheme, parsed.netloc, normalized_path, parsed.query, parsed.fragment)
    )
    if normalized_url not in candidates:
        candidates.append(normalized_url)
    return candidates


def _trim_error_text(text, max_length=300):
    normalized = (text or '').replace('\n', ' ').replace('\r', ' ').strip()
    if len(normalized) <= max_length:
        return normalized
    return normalized[:max_length] + '...'


def build_home_notification_test_message(user):
    timestamp = timezone.localtime().strftime('%Y/%m/%d %H:%M:%S')
    username = getattr(user, 'username', '') or '-'
    return '\n'.join(
        [
            '【Nexus】通知テスト',
            f'実行ユーザー: {username}',
            f'送信日時: {timestamp}',
            'ホーム > Management > 通知テスト から送信しました。',
        ]
    )


def build_home_mail_draft_test_payload(user):
    timestamp = timezone.localtime().strftime('%Y/%m/%d %H:%M:%S')
    username = getattr(user, 'username', '') or '-'
    return {
        'action': 'create_draft',
        'to': username,
        'subject': f'【Nexus】メール下書きテスト ({timestamp})',
        'body': '\n'.join(
            [
                'これはNexusのメール下書きテストです。',
                f'実行ユーザー: {username}',
                f'実行日時: {timestamp}',
                'ホーム > Management > メール下書きテスト から作成しました。',
            ]
        ),
    }


def get_google_chat_target_url():
    gas_url = (getattr(settings, 'GOOGLE_CHAT_GAS_WEB_APP_URL', '') or '').strip()
    if gas_url:
        return gas_url

    webhook_url = (getattr(settings, 'GOOGLE_CHAT_WEBHOOK_URL', '') or '').strip()
    if webhook_url:
        return webhook_url

    raise GoogleChatNotificationError('Google Chat の送信先URLが未設定です。')


def uses_google_apps_script():
    return bool((getattr(settings, 'GOOGLE_CHAT_GAS_WEB_APP_URL', '') or '').strip())


def _request_json_post(url, payload_bytes):
    request = Request(
        url,
        data=payload_bytes,
        headers={'Content-Type': 'application/json; charset=UTF-8'},
        method='POST',
    )

    with urlopen(request, timeout=15) as response:
        status_code = getattr(response, 'status', response.getcode())
        body = response.read().decode('utf-8', errors='replace')
    return status_code, body


def _resolve_webhook_url():
    return (getattr(settings, 'GOOGLE_CHAT_WEBHOOK_URL', '') or '').strip()


def send_google_chat_message(text):
    payload = json.dumps({'text': text}).encode('utf-8')
    gas_url = (getattr(settings, 'GOOGLE_CHAT_GAS_WEB_APP_URL', '') or '').strip()
    webhook_url = _resolve_webhook_url()

    target_urls = []
    if gas_url:
        target_urls.extend(_build_gas_candidate_urls(gas_url))
    if webhook_url:
        if webhook_url not in target_urls:
            target_urls.append(webhook_url)

    if not target_urls:
        raise GoogleChatNotificationError('Google Chat の送信先URLが未設定です。')

    last_http_error = None
    for target_url in target_urls:
        try:
            status_code, body = _request_json_post(target_url, payload)
        except HTTPError as exc:
            response_body = exc.read().decode('utf-8', errors='replace')
            last_http_error = (exc.code, response_body or exc.reason or '')
            continue
        except URLError as exc:
            raise GoogleChatNotificationError(f'接続エラー: {exc.reason}') from exc
        except Exception as exc:
            raise GoogleChatNotificationError(str(exc)) from exc

        if 200 <= status_code < 300:
            return {'status_code': status_code, 'body': body}

        last_http_error = (status_code, body)

    if last_http_error:
        status_code, detail = last_http_error
        raise GoogleChatNotificationError(f'HTTP {status_code}: {_trim_error_text(detail)}')

    raise GoogleChatNotificationError('通知テストの送信に失敗しました。')


def create_mail_draft_via_gas(payload):
    gas_url = (getattr(settings, 'GOOGLE_CHAT_GAS_WEB_APP_URL', '') or '').strip()
    if not gas_url:
        raise GoogleChatNotificationError('メール下書きはGAS経由のみ対応です。GOOGLE_CHAT_GAS_WEB_APP_URLを設定してください。')

    request_body = json.dumps(payload).encode('utf-8')
    candidate_urls = _build_gas_candidate_urls(gas_url)
    last_http_error = None

    for candidate_url in candidate_urls:
        request = Request(
            candidate_url,
            data=request_body,
            headers={'Content-Type': 'application/json; charset=UTF-8'},
            method='POST',
        )

        try:
            with urlopen(request, timeout=15) as response:
                status_code = getattr(response, 'status', response.getcode())
                response_text = response.read().decode('utf-8', errors='replace')
        except HTTPError as exc:
            response_body = exc.read().decode('utf-8', errors='replace')
            last_http_error = (exc.code, response_body or exc.reason or '')
            continue
        except URLError as exc:
            raise GoogleChatNotificationError(f'接続エラー: {exc.reason}') from exc
        except Exception as exc:
            raise GoogleChatNotificationError(str(exc)) from exc

        if not 200 <= status_code < 300:
            last_http_error = (status_code, response_text)
            continue

        try:
            response_json = json.loads(response_text) if response_text else {}
        except json.JSONDecodeError:
            response_json = {'raw': response_text}

        if response_json.get('ok') is False:
            raise GoogleChatNotificationError(response_json.get('body') or 'メール下書きの作成に失敗しました。')

        return {'status_code': status_code, 'body': response_json}

    if last_http_error:
        status_code, detail = last_http_error
        message = f'HTTP {status_code}: {_trim_error_text(detail)}'
        if status_code in {401, 403, 404}:
            message += ' / GASのWebアプリ公開範囲(全員)とURL形式(macros/s/.../exec)を確認してください。'
        raise GoogleChatNotificationError(message)

    raise GoogleChatNotificationError('メール下書きの作成に失敗しました。')