"""Run pytest's real-browser viewer checks with a simulated host.

This does not establish live ChatGPT integration or durable host storage.
"""
import argparse
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', required=True, help='Chrome/Chromium executable')
    parser.add_argument('--suite', choices=('all', 'inline', 'library'), default='all',
                        help='Browser test suite (default: all)')
    args, pytest_args = parser.parse_known_args(argv)
    import pytest
    tests = Path(__file__).resolve().parents[1] / 'tests/browser'
    if args.suite != 'all':
        tests /= 'test_' + args.suite + '_preview.py'
    return pytest.main([str(tests), '--browser', args.browser, *pytest_args])


if __name__ == '__main__':
    raise SystemExit(main())
