"""Generate an analysis PDF from the same context used by the screen."""

from io import BytesIO
from html.parser import HTMLParser
from math import ceil
from pathlib import Path
from xml.sax.saxutils import escape

from django.conf import settings
from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .posting_quotas import quota_progress

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
                           topMargin=62, bottomMargin=30, title='投稿レポート')
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

    half = context['report_half_year_target']
    story = [p('投稿レポート', heading),
             p(f'出力対象月：{context["report_month"]}'),
             p('メンバー別の投稿状況・グラフ・記事一覧は出力対象月で集計します。半期目標は対象月の属する半期です。'),
             Spacer(1, 12), p('半期目標', heading)]
    target_rows = [['目標・対象', '期間', '投稿数 / 必要件数', 'Q&A・Tips内訳']]
    target_rows.append(['半期目標（全メンバー）', period(half['start'], half['end']),
                        count(half['actual'], half['target']) + f'（{half["progress_percent"]:.1f}%）',
                        f'Q&A {half["faq"]} 件 / Tips {half["tips"]} 件'])
    story.extend([table(target_rows, [width * .24, width * .23, width * .23, width * .30],
                        {(2, 1)} if half['actual'] < half['target'] else set()),
                  Spacer(1, 12), p(f'{context["report_month"]} のメンバー別投稿状況', heading),
                  p('アナライズ対象メンバー全員（投稿0件を含む）。非表示メンバーは画面の集計対象設定に従います。'),
                  Spacer(1, 8)])
    members = context['report_member_summaries']
    monthly_target = context['report_monthly_target']
    if monthly_target is None:
        story.append(p('対象月の投稿目標は未設定のため、不足判定は行いません。'))
    else:
        story.append(p('赤字：対象月の設定件数に対する不足（Q&A・Tipsの基本件数と＋αで判定）。'))
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
    story.append(table(member_rows, [width * fraction for fraction in (.12, .32, .12, .12, .12, .20)], red_cells))
    # This report only displays the half-year goal, never monthly/personal goal lines.
    target = {'value': None, 'label': ''}
    largest = max([item['post_count_total'] for item in members] + [0])
    maximum = ceil(largest * 1.3) if largest else 5
    for offset in range(0, len(members), 16):
        suffix = f'（{offset + 1}〜{min(offset + 16, len(members))}人目）' if len(members) > 16 else ''
        story.append(KeepTogether([
            Spacer(1, 12),
            p(f'{context["report_month"]} のメンバー投稿数グラフ{suffix}', heading),
            member_chart(members[offset:offset + 16], target, maximum, width),
        ]))
    if not members:
        story.append(KeepTogether([
            Spacer(1, 12), p(f'{context["report_month"]} のメンバー投稿数グラフ', heading),
            p('集計対象のメンバーがいません。'),
        ]))
    story.extend([PageBreak(), p(f'{context["report_month"]} の投稿ナレッジ一覧', heading),
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
                      p('本文をテキストとして掲載しています。画像・添付ファイルは埋め込まず、画像位置を［画像］で示します。'),
                      Spacer(1, 12)])
        for index, article in enumerate(context['report_articles'], 1):
            if index > 1:
                story.append(PageBreak())
            detail_rows = [
                [p('タイトル'), p(f'{index}. [{article["kind"]}] {article["title"]}')],
                [p('管理番号'), p(article['management_code'] or '—')],
                [p('投稿日時'), p(f'{article["created_at"]:%Y/%m/%d %H:%M}')],
                [p('投稿者'), p(article['creator_name'])],
                [p('カテゴリ'), p(article['category'] or '—')],
                [p('承認状況'), p('承認済み' if article['is_approved'] else '未承認')],
            ]
            for label, value in (
                ('質問', article.get('question', '')),
                ('回答' if article['kind'] == 'Q&A' else '内容', article.get('body', '')),
            ):
                if label == '質問' and article['kind'] != 'Q&A':
                    continue
                text = content_text(value) or '内容がありません。'
                detail_rows.append([p(label), [p(line) for line in text.splitlines()]])
            # Permit splitting inside a body cell, including a single long paragraph.
            # Repeat the title row so continued pages identify the article.
            detail_table = Table(detail_rows, colWidths=[width * .14, width * .86],
                                 repeatRows=1, splitByRow=1, splitInRow=1, hAlign='LEFT')
            detail_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f6f7f9')),
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#fff1df')),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('GRID', (0, 0), (-1, -1), .4, colors.HexColor('#dddddd')),
                ('LEFTPADDING', (0, 0), (-1, -1), 8),
                ('RIGHTPADDING', (0, 0), (-1, -1), 8),
                ('TOPPADDING', (0, 0), (-1, -1), 7),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
            ]))
            story.append(detail_table)

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
                               f'投稿レポート | {context["report_month"]}')
        canvas.setStrokeColor(colors.HexColor('#dddddd'))
        canvas.line(30, page_height - 51, page_width - 30, page_height - 51)
        canvas.setFont(FONT, 8)
        canvas.drawRightString(width + 30, 16, f'{document.page} ページ')
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return stream.getvalue()