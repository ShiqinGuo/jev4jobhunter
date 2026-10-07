"""Build the small normal-UI text helper with a configured Android SDK and JDK."""
from __future__ import annotations
import argparse
from pathlib import Path
import subprocess
import tempfile
import os

def build(sdk: Path, jdk: Path, output: Path):
    android = sdk / 'platforms/android-36/android.jar'
    ui = sdk / 'platforms/android-36/uiautomator.jar'
    optional = list((sdk / 'platforms/android-36/optional').glob('android.test.*.jar'))
    sources = sorted((Path(__file__).parent / 'android').glob('*.java'))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix='input-build-') as temporary:
        root = Path(temporary)
        classes, dex = root / 'classes', root / 'dex'
        classes.mkdir()
        dex.mkdir()
        subprocess.run([str(jdk / 'bin/javac.exe'), '-encoding', 'UTF-8', '--release', '8',
            '-classpath', os.pathsep.join(map(str, [android, ui, *optional])), '-d', str(classes), *map(str, sources)], check=True)
        subprocess.run([str(jdk / 'bin/java.exe'), '-cp', str(sdk / 'build-tools/35.0.0/lib/d8.jar'),
            'com.android.tools.r8.D8', '--min-api', '23', '--lib', str(android), '--lib', str(ui),
            '--output', str(dex), *map(str, classes.rglob('*.class'))], check=True)
        subprocess.run([str(jdk / 'bin/jar.exe'), 'cf', str(output), '-C', str(dex), 'classes.dex'], check=True)
    return output

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--jdk', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    print(build(arguments.sdk, arguments.jdk, arguments.output))
