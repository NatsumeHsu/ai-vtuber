"""Vendor the pinned Idleology checkout with isolated imports and stable assets.

Usage: python scripts/vendor_idleology.py PATH_TO_CHECKOUT
No upstream entry point is imported or executed.
"""
import ast
from pathlib import Path
import re
import shutil
import subprocess
import sys

try:
    from rpg_localization_source import localize_modal_bases
except ModuleNotFoundError:
    from scripts.rpg_localization_source import localize_modal_bases

COMMIT = '9cafeb412a5c6fc1548860737160249afccfc836'
ROOT = Path(__file__).resolve().parents[1]
PREFIX = 'vtuber.rpg.idleology'


def transform(text):
    text = re.sub(r'(?m)^(\s*from )(core|database|cogs)([.\s])',
                  lambda m: m[1] + PREFIX + '.' + m[2] + m[3], text)
    text = re.sub(r'(?m)^([ \t]*)import (core|database|cogs)(\.[\w.]+)?(.*)$',
                  lambda m: (m[1] + 'import ' + PREFIX + '.' + m[2] + m[3] + m[4]
                             + '\n' + m[1] + 'from ' + PREFIX + ' import ' + m[2]) if m[3]
                  else m[1] + 'from ' + PREFIX + ' import ' + m[2] + m[4], text)
    # Fix literal asset paths and paths computed relative to modules which were
    # moved within the upstream tree. Never change the process working directory.
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    changes = []
    for node in ast.walk(tree):
        replacement = None
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'join':
            values = [a.value if isinstance(a, ast.Constant) and isinstance(a.value, str) else None for a in node.args]
            if 'assets' in values and all(isinstance(v, str) for v in values[values.index('assets') + 1:]):
                replacement = f'str(asset_path({"/".join(values[values.index("assets") + 1:])!r}))'
            elif len(values) == 2 and values[1] and '/assets/' in values[1]:
                replacement = f'str(asset_path({values[1].split("/assets/", 1)[1]!r}))'
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith('assets/'):
            replacement = f'str(asset_path({node.value[7:]!r}))'
        if replacement:
            # AST column offsets are UTF-8 byte positions.
            start = offsets[node.lineno - 1] + len(lines[node.lineno - 1].encode()[:node.col_offset].decode())
            end = offsets[node.end_lineno - 1] + len(lines[node.end_lineno - 1].encode()[:node.end_col_offset].decode())
            changes.append((start, end, replacement))
    accepted = []
    for change in sorted(changes, key=lambda c: (c[0], -c[1])):
        if not accepted or change[0] >= accepted[-1][1]:
            accepted.append(change)
    for start, end, replacement in reversed(accepted):
        text = text[:start] + replacement + text[end:]
    if accepted:
        # Preserve module docstrings and future imports.
        nodes = ast.parse(text).body
        index = 0
        for node in nodes:
            if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)) or (isinstance(node, ast.ImportFrom) and node.module == '__future__'):
                index = node.end_lineno
            else:
                break
        lines = text.splitlines(keepends=True)
        lines.insert(index, f'from {PREFIX}.paths import asset_path\n')
        text = ''.join(lines)
    # Explicit managed transactions plus autocommit outside a transaction avoid
    # accidentally enrolling another task's pending write in a command rollback.
    text = text.replace('conn._tx_owner = asyncio.current_task()\n            try:\n                yield',
                        'conn._tx_owner = asyncio.current_task()\n            try:\n                await conn._real.execute("BEGIN IMMEDIATE")\n                yield')
    text = text.replace('AlchemyRepository._passive_duration_col_added', 'self._passive_duration_col_added')
    return localize_modal_bases(text)


def main(source):
    source = Path(source).resolve()
    revision = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    if revision != COMMIT:
        raise ValueError(f'Expected upstream commit {COMMIT}, got {revision}')
    target = ROOT / 'vtuber/rpg/idleology'
    for folder in ('core', 'database', 'cogs', 'assets'):
        for path in (source / folder).rglob('*'):
            if not path.is_file() or '__pycache__' in path.parts:
                continue
            destination = target / path.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if path.suffix == '.py':
                destination.write_text(transform(path.read_text(encoding='utf-8')), encoding='utf-8')
            else:
                shutil.copy2(path, destination)
    for folder in [target, *(p for p in target.rglob('*') if p.is_dir() and 'assets' not in p.parts)]:
        init = folder / '__init__.py'
        if not init.exists():
            init.write_text('', encoding='utf-8')
    shutil.copy2(source / 'README.md', target / 'UPSTREAM_README.md')
    # Preserve upstream DB initialization (including its migrations) as a method,
    # without importing bot.py, which starts a second bot at module scope.
    bot_source = (source / 'bot.py').read_text(encoding='utf-8')
    bot_class = next(n for n in ast.parse(bot_source).body if isinstance(n, ast.ClassDef) and n.name == 'DiscordBot')
    method = next(n for n in bot_class.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'init_db')
    method_source = '\n'.join(bot_source.splitlines()[method.lineno - 1:method.end_lineno])
    method_source = re.sub(r'f"\{os.path.realpath\(os.path.dirname\(__file__\)\)\}/database/database.db"', 'str(self.path)', method_source)
    method_source = re.sub(r'f"\{os.path.realpath\(os.path.dirname\(__file__\)\)\}/database/schema.sql"', 'str(Path(__file__).parent / "database/schema.sql")', method_source)
    method_source = method_source.replace(') as file:', ', encoding="utf-8") as file:')
    (target / 'bootstrap.py').write_text('from pathlib import Path\nimport aiosqlite\n\nclass Bootstrap:\n' + method_source + '\n', encoding='utf-8')


if __name__ == '__main__':
    main(sys.argv[1])
