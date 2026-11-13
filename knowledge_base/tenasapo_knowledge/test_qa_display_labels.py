"""Q&A presentation contracts; no database, mail, or Chat writes are allowed."""

import ast
import inspect
import json
from datetime import date
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import AnonymousUser
from django.db import migrations
from django.db.migrations.loader import MigrationLoader
from django.template import Context, Template
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase
from django.utils.html import escape
from django.utils import timezone

from . import views
from .manual_defaults import OPERATION_MANUALS
from .management.commands import (
    notify_expiring_articles, notify_monthly_posts, unpublish_expired_articles,
)
from .models import ArticleFavorite, ArticleGood, FAQCategory, FAQParentCategorySetting


class QADisplayLabelTests(SimpleTestCase):
    def test_model_metadata_and_new_migration_preserve_non_display_options(self):
        loader = MigrationLoader(None)
        migration = loader.disk_migrations[('tenasapo_knowledge', '0077_q_and_a_display_labels')]
        self.assertEqual(migration.dependencies, [('tenasapo_knowledge', '0076_qa_display_labels')])
        self.assertEqual(len(migration.operations), 4)
        self.assertTrue(all(isinstance(op, migrations.AlterModelOptions) for op in migration.operations))
        before = loader.project_state(migration.dependencies)
        after = loader.project_state([('tenasapo_knowledge', migration.name)])
        for model, label in ((FAQCategory, 'Q&Aカテゴリ'),
                     (FAQParentCategorySetting, 'Q&A大カテゴリ設定'),
                     (ArticleGood, 'Q&Aグッド'), (ArticleFavorite, 'Q&Aお気に入り')):
            with self.subTest(model=model.__name__):
                self.assertEqual(model._meta.verbose_name, label)
                self.assertEqual(model._meta.verbose_name_plural, label)
                key = ('tenasapo_knowledge', model._meta.model_name)
                old, new = before.models[key], after.models[key]
                self.assertEqual(old.fields, new.fields)
                self.assertEqual(
                    {k: v for k, v in old.options.items() if not k.startswith('verbose_name')},
                    {k: v for k, v in new.options.items() if not k.startswith('verbose_name')},
                )
                self.assertEqual(new.options['verbose_name'], label)
                self.assertEqual(new.options['verbose_name_plural'], label)

    def test_home_menu_uses_qa_and_keeps_collection_keys(self):
        request = RequestFactory().get('/')
        request.user = AnonymousUser()
        view = views.HomeView()
        view.setup(request)
        with patch.object(views, 'visible_knowledge_queryset') as visible:
            visible.return_value.count.return_value = 3
            context = view.get_context_data()
        items = [item for group in context['menu_groups'] for item in group['items']]
        self.assertIn({'label': 'Q&A', 'url_name': 'article_list'}, items)
        self.assertEqual(context['home_faq_count'], 3)
        self.assertIn('recent_faqs', context)

    def test_form_titles_and_template_source_keep_internal_faq_names(self):
        for view, expected in ((views.KnowledgeArticleCreateView, 'Q&A登録'),
                       (views.KnowledgeArticleUpdateView, 'Q&A編集')):
            source = inspect.getsource(view.get_context_data)
            self.assertIn("context['form_title'] = '" + expected + "'", source)
        path = Path(__file__).parent / 'templates/tenasapo_knowledge/article_form.html'
        source = path.read_text(encoding='utf-8')
        title_block = source.split('{% block title %}', 1)[1].split('{% endblock %}', 1)[0]
        for title, expected in (('Q&A登録', 'Q&A登録'), ('Q&A編集', 'Q&A編集'),
                                ('FAQ登録', 'Q&A登録'), ('FAQ編集', 'Q&A編集'),
                                ('', 'Q&A登録')):
            with self.subTest(title=title):
                rendered = Template(title_block).render(Context({'form_title': title}))
                self.assertIn(escape(expected), rendered)
                self.assertNotIn('FAQ', rendered)
                self.assertNotIn('Q&amp;amp;A', rendered)
        self.assertIn('Q&amp;Aの質問・回答', source)
        self.assertIn('default:"Q&amp;A登録"', source)
        self.assertIn('id="faqPreviewModal"', source)
        self.assertIn('id="previewFaqAnswer"', source)

    def test_rendered_headings_and_sidebar_escape_q_and_a_once(self):
        root = Path(__file__).parent / 'templates/tenasapo_knowledge'
        for filename, title in (('article_list.html', 'Q&A'),
                                ('article_form.html', 'Q&A登録')):
            source = (root / filename).read_text(encoding='utf-8')
            heading = source.split('{% block page_header %}', 1)[1].split('{% endblock %}', 1)[0]
            rendered = Template(heading).render(Context({'form_title': title}))
            with self.subTest(template=filename):
                self.assertIn('<h1', rendered)
                self.assertIn(escape(title), rendered)
                self.assertNotIn('Q&A', rendered)
                self.assertNotIn('Q&amp;amp;A', rendered)
        sidebar = render_to_string('includes/sidebar_menu.html', {
            'user': AnonymousUser(), 'current_user_is_customer': True,
        })
        self.assertIn('href="' + views.reverse('article_list') + '"', sidebar)
        self.assertIn('</i>Q&amp;A</a>', sidebar)
        self.assertNotIn('Q&A', sidebar)
        self.assertNotIn('Q&amp;amp;A', sidebar)

    def test_template_sources_distinguish_html_and_javascript_labels(self):
        includes = Path(__file__).parent.parent / 'templates/includes'
        for filename in ('home_member_posts.html', 'knowledge_duplicate_check.html',
                         'sidebar_menu.html'):
            with self.subTest(template=filename):
                source = (includes / filename).read_text(encoding='utf-8')
                self.assertIn('Q&amp;A', source)
                self.assertNotIn('Q&A', source)
        chart = (includes / 'home_member_posts_assets.html').read_text(encoding='utf-8')
        duplicate = (includes / 'knowledge_duplicate_check_assets.html').read_text(encoding='utf-8')
        self.assertIn("{label: 'Q&A',", chart)
        self.assertIn("{FAQ: 'Q&A', Tips: 'Tips'}", duplicate)
        for source in (chart, duplicate):
            self.assertNotIn('Q&amp;A', source)
        summary = (Path(__file__).parent / 'templates/tenasapo_knowledge/summary.html').read_text(encoding='utf-8')
        self.assertIn('>Q&amp;A</th>', summary)
        self.assertIn("label: 'Q&A',", summary)

    def test_report_headers_and_manual_defaults_change_only_display_names(self):
        source = inspect.getsource(views)
        for label in ('Q&A投稿数', 'Q&A承認数'):
            self.assertIn(label, source)
        self.assertIn("['メンバー', '投稿数', 'Q&A', 'Tips'", source)
        for key in ('faq_post_count', 'faq_approved_count', 'customer_faq_goods',
                    'customer_faq_good_total', 'internal_top_faq_articles'):
            self.assertIn("'" + key + "'", source)
        definitions = {item.menu_key: item for item in OPERATION_MANUALS}
        self.assertEqual(definitions['faq'].title, 'Q&A')
        self.assertEqual(definitions['faq'].url_name, 'article_list')
        for item in OPERATION_MANUALS:
            self.assertNotIn('FAQ', item.title + item.description)
            self.assertNotIn('QA', item.title + item.description)
            self.assertNotIn('Q&amp;A', item.title + item.description)

    def test_answer_tracking_retains_legacy_history_name_and_response_keys(self):
        request = RequestFactory().post('/faq/track/', json.dumps({'article_id': 1}),
                                        content_type='application/json')
        request.user = SimpleNamespace(is_authenticated=True)
        article = SimpleNamespace(title='既存記事', answer_view_count=4, created_by=None)
        with patch.object(views, 'get_object_or_404', return_value=article), \
                patch.object(views, 'can_user_access_article', return_value=True), \
                patch.object(views, 'should_count_content_view', return_value=False), \
                patch.object(views, 'record_view_history') as record:
            response = views.FAQAnswerViewTrackView.as_view()(request)
        self.assertEqual(json.loads(response.content), {'ok': True, 'answer_view_count': 4})
        self.assertEqual(record.call_args.kwargs['page_name'], 'FAQ回答表示: 既存記事')
        source = inspect.getsource(views)
        self.assertIn("page_name__startswith='FAQ回答表示:'", source)
        self.assertIn("removeprefix('FAQ回答表示:')", source)
        # All existing history producers, including list/reactions, keep their names.
        for label in ('FAQ一覧', 'FAQグッド:', 'FAQお気に入り:'):
            self.assertIn(label, source)

    def test_duplicate_api_keeps_faq_kind_and_original_envelope(self):
        request = RequestFactory().get('/knowledge/duplicate-candidates/', {'registered_category': 1})
        request.user = SimpleNamespace(is_authenticated=True, is_staff=True)
        queryset = MagicMock()
        queryset.values.return_value.iterator.return_value = iter([{
            'title': '既存記事', 'management_code': 'FQ000001',
            'category': 'PC/設定', 'updated_at': timezone.now(),
        }])
        empty = MagicMock()
        empty.values.return_value.iterator.return_value = iter([])
        with patch.object(FAQCategory.objects, 'filter') as categories, \
                patch.object(views, 'visible_knowledge_queryset', side_effect=[queryset, empty]):
            categories.return_value.order_by.return_value = [SimpleNamespace(full_name='PC/設定')]
            response = views.KnowledgeDuplicateCandidatesView.as_view()(request)
        payload = json.loads(response.content)
        self.assertEqual(set(payload), {'categories', 'results', 'total', 'page', 'pages'})
        self.assertEqual(payload['results'][0]['kind'], 'FAQ')
        self.assertIn('FQ000001', payload['results'][0]['url'])


