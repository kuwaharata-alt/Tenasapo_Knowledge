"""Read-only scheduled messages. Article bodies are neither loaded nor sent."""

from calendar import monthrange
from html import escape
import re
from django.conf import settings
from django.utils import timezone

from .chat_schedule import JAPAN_TIMEZONE
from .notifications import MAX_GOOGLE_CHAT_PAYLOAD_BYTES, validate_google_chat_payload_size
from .posting_quotas import posting_targets, quota_progress
from .scheduled_report_stats import report_stats


MAX_PAYLOAD_BYTES = MAX_GOOGLE_CHAT_PAYLOAD_BYTES
MAX_ENTRIES = 40


def build_deadline_message(schedule, now=None):
    """全不足者を列挙。分析と同じ対象・保存名ルール、JST月範囲と実行時刻で集計。"""
    from .views import SummaryView

    now = timezone.now() if now is None else now
    if timezone.is_naive(now):
        raise ValueError('now must be timezone aware')
    month = schedule.target_month.replace(day=1)
    end = min(schedule.deadline, month.replace(day=monthrange(month.year, month.month)[1]))
    lines = [f'【Nexus】{month.year}年{month.month}月 投稿期限のお知らせ',
             f'投稿期限: {schedule.deadline:%Y/%m/%d}',
             'Q&A・Tipsの投稿をお願いします。', '',
             f'集計期間: {month:%Y/%m/%d}〜{end:%Y/%m/%d}（JST）',
             f'集計時点: {timezone.localtime(now, JAPAN_TIMEZONE):%Y/%m/%d %H:%M} JST']
    with timezone.override(JAPAN_TIMEZONE):
        target = posting_targets(month, 'current')['monthly']
        if target is None:
            lines.append('対象月の投稿目標が未設定のため、不足者を判定できません。')
        else:
            members, _ = SummaryView.member_post_counts(from_date=month, to_date=end, as_of=now)
            lines.append(f"月間目標: Q&A {target['faq']}件 / Tips {target['tips']}件 ＋ α {target['alpha']}件")
            missing_rows = []
            for member in members:
                progress = quota_progress(member['faq_post_count'], member['tips_post_count'], target)
                if progress['achieved']:
                    continue
                # Normalize line breaks only: do not truncate names or the roster.
                name = ' '.join(member['name'].split())
                missing_rows.append(f'・{name}')
            if not members:
                lines.append('集計対象メンバーがいません。')
            elif not missing_rows:
                lines.append('対象メンバー全員が投稿目標を達成しています。')
            else:
                lines.extend(['', f'投稿数不足者: {len(missing_rows)}人'])
                lines.extend(missing_rows)
    message = '\n'.join(lines)
    validate_google_chat_payload_size({'text': message})
    return message


def build_deadline_notification(schedule, now=None):
    """Send the introduction as chat text and the details as a single card."""
    text = build_deadline_message(schedule, now)
    lines = text.splitlines()
    month = schedule.target_month.replace(day=1)
    end = min(schedule.deadline, month.replace(day=monthrange(month.year, month.month)[1]))
    widgets = []
    for line in [lines[1], *lines[4:]]:
        if not line:
            continue
        title, separator, content = line.partition(': ')
        if title not in ('投稿期限', '集計期間', '集計時点', '月間目標', '投稿数不足者'):
            separator = ''
        if title == '集計期間' and separator:
            content = (f'{month.year}/{month.month}/{month.day}'
                       f'~{end.year}/{end.month}/{end.day}')
        rendered = (f'{escape(title)}<br>　{escape(content)}'
                    if separator else escape(line))
        if title == '投稿数不足者' and separator:
            rendered = f'不足者（{escape(content)}）'
        widgets.append({'textParagraph': {'text': rendered}})
    payload = {
        'text': '\n'.join([lines[0], lines[2]]),
        'cardsV2': [{'cardId': 'scheduled-posting-deadline', 'card': {
            'sections': [{'widgets': widgets}],
        }}],
    }
    validate_google_chat_payload_size(payload)
    return payload


def build_report_message(schedule, now, *, pdf_url=None):
    """A named PDF link in chat text and one compact member-count card."""
    if pdf_url is not None and (not isinstance(pdf_url, str) or not re.fullmatch(
            r'https://drive\.google\.com/file/d/[A-Za-z0-9_-]{10,200}/view', pdf_url)):
        raise ValueError('Invalid report PDF link')
    _, totals, half = report_stats(schedule, now)
    month = schedule.target_month
    text = f'【Nexus】{month.year}年{month.month}月 投稿状況'
    report_url = pdf_url
    if report_url is None:
        folder_id = getattr(settings, 'GOOGLE_DRIVE_REPORT_FOLDER_ID', '')
        if isinstance(folder_id, str) and re.fullmatch(r'[A-Za-z0-9_-]{10,200}', folder_id):
            report_url = f'https://drive.google.com/drive/folders/{folder_id}'
    if report_url:
        text += f'\n<{report_url}|月次レポート>'
    monthly_text = (f'{month.month}月投稿数<br>'
                    f'　Q&amp;A：{totals["faq_post_count"]}件<br>'
                    f'　Tips：{totals["tips_post_count"]}件')
    half_text = (f'半期投稿数<br>　進捗：{half["progress_percent"]:.1f}％<br>'
                 f'　Q&amp;A：{half["faq"]}件<br>　Tips：{half["tips"]}件')
    result = {
        'text': text,
        'cardsV2': [{'cardId': 'scheduled-posting-report', 'card': {
            'sections': [{'widgets': [{'textParagraph': {'text': block}}]}
                         for block in (monthly_text, half_text)],
        }}],
    }
    validate_google_chat_payload_size(result)
    return result