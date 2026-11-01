"""Extract public upstream text and build an offline Traditional Chinese catalog.

Development-only translation bootstrap. Runtime never calls a translation API.
Only public, vendored source text is submitted; no settings or saves are read.
Existing translations are cached and editorial overrides always take precedence.
"""
import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import json
from pathlib import Path
import re
import string
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'vtuber/rpg'
DEST = PACKAGE / 'locales/zh_TW.json'
GLOSSARY = PACKAGE / 'locales/glossary.json'
SKIP = re.compile(r'(?:https?://|SELECT\b.+\bFROM\b|INSERT\s+INTO|UPDATE\b.+\bSET\b|DELETE\s+FROM|CREATE\s+TABLE|ALTER\s+TABLE|PRAGMA\s|ON CONFLICT|Traceback|\.py\b|__\w+__)', re.I | re.S)


def worthwhile(value):
    return (bool(re.search(r'[A-Za-z]{2}', re.sub(r'ZXV\d{3}ZXV', '', value))) and len(value) <= 8000
            and not SKIP.search(value) and not value.startswith(('from ', 'import ', '#'))
            and not re.fullmatch(r'[a-z0-9_./:\\-]+', value))


def extract():
    entries = {}
    for path in sorted((PACKAGE / 'idleology').rglob('*.py')):
        if 'database' in path.parts or path.name in ('bootstrap.py', 'paths.py', 'images.py') or path.stem in ('owner', 'admin', 'general'):
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'))
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                      if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                      and node.body and isinstance(node.body[0], ast.Expr)
                      and isinstance(node.body[0].value, ast.Constant)}
        for node in ast.walk(tree):
            value, slots = None, []
            if isinstance(node, ast.JoinedStr) and not isinstance(parents.get(node), ast.FormattedValue):
                parts = []
                for item in node.values:
                    if isinstance(item, ast.Constant):
                        parts.append(item.value)
                    else:
                        parts.append(f'ZXV{len(slots):03}ZXV')
                        slots.append(ast.unparse(item.value))
                value = ''.join(parts)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) in docstrings or isinstance(parents.get(node), ast.JoinedStr):
                    continue
                # Dictionary keys and comparisons are game IDs, not copy.
                parent = parents.get(node)
                if isinstance(parent, ast.Dict) and node in parent.keys:
                    continue
                if isinstance(parent, (ast.Compare, ast.Subscript)):
                    continue
                value = node.value
                # NPC quips and other .format() templates also need protected
                # placeholders; retain their English keys only in metadata.
                if '{' in value:
                    try:
                        formatted, fields = [], []
                        for literal, field, spec, conversion in string.Formatter().parse(value):
                            formatted.append(literal)
                            if field is not None:
                                if not re.fullmatch(r'[\w.\[\]]+', field):
                                    raise ValueError('not a format field')
                                formatted.append(f'ZXV{len(fields):03}ZXV')
                                fields.append(field)
                        if fields:
                            value, slots = ''.join(formatted), fields
                    except ValueError:
                        pass
            if value and worthwhile(value):
                entries.setdefault(value, {'source': value, 'slots': slots})
    # Assets remain unchanged. Their display names, flavor and story text join
    # the same output catalog so old saves keep their original identifiers.
    for filename, columns in (('monsters.csv', ('name', 'flavor', 'species')),
                              ('partners.csv', ('name', 'title', 'pull_message')),
                              ('profiles.csv', ('name',))):
        with (PACKAGE / 'idleology/assets' / filename).open(encoding='utf-8-sig', newline='') as stream:
            for row in csv.DictReader(stream):
                for column in columns:
                    value = row.get(column)
                    if value and re.search(r'[A-Za-z]{2}', value):
                        entries.setdefault(value, {'source': value, 'slots': []})
    for path in (PACKAGE / 'idleology/assets/items').glob('*.txt'):
        for value in path.read_text(encoding='utf-8-sig').splitlines():
            if value.strip():
                entries.setdefault(value, {'source': value, 'slots': []})
    def visit(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in ('text', 'title') and isinstance(child, str):
                    entries.setdefault(child, {'source': child, 'slots': []})
                else:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(json.loads((PACKAGE / 'idleology/assets/partners/affinity_stories.json').read_text(encoding='utf-8')))
    return entries


def translate_batch(batch, glossary):
    names = sorted(glossary, key=len, reverse=True)
    terms = re.compile(r'(?<![A-Za-z])(' + '|'.join(re.escape(k) for k in names) + r')(?![A-Za-z])', re.I)
    lookup = {k.lower(): v for k, v in glossary.items()}
    protected = {}
    def prepare(source):
        def hide(match):
            marker = f'ZXK{len(protected):04}ZXK'
            protected[marker] = match[0]
            return marker
        source = re.sub(r'<a?:\w+:\d+>|<[@#][!&]?\d+>|(?<!\w)/[a-z][\w-]*', hide, source)
        return terms.sub(lambda m: lookup[m[0].lower()], source)
    text = '\n'.join(f'ZXQ{i:05}ZXQ\n' + prepare(item['source']) for i, item in enumerate(batch))
    query = urllib.parse.urlencode({'client': 'gtx', 'sl': 'en', 'tl': 'zh-TW', 'dt': 't', 'q': text})
    request = urllib.request.Request('https://translate.googleapis.com/translate_a/single?' + query,
                                     headers={'User-Agent': 'Mozilla/5.0'})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                result = json.load(response)
            result = ''.join(part[0] for part in result[0] if part[0])
            chunks = re.split(r'ZXQ\s*(\d{5})\s*ZXQ', result)
            translated = {int(chunks[i]): chunks[i + 1].strip() for i in range(1, len(chunks) - 1, 2)}
            if len(translated) != len(batch):
                raise ValueError('Translation segment markers did not survive')
            output = []
            for i, item in enumerate(batch):
                target = re.sub(r'ZXV\s*(\d{3})\s*ZXV', lambda m: 'ZXV' + m[1] + 'ZXV', translated[i])
                target = re.sub(r'ZXK\s*(\d{1,4})\s*ZXK', lambda m: protected['ZXK' + m[1].zfill(4) + 'ZXK'], target)
                target = target.replace('您', '你').replace('點擊', '點選').replace('這將', '這會')
                expected = sorted(re.findall(r'ZXV\d{3}ZXV', item['source']))
                if sorted(re.findall(r'ZXV\d{3}ZXV', target)) != expected:
                    if len(batch) > 1:
                        try:
                            output.extend(translate_batch([item], glossary))
                        except Exception:
                            pass
                        continue
                    raise ValueError('Translation placeholders did not survive')
                output.append((item['source'], target))
            return output
        except Exception:
            if attempt == 3:
                if len(batch) > 1:
                    midpoint = len(batch) // 2
                    return translate_batch(batch[:midpoint], glossary) + translate_batch(batch[midpoint:], glossary)
                raise
            time.sleep(1 + attempt)


def main():
    DEST.parent.mkdir(parents=True, exist_ok=True)
    entries = extract()
    prior = json.loads(DEST.read_text(encoding='utf-8')) if DEST.exists() else {}
    # Repair old bootstrap translations which localized a canonical slash name.
    for source, target in list(prior.items()):
        source_commands = set(re.findall(r'(?<!\w)/[a-z][\w-]*', source))
        if source_commands and not source_commands.issubset(set(re.findall(r'(?<!\w)/[a-z][\w-]*', target))):
            del prior[source]
    glossary = json.loads(GLOSSARY.read_text(encoding='utf-8'))
    editorial = json.loads((DEST.parent / 'editorial.json').read_text(encoding='utf-8')) if (DEST.parent / 'editorial.json').exists() else {}
    pending = [item for source, item in entries.items() if source not in prior and source not in editorial]
    batches, batch, size = [], [], 0
    for item in pending:
        if batch and size + len(item['source']) > 2800:
            batches.append(batch)
            batch, size = [], 0
        batch.append(item)
        size += len(item['source']) + 20
    if batch:
        batches.append(batch)
    print(f'Extracted {len(entries)} entries; cached {len(prior)}; {len(batches)} batches', flush=True)
    failed = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(translate_batch, batch, glossary): batch for batch in batches}
        for count, future in enumerate(as_completed(futures), 1):
            try:
                for source, target in future.result():
                    prior[source] = target
            except Exception as exc:
                failed.extend(futures[future])
                print(f'Batch failed: {type(exc).__name__}: {exc}', flush=True)
            if count % 5 == 0 or count == len(futures):
                DEST.write_text(json.dumps(prior, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                print(f'Completed {count}/{len(batches)} batches; {len(prior)} translations', flush=True)
    (DEST.parent / 'templates.json').write_text(json.dumps(list(entries.values()), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    editorial = json.loads((DEST.parent / 'editorial.json').read_text(encoding='utf-8')) if (DEST.parent / 'editorial.json').exists() else {}
    failed = [item for source, item in entries.items() if source not in prior and source not in editorial]
    print(f'Untranslated: {len(failed)}', flush=True)
    (DEST.parent / 'untranslated.json').write_text(json.dumps(failed, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if failed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
