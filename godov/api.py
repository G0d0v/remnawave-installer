"""Remnawave 3.4 API client with resumable, installation-owned resources."""

import json
import time
import urllib.error
import urllib.request

from .config import Config, panel_url
from .render import xray_profile


class APIError(RuntimeError):
    def __init__(self, message, status=0):
        super().__init__(message)
        self.status = status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class API:
    def __init__(self, url: str, token='', *, allow_local=False):
        self.url = panel_url(url, allow_local=allow_local)
        self.token = token
        self.opener = urllib.request.build_opener(NoRedirect())
        self.local = self.url.startswith(('http://127.0.0.1:', 'http://localhost:'))

    def request(self, method, path, payload=None):
        if not path.startswith('/api/') or any(ch in path for ch in '\r\n?#'):
            raise ValueError('Некорректный API endpoint.')
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json', 'User-Agent': 'G0d0v/1.0'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        if self.local:
            headers.update({'X-Forwarded-Proto': 'https', 'X-Forwarded-For': '127.0.0.1'})
        body = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(self.url + path, body, headers, method=method)
        for attempt in range(3 if method == 'GET' else 1):
            try:
                with self.opener.open(req, timeout=30) as response:
                    raw = response.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise APIError('Ответ панели слишком большой.')
                data = json.loads(raw)
                if 'response' not in data:
                    raise APIError('Неожиданный формат ответа панели.')
                return data['response']
            except urllib.error.HTTPError as exc:
                # Deliberately omit server-controlled bodies, which may echo secrets.
                status = exc.code
                exc.close()
                if method == 'GET' and status in (502, 503, 504) and attempt < 2:
                    time.sleep(attempt + 1)
                    continue
                raise APIError(f'Remnawave: HTTP {status}, {method} {path}. Проверьте доступ и права токена.', status) from None
            except (urllib.error.URLError, TimeoutError, OSError):
                if method == 'GET' and attempt < 2:
                    time.sleep(attempt + 1)
                    continue
                raise APIError(f'Нет соединения с панелью: {method} {path}. Проверьте DNS, TLS и сеть.') from None
            except (ValueError, TypeError):
                raise APIError('Панель вернула некорректный JSON. Проверьте reverse proxy.') from None

    def get(self, path):
        return self.request('GET', path)

    def post(self, path, payload):
        return self.request('POST', path, payload)

    def patch(self, path, payload):
        return self.request('PATCH', path, payload)


def scoped_token(api: API, name: str, resources: set) -> str:
    catalog = api.get('/api/tokens/scopes')
    scopes = []
    found = set()
    for group in catalog['resources']:
        if group['resource'] in resources:
            found.add(group['resource'])
            # Endpoint keys come from the server; never guess scope syntax.
            scopes.extend(endpoint['key'] for endpoint in group['endpoints'])
    if found != resources or not scopes:
        raise APIError('Каталог scopes панели несовместим с установщиком.')
    return api.post('/api/tokens', {'name': name, 'expiresInDays': 365,
                                    'scopes': sorted(set(scopes))})['token']


def bootstrap_panel(api: API, c: Config, checkpoint):
    status = api.get('/api/auth/status')
    credentials = {'username': c.admin_username, 'password': c.admin_password}
    endpoint = '/api/auth/register' if status['isRegisterAllowed'] else '/api/auth/login'
    api.token = api.post(endpoint, credentials)['accessToken']
    if not c.api_token:
        c.api_token = scoped_token(api, 'G0d0v Manager',
                                   {'nodes', 'hosts', 'config-profiles', 'internal-squads', 'keygen', 'system'})
        checkpoint()
    if not c.bot_token:
        c.bot_token = scoped_token(api, 'G0d0v ShopBot',
                                   {'users', 'hosts', 'subscriptions', 'internal-squads',
                                    'external-squads', 'hwid-user-devices'})
        checkpoint()
    api.token = c.api_token


def collection(api: API, path, key=None):
    result = api.get(path)
    result = result[key] if key else result
    if not isinstance(result, list):
        raise APIError(f'Некорректная коллекция {path}.')
    return result


def provision(api: API, c: Config, checkpoint):
    """Create only objects with this installation's unique name; never delete defaults."""
    name = c.resource_name

    def remember(kind, obj):
        c.resources[kind] = obj['uuid']
        checkpoint()
        return obj

    def find(path, field='name', key=None):
        matches = [obj for obj in collection(api, path, key) if obj.get(field) == name]
        if len(matches) > 1:
            raise APIError(f'Найдены дубликаты {path}. Устраните конфликт имён вручную.')
        return matches[0] if matches else None

    profile = find('/api/config-profiles', key='configProfiles')
    if profile is None:
        if not c.private_key:
            keypair = api.get('/api/system/tools/x25519/generate')['keypairs'][0]
            c.private_key, c.public_key = keypair['privateKey'], keypair['publicKey']
            checkpoint()
        profile = api.post('/api/config-profiles', {'name': name, 'config': xray_profile(c)})
    remember('profile', profile)
    inbounds = [item for item in profile['inbounds'] if item['tag'] == 'G0D0V_VLESS_REALITY']
    if len(inbounds) != 1:
        raise APIError('В профиле отсутствует единственный inbound G0D0V_VLESS_REALITY.')
    c.resources['inbound'] = inbounds[0]['uuid']
    checkpoint()

    if not c.node_secret:
        c.node_secret = api.get('/api/keygen')['secretKey']
        checkpoint()
    node = find('/api/nodes')
    if node is None:
        node = api.post('/api/nodes', {
            'name': name, 'address': 'host.docker.internal' if c.has_panel else c.node_address,
            'port': c.node_port, 'countryCode': c.country,
            'configProfile': {'activeConfigProfileUuid': c.resources['profile'],
                              'activeInbounds': [c.resources['inbound']]},
        })
    remember('node', node)

    host = find('/api/hosts', field='remark')
    if host is None:
        host = api.post('/api/hosts', {
            'remark': name, 'address': c.node_address, 'port': c.vpn_port,
            'sni': c.node_domain if c.selfsteal else c.reality_domain, 'fingerprint': 'chrome',
            'inbound': {'configProfileUuid': c.resources['profile'],
                        'configProfileInboundUuid': c.resources['inbound']},
        })
    remember('host', host)
    squad = find('/api/internal-squads', key='internalSquads')
    if squad is None:
        squad = api.post('/api/internal-squads', {'name': name, 'inbounds': [c.resources['inbound']]})
    remember('squad', squad)


def update_profile(api: API, c: Config):
    if not c.resources.get('profile'):
        raise APIError('Профиль ещё не зарегистрирован. Сначала завершите установку.')
    api.patch('/api/config-profiles', {'uuid': c.resources['profile'], 'config': xray_profile(c)})
    api.patch('/api/hosts', {'uuid': c.resources['host'], 'address': c.node_address, 'port': c.vpn_port,
                            'sni': c.node_domain if c.selfsteal else c.reality_domain})
