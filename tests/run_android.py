"""Run named mobile feature suites without connecting to a phone or calling Jev."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
FEATURES = ('device','navigation','acquisition','delivery','communication','native',
            'recovery','protocol','proxy','policy','workflow')
IMPACT = {
    'android_actions.py': ('workflow','device','recovery'),
    'android_application.py': ('acquisition','delivery','communication','recovery','workflow'),
    'android_domain.py': FEATURES,
    'android_device.py': ('device','navigation','native','proxy','workflow'),
    'android_ui.py': ('navigation','communication','recovery','workflow'),
    'android_repository.py': ('acquisition','recovery','delivery','workflow'),
    'android_receipts.py': ('protocol','proxy','delivery','workflow'),
    'android_capture.py': ('protocol','proxy'),
    'boss_protocol.py': ('protocol','acquisition','workflow'),
    'android_text.py': ('native','communication'),
    'build_native_input.py': ('native',),
    'store.py': ('delivery','recovery','communication','policy','workflow'),
    'policy_rules.py': ('policy','delivery','communication','workflow'),
    'jev.py': ('acquisition','workflow'),
}


def changed_plan():
    paths = set()
    for arguments in (['git','diff','--name-only','HEAD'],['git','ls-files','--others','--exclude-standard']):
        result = subprocess.run(arguments,cwd=ROOT,capture_output=True,text=True,encoding='utf-8',check=True)
        paths.update(result.stdout.splitlines())
    selected, extra = set(), set()
    for name in paths:
        path = Path(name)
        if name == 'pytest.ini':
            selected.update(FEATURES)
        if name.startswith('skills/job-hunter/scripts/'):
            selected.update(IMPACT.get(path.name,()))
            if path.suffix == '.java' or path.name == 'android-requirements.txt':
                selected.add('native' if path.suffix == '.java' else 'protocol')
            if path.name == 'store.py':
                extra.update(('test_runtime.py','test_policy_and_runs.py','test_boss_chat.py'))
            if path.name == 'policy_rules.py':
                extra.update(('test_policy_and_runs.py','test_boss_chat.py'))
        elif name.startswith('tests/'):
            if path.name in ('android_support.py','conftest.py','requirements.txt','run_android.py'):
                selected.update(FEATURES)
            elif path.name.startswith('test_android_'):
                selected.add(path.stem.removeprefix('test_android_'))
            elif path.suffix == '.java':
                selected.add('native')
    return [key for key in FEATURES if key in selected],sorted(extra)


class PytestSummary:
    def __init__(self):
        self.selected = 0
        self.observed = set()
        self.passed = set()
        self.failures = set()
        self.errors = set()
        self.skipped = set()

    def pytest_collection_finish(self, session):
        self.selected = len(session.items)

    def pytest_runtest_logreport(self, report):
        self.observed.add(report.nodeid)
        if report.skipped:
            self.skipped.add(report.nodeid)
        elif report.failed:
            (self.failures if report.when == 'call' else self.errors).add(report.nodeid)
        elif report.when == 'call' and report.passed:
            self.passed.add(report.nodeid)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('features',nargs='*',choices=('all',*FEATURES))
    parser.add_argument('--changed',action='store_true',help='Select pytest suites for current Git changes.')
    parser.add_argument('--list',action='store_true',help='Show the plan without running tests.')
    parser.add_argument('--jdk',type=Path,help='JDK for the real Java selector fixture; also accepts JAVA_HOME.')
    parser.add_argument('--report',type=Path,help='Optional JSON report outside the repository.')
    parser.add_argument('-v','--verbose',action='store_true')
    args = parser.parse_args()
    if args.changed and args.features:
        parser.error('--changed and named features are separate selection modes')
    if args.changed:
        selected,extra = changed_plan()
    else:
        selected = list(FEATURES) if not args.features or 'all' in args.features else list(dict.fromkeys(args.features))
        extra = []
    files = [f'test_android_{key}.py' for key in selected]+extra
    print('Features: '+', '.join(selected),flush=True)
    if extra:
        print('Shared contracts: '+', '.join(extra),flush=True)
    if args.list or not files:
        return 0
    try:
        import pytest
    except ImportError:
        parser.error('Install tests/requirements.txt in the test Python runtime.')
    if 'protocol' in selected:
        try:
            import lz4.block
            from cryptography.hazmat.decrepit.ciphers.algorithms import ARC4
        except ImportError:
            parser.error('Use a Python runtime with tests/requirements.txt installed.')
    if args.jdk:
        os.environ['JOB_HUNTER_TEST_JDK'] = str(args.jdk.resolve())
    if 'native' in selected:
        configured = os.environ.get('JOB_HUNTER_TEST_JDK') or os.environ.get('JAVA_HOME')
        javac = Path(configured)/'bin'/('javac.exe' if os.name=='nt' else 'javac') if configured else None
        if not (javac and javac.is_file()) and not shutil.which('javac'):
            parser.error('Native selector tests require --jdk JDK or JAVA_HOME.')
    report = args.report.resolve() if args.report else None
    if report and report.is_relative_to(ROOT):
        parser.error('Test reports must stay outside the repository')
    output = report.parent if report else Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))/'outputs'/'job-hunter-tests'
    parameters = ['-c',str(ROOT/'pytest.ini'),*[str(ROOT/'tests'/name) for name in files],
                  '--session-output='+str(output.resolve()),'-v' if args.verbose else '-q']
    if args.jdk:
        parameters.append('--jdk='+str(args.jdk.resolve()))
    summary = PytestSummary()
    started = time.monotonic()
    exit_code = int(pytest.main(parameters,plugins=[summary]))
    data = {'framework':'pytest','version':pytest.__version__,'features':selected,'sharedContracts':extra,
            'tests':len(summary.observed),'selected':summary.selected,'passedCount':len(summary.passed),
            'elapsedSeconds':round(time.monotonic()-started,3),'passed':exit_code==0,
            'failures':sorted(summary.failures),'errors':sorted(summary.errors),'skipped':sorted(summary.skipped)}
    if report:
        report.parent.mkdir(parents=True,exist_ok=True)
        report.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
