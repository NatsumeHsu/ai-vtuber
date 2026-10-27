"""Index-TTS model hosted directly in the VTuber process."""
from pathlib import Path
import sys
import threading


class IndexTTSClient:
    def __init__(self, root, settings):
        self.root = Path(root).resolve()
        self.settings = settings
        self.lock = threading.Lock()
        self.model = None
        def absolute(value, base=self.root):
            path = Path(value)
            return (path if path.is_absolute() else base / path).resolve()
        self.engine_root = absolute(settings['root'])
        self.reference = absolute(settings['reference_audio'])
        config = absolute(settings['config_path'], self.engine_root)
        model_dir = absolute(settings['model_dir'], self.engine_root)
        for path in (self.reference, config):
            if not path.is_file():
                raise FileNotFoundError(path)
        conversion = settings.get('text_conversion', 't2s')
        self.text_converter = None
        if conversion:
            from opencc import OpenCC
            self.text_converter = OpenCC(conversion)
        from runtime_support import prepare_runtime
        prepare_runtime()
        engine_path = str(self.engine_root)
        if engine_path not in sys.path:
            sys.path.insert(0, engine_path)
        import torch
        from indextts.infer_v2_5 import IndexTTS2
        device = settings.get('device', 'cuda:0')
        if device.startswith('cuda') and not torch.cuda.is_available():
            raise RuntimeError('Index-TTS CUDA unavailable')
        self.model = IndexTTS2(
            cfg_path=str(config), model_dir=str(model_dir), device=device,
            use_bf16=bool(settings.get('use_bf16', True)) and device.startswith('cuda') and torch.cuda.is_bf16_supported(),
            use_cuda_kernel=False, use_deepspeed=False, use_qwen_emo=False)

    def synthesize(self, text, output, emo_vector=None):
        with self.lock:
            if self.model is None:
                raise RuntimeError('Index-TTS 已關閉')
            output = Path(output).resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            spoken_text = self.text_converter.convert(text) if self.text_converter else text
            self.model.infer(
                spk_audio_prompt=str(self.reference), text=spoken_text,
                output_path=str(output), emo_vector=emo_vector, verbose=False,
                **self.settings.get('generation', {}))

    def close(self):
        # Wait for active inference before releasing its model.
        with self.lock:
            self.model = None
