"""Presentation-only genres. Never rename categories or rewrite stored values.

Match the complete first slash-delimited category component, not a prefix.
The module is deliberately independent of Django and database configuration.
"""

CATEGORY_GENRES = (
    {'key': 'client', 'name': 'クライアント環境',
     'parents': ('PC', 'ハードウェア', '周辺機器')},
    {'key': 'infrastructure', 'name': 'システム基盤',
     'parents': ('サーバー', 'ネットワーク', 'Azure', 'クラウドインフラ')},
    {'key': 'applications', 'name': '業務アプリ・サービス',
     'parents': ('Microsoft 365', 'ソフトウェア', 'グループウェア')},
    {'key': 'operations', 'name': '共通業務・運用',
     'parents': ('移行・導入', '運用・保守')},
)
OTHER_GENRE = {'key': 'other', 'name': 'その他', 'parents': ()}
GENRE_KEYS = tuple(genre['key'] for genre in CATEGORY_GENRES) + ('other',)


def normalize_genre_key(value):
    return value if value in GENRE_KEYS else ''


def genre_for_parent(parent_name):
    name = (parent_name or '').strip()
    return next((genre for genre in CATEGORY_GENRES if name in genre['parents']), OTHER_GENRE)


def genre_fields(parent_name):
    genre = genre_for_parent(parent_name)
    return {'genre_key': genre['key'], 'genre_name': genre['name']}


def category_genre_keys(category_text):
    names = [name.strip() for name in (category_text or '').split(',') if name.strip()]
    return {genre_for_parent(name.split('/', 1)[0])['key'] for name in names} or {'other'}


def category_matches_genre(category_text, genre_key):
    return not normalize_genre_key(genre_key) or genre_key in category_genre_keys(category_text)


def group_parents_by_genre(parents):
    """Return ordered groups, each containing the original parent dictionaries once."""
    return [
        {'key': genre['key'], 'name': genre['name'],
         'parents': [parent for parent in parents if parent['genre_key'] == genre['key']]}
        for genre in (*CATEGORY_GENRES, OTHER_GENRE)
        if genre['key'] != 'other' or any(parent['genre_key'] == 'other' for parent in parents)
    ]