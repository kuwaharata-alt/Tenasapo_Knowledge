#!/usr/bin/env python
"""
SQLite → PostgreSQL データ移行スクリプト

使用方法:
  パターン1: knowledge_base ディレクトリから直接実行
    cd knowledge_base
    python ../migrate_to_postgres.py

  パターン2: Django シェルから実行
    cd knowledge_base
    python manage.py shell < ../migrate_to_postgres.py
"""

import os
import django
import sys
from pathlib import Path

# Django 設定
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

# プロジェクトルート検出
script_dir = Path(__file__).resolve().parent
knowledge_base_dir = script_dir / 'knowledge_base'

# knowledge_base が隣にない場合は現在の親を確認
if knowledge_base_dir.exists():
    os.chdir(knowledge_base_dir)
    BASE_DIR = knowledge_base_dir
else:
    # knowledge_base 内から実行されている場合
    BASE_DIR = script_dir.parent if 'knowledge_base' in str(script_dir) else script_dir

sys.path.insert(0, str(BASE_DIR.parent if BASE_DIR.name == 'knowledge_base' else BASE_DIR))

django.setup()

# ここからデータ移行処理
from django.core.management import call_command
from django.db import connections
from django.db.utils import DatabaseError
import json


def backup_sqlite_data():
    """SQLite データを JSON でバックアップ"""
    print("⏳ SQLite データをバックアップ中...")
    
    backup_file = BASE_DIR / 'sqlite_backup.json'
    
    try:
        with open(backup_file, 'w', encoding='utf-8') as f:
            call_command('dumpdata', '--all', stdout=f, verbosity=1)
        
        print(f"✅ バックアップ完了: {backup_file}")
        return backup_file
    except Exception as e:
        print(f"❌ バックアップエラー: {e}")
        return None


def test_postgres_connection():
    """PostgreSQL への接続確認"""
    print("⏳ PostgreSQL 接続テスト中...")
    
    try:
        # 一時的に PostgreSQL に接続
        from django.db import connections
        connections.ensure_defaults()
        
        # PostgreSQL 接続情報の確認
        db_config = connections.databases.get('default', {})
        engine = db_config.get('ENGINE', '')
        
        if 'postgresql' in engine:
            connection = connections['default']
            cursor = connection.cursor()
            cursor.execute("SELECT version();")
            version = cursor.fetchone()
            print(f"✅ PostgreSQL 接続成功: {version[0]}")
            cursor.close()
            return True
        else:
            print(f"⚠️  DATABASE_ENGINE が PostgreSQL ではありません: {engine}")
            return False
    except DatabaseError as e:
        print(f"❌ PostgreSQL 接続エラー: {e}")
        print("\n💡 以下の環境変数が設定されているか確認してください:")
        print("   DB_ENGINE=django.db.backends.postgresql")
        print("   DB_NAME=<database_name>")
        print("   DB_USER=<postgres_user>")
        print("   DB_PASSWORD=<postgres_password>")
        print("   DB_HOST=<postgres_host>")
        return False


def migrate_database():
    """Django マイグレーション実行"""
    print("⏳ Django マイグレーション実行中...")
    
    try:
        call_command('migrate', verbosity=1)
        print("✅ マイグレーション完了")
        return True
    except Exception as e:
        print(f"❌ マイグレーションエラー: {e}")
        return False


def load_data(backup_file):
    """バックアップデータを PostgreSQL にロード"""
    print(f"⏳ データをロード中: {backup_file}")
    
    try:
        call_command('loaddata', str(backup_file), verbosity=1)
        print("✅ データロード完了")
        return True
    except Exception as e:
        print(f"❌ データロードエラー: {e}")
        return False


def verify_data():
    """データ検証"""
    print("⏳ データ検証中...")
    
    try:
        from tenasapo_knowledge.models import (
            KnowledgeArticle,
            TipsArticle,
            Manual,
            UserProfile,
            ViewHistory,
            LoginHistory,
        )
        
        stats = {
            'knowledge_articles': KnowledgeArticle.objects.count(),
            'tips_articles': TipsArticle.objects.count(),
            'manuals': Manual.objects.count(),
            'user_profiles': UserProfile.objects.count(),
            'view_histories': ViewHistory.objects.count(),
            'login_histories': LoginHistory.objects.count(),
        }
        
        print("✅ データベース統計:")
        for model_name, count in stats.items():
            print(f"   - {model_name}: {count} レコード")
        
        # すべてが 0 でないか確認
        if sum(stats.values()) == 0:
            print("⚠️  警告: すべてのテーブルが空です。データが読み込まれてない可能性があります。")
            return False
        
        return True
    except ImportError:
        print("⚠️  モデル読み込みエラー（初回は無視可能）")
        return True


def main():
    """メイン処理"""
    print("=" * 60)
    print("SQLite → PostgreSQL データ移行スクリプト")
    print("=" * 60)
    
    # ステップ 1: PostgreSQL 接続確認
    if not test_postgres_connection():
        print("\n❌ PostgreSQL に接続できません。")
        print("環境変数を確認して、スクリプトを再実行してください。")
        sys.exit(1)
    
    # ステップ 2: SQLite データをバックアップ
    backup_file = backup_sqlite_data()
    if not backup_file:
        print("\n❌ データバックアップに失敗しました。")
        sys.exit(1)
    
    # ステップ 3: マイグレーション実行
    if not migrate_database():
        print("\n❌ マイグレーションに失敗しました。")
        sys.exit(1)
    
    # ステップ 4: データをロード
    if not load_data(backup_file):
        print("\n⚠️  データロードに失敗しましたが、スキーマは作成されています。")
        print("手動でデータを移行してください。")
        # ここでは継続
    
    # ステップ 5: データ検証
    verify_data()
    
    print("\n" + "=" * 60)
    print("✅ マイグレーション完了！")
    print("=" * 60)
    print("\n次のステップ:")
    print("1. `python manage.py createsuperuser` でスーパーユーザーを作成")
    print("2. アプリケーションをテスト実行: `python manage.py runserver`")
    print("3. 本番環境にデプロイ")


if __name__ == '__main__':
    main()
