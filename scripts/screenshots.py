"""Render captured ANSI preview output to PNG; Pillow is a docs-only dependency."""

import os
import pathlib
import re
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = pathlib.Path(__file__).resolve().parents[1]
FONT = os.environ.get('GODOV_SCREENSHOT_FONT', r'C:\Windows\Fonts\consola.ttf')
ANSI = re.compile(r'\x1b\[([0-9;]*)m')


def screenshot(preview, output):
    env = dict(os.environ)
    env.pop('NO_COLOR', None)
    env['PYTHONIOENCODING'] = 'utf-8'
    captured = subprocess.check_output([sys.executable, str(ROOT / 'manage.py'), '--preview', preview],
                                       env=env).decode('utf-8')
    lines = captured.strip('\r\n').replace('\r', '').split('\n')
    font = ImageFont.truetype(FONT, 22)
    symbols = ImageFont.truetype(r'C:\Windows\Fonts\seguisym.ttf', 22) if os.name == 'nt' else font
    mono = font.getlength('M')
    width = max(1180, int(max(len(ANSI.sub('', line)) for line in lines) * mono) + 90)
    height = 104 + len(lines) * 32
    image = Image.new('RGB', (width, height), '#0d110f')
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((14, 14, width - 14, height - 14), radius=15, fill='#161c18', outline='#303a32', width=1)
    draw.rounded_rectangle((15, 15, width - 15, 66), radius=14, fill='#202822')
    draw.rectangle((15, 49, width - 15, 66), fill='#202822')
    for x, fill in ((40, '#e8796b'), (63, '#e8c46b'), (86, '#9bc979')):
        draw.ellipse((x, 34, x + 11, 45), fill=fill)
    draw.text((120, 25), 'godov-remna · CLI preview', font=font, fill='#a2afa4')

    def segment(x, y, text, fill):
        for part in re.split(r'([✓✕●○])', text):
            is_symbol = part in ('✓', '✕', '●', '○')
            selected = symbols if is_symbol else font
            draw.text((x, y), part, font=selected, fill=fill)
            x += mono if is_symbol else font.getlength(part)
        return x

    for index, line in enumerate(lines):
        x, y, current, start = 42, 83 + index * 32, '#e7ece3', 0
        for match in ANSI.finditer(line):
            part = line[start:match.start()]
            x = segment(x, y, part, current)
            code = match.group(1)
            if code.startswith('38;2;'):
                current = tuple(int(value) for value in code.split(';')[2:5])
            elif code == '0':
                current = '#e7ece3'
            start = match.end()
        segment(x, y, line[start:], current)
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, optimize=True)
    print(output)


if __name__ == '__main__':
    for preview, filename in (('menu', 'menu'), ('install', 'modes'), ('plan', 'plan')):
        screenshot(preview, ROOT / 'docs' / 'images' / (filename + '.png'))
