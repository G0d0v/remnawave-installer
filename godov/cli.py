"""Interactive wizard and scriptable maintenance CLI."""

import argparse
import copy
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

from . import VERSION, ui
from .backup import backup_stack, compose_args, restore_stack
from .bot import bind_bot, export_connection, read_connection
from .api import API
from .config import MODES, Config, load
from .operations import (backup_and_prune, diagnostics, enable_bbr, reconfigure,
                         schedule_backups, uninstall, update_stack)
from .system import DEFAULT_ROOT, Stack, linux_root, protect_node


@contextmanager
def lock():
    import fcntl
    with open('/run/lock/godov-remna.lock', 'a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Другая операция G0d0v уже выполняется. Дождитесь её завершения.') from None
        yield


def parser():
    p = argparse.ArgumentParser(description='G0d0v · установка и управление Remnawave')
    p.add_argument('--version', action='version', version='G0d0v ' + VERSION)
    p.add_argument('--root', type=Path, default=DEFAULT_ROOT, help='каталог состояния (по умолчанию /opt/godov-remnawave)')
    p.add_argument('--no-color', action='store_true', help='отключить ANSI-цвета')
    p.add_argument('--preview', choices=('menu', 'install', 'plan'), help='предпросмотр интерфейса без изменений сервера')
    commands = p.add_subparsers(dest='command')
    install = commands.add_parser('install', help='мастер установки')
    install.add_argument('--mode', choices=MODES)
    install.add_argument('--config', type=Path, help='JSON с параметрами; секреты вводите через файл, не через аргументы')
    install.add_argument('--yes', action='store_true', help='применить параметры из --config без подтверждения')
    install.add_argument('--skip-dns-check', action='store_true', help='пропустить сравнение DNS с локальными IP для NAT')
    resume = commands.add_parser('resume', help='продолжить установку после ошибки')
    resume.add_argument('--skip-dns-check', action='store_true')
    commands.add_parser('menu', help='главное меню')
    commands.add_parser('status', help='статус контейнеров')
    commands.add_parser('doctor', help='контейнеры, TLS, Xray и свободное место')
    commands.add_parser('domains', help='изменить домены и перевыпустить HTTPS')
    commands.add_parser('selfsteal', help='изменить домен и тему сайта маскировки')
    bind = commands.add_parser('bind-bot', help='добавить хост в существующий ShopBot')
    bind.add_argument('--container', default='remnawave-shopbot')
    bind.add_argument('--database', type=Path, help='users.db на этом хосте вместо контейнера')
    bind.add_argument('--connection', type=Path, help='файл export-bot для бота на другом сервере')
    commands.add_parser('export-bot', help='сохранить файл подключения для удалённого ShopBot')
    credentials = commands.add_parser('credentials', help='путь к файлу доступов')
    credentials.add_argument('--show-secrets', action='store_true', help='показать пароль и токены в терминале')
    logs = commands.add_parser('logs', help='логи выбранного сервиса')
    logs.add_argument('service', choices=('panel', 'node', 'caddy', 'db', 'redis'), nargs='?')
    logs.add_argument('--follow', '-f', action='store_true')
    backup = commands.add_parser('backup', help='согласованная копия БД, конфигов и TLS')
    backup.add_argument('--keep', type=int, help='оставить последние N копий после успешной записи')
    commands.add_parser('backup-schedule', help='ежедневная копия через systemd, хранить 7 копий')
    restore = commands.add_parser('restore', help='восстановить резервную копию этой установки')
    restore.add_argument('archive', type=Path)
    restore.add_argument('--yes', action='store_true', help='подтвердить замену текущего состояния')
    update = commands.add_parser('update', help='обновить контейнеры с резервной копией и откатом')
    for name in ('panel', 'node', 'caddy'):
        update.add_argument('--' + name + '-version', help='точный тег, например 3.4.11')
    for command in ('start', 'stop', 'restart', 'bbr', 'firewall'):
        commands.add_parser(command, help={'start': 'запустить сервисы', 'stop': 'остановить сервисы',
                                          'restart': 'перезапустить сервисы', 'bbr': 'включить BBR',
                                          'firewall': 'применить защиту API ноды'}[command])
    remove = commands.add_parser('uninstall', help='сохранить копию и удалить контейнеры')
    remove.add_argument('--purge', action='store_true', help='удалить также Docker volumes с данными')
    remove.add_argument('--yes', action='store_true')
    return p


