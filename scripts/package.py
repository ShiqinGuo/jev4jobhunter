"""Build a release archive from the explicit plugin source file set."""
import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


def package(root: Path, output: Path) -> dict:
    files = []
    for name in ('README.md', 'README.en.md', 'ROADMAP.md', 'CHANGELOG.md', 'CONTRIBUTING.md', 'VALIDATION.md', 'LICENSE', '.gitignore', 'package.json', 'package-lock.json',
                 'docs/media/README.md', 'docs/media/demo.gif', 'docs/media/demo-poster.png',
                 'docs/media/architecture.svg', 'docs/media/generate.py', 'docs/media/motion.py', 'docs/media/requirements.txt',
                 '.agents/plugins/marketplace.json',
                 '.codex-plugin/plugin.json', '.claude-plugin/plugin.json', '.claude-plugin/marketplace.json',
                 '.github/workflows/test.yml', 'scripts/package.py', 'scripts/audit-gate.ps1', 'scripts/notify.ps1'):
        path = root / name
        if path.is_file():
            files.append(path)
    skill = root / 'skills' / 'job-hunter'
    files.append(skill / 'SKILL.md')
    for directory, suffix in [('references', '.md'), ('scripts', '.py'), ('agents', '.yaml')]:
        files.extend(p for p in (skill / directory).glob('*' + suffix) if p.is_file())
    files.extend(p for p in (root / 'commands').glob('*.md') if p.is_file())
    files.extend(p for p in (root / 'tests').glob('test_*.py') if p.is_file())
    forbidden = {'state.json', 'policy.json', 'profile.md', 'android-device.json', 'automation-guide.md'}
    files = [p for p in files if p.name not in forbidden]
    if any(p.is_symlink() or not p.resolve().is_relative_to(root.resolve()) for p in files):
        raise ValueError('package-source-must-stay-inside-plugin')
    contents = {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(set(files))}
    manifest = json.loads(contents['.codex-plugin/plugin.json'])
    if manifest['name'] != 'job-hunter':
        raise ValueError('unexpected-plugin-name')
    output = output.resolve()
    if output in [p.resolve() for p in files]:
        raise ValueError('output-would-overwrite-source')
    if output.exists():
        raise ValueError('output-already-exists:choose-a-new-artifact-path')
    hashes = {name: hashlib.sha256(body).hexdigest() for name, body in contents.items()}
    with ZipFile(output, 'x', compression=ZIP_DEFLATED) as archive:
        for name, body in {**contents, 'FILES.sha256.json': json.dumps(hashes, indent=2).encode('utf-8')}.items():
            entry = ZipInfo('job-hunter/' + name, date_time=(2026, 1, 1, 0, 0, 0))
            entry.compress_type = ZIP_DEFLATED
            entry.external_attr = 0o644 << 16
            archive.writestr(entry, body)
    with ZipFile(output) as archive:
        assert archive.testzip() is None
        for name, digest in hashes.items():
            assert hashlib.sha256(archive.read('job-hunter/' + name)).hexdigest() == digest
    return {'archive': str(output), 'version': manifest['version'], 'files': len(hashes),
            'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(package(Path(__file__).resolve().parents[1], args.output), ensure_ascii=False))
