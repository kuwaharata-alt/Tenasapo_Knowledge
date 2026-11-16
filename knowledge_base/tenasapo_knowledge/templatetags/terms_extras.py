"""Plaintext-only terms structure. Templates escape every stored text fragment."""

import re

from django import template

from ..terms import terms_acceptance_applies


register = template.Library()
ARTICLE = re.compile(
    r'^[ \t　]*第[ \t　]*[0-9０-９一二三四五六七八九十百千]+[ \t　]*条'
    r'(?:[ \t　]*(?:（[^\n（）]+）|\([^\n()]+\))|[ \t　]+\S[^\n]*)[ \t　]*$'
)
NUMBERED = re.compile(r'^([0-9０-９]{1,3}[.．、])([ \t　]*)(\S.*)$')
SUBNUMBERED = re.compile(r'^[ \t　]*((?:\([0-9０-９]{1,3}\)|（[0-9０-９]{1,3}）))[ \t　]*(\S.*)$')
BULLET = re.compile(r'^[ \t　]*([・･])[ \t　]*(\S.*)$')
NESTED = re.compile(r'^(?:[0-9０-９]+[.．、]|[（(][0-9０-９]+[）)])')


@register.filter
def consent_applicable(user):
    return terms_acceptance_applies(user)


@register.filter
def is_article_heading(value):
    """Use the same article recognition for saved titles and body lines."""
    return bool(ARTICLE.fullmatch(str(value or '')))


@register.filter
def terms_blocks(value):
    """Recognize standalone headings/items, retaining unknown lines and spacing.

    Do not invoke the rich-text renderer: even stored HTML is literal legal text.
    Parenthetical items belong to the preceding primary item; bullets belong
    to the preceding parenthetical item (or primary item). Without a parent,
    either form is a standalone list. Blank/unknown lines reset the hierarchy.
    """
    blocks = []
    paragraph = []
    items = []
    root_style = 'numbered'
    primary = None
    secondary = None
    current = None

    def flush_paragraph():
        if paragraph:
            blocks.append({'kind': 'paragraph', 'text': '\n'.join(paragraph)})
            paragraph.clear()

    def flush_list():
        nonlocal primary, secondary, current
        if items:
            block = {'kind': 'list', 'items': list(items)}
            if root_style != 'numbered':
                block['style'] = root_style
            blocks.append(block)
            items.clear()
        primary = secondary = current = None

    def append_item(style, marker, content, parent=None):
        nonlocal root_style
        if parent is None:
            if items and root_style != style:
                flush_list()
            root_style = style
            target = items
        else:
            children = parent.setdefault('children', [])
            if not children or children[-1]['style'] != style:
                children.append({'kind': 'list', 'style': style, 'items': []})
            target = children[-1]['items']
        item = {'marker': marker, 'text': content}
        target.append(item)
        return item

    text = str(value or '').replace('\r\n', '\n').replace('\r', '\n')
    for line in text.split('\n'):
        numbered = NUMBERED.fullmatch(line)
        subnumbered = SUBNUMBERED.fullmatch(line)
        bullet = BULLET.fullmatch(line)
        # A decimal/version such as 1.2 must not become a numbered item.
        if numbered and not numbered[2] and numbered[3][0] in '0123456789０１２３４５６７８９':
            numbered = None
        if ARTICLE.fullmatch(line):
            flush_paragraph()
            flush_list()
            blocks.append({'kind': 'heading', 'text': line})
        elif numbered:
            flush_paragraph()
            primary = append_item('numbered', numbered[1], numbered[3])
            secondary = None
            current = primary
        elif subnumbered:
            flush_paragraph()
            secondary = append_item('parenthetical', subnumbered[1], subnumbered[2], primary)
            current = secondary
        elif bullet:
            flush_paragraph()
            current = append_item('bullet', bullet[1], bullet[2], secondary or primary)
        elif not line.strip():
            flush_paragraph()
            flush_list()
            blocks.append({'kind': 'blank', 'text': line})
        elif current is not None and line[0] in ' \t　' and not NESTED.match(line.lstrip()):
            current['text'] += '\n' + line
        else:
            flush_list()
            paragraph.append(line)
    flush_paragraph()
    flush_list()
    return blocks