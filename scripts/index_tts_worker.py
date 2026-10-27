"""JSON-lines worker. All model output goes to stderr, protocol to stdout."""
import contextlib
import json
from pathlib import Path
import sys
import traceback

protocol = sys.stdout

def send(value):
    protocol.write(json.dumps(value, ensure_ascii=False) + '\n')
    protocol.flush()

try:
    settings = json.loads(sys.argv[1])
    sys.path.insert(0, str(Path.cwd()))
    with contextlib.redirect_stdout(sys.stderr):
        import torch
        from indextts.infer_v2_5 import IndexTTS2
        device = settings.get('device', 'cuda:0')
        if device.startswith('cuda') and not torch.cuda.is_available():
            raise RuntimeError('Index-TTS CUDA 不可用')
        model = IndexTTS2(cfg_path=settings['config_path'], model_dir=settings['model_dir'],
                          device=device, use_bf16=bool(settings.get('use_bf16', True)) and device.startswith('cuda') and torch.cuda.is_bf16_supported(),
                          use_cuda_kernel=False, use_deepspeed=False, use_qwen_emo=False)
    send({'ready': True})
    for line in sys.stdin:
        try:
            request = json.loads(line)
            with contextlib.redirect_stdout(sys.stderr):
                model.infer(spk_audio_prompt=request['reference'], text=request['text'],
                            output_path=request['output'], emo_vector=request.get('emo_vector'),
                            verbose=False, **request['generation'])
            send({'ok': True})
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            send({'error': str(exc)})
except Exception as exc:
    traceback.print_exc(file=sys.stderr)
    send({'error': str(exc)})
    sys.exit(1)
