"""設定檔讀取與專案路徑。"""
from pathlib import Path
import threading

_SETTINGS_LOCK = threading.Lock()


def update_settings(path, section, values):
    """Update selected top-level section keys without rewriting unrelated settings."""
    import re
    import yaml
    path = Path(path)
    with _SETTINGS_LOCK:
        text = path.read_text(encoding='utf-8')
        lines = text.splitlines(keepends=True)
        start = next(i for i, line in enumerate(lines) if line.startswith(section + ':'))
        end = next((i for i in range(start + 1, len(lines))
                    if re.match(r'^[A-Za-z_][\w-]*:', lines[i])), len(lines))
        for key, value in values.items():
            scalar = yaml.safe_dump(value, allow_unicode=True, default_flow_style=True).strip()
            scalar = scalar.removesuffix('\n...')
            replacement = f'  {key}: {scalar}\n'
            index = next((i for i in range(start + 1, end)
                          if re.match(r'^  ' + re.escape(key) + r':', lines[i])), None)
            if index is None:
                lines.insert(start + 1, replacement)
                end += 1
            else:
                lines[index] = replacement
        result = ''.join(lines)
        yaml.safe_load(result)
        temporary = path.with_suffix(path.suffix + '.tmp')
        temporary.write_text(result, encoding='utf-8')
        temporary.replace(path)


class SettingsMixin:
    """設定檔讀取與專案路徑。"""

    def load_settings(self):
        import yaml
        path = self.settings_path
        try:
            settings = yaml.safe_load(path.read_text(encoding='utf-8'))
        except yaml.YAMLError as exc:
            raise ValueError(f'設定檔格式錯誤：{path}\n{exc}') from exc
        if not isinstance(settings, dict):
            raise ValueError('setting.yml 必須包含設定分類')
        for section in ('llm', 'chat', 'output', 'tts', 'asr', 'live2d'):
            if not isinstance(settings.get(section), dict):
                raise ValueError(f'setting.yml 缺少設定分類：{section}')
        return settings

    def project_path(self, path):
        path = Path(path)
        return path if path.is_absolute() else self.root / path
