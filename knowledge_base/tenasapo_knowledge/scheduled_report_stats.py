"""Shared, timestamp-bounded member counts for scheduled PDF and Chat."""

from calendar import monthrange
from datetime import date

from django.utils import timezone

from .chat_schedule import JAPAN_TIMEZONE


def report_stats(schedule, now):
    from .views import SummaryView

    if timezone.is_naive(now) or timezone.is_naive(schedule.report_notice_at):
        raise ValueError('now and report_notice_at must be timezone aware')
    month = schedule.target_month.replace(day=1)
    month_end = month.replace(day=monthrange(month.year, month.month)[1])
    as_of = min(now, schedule.report_notice_at)
    if 4 <= month.month <= 10:
        start, end = date(month.year, 4, 1), date(month.year, 10, 31)
        label = '上半期（4月〜10月）'
    else:
        year = month.year if month.month >= 11 else month.year - 1
        start, end = date(year, 11, 1), date(year + 1, 3, 31)
        label = '下半期（11月〜3月）'
    with timezone.override(JAPAN_TIMEZONE):
        rows, totals = SummaryView.member_post_counts(
            from_date=month, to_date=month_end, as_of=as_of)
        _, half_totals = SummaryView.member_post_counts(
            from_date=start, to_date=end, as_of=as_of)
    actual = half_totals['post_count_total']
    half = dict(label=label, start=start, end=end,
                faq=half_totals['faq_post_count'], tips=half_totals['tips_post_count'],
                actual=actual, target=300, progress_percent=actual / 300 * 100,
                achieved=actual >= 300)
    return rows, totals, half