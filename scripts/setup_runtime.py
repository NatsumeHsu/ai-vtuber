"""Download public assets, configure hardware and check installation readiness."""
from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path
import shutil
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def project_path(value):
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def read_yaml(path):
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8-sig"))


def write_yaml(path, value):
    import yaml
    backup = path.with_suffix(path.suffix + ".setup.bak")
    if not backup.exists():
        shutil.copy2(path, backup)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")
    temporary.replace(path)


def download_public(settings, tts):
    from huggingface_hub import snapshot_download
    import nltk

    def snapshot(repo, destination, patterns=None):
        print(f"Download/resume {repo} -> {destination}", flush=True)
        snapshot_download(repo_id=repo, local_dir=str(destination), allow_patterns=patterns)

    base = ROOT / "GPT_SoVITS/pretrained_models"
    # This repository supplies the exact HuBERT/RoBERTa layout expected by GPT-SoVITS.
    snapshot("lj1995/GPT-SoVITS", base, [
        "chinese-roberta-wwm-ext-large/*", "chinese-hubert-base/*", "sv/*",
    ])
    # Custom paths can be populated from the same public base models.
    for key, folder in (("bert_base_path", "chinese-roberta-wwm-ext-large"),
                        ("cnhuhbert_base_path", "chinese-hubert-base")):
        destination = project_path(tts[key])
        if destination.resolve() != (base / folder).resolve():
            shutil.copytree(base / folder, destination, dirs_exist_ok=True)
    g2pw = ROOT / "GPT_SoVITS/text/G2PWModel"
    if not (g2pw / "g2pW.onnx").is_file():
        archive = ROOT / "TEMP/setup/G2PWModel_1.1.zip"
        archive.parent.mkdir(parents=True, exist_ok=True)
        if not archive.is_file():
            partial = archive.with_suffix(".zip.part")
            urllib.request.urlretrieve(
                "https://www.modelscope.cn/models/kamiorinn/g2pw/resolve/master/G2PWModel_1.1.zip", partial)
            partial.replace(archive)
        unpack = ROOT / "TEMP/setup/g2pw"
        with zipfile.ZipFile(archive) as bundle:
            for entry in bundle.infolist():
                if not (unpack / entry.filename).resolve().is_relative_to(unpack.resolve()):
                    raise ValueError("Invalid path in G2PW archive")
            bundle.extractall(unpack)
        extracted = unpack / "G2PWModel_1.1"
        shutil.copytree(extracted, g2pw, dirs_exist_ok=True)
    if settings["asr"]["enabled"]:
        snapshot("FunAudioLLM/SenseVoiceSmall", project_path(settings["asr"]["model_dir"]))
    gesture = settings["live2d"].get("camera_tracking", {}).get("gestures", {})
    if gesture.get("enabled"):
        target = project_path(gesture["model_path"])
        if not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_suffix(target.suffix + ".part")
            urllib.request.urlretrieve(
                "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task",
                partial,
            )
            partial.replace(target)
    nltk_dir = ROOT / "runtime/nltk_data"
    for name in ("averaged_perceptron_tagger", "averaged_perceptron_tagger_eng", "cmudict", "punkt", "punkt_tab"):
        if not nltk.download(name, download_dir=str(nltk_dir), raise_on_error=True):
            raise RuntimeError(f"NLTK download failed: {name}")


def configure(settings, tts, device):
    import torch
    from llama_cpp import llama_supports_gpu_offload
    cuda = device != "cpu" and torch.cuda.is_available()
    if cuda:
        # Check an actual operation as well as driver visibility.
        try:
            torch.ones(1, device="cuda").add_(1).cpu()
        except RuntimeError:
            if device == "cuda":
                raise
            cuda = False
    if device == "cuda" and not cuda:
        raise RuntimeError("CUDA requested but PyTorch cannot use it. Check NVIDIA driver or use -Device cpu.")
    settings["llm"]["n_gpu_layers"] = -1 if cuda and llama_supports_gpu_offload() else 0
    settings["asr"]["device"] = "cuda:0" if cuda else "cpu"
    settings["tts"]["device"] = "cuda" if cuda else "cpu"
    settings["tts"]["acceleration"]["use_cuda_graph"] = cuda
    settings["tts"]["acceleration"]["use_flash_attention"] = False
    tts["device"] = "cuda" if cuda else "cpu"
    tts["is_half"] = cuda
    for setting, config in (("gpt_weights", "t2s_weights_path"), ("sovits_weights", "vits_weights_path")):
        if settings["tts"].get(setting):
            tts[config] = settings["tts"][setting]
    print(f"Hardware: TTS/ASR={'CUDA' if cuda else 'CPU'}, LLM GPU layers={settings['llm']['n_gpu_layers']}")


