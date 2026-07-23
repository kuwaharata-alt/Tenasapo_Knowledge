#!/usr/bin/env python
"""
Google Admin API を使ってユーザー情報を取得するテスト
Domain-wide Delegationが有効化されている必要があります
"""

import json
from google.oauth2 import service_account
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import os

# このスクリプトと同じディレクトリにある場合
DESKTOP_PATH = os.path.expanduser(r'~\Desktop\sol-nexus-2026-107dd1a85a2a.json')

# サービスアカウントの設定
SCOPES = ['https://www.googleapis.com/auth/admin.directory.user.readonly']

def get_admin_api_service(service_account_json_path, admin_email):
    """
    Google Admin API サービスを作成
    admin_email: ドメイン管理者のメールアドレス（Domain-wide Delegationで指定）
    """
    try:
        credentials = service_account.Credentials.from_service_account_file(
            service_account_json_path,
            scopes=SCOPES
        )
        
        # サービスアカウントがドメイン管理者のとして動作するように委任
        credentials = credentials.with_subject(admin_email)
        
        service = build('admin', 'directory_v1', credentials=credentials)
        print("✓ Google Admin API への認証に成功しました")
        return service
    
    except Exception as e:
        print(f"✗ 認証エラー: {e}")
        return None

def list_users(service, domain=None, max_results=10):
    """
    ドメイン内のユーザー情報を取得
    """
    try:
        if not service:
            print("✗ サービスが初期化されていません")
            return []
        
        query = f"orgUnitPath='/'1 and isDelegatedAdmin=false"
        if domain:
            query = f"emails:*@{domain}"
        
        results = service.users().list(
            customer='my_customer',
            maxResults=max_results,
            orderBy='email',
            query=query
        ).execute()
        
        users = results.get('users', [])
        print(f"\n✓ {len(users)} 件のユーザー情報を取得しました:")
        
        for user in users:
            print(f"  - {user['primaryEmail']}: {user.get('name', {}).get('fullName', 'N/A')}")
        
        return users
    
    except Exception as e:
        print(f"✗ ユーザー情報取得エラー: {e}")
        return []

def test_authentication():
    """
    認証テスト（ユーザー情報取得なし）
    """
    if not os.path.exists(DESKTOP_PATH):
        print(f"✗ サービスアカウントキーが見つかりません: {DESKTOP_PATH}")
        return False
    
    try:
        with open(DESKTOP_PATH, 'r', encoding='utf-8') as f:
            sa_config = json.load(f)
        
        print(f"サービスアカウント: {sa_config['client_email']}")
        print(f"プロジェクトID: {sa_config['project_id']}")
        print("\n認証テスト中...\n")
        
        # 基本的な認証テスト
        credentials = service_account.Credentials.from_service_account_file(
            DESKTOP_PATH,
            scopes=SCOPES
        )
        
        print("✓ サービスアカウント認証に成功しました")
        print(f"  認証スコープ: {SCOPES}")
        print("\n【次のステップ】")
        print("1. Domain-wide Delegation を有効化する")
        print("2. APIコンソールで以下の権限を付与:")
        print("   - https://www.googleapis.com/auth/admin.directory.user.readonly")
        print("3. ドメイン管理者のメールアドレスを指定してユーザー情報を取得")
        
        return True
    
    except Exception as e:
        print(f"✗ 認証エラー: {e}")
        return False

def main():
    print("=" * 60)
    print("Google Admin API ユーザー情報取得 テスト")
    print("=" * 60 + "\n")
    
    # ステップ1: 認証テスト
    if not test_authentication():
        print("\n✗ 認証が失敗しました")
        return
    
    # ステップ2: Domain-wide Delegationが有効な場合のテスト
    print("\n" + "=" * 60)
    print("Domain-wide Delegation 有効時のテスト")
    print("=" * 60 + "\n")
    
    # TODO: 自社ドメインとドメイン管理者のメールアドレスを設定
    # admin_email = "admin@yourdomain.com"
    # domain = "yourdomain.com"
    
    admin_email = "nexus-directory@sol-nexus-2026.iam.gserviceaccount.com"
    domain = "sol-nexus-2026.iam.gserviceaccount.com"
    
    service = get_admin_api_service(DESKTOP_PATH, admin_email)
    
    if service:
        print("\n【ユーザー情報取得】")
        users = list_users(service, domain, max_results=5)
        
        if not users:
            print("\n⚠ ユーザー情報が取得できません理由:")
            print("  - Domain-wide Delegationが有効化されていない")
            print("  - API権限が付与されていない")
            print("  - ドメイン管理者のメールアドレスが間違っている")

if __name__ == '__main__':
    main()
