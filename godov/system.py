"""Linux operations. All subprocesses receive argument arrays, never shell input."""

import ipaddress
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from .api import API, bootstrap_panel, node_ready, provision
from .config import Config, load, save, write_private
from .render import render

DEFAULT_ROOT = Path('/opt/godov-remnawave')
FIREWALL_SERVICE = 'godov-remna-firewall.service'


def run(args, *, input=None, timeout=180, check=True):
    try:
        proc = subprocess.run([str(arg) for arg in args], input=input, capture_output=True,
                              text=True, encoding='utf-8', errors='replace', timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f'Истекло время операции {args[0]}. Состояние сохранено; повторите команду.') from None
    if check and proc.returncode:
        # Tool output can contain env values. Do not copy it into errors or CI logs.
        raise RuntimeError(f'Команда {args[0]} {args[1] if len(args) > 1 else ""} завершилась с кодом {proc.returncode}. '
                           'Проверьте журнал соответствующего сервиса.')
    return proc


def linux_root():
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise RuntimeError('Операция требует Linux и root. Сначала выполните sudo -i.')


def os_release():
    values = {}
    for line in Path('/etc/os-release').read_text().splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            values[key] = value.strip('"')
    supported = {'ubuntu': {'22.04', '24.04', '26.04', '26.10'}, 'debian': {'12', '13'}}
    if values.get('VERSION_ID') not in supported.get(values.get('ID'), set()):
        raise RuntimeError('Поддерживаются Ubuntu 22.04/24.04/26.04/26.10 и Debian 12/13.')
    if platform.machine() not in ('x86_64', 'aarch64', 'arm64'):
        raise RuntimeError('Поддерживаются только amd64 и arm64.')
    return values


def install_docker():
    if shutil.which('docker'):
        run(['docker', 'info'])
        run(['docker', 'compose', 'version'])
        return
    release = os_release()
    run(['apt-get', 'update'], timeout=300)
    run(['apt-get', 'install', '-y', 'ca-certificates', 'curl', 'iptables'], timeout=300)
    distro, codename = release['ID'], release['VERSION_CODENAME']
    key = urllib.request.urlopen(f'https://download.docker.com/linux/{distro}/gpg', timeout=30).read()
    keydir = Path('/etc/apt/keyrings')
    keydir.mkdir(exist_ok=True, mode=0o755)
    write_private(keydir / 'godov-docker.asc', key.decode('ascii'), mode=0o644)
    arch = run(['dpkg', '--print-architecture']).stdout.strip()
    sources = (f'Types: deb\nURIs: https://download.docker.com/linux/{distro}\nSuites: {codename}\n'
               f'Components: stable\nArchitectures: {arch}\nSigned-By: /etc/apt/keyrings/godov-docker.asc\n')
    # Do not overwrite repositories created by the server administrator.
    target = Path('/etc/apt/sources.list.d/godov-docker.sources')
    write_private(target, sources, mode=0o644)
    run(['apt-get', 'update'], timeout=300)
    run(['apt-get', 'install', '-y', 'docker-ce', 'docker-ce-cli', 'containerd.io',
         'docker-buildx-plugin', 'docker-compose-plugin'], timeout=600)
    run(['systemctl', 'enable', '--now', 'docker'])
    run(['docker', 'info'])


