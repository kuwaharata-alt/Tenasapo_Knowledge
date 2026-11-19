#!/usr/bin/env python
"""互換入口。実装は scripts.deployment.migrate_to_postgres。"""

from scripts.deployment.migrate_to_postgres import (
    BASE_DIR,
    backup_sqlite_data,
    load_data,
    main,
    migrate_database,
    test_postgres_connection,
    verify_data,
)

__all__ = [
    'BASE_DIR', 'backup_sqlite_data', 'load_data', 'main', 'migrate_database',
    'test_postgres_connection', 'verify_data',
]


if __name__ == '__main__':
    main()
