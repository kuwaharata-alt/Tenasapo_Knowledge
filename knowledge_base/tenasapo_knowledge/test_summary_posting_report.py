from datetime import date
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase
from django.urls import reverse

from .models import KnowledgeArticle, TipsArticle, UserProfile
from .views import SummaryView, KnowledgeArticleDetailView, TipsArticleDetailView
from .views import SummaryPostingReportPDFView, CONTRIBUTOR_GROUP_NAME
from .posting_report_pdf import build_posting_report_pdf, member_chart, content_text


class SummaryPostingReportTests(TestCase):
    def test_pdf_contains_combined_report_and_uses_report_month(self):
        context = self.response({'report_month': '2026-11', 'period': 'previous'}).context_data
        request = RequestFactory().get(reverse('summary_posting_report_pdf'),
                                       {'report_month': '2026-11', 'period': 'previous'})
        request.user = self.staff
        with patch.object(SummaryView, 'get_context_data', return_value=context) as get_context:
            response = SummaryPostingReportPDFView.as_view()(request)
        get_context.assert_called_once()
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('posting_report_2026-11.pdf', response['Content-Disposition'])
        self.assertTrue(response.content.startswith(b'%PDF-'))
        self.assertIn(b'/HeiseiKakuGo-W5', response.content)
        self.assertGreater(len(response.content), 3000)
        self.assertEqual(context['selected_period'], 'previous')
        self.assertEqual(context['posting_targets']['target_month'], date(2026, 10, 1))
        self.assertEqual(context['report_month'], '2026-11')
        self.assertEqual(context['report_half_year_target']['start'], date(2026, 11, 1))
        self.assertContains(self.response(), 'id="btnPostingReportPdf"')
        self.assertNotContains(self.response(), 'id="btnPdfReport"')

    def test_report_context_supplies_real_attachment_fields_and_reference_links(self):
        from .models import ArticleAttachment, KnowledgeArticleImageAttachment, TipsImageAttachment

        context = self.response({'report_month': '2026-11'}).context_data
        faq_row = next(row for row in context['report_articles'] if row['kind'] == 'Q&A')
        tips_row = next(row for row in context['report_articles'] if row['kind'] == 'Tips')
        question = ArticleAttachment.objects.create(article_id=faq_row['id'], placement='question', file='test/question.png')
        answer = KnowledgeArticleImageAttachment.objects.create(article_id=faq_row['id'], file='test/answer.png')
        tip_image = TipsImageAttachment.objects.create(tip_id=tips_row['id'], file='test/tips.png')
        links = [{'label': '参考資料', 'url': 'https://example.com/reference'}]
        TipsArticle.objects.filter(pk=tips_row['id']).update(reference_links=links)
        context = self.response({'report_month': '2026-11'}).context_data
        faq_row = next(row for row in context['report_articles'] if row['kind'] == 'Q&A')
        tips_row = next(row for row in context['report_articles'] if row['kind'] == 'Tips')
        self.assertEqual(faq_row['question_images'], [question])
        self.assertEqual(faq_row['body_images'], [answer])
        self.assertEqual(tips_row['body_images'], [tip_image])
        self.assertEqual(tips_row['reference_links'], links)

    def test_report_members_include_zero_and_follow_month_visibility_and_saved_name(self):
        group, _ = Group.objects.get_or_create(name=CONTRIBUTOR_GROUP_NAME)
        users = {}
        for number, name in enumerate(('投稿者', 'ゼロ', '非表示'), 1):
            user = get_user_model().objects.create_user('monthly-member-' + str(number))
            user.groups.add(group)
            UserProfile.objects.create(user=user, display_name=name, uid=f'{number:06}',
                                       exclude_from_analysis=name == '非表示')
            users[name] = user
        for model, author, saved, at, visible in (
            (KnowledgeArticle, users['投稿者'], '', '2026-10-31T15:00:00+00:00', True),
            (TipsArticle, self.staff, '投稿者', '2026-11-30T14:59:59+00:00', True),
            (KnowledgeArticle, users['投稿者'], '', '2026-10-31T14:59:59+00:00', True),
            (TipsArticle, users['投稿者'], '', '2026-11-30T15:00:00+00:00', True),
            (TipsArticle, users['投稿者'], '', '2026-11-10T00:00:00+00:00', False),
            (KnowledgeArticle, users['非表示'], '', '2026-11-10T00:00:00+00:00', True),
        ):
            article = model.objects.create(title='対象月テスト', body='本文', created_by=author,
                                           created_by_name=saved, visible_to_customer=False,
                                           visible_to_systena=visible, is_published=False, is_approved=False)
            model.objects.filter(pk=article.pk).update(created_at=at)
        for period in ('all', 'current', 'previous'):
            context = self.response({'report_month': '2026-11', 'period': period}).context_data
            rows = context['report_member_summaries']
            self.assertEqual([row['name'] for row in rows], ['投稿者', 'ゼロ'])
            self.assertEqual([row['post_count_total'] for row in rows], [2, 0])
            self.assertEqual(context['report_member_totals'],
                             dict(faq_post_count=1, tips_post_count=1, post_count_total=2))
        context = self.response({'report_month': '2026-11', 'include_hidden': '1'}).context_data
        self.assertEqual(len(context['report_member_summaries']), 3)
        self.assertEqual(context['report_member_totals']['post_count_total'], 3)
        context = self.response({'report_month': '2026-12'}).context_data
        self.assertEqual(context['report_member_totals']['post_count_total'], 1)

    def test_pdf_only_half_year_goal_and_selected_month_member_rows(self):
        from reportlab.platypus import Paragraph, Table

        context = self.response({'report_month': '2026-11', 'period': 'previous'}).context_data
        context['report_member_summaries'] = [dict(name='<全員 & ゼロ>', management_uid='000100',
                                                   faq_post_count=0, tips_post_count=0, post_count_total=0)]
        captured_tables, paragraphs = [], []

        def capture_table(data, **options):
            captured_tables.append(data)
            return Table(data, **options)

        def capture_paragraph(text, style):
            paragraphs.append(text)
            return Paragraph(text, style)

        with patch('tenasapo_knowledge.posting_report_pdf.Table', side_effect=capture_table), \
             patch('tenasapo_knowledge.posting_report_pdf.Paragraph', side_effect=capture_paragraph), \
             patch('tenasapo_knowledge.posting_report_pdf.member_chart', wraps=member_chart) as chart:
            self.assertTrue(build_posting_report_pdf(context).startswith(b'%PDF-'))
        self.assertEqual(len(captured_tables[0]), 2)  # Header + half-year goal only.
        self.assertIn('半期目標（全メンバー）', paragraphs)
        self.assertNotIn('累積目標（1人あたり・本人実績）', paragraphs)
        self.assertNotIn('月間目標（1人あたり・本人実績）', paragraphs)
        self.assertIn('&lt;全員 &amp; ゼロ&gt;', paragraphs)
        self.assertIn('投稿なし', paragraphs)
        self.assertEqual(chart.call_args.args[0], context['report_member_summaries'])
        self.assertIsNone(chart.call_args.args[1]['value'])

    def test_pdf_missing_counts_are_red_but_achieved_and_unset_are_not(self):
        from reportlab.lib import colors
        from reportlab.platypus import Table

        context = self.response({'report_month': '2026-11', 'period': 'previous'}).context_data
        context['report_member_summaries'] = [
            dict(name=name, management_uid='', faq_post_count=faq, tips_post_count=tips,
                 post_count_total=faq + tips)
            for name, faq, tips in [('基本不足', 0, 5), ('追加不足', 1, 1), ('達成', 2, 1), ('ゼロ', 0, 0)]
        ]
        red = colors.HexColor('#c62828')
        for target in (dict(faq=1, tips=1, alpha=1, total=3), None,
                       dict(faq=0, tips=0, alpha=0, total=0)):
            context['report_monthly_target'] = target
            captured = []

            def capture(data, **options):
                captured.append(data)
                return Table(data, **options)

            with patch('tenasapo_knowledge.posting_report_pdf.Table', side_effect=capture):
                build_posting_report_pdf(context)
            rows = captured[1]
            if target and target['total']:
                self.assertEqual(rows[1][2].style.textColor, red)
                self.assertNotEqual(rows[1][3].style.textColor, red)
                self.assertEqual(rows[1][4].style.textColor, red)  # Total met, base missing.
                self.assertEqual(rows[2][4].style.textColor, red)  # Alpha missing.
                self.assertNotEqual(rows[2][2].style.textColor, red)
                self.assertNotEqual(rows[3][4].style.textColor, red)
                self.assertEqual(rows[3][5].getPlainText(), '達成')
                self.assertEqual(rows[4][4].style.textColor, red)
            else:
                self.assertTrue(all(cell.style.textColor != red for row in rows for cell in row))

    def test_pdf_handles_long_article_list_and_empty_report(self):
        context = self.response({'report_month': '2026-11'}).context_data
        context['report_articles'] = [dict(context['report_articles'][0],
                                          title='<script> & 長いタイトル ' * 8,
                                          body='<p>本文 &amp; テスト</p>' * 80) for _ in range(70)]
        output = build_posting_report_pdf(context)
        self.assertTrue(output.startswith(b'%PDF-'))
        context['report_articles'] = []
        context['member_summaries'] = []
        self.assertTrue(build_posting_report_pdf(context).startswith(b'%PDF-'))

    def test_report_content_and_pdf_explicit_line_breaks(self):
        context = self.response({'report_month': '2026-11'}).context_data
        self.assertEqual(context['report_articles'][0]['body'], '本文')
        self.assertEqual(context['report_articles'][1]['question'], '月初Q&A')
        self.assertEqual(content_text('<p>質問 &amp; 内容</p><p>回答<br>次の行</p><script>secret</script>'),
                         '質問 & 内容\n回答\n次の行')
        from reportlab.platypus import Paragraph
        paragraphs = []
        def capture(text, style):
            paragraphs.append(text)
            return Paragraph(text, style)
        with patch('tenasapo_knowledge.posting_report_pdf.Paragraph', side_effect=capture):
            build_posting_report_pdf(context)
        code = context['report_articles'][1]['management_code']
        self.assertIn('月初Q&amp;A', paragraphs)
        self.assertIn(code, paragraphs)
        self.assertIn('本文', paragraphs)
        self.assertIn('投稿ナレッジの内容', paragraphs)

    def test_report_list_column_order_matches_screen_and_pdf(self):
        import re
        from reportlab.platypus import Table

        response = self.response({'report_month': '2026-11'})
        expected = ['管理番号', '種類', 'カテゴリ', 'タイトル', '承認状況', '投稿者', '投稿日時']
        html = response.content.decode()
        report_html = html.split('id="posting-report-table"', 1)[1].split('</table>', 1)[0]
        self.assertEqual(re.findall(r'<th scope="col">(.*?)</th>', report_html), expected)
        tables = []

        def capture(data, **options):
            tables.append(data)
            return Table(data, **options)

        with patch('tenasapo_knowledge.posting_report_pdf.Table', side_effect=capture):
            build_posting_report_pdf(response.context_data)
        rows = next(data for data in tables if [cell.getPlainText() for cell in data[0]] == expected)
        article = response.context_data['report_articles'][0]
        self.assertEqual([cell.getPlainText() for cell in rows[1]], [
            article['management_code'] or '—', article['kind'], article['category'] or '—',
            article['title'], '承認済み' if article['is_approved'] else '未承認',
            article['creator_name'], article['created_at'].strftime('%Y/%m/%d %H:%M'),
        ])

    def test_pdf_article_details_are_labeled_tables(self):
        from reportlab.platypus import Table

        context = self.response({'report_month': '2026-11'}).context_data
        tables = []

        def capture(data, **kwargs):
            tables.append((data, kwargs))
            return Table(data, **kwargs)

        with patch('tenasapo_knowledge.posting_report_pdf.Table', side_effect=capture):
            output = build_posting_report_pdf(context)
        self.assertTrue(output.startswith(b'%PDF-'))
        details = [(data, options) for data, options in tables if options.get('splitInRow') == 1]
        self.assertEqual(len(details), 2)
        for (data, options), article in zip(details, context['report_articles']):
            labels = [row[0].getPlainText() for row in data]
            self.assertEqual(labels[:4], ['タイトル', '管理番号', '投稿日時', 'カテゴリ'])
            self.assertEqual(labels[4:], ['質問', '回答'] if article['kind'] == 'Q&A' else ['内容'])
            self.assertEqual(data[1][2].getPlainText(), '種類')
            self.assertEqual(data[1][3].getPlainText(), article['kind'])
            self.assertEqual(data[2][2].getPlainText(), '投稿者')
            self.assertEqual(data[2][4].getPlainText(), '承認状況')
            self.assertEqual(data[1][1].getPlainText(), article['management_code'])
            self.assertEqual(options['repeatRows'], 0)

    def test_pdf_chart_heading_stays_on_chart_page(self):
        from reportlab.graphics.shapes import Drawing
        from reportlab.platypus import Paragraph

        context = self.response({'report_month': '2026-11'}).context_data
        original_paragraph_draw = Paragraph.draw
        original_chart_draw = Drawing.draw
        for count in (4, 20):
            context['report_member_summaries'] = [dict(name=f'メンバー{index}', management_uid=str(index),
                                                       faq_post_count=0, tips_post_count=0,
                                                       post_count_total=0) for index in range(count)]
            heading_pages, chart_pages = [], []

            def paragraph_draw(paragraph):
                if 'メンバー投稿数（' in paragraph.getPlainText():
                    heading_pages.append(paragraph.canv.getPageNumber())
                return original_paragraph_draw(paragraph)

            def chart_draw(drawing, *args, **kwargs):
                chart_pages.append(drawing.canv.getPageNumber())
                return original_chart_draw(drawing, *args, **kwargs)

            with patch.object(Paragraph, 'draw', paragraph_draw), patch.object(Drawing, 'draw', chart_draw):
                build_posting_report_pdf(context)
            self.assertEqual(heading_pages, chart_pages, f'members={count}')
            self.assertEqual(chart_pages[0], 1, f'first chart stays on page one: members={count}')

    def test_pdf_article_title_and_body_start_on_same_page(self):
        from reportlab.platypus import Paragraph

        context = self.response({'report_month': '2026-11'}).context_data
        original = context['report_articles'][0]
        context['report_articles'] = [dict(original, title=f'改ページ確認記事{index}',
                                          body=f'<p>本文開始確認{index}</p>' + '<p>本文の行</p>' * 10)
                                      for index in range(3)]
        pages = {}
        original_draw = Paragraph.draw

        def capture_draw(paragraph):
            pages.setdefault(paragraph.getPlainText(), []).append(paragraph.canv.getPageNumber())
            return original_draw(paragraph)

        with patch.object(Paragraph, 'draw', capture_draw):
            build_posting_report_pdf(context)
        article_pages = []
        for index in range(3):
            title = f'{index + 1}. [Tips] 改ページ確認記事{index}'
            page = pages[title][0]
            self.assertEqual(pages[f'本文開始確認{index}'], [page])
            article_pages.append(page)
        self.assertEqual(article_pages, list(range(article_pages[0], article_pages[0] + 3)))

    def test_pdf_table_splits_single_long_paragraph_without_losing_tail(self):
        from reportlab.platypus import Paragraph

        context = self.response({'report_month': '2026-11'}).context_data
        context['report_articles'] = [dict(context['report_articles'][1],
                                           body='<p>' + '長文の回答です。' * 3000 + '末尾確認</p>')]
        rendered = []
        original_draw = Paragraph.draw

        def capture_draw(paragraph):
            for line in getattr(paragraph.blPara, 'lines', []):
                # Plain paragraphs use (extraSpace, words); rich text uses fragments.
                words = line[1] if isinstance(line, tuple) else line.words
                rendered.extend(word if isinstance(word, str) else getattr(word, 'text', '')
                                for word in words)
            return original_draw(paragraph)

        with patch.object(Paragraph, 'draw', capture_draw):
            output = build_posting_report_pdf(context)
        self.assertTrue(output.startswith(b'%PDF-'))
        text = ''.join(rendered)
        self.assertIn('末尾確認', text)

    def test_pdf_chart_handles_zero_unset_and_high_targets(self):
        from reportlab.graphics.shapes import String

        members = [{'name': '日本語メンバー', 'faq_post_count': 0, 'tips_post_count': 0}]
        for value in (None, 0, 1000):
            drawing = member_chart(members, {'value': value, 'label': '目標'}, max(5, value or 0))
            labels = [item.text for item in drawing.contents if isinstance(item, String)]
            self.assertEqual(any(text.startswith('目標：') for text in labels), value is not None)

    def test_summary_chart_qa_bar_and_legend_are_red(self):
        group, _ = Group.objects.get_or_create(name=CONTRIBUTOR_GROUP_NAME)
        self.staff.groups.add(group)
        UserProfile.objects.create(user=self.staff, display_name='グラフ確認メンバー',
                                   uid='000001', exclude_from_analysis=False)
        response = self.response()
        self.assertEqual(len(response.context_data['member_summaries']), 1)
        self.assertContains(response, "backgroundColor: 'rgba(220, 53, 69, 0.82)'")
        self.assertContains(response, 'background-color: rgba(220, 53, 69, 0.82)')
        self.assertContains(response, "backgroundColor: 'rgba(73, 136, 232, 0.82)'")

    def test_pdf_chart_qa_bar_and_legend_are_red(self):
        from reportlab.graphics.shapes import Rect
        from reportlab.lib import colors

        drawing = member_chart([dict(name='テスト', faq_post_count=1, tips_post_count=1)],
                               {'value': None, 'label': ''}, 4)
        rectangles = [item for item in drawing.contents if isinstance(item, Rect)]
        self.assertEqual([item.fillColor for item in rectangles], [
            colors.HexColor('#dc3545'), colors.HexColor('#4988e8'),
            colors.HexColor('#dc3545'), colors.HexColor('#4988e8'),
        ])

    def test_pdf_chart_ticks_are_one_count_apart(self):
        from reportlab.graphics.shapes import String

        for maximum in (1, 4, 5, 12):
            drawing = member_chart([], {'value': None, 'label': ''}, maximum)
            ticks = [item.text for item in drawing.contents
                     if isinstance(item, String) and item.textAnchor == 'end']
            self.assertEqual(ticks, [str(value) for value in range(maximum + 1)])

    def test_pdf_logo_is_drawn_on_every_page_without_distortion(self):
        from reportlab.pdfgen.canvas import Canvas

        context = self.response({'report_month': '2026-11'}).context_data
        pages = []
        original = Canvas.drawImage

        def capture(canvas, image, x, y, **options):
            pages.append(canvas.getPageNumber())
            self.assertAlmostEqual(options['width'] / options['height'], 779 / 246)
            self.assertGreater(y, canvas._pagesize[1] - 51)
            return original(canvas, image, x, y, **options)

        with patch.object(Canvas, 'drawImage', capture):
            output = build_posting_report_pdf(context)
        self.assertIn(b'/Subtype /Image', output)
        self.assertGreater(len(pages), 1)
        self.assertEqual(pages, list(range(1, len(pages) + 1)))

    @classmethod
    def setUpTestData(cls):
        cls.staff = get_user_model().objects.create_user(username='report-staff', is_staff=True)
        for model, title, posted_at, visible, published in (
            (KnowledgeArticle, '月初Q&A', '2026-10-31T15:00:00+00:00', True, True),
            (TipsArticle, '<script>月末Tips</script>', '2026-11-30T14:59:59+00:00', True, False),
            (KnowledgeArticle, '前月Q&A', '2026-10-31T14:59:59+00:00', True, True),
            (TipsArticle, '翌月Tips', '2026-11-30T15:00:00+00:00', True, True),
            (KnowledgeArticle, '表示対象外', '2026-11-15T00:00:00+00:00', False, True),
        ):
            article = model.objects.create(title=title, body='本文', created_by=cls.staff,
                                           visible_to_customer=visible, visible_to_systena=False,
                                           is_published=published)
            model.objects.filter(pk=article.pk).update(created_at=posted_at)

    def response(self, params=None, user=None):
        request = RequestFactory().get(reverse('summary'), params or {})
        request.user = user or self.staff
        request.session = {}
        request._messages = FallbackStorage(request)
        with patch('tenasapo_knowledge.views.timezone.localdate', return_value=date(2026, 11, 13)), \
            patch('tenasapo_knowledge.views.record_view_history'):
            response = SummaryView.as_view()(request)
            if hasattr(response, 'render'):
                response.render()
            return response

    def test_selected_month_combines_types_and_local_boundaries(self):
        response = self.response({'report_month': '2026-11', 'tab': 'report'})
        context = response.context_data
        self.assertEqual([row['title'] for row in context['report_articles']],
                         ['<script>月末Tips</script>', '月初Q&A'])
        self.assertEqual(context['report_totals'], {'total': 2, 'faq': 1, 'tips': 1})
        self.assertTrue(context['report_active'])
        self.assertEqual(context['report_articles'][0]['detail_url'], '')
        self.assertContains(response, '&lt;script&gt;月末Tips&lt;/script&gt;')
        self.assertNotContains(response, '<script>月末Tips</script>')
        self.assertContains(response, 'id="internal-pane"')
        self.assertContains(response, '投稿レポート')
        self.assertNotContains(response, 'Nexus 月次レポート')

    def test_default_and_invalid_month_fall_back_to_current_month(self):
        for value in (None, '', '2026-13', '2026-1', 'invalid', '9999-12'):
            with self.subTest(value=value):
                response = self.response({} if value is None else {'report_month': value})
                self.assertEqual(response.context_data['report_month'], '2026-11')

    def test_empty_month_and_other_analytics_unchanged(self):
        before = self.response({'period': 'previous'}).context_data
        response = self.response({'report_month': '2027-01', 'period': 'previous', 'sort_by': 'post_count'})
        after = response.context_data
        self.assertEqual(after['report_articles'], [])
        self.assertContains(response, 'この月に投稿されたナレッジはありません。')
        self.assertEqual(before['summary_totals'], after['summary_totals'])
        self.assertEqual(before['customer_faq_good_total'], after['customer_faq_good_total'])
        self.assertContains(response, 'name="period" value="previous"')
        self.assertContains(response, 'name="sort_by" value="post_count"')

    def test_non_staff_cannot_access_report(self):
        user = get_user_model().objects.create_user(username='report-regular')
        response = self.response({'report_month': '2026-11'}, user=user)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('article_list'))

    def test_report_detail_link_preserves_month_filters_and_tab(self):
        response = self.response({'report_month': '2026-11', 'period': 'previous',
                                  'sort_by': 'post_count', 'include_hidden': '1'})
        link = response.context_data['report_articles'][1]['detail_url']
        back = parse_qs(urlsplit(link).query)['next'][0]
        self.assertEqual(urlsplit(back).path, reverse('summary'))
        self.assertEqual(parse_qs(urlsplit(back).query), {
            'report_month': ['2026-11'], 'period': ['previous'],
            'sort_by': ['post_count'], 'include_hidden': ['1'], 'tab': ['report'],
        })

    def test_both_detail_views_return_to_analysis_or_safe_default(self):
        for model, view, fallback in (
            (KnowledgeArticle, KnowledgeArticleDetailView, 'article_list'),
            (TipsArticle, TipsArticleDetailView, 'tip_list'),
        ):
            article = model.objects.create(title='戻り先テスト', body='本文', is_published=True,
                                           visible_to_customer=True, created_by=self.staff)
            for target in ('/summary/?tab=report&report_month=2026-11&sort_by=post_count',
                           '', 'https://evil.example/summary/', '//evil.example/summary/',
                           '/knowledge/', '/summary/pdf/', '/\\evil.example/summary/'):
                with self.subTest(model=model.__name__, target=target):
                    request = RequestFactory().get('/detail/', {'next': target})
                    request.user = self.staff
                    request.session = {}
                    request._messages = FallbackStorage(request)
                    response = view.as_view()(request, management_code=article.management_code)
                    response.render()
                    valid = target.startswith('/summary/?')
                    self.assertEqual(response.context_data['detail_back_url'],
                                     target if valid else reverse(fallback))
                    if valid:
                        self.assertContains(response, 'アナライズに戻る')