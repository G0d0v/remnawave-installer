"""Consistent PostgreSQL snapshots and checked, traversal-safe archives."""

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath

from .config import MARKER, Config, load, save, write_private
from .render import render
from .system import Stack, protect_node

FILES = (MARKER, 'state.json', 'compose.yaml', 'panel.env', 'node.env', 'Caddyfile',
         'credentials.txt', 'xray-profile.json')


def checksum(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def pack(source: Path, archive: Path):
    files = sorted(path for path in source.rglob('*') if path.is_file() and path.name != 'manifest.json')
    if any(path.is_symlink() for path in source.rglob('*')):
        raise ValueError('Резервная копия не должна содержать symlink.')
    manifest = {path.relative_to(source).as_posix(): checksum(path) for path in files}
    write_private(source / 'manifest.json', json.dumps(manifest, indent=2))
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.exists() or archive.is_symlink():
        raise ValueError('Файл резервной копии уже существует.')
    fd = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as output, tarfile.open(fileobj=output, mode='w:gz') as tar:
            for path in files + [source / 'manifest.json']:
                tar.add(path, arcname=path.relative_to(source).as_posix(), recursive=False)
    except BaseException:
        archive.unlink(missing_ok=True)
        raise


def unpack(archive: Path, destination: Path):
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('Распаковка возможна только в пустой каталог.')
    destination.mkdir(parents=True, exist_ok=True)
    os.chmod(destination, 0o700)
    with tarfile.open(archive, 'r:gz') as tar:
        members = tar.getmembers()
        names = set()
        total = 0
        for member in members:
            name = PurePosixPath(member.name)
            total += member.size
            if (not member.isfile() or name.is_absolute() or '..' in name.parts or ':' in member.name
                    or '\\' in member.name or str(name) != member.name or member.name in names
                    or total > 32 * 1024**3 or len(members) > 100000):
                raise ValueError('Архив содержит недопустимый путь, тип, дубликат или слишком много данных.')
            names.add(member.name)
        if 'manifest.json' not in names:
            raise ValueError('В архиве отсутствует manifest.json.')
        # Inspect every header before writing even one file.
        for member in members:
            target = destination / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as source, target.open('xb') as output:
                os.chmod(target, 0o600)
                shutil.copyfileobj(source, output)
    manifest = json.loads((destination / 'manifest.json').read_text())
    if set(manifest) != names - {'manifest.json'}:
        raise ValueError('Список файлов архива не совпадает с manifest.json.')
    for name, digest in manifest.items():
        if checksum(destination / name) != digest:
            raise ValueError('Контрольная сумма резервной копии не совпадает.')


def compose_args(stack, *args):
    return ['docker', 'compose', '--project-directory', str(stack.root), '-f', str(stack.root / 'compose.yaml'), *args]


def database_dump(stack: Stack, target):
    with target.open('wb') as output:
        os.chmod(target, 0o600)
        proc = subprocess.run(compose_args(stack, 'exec', '-T', 'db', 'pg_dump', '-U', 'postgres',
                                          '--format=custom', 'postgres'), stdout=output, stderr=subprocess.PIPE,
                              timeout=900)
    if proc.returncode or target.stat().st_size == 0:
        raise RuntimeError('pg_dump не выполнен. Проверьте PostgreSQL; обновление остановлено.')


def backup_stack(stack: Stack) -> Path:
    c = load(stack.root)
    archive = stack.root / 'backups' / f'godov-{time.time_ns()}.tar.gz'
    with tempfile.TemporaryDirectory(prefix='godov-backup-') as temp:
        staging = Path(temp)
        os.chmod(staging, 0o700)
        for name in FILES:
            source = stack.root / name
            if source.is_symlink():
                raise ValueError('Отказ резервного копирования symlink.')
            if source.is_file():
                shutil.copy2(source, staging / name)
        if (stack.root / 'site').is_dir():
            shutil.copytree(stack.root / 'site', staging / 'site')
        if c.has_panel:
            database_dump(stack, staging / 'database.dump')
        if c.has_caddy:
            container = stack.compose('ps', '-a', '-q', 'caddy').stdout.strip()
            if container:
                from .system import run
                run(['docker', 'cp', container + ':/data', str(staging / 'caddy-data')], timeout=120)
        pack(staging, archive)
    return archive


def validate_snapshot(staging: Path):
    c = load(staging).validate()
    if c.has_panel and not (staging / 'database.dump').is_file():
        raise ValueError('В резервной копии панели отсутствует PostgreSQL dump.')
    for path in staging.rglob('*'):
        if path.is_file() and path.relative_to(staging).parts[0] not in {*FILES, 'database.dump', 'manifest.json', 'site', 'caddy-data'}:
            raise ValueError('В архиве есть посторонний файл.')
    return c


def apply_snapshot(stack: Stack, staging: Path, c: Config):
    if (stack.root / 'compose.yaml').exists():
        stack.compose('stop')
    save(stack.root, c)
    render(stack.root, c)
    if (staging / 'site').is_dir():
        shutil.copytree(staging / 'site', stack.root / 'site', dirs_exist_ok=True)
        for page in (stack.root / 'site').rglob('*'):
            os.chmod(page, 0o755 if page.is_dir() else 0o644)
    stack.compose('config', '--quiet')
    if c.has_panel:
        stack.compose('up', '-d', '--wait', '--wait-timeout', '120', 'db', 'redis', timeout=180)
        with (staging / 'database.dump').open('rb') as source:
            proc = subprocess.run(compose_args(stack, 'exec', '-T', 'db', 'pg_restore', '-U', 'postgres',
                                              '--clean', '--if-exists', '--no-owner', '--no-privileges',
                                              '--exit-on-error', '-d', 'postgres'),
                                  stdin=source, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=900)
        if proc.returncode:
            raise RuntimeError('pg_restore не выполнен.')
    if c.has_caddy and (staging / 'caddy-data').is_dir():
        from .system import run
        stack.compose('create', 'caddy')
        container = stack.compose('ps', '-a', '-q', 'caddy').stdout.strip()
        run(['docker', 'cp', str(staging / 'caddy-data') + '/.', container + ':/data'], timeout=120)
    if c.has_node:
        protect_node(c)
    stack.compose('up', '-d', '--wait', '--wait-timeout', '300', timeout=360)
    if c.has_node and c.mode != 'node':
        stack.api(c).restart_node(c.resources['node'])
        stack.wait_node(c)
    stack.credentials(c)


def restore_stack(stack: Stack, archive: Path):
    # Verify the archive before stopping any service or modifying installation files.
    with tempfile.TemporaryDirectory(prefix='godov-restore-') as temp:
        staging = Path(temp)
        unpack(archive, staging)
        c = validate_snapshot(staging)
        current = load(stack.root)
        if current.install_id != c.install_id or current.mode != c.mode:
            raise ValueError('Копия принадлежит другой установке или другому режиму.')
        previous = backup_stack(stack)
        try:
            apply_snapshot(stack, staging, c)
        except Exception:
            with tempfile.TemporaryDirectory(prefix='godov-rollback-') as rollback:
                rollback_path = Path(rollback)
                unpack(previous, rollback_path)
                try:
                    apply_snapshot(stack, rollback_path, validate_snapshot(rollback_path))
                except Exception:
                    raise RuntimeError(f'Восстановление и откат не завершены. Сохранена копия: {previous}') from None
            raise RuntimeError('Восстановление не удалось; предыдущее состояние возвращено.') from None
