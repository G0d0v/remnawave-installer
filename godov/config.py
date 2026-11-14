"""Validated installation state and atomic private files."""

import ipaddress
import json
import os
import re
import secrets
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

MARKER = '.g0d0v-managed'
MODES = ('panel-node', 'panel', 'node', 'attach')


def domain(value: str) -> str:
    value = value.strip().rstrip('.').lower()
    try:
        value = value.encode('idna').decode('ascii')
    except UnicodeError as exc:
        raise ValueError('Некорректный домен.') from exc
    if len(value) > 253 or '.' not in value or any(
        not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part)
        for part in value.split('.')
    ):
        raise ValueError('Укажите домен без https://, порта, пути и wildcard.')
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return value
    raise ValueError('Нужен домен, а не IP-адрес.')


def address(value: str) -> str:
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return domain(value)


def panel_url(value: str, *, allow_local=False) -> str:
    parsed = urlsplit(value)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
        raise ValueError('URL панели не должен содержать путь, пароль или параметры.')
    local = parsed.hostname in ('127.0.0.1', 'localhost', '::1')
    if not parsed.hostname or (parsed.scheme != 'https' and not (allow_local and local and parsed.scheme == 'http')):
        raise ValueError('Для удалённой панели требуется https://.')
    _ = parsed.port  # Validate malformed ports before any request.
    return value.rstrip('/')


def image(value: str, repository: str) -> str:
    if not re.fullmatch(re.escape(repository) + r':[0-9]+(?:\.[0-9]+){1,2}(?:-[a-z0-9.-]+)?', value):
        raise ValueError(f'Нужна фиксированная версия образа {repository}, например :3.4.11.')
    return value


