"""Run the opt-in public-fixture CPU integration test with an existing local runtime."""
import argparse
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New output directory")
    args = parser.parse_args(argv)
    import pytest
    test = Path(__file__).resolve().parents[1] / "tests/integration/test_cpu_smoke.py"
    return pytest.main([str(test), "--cpu-runtime-config", str(args.runtime_config.resolve()),
                        "--cpu-output", str(args.output.resolve()), "-q"])


if __name__ == "__main__":
    raise SystemExit(main())
