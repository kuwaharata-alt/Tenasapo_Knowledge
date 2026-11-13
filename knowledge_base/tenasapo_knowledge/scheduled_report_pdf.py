"""Build a scheduled monthly PDF without a request or a view invocation."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.utils import timezone

from .chat_schedule import JAPAN_TIMEZONE
from .models import KnowledgeArticle, TipsArticle
from .posting_quotas import posting_targets
from .posting_report_pdf import build_posting_report_pdf
from .scheduled_report_stats import report_stats
from .views import SummaryView, can_view_restricted_knowledge_content, is_demo_user


RESTRICTED_CONTENT = 'この記事の内容は閲覧制限されています。'


def build_scheduled_report_context(schedule, now):
    """Use the saved actor, selected JST month and the earlier execution/notice time.

    Counts intentionally retain the screen's contributor and saved-name rules;
    the article list includes nonmembers too, regardless of approval/publication.
    """
    actor = schedule.updated_by
    if (not isinstance(actor, get_user_model()) or actor.pk is None
            or actor._state.adding or not actor.is_authenticated or not actor.is_active
            or not (actor.is_staff or actor.is_superuser)):
        raise PermissionDenied('A saved active staff or superuser actor is required.')
    if timezone.is_naive(now) or timezone.is_naive(schedule.report_notice_at):
        raise ValueError('now and report_notice_at must be timezone aware')

    with timezone.override(JAPAN_TIMEZONE):
        as_of = min(now, schedule.report_notice_at)
        month_start = schedule.target_month.replace(day=1)
        month_next = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
        month_end = month_next - timedelta(days=1)
        members, _ = SummaryView.member_registry()
        rows, totals, half = report_stats(schedule, now)
        for row in rows:
            row['management_uid'] = members[row['name']]['management_uid']

        articles = []
        demo = is_demo_user(actor)
        for model, kind in ((KnowledgeArticle, 'Q&A'), (TipsArticle, 'Tips')):
            queryset = model.objects.filter(
                Q(visible_to_customer=True) | Q(visible_to_systena=True),
                created_at__date__gte=month_start, created_at__date__lte=month_end,
                created_at__lte=as_of,
            ).select_related('created_by__knowledge_profile').prefetch_related('images')
            if kind == 'Q&A':
                queryset = queryset.prefetch_related('attachments')
            for article in queryset:
                # The screen sets this transient attribute; it is not a model property.
                article.creator_display_name = SummaryView.resolve_contributor_name(
                    article.created_by_name, article.created_by) or '-'
                allowed = not demo and can_view_restricted_knowledge_content(actor, article)
                articles.append({
                    'kind': kind, 'title': article.title, 'id': article.pk,
                    'management_code': article.management_code, 'category': article.category,
                    'creator_name': article.creator_display_name,
                    'created_at': timezone.localtime(article.created_at, JAPAN_TIMEZONE),
                    'is_approved': article.is_approved, 'detail_url': '',
                    'question': ((article.summary or article.title) if allowed
                                 else RESTRICTED_CONTENT) if kind == 'Q&A' else '',
                    'body': article.body if allowed else RESTRICTED_CONTENT,
                    'question_images': [image for image in article.attachments.all()
                                        if image.placement == 'question']
                                       if allowed and kind == 'Q&A' else [],
                    'body_images': list(article.images.all()) if allowed else [],
                    'reference_links': article.reference_links if allowed else [],
                })
        articles.sort(key=lambda item: (item['created_at'], item['kind'], item['id']), reverse=True)
        return {
            'report_month': month_start.strftime('%Y-%m'),
            'report_month_start': month_start, 'report_month_end': month_end,
            'report_member_summaries': rows, 'report_member_totals': totals,
            'report_monthly_target': posting_targets(month_start, 'current')['monthly'],
            'report_half_year_target': half, 'report_articles': articles,
            'report_totals': {'total': len(articles),
                              'faq': sum(item['kind'] == 'Q&A' for item in articles),
                              'tips': sum(item['kind'] == 'Tips' for item in articles)},
        }


def build_scheduled_report_pdf(schedule, now):
    """Render the existing posting report using only the scheduled context."""
    with timezone.override(JAPAN_TIMEZONE):
        return build_posting_report_pdf(build_scheduled_report_context(schedule, now))