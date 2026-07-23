import os
import ssl
import sys
from django.core.management import execute_from_command_line
from django.core.management.base import BaseCommand
from django.core.wsgi import get_wsgi_application
from werkzeug.serving import run_simple


class Command(BaseCommand):
    help = 'Runs a development server with SSL/TLS support'

    def add_arguments(self, parser):
        parser.add_argument(
            'addrport',
            nargs='?',
            default='127.0.0.1:8000',
            help='Optional port number, or ipaddr:port',
        )
        parser.add_argument(
            '--cert',
            dest='cert',
            default='localhost.crt',
            help='Path to SSL certificate file (default: localhost.crt)',
        )
        parser.add_argument(
            '--key',
            dest='key',
            default='localhost.key',
            help='Path to SSL key file (default: localhost.key)',
        )

    def handle(self, *args, **options):
        addrport = options['addrport']
        cert_file = options['cert']
        key_file = options['key']

        # Parse address and port
        if ':' in addrport:
            addr, port = addrport.rsplit(':', 1)
            try:
                port = int(port)
            except ValueError:
                self.stderr.write(self.style.ERROR(f'Invalid port: {port}'))
                return
        else:
            addr = '127.0.0.1'
            try:
                port = int(addrport)
            except ValueError:
                self.stderr.write(self.style.ERROR(f'Invalid port: {addrport}'))
                return

        if not os.path.exists(cert_file):
            self.stderr.write(self.style.ERROR(f'Certificate file not found: {cert_file}'))
            return

        if not os.path.exists(key_file):
            self.stderr.write(self.style.ERROR(f'Key file not found: {key_file}'))
            return

        application = get_wsgi_application()

        self.stdout.write(
            self.style.SUCCESS(f'\nStarting HTTPS development server at https://{addr}:{port}/')
        )
        self.stdout.write(f'Using SSL certificate: {os.path.abspath(cert_file)}')
        self.stdout.write(f'Using SSL key: {os.path.abspath(key_file)}')
        self.stdout.write(self.style.WARNING('Press CTRL-C to quit.\n'))

        try:
            run_simple(
                addr,
                port,
                application,
                use_reloader=True,
                use_debugger=True,
                ssl_context=(cert_file, key_file),
            )
        except KeyboardInterrupt:
            self.stdout.write('\nShutting down...')
