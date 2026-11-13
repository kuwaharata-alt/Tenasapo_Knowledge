"""Display-only calendar grouping for posting summaries."""

from datetime import date

from django import template

register = template.Library()
COUNT_KEYS = (
    'post_count_total', 'faq_post_count', 'tips_post_count',
    'approved_count_total', 'faq_approved_count', 'tips_approved_count',
)


@register.filter
def calendar_half_years(months, target_month=None):
    """Keep selected-period counts and fill each calendar half with six slots."""
    by_month = {row['month']: row for row in (months or [])}
    anchor = target_month if isinstance(target_month, date) else date.today()
    indexes = [int(key[:4]) * 2 + (int(key[5:7]) - 1) // 6 for key in by_month]
    indexes.append(anchor.year * 2 + (anchor.month - 1) // 6)
    result = []
    for index in range(max(indexes), min(indexes) - 1, -1):
        year, half = divmod(index, 2)
        start = half * 6 + 1
        rows = []
        for month in range(start, start + 6):
            key = f'{year:04d}-{month:02d}'
            row = {'month': key, 'month_label': f'{month}月', **dict.fromkeys(COUNT_KEYS, 0)}
            row.update(by_month.get(key, {}))
            rows.append(row)
        result.append({'key': f'{year}-{half + 1}',
                       'label': f'{year}年 {start}〜{start + 5}月', 'months': rows})
    return result