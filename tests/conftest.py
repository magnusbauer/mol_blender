def pytest_addoption(parser):
    parser.addoption("--browser", help="Explicit Chrome/Chromium executable for viewer tests")
    parser.addoption("--cpu-runtime-config", help="Opt in to a real public-fixture CPU render using this runtime JSON")
    parser.addoption("--cpu-output", help="New directory for CPU smoke outputs; defaults to pytest temporary storage")
