"""Plaintext-only terms structure. Templates escape every stored text fragment."""

import re

from django import template

from ..terms import DEFAULT_TERMS_TITLE, terms_acceptance_applies


register = template.Library()
ARTICLE = re.compile(
    r'^[ \t　]*第[ \t　]*[0-9０-９一二三四五六七八九十百千]+[ \t　]*条'
    r'(?:[ \t　]*(?:（[^\n（）]+）|\([^\n()]+\))|[ \t　]+\S[^\n]*)[ \t　]*$'
)
NUMBERED = re.compile(r'^([0-9０-９]{1,3}[.．、])([ \t　]*)(\S.*)$')
NESTED = re.compile(r'^(?:[0-9０-９]+[.．、]|[（(][0-9０-９]+[）)])')


@register.filter
def consent_applicable(user):
    return terms_acceptance_applies(user)


@register.filter
def is_article_heading(value):
    """Use the same article recognition for saved titles and body lines."""
    return bool(ARTICLE.fullmatch(str(value or '')))


@register.filter
def is_primary_terms_title(value):
    return str(value or '').strip() == DEFAULT_TERMS_TITLE


@register.filter
def terms_blocks(value):
    """Recognize standalone headings/items, retaining unknown lines and spacing.

    Do not invoke the rich-text renderer: even stored HTML is literal legal text.
    Only indented non-numbered lines continue an item. Blank/ordinary/nested
    lines terminate the list rather than silently becoming part of its last item.
    """
    blocks = []
    paragraph = []
    items = []

    def flush_paragraph():
        if paragraph:
            blocks.append({'kind': 'paragraph', 'text': '\n'.join(paragraph)})
            paragraph.clear()

    def flush_list():
        if items:
            blocks.append({'kind': 'list', 'items': list(items)})
            items.clear()

    text = str(value or '').replace('\r\n', '\n').replace('\r', '\n')
    for line in text.split('\n'):
        numbered = NUMBERED.fullmatch(line)
        # A decimal/version such as 1.2 must not become a numbered item.
        if numbered and not numbered[2] and numbered[3][0] in '0123456789０１２３４５６７８９':
            numbered = None
        if ARTICLE.fullmatch(line):
            flush_paragraph()
            flush_list()
            blocks.append({'kind': 'heading', 'text': line})
        elif numbered:
            flush_paragraph()
            items.append({'marker': numbered[1], 'text': numbered[3]})
        elif not line.strip():
            flush_paragraph()
            flush_list()
            blocks.append({'kind': 'blank', 'text': line})
        elif items and line[0] in ' \t　' and not NESTED.match(line.lstrip()):
            items[-1]['text'] += '\n' + line
        else:
            flush_list()
            paragraph.append(line)
    flush_paragraph()
    flush_list()
    return blocks