"""DB・本番設定・秘密値に触れずに補助スクリプトの実行境界を検証する。"""

from contextlib import ExitStack, redirect_stdout
import importlib
import io
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import call, MagicMock, Mock, mock_open, patch


ROOT = Path(__file__).resolve().parents[1]
REAL_POPEN = subprocess.Popen
ENTRYPOINTS = (
    ('locally_dev', 'scripts.development.locally_dev'),
    ('migrate_to_postgres', 'scripts.deployment.migrate_to_postgres'),
    ('register_google_oauth', 'scripts.users.register_google_oauth'),
    ('seed_categories_2plus', 'scripts.knowledge.seed_categories_2plus'),
)

# Fresh isolated interpreter: stop at main's call event, BEFORE any body executes.
# Guards turn accidental initialization, writes, secret reads or DB/network access
# into failures, rather than relying solely on the implementation being correct.
PROBE = r'''
import importlib
import importlib.abc
import os
from pathlib import Path
import runpy
import sys
from unittest.mock import patch
import django

root, legacy, implementation, mode = sys.argv[1:]
root = Path(root)
source = root.joinpath(*implementation.split('.')).with_suffix('.py')
initial_cwd = Path.cwd()
initial_settings = os.environ.get('DJANGO_SETTINGS_MODULE')

def forbidden(*args, **kwargs):
    raise AssertionError('Forbidden setup, command or secret access')

class BlockSettingsAndModels(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if (fullname == 'config' or fullname.startswith('config.')
                or fullname == 'tenasapo_knowledge.models'
                or fullname in ('django.contrib.sites.models', 'allauth.socialaccount.models')):
            raise AssertionError('Forbidden settings/model import: ' + fullname)

def audit(event, args):
    if event == 'open':
        path, mode, flags = args
        name = os.fsdecode(path) if isinstance(path, (str, bytes)) else ''
        basename = Path(name).name.lower()
        if (basename.startswith(('.env', 'client_secret'))
                or basename.endswith(('.sqlite3', '.sqlite', '.db'))
                or basename == 'sqlite_backup.json'):
            raise AssertionError('Forbidden data/secret file access')
        if (mode and any(char in mode for char in 'wax+')) or flags & (
                os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            raise AssertionError('Forbidden file write')
    if event in ('os.chdir', 'sqlite3.connect', 'socket.connect', 'subprocess.Popen'):
        raise AssertionError('Forbidden side effect: ' + event)

class ReachedMain(BaseException):
    pass

def trace(frame, event, arg):
    if event == 'call' and frame.f_code.co_name == 'main':
        assert Path(frame.f_code.co_filename).resolve() == source.resolve()
        assert frame.f_globals['BASE_DIR'] == root
        assert str(root) in sys.path
        raise ReachedMain()
    return trace

sys.meta_path.insert(0, BlockSettingsAndModels())
sys.addaudithook(audit)
with patch.object(django, 'setup', forbidden), patch('os.getenv', forbidden), \
        patch('django.core.management.call_command', forbidden):
    if mode == 'import':
        sys.path.insert(0, str(root))
        old = importlib.import_module(legacy)
        new = importlib.import_module(implementation)
        assert old.main is new.main
        assert new.BASE_DIR == root
        assert not django.conf.settings.configured
    else:
        if mode == 'legacy':
            target = root / (legacy + '.py')
        else:
            target = source
        # Match Python's path-file execution: only the script directory starts
        # on sys.path. In particular, standalone implementations must add root.
        sys.path.insert(0, str(target.parent))
        sys.settrace(trace)
        try:
            if mode == 'module':
                sys.path.insert(0, str(root))
                runpy.run_module(implementation, run_name='__main__')
            else:
                runpy.run_path(str(target), run_name='__main__')
        except ReachedMain:
            pass
        else:
            raise AssertionError('Entrypoint did not delegate to main')
        finally:
            sys.settrace(None)
assert Path.cwd() == initial_cwd
assert os.environ.get('DJANGO_SETTINGS_MODULE') == initial_settings
print('SAFE ' + legacy + ' ' + mode)
'''


def fake_module(name, **members):
    module = ModuleType(name)
    vars(module).update(members)
    return module


