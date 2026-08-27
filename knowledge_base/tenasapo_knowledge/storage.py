import os
import logging
from io import BytesIO
from django.conf import settings
from django.core.files.storage import Storage, FileSystemStorage
from django.core.files import File
from django.utils.deconstruct import deconstructible

_logger = logging.getLogger(__name__)

@deconstructible
class HybridGoogleDriveStorage(Storage):
    """
    Google Drive API を使用した Django カスタムストレージ。
    サービスアカウント情報 (GOOGLE_SERVICE_ACCOUNT_JSON_PATH) が設定されていない、または
    ファイルが存在しないなど利用できない場合は自動的にローカルの FileSystemStorage にフォールバックします。
    """
    def __init__(self, **kwargs):
        self._local_storage = FileSystemStorage()
        self._initialized = False
        self.service = None
        self.root_folder_id = getattr(settings, 'GOOGLE_DRIVE_ROOT_FOLDER_ID', None)
        
        # 認証鍵パスの確認
        json_path = getattr(settings, 'GOOGLE_SERVICE_ACCOUNT_JSON_PATH', None)
        if json_path and os.path.exists(json_path):
            try:
                from google.oauth2 import service_account
                from googleapiclient.discovery import build
                import httplib2
                
                # Google API へのリクエストに5秒のタイムアウトを設定し、社内プロキシやAzure内での無制限ハングアップによる502/回線切断を防止します。
                http_transport = httplib2.Http(timeout=5)
                
                credentials = service_account.Credentials.from_service_account_file(
                    json_path,
                    scopes=['https://www.googleapis.com/auth/drive']
                )
                authorized_http = credentials.authorize(http_transport)
                self.service = build('drive', 'v3', http=authorized_http)
                self._initialized = True
            except Exception as e:
                _logger.error(f"Failed to initialize Google Drive Storage (Timeout parameter set): {e}")

    @property
    def is_active(self):
        # settings.IS_TESTING 実行時はテスト環境の安全のためフォールバックする
        if getattr(settings, 'IS_TESTING', False):
            return False
        return self._initialized and self.service is not None

    def _open(self, name, mode='rb'):
        if not self.is_active:
            return self._local_storage._open(name, mode)
            
        from googleapiclient.http import MediaIoBaseDownload
        file_id = self._get_file_id_by_path(name)
        if not file_id:
            # Google Drive上に見つからない場合、ローカルストレージにあるか確認しフォールバックします
            if self._local_storage.exists(name):
                return self._local_storage._open(name, mode)
            raise FileNotFoundError(f"File not found on Google Drive or Local: {name}")
            
        try:
            request = self.service.files().get_media(fileId=file_id)
            fh = BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            fh.seek(0)
            return File(fh, name=os.path.basename(name))
        except Exception as e:
            _logger.error(f"Error reading file from Google Drive ({name}), attempting local fallback: {e}")
            if self._local_storage.exists(name):
                return self._local_storage._open(name, mode)
            raise IOError(f"Google Drive Read Error: {e}")

    def _save(self, name, content):
        if not self.is_active:
            return self._local_storage._save(name, content)
            
        from googleapiclient.http import MediaIoBaseUpload
        import mimetypes
        
        # パスをパースしてフォルダ階層を作成する
        # 例: name = "【12345】顧客名/Nexusドキュメント/filename.xlsx"
        parts = [p for p in name.replace('\\', '/').split('/') if p]
        if not parts:
            raise ValueError("Invalid file path name specified")
            
        folders = parts[:-1]
        filename = parts[-1]
        
        try:
            # フォルダ階層を順番に辿る（存在しなければ作成）
            current_parent = self.root_folder_id
            for folder in folders:
                current_parent = self._find_or_create_folder(folder, current_parent)
                
            # メディアアップロード準備
            mimetype, _ = mimetypes.guess_type(filename)
            if not mimetype:
                mimetype = 'application/octet-stream'
                
            # contentのポインタ初期化
            if hasattr(content, 'seek'):
                try: content.seek(0)
                except Exception: pass
                
            file_bytes = content.read()
            media = MediaIoBaseUpload(BytesIO(file_bytes), mimetype=mimetype, resumable=True)
            
            # 既存ファイルの検索（上書き用）
            existing_file_id = self._find_file(filename, current_parent)
            
            if existing_file_id:
                # 既存ファイルの更新 (上書き)
                self.service.files().update(fileId=existing_file_id, media_body=media).execute()
                _logger.info(f"Updated existing Google Drive file: {name} (ID: {existing_file_id})")
            else:
                # 新規ファイルの作成
                file_metadata = {
                    'name': filename,
                }
                if current_parent:
                    file_metadata['parents'] = [current_parent]
                    
                uploaded = self.service.files().create(body=file_metadata, media_body=media, fields='id').execute()
                _logger.info(f"Uploaded new file to Google Drive: {name} (ID: {uploaded.get('id')})")
                
            return name
        except Exception as e:
            _logger.error(f"Error saving file to Google Drive ({name}), falling back to local storage: {e}")
            try:
                # ポインタを受信当初にリセットしてローカルとして保存
                if hasattr(content, 'seek'):
                    try: content.seek(0)
                    except Exception: pass
                return self._local_storage._save(name, content)
            except Exception as local_e:
                _logger.error(f"Secondary fallback to local storage failed: {local_e}")
                raise IOError(f"Google Drive and Local Storage both failed to save: {e}")

    def exists(self, name):
        if not self.is_active:
            return self._local_storage.exists(name)
        try:
            found_on_drive = self._get_file_id_by_path(name) is not None
            if found_on_drive:
                return True
        except Exception:
            pass
        return self._local_storage.exists(name)

    def delete(self, name):
        if not self.is_active:
            return self._local_storage.delete(name)
            
        file_id = self._get_file_id_by_path(name)
        if file_id:
            try:
                self.service.files().delete(fileId=file_id).execute()
                _logger.info(f"Deleted file from Google Drive: {name} (ID: {file_id})")
            except Exception as e:
                _logger.error(f"Error deleting file from Google Drive: {e}")

    def url(self, name):
        if not self.is_active:
            return self._local_storage.url(name)
        return self._local_storage.url(name)

    def _find_folder(self, folder_name, parent_id=None):
        """
        案件番号 【XYZ】 を含むフォルダ名、または完全一致でフォルダを検索します。
        顧客名の半角・全角の表記揺れや後ろに作業概要が追記されている場合でも、
        同じ【案件番号】のフォルダであれば正しく再利用できるようにします。
        """
        project_prefix = None
        if folder_name.startswith('【') and '】' in folder_name:
            idx = folder_name.find('】')
            project_prefix = folder_name[:idx + 1]  # 例: "【12345】"

        if project_prefix:
            # 案件番号を部分一致で優先検索
            q = f"name contains '{project_prefix}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        else:
            # 案件番号が無い、または別の階層（例: "Nexusドキュメント"）の場合は完全一致で検索
            q = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"

        if parent_id:
            q += f" and '{parent_id}' in parents"
        else:
            if not self.root_folder_id:
                q += " and 'root' in parents"

        response = self.service.files().list(q=q, spaces='drive', fields='files(id)').execute()
        files = response.get('files', [])
        return files[0]['id'] if files else None

    def _find_or_create_folder(self, folder_name, parent_id=None):
        existing_id = self._find_folder(folder_name, parent_id)
        if existing_id:
            return existing_id
            
        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        if parent_id:
            file_metadata['parents'] = [parent_id]
        elif self.root_folder_id:
            file_metadata['parents'] = [self.root_folder_id]
            
        folder = self.service.files().create(body=file_metadata, fields='id').execute()
        return folder.get('id')

    def _find_file(self, filename, parent_id=None):
        q = f"name = '{filename}' and mimeType != 'application/vnd.google-apps.folder' and trashed = false"
        if parent_id:
            q += f" and '{parent_id}' in parents"
        else:
            if not self.root_folder_id:
                q += " and 'root' in parents"
                
        response = self.service.files().list(q=q, spaces='drive', fields='files(id)').execute()
        files = response.get('files', [])
        return files[0]['id'] if files else None

    def _get_file_id_by_path(self, name):
        parts = [p for p in name.replace('\\', '/').split('/') if p]
        if not parts:
            return None
            
        folders = parts[:-1]
        filename = parts[-1]
        
        try:
            current_parent = self.root_folder_id
            for folder in folders:
                folder_id = self._find_folder(folder, current_parent)
                if not folder_id:
                    return None
                current_parent = folder_id
                
            return self._find_file(filename, current_parent)
        except Exception as e:
            _logger.debug(f"Failed to find file {name} on Google Drive: {e}")
            return None
