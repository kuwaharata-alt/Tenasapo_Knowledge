#!/usr/bin/env python
"""互換入口。実装は scripts.development.locally_dev。"""

from scripts.development.locally_dev import BASE_DIR, main, run_command

__all__ = ['BASE_DIR', 'main', 'run_command']


if __name__ == '__main__':
    main()
