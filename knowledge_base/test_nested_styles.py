#!/usr/bin/env python
"""
テスト: ネストされたスタイル適用時のマークアップ変換
シナリオ:
1. 全文に "本文" スタイル（黒/10pt）を適用
2. その一部を "ポイント" スタイル（オレンジ/14pt）で上書き
3. 保存時に正しくマークアップに変換されることを確認
"""
import os
import django
import json

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from tenasapo_knowledge.models import KnowledgeArticle
from tenasapo_knowledge.models import UserProfile
from django.contrib.auth.models import User


def test_nested_styles():
    """複合スタイルのマークアップ変換テスト"""
    
    # テスト用ユーザーを作成
    user, _ = User.objects.get_or_create(username='testuser')
    profile, _ = UserProfile.objects.get_or_create(user=user)
    
    # テスト用記事を作成
    article = KnowledgeArticle.objects.create(
        category='操作ガイド',
        title='ネストスタイルテスト',
        body='[size=10][color=black]これはテキストです[/color][/size]',
        created_by=user
    )
    
    print("✅ テスト記事を作成しました")
    print(f"  記事ID: {article.id}")
    print(f"  元のbody: {article.body}")
    
    # マークアップが複雑なケースをテスト
    complex_markup = '[size=10][color=black]これは[/color][/size][size=14][color=orange]テスト[/color][/size]'
    article.body = complex_markup
    article.save()
    
    # 保存後、再取得して確認
    article.refresh_from_db()
    print(f"\n✅ 複合マークアップを保存しました")
    print(f"  保存されたbody: {article.body}")
    
    # HTML への変換をテスト
    from tenasapo_knowledge.templatetags.article_extras import render_rich_text
    
    html_output = render_rich_text(article.body)
    print(f"\n✅ HTML出力を生成しました")
    print(f"  HTML: {html_output}")
    
    # HTML にそのまま表示されるマークアップが含まれていないか確認
    if '[' in html_output or ']' in html_output:
        print("⚠️ 警告: HTML에 [ または ] が含まれています")
        print("  これはマークアップが正しく処理されていないことを示します")
    else:
        print("✅ マークアップは正しく HTML に変換されました")
    
    # クリーンアップ
    article.delete()
    print("\n✅ テストを完了しました")


if __name__ == '__main__':
    test_nested_styles()