def write_private(path: Path, content: str, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(f'Отказ записи через symlink: {path.name}')
    fd, temp = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        os.chmod(temp, mode)
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


@dataclass
class Config:
    mode: str = 'panel-node'
    install_id: str = field(default_factory=lambda: secrets.token_hex(6))
    panel_domain: str = ''
    sub_domain: str = ''
    node_domain: str = ''
    node_address: str = ''
    panel_ip: str = ''
    email: str = ''
    selfsteal: bool = True
    reality_domain: str = 'www.microsoft.com'
    node_port: int = 2222
    vpn_port: int = 443
    country: str = 'XX'
    block_torrent: bool = True
    site_theme: str = 'studio'
    panel_url: str = ''
    panel_image: str = 'remnawave/backend:3.4.11'
    node_image: str = 'remnawave/node:3.4.6'
    caddy_image: str = 'caddy:2.11.9'
    db_password: str = field(default_factory=lambda: secrets.token_hex(24))
    app_secret: str = field(default_factory=lambda: secrets.token_hex(64))
    metrics_password: str = field(default_factory=lambda: secrets.token_hex(32))
    webhook_secret: str = field(default_factory=lambda: secrets.token_hex(32))
    admin_username: str = 'godov'
    admin_password: str = field(default_factory=lambda: 'G0d0v-' + secrets.token_urlsafe(30))
    api_token: str = ''
    bot_token: str = ''
    node_secret: str = ''
    private_key: str = ''
    public_key: str = ''
    short_id: str = field(default_factory=lambda: secrets.token_hex(8))
    resources: dict = field(default_factory=dict)
    firewall_networks: list = field(default_factory=list)
    completed: bool = False

    @property
    def has_panel(self):
        return self.mode in ('panel-node', 'panel')

    @property
    def has_node(self):
        return self.mode != 'panel'

    @property
    def has_caddy(self):
        return self.has_panel or self.selfsteal

    @property
    def resource_name(self):
        return 'G0d0v-' + self.install_id

    def validate(self):
        if self.mode not in MODES or not re.fullmatch(r'[a-f0-9]{12}', self.install_id):
            raise ValueError('Некорректный режим или ID установки.')
        if any(type(flag) is not bool for flag in (self.selfsteal, self.block_torrent, self.completed)):
            raise ValueError('Флаги конфигурации должны быть boolean.')
        if len(self.admin_password) < 24 or not re.fullmatch(r'[A-Za-z0-9_-]{3,32}', self.admin_username):
            raise ValueError('Некорректный логин или слишком короткий пароль администратора.')
        for value in (self.db_password, self.app_secret, self.metrics_password, self.webhook_secret):
            if not re.fullmatch(r'[a-f0-9]{32,128}', value):
                raise ValueError('Внутренние секреты должны содержать только hex-символы.')
        if self.node_secret and not re.fullmatch(r'[A-Za-z0-9+/=_-]{16,}', self.node_secret):
            raise ValueError('SECRET_KEY должен быть однострочным ключом, скопированным из панели.')
        for key, value in self.resources.items():
            if key not in ('profile', 'inbound', 'node', 'host', 'squad'):
                raise ValueError('Неизвестный объект в состоянии установки.')
            uuid.UUID(value)
        if self.has_panel:
            self.panel_domain = domain(self.panel_domain)
            self.sub_domain = domain(self.sub_domain)
            self.panel_url = 'https://' + self.panel_domain
        if self.has_node:
            self.node_address = address(self.node_address or self.node_domain)
            if self.selfsteal:
                self.node_domain = domain(self.node_domain)
            else:
                self.reality_domain = domain(self.reality_domain)
            if self.mode in ('attach', 'node'):
                self.panel_ip = str(ipaddress.ip_address(self.panel_ip))
        if self.mode == 'attach':
            self.panel_url = panel_url(self.panel_url)
            if not self.api_token:
                raise ValueError('Нужен API-токен существующей панели.')
        if self.mode == 'node' and not self.node_secret:
            raise ValueError('Нужен SECRET_KEY из существующей панели.')
        if self.mode == 'panel-node' and (not self.selfsteal or self.vpn_port != 443):
            raise ValueError('Совместная установка использует selfsteal и VPN-порт 443.')
        domains = ([self.panel_domain, self.sub_domain] if self.has_panel else [])
        if self.has_node and self.selfsteal:
            domains.append(self.node_domain)
        if len(set(domains)) != len(domains):
            raise ValueError('Домены панели, подписки и selfsteal должны различаться.')
        if self.has_caddy and not re.fullmatch(r'[^\s@{}"\\]+@[^\s@{}"\\]+\.[^\s@{}"\\]+', self.email):
            raise ValueError('Нужен корректный email для TLS-сертификатов.')
        ports = [self.node_port, self.vpn_port, 3000, 3001, 8443, 80]
        if any(type(p) is not int or not 1 <= p <= 65535 for p in ports) or len(set(ports)) != len(ports):
            raise ValueError('Порты должны быть различными числами от 1 до 65535.')
        if not re.fullmatch(r'[A-Z]{2}', self.country) or self.site_theme not in ('studio', 'minimal', 'status'):
            raise ValueError('Некорректная страна или тема сайта.')
        image(self.panel_image, 'remnawave/backend')
        image(self.node_image, 'remnawave/node')
        image(self.caddy_image, 'caddy')
        for secret in (self.db_password, self.app_secret, self.metrics_password, self.webhook_secret,
                       self.node_secret, self.api_token, self.bot_token, self.admin_password):
            if any(ch in secret for ch in '\r\n\x00'):
                raise ValueError('Секреты не должны содержать перевод строки или NUL.')
        for network in self.firewall_networks:
            ipaddress.ip_network(network)
        return self


def save(root: Path, config: Config):
    if root.is_symlink():
        raise ValueError('Каталог установки не должен быть symlink.')
    marker = root / MARKER
    if marker.is_symlink():
        raise ValueError('Маркер владения не должен быть symlink.')
    if root.exists() and any(root.iterdir()) and not marker.is_file():
        raise ValueError('Каталог не принадлежит G0d0v. Выберите пустой каталог.')
    if marker.exists() and marker.read_text().strip() != config.install_id:
        raise ValueError('ID каталога не совпадает с состоянием установки.')
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    write_private(marker, config.install_id + '\n')
    write_private(root / 'state.json', json.dumps(asdict(config), ensure_ascii=False, indent=2) + '\n')


def load(root: Path) -> Config:
    if root.is_symlink() or (root / MARKER).is_symlink() or (root / 'state.json').is_symlink() or not (root / MARKER).is_file():
        raise ValueError('Установка G0d0v в этом каталоге не найдена.')
    config = Config(**json.loads((root / 'state.json').read_text(encoding='utf-8')))
    if (root / MARKER).read_text().strip() != config.install_id:
        raise ValueError('Повреждено состояние установки: ID не совпадает.')
    return config
