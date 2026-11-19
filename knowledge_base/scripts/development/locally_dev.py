#!/usr/bin/env python
"""ローカル開発環境のセットアップ。旧ルートの入口からも実行可能。"""

import sys
import subprocess
from pathlib import Path

# cwd や呼び出し元に依存せず、元の workspace root を使用する。
BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def run_command(cmd, description):
    """コマンド実行とエラーハンドリング"""
    print(f"\n⏳ {description}...")
    try:
        result = subprocess.run(cmd, shell=True, cwd=BASE_DIR, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"✅ {description}成功")
            return True
        else:
            print(f"❌ {description}失敗:")
            print(result.stderr)
            return False
    except Exception as e:
        print(f"❌ エラー: {e}")
        return False


def main():
    print("=" * 60)
    print("Django ローカル開発環境セットアップ")
    print("=" * 60)

    venv_path = BASE_DIR / ".venv"

    if not venv_path.exists():
        print("\n⚠️  仮想環境が見つかりません。")
        print("以下を実行: python -m venv .venv")
        sys.exit(1)

    # 存在確認のみ。内容は読み込まない。
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        print("\n⚠️  .env ファイルが見つかりません。")
        print(".env.example をコピーして .env を作成してください:")
        print("  cp .env.example .env")
        print("その後、値を編集してください。")
        sys.exit(1)

    pip_cmd = f'"{venv_path}\\Scripts\\pip.exe" install -r requirements.txt'
    if not run_command(pip_cmd, "依存関係インストール"):
        print("❌ 依存関係のインストールに失敗しました。")
        sys.exit(1)

    manage_py = BASE_DIR / "manage.py"
    if manage_py.exists():
        python_cmd = f'"{venv_path}\\Scripts\\python.exe"'

        if not run_command(f'{python_cmd} "{manage_py}" migrate', "データベースマイグレーション"):
            print("⚠️  マイグレーションに問題があります。")

        if not run_command(f'{python_cmd} "{manage_py}" collectstatic --noinput', "静的ファイル収集"):
            print("⚠️  静的ファイル収集に問題があります。")

    print("\n" + "=" * 60)
    print("✅ セットアップ完了！")
    print("=" * 60)
    print("\n次のコマンドで開発サーバーを起動:")
    print(f'  cd "{BASE_DIR}"')
    print(f'  "{venv_path}\\Scripts\\python.exe" manage.py runserver')
    print("\nアクセス: http://localhost:8000")


if __name__ == '__main__':
    main()