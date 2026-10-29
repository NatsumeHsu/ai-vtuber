"""相容的啟動入口；功能實作位於 vtuber 套件。"""
from contextlib import redirect_stderr, redirect_stdout
import os
from pathlib import Path
import sys
import traceback

from vtuber import VtuberAI

__all__ = ['VtuberAI', 'main']


def main():
    import argparse
    from vtuber.discord_host import DiscordHost
    from vtuber.config import update_settings
    import yaml
    root = Path(__file__).resolve().parent
    os.chdir(root)
    parser = argparse.ArgumentParser()
    parser.add_argument('--allow-discord-user', type=int, action='append', help='加入允許使用 Discord 指令的使用者 ID，儲存後離開')
    parser.add_argument('--clear-discord-users', action='store_true', help='清空 Discord 指令白名單，儲存後離開')
    args = parser.parse_args()
    if args.allow_discord_user or args.clear_discord_users:
        path = root / 'setting.yml'
        settings = yaml.safe_load(path.read_text(encoding='utf-8'))
        ids = [] if args.clear_discord_users else settings.get('discord', {}).get('allowed_user_ids', [])
        ids = sorted(set(int(value) for value in [*ids, *(args.allow_discord_user or [])]))
        if any(value <= 0 for value in ids):
            parser.error('Discord 使用者 ID 必須為正整數')
        update_settings(path, 'discord', {'allowed_user_ids': ids})
        print('Discord 指令白名單已更新。')
        return
    if Path(sys.executable).stem.lower() != 'pythonw':
        DiscordHost().run()
        return

    log_dir = root / 'logs'
    log_dir.mkdir(exist_ok=True)
    with (log_dir / 'vtuber.log').open('a', encoding='utf-8', buffering=1) as log:
        with redirect_stdout(log), redirect_stderr(log):
            try:
                DiscordHost().run()
            except Exception:
                traceback.print_exc()


if __name__ == '__main__':
    main()
