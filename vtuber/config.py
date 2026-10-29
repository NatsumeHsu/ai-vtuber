"""設定檔讀取與專案路徑。"""
from pathlib import Path


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
