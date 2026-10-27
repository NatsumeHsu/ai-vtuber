"""Defer an unused legacy DAC import for the shared MediaPipe environment.

IndexTTS 2.5 checkpoints disable vector_quantize. Eagerly importing its legacy
DAC requires audiotools, whose protobuf<3.20 conflicts with MediaPipe>=4.25.3.
The optional quantizer remains available when its own dependencies are installed.
"""
from pathlib import Path


def prepare(root):
    path = Path(root) / 'index-tts/indextts/s2mel/modules/length_regulator.py'
    source = path.read_text(encoding='utf-8')
    eager = 'from indextts.s2mel.dac.nn.quantize import VectorQuantize\n'
    target = '            if vector_quantize:\n'
    lazy = target + '                ' + eager
    if lazy in source:
        return
    if not source.startswith(eager) and '\n' + eager not in source:
        raise RuntimeError('Unexpected IndexTTS quantizer source; review compatibility patch')
    if source.count(target) != 1:
        raise RuntimeError('Unexpected IndexTTS quantizer branch')
    source = source.replace(eager, '').replace(target, lazy)
    path.write_text(source, encoding='utf-8')


if __name__ == '__main__':
    prepare(Path(__file__).resolve().parents[1])