def wizard(mode=None):
    if not mode:
        ui.modes()
        choice = ui.ask('Сценарий', '1')
        if choice == '0':
            return None
        if choice not in ('1', '2', '3', '4'):
            raise ValueError('Выберите сценарий от 1 до 4.')
        mode = MODES[int(choice) - 1]
    c = Config(mode=mode)
    ui.header('SETUP / ' + mode.upper())
    if c.has_panel:
        c.panel_domain = ui.ask('Домен панели', 'panel.example.com')
        c.sub_domain = ui.ask('Домен подписок', 'sub.example.com')
    if mode == 'attach':
        c.panel_url = ui.ask('HTTPS URL существующей панели')
        c.api_token = ui.ask('API-токен панели с правами управления нодами и профилями', secret=True)
    if c.has_node:
        if mode != 'panel-node':
            c.selfsteal = ui.confirm('Использовать свой сайт и домен для Reality selfsteal?')
        if c.selfsteal:
            c.node_domain = ui.ask('Домен selfsteal', 'node.example.com')
            c.site_theme = ui.ask('Тема сайта: studio / minimal / status', 'studio')
        else:
            c.reality_domain = ui.ask('Внешний TLS 1.3 SNI для Reality', 'www.microsoft.com')
        c.node_address = ui.ask('Публичный IP или DNS ноды для клиентов', c.node_domain)
        c.country = ui.ask('Код страны ноды: DE / NL / FI / ...', 'XX').upper()
        c.block_torrent = ui.confirm('Блокировать BitTorrent на ноде?', default=True)
        if mode in ('node', 'attach'):
            c.panel_ip = ui.ask('Исходящий IP панели для доступа к API ноды')
            c.node_port = int(ui.ask('Порт API ноды', '2222'))
        if mode == 'node':
            c.node_secret = ui.ask('SECRET_KEY из панели Remnawave', secret=True)
    if c.has_caddy:
        c.email = ui.ask('Email для сертификатов Let’s Encrypt')
    return c.validate()


def maintenance_menu(stack):
    while True:
        c = load(stack.root) if (stack.root / 'state.json').exists() else None
        ui.menu(c)
        choice = ui.ask('Выберите действие', '0')
        commands = {'1': ['install'], '2': ['resume'], '3': ['domains'], '4': ['selfsteal'],
                    '5': ['bind-bot'], '6': ['doctor'], '7': ['logs'], '9': ['update'],
                    '10': ['credentials', '--show-secrets'], '13': ['uninstall']}
        if choice == '0':
            return 0
        if choice == '8':
            ui.item(1, 'Создать копию'); ui.item(2, 'Восстановить'); ui.item(3, 'Ежедневные копии')
            backup_choice = ui.ask('Действие', '1')
            args = {'1': ['backup'], '2': ['restore', ui.ask('Полный путь к архиву')] if backup_choice == '2' else [],
                    '3': ['backup-schedule']}.get(backup_choice)
        elif choice == '11':
            action = ui.ask('start / stop / restart', 'restart')
            args = [action] if action in ('start', 'stop', 'restart') else None
        elif choice == '12':
            action = ui.ask('bbr / firewall', 'firewall')
            args = [action] if action in ('bbr', 'firewall') else None
        elif choice == '9':
            args = ['update']
            if c and c.has_panel:
                version = ui.ask('Точная версия панели (Enter — сохранить)', c.panel_image.split(':')[1])
                args += ['--panel-version', version]
            if c and c.has_node:
                version = ui.ask('Точная версия ноды (Enter — сохранить)', c.node_image.split(':')[1])
                args += ['--node-version', version]
        else:
            args = commands.get(choice)
        if args:
            main(['--root', str(stack.root), *args])
        else:
            ui.message('Неизвестный пункт.', error=True)
        ui.ask('Enter — вернуться в меню')


