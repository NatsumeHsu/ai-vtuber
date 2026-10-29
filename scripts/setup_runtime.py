"""Validate the shared Python environment and required local assets."""
import importlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def main():
    from vtuber.runtime_support import prepare_runtime
    prepare_runtime()
    import yaml
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--device',choices=('auto','cpu','cuda'),default='auto')
    args=parser.parse_args()
    assert sys.version_info[:2] == (3, 11), sys.version
    settings_path = ROOT / 'setting.yml'
    settings = yaml.safe_load(settings_path.read_text(encoding='utf-8-sig'))
    if args.device != 'auto':
        cuda=args.device=='cuda'
        settings['llm']['n_gpu_layers']=-1 if cuda else 0
        settings['asr']['device']='cuda:0' if cuda else 'cpu'
        settings['tts']['device']='cuda:0' if cuda else 'cpu'
        settings['tts']['use_bf16']=cuda
        import shutil
        shutil.copy2(settings_path, settings_path.with_suffix(settings_path.suffix + '.device.bak'))
        settings_path.write_text(yaml.safe_dump(settings,allow_unicode=True,sort_keys=False),encoding='utf-8')
    for module in ('numpy', 'cv2', 'mediapipe', 'live2d', 'pygame', 'OpenGL',
                   'cv2_enumerate_cameras', 'sounddevice', 'soundfile', 'opencc', 'funasr', 'llama_cpp', 'torch', 'torchaudio'):
        importlib.import_module(module)
        print('Import OK:', module, flush=True)
    import torch
    from llama_cpp import llama_supports_gpu_offload
    if settings['asr']['device'].startswith('cuda'):
        assert torch.cuda.is_available(), 'ASR requires CUDA'
        assert torch.ones(1, device='cuda').add(1).item() == 2
    if settings['llm']['n_gpu_layers'] != 0:
        assert llama_supports_gpu_offload(), 'llama-cpp GPU unavailable'
    def require(value):
        p = Path(value)
        p = p if p.is_absolute() else ROOT / p
        assert p.is_file() and p.stat().st_size, f'Missing asset: {p}'
    require(settings['llm']['model_path'])
    require(settings['live2d']['model_path'])
    if settings['asr']['enabled'] or settings.get('discord', {}).get('enabled', False):
        require(str(Path(settings['asr']['model_dir']) / 'model.pt'))
        require(str(Path(settings['asr']['model_dir']) / 'config.yaml'))
    if settings.get('discord', {}).get('enabled', False):
        from discord.ext.voice_recv.opus import PacketDecoder
        assert hasattr(PacketDecoder, '_dave_decrypt'), 'Discord receiver requires the pinned DAVE-compatible build'
        from vtuber.discord_voice import DiscordVoiceService
        DiscordVoiceService(settings['discord'], None, None, None)
        print('Discord voice dependencies and configuration OK', flush=True)
    gesture = settings['live2d'].get('camera_tracking', {}).get('gestures', {})
    if gesture.get('enabled'):
        require(gesture['model_path'])
    require(settings['tts']['reference_audio'])
    require('ffmpeg.exe')
    require('ffprobe.exe')
    engine = ROOT / settings['tts']['root']
    require(str(engine / settings['tts']['config_path']))
    sys.path.insert(0, str(engine))
    from indextts.infer_v2_5 import IndexTTS2
    if settings['tts']['device'].startswith('cuda'):
        assert torch.ones(1, device='cuda').add(1).item() == 2
    print('Index-TTS imports and device OK', flush=True)
    print('Runtime checks passed', flush=True)

if __name__ == '__main__':
    main()
