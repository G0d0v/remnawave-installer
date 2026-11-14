"""Small dependency-free terminal interface; also used by README previews."""

import getpass
import os
import sys

from . import VERSION

WIDTH = 72


def color(text, code):
    return text if 'NO_COLOR' in os.environ else f'\033[{code}m{text}\033[0m'


def green(text):
    return color(text, '38;2;201;244;107')


def muted(text):
    return color(text, '38;2;137;148;153')


def header(subtitle='REMNAWAVE / DEPLOY & CONTROL'):
    print()
    print(muted('  ' + '─' * WIDTH))
    logo = [r'   ____   ___      _   ___       ', r'  / ___| / _ \  __| | / _ \__   __',
            r' | |  _ | | | |/ _` || | | \ \ / /', r' | |_| || |_| | (_| || |_| |\ V / ',
            r'  \____| \___/ \__,_| \___/  \_/  ']
    for index, line in enumerate(logo):
        suffix = ['', '    INFRASTRUCTURE, SIMPLIFIED', f'    {subtitle}', f'    INSTALLER {VERSION}', ''][index]
        print(green('  ' + line) + muted(suffix))
    print(muted('  ' + '─' * WIDTH))
    print()


def section(title):
    print('  ' + muted(title.upper()))


def item(number, title, description=''):
    print(f'  {green(str(number).rjust(2))}  {title}')
    if description:
        print('      ' + muted(description))


def menu(c=None, *, preview=False):
    header()
    if preview:
        print('  ' + muted('ПРЕДПРОСМОТР · команды не выполняются'))
    elif c:
        print('  ' + green('●') + f' {c.mode}  ' + muted(c.panel_domain or c.node_domain or c.node_address))
    else:
        print('  ' + muted('○ Сервер ещё не настроен'))
    print()
    section('Развёртывание')
    item(1, 'Установить Remnawave', 'Панель + нода / только панель / только нода / нода в панель')
    item(2, 'Продолжить прерванную установку')
    print()
    section('Настроить')
    item(3, 'Домены и HTTPS')
    item(4, 'Selfsteal и сайт маскировки')
    item(5, 'Подключить ShopBot')
    print()
    section('Управлять')
    item(6, 'Статус и диагностика')
    item(7, 'Логи сервисов')
    item(8, 'Резервные копии и восстановление')
    item(9, 'Обновить версии контейнеров')
    item(10, 'Доступы к панели и API')
    item(11, 'Запуск / остановка / перезапуск')
    item(12, 'BBR и защита API ноды')
    item(13, 'Удалить установку')
    print()
    item(0, 'Выход')
    print()


def modes():
    header('CHOOSE YOUR SETUP')
    section('Один сценарий — один понятный мастер')
    print()
    item(1, 'Панель + нода', 'Готовый сервер: Remnawave, Reality, selfsteal, HTTPS и подписки')
    print()
    item(2, 'Только панель', 'Панель управления и домен подписок. Ноды добавляются отдельно')
    print()
    item(3, 'Только нода · вручную', 'SECRET_KEY из панели + готовый профиль для импорта')
    print()
    item(4, 'Нода в существующую панель · автоматически', 'API-токен → профиль → нода → host → squad')
    print()
    item(0, 'Назад')
    print()


def plan(c, *, preview=False):
    header('READY TO DEPLOY')
    section('Проверьте параметры')
    print()
    values = [('Режим', c.mode), ('Панель', c.panel_domain or c.panel_url or '—'),
              ('Подписки', c.sub_domain or 'из существующей панели'),
              ('Нода', c.node_address or 'не устанавливается'),
              ('Selfsteal', c.node_domain if c.has_node and c.selfsteal else 'выключен'),
              ('Сайт', c.site_theme if c.has_node and c.selfsteal else '—'),
              ('Reality', f'VLESS / TCP / Vision / порт {c.vpn_port}' if c.has_node else '—'),
              ('API ноды', f'{c.node_port} · только для панели' if c.has_node else '—'),
              ('Панель · версия', c.panel_image if c.has_panel else '—'),
              ('Нода · версия', c.node_image if c.has_node else '—')]
    for key, value in values:
        print('  ' + muted(key.ljust(21)) + str(value))
    print()
    print('  ' + green('✓') + ' Случайные пароли и отдельный API-токен для бота')
    print('  ' + green('✓') + ' PostgreSQL и Valkey во внутренней сети Docker')
    print('  ' + green('✓') + ' Проверка DNS, портов и состояния сервисов')
    print()
    if preview:
        print('  ' + muted('ПРЕДПРОСМОТР · используются домены example.com'))
    print()


def ask(label, default='', *, secret=False):
    suffix = f' [{default}]' if default and not secret else ''
    prompt = '  ' + green('›') + f' {label}{suffix}: '
    value = getpass.getpass(prompt) if secret else input(prompt)
    return value.strip() or default


def confirm(label):
    return ask(label + ' (да/нет)', 'нет').lower() in ('да', 'д', 'yes', 'y')


def message(text, *, error=False):
    print('\n  ' + color('✕' if error else '✓', '31' if error else '38;2;201;244;107') + ' ' + text + '\n',
          file=sys.stderr if error else sys.stdout)
