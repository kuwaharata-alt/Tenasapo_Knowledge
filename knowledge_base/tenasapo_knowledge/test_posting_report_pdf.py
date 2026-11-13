"""PDF-only regression tests; no database, network or real attachment files."""

from datetime import date, datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from PIL import Image as PillowImage
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Image, Paragraph, Table

from . import posting_report_pdf as pdf


def report_context(**article_changes):
    article = dict(management_code='QA-0001', kind='Q&A', title='固有の記事タイトル',
                   category='カテゴリ', creator_name='投稿者', is_approved=True,
                   created_at=datetime(2026, 1, 10, 12), question='質問本文', body='回答本文')
    article.update(article_changes)
    return dict(report_month='2026-01', report_month_start=date(2026, 1, 1),
                report_half_year_target=dict(start=date(2025, 11, 1), end=date(2026, 4, 30),
                                             actual=1, target=10, progress_percent=10, faq=1, tips=0),
                report_member_summaries=[], report_monthly_target=None,
                report_member_totals=dict(faq_post_count=1, tips_post_count=0, post_count_total=1),
                report_totals=dict(total=1, faq=1, tips=0), report_articles=[article])


class PostingReportPDFTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if pdf.FONT not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(UnicodeCIDFont(pdf.FONT))
        cls.style = ParagraphStyle('pdf-test', fontName=pdf.FONT, fontSize=9,
                                   leading=14, wordWrap='CJK', splitLongWords=1)

    def attachment(self, data=None, url='/media/allowed.png', size=(160, 80)):
        if data is None:
            stream = BytesIO()
            PillowImage.new('RGB', size, 'blue').save(stream, 'PNG')
            data = stream.getvalue()
        storage = Mock()
        storage.open.side_effect = lambda *args: BytesIO(data)
        field = SimpleNamespace(url=url, name='allowed.png', storage=storage)
        return SimpleNamespace(image=field, display_name='添付画像.png')

    def text(self, flows):
        return ''.join(flow.getPlainText() for flow in flows if isinstance(flow, Paragraph))

    def drawn_paragraphs(self, context):
        rendered, paragraphs = [], []
        original = Paragraph.draw

        def draw(paragraph):
            page = paragraph.canv.getPageNumber()
            paragraphs.append((page, paragraph.getPlainText()))
            for line in getattr(paragraph.blPara, 'lines', []):
                words = line[1] if isinstance(line, tuple) else line.words
                rendered.append((page, ''.join(word if isinstance(word, str) else getattr(word, 'text', '')
                                               for word in words)))
            return original(paragraph)

        with patch.object(Paragraph, 'draw', draw):
            output = pdf.build_posting_report_pdf(context)
        self.assertTrue(output.startswith(b'%PDF-'))
        return paragraphs, rendered

    def test_month_uses_start_date_without_mutating_context(self):
        context = report_context()
        context['report_month'] = 'unchanged-machine-value'
        page_headers = []
        original = Canvas.drawRightString

        def draw(canvas, x, y, text, *args, **kwargs):
            page_headers.append(text)
            return original(canvas, x, y, text, *args, **kwargs)

        with patch.object(Canvas, 'drawRightString', draw):
            paragraphs, _ = self.drawn_paragraphs(context)
        text = ''.join(text for _, text in paragraphs)
        self.assertIn('出力対象月：2026年1月', text)
        self.assertIn('2026年1月 の投稿ナレッジ一覧', text)
        self.assertIn('メンバー投稿数（2026年1月）', text)
        self.assertLess(text.index('メンバー投稿数（2026年1月）'),
                text.index('2026年1月 のメンバー別投稿状況'))
        chart_page = next(page for page, value in paragraphs if value == 'メンバー投稿数（2026年1月）')
        member_page = next(page for page, value in paragraphs if value == '2026年1月 のメンバー別投稿状況')
        self.assertEqual(member_page, chart_page + 1)
        self.assertGreaterEqual(member_page, 2)
        self.assertIn('Nexus 月次レポート | 2026年1月', page_headers)
        self.assertNotIn('unchanged-machine-value', text)
        self.assertEqual(context['report_month'], 'unchanged-machine-value')

    def test_html_is_rebuilt_with_breaks_and_hidden_content_removed(self):
        flows = pdf.article_content(
            '<p>前&amp;文<br>改行</p><script>秘密</script><style>非表示</style>'
            '<p><b>後文</b><a href="https://example.com/?a=1&amp;b=2">参照</a></p>',
            [], self.style, 500)
        text = self.text(flows)
        self.assertIn('前&文', text)
        self.assertIn('改行', text)
        self.assertIn('後文', text)
        self.assertIn('https://example.com/?a=1&b=2', text)
        self.assertNotIn('秘密', text)
        self.assertNotIn('非表示', text)
        self.assertTrue(any('<br/>' in flow.text for flow in flows))

    def test_links_are_escaped_visible_and_only_http_s_clickable(self):
        links = [dict(label='<資料 & "説明">', url='https://example.com/?q="x"&a=1'),
                 'http://example.org/a', 'javascript:alert(1)', 'file:///C:/secret',
                 'data:text/html,test', '//example.org/x']
        flows = pdf.reference_content(links, self.style)
        self.assertIn('<資料 & "説明">', flows[0].getPlainText())
        self.assertIn('https://example.com/', flows[0].getPlainText())
        self.assertIn('&quot;', flows[0].text)
        self.assertTrue(all('<a href=' not in flow.text for flow in flows[2:]))
        for value in ('javascript:alert(1)', 'file:///x', 'data:image/png;base64,abc',
                      'https://[broken', 'https://example.com/\nattack'):
            self.assertEqual(pdf._http_url(value), '')

    def test_unsafe_article_anchors_do_not_create_actions(self):
        flows = pdf.article_content('<p><a href="javascript:alert(1)">危険</a>'
                                    '<a href="file:///C:/secret">ファイル</a></p>',
                                    [], self.style, 500)
        self.assertIn('危険', self.text(flows))
        self.assertFalse(any('<a href=' in flow.text for flow in flows))

    def test_marker_images_use_fieldfile_storage_and_preserve_order_and_aspect(self):
        attachment = self.attachment(size=(1000, 400))
        flows = pdf.article_content('画像より前<image1>画像より後', [attachment], self.style, 300)
        index = next(index for index, flow in enumerate(flows) if isinstance(flow, Image))
        self.assertIn('画像より前', self.text(flows[:index]))
        self.assertIn('画像より後', self.text(flows[index + 1:]))
        image = flows[index]
        self.assertLessEqual(image.drawWidth, 300)
        self.assertLessEqual(image.drawHeight, 220)
        self.assertAlmostEqual(image.drawWidth / image.drawHeight, 2.5)
        attachment.image.storage.open.assert_called_once_with('allowed.png', 'rb')

    def test_named_and_sequential_legacy_markers(self):
        for marker in ('【画像:添付画像.png】', '<image:添付画像.png>', '<image>'):
            with self.subTest(marker=marker):
                flows = pdf.article_content('前' + marker + '後', [self.attachment()], self.style, 500)
                self.assertEqual(sum(isinstance(flow, Image) for flow in flows), 1)

    def test_model_file_field_and_protected_download_url_are_supported(self):
        fixture = self.attachment()
        attachment = SimpleNamespace(file=fixture.image, display_name='添付画像.png',
                                     download_url='/knowledge/files/article-image/123/')
        flows = pdf.article_content('<p>前<img src="/knowledge/files/article-image/123/">後</p>',
                                    [attachment], self.style, 500)
        self.assertEqual(sum(isinstance(flow, Image) for flow in flows), 1)
        fixture.image.storage.open.assert_called_once_with('allowed.png', 'rb')

    def test_unmarked_attachments_are_included(self):
        flows = pdf.article_content('<p>本文</p>', [self.attachment()], self.style, 500)
        self.assertEqual(sum(isinstance(flow, Image) for flow in flows), 1)

    def test_existing_html_image_is_not_appended_again_and_keeps_body_order(self):
        for ending in ('>', '/>'):
            with self.subTest(ending=ending):
                attachment = self.attachment()
                flows = pdf.article_content('<p>前文<img src="/media/allowed.png"' + ending + '後文</p>',
                                            [attachment], self.style, 500)
                images = [index for index, flow in enumerate(flows) if isinstance(flow, Image)]
                self.assertEqual(len(images), 1)
                self.assertIn('前文', self.text(flows[:images[0]]))
                self.assertIn('後文', self.text(flows[images[0] + 1:]))
                attachment.image.storage.open.assert_called_once()

    def test_untrusted_image_sources_never_open_storage_or_network(self):
        attachment = self.attachment()
        sources = ['https://evil.example/allowed.png', 'file:///C:/secret.png',
                   'C:/secret.png', '/media/other.png', 'data:image/png;base64,AAAA']
        html = ''.join(f'<img src="{source}" alt="外部画像">' for source in sources)
        with patch('urllib.request.urlopen', side_effect=AssertionError('network access')):
            flows = pdf.article_content(html + '<image1>', [attachment], self.style, 500)
        self.assertEqual(self.text(flows).count('画像を表示できません'), len(sources))
        attachment.image.storage.open.assert_called_once_with('allowed.png', 'rb')

    def test_corrupt_missing_oversize_and_excess_pixel_images_have_fallback(self):
        for data in (b'not an image', b'x' * (pdf.MAX_IMAGE_BYTES + 1)):
            with self.subTest(size=len(data)):
                flows = pdf.article_content('<image1>', [self.attachment(data)], self.style, 500)
                self.assertIn('画像を表示できません', self.text(flows))
        attachment = self.attachment()
        attachment.image.storage.open.side_effect = FileNotFoundError
        flows = pdf.article_content('<image1>', [attachment], self.style, 500)
        self.assertIn('画像を表示できません', self.text(flows))
        with patch.object(pdf, 'MAX_IMAGE_PIXELS', 1):
            flows = pdf.article_content('<image1>', [self.attachment()], self.style, 500)
        self.assertIn('画像を表示できません', self.text(flows))

    def test_empty_field_and_unavailable_url_have_fallback(self):
        for field in (None, SimpleNamespace(name=''), Mock(name='broken')):
            if isinstance(field, Mock):
                from unittest.mock import PropertyMock
                type(field).url = PropertyMock(side_effect=ValueError('no URL'))
            flows = pdf.article_content('<image1>', [SimpleNamespace(image=field)], self.style, 500)
            self.assertIn('画像を表示できません', self.text(flows))

    def test_storage_read_is_bounded(self):
        attachment = self.attachment()
        reader = Mock()
        reader.__enter__ = Mock(return_value=reader)
        reader.__exit__ = Mock(return_value=False)
        reader.read.return_value = b'broken'
        attachment.image.storage.open.side_effect = None
        attachment.image.storage.open.return_value = reader
        pdf.article_content('<image1>', [attachment], self.style, 500)
        reader.read.assert_called_once_with(pdf.MAX_IMAGE_BYTES + 1)

    def test_detail_table_labels_and_split_options_are_preserved(self):
        tables = []

        def capture(data, **options):
            tables.append((data, options))
            return Table(data, **options)

        with patch.object(pdf, 'Table', side_effect=capture):
            pdf.build_posting_report_pdf(report_context(reference_links=['https://example.com']))
        rows, options = next((rows, options) for rows, options in tables if options.get('splitInRow') == 1)
        self.assertEqual(options['repeatRows'], 0)
        self.assertEqual([row[0].getPlainText() for row in rows],
                         ['タイトル', '管理番号', '投稿日時', 'カテゴリ',
                          '質問', '回答', '参考リンク'])

    def test_long_single_paragraph_has_no_loss_duplicates_or_repeated_titles(self):
        body = '長文回答の連続です。' * 2000 + '末尾固有確認'
        paragraphs, rendered = self.drawn_paragraphs(report_context(body='<p>' + body + '</p>'))
        titles = [page for page, text in paragraphs if text == '1. [Q&A] 固有の記事タイトル']
        self.assertEqual(len(titles), 1)
        # Decorations can occur in the middle of a word split across pages.
        joined = ''.join(text for _, text in rendered).replace('次ページへ続く', '').replace(
            '管理番号（続き）：QA-0001', '')
        self.assertEqual(joined.count('長文回答の連続です。'), 2000)
        self.assertEqual(joined.count('末尾固有確認'), 1)
        continuation_pages = [page for page, text in paragraphs if text == '管理番号（続き）：QA-0001']
        footer_pages = [page for page, text in paragraphs if text == '次ページへ続く']
        self.assertGreaterEqual(len(continuation_pages), 2)
        self.assertEqual(continuation_pages, [page + 1 for page in footer_pages])
        self.assertEqual(footer_pages[0], titles[0])
        self.assertNotIn(continuation_pages[-1], footer_pages)

    def test_many_paragraphs_retain_every_line_once(self):
        body = ''.join(f'<p>本文固有行{index:04d}完</p>' for index in range(160))
        _, rendered = self.drawn_paragraphs(report_context(body=body))
        joined = ''.join(text for _, text in rendered)
        for index in range(160):
            self.assertEqual(joined.count(f'本文固有行{index:04d}完'), 1)

    def test_long_links_and_large_images_build_and_only_safe_actions_exist(self):
        attachment = self.attachment(size=(80, 2000))
        long_url = 'https://example.com/' + 'a' * 7000 + '?x=1&y=2'
        context = report_context(body='<p>前文</p><image1><p>後文</p>', body_images=[attachment],
                                 reference_links=[dict(label='長文リンク', url=long_url),
                                                  'javascript:alert(1)', 'file:///C:/secret'])
        with patch('urllib.request.urlopen', side_effect=AssertionError('network access')):
            output = pdf.build_posting_report_pdf(context)
        self.assertIn(b'/Subtype /Image', output)
        self.assertIn(b'/URI (https://example.com/', output)
        self.assertNotIn(b'/URI (javascript:', output)
        self.assertNotIn(b'/URI (file:', output)

    def test_wrapper_reserves_header_and_footer_space(self):
        canvas = Canvas(BytesIO())
        table = Table([[Paragraph('本文' * 2000, self.style)]], colWidths=[400],
                      splitInRow=1, splitByRow=0)
        wrapper = pdf.ArticleDetail(table, 'QA-0001', self.style)
        wrapper.wrapOn(canvas, 400, 300)
        fragments = wrapper.splitOn(canvas, 400, 300)
        self.assertTrue(fragments)
        first = fragments[0]
        _, height = first.wrapOn(canvas, 400, 300)
        self.assertLessEqual(height, 300)
        self.assertGreater(first.footer_height, 0)
        self.assertTrue(fragments[-1].continued)
        self.assertEqual(wrapper.splitOn(canvas, 400, 1), [])

    def test_content_text_legacy_api_unchanged(self):
        self.assertEqual(pdf.content_text('<p>質問 &amp; 内容</p><p>回答<br>次の行</p>'
                                          '<script>secret</script>'), '質問 & 内容\n回答\n次の行')