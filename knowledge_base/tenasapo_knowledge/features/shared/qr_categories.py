"""QR category hierarchy shared by browsing and INPUT forms."""

from ...models import ConvenienceCategory, ConvenienceFeature


DEFAULT_QR_CATEGORY_HIERARCHY = [
    {
        'value': 'shortcut',
        'label': 'ショートカット',
        'children': [
            {
                'value': 'Winodws',
                'label': 'Winodws',
                'children': ['デスクトップ', '文書', 'ウィンドウ', 'ファイル操作'],
            },
            {
                'value': 'Office',
                'label': 'Office',
                'children': ['Word', 'Excel', 'PowerPoint', 'Outlook'],
            },
            {
                'value': 'Googleカレンダー',
                'label': 'Googleカレンダー',
                'children': ['基本操作', '予定管理'],
            },
            {
                'value': 'コントロールパネル',
                'label': 'コントロールパネル',
                'children': ['基本操作', 'プログラム', 'システム'],
            },
            {
                'value': 'Windowsの設定',
                'label': 'Windowsの設定',
                'children': ['基本操作', 'ネットワーク', 'アカウント'],
            },
        ],
    },
    {
        'value': 'command',
        'label': 'コマンド',
        'children': [
            {
                'value': 'Winodws',
                'label': 'Winodws',
                'children': ['ファイル操作', 'ネットワーク', 'システム'],
            },
            {
                'value': 'Office',
                'label': 'Office',
                'children': ['Word', 'Excel', 'PowerPoint', 'Outlook'],
            },
        ],
    },
]


def get_qr_category_hierarchy():
    categories = ConvenienceCategory.objects.order_by('reference_type', 'category', 'middle_category', 'id')
    if not categories.exists():
        return DEFAULT_QR_CATEGORY_HIERARCHY

    type_label_map = dict(ConvenienceFeature.TYPE_CHOICES)
    big_map = {}
    for item in categories:
        big_node = big_map.setdefault(
            item.reference_type,
            {
                'value': item.reference_type,
                'label': type_label_map.get(item.reference_type, item.reference_type),
                'children': {},
            },
        )
        mid_node = big_node['children'].setdefault(
            item.category,
            {
                'value': item.category,
                'label': item.category,
                'children': [],
            },
        )
        if item.middle_category and item.middle_category not in mid_node['children']:
            mid_node['children'].append(item.middle_category)

    hierarchy = []
    for big in big_map.values():
        hierarchy.append(
            {
                'value': big['value'],
                'label': big['label'],
                'children': list(big['children'].values()),
            }
        )
    return hierarchy