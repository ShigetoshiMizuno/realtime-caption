import ctypes
import os
import subprocess
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
MODEL_BASE = SCRIPT_DIR / "models"


def _to_short_path(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    buf = ctypes.create_unicode_buffer(1024)
    r = ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, 1024)
    return buf.value if r > 0 else str(path)


def _ensure_ascii_path(path: Path) -> tuple[Path, str | None]:
    path.mkdir(parents=True, exist_ok=True)
    path_str = str(path)
    if all(ord(c) < 128 for c in path_str):
        return path, None
    short = _to_short_path(path)
    if short != path_str and all(ord(c) < 128 for c in short):
        return Path(short), None
    for letter in "RSTUVWXYZ":
        if not Path(f"{letter}:\\").exists():
            r = subprocess.run(["subst", f"{letter}:", path_str], capture_output=True)
            if r.returncode == 0:
                return Path(f"{letter}:\\"), letter
    return path, None


def _release_subst(letter: str | None):
    if letter:
        subprocess.run(["subst", f"{letter}:", "/d"], capture_output=True)


# モデル保存先を ASCII パスに変換して環境変数に設定
_ascii_models, _subst_letter = _ensure_ascii_path(MODEL_BASE)
os.environ.setdefault("HF_HOME", str(_ascii_models / "huggingface"))
os.environ.setdefault("TORCH_HOME", str(_ascii_models / "torch"))

import yaml
import torch


def load_config() -> dict:
    with open(SCRIPT_DIR / "config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def download_whisper(model_name: str):
    marker = MODEL_BASE / "huggingface" / "hub" / f"models--Systran--faster-whisper-{model_name}"
    if marker.exists():
        print(f"[SKIP] Whisper {model_name} already exists.")
        return
    print(f"[INFO] Downloading Whisper {model_name}...")
    from faster_whisper import WhisperModel
    m = WhisperModel(model_name)
    del m
    print(f"[INFO] Whisper {model_name} done.")


def download_silero():
    hub_dir = _ascii_models / "torch" / "hub"
    marker = MODEL_BASE / "torch" / "hub" / "snakers4_silero-vad_master"
    if marker.exists():
        print("[SKIP] Silero VAD already exists.")
        return
    print("[INFO] Downloading Silero VAD...")
    torch.hub.set_dir(str(hub_dir))
    torch.hub.load("snakers4/silero-vad", "silero_vad", trust_repo=True, verbose=False)
    print("[INFO] Silero VAD done.")


if __name__ == "__main__":
    try:
        cfg = load_config()
        model_name = cfg.get("whisper", {}).get("model", "small")
        download_whisper(model_name)
        download_silero()
        print("\n[INFO] All models ready.")
    finally:
        _release_subst(_subst_letter)
