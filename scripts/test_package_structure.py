"""Check package boundaries without loading models or opening devices."""
import ast
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class PackageStructureTests(unittest.TestCase):
    def test_internal_imports_resolve(self):
        for path in [*(ROOT / 'vtuber').rglob('*.py'), *(ROOT / 'database').rglob('*.py')]:
            tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(('vtuber.', 'database.')):
                    with self.subTest(path=path, module=node.module):
                        self.assertIsNotNone(importlib.util.find_spec(node.module))

    def test_service_imports_do_not_load_desktop_application(self):
        result = subprocess.run([sys.executable, '-c',
            "import sys; import vtuber; "
            "import vtuber.runtime.ipc; "
            "import vtuber.twitch.twitch_chat; "
            "assert 'vtuber.app' not in sys.modules; "
            "assert 'numpy' not in sys.modules; "
            "assert 'torch' not in sys.modules"],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_shared_root_is_independent_of_working_directory(self):
        from vtuber.core.paths import PROJECT_ROOT
        self.assertEqual(PROJECT_ROOT, ROOT)
        result = subprocess.run([sys.executable, '-c',
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "from vtuber.core.paths import PROJECT_ROOT; "
            "assert str(PROJECT_ROOT) == sys.argv[1]", str(ROOT)],
            cwd=ROOT.parent, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
