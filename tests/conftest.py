"""Pytest fixture ownership and session artifacts for phone feature suites."""
import os
from pathlib import Path
import sys
import uuid
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'skills/job-hunter/scripts'))
from android_support import AndroidCase


def pytest_addoption(parser):
    parser.addoption('--jdk',default=None,help='JDK for the native Java selector fixture')
    parser.addoption('--session-output',default=None,help='Test artifacts outside the repository')


def pytest_configure(config):
    configured = config.getoption('--session-output')
    base = Path(configured).resolve() if configured else Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))/'outputs'/'job-hunter-tests'
    base = base.resolve()
    if base.is_relative_to(ROOT):
        raise pytest.UsageError('--session-output must stay outside the repository')
    if config.option.basetemp is None:
        config.option.basetemp = str(base/('pytest-temp-'+uuid.uuid4().hex))
    if config.getoption('--jdk'):
        os.environ['JOB_HUNTER_TEST_JDK'] = str(Path(config.getoption('--jdk')).resolve())


@pytest.fixture
def case(tmp_path):
    return AndroidCase(tmp_path)
