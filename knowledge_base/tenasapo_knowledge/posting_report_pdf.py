"""Generate an analysis PDF from the same context used by the screen."""

from io import BytesIO
from html.parser import HTMLParser
from math import ceil
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
import warnings
from xml.sax.saxutils import escape

from django.conf import settings
from PIL import Image as PillowImage
from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Flowable, Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .posting_quotas import quota_progress
from .templatetags.article_extras import render_inline_images

FONT = 'HeiseiKakuGo-W5'
ORANGE = colors.HexColor('#e87524')
QA_RED = colors.HexColor('#dc3545')
BLUE = colors.HexColor('#4988e8')


class ContentTextParser(HTMLParser):
    """Extract readable text without executing or passing article HTML to PDF."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden_depth += 1
        if self.hidden_depth:
            return
        if tag in ('p', 'div', 'br', 'li', 'tr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self.parts.append('\n')
        if tag == 'li':
            self.parts.append('・')
        if tag in ('td', 'th'):
            self.parts.append(' ')
        if tag == 'img':
            self.parts.append('[画像]')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden_depth = max(0, self.hidden_depth - 1)
        elif not self.hidden_depth and tag in ('p', 'div', 'li', 'tr'):
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden_depth:
            self.parts.append(data)


def content_text(value):
    parser = ContentTextParser()
    parser.feed(value or '')
    return '\n'.join(line.strip() for line in ''.join(parser.parts).splitlines() if line.strip())


MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000


def _http_url(value):
    """Only absolute HTTP(S) URLs can become PDF actions."""
    value = str(value or '').strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return ''
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() in ('http', 'https') and parsed.hostname:
            return value
    except ValueError:
        pass
    return ''


def _link_markup(label, url):
    # Attribute quotes and all visible text are rebuilt, never copied from HTML.
    text = escape(str(label))
    safe_url = _http_url(url)
    if safe_url:
        attribute = escape(safe_url, {'"': '&quot;'})
        return f'<a href="{attribute}" color="#2463a5">{text}</a>'
    return text


class _VisibleHTML(HTMLParser):
    """Remove hidden contents *before* the existing renderer sanitizes tags."""
    def __init__(self, image_numbers):
        super().__init__(convert_charrefs=False)
        self.parts = []
        self.hidden = []
        self.image_numbers = image_numbers

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden.append(tag)
        elif not self.hidden:
            number = self.image_numbers.get(dict(attrs).get('src')) if tag == 'img' else None
            # Treat existing allowlisted HTML images as explicit markers too, so
            # the shared renderer does not append a second copy after the body.
            self.parts.append(f'<image{number}>' if number else self.get_starttag_text())

    def handle_startendtag(self, tag, attrs):
        if not self.hidden and tag not in ('script', 'style'):
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
        else:
            self.parts.append(f'</{tag}>')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)

    def handle_entityref(self, name):
        self.handle_data(f'&{name};')

    def handle_charref(self, name):
        self.handle_data(f'&#{name};')


def _attachment_image(field, max_width):
    """Never pass article paths/URLs to ReportLab or Pillow."""
    with field.storage.open(field.name, 'rb') as source:
        data = source.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError('image exceeds byte limit')
    with warnings.catch_warnings():
        warnings.simplefilter('error', PillowImage.DecompressionBombWarning)
        with PillowImage.open(BytesIO(data)) as probe:
            width, height = probe.size
            if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                raise ValueError('image exceeds pixel limit')
            probe.verify()
        with PillowImage.open(BytesIO(data)) as decoded:
            decoded.load()
            # A validated in-memory PNG also prevents lazy file access at draw time.
            normalized = BytesIO()
            decoded.convert('RGBA' if 'A' in decoded.getbands() or 'transparency' in decoded.info
                            else 'RGB').save(normalized, format='PNG')
    normalized.seek(0)
    scale = min(1, max_width / width, 220 / height)
    return Image(normalized, width=width * scale, height=height * scale, hAlign='LEFT')


class _ArticleHTML(HTMLParser):
    """Rebuild only paragraphs, breaks, allowlisted images and safe links."""
    blocks = {'p', 'div', 'li', 'ul', 'ol', 'tr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'pre'}

    def __init__(self, style, images, max_width):
        super().__init__(convert_charrefs=True)
        self.style, self.images, self.max_width = style, images, max_width
        self.flowables, self.parts = [], []
        self.url = None
        self.link_text = []
        self.hidden = 0

    def flush(self):
        if self.parts:
            markup = ''.join(self.parts)
            if markup.strip(' \t\r\n\xa0'):
                self.flowables.append(Paragraph(markup, self.style))
            self.parts = []

    def end_link(self):
        if self.url is not None:
            label = ''.join(self.link_text).strip()
            if label and label != self.url:
                self.parts.append(' (' + _link_markup(self.url, self.url) + ')')
        self.url = None
        self.link_text = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        if self.hidden:
            return
        attrs = dict(attrs)
        if tag in self.blocks:
            self.flush()
            if tag == 'li':
                self.parts.append('・')
        elif tag == 'br':
            self.parts.append('<br/>')
        elif tag in ('td', 'th'):
            self.parts.append(' ')
        elif tag == 'a':
            self.end_link()
            self.url = attrs.get('href') or ''
        elif tag == 'img':
            self.flush()
            field = self.images.get(attrs.get('src'))
            try:
                if field is None:
                    raise ValueError('image not allowlisted')
                image = _attachment_image(field, self.max_width)
            except Exception:
                # Missing storage objects, corrupt files and rejected sources are visible.
                alt = attrs.get('alt') or '画像'
                image = Paragraph(escape(f'［画像を表示できません：{alt}］'), self.style)
            self.flowables.append(image)

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)
        elif not self.hidden:
            if tag == 'a':
                self.end_link()
            elif tag in self.blocks:
                self.flush()

    def handle_data(self, data):
        if not self.hidden:
            if self.url is not None:
                self.link_text.append(data)
            markup = _link_markup(data, self.url) if self.url is not None else escape(data)
            self.parts.append(markup.replace('\n', '<br/>'))


def article_content(value, attachments, style, max_width):
    """Use legacy markers while authorizing only this article's FieldFiles."""
    images, adapters, image_numbers = {}, [], {}
    for attachment in attachments or []:
        field = getattr(attachment, 'file', None) or getattr(attachment, 'image', None)
        try:
            if not field or not field.name:
                raise ValueError('missing attachment')
            images[field.url] = field
            image_numbers[field.url] = len(adapters) + 1
            download_url = getattr(attachment, 'download_url', '')
            if download_url:
                images[download_url] = field
                image_numbers[download_url] = len(adapters) + 1
            # Adapt both model FileFields and standalone image fixtures.
            adapters.append(SimpleNamespace(file=field,
                                           display_name=getattr(attachment, 'display_name', '')))
        except Exception:
            # Preserve the attachment's presence even when its URL cannot be obtained.
            adapters.append(SimpleNamespace(file=SimpleNamespace(name='missing', url=''),
                                           display_name='画像を表示できません'))
    visible = _VisibleHTML(image_numbers)
    visible.feed(str(value or ''))
    visible.close()
    rendered = render_inline_images(''.join(visible.parts), adapters)
    parser = _ArticleHTML(style, images, max_width)
    parser.feed(str(rendered))
    parser.close()
    parser.end_link()
    parser.flush()
    return parser.flowables or [Paragraph('内容がありません。', style)]


