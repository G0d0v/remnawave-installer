"""Pure renderers: Compose JSON (valid YAML), Caddy, Xray and decoy sites."""

import html
import json
from pathlib import Path

from .config import Config, write_private


def compose(c: Config) -> dict:
    logging = {'driver': 'json-file', 'options': {'max-size': '20m', 'max-file': '3'}}
    services = {}
    if c.has_panel:
        services.update({
            'panel': {
                'image': c.panel_image, 'restart': 'unless-stopped', 'env_file': ['panel.env'],
                'ports': ['127.0.0.1:3000:3000', '127.0.0.1:3001:3001'],
                'extra_hosts': ['host.docker.internal:host-gateway'],
                'volumes': ['valkey-socket:/var/run/valkey'],
                'depends_on': {'db': {'condition': 'service_healthy'}, 'redis': {'condition': 'service_healthy'}},
                'healthcheck': {'test': ['CMD', 'curl', '-f', 'http://localhost:3001/health'],
                                'interval': '10s', 'timeout': '5s', 'retries': 15, 'start_period': '60s'},
                'ulimits': {'nofile': {'soft': 1048576, 'hard': 1048576}},
            },
            'db': {
                'image': 'postgres:18.4', 'restart': 'unless-stopped', 'env_file': ['panel.env'],
                'volumes': ['postgres:/var/lib/postgresql'], 'shm_size': '256mb',
                'healthcheck': {'test': ['CMD-SHELL', 'pg_isready -U postgres -d postgres'],
                                'interval': '3s', 'timeout': '5s', 'retries': 20},
            },
            'redis': {
                'image': 'valkey/valkey:9.0.0-alpine', 'restart': 'unless-stopped',
                'volumes': ['valkey-socket:/var/run/valkey'],
                'command': ['valkey-server', '--save', '', '--appendonly', 'no', '--port', '0',
                            '--unixsocket', '/var/run/valkey/valkey.sock', '--unixsocketperm', '777',
                            '--maxmemory-policy', 'noeviction', '--loglevel', 'warning'],
                'healthcheck': {'test': ['CMD', 'valkey-cli', '-s', '/var/run/valkey/valkey.sock', 'ping'],
                                'interval': '3s', 'timeout': '3s', 'retries': 20},
            },
        })
    if c.has_node:
        services['node'] = {'image': c.node_image, 'restart': 'unless-stopped', 'network_mode': 'host',
                            'env_file': ['node.env'],
                            'healthcheck': {'test': ['CMD', 'node', '-e',
                                f"const s=require('net').connect({c.node_port},'127.0.0.1');"
                                "s.once('connect',()=>{s.end();process.exit(0)});"
                                "s.once('error',()=>process.exit(1));setTimeout(()=>process.exit(1),2000)"],
                                'interval': '5s', 'timeout': '3s', 'retries': 30, 'start_period': '10s'},
                            'ulimits': {'nofile': {'soft': 1048576, 'hard': 1048576}}}
    if c.has_caddy:
        services['caddy'] = {
            'image': c.caddy_image, 'restart': 'unless-stopped', 'network_mode': 'host',
            'volumes': ['./Caddyfile:/etc/caddy/Caddyfile:ro', './site:/srv:ro',
                        'caddy-data:/data', 'caddy-config:/config'],
            'cap_drop': ['ALL'], 'cap_add': ['NET_BIND_SERVICE'],
            'security_opt': ['no-new-privileges:true'],
        }
    for service in services.values():
        service['logging'] = logging
        service['labels'] = {'io.godov.managed': c.install_id}
    volumes = {}
    if c.has_panel:
        volumes.update({'postgres': {}, 'valkey-socket': {}})
    if c.has_caddy:
        volumes.update({'caddy-data': {}, 'caddy-config': {}})
    result = {'name': 'g0d0v-remna', 'services': services}
    if volumes:
        result['volumes'] = volumes
    return result


def caddyfile(c: Config, *, internal_tls=False) -> str:
    loopback = c.has_node and c.selfsteal
    lines = ['{', '    admin off', f'    email {c.email}', '    auto_https disable_redirects']
    if loopback:
        lines += ['    https_port 8443', '    servers {', '        protocols h1 h2',
                  '        listener_wrappers {', '            proxy_protocol {',
                  '                allow 127.0.0.1/32 ::1/128', '                fallback_policy skip',
                  '            }', '            tls', '        }', '    }']
    lines += ['}', '']
    domains = []
    if c.has_panel:
        domains += [c.panel_domain, c.sub_domain]
    if c.has_node and c.selfsteal:
        domains += [c.node_domain]
    for hostname in domains:
        lines += [f'http://{hostname} {{', '    redir https://{host}{uri} permanent', '}', '', f'https://{hostname} {{']
        if loopback:
            lines.append('    bind 127.0.0.1')
        if internal_tls:
            lines.append('    tls internal')
        else:
            lines += ['    tls {', '        issuer acme {', '            disable_tlsalpn_challenge', '        }', '    }']
        lines += ['    encode zstd gzip', '    header {', '        X-Content-Type-Options nosniff',
                  '        Referrer-Policy strict-origin-when-cross-origin', '        -Server', '    }']
        if hostname == c.panel_domain:
            lines += ['    reverse_proxy 127.0.0.1:3000']
        elif hostname == c.sub_domain:
            lines += ['    @subscription path /api/sub/*', '    handle @subscription {',
                      '        reverse_proxy 127.0.0.1:3000', '    }', '    handle {', '        respond 404', '    }']
        else:
            lines += ['    root * /srv', '    file_server', '    header X-Frame-Options DENY']
        lines += ['}', '']
    return '\n'.join(lines)


