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
