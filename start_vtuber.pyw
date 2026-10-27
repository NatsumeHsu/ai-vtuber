"""無終端啟動，保留 Python 輸出與例外至日誌。"""
import os
from pathlib import Path
import runpy
import sys
import traceback


def main():
    root = Path(__file__).resolve().parent
    os.chdir(root)
    log_dir = root / 'logs'
    log_dir.mkdir(exist_ok=True)
    with (log_dir / 'vtuber.log').open('a', encoding='utf-8', buffering=1) as log:
        sys.stdout = sys.stderr = log
        try:
            runpy.run_path(str(root / 'vtuberAI.py'), run_name='__main__')
        except Exception:
            traceback.print_exc()
        finally:
            sys.stdout = sys.__stdout__
            sys.stderr = sys.__stderr__


if __name__ == '__main__':
    main()