def xray_profile(c: Config) -> dict:
    reality = {'show': False, 'target': '127.0.0.1:8443' if c.selfsteal else c.reality_domain + ':443',
               'xver': 1 if c.selfsteal else 0, 'serverNames': [c.node_domain if c.selfsteal else c.reality_domain],
               'privateKey': c.private_key, 'shortIds': [c.short_id]}
    rules = [{'type': 'field', 'ip': ['geoip:private'], 'outboundTag': 'BLOCK'}]
    if c.block_torrent:
        rules.append({'type': 'field', 'protocol': ['bittorrent'], 'outboundTag': 'BLOCK'})
    return {
        'log': {'loglevel': 'warning'},
        'inbounds': [{'tag': 'G0D0V_VLESS_REALITY', 'listen': '0.0.0.0', 'port': c.vpn_port, 'protocol': 'vless',
                      'settings': {'clients': [], 'decryption': 'none', 'flow': 'xtls-rprx-vision'},
                      'sniffing': {'enabled': True, 'destOverride': ['http', 'tls', 'quic'], 'routeOnly': True},
                      'streamSettings': {'network': 'tcp', 'security': 'reality', 'realitySettings': reality}}],
        'outbounds': [{'tag': 'DIRECT', 'protocol': 'freedom'}, {'tag': 'BLOCK', 'protocol': 'blackhole'}],
        'routing': {'domainStrategy': 'IPIfNonMatch', 'rules': rules},
    }


def site(theme: str) -> str:
    pages = {
        'studio': ('FORM / STUDIO', 'Идеи обретают форму.', 'Независимая студия цифрового дизайна.', '#d8f76c'),
        'minimal': ('NORTH JOURNAL', 'Меньше шума. Больше смысла.', 'Заметки о дизайне, технологиях и повседневной жизни.', '#d4b79b'),
        'status': ('NETWORK STATUS', 'Все системы работают.', 'Состояние платформы и доступность сервисов.', '#8de2bc'),
    }
    brand, title, subtitle, accent = pages[theme]
    return f'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(brand)}</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#141614;color:#e9ebe5;font:18px system-ui,sans-serif}}
main{{max-width:1100px;margin:auto;padding:42px 30px}}nav{{display:flex;justify-content:space-between;border-bottom:1px solid #343834;padding-bottom:28px}}
.tag{{color:{accent};font-size:13px;letter-spacing:.16em}}h1{{font-size:clamp(46px,8vw,112px);line-height:1.05;font-weight:500;letter-spacing:-.07em;max-width:900px;margin:110px 0 32px}}
p{{color:#9ba396;max-width:500px;line-height:1.7}}.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:24px;margin:100px 0 60px}}
article{{border-top:1px solid #343834;padding-top:20px}}article span{{color:{accent}}}footer{{border-top:1px solid #343834;padding-top:24px;color:#9ba396;font-size:13px}}
@media(max-width:600px){{.grid{{grid-template-columns:1fr}}h1{{margin-top:70px}}}}</style></head>
<body><main><nav><b>{brand}</b><span class="tag">INDEPENDENT / ONLINE</span></nav>
<h1>{title}</h1><p>{subtitle}</p><section class="grid">
<article><span>01 / Подход</span><p>Внимание к деталям. Понятные решения. Уважение к вашему времени.</p></article>
<article><span>02 / Практика</span><p>Исследуем, создаём и делимся результатом. Каждый день.</p></article>
<article><span>03 / Контакт</span><p>Для новых идей всегда найдётся место.</p></article></section>
<footer>© {brand} · Digital craft</footer></main></body></html>'''


def render(root: Path, c: Config, *, internal_tls=False):
    write_private(root / 'compose.yaml', json.dumps(compose(c), indent=2) + '\n')
    if c.has_panel:
        env = {'APP_PORT': '3000', 'METRICS_PORT': '3001', 'API_INSTANCES': '1',
               'DATABASE_URL': f'postgresql://postgres:{c.db_password}@db:5432/postgres',
               'REDIS_SOCKET': '/var/run/valkey/valkey.sock', 'APP_SECRET': c.app_secret,
               'METRICS_USER': 'godov', 'METRICS_PASS': c.metrics_password,
               'WEBHOOK_ENABLED': 'false', 'WEBHOOK_SECRET_HEADER': c.webhook_secret,
               'IS_TELEGRAM_NOTIFICATIONS_ENABLED': 'false', 'PANEL_DOMAIN': c.panel_domain,
               'FRONT_END_DOMAIN': c.panel_domain, 'SUB_PUBLIC_DOMAIN': c.sub_domain + '/api/sub',
               'POSTGRES_USER': 'postgres', 'POSTGRES_PASSWORD': c.db_password, 'POSTGRES_DB': 'postgres'}
        write_private(root / 'panel.env', ''.join(f'{key}={value}\n' for key, value in env.items()))
    if c.has_node:
        # JSON-style quoting prevents Compose interpolation of an opaque SECRET_KEY.
        write_private(root / 'node.env', f'NODE_PORT={c.node_port}\nSECRET_KEY=\'{c.node_secret}\'\n')
        write_private(root / 'xray-profile.json', json.dumps(xray_profile(c), indent=2) + '\n')
    if c.has_caddy:
        write_private(root / 'Caddyfile', caddyfile(c, internal_tls=internal_tls))
        write_private(root / 'site' / 'index.html', site(c.site_theme), mode=0o644)
