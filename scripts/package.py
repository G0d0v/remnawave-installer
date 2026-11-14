"""Build the exact archive consumed by install.sh; no credentials or dev files."""

import hashlib
import pathlib
import tarfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
VERSION = (ROOT / 'VERSION').read_text().strip()


def build():
    output = ROOT / 'build'
    output.mkdir(exist_ok=True)
    archive = output / f'godov-remnawave-v{VERSION}.tar.gz'
    files = [ROOT / name for name in ('manage.py', 'VERSION', 'LICENSE', 'README.md', 'install.sh')]
    files += sorted((ROOT / 'godov').glob('*.py'))
    with tarfile.open(archive, 'w:gz') as tar:
        for path in files:
            tar.add(path, arcname=path.relative_to(ROOT).as_posix(), recursive=False)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / 'SHA256SUMS').write_text(f'{digest}  {archive.name}\n', encoding='ascii', newline='\n')
    print(archive)
    return archive


if __name__ == '__main__':
    build()