def reference_content(links, style):
    result = []
    for link in links or []:
        url = str(link.get('url') or '') if isinstance(link, dict) else str(link)
        label = str(link.get('label') or url) if isinstance(link, dict) else url
        markup = _link_markup(label, url)
        if label != url:
            markup += ' (' + _link_markup(url, url) + ')'
        result.append(Paragraph(markup or '—', style))
    return result


class ArticleDetail(Flowable):
    """Decorate Table split fragments without modifying private cell values."""
    def __init__(self, table, management_code, style, continued=False, continues=False):
        super().__init__()
        self.table = table
        self.management_code = management_code
        self.style = style
        self.continued, self.continues = continued, continues
        self.header = Paragraph(escape(f'管理番号（続き）：{management_code}'), style)
        self.footer = Paragraph('次ページへ続く', style)

    def _decoration_height(self, paragraph, width):
        return paragraph.wrapOn(self.canv, width, 100000)[1] + 6

    def wrap(self, availWidth, availHeight):
        self.header_height = self._decoration_height(self.header, availWidth) if self.continued else 0
        self.footer_height = self._decoration_height(self.footer, availWidth) if self.continues else 0
        self.width, self.table_height = self.table.wrapOn(
            self.canv, availWidth, max(0, availHeight - self.header_height - self.footer_height))
        self.height = self.header_height + self.table_height + self.footer_height
        return self.width, self.height

    def split(self, availWidth, availHeight):
        header_height = self._decoration_height(self.header, availWidth) if self.continued else 0
        footer_height = self._decoration_height(self.footer, availWidth)
        room = availHeight - header_height - footer_height
        if room <= 0:
            return []
        fragments = self.table.splitOn(self.canv, availWidth, room)
        if len(fragments) < 2:
            return []
        first = ArticleDetail(fragments[0], self.management_code, self.style,
                              continued=self.continued, continues=True)
        rest = [ArticleDetail(fragment, self.management_code, self.style, continued=True,
                              continues=self.continues or index < len(fragments) - 1)
                for index, fragment in enumerate(fragments[1:], 1)]
        # A continuation notice always refers to the next page, not another fragment below it.
        result = [first]
        for fragment in rest:
            result.extend([PageBreak(), fragment])
        return result

    def draw(self):
        if self.continued:
            self.header.drawOn(self.canv, 0, self.height - self.header_height + 6)
        self.table.drawOn(self.canv, 0, self.footer_height)
        if self.continues:
            self.footer.drawOn(self.canv, 0, 0)