class Stack:
    def __init__(self, root=DEFAULT_ROOT):
        self.root = Path(root)

    def compose(self, *args, **kwargs):
        return run(['docker', 'compose', '--project-directory', self.root,
                    '-f', self.root / 'compose.yaml', *args], **kwargs)

    def checkpoint(self, c):
        save(self.root, c)

    def check_dns(self, c):
        names = ([c.panel_domain, c.sub_domain] if c.has_panel else [])
        if c.has_node and c.selfsteal:
            names.append(c.node_domain)
        local = set()
        for interface in json.loads(run(['ip', '-j', 'address']).stdout):
            for item in interface.get('addr_info', []):
                local.add(item['local'])
        public_local = {ip for ip in local if ipaddress.ip_address(ip).is_global}
        for name in names:
            try:
                records = {item[4][0] for item in socket.getaddrinfo(name, 443, type=socket.SOCK_STREAM)}
            except socket.gaierror:
                raise RuntimeError(f'DNS не настроен: {name}. Создайте A/AAAA-запись на этот сервер.') from None
            if public_local and not records.issubset(local):
                raise RuntimeError(f'DNS {name} указывает на другой IP. Проверьте A/AAAA и отключите CDN proxy.')

    def preflight(self, c):
        linux_root()
        os_release()
        c.validate()
        self.root.parent.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(self.root.parent).free < (8 if c.has_panel else 2) * 1024**3:
            raise RuntimeError('Недостаточно места: нужно хотя бы 8 ГБ для панели или 2 ГБ для ноды.')
        memory = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')
        if memory < (1800 if c.has_panel else 900) * 1024**2:
            raise RuntimeError('Недостаточно RAM: панель требует 2 ГБ, нода — 1 ГБ.')
        if not (self.root / 'compose.yaml').exists():
            ports = []
            if c.has_panel:
                ports += [3000, 3001]
            if c.has_caddy:
                ports += [80, 8443 if c.has_node else 443]
            if c.has_node:
                ports += [c.node_port, c.vpn_port]
            for port in set(ports):
                with socket.socket() as probe:
                    try:
                        probe.bind(('0.0.0.0', port))
                    except OSError:
                        raise RuntimeError(f'Порт {port} занят. Освободите его или выберите отдельный сервер.') from None

    def api(self, c):
        return API('http://127.0.0.1:3000' if c.has_panel else c.panel_url,
                   c.api_token, allow_local=c.has_panel)

    def install(self, c: Config, *, skip_dns=False, progress=print):
        self.preflight(c)
        if not skip_dns:
            self.check_dns(c)
        self.checkpoint(c)
        progress('Устанавливаю Docker и проверяю Compose…')
        install_docker()
        if c.has_node and not shutil.which('iptables'):
            run(['apt-get', 'update'], timeout=300)
            run(['apt-get', 'install', '-y', 'iptables'], timeout=300)
        render(self.root, c)
        self.compose('config', '--quiet')
        progress('Загружаю зафиксированные версии контейнеров…')
        self.compose('pull', timeout=1200)
        if c.has_panel:
            progress('Запускаю PostgreSQL, Valkey, панель и HTTPS…')
            self.compose('up', '-d', '--wait', '--wait-timeout', '300', 'db', 'redis', 'panel', 'caddy', timeout=360)
            if not c.api_token or not c.bot_token:
                bootstrap_panel(self.api(c), c, lambda: self.checkpoint(c))
        if c.has_node:
            if c.mode != 'node':
                progress('Создаю профиль Reality, ноду, host и squad…')
                provision(self.api(c), c, lambda: self.checkpoint(c))
            elif not c.private_key:
                output = run(['docker', 'run', '--rm', '--entrypoint', '/usr/local/bin/xray', c.node_image,
                              'x25519'], timeout=60).stdout
                pairs = dict(line.split(':', 1) for line in output.splitlines() if ':' in line)
                c.private_key = pairs.get('PrivateKey', pairs.get('Private key', '')).strip()
                c.public_key = pairs.get('Password', pairs.get('PublicKey', pairs.get('Public key', ''))).strip()
                if not c.private_key or not c.public_key:
                    raise RuntimeError('Не удалось разобрать X25519. Проверьте версию Xray.')
                self.checkpoint(c)
            c.validate()
            if c.has_panel:
                info = self.compose('ps', '-q', 'panel').stdout.strip()
                networks = json.loads(run(['docker', 'inspect', info, '--format',
                                          '{{json .NetworkSettings.Networks}}']).stdout)
                cidrs = []
                for name in networks:
                    data = json.loads(run(['docker', 'network', 'inspect', name, '--format', '{{json .IPAM.Config}}']).stdout)
                    cidrs.extend(item['Subnet'] for item in data if 'Subnet' in item)
                c.firewall_networks = cidrs
            self.checkpoint(c)
            progress('Ограничиваю API ноды IP-адресом панели…')
            protect_node(c)
            install_firewall_service(self.root)
        render(self.root, c)
        self.compose('config', '--quiet')
        progress('Запускаю все сервисы…')
        self.compose('up', '-d', '--wait', '--wait-timeout', '300', timeout=360)
        if c.has_node and c.mode != 'node':
            self.api(c).restart_node(c.resources['node'])
            progress('Проверяю связь панели с нодой…')
            self.wait_node(c)
        c.completed = True
        self.checkpoint(c)
        self.credentials(c)
        progress('Готово. Доступы сохранены в ' + str(self.root / 'credentials.txt'))
        if c.mode == 'node':
            progress('Ручной режим: импортируйте xray-profile.json в панель и назначьте его ноде.')

    def wait_node(self, c, timeout=120):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            node = self.api(c).get(f'/api/nodes/{c.resources["node"]}')
            if node_ready(node):
                return
            time.sleep(3)
        raise RuntimeError('Нода не подтвердила запуск Xray. Проверьте IP панели, firewall и godov-remna logs node; затем resume.')

    def credentials(self, c):
        text = f'G0d0v Remnawave · {c.install_id}\n'
        if c.has_panel:
            text += f'Панель: {c.panel_url}\nЛогин: {c.admin_username}\nПароль: {c.admin_password}\n'
            text += f'Подписки: https://{c.sub_domain}/api/sub/SHORT_UUID\n'
        if c.api_token:
            text += f'API token: {c.api_token}\n'
        if c.bot_token:
            text += f'ShopBot token: {c.bot_token}\n'
        if c.resources.get('squad'):
            text += f'Squad UUID: {c.resources["squad"]}\n'
        text += 'API-токены новой панели действуют 365 дней. Перед истечением перевыпустите их в панели.\n'
        write_private(self.root / 'credentials.txt', text)


