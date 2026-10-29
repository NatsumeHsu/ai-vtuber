"""相容的啟動入口；功能實作位於 vtuber 套件。"""
from contextlib import redirect_stderr, redirect_stdout
import os
from pathlib import Path
import sys
import traceback

from vtuber import VtuberAI

__all__ = ['VtuberAI', 'main']


def main():
    root = Path(__file__).resolve().parent
    os.chdir(root)
    if Path(sys.executable).stem.lower() != 'pythonw':
        VtuberAI().run()
        return

    log_dir = root / 'logs'
    log_dir.mkdir(exist_ok=True)
    with (log_dir / 'vtuber.log').open('a', encoding='utf-8', buffering=1) as log:
        with redirect_stdout(log), redirect_stderr(log):
            try:
                VtuberAI().run()
            except Exception:
                traceback.print_exc()


if __name__ == '__main__':
    main()