def member_chart(members, target, maximum, width=780):
    """Vector stacked bars, sharing a scale across member batches."""
    drawing = Drawing(width, 220)
    maximum = max(1, ceil(maximum))
    left, bottom, height = 36, 45, 140
    plot_width = width - left - 15
    for tick in range(maximum + 1):
        y = bottom + height * tick / maximum
        drawing.add(Line(left, y, width - 15, y, strokeColor=colors.HexColor('#dddddd')))
        drawing.add(String(left - 5, y - 3, str(tick),
                           textAnchor='end', fontName=FONT, fontSize=8))
    spacing = plot_width / max(len(members), 1)
    for index, member in enumerate(members):
        x = left + spacing * (index + .5)
        faq = member['faq_post_count']
        tips = member['tips_post_count']
        bar_width = min(30, spacing * .55)
        faq_height, tips_height = faq / maximum * height, tips / maximum * height
        drawing.add(Rect(x - bar_width / 2, bottom, bar_width, faq_height, fillColor=QA_RED, strokeColor=None))
        drawing.add(Rect(x - bar_width / 2, bottom + faq_height, bar_width, tips_height, fillColor=BLUE, strokeColor=None))
        drawing.add(String(x, bottom + faq_height + tips_height + 4, str(faq + tips),
                           textAnchor='middle', fontName=FONT, fontSize=8))
        name = member['name']
        for line_index in range(0, len(name), 8):
            if line_index >= 24:
                break
            drawing.add(String(x, bottom - 12 - line_index // 8 * 10, name[line_index:line_index + 8],
                               textAnchor='middle', fontName=FONT, fontSize=7))
    value = target['value']
    if value is not None:
        y = bottom + value / maximum * height
        drawing.add(Line(left, y, width - 15, y, strokeColor=colors.red, strokeWidth=1.5))
        drawing.add(String(left, 205, f"{target['label']}：{value} 件", fontName=FONT,
                           fontSize=9, fillColor=colors.red))
    drawing.add(Rect(width - 140, 192, 9, 9, fillColor=QA_RED, strokeColor=None))
    drawing.add(String(width - 126, 192, 'Q&A', fontName=FONT, fontSize=9))
    drawing.add(Rect(width - 65, 192, 9, 9, fillColor=BLUE, strokeColor=None))
    drawing.add(String(width - 51, 192, 'Tips', fontName=FONT, fontSize=9))
    return drawing


