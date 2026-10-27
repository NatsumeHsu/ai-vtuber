"""Register native CUDA libraries from the active environment on Windows."""
import os
from pathlib import Path
import sysconfig

_DLL_HANDLES=[]
_READY=False

def prepare_runtime():
    global _READY
    if _READY:
        return
    _READY=True
    root=Path(__file__).resolve().parent
    directories=[root]
    if os.name=='nt':
        site=Path(sysconfig.get_path('purelib'))
        directories += [site/'torch/lib', *sorted((site/'nvidia').glob('*/bin')),
                        *sorted((site/'nvidia').glob('*/bin/x86_64'))]
        for directory in directories:
            if directory.is_dir():
                _DLL_HANDLES.append(os.add_dll_directory(str(directory)))
    os.environ['PATH']=os.pathsep.join(str(p) for p in directories if p.is_dir())+os.pathsep+os.environ.get('PATH','')