def check(settings, tts):
    missing = []

    def require(value, label):
        path = project_path(value)
        if not path.is_file() or path.stat().st_size == 0:
            missing.append(f"{label}: {path}")
        return path

    live2d = require(settings["live2d"]["model_path"], "Live2D model")
    if live2d.is_file():
        refs = json.loads(live2d.read_text(encoding="utf-8-sig")).get("FileReferences", {})
        def references(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key == "File":
                        require(live2d.parent / item, "Live2D asset")
                    elif key in ("Moc", "Physics", "Pose", "UserData", "DisplayInfo") and isinstance(item, str):
                        require(live2d.parent / item, "Live2D asset")
                    elif key == "Textures":
                        for texture in item:
                            require(live2d.parent / texture, "Live2D texture")
                    else:
                        references(item)
            elif isinstance(value, list):
                for item in value:
                    references(item)
        references(refs)
    require(settings["llm"]["model_path"], "GGUF LLM (supply your own)")
    require(settings["tts"].get("gpt_weights") or tts["t2s_weights_path"], "GPT voice weights (supply your own)")
    require(settings["tts"].get("sovits_weights") or tts["vits_weights_path"], "SoVITS voice weights (supply your own)")
    require(settings["tts"]["reference"]["ref_audio_path"], "Reference audio (set tts.reference.ref_audio_path)")
    for value in settings["tts"]["reference"].get("aux_ref_audio_paths", []):
        require(value, "Auxiliary reference audio")
    for key in ("bert_base_path", "cnhuhbert_base_path"):
        folder = project_path(tts[key])
        require(folder / "config.json", key)
        if not any(folder.glob("*.safetensors")) and not any(folder.glob("pytorch_model*.bin")):
            missing.append(f"{key} weights: {folder}")
    if settings["asr"]["enabled"]:
        for name in ("model.pt", "config.yaml"):
            require(project_path(settings["asr"]["model_dir"]) / name, "ASR")
    require("GPT_SoVITS/text/G2PWModel/g2pW.onnx", "G2PW")
    if tts.get("version") in ("v2Pro", "v2ProPlus"):
        require("GPT_SoVITS/pretrained_models/sv/pretrained_eres2netv2w24s4ep4.ckpt", "Speaker embedding model")
    camera = settings["live2d"].get("camera_tracking", {})
    if camera.get("enabled"):
        require("ffmpeg.exe", "FFmpeg")
        if camera.get("gestures", {}).get("enabled"):
            require(camera["gestures"]["model_path"], "Hand landmarker")
    if missing:
        report = ROOT / "logs/setup-missing.txt"
        report.write_text("\n".join(missing) + "\n", encoding="utf-8")
        print("\nMissing files:\n" + "\n".join(f"  - {line}" for line in missing))
        print(f"\nSupply the files and update setting.yml, then rerun setup.bat. Report: {report}")
        return False
    return True


def smoke_imports(settings):
    from GPT_SoVITS.tools.portable_runtime import activate
    activate(change_cwd=True)
    for name in ("tkinter", "pygame", "live2d", "sounddevice", "soundfile", "cv2", "mediapipe", "llama_cpp",
                 "GPT_SoVITS.TTS_infer_pack.TTS", "text.chinese2", "text.english"):
        importlib.import_module(name)
        print(f"Import OK: {name}", flush=True)
    if settings["asr"]["enabled"]:
        importlib.import_module("funasr")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--skip-models", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    os.chdir(ROOT)
    settings_path = ROOT / "setting.yml"
    settings = read_yaml(settings_path)
    tts_path = project_path(settings["tts"]["config_path"])
    config = read_yaml(tts_path)
    tts = config.get("custom") or config["v2Pro"].copy()
    if not args.check:
        if not args.skip_models:
            download_public(settings, tts)
        configure(settings, tts, args.device)
        config["custom"] = tts
        write_yaml(settings_path, settings)
        write_yaml(tts_path, config)
    ready = check(settings, tts)
    if ready:
        smoke_imports(settings)
    return 0 if ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
