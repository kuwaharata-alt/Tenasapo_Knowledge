import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from tenasapo_knowledge.models import FAQCategory

# 最低2階層: parent / child
# 必要に応じて middle を使用（例: Linux/RHEL, DB/MSSQL など）
CATEGORY_DATA = {
    'サーバー': {
        '': [
            'Windows Server', 'Linux', 'Active Directory', 'ADCS', 'ADFS', 'DNS', 'DHCP',
            'NPS', 'WSUS', 'RDS', 'DFS名前空間', 'DFSレプリケーション', 'WINS', 'IIS',
            'Apache', 'ライセンスサーバー', 'ファイルサーバー'
        ],
        'Linux': ['RHEL', 'Ubuntu', 'その他Linux'],
    },
    'PC': {
        '': [
            'PCキッティング', 'PC展開', 'PCリプレイス', 'Intune', 'Autopilot', 'プロビジョニング',
            'ローカル設定', 'Office展開', 'Windows Update', 'BitLocker', '資産管理', 'マスター作成'
        ]
    },
    'ネットワーク': {
        '': [
            'Cisco', 'Allied Telesis', 'YAMAHA', 'HPE Aruba', 'Fortinet', 'VLAN', 'VPN',
            '無線LAN', 'L2スイッチ', 'L3スイッチ', 'ルーター', 'ファイアウォール',
            'ロードバランサー', 'ネットワーク調査'
        ]
    },
    'Azure': {
        '': [
            'Azure VM', 'Azure Blob Storage', 'Azure Files', 'Azure Backup', 'Azure Site Recovery',
            'Azure VPN Gateway', 'Azure Virtual Network', 'Azure App Service', 'Azure SQL',
            'Azure Monitor', 'Azure Arc', 'Azure移行'
        ]
    },
    'Microsoft 365': {
        '': [
            'Exchange Online', 'SharePoint Online', 'OneDrive', 'Teams', 'Entra ID', 'Entra Connect',
            'Intune', 'Defender for Business', 'Defender for Endpoint', 'MFA', 'Microsoft 365移行'
        ]
    },
    'ソフトウェア': {
        '': [
            'DB', 'Adobe', 'Office LTSC', 'ウイルス対策ソフト', '業務アプリ', 'Webアプリ',
            'ミドルウェア', 'ライセンス管理'
        ],
        'DB': ['MSSQL', 'MySQL', 'Oracle', 'SQLite'],
    },
    'ハードウェア': {
        '': ['サーバー', 'ストレージ', 'UPS', 'テープ装置', 'RDX', 'その他機器'],
        'サーバー': ['Dell PowerEdge', 'HPE ProLiant', 'Fujitsu PRIMERGY', 'Lenovo ThinkSystem'],
        'ストレージ': ['NetApp', 'HPE MSA', 'HPE Alletra', 'QNAP', 'Synology', 'TeraStation'],
        'UPS': ['APC', 'OMRON'],
    },
    '周辺機器': {
        '': ['プリンター', 'スキャナー', 'モニター', 'ドッキングステーション', 'Webカメラ', 'ヘッドセット', 'NAS', 'その他周辺機器']
    },
    '運用・保守': {
        '': ['監視', '障害対応', '定期保守', 'パッチ適用', 'ログ確認', 'バックアップ運用', '容量管理', '資産管理', '問い合わせ対応', 'ベンダー調整']
    },
    '移行・導入': {
        '': ['サーバー移行', 'NAS移行', 'クラウド移行', 'M365移行', 'AD移行', 'DFS移行', 'robocopy', 'Veeam', 'Arcserve', 'Acronis', 'クローン作成', 'ラック搭載', '機器設置', '現地作業']
    },
}


def main():
    created = 0
    existing = 0

    for parent, middle_map in CATEGORY_DATA.items():
        for middle, children in middle_map.items():
            for child in children:
                _, is_created = FAQCategory.objects.get_or_create(
                    parent_name=parent,
                    middle_name=middle,
                    child_name=child,
                )
                if is_created:
                    created += 1
                else:
                    existing += 1

    print(f'created={created}, existing={existing}, total={FAQCategory.objects.count()}')


if __name__ == '__main__':
    main()
