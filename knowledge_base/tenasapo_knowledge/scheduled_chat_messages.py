"""Read-only scheduled messages. Article bodies are neither loaded nor sent."""

from datetime import datetime
from html import escape
import json
from urllib.parse import urlsplit

from django.conf import settings
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from .chat_schedule import JAPAN_TIMEZONE, notification_datetime
from .models import KnowledgeArticle, TipsArticle


MAX_PAYLOAD_BYTES = 28_000
MAX_ENTRIES = 40


def build_deadline_message(schedule):
    return (f'【Nexus】{schedule.target_month:%Y年%m月} 投稿期限のお知らせ\n'
            f'投稿期限: {schedule.deadline:%Y/%m/%d}\n'
            'Q&A・Tipsの投稿をお願いします。')


def _plain(value, limit):
    value = ' '.join(str(value or '-').split())
    return value if len(value) <= limit else value[:limit - 1] + '…'


def _site_url():
    value = (getattr(settings, 'SITE_URL', '') or '').strip().rstrip('/')
    try:
        parsed = urlsplit(value)
        if (len(value) > 500 or parsed.scheme != 'https' or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or any(c.isspace() or c in '<>"\\' or ord(c) < 32 for c in value)):
            return ''
        parsed.port  # Reject malformed port numbers as well.
    except ValueError:
        return ''
    return value


def build_report_message(schedule, now):
    """Return text + cardsV2, capped in UTF-8 bytes (also with default JSON escaping).

    Scope is Q&A OR Tips visible to customer OR Systena, created within the
    target JST month and no later than min(now, report_notice_at). Publication
    and approval do not change posting counts. No member/analysis roster filter.
    """
    if timezone.is_naive(now):
        raise ValueError('now must be timezone aware')
    start = notification_datetime(schedule.target_month, datetime.min.time())
    month = schedule.target_month
    end = start.replace(year=month.year + (month.month == 12), month=month.month % 12 + 1)
    as_of = min(now, schedule.report_notice_at)
    candidates = []
    total = 0
    site = _site_url()
    fields = ('pk', 'title', 'management_code', 'created_by_name', 'created_by__username', 'created_at')
    for model, label, route in ((KnowledgeArticle, 'Q&A', 'article_detail'),
                                (TipsArticle, 'Tips', 'tip_detail')):
        query = model.objects.filter(
            Q(visible_to_customer=True) | Q(visible_to_systena=True),
            created_at__gte=start, created_at__lt=end, created_at__lte=as_of)
        total += query.count()
        for row in query.order_by('-created_at', '-pk').values(*fields)[:MAX_ENTRIES]:
            row.update(kind=label, route=route)
            candidates.append(row)
    candidates.sort(key=lambda r: (r['created_at'], r['kind'], r['pk']), reverse=True)
    selected = candidates[:MAX_ENTRIES]

    def payload(rows):
        omitted = total - len(rows)
        heading = (f'【Nexus】{month:%Y年%m月} 投稿状況\n'
                   f'集計時点: {timezone.localtime(as_of, JAPAN_TIMEZONE):%Y/%m/%d %H:%M} JST\n'
                   f'全{total}件 / 表示{len(rows)}件 / 省略{omitted}件')
        text_rows, widgets = [], []
        for row in rows:
            title = _plain(row['title'], 120)
            code = _plain(row['management_code'], 24)
            author = _plain(row['created_by_name'] or row['created_by__username'], 60)
            day = timezone.localtime(row['created_at'], JAPAN_TIMEZONE).strftime('%Y/%m/%d')
            text_rows.append(f"{row['kind']} | {title} | {code} | {author} | {day}")
            title_html = escape(title)
            if site and row['management_code']:
                url = site + reverse(row['route'], kwargs={'management_code': row['management_code']})
                title_html = f'<a href="{escape(url, quote=True)}">{title_html}</a>'
            # Chat supports at most two columns. Two labelled cells per column
            # give all four table fields without squeezing/truncating the title.
            widgets.append({'columns': {'columnItems': [
                {'horizontalSizeStyle': 'FILL_AVAILABLE_SPACE', 'widgets': [
                    {'decoratedText': {'topLabel': escape(row['kind']) + ' | タイトル',
                                       'text': title_html, 'wrapText': True}},
                    {'decoratedText': {'topLabel': '管理番号', 'text': escape(code), 'wrapText': True}},
                ]},
                {'horizontalSizeStyle': 'FILL_AVAILABLE_SPACE', 'widgets': [
                    {'decoratedText': {'topLabel': '投稿者', 'text': escape(author), 'wrapText': True}},
                    {'decoratedText': {'topLabel': '投稿日（JST）', 'text': day, 'wrapText': True}},
                ]},
            ]}})
        widgets.append({'textParagraph': {'text': f'省略{omitted}件（全{total}件）'}})
        return {
            # Old GAS relays that ignore cards still get a useful bounded list.
            'text': heading + '\n種別 | タイトル | 管理番号 | 投稿者 | 投稿日\n' + '\n'.join(text_rows),
            'cardsV2': [{'cardId': 'scheduled-posting-report', 'card': {
                'header': {'title': f'{month:%Y年%m月} 投稿ナレッジ一覧',
                           'subtitle': f'全{total}件 / 省略{omitted}件'},
                'sections': [{'widgets': widgets}],
            }}],
        }

    result = payload(selected)
    while len(json.dumps(result).encode('utf-8')) >= MAX_PAYLOAD_BYTES:
        selected.pop()
        result = payload(selected)
    return result