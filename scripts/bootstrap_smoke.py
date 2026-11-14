"""Exercise the real root bootstrap using locally built release assets in CI."""

import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main():
    if os.environ.get('GITHUB_ACTIONS') != 'true' or os.geteuid() != 0:
        raise SystemExit('Only run as root on a disposable GitHub Actions runner.')
    if pathlib.Path('/opt/godov-remnawave-cli').exists():
        raise SystemExit('Refusing to replace an existing CLI.')
    subprocess.run([sys.executable, str(ROOT / 'scripts/package.py')], check=True)
    with tempfile.TemporaryDirectory(prefix='godov-bootstrap-test-') as temp:
        folder = pathlib.Path(temp)
        curl = folder / 'curl'
        curl.write_text('''#!/usr/bin/python3
import os,pathlib,shutil,sys
args=sys.argv[1:]
url=next(value for value in args if value.startswith('https://'))
prefix='https://github.com/G0d0v/remnawave-installer/releases/download/v1.0.0/'
if not url.startswith(prefix):raise SystemExit('Unexpected release URL')
name=url[len(prefix):]
if name not in ('godov-remnawave-v1.0.0.tar.gz','SHA256SUMS'):raise SystemExit('Unexpected asset')
shutil.copyfile(pathlib.Path(os.environ['GODOV_TEST_ASSETS'])/name,args[args.index('-o')+1])
''', encoding='utf-8')
        curl.chmod(0o755)
        env = dict(os.environ, PATH=str(folder) + ':' + os.environ['PATH'], GODOV_TEST_ASSETS=str(ROOT / 'build'))
        result = subprocess.run(['bash', str(ROOT / 'install.sh'), '--preview', 'menu'],
                                env=env, text=True, capture_output=True)
        if result.returncode:
            raise RuntimeError(result.stderr)
        assert 'ПРЕДПРОСМОТР' in result.stdout
        version = subprocess.check_output(['/usr/local/bin/godov-remna', '--version'], text=True)
        assert version.strip() == 'G0d0v 1.0.0'
    print('PASS: SHA-256 release download, safe extraction, installed launcher and CLI preview', flush=True)


if __name__ == '__main__':
    main()