def edit_domains(stack, c, *, selfsteal=False):
    candidate = copy.deepcopy(c)
    ui.header('SELFSTEAL' if selfsteal else 'DOMAINS / HTTPS')
    if not selfsteal and c.has_panel:
        candidate.panel_domain = ui.ask('Домен панели', c.panel_domain)
        candidate.sub_domain = ui.ask('Домен подписок', c.sub_domain)
    if c.has_node and c.selfsteal:
        candidate.node_domain = ui.ask('Домен selfsteal', c.node_domain)
        if c.node_address == c.node_domain:
            candidate.node_address = candidate.node_domain
        candidate.site_theme = ui.ask('Тема: studio / minimal / status', c.site_theme)
        candidate.block_torrent = ui.confirm('Блокировать BitTorrent?', default=c.block_torrent)
    elif selfsteal:
        raise ValueError('Для установки без selfsteal сначала создайте новую ноду в режиме selfsteal.')
    candidate.validate()
    ui.plan(candidate)
    if ui.confirm('Применить с резервной копией?'):
        archive = reconfigure(stack, candidate)
        ui.message('Готово. Предыдущая конфигурация: ' + str(archive))
        if candidate.panel_url != c.panel_url:
            ui.message('URL панели изменён. Повторите bind-bot для подключения бота к новому URL.')


