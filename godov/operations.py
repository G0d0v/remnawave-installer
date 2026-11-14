"""Maintenance operations and diagnostics for an owned stack."""

import copy
import json
import os
import re
import shutil
import socket
import ssl
from pathlib import Path

from .api import node_ready, update_profile
from .backup import apply_snapshot, backup_stack, unpack, validate_snapshot
from .config import load, save, write_private
from .render import render
from .system import Stack, install_firewall_service, protect_node, remove_firewall, run


def rollback(stack, archive):
    import tempfile
    with tempfile.TemporaryDirectory(prefix='godov-rollback-') as temp:
        staging = Path(temp)
        unpack(archive, staging)
        apply_snapshot(stack, staging, validate_snapshot(staging))


def update_stack(stack: Stack, *, panel_version=None, node_version=None, caddy_version=None):
    c = load(stack.root).validate()
    for version, repository, field in ((panel_version, 'remnawave/backend', 'panel_image'),
                                       (node_version, 'remnawave/node', 'node_image'),
                                       (caddy_version, 'caddy', 'caddy_image')):
        if version:
            setattr(c, field, repository + ':' + version)
    c.validate()
    before = backup_stack(stack)
    try:
        render(stack.root, c)
        stack.compose('config', '--quiet')
        stack.compose('pull', timeout=1200)
        stack.compose('up', '-d', '--wait', '--wait-timeout', '300', timeout=360)
        if c.has_node and c.mode != 'node':
            stack.api(c).restart_node(c.resources['node'])
            stack.wait_node(c)
        save(stack.root, c)
    except Exception:
        try:
            rollback(stack, before)
        except Exception:
            raise RuntimeError(f'Обновление и откат не завершены. Резервная копия: {before}') from None
        raise RuntimeError('Обновление не удалось; база и конфигурация возвращены из резервной копии.') from None
    return before


def reconfigure(stack: Stack, c):
    old = load(stack.root).validate()
    if c.mode != old.mode or c.install_id != old.install_id:
        raise ValueError('Смена режима действующей установки не поддерживается.')
    c.validate()
    stack.check_dns(c)
    before = backup_stack(stack)
    try:
        render(stack.root, c)
        stack.compose('config', '--quiet')
        if c.has_caddy:
            stack.compose('run', '--rm', '--no-deps', 'caddy', 'validate', '--config', '/etc/caddy/Caddyfile')
        if c.has_node and c.mode != 'node':
            update_profile(stack.api(old), c)
        stack.compose('up', '-d', '--force-recreate', '--wait', '--wait-timeout', '300', timeout=360)
        if c.has_node and c.mode != 'node':
            stack.api(c).restart_node(c.resources['node'])
            stack.wait_node(c)
        save(stack.root, c)
        stack.credentials(c)
    except Exception:
        try:
            render(stack.root, old)
            if old.has_node and old.mode != 'node':
                update_profile(stack.api(old), old)
            rollback(stack, before)
        except Exception:
            raise RuntimeError(f'Изменения и откат не завершены. Копия: {before}') from None
        raise RuntimeError('Изменения не применены; предыдущее состояние восстановлено.') from None
    return before


def diagnostics(stack: Stack):
    c = load(stack.root)
    results = []
    result = stack.compose('ps', '--all', '--format', 'json', check=False)
    if result.returncode:
        results.append(('Docker Compose', False, 'не удалось прочитать состояние'))
    else:
        try:
            rows = json.loads(result.stdout) if result.stdout.lstrip().startswith('[') else [json.loads(line) for line in result.stdout.splitlines() if line]
            for row in rows:
                state, health = row.get('State', ''), row.get('Health', '')
                results.append((row['Service'], state == 'running' and health in ('', 'healthy'), f'{state} {health}'.strip()))
        except (ValueError, KeyError):
            results.append(('Docker Compose', False, 'неизвестный формат ответа'))
    for hostname in ([c.panel_domain, c.sub_domain] if c.has_panel else []) + ([c.node_domain] if c.selfsteal and c.has_node else []):
        try:
            with socket.create_connection((hostname, 443), timeout=8) as raw:
                with ssl.create_default_context().wrap_socket(raw, server_hostname=hostname) as secure:
                    certificate = secure.getpeercert()
                    results.append((hostname, True, 'TLS до ' + certificate.get('notAfter', '?')))
        except (OSError, ssl.SSLError):
            results.append((hostname, False, 'TLS недоступен: проверьте DNS, порты 80/443 и ACME'))
    if c.api_token and c.resources.get('node'):
        try:
            node = stack.api(c).get('/api/nodes/' + c.resources['node'])
            ready = node_ready(node)
            results.append(('Связь панели с нодой', ready, 'Xray запущен' if ready else 'Xray не подтверждён'))
        except RuntimeError as error:
            results.append(('Remnawave API', False, str(error)))
    results.append(('Диск', shutil.disk_usage(stack.root).free > 1024**3,
                    f'{shutil.disk_usage(stack.root).free / 1024**3:.1f} ГБ свободно'))
    return results


def backup_and_prune(stack: Stack, keep=7):
    archive = backup_stack(stack)
    candidates = sorted((stack.root / 'backups').glob('godov-*.tar.gz'), reverse=True)
    for path in candidates[keep:]:
        if path.is_file() and not path.is_symlink() and re.fullmatch(r'godov-[0-9]+\.tar\.gz', path.name):
            path.unlink()
    return archive


def schedule_backups(stack: Stack):
    import shlex
    import sys
    manager = Path(__file__).resolve().parents[1] / 'manage.py'
    command = ' '.join(shlex.quote(str(arg)) for arg in (sys.executable, manager, '--root', stack.root, 'backup', '--keep', '7'))
    unit = ('[Unit]\nDescription=G0d0v Remnawave daily backup\nAfter=docker.service\n'
            '[Service]\nType=oneshot\nUMask=0077\n' + f'ExecStart={command}\n')
    timer = ('[Unit]\nDescription=G0d0v daily backups\n[Timer]\nOnCalendar=daily\n'
             'Persistent=true\nRandomizedDelaySec=30m\n[Install]\nWantedBy=timers.target\n')
    write_private(Path('/etc/systemd/system/godov-remna-backup.service'), unit, mode=0o644)
    write_private(Path('/etc/systemd/system/godov-remna-backup.timer'), timer, mode=0o644)
    run(['systemctl', 'daemon-reload'])
    run(['systemctl', 'enable', '--now', 'godov-remna-backup.timer'])


def enable_bbr():
    run(['modprobe', 'tcp_bbr'])
    if not Path('/sys/module/tcp_bbr').exists():
        raise RuntimeError('BBR недоступен в ядре этого сервера.')
    write_private(Path('/etc/sysctl.d/90-godov-remna-bbr.conf'),
                  'net.core.default_qdisc=fq\nnet.ipv4.tcp_congestion_control=bbr\n', mode=0o644)
    run(['sysctl', '-p', '/etc/sysctl.d/90-godov-remna-bbr.conf'])


def uninstall(stack: Stack, *, purge=False):
    c = load(stack.root).validate()
    archive = backup_stack(stack)
    args = ['down'] + (['--volumes'] if purge else [])
    stack.compose(*args)
    if c.has_node:
        remove_firewall(c)
    run(['systemctl', 'disable', '--now', 'godov-remna-backup.timer'], check=False)
    if purge and c.has_panel:
        c.api_token = c.bot_token = c.node_secret = ''
        c.resources = {}
    c.completed = False
    save(stack.root, c)
    return archive
