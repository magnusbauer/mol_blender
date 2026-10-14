"""Run pytest's Library browser checks with a simulated host.

This does not establish live ChatGPT integration or durable host storage.
"""
import argparse
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', required=True, help='Chrome/Chromium executable')
    args, pytest_args = parser.parse_known_args(argv)
    import pytest
    tests = Path(__file__).resolve().parents[1] / 'tests/browser/test_library_preview.py'
    return pytest.main([str(tests), '--browser', args.browser, *pytest_args])


if __name__ == '__main__':
    raise SystemExit(main())
