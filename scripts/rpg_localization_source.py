"""Small reproducible source adaptation for modal locale context."""
import ast


def localize_modal_bases(source):
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    changes = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            if ast.unparse(base) not in ('Modal', 'ui.Modal', 'discord.ui.Modal'):
                continue
            start = offsets[base.lineno - 1] + len(lines[base.lineno - 1].encode()[:base.col_offset].decode())
            end = offsets[base.end_lineno - 1] + len(lines[base.end_lineno - 1].encode()[:base.end_col_offset].decode())
            changes.append((start, end))
    if not changes:
        return source
    for start, end in reversed(sorted(changes)):
        source = source[:start] + 'LocalizedModal' + source[end:]
    insertion = 0
    for node in tree.body:
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)) or (isinstance(node, ast.ImportFrom) and node.module == '__future__'):
            insertion = node.end_lineno
        else:
            break
    lines = source.splitlines(keepends=True)
    lines.insert(insertion, 'from vtuber.rpg.localization import LocalizedModal\n')
    return ''.join(lines)