class QANotificationLabelTests(SimpleTestCase):
    def test_expiry_mail_subject_body_and_summary_use_qa_without_sending(self):
        command = notify_expiring_articles.Command(stdout=StringIO())
        item = SimpleNamespace(title='確認記事', category='PC', expires_on=date(2026, 11, 19),
                               created_by=SimpleNamespace(email='author@example.test', username='author'),
                               approved_by=None, created_by_name='投稿者', approved_by_name='')
        with patch.object(notify_expiring_articles.KnowledgeArticle.objects, 'filter') as faq, \
                patch.object(notify_expiring_articles.TipsArticle.objects, 'filter') as tips, \
                patch.object(notify_expiring_articles, 'send_mail') as send:
            faq.return_value.select_related.return_value = [item]
            tips.return_value.select_related.return_value = []
            command.handle(dry_run=False)
        self.assertIn('Q&A「確認記事」', send.call_args.kwargs['subject'])
        self.assertIn('Q&Aの掲載期限', send.call_args.kwargs['message'])
        self.assertIn('Q&A 1件 / Tips 0件', command.stdout.getvalue())

    def test_unpublish_dry_run_uses_qa_and_does_not_save(self):
        command = unpublish_expired_articles.Command(stdout=StringIO())
        item = MagicMock(title='確認記事', id=1)
        with patch.object(unpublish_expired_articles.KnowledgeArticle.objects, 'filter', return_value=[item]), \
                patch.object(unpublish_expired_articles.TipsArticle.objects, 'filter', return_value=[]):
            command.handle(dry_run=True)
        self.assertIn('[dry-run] Q&A「確認記事」', command.stdout.getvalue())
        self.assertIn('Q&A 1件 / Tips 0件', command.stdout.getvalue())
        item.save.assert_not_called()

    def test_monthly_text_and_image_labels_keep_count_keys_and_filename(self):
        command = notify_monthly_posts.Command(stdout=StringIO())
        with patch.object(notify_monthly_posts, 'get_user_model') as users, \
                patch.object(notify_monthly_posts.KnowledgeArticle.objects, 'filter') as faq, \
                patch.object(notify_monthly_posts.TipsArticle.objects, 'filter') as tips, \
                patch.object(notify_monthly_posts, 'send_google_chat_message') as send:
            users.return_value.objects.filter.return_value.distinct.return_value.prefetch_related.return_value.filter.return_value.order_by.return_value = []
            faq.return_value.select_related.return_value = []
            tips.return_value.select_related.return_value = []
            command.handle(dry_run=True, text_only=True)
        send.assert_not_called()
        self.assertIn('Q&A1件登録', command.stdout.getvalue())
        self.assertIn('|  Q&A  | Tips', command.stdout.getvalue())
        with patch.object(notify_monthly_posts.ImageDraw, 'Draw') as draw:
            image = command.generate_member_chart_image([], date(2026, 11, 12))
        self.assertTrue(image.startswith(b'\x89PNG'))
        labels = [call.args[1] for call in draw.return_value.text.call_args_list]
        self.assertIn('Q&A', labels)
        self.assertNotIn('FAQ', labels)
        constants = [node.value for node in ast.walk(ast.parse(inspect.getsource(notify_monthly_posts)))
                     if isinstance(node, ast.Constant) and isinstance(node.value, str)]
        for preserved in ('faq_count', 'tips_count', 'nexus_monthly_chart_dryrun.png'):
            self.assertIn(preserved, constants)
        for module in (notify_expiring_articles, notify_monthly_posts, unpublish_expired_articles):
            self.assertIn('Q&A', module.Command.help)
            self.assertNotIn('FAQ', module.Command.help)