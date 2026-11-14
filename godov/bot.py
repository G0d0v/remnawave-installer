"""Add one Remnawave host to the existing ShopBot, with SQLite backup."""

import inspect
import json
import re
import uuid
from pathlib import Path

from .system import run
from .config import Config, domain, panel_url, write_private


def configure_database(db_path, payload):
    """Runs locally or inside the bot container; no third-party imports."""
    import os
    import sqlite3
    import time
    from contextlib import closing
    from pathlib import Path

    db_path = Path(db_path)
    if not db_path.is_file():
        raise ValueError('База бота не найдена; новая база не создаётся.')
    with closing(sqlite3.connect(str(db_path), timeout=30)) as con:
        columns = {row[1] for row in con.execute('PRAGMA table_info(xui_hosts)')}
        required = {'host_name', 'host_url', 'host_username', 'host_pass', 'host_inbound_id',
                    'remnawave_base_url', 'remnawave_api_token', 'squad_uuid', 'subscription_url'}
        if not required.issubset(columns):
            raise ValueError('Схема ShopBot несовместима. Требуется версия с Remnawave-настройками хоста.')
        existing = con.execute('SELECT remnawave_base_url,squad_uuid FROM xui_hosts WHERE host_name=?',
                               (payload['name'],)).fetchone()
        if existing and existing[1] != payload['squad']:
            raise ValueError('Хост с таким именем уже имеет другую привязку. Данные не изменены.')
        folder = db_path.parent / 'backups'
        folder.mkdir(exist_ok=True)
        backup = folder / f'godov-before-bind-{time.time_ns()}.sqlite3'
        fd = os.open(str(backup), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        with closing(sqlite3.connect(str(backup))) as target:
            con.backup(target)
        con.execute('BEGIN IMMEDIATE')
        if not existing:
            con.execute('''INSERT INTO xui_hosts
                (host_name,host_url,host_username,host_pass,host_inbound_id,
                 remnawave_base_url,remnawave_api_token,squad_uuid,subscription_url)
                VALUES (?,?,\'\',\'\',0,?,?,?,?)''',
                        (payload['name'], payload['url'], payload['url'], payload['token'],
                         payload['squad'], payload.get('subscription')))
        else:
            con.execute('''UPDATE xui_hosts SET host_url=?, remnawave_base_url=?, remnawave_api_token=?,
                           subscription_url=? WHERE host_name=?''',
                        (payload['url'], payload['url'], payload['token'], payload.get('subscription'), payload['name']))
        con.commit()
    return str(backup)


def bind_bot(c, *, container='remnawave-shopbot', database=None):
    if not c.resources.get('squad') or not c.panel_url or not (c.bot_token or c.api_token):
        raise ValueError('Для привязки нужны URL панели, API-токен и UUID работающего squad.')
    panel_url(c.panel_url)
    uuid.UUID(c.resources['squad'])
    payload = {'name': c.resource_name, 'url': c.panel_url, 'token': c.bot_token or c.api_token,
               'squad': c.resources['squad'],
               'subscription': 'https://' + c.sub_domain + '/api/sub' if c.sub_domain else None}
    if database:
        return configure_database(Path(database), payload)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', container):
        raise ValueError('Некорректное имя контейнера.')
    source = ('import json,sys\n' + inspect.getsource(configure_database) + '\n'
              'from shop_bot.data_manager.database import DB_FILE\n'
              'print(json.dumps({"backup": configure_database(DB_FILE, json.load(sys.stdin))}))\n')
    output = run(['docker', 'exec', '-i', container, 'python', '-c', source], input=json.dumps(payload)).stdout
    try:
        return json.loads(output.strip().splitlines()[-1])['backup']
    except (ValueError, KeyError, IndexError):
        raise RuntimeError('Бот не подтвердил запись. Проверьте контейнер и совместимость схемы.') from None


def export_connection(c, path):
    if not c.resources.get('squad') or not (c.bot_token or c.api_token):
        raise ValueError('Сначала создайте ноду и внутренний squad.')
    payload = {'install_id': c.install_id, 'panel_url': c.panel_url,
               'token': c.bot_token or c.api_token, 'squad': c.resources['squad'], 'sub_domain': c.sub_domain}
    write_private(Path(path), json.dumps(payload, indent=2) + '\n')
    return Path(path)


def read_connection(path):
    values = json.loads(Path(path).read_text(encoding='utf-8'))
    if set(values) != {'install_id', 'panel_url', 'token', 'squad', 'sub_domain'}:
        raise ValueError('Некорректный файл подключения ShopBot.')
    if not re.fullmatch(r'[a-f0-9]{12}', values['install_id']):
        raise ValueError('Некорректный ID подключения ShopBot.')
    url = panel_url(values['panel_url'])
    uuid.UUID(values['squad'])
    if not isinstance(values['token'], str) or not values['token'] or any(ch in values['token'] for ch in '\r\n\x00'):
        raise ValueError('Некорректный токен в файле подключения.')
    sub = domain(values['sub_domain']) if values['sub_domain'] else ''
    return Config(mode='panel', install_id=values['install_id'], panel_url=url, bot_token=values['token'],
                  sub_domain=sub, resources={'squad': values['squad']})