class ScriptEntrypointTests(unittest.TestCase):
    def setUp(self):
        # No Django test runner or real settings needed; models are substituted
        # before a main() body is ever called.
        guards = ExitStack()
        self.addCleanup(guards.close)
        for target in ('django.setup', 'django.core.management.call_command',
                       'sqlite3.connect', 'builtins.open', 'subprocess.Popen'):
            guards.enter_context(patch(
                target, side_effect=AssertionError('Unmocked side effect: ' + target),
            ))
        self.output = io.StringIO()
        self.addCleanup(self.output.close)
        self.redirect = redirect_stdout(self.output)
        self.redirect.__enter__()
        self.addCleanup(self.redirect.__exit__, None, None, None)

    def test_isolated_imports_and_cli_dispatch_have_no_side_effects(self):
        # Do not pass OAuth/DB secrets from the parent process to probes.
        env = {key: os.environ[key] for key in ('SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP')
               if key in os.environ}
        env['PYTHONIOENCODING'] = 'utf-8'
        # Permit process creation only for the guarded, sanitized probes here.
        with patch.object(subprocess, 'Popen', REAL_POPEN), \
            tempfile.TemporaryDirectory(prefix='script entrypoints ') as cwd:
            for legacy, implementation in ENTRYPOINTS:
                for mode in ('import', 'legacy', 'standalone', 'module'):
                    with self.subTest(script=legacy, mode=mode):
                        result = subprocess.run(
                            [sys.executable, '-I', '-B', '-c', PROBE,
                             str(ROOT), legacy, implementation, mode],
                            cwd=cwd, env=env, capture_output=True,
                            text=True, encoding='utf-8', timeout=30,
                        )
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        self.assertIn(f'SAFE {legacy} {mode}', result.stdout)

    def test_legacy_public_api_reexports_implementation_objects(self):
        expected = {
            'locally_dev': ('BASE_DIR', 'run_command', 'main'),
            'migrate_to_postgres': (
                'BASE_DIR', 'backup_sqlite_data', 'test_postgres_connection',
                'migrate_database', 'load_data', 'verify_data', 'main',
            ),
            'register_google_oauth': ('main',),
            'seed_categories_2plus': ('CATEGORY_DATA', 'main'),
        }
        for legacy, implementation in ENTRYPOINTS:
            old = importlib.import_module(legacy)
            new = importlib.import_module(implementation)
            for name in expected[legacy]:
                with self.subTest(script=legacy, name=name):
                    self.assertIs(getattr(old, name), getattr(new, name))

    def test_legacy_main_calls_delegate_once(self):
        for legacy, implementation in ENTRYPOINTS:
            module = importlib.import_module(implementation)
            with self.subTest(script=legacy), patch.object(module, 'main') as main:
                runpy.run_path(str(ROOT / (legacy + '.py')), run_name='__main__')
                main.assert_called_once_with()

    def test_development_uses_root_paths_including_spaces(self):
        from scripts.development import locally_dev as module
        root = Path('C:/workspace with spaces/knowledge_base')
        with patch.object(module, 'BASE_DIR', root), \
                patch.object(Path, 'exists', return_value=True) as exists, \
                patch.object(module, 'run_command', return_value=True) as command:
            module.main()
        self.assertEqual(exists.call_count, 3)
        self.assertEqual(command.call_args_list, [
            call(f'"{root / ".venv"}\\Scripts\\pip.exe" install -r requirements.txt',
                 '依存関係インストール'),
            call(f'"{root / ".venv"}\\Scripts\\python.exe" "{root / "manage.py"}" migrate',
                 'データベースマイグレーション'),
            call(f'"{root / ".venv"}\\Scripts\\python.exe" "{root / "manage.py"}" collectstatic --noinput',
                 '静的ファイル収集'),
        ])
        self.assertIn(f'cd "{root}"', self.output.getvalue())

    def test_development_missing_prerequisites_never_run_commands(self):
        from scripts.development import locally_dev as module
        for existence in ([False], [True, False]):
            with self.subTest(existence=existence), \
                    patch.object(Path, 'exists', side_effect=existence), \
                    patch.object(module, 'run_command') as command:
                with self.assertRaises(SystemExit) as caught:
                    module.main()
                self.assertEqual(caught.exception.code, 1)
                command.assert_not_called()

    def test_run_command_keeps_workspace_cwd_and_handles_failures(self):
        from scripts.development import locally_dev as module
        for result, expected in (
            (SimpleNamespace(returncode=0, stderr=''), True),
            (SimpleNamespace(returncode=1, stderr='test error'), False),
            (OSError('test failure'), False),
        ):
            with self.subTest(expected=expected), patch.object(module.subprocess, 'run') as run:
                if isinstance(result, Exception):
                    run.side_effect = result
                else:
                    run.return_value = result
                self.assertEqual(module.run_command('test-only-command', 'test'), expected)
                run.assert_called_once_with(
                    'test-only-command', shell=True, cwd=ROOT, capture_output=True, text=True,
                )

    def test_backup_path_and_management_arguments_without_file_write(self):
        from scripts.deployment import migrate_to_postgres as module
        opened = mock_open()
        with patch('builtins.open', opened), patch.object(module, 'call_command') as command:
            backup = module.backup_sqlite_data()
            self.assertEqual(backup, ROOT / 'sqlite_backup.json')
            opened.assert_called_once_with(backup, 'w', encoding='utf-8')
            command.assert_called_once_with('dumpdata', '--all', stdout=opened(), verbosity=1)
            command.reset_mock()
            self.assertTrue(module.migrate_database())
            command.assert_called_once_with('migrate', verbosity=1)
            command.reset_mock()
            self.assertTrue(module.load_data(backup))
            command.assert_called_once_with('loaddata', str(backup), verbosity=1)

    def test_migration_main_preserves_order_and_settings_override(self):
        from scripts.deployment import migrate_to_postgres as module
        order = Mock()
        backup = ROOT / 'sqlite_backup.json'
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {'DJANGO_SETTINGS_MODULE': 'test.only'}))
            setup = stack.enter_context(patch.object(module.django, 'setup'))
            order.attach_mock(setup, 'setup')
            for name, result in (
                ('test_postgres_connection', True), ('backup_sqlite_data', backup),
                ('migrate_database', True), ('load_data', True), ('verify_data', True),
            ):
                mocked = stack.enter_context(patch.object(module, name, return_value=result))
                order.attach_mock(mocked, name)
            module.main()
            self.assertEqual(os.environ['DJANGO_SETTINGS_MODULE'], 'test.only')
        self.assertEqual(order.mock_calls, [
            call.setup(), call.test_postgres_connection(), call.backup_sqlite_data(),
            call.migrate_database(), call.load_data(backup), call.verify_data(),
        ])

    def test_migration_connection_failure_stops_before_backup(self):
        from scripts.deployment import migrate_to_postgres as module
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(module.django, 'setup'), \
                patch.object(module, 'test_postgres_connection', return_value=False), \
                patch.object(module, 'backup_sqlite_data') as backup:
            with self.assertRaises(SystemExit) as caught:
                module.main()
            self.assertEqual(caught.exception.code, 1)
            backup.assert_not_called()

    def test_oauth_only_uses_mock_settings_and_models_when_called(self):
        from scripts.users import register_google_oauth as module
        site = SimpleNamespace(domain='test.invalid')
        Site = MagicMock()
        Site.DoesNotExist = type('DoesNotExist', (Exception,), {})
        Site.objects.get.side_effect = Site.DoesNotExist
        Site.objects.create.return_value = site
        SocialApp = MagicMock()
        SocialApp.objects.filter.return_value.first.return_value = None
        models = {
            'django.contrib.sites.models': fake_module('django.contrib.sites.models', Site=Site),
            'allauth.socialaccount.models': fake_module('allauth.socialaccount.models', SocialApp=SocialApp),
        }
        # Only synthetic credentials are available; never read the real env.
        with patch.dict(sys.modules, models), patch('django.setup') as setup, \
                patch.dict(os.environ, {'DJANGO_SETTINGS_MODULE': 'test.only'}, clear=True), \
                patch.object(module.os, 'getenv', side_effect=lambda key: {
                    'GOOGLE_OAUTH_CLIENT_ID': ' fake-client ',
                    'GOOGLE_OAUTH_CLIENT_SECRET': ' fake-secret ',
                }[key]):
            module.main()
            setup.assert_called_once_with()
            self.assertEqual(os.environ['DJANGO_SETTINGS_MODULE'], 'test.only')
        Site.objects.create.assert_called_once_with(
            id=1, domain='tk-tenainfo.rikka.biz:8443', name='tk-tenainfo.rikka.biz:8443',
        )
        SocialApp.objects.create.assert_called_once_with(
            provider='google', name='Google', client_id='fake-client', secret='fake-secret',
        )
        SocialApp.objects.create.return_value.sites.add.assert_called_once_with(site)
        self.assertNotIn('fake-secret', self.output.getvalue())

    def test_oauth_missing_values_fail_before_model_operations(self):
        from scripts.users import register_google_oauth as module
        Site, SocialApp = MagicMock(), MagicMock()
        models = {
            'django.contrib.sites.models': fake_module('django.contrib.sites.models', Site=Site),
            'allauth.socialaccount.models': fake_module('allauth.socialaccount.models', SocialApp=SocialApp),
        }
        with patch.dict(sys.modules, models), patch('django.setup'), \
                patch.dict(os.environ, {}, clear=True), patch.object(module.os, 'getenv', return_value=None):
            with self.assertRaises(ValueError):
                module.main()
        self.assertEqual(Site.mock_calls, [])
        self.assertEqual(SocialApp.mock_calls, [])

    def test_seed_only_calls_mock_model_and_retains_category_data(self):
        from scripts.knowledge import seed_categories_2plus as module
        model = MagicMock()
        model.objects.get_or_create.side_effect = [(object(), True), (object(), False)]
        model.objects.count.return_value = 2
        models = fake_module('tenasapo_knowledge.models', FAQCategory=model)
        with patch.dict(sys.modules, {'tenasapo_knowledge.models': models}), \
                patch('django.setup') as setup, patch.dict(os.environ, {}, clear=True), \
                patch.object(module, 'CATEGORY_DATA', {'parent': {'middle': ['new', 'old']}}):
            module.main()
            setup.assert_called_once_with()
            self.assertEqual(os.environ['DJANGO_SETTINGS_MODULE'], 'config.settings')
        self.assertEqual(model.objects.get_or_create.call_args_list, [
            call(parent_name='parent', middle_name='middle', child_name='new'),
            call(parent_name='parent', middle_name='middle', child_name='old'),
        ])
        self.assertIn('created=1, existing=1, total=2', self.output.getvalue())
        self.assertEqual(len(module.CATEGORY_DATA), 12)


if __name__ == '__main__':
    unittest.main()