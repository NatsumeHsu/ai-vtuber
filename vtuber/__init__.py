"""AI VTuber 應用程式套件。"""
__all__ = ["VtuberAI"]


def __getattr__(name):
    if name == 'VtuberAI':
        from vtuber.app import VtuberAI
        globals()[name] = VtuberAI
        return VtuberAI
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
