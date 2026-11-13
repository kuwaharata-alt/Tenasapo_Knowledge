"""Read-only manual presentation; never interpret operator text as markup."""

from .manual_defaults import OPERATION_MANUALS


# The unlabelled home entry precedes the four sidebar groups.
MENU_GROUPS = (
    ('', ('home',)),
    ('Knowledge', ('faq', 'tips', 'documents', 'quick-reference')),
    ('Input', ('knowledge-register', 'category-register', 'review')),
    ('Management', ('manual', 'users', 'analysis', 'article-management')),
    ('History', ('updates', 'login-history', 'view-history')),
)
MENU_ICONS = {
    'home': 'bi-house-door',
    'faq': 'bi-question-circle',
    'tips': 'bi-lightbulb',
    'documents': 'bi-folder2-open',
    'quick-reference': 'bi-lightning-charge',
    'knowledge-register': 'bi-pencil-square',
    'category-register': 'bi-tags',
    'review': 'bi-clipboard-check',
    'manual': 'bi-book',
    'users': 'bi-people',
    'analysis': 'bi-bar-chart-line',
    'article-management': 'bi-files',
    'updates': 'bi-clock-history',
    'login-history': 'bi-box-arrow-in-right',
    'view-history': 'bi-eye',
}
BODY_HEADINGS = {
    '内容': '内容', '対象・できること': '内容',
    '操作手順': '操作手順', '注意点': '注意点',
}


def parse_manual_body(body: str) -> list[dict[str, str]]:
    """Split only exact trimmed standalone headings, retaining all other bytes.

    ``source_heading`` retains the original line (including its line ending)
    for lossless reconstruction. ``heading`` is the display-only alias.
    A body without recognised headings is one unmodified plaintext block.
    """
    blocks = []
    heading = source_heading = ''
    lines: list[str] = []
    for line in body.splitlines(keepends=True):
        label = BODY_HEADINGS.get(line.strip())
        if label is not None:
            if lines or source_heading:
                blocks.append({'heading': heading, 'body': ''.join(lines),
                               'source_heading': source_heading})
            heading, source_heading, lines = label, line, []
        else:
            lines.append(line)
    if lines or source_heading or not blocks:
        blocks.append({'heading': heading, 'body': ''.join(lines),
                       'source_heading': source_heading})
    return blocks


def build_menu_groups(sections: list[dict]) -> list[dict]:
    """Group available sections only, using stored titles and sidebar order."""
    keyed = {section['manual'].menu_key: section for section in sections}
    return [{'title': title, 'sections': [keyed[key] for key in keys if key in keyed]}
            for title, keys in MENU_GROUPS if any(key in keyed for key in keys)]


def menu_definitions():
    """Resolve metadata by key rather than coupling it to default text order."""
    definitions = {definition.menu_key: definition for definition in OPERATION_MANUALS}
    return [definitions[key] for _, keys in MENU_GROUPS for key in keys]