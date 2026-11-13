"""Summary-only targets. Unknown months never become a zero or partial target."""

from .models import POSTING_QUOTA_START, PostingQuota


def posting_targets(target_month, period):
    target_month = target_month.replace(day=1)
    settings = {quota.effective_month: quota for quota in
                PostingQuota.objects.filter(effective_month__lte=target_month)}
    monthly = None
    cumulative = None
    missing = []
    totals = {'faq': 0, 'tips': 0, 'alpha': 0, 'total': 0}
    month = POSTING_QUOTA_START
    while month <= target_month:
        active = settings.get(month)
        month_target = None
        if active is None:
            missing.append(month.strftime('%Y-%m'))
        else:
            month_target = {'faq': active.faq_count, 'tips': active.tips_count,
                            'alpha': active.alpha_count,
                            'total': active.faq_count + active.tips_count + active.alpha_count}
            for key in totals:
                totals[key] += month_target[key]
        if month == target_month:
            monthly = month_target
            break
        month = month.replace(year=month.year + 1, month=1) if month.month == 12 else month.replace(month=month.month + 1)
    if target_month >= POSTING_QUOTA_START and not missing:
        cumulative = totals
    chart = cumulative if period == 'all' else monthly
    return {
        'target_month': target_month,
        'before_start': target_month < POSTING_QUOTA_START,
        'monthly': monthly,
        'cumulative': cumulative,
        'missing_months': missing,
        'chart': {'value': chart['total'] if chart is not None else None,
                  'label': '1人あたり累積目標' if period == 'all' else '1人あたり月間目標'},
    }


def quota_progress(faq_actual, tips_actual, target):
    """Base minima are mandatory; only excess posts can fill the common bucket."""
    if target is None:
        return None
    faq_missing = max(target['faq'] - faq_actual, 0)
    tips_missing = max(target['tips'] - tips_actual, 0)
    alpha_fulfilled = min(target['alpha'], max(faq_actual - target['faq'], 0)
                          + max(tips_actual - target['tips'], 0))
    alpha_missing = target['alpha'] - alpha_fulfilled
    achieved = (faq_missing == 0 and tips_missing == 0 and alpha_missing == 0
                and faq_actual + tips_actual >= target['total'])
    return {
        'faq_missing': faq_missing, 'tips_missing': tips_missing,
        'alpha_fulfilled': alpha_fulfilled, 'alpha_target': target['alpha'],
        'alpha_missing': alpha_missing, 'achieved': achieved,
        'label': '達成' if achieved else '未達成',
    }


def summary_filters(query):
    """Whitelist only known filters; never accept a caller-provided return URL."""
    period = str(query.get('period') or 'all').strip().lower()
    sort_by = str(query.get('sort_by') or 'no').strip().lower()
    return {
        'period': period if period in {'all', 'current', 'previous'} else 'all',
        'sort_by': sort_by if sort_by in {'no', 'post_count'} else 'no',
        'include_hidden': '1' if query.get('include_hidden') == '1' else '0',
    }