def firewall_rules(c, family):
    allowed = [c.panel_ip] if c.panel_ip else []
    allowed += c.firewall_networks
    chain = 'G0D0V_NODE'
    lines = ['*filter', f':{chain} - [0:0]', f'-F {chain}', f'-A {chain} -i lo -j ACCEPT']
    for value in allowed:
        net = ipaddress.ip_network(value, strict=False)
        if net.version == family:
            lines.append(f'-A {chain} -s {net} -j ACCEPT')
    lines += [f'-A {chain} -j DROP', 'COMMIT', '']
    return '\n'.join(lines)


def protect_node(c):
    for family, binary in ((4, 'iptables'), (6, 'ip6tables')):
        if family == 6 and (not Path('/proc/net/if_inet6').exists() or not Path('/proc/net/if_inet6').read_text().strip()):
            continue
        if not shutil.which(binary):
            raise RuntimeError(f'Не найден {binary}. Нода не запущена без защиты API.')
        run([binary + '-restore', '--noflush'], input=firewall_rules(c, family))
        rule = ['INPUT', '-p', 'tcp', '--dport', str(c.node_port), '-j', 'G0D0V_NODE']
        if run([binary, '-C', *rule], check=False).returncode:
            run([binary, '-I', 'INPUT', '1', *rule[1:]])


def remove_firewall(c):
    for binary in ('iptables', 'ip6tables'):
        if not shutil.which(binary):
            continue
        rule = ['INPUT', '-p', 'tcp', '--dport', str(c.node_port), '-j', 'G0D0V_NODE']
        if run([binary, '-C', *rule], check=False).returncode == 0:
            run([binary, '-D', *rule])
        run([binary, '-F', 'G0D0V_NODE'], check=False)
        run([binary, '-X', 'G0D0V_NODE'], check=False)
    run(['systemctl', 'disable', FIREWALL_SERVICE], check=False)
    Path('/etc/systemd/system', FIREWALL_SERVICE).unlink(missing_ok=True)
    run(['systemctl', 'daemon-reload'])


def install_firewall_service(root):
    import shlex
    manager = Path(__file__).resolve().parents[1] / 'manage.py'
    command = ' '.join(shlex.quote(str(arg)) for arg in (sys.executable, manager, '--root', root, 'firewall'))
    unit = ('[Unit]\nDescription=G0d0v Remnawave node API access control\n'
            'DefaultDependencies=no\nAfter=local-fs.target\nBefore=docker.service\n'
            '[Service]\nType=oneshot\nRemainAfterExit=yes\n'
            f'ExecStart={command}\n[Install]\nWantedBy=multi-user.target\n')
    write_private(Path('/etc/systemd/system', FIREWALL_SERVICE), unit, mode=0o644)
    run(['systemctl', 'daemon-reload'])
    run(['systemctl', 'enable', FIREWALL_SERVICE])
