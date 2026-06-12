import re

views_path = r'c:\webapps\knowledge_base\tenasapo_knowledge\views.py'

with open(views_path, 'r', encoding='utf-8') as f:
    content = f.read()

# The new menu section to replace from recent_faqs to class ConvenienceListView
new_section = """        context['recent_faqs'] = faq_qs.order_by('-updated_at')[:3]
        context['recent_tips'] = tips_qs.order_by('-updated_at')[:3]
        menu_groups = [
            {
                'name': 'Knowledge',
                'icon': '📚',
                'items': [
                    {'label': 'FAQ', 'url_name': 'article_list'},
                    {'label': 'Tips', 'url_name': 'tip_list'},
                    {'label': 'クイックリファレンス', 'url_name': 'convenience_list'},
                ],
            },
            {'name': 'Input', 'icon': '✍️', 'items': []},
            {'name': 'Manual', 'icon': '📘', 'items': []},
            {'name': 'User', 'icon': '👥', 'items': []},
            {'name': 'Management', 'icon': '📊', 'items': []},
            {'name': 'History', 'icon': '🕒', 'items': []},
        ]
        if is_admin:
            menu_groups[1]['items'].extend(
                [
                    {'label': 'FAQ登録', 'url_name': 'article_create'},
                    {'label': 'Tips登録', 'url_name': 'tip_create'},
                    {'label': 'クイックリファレンス登録', 'url_name': 'convenience_create'},
                    {'label': 'カテゴリ登録', 'url_name': 'category_create'},
                ]
            )
            menu_groups[2]['items'].append({'label': '運用マニュアル', 'url_name': 'manual_list'})
            menu_groups[3]['items'].append({'label': 'ユーザー一覧', 'url_name': 'user_list'})
            menu_groups[4]['items'].extend(
                [
                    {'label': 'データ分析まとめ', 'url_name': 'summary'},
                    {'label': '記事管理', 'url_name': 'article_management'},
                ]
            )
            menu_groups[5]['items'].extend(
                [
                    {'label': 'ログイン履歴', 'url_name': 'login_history_list'},
                    {'label': '閲覧履歴', 'url_name': 'view_history_list'},
                ]
            )
        context['menu_groups'] = [group for group in menu_groups if group['items']]
        return context


"""

start_marker = "        context['recent_faqs']"
end_marker = "class ConvenienceListView"

start_idx = content.index(start_marker)
end_idx = content.index(end_marker)

new_content = content[:start_idx] + new_section + content[end_idx:]

with open(views_path, 'w', encoding='utf-8') as f:
    f.write(new_content)

print(f"Done. Original length: {len(content)}, New length: {len(new_content)}")
print(f"Replaced section: {start_idx} to {end_idx}")