def execute(args, stack):
    command = args.command
    if command == 'bind-bot' and args.connection:
        c = read_connection(args.connection)
        API(c.panel_url, c.bot_token).get('/api/internal-squads/' + c.resources['squad'])
        backup = bind_bot(c, container=args.container, database=args.database)
        ui.message('Хост добавлен в ShopBot. Копия SQLite: ' + backup)
        return 0
    if command == 'install':
        if (stack.root / 'state.json').exists():
            raise ValueError('Установка уже существует. Используйте resume или меню управления.')
        c = Config(**json.loads(args.config.read_text(encoding='utf-8'))) if args.config else wizard(args.mode)
        if c is None:
            return 0
        c.validate()
        ui.plan(c)
        if not (args.yes and args.config):
            if not sys.stdin.isatty() or not ui.confirm('Установить на этот сервер?'):
                raise ValueError('Установка отменена. Для автоматизации используйте --config FILE --yes.')
        stack.install(c, skip_dns=args.skip_dns_check, progress=lambda text: print('  ' + ui.muted(text)))
        if c.resources.get('squad') and sys.stdin.isatty() and ui.confirm('Подключить работающий на этом сервере ShopBot?'):
            container = ui.ask('Имя контейнера', 'remnawave-shopbot')
            ui.message('Хост добавлен в бота. Копия SQLite: ' + bind_bot(c, container=container))
        elif c.mode == 'panel':
            ui.message('Панель готова. Добавьте ноду, затем выполните bind-bot.')
        return 0
    c = load(stack.root).validate()
    if command == 'resume':
        stack.install(c, skip_dns=args.skip_dns_check)
    elif command in ('status', 'logs'):
        options = ['ps', '--all'] if command == 'status' else ['logs', '--tail', '100']
        if command == 'logs':
            if args.follow:
                options += ['--follow']
            if args.service:
                options += [args.service]
        return subprocess.run(compose_args(stack, *options)).returncode
    elif command == 'doctor':
        ui.header('DIAGNOSTICS')
        results = diagnostics(stack)
        for label, ok, detail in results:
            print('  ' + (ui.green('✓') if ok else ui.color('✕', '31')) + f' {label}: {detail}')
        return 0 if results and all(ok for _, ok, _ in results) else 1
    elif command == 'credentials':
        print((stack.root / 'credentials.txt').read_text(encoding='utf-8') if args.show_secrets else stack.root / 'credentials.txt')
    elif command in ('domains', 'selfsteal'):
        edit_domains(stack, c, selfsteal=command == 'selfsteal')
    elif command == 'bind-bot':
        if not c.resources.get('squad'):
            if not sys.stdin.isatty():
                raise ValueError('Укажите squad в меню bind-bot после регистрации ноды.')
            import uuid
            c.resources['squad'] = str(uuid.UUID(ui.ask('UUID внутреннего squad существующей ноды')))
            c.bot_token = ui.ask('API-токен для бота', secret=True)
            stack.api(c).get('/api/internal-squads/' + c.resources['squad'])
            stack.checkpoint(c)
        backup = bind_bot(c, container=args.container, database=args.database)
        ui.message('Хост добавлен в ShopBot. Копия SQLite: ' + backup)
    elif command == 'export-bot':
        print(export_connection(c, stack.root / 'shopbot-connection.json'))
    elif command == 'backup':
        if args.keep is not None and args.keep < 1:
            raise ValueError('--keep должен быть положительным числом.')
        print(backup_and_prune(stack, args.keep) if args.keep else backup_stack(stack))
    elif command == 'backup-schedule':
        schedule_backups(stack)
        ui.message('Ежедневные копии включены. Хранятся последние 7 архивов.')
    elif command == 'restore':
        if not args.yes and (not sys.stdin.isatty() or not ui.confirm('Заменить БД и настройки данными из архива?')):
            raise ValueError('Восстановление отменено.')
        restore_stack(stack, args.archive)
        ui.message('Резервная копия восстановлена.')
    elif command == 'update':
        archive = update_stack(stack, panel_version=args.panel_version, node_version=args.node_version,
                               caddy_version=args.caddy_version)
        ui.message('Контейнеры обновлены. Копия до обновления: ' + str(archive))
    elif command == 'firewall':
        if c.has_node:
            protect_node(c)
    elif command == 'bbr':
        enable_bbr()
        ui.message('BBR включён.')
    elif command in ('start', 'stop', 'restart'):
        if command != 'stop' and c.has_node:
            protect_node(c)
        stack.compose(*(['up', '-d'] if command == 'start' else [command]))
    elif command == 'uninstall':
        if not args.yes and (not sys.stdin.isatty() or not ui.confirm('Удалить контейнеры' + (' и данные' if args.purge else ', сохранив данные') + '?')):
            raise ValueError('Удаление отменено.')
        ui.message('Контейнеры удалены. Резервная копия: ' + str(uninstall(stack, purge=args.purge)))
    return 0


def main(argv=None):
    args = parser().parse_args(argv)
    if args.no_color:
        os.environ['NO_COLOR'] = '1'
    if args.preview:
        if args.preview == 'menu':
            ui.menu(preview=True)
        elif args.preview == 'install':
            ui.modes()
        else:
            ui.plan(Config(panel_domain='panel.example.com', sub_domain='sub.example.com',
                           node_domain='node.example.com', node_address='node.example.com', email='admin@example.com'), preview=True)
        return 0
    try:
        linux_root()
        if not args.root.is_absolute() or args.root.is_symlink() or args.root.resolve() == Path('/'):
            raise ValueError('Нужен абсолютный путь к отдельному каталогу состояния.')
        stack = Stack(args.root)
        if args.command in (None, 'menu'):
            if not sys.stdin.isatty():
                raise ValueError('Для меню нужен терминал. Используйте install --config FILE --yes.')
            return maintenance_menu(stack)
        if args.command in ('status', 'doctor', 'logs', 'credentials'):
            return execute(args, stack)
        with lock():
            return execute(args, stack)
    except (ValueError, RuntimeError, OSError, KeyError) as error:
        ui.message(str(error), error=True)
        return 1
    except (KeyboardInterrupt, EOFError):
        print('\n  Операция прервана. Для сохранённой установки используйте godov-remna resume.\n')
        return 130