def build_posting_report_pdf(context):
    if FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(FONT))
    stream = BytesIO()
    logo_path = Path(settings.BASE_DIR) / 'logo' / 'systena_logo.png'
    logo = ImageReader(str(logo_path)) if logo_path.is_file() else None
    doc = SimpleDocTemplate(stream, pagesize=landscape(A4), leftMargin=30, rightMargin=30,
                           topMargin=62, bottomMargin=30, title='Nexus 月次レポート')
    width = landscape(A4)[0] - 60
    normal = ParagraphStyle('report', fontName=FONT, fontSize=9, leading=14, wordWrap='CJK')
    heading = ParagraphStyle('heading', parent=normal, fontSize=15, leading=22, textColor=ORANGE, spaceAfter=10)
    insufficient = ParagraphStyle('insufficient', parent=normal, textColor=colors.HexColor('#c62828'))

    def p(text, style=normal):
        return Paragraph(escape(str(text)).replace('\n', '<br/>'), style)

    def table(rows, widths, red_cells=()):
        result = Table([[p(cell, insufficient if (column, index) in red_cells else normal)
                         for column, cell in enumerate(row)] for index, row in enumerate(rows)],
                       colWidths=widths, repeatRows=1)
        result.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#fff1df')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('GRID', (0, 0), (-1, -1), .4, colors.HexColor('#dddddd')),
            ('LEFTPADDING', (0, 0), (-1, -1), 7), ('RIGHTPADDING', (0, 0), (-1, -1), 7),
            ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        return result

    def count(actual, required):
        return f'{actual} / {required if required is not None else "未確定"} 件'

    def period(start, end):
        return f'{start:%Y/%m/%d}〜{end:%Y/%m/%d}'

    month_start = context['report_month_start']
    report_month = f'{month_start.year}年{month_start.month}月'
    half = context['report_half_year_target']
    story = [Spacer(1, 12), p('Nexus 月次レポート', heading),
             p(f'出力対象月：{report_month}'),
             p('メンバー別の投稿状況・グラフ・記事一覧は出力対象月で集計します。半期目標は対象月の属する半期です。'),
             Spacer(1, 20), p('半期目標', heading)]
    target_rows = [['目標・対象', '期間', '投稿数 / 必要件数', 'Q&A・Tips内訳']]
    target_rows.append(['半期目標（全メンバー）', period(half['start'], half['end']),
                        count(half['actual'], half['target']) + f'（{half["progress_percent"]:.1f}%）',
                        f'Q&A {half["faq"]} 件 / Tips {half["tips"]} 件'])
    story.extend([table(target_rows, [width * .24, width * .23, width * .23, width * .30],
                        {(2, 1)} if half['actual'] < half['target'] else set())])
    member_section = []
    member_section.append(Spacer(1, 12))
    member_section.append(p(f'{report_month} のメンバー別投稿状況', heading))
    member_section.append(p('アナライズ対象メンバー全員（投稿0件を含む）。非表示メンバーは画面の集計対象設定に従います。'))
    member_section.append(Spacer(1, 8))
    members = context['report_member_summaries']
    monthly_target = context['report_monthly_target']
    if monthly_target is None:
        member_section.append(p('対象月の投稿目標は未設定のため、不足判定は行いません。'))
    else:
        member_section.append(p('赤字：対象月の設定件数に対する不足（Q&A・Tipsの基本件数と＋αで判定）。'))
    member_rows = [['管理番号', 'メンバー', 'Q&A', 'Tips', '合計', '投稿状況']]
    red_cells = set()
    for member in members:
        progress = quota_progress(member['faq_post_count'], member['tips_post_count'], monthly_target)
        status = '投稿あり' if member['post_count_total'] else '投稿なし'
        if progress is not None:
            status = progress['label']
            row = len(member_rows)
            if progress['faq_missing']:
                red_cells.add((2, row))
            if progress['tips_missing']:
                red_cells.add((3, row))
            if not progress['achieved']:
                red_cells.update({(4, row), (5, row)})
        member_rows.append([member['management_uid'] or '—', member['name'],
                            str(member['faq_post_count']), str(member['tips_post_count']),
                            str(member['post_count_total']), status])
    totals = context['report_member_totals']
    member_rows.append(['', '合計', str(totals['faq_post_count']), str(totals['tips_post_count']),
                        str(totals['post_count_total']), f'{len(members)} 人'])
    member_section.append(table(member_rows, [width * fraction for fraction in (.12, .32, .12, .12, .12, .20)], red_cells))
    # This report only displays the half-year goal, never monthly/personal goal lines.
    target = {'value': None, 'label': ''}
    largest = max([item['post_count_total'] for item in members] + [0])
    maximum = ceil(largest * 1.3) if largest else 5
    for offset in range(0, len(members), 16):
        suffix = f'（{offset + 1}〜{min(offset + 16, len(members))}人目）' if len(members) > 16 else ''
        story.append(KeepTogether([
            Spacer(1, 20),
            p(f'メンバー投稿数（{report_month}）{suffix}', heading),
            member_chart(members[offset:offset + 16], target, maximum, width),
        ]))
    if not members:
        story.append(KeepTogether([
            Spacer(1, 20), p(f'メンバー投稿数（{report_month}）', heading),
            p('集計対象のメンバーがいません。'),
        ]))
    story.append(PageBreak())
    story.extend(member_section)
    story.extend([PageBreak(), p(f'{report_month} の投稿ナレッジ一覧', heading),
                  p(f'合計 {context["report_totals"]["total"]} 件 / Q&A {context["report_totals"]["faq"]} 件 / Tips {context["report_totals"]["tips"]} 件'),
                  Spacer(1, 10)])
    rows = [['管理番号', '種類', 'カテゴリ', 'タイトル', '承認状況', '投稿者', '投稿日時']]
    for article in context['report_articles']:
        rows.append([article['management_code'] or '—', article['kind'],
                     article['category'] or '—', article['title'],
                     '承認済み' if article['is_approved'] else '未承認',
                     article['creator_name'], article['created_at'].strftime('%Y/%m/%d %H:%M')])
    if len(rows) == 1:
        story.append(p('この月に投稿されたナレッジはありません。'))
    else:
        story.append(table(rows, [width * fraction for fraction in (.11, .06, .17, .30, .09, .12, .15)]))
        story.extend([PageBreak(), p('投稿ナレッジの内容', heading),
                      p('本文・記事の添付画像・参考リンクを掲載しています。表示できない画像は代替表示します。'),
                      Spacer(1, 12)])
        for index, article in enumerate(context['report_articles'], 1):
            if index > 1:
                story.append(PageBreak())
            detail_rows = [
                [p('タイトル'), p(f'{index}. [{article["kind"]}] {article["title"]}'), '', '', '', ''],
                [p('管理番号'), p(article['management_code'] or '—'), p('種類'), p(article['kind']), '', ''],
                [p('投稿日時'), p(f'{article["created_at"]:%Y/%m/%d %H:%M}'),
                 p('投稿者'), p(article['creator_name']), p('承認状況'),
                 p('承認済み' if article['is_approved'] else '未承認')],
                [p('カテゴリ'), p(article['category'] or '—'), '', '', '', ''],
            ]
            for label, value in (
                ('質問', article.get('question', '')),
                ('回答' if article['kind'] == 'Q&A' else '内容', article.get('body', '')),
            ):
                if label == '質問' and article['kind'] != 'Q&A':
                    continue
                attachments = article.get('question_images' if label == '質問' else 'body_images', [])
                detail_rows.append([p(label), article_content(value, attachments, normal, width * .86 - 16), '', '', '', ''])
            references = reference_content(article.get('reference_links', []), normal)
            if references:
                detail_rows.append([p('参考リンク'), references, '', '', '', ''])
            # Permit splitting inside a body cell, including a single long paragraph.
            # The wrapper supplies continuation notices; never repeat the title row.
            detail_table = Table(detail_rows, colWidths=[width * fraction for fraction in (.14, .23, .10, .23, .10, .20)],
                                 repeatRows=0, splitByRow=0, splitInRow=1, hAlign='LEFT')
            detail_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f6f7f9')),
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#fff1df')),
                ('BACKGROUND', (2, 1), (2, 2), colors.HexColor('#f6f7f9')),
                ('BACKGROUND', (4, 2), (4, 2), colors.HexColor('#f6f7f9')),
                ('SPAN', (1, 0), (5, 0)),
                ('SPAN', (3, 1), (5, 1)),
                *[('SPAN', (1, row), (5, row)) for row in range(3, len(detail_rows))],
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('GRID', (0, 0), (-1, -1), .4, colors.HexColor('#dddddd')),
                ('LEFTPADDING', (0, 0), (-1, -1), 8),
                ('RIGHTPADDING', (0, 0), (-1, -1), 8),
                ('TOPPADDING', (0, 0), (-1, -1), 7),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
            ]))
            story.append(ArticleDetail(detail_table, article['management_code'] or '—', normal))

    def footer(canvas, document):
        canvas.saveState()
        page_width, page_height = landscape(A4)
        if logo is not None:
            image_width, image_height = logo.getSize()
            scale = min(95 / image_width, 30 / image_height)
            canvas.drawImage(logo, 30, page_height - 16 - image_height * scale,
                             width=image_width * scale, height=image_height * scale, mask='auto')
        canvas.setFont(FONT, 9)
        canvas.setFillColor(colors.HexColor('#555555'))
        canvas.drawRightString(page_width - 30, page_height - 33,
                               f'Nexus 月次レポート | {report_month}')
        canvas.setStrokeColor(colors.HexColor('#dddddd'))
        canvas.line(30, page_height - 51, page_width - 30, page_height - 51)
        canvas.setFont(FONT, 8)
        canvas.drawRightString(width + 30, 16, f'{document.page} ページ')
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return stream.getvalue()