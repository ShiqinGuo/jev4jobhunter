import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

spec = importlib.util.spec_from_file_location('package', Path(__file__).resolve().parents[1] / 'scripts' / 'package.py')
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class PackageTests(unittest.TestCase):
    def test_package_excludes_runtime_data_and_is_reproducible(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'plugin'
            for name in ('.codex-plugin', 'skills/job-hunter/references', '.job-hunter', 'drafts'):
                (root / name).mkdir(parents=True)
            (root / '.codex-plugin/plugin.json').write_text(json.dumps({'name': 'job-hunter', 'version': '0.3.0-preview.1'}))
            (root / 'skills/job-hunter/SKILL.md').write_text('Fixture skill')
            (root / '.job-hunter/state.json').write_text('PRIVATE')
            (root / 'skills/job-hunter/references/profile.md').write_text('PRIVATE')
            first = package.package(root, Path(temp) / 'a.zip')
            second = package.package(root, Path(temp) / 'b.zip')
            self.assertEqual(first['sha256'], second['sha256'])
            with ZipFile(first['archive']) as archive:
                self.assertFalse(any(b'PRIVATE' in archive.read(name) for name in archive.namelist()))
                self.assertIn('job-hunter/FILES.sha256.json', archive.namelist())
            with self.assertRaisesRegex(ValueError, 'output-already-exists'):
                package.package(root, Path(first['archive']))


if __name__ == '__main__':
    unittest.main()


def test_mobile_release_contains_native_sources_and_pytest_support(tmp_path):
    root = tmp_path / 'plugin'
    names = {
        '.codex-plugin/plugin.json': json.dumps({'name': 'job-hunter', 'version': '0.7.0'}),
        'skills/job-hunter/SKILL.md': 'Fixture skill',
        'skills/job-hunter/scripts/android/NativeText.java': 'class NativeText {}',
        'skills/job-hunter/scripts/android/SendTarget.java': 'class SendTarget {}',
        'tests/fixtures/android/NativeSendTargetCase.java': 'class NativeSendTargetCase {}',
        'pytest.ini': '[pytest]',
        'tests/conftest.py': '# fixtures',
        'tests/android_support.py': '# support',
        'tests/run_android.py': '# runner',
        'tests/requirements.txt': 'pytest>=8.4,<10',
        'tests/README.android.md': 'Test commands',
    }
    for name, body in names.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding='utf-8')
    output = package.package(root, tmp_path / 'mobile.zip')
    with ZipFile(output['archive']) as archive:
        for name in names:
            assert archive.read('job-hunter/' + name).decode('utf-8') == names[name]
