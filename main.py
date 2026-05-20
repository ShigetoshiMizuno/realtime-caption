"""
リアルタイム字幕・翻訳システム
OBS Virtual Audio Cable から音声を取得し、Whisper で文字起こし、
OpenAI API で翻訳して、WebSocket 経由で OBS Browser Source に字幕を配信する。
"""

import ctypes
import os
import subprocess
from pathlib import Path as _Path


def _to_short_path(path: _Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    buf = ctypes.create_unicode_buffer(1024)
    r = ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, 1024)
    return buf.value if r > 0 else str(path)


def _ensure_ascii_path(path: _Path) -> tuple[_Path, str | None]:
    path.mkdir(parents=True, exist_ok=True)
    path_str = str(path)
    if all(ord(c) < 128 for c in path_str):
        return path, None
    short = _to_short_path(path)
    if short != path_str and all(ord(c) < 128 for c in short):
        return _Path(short), None
    for letter in "RSTUVWXYZ":
        if not _Path(f"{letter}:\\").exists():
            r = subprocess.run(["subst", f"{letter}:", path_str], capture_output=True)
            if r.returncode == 0:
                return _Path(f"{letter}:\\"), letter
    return path, None


def _release_subst(letter: str | None):
    if letter:
        subprocess.run(["subst", f"{letter}:", "/d"], capture_output=True)


# モデルキャッシュ先: プロジェクトディレクトリが ASCII なら ./models、
# 非 ASCII なら %LOCALAPPDATA%\rc-models にフォールバック（torch.jit の fopen 制約を回避）
# app.py から import された場合（RC_MODELS_CONFIGURED）は重複セットアップとログを回避
_already_configured = os.environ.get("RC_MODELS_CONFIGURED") == "1"
_project_models = _Path(__file__).parent.resolve() / "models"
if all(ord(c) < 128 for c in str(_project_models)):
    _MODEL_BASE = _project_models
else:
    _localappdata = os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
    _MODEL_BASE = _Path(_localappdata) / "rc-models"
    if not _already_configured:
        print(f"[main] プロジェクトパスに非ASCII文字を含むため、モデルキャッシュを {_MODEL_BASE} に配置します", flush=True)

_ascii_models, _subst_letter = _ensure_ascii_path(_MODEL_BASE)
os.environ.setdefault("HF_HOME", str(_ascii_models / "huggingface"))
os.environ.setdefault("TORCH_HOME", str(_ascii_models / "torch"))
os.environ["RC_MODELS_CONFIGURED"] = "1"

import asyncio
from enum import Enum
import io
import json
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
import numpy as np
import yaml
import pyaudiowpatch as pyaudio
import torch
import websockets
from openai import OpenAI
from RealtimeSTT import AudioToTextRecorder
from scipy.signal import resample_poly
from math import gcd
from config_utils import decode_api_key

# Windows コンソールの文字化け対策
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# RealtimeSTT シャットダウン時の既知ノイズを抑制（issue #4）
# `_recording_worker` が閉じた multiprocessing queue にアクセスして WinError 6 を吐く
import logging as _logging
_logging.getLogger("realtimestt").setLevel(_logging.CRITICAL)

_default_thread_excepthook = threading.excepthook
def _rc_thread_excepthook(args):
    # WinError 6 / ERROR_INVALID_HANDLE は停止時のレースで出るだけなので黙殺
    exc = args.exc_value
    if isinstance(exc, OSError) and getattr(exc, "winerror", None) == 6:
        return
    # その他はデフォルト動作へ
    _default_thread_excepthook(args)
threading.excepthook = _rc_thread_excepthook

# Silero VAD モデルを信頼済みリポジトリとして事前登録（初回確認をスキップ）
# このロードに失敗すると AudioToTextRecorder 初期化時に jit ファイル不在で落ちる。
_hub_dir = _ascii_models / "torch" / "hub"
torch.hub.set_dir(str(_hub_dir))
try:
    torch.hub.load('snakers4/silero-vad', 'silero_vad', trust_repo=True, verbose=False)
except Exception as e:
    print(f"[WARN] Silero VAD の事前ロードに失敗しました: {e}", flush=True)
    print(f"[WARN] オフライン環境なら初回だけオンラインで起動してください", flush=True)
    # モデルファイルが不完全な場合は再取得を試みる
    import shutil
    _silero_dir = _hub_dir / "snakers4_silero-vad_master"
    _jit_path = _silero_dir / "src" / "silero_vad" / "data" / "silero_vad.jit"
    if _silero_dir.exists() and not _jit_path.exists():
        print(f"[WARN] 不完全な Silero キャッシュを削除して再取得します: {_silero_dir}", flush=True)
        try:
            shutil.rmtree(_silero_dir)
            torch.hub.load('snakers4/silero-vad', 'silero_vad', trust_repo=True, verbose=False)
            print(f"[INFO] Silero VAD 再取得に成功しました", flush=True)
        except Exception as e2:
            print(f"[ERROR] Silero VAD 再取得にも失敗: {e2}", flush=True)

# RealtimeSTT が期待するサンプルレート
REALTIMESTT_SAMPLE_RATE = 16000
# gpt-realtime-translate が期待するサンプルレート
REALTIME_TRANSLATE_SAMPLE_RATE = 24000


def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def list_audio_devices(
    device_type: str = "input",
    host_api: str = "wasapi",
) -> list[dict]:
    """
    オーディオデバイスをリストアップする。

    Parameters
    ----------
    device_type:
        "input"  - 入力デバイス（マイク）と WASAPI ループバックデバイス（後方互換デフォルト）
        "output" - 出力デバイス（スピーカー / 仮想ケーブル）
        "all"    - 入出力すべてのデバイス
    host_api:
        "wasapi"      - WASAPI のみ表示（デフォルト。重複表示を抑制）
        "all"         - 全 Host API を表示
        "mme"         - MME のみ
        "directsound" - DirectSound のみ
    """
    pa = pyaudio.PyAudio()
    devices = []
    try:
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            host_idx = info.get("hostApi", -1)
            try:
                host_info = pa.get_host_api_info_by_index(host_idx)
            except Exception:
                continue
            host_name = host_info.get("name", "").lower()

            # Host API フィルタ
            if host_api != "all":
                if host_api == "wasapi" and "wasapi" not in host_name:
                    continue
                elif host_api == "mme" and "mme" not in host_name:
                    continue
                elif host_api == "directsound" and "directsound" not in host_name:
                    continue

            is_loopback = info.get("isLoopbackDevice", False)
            has_input = info.get("maxInputChannels", 0) > 0
            has_output = info.get("maxOutputChannels", 0) > 0

            # デバイスタイプ判定
            if device_type == "output":
                if not (has_output and not is_loopback):
                    continue
            elif device_type == "all":
                if not (has_input or has_output or is_loopback):
                    continue
            else:  # "input" (デフォルト・後方互換)
                if not (has_input or is_loopback):
                    continue

            # 名前の整形（loopback 明示）
            name = info["name"]
            if is_loopback and "[Loopback]" not in name:
                name = f"{name} [Loopback]"
            elif not is_loopback and "[Loopback]" in name:
                name = name.replace(" [Loopback]", "")

            devices.append({
                "index": i,
                "name": name,
                "isLoopback": is_loopback,
                "defaultSampleRate": info.get("defaultSampleRate", 44100),
                "maxInputChannels": info.get("maxInputChannels", 0),
                "maxOutputChannels": info.get("maxOutputChannels", 0),
                "hostApi": host_name,
            })
    finally:
        pa.terminate()
    return devices


def find_device_by_name(name_keyword: str, devices: list[dict]) -> dict | None:
    """
    デバイス名の部分一致（大小文字無視）でデバイス情報を返す。
    見つからない場合は None を返す。

    Parameters
    ----------
    name_keyword:  デバイス名の一部（"CABLE Input" 等）
    devices:       list_audio_devices() の戻り値
    """
    keyword_lower = name_keyword.lower()
    return next(
        (d for d in devices if keyword_lower in d["name"].lower()),
        None,
    )


def select_audio_device() -> dict:
    """デバイスを選択し、デバイス情報の辞書を返す。"""
    devices = list_audio_devices()
    print("\n利用可能な入力デバイス一覧:")
    for d in devices:
        name = d["name"].replace(" [Loopback]", "")  # デバイス名に含まれる重複を除去
        label = " [Loopback]" if d["isLoopback"] else ""
        print(f"  [{d['index']}] {name}{label}")
    print()
    while True:
        try:
            choice = int(input("デバイス番号を入力してください: "))
            matched = next((d for d in devices if d["index"] == choice), None)
            if matched is not None:
                return matched
            print("無効な番号です。再度入力してください。")
        except ValueError:
            print("数字を入力してください。")


def select_output_device() -> dict:
    """出力デバイスを選択し、デバイス情報の辞書を返す。"""
    devices = list_audio_devices(device_type="output")
    print("\n利用可能な出力デバイス一覧:")
    for d in devices:
        print(f"  [{d['index']}] {d['name']}")
    print()
    while True:
        try:
            choice = int(input("出力デバイス番号を入力してください: "))
            matched = next((d for d in devices if d["index"] == choice), None)
            if matched is not None:
                return matched
            print("無効な番号です。再度入力してください。")
        except ValueError:
            print("数字を入力してください。")


def select_whisper_model(default: str) -> str:
    print(f"\nWhisper モデルを選択してください（デフォルト: {default}）")
    print("  [1] small")
    print("  [2] medium")
    choice = input("選択 (Enter でデフォルト使用): ").strip()
    if choice == "1":
        return "small"
    elif choice == "2":
        return "medium"
    return default


class SubtitleBroadcaster:
    """WebSocket サーバーで接続中の全クライアントに字幕を配信する。

    threading.Lock を使用することで、複数の asyncio.run() スレッド（MultiCaptionSystem の
    route_a / route_b）から同時に broadcast() を呼び出しても安全に動作する。
    asyncio.Lock は _LoopBoundMixin を継承し初回 acquire 時にイベントループに束縛されるため、
    異なるループから呼び出すと RuntimeError が発生する（Issue #38 Critical 1 修正）。
    """

    def __init__(self):
        self._clients: set = set()
        self._lock = threading.Lock()

    async def register(self, websocket):
        with self._lock:
            self._clients.add(websocket)
        try:
            await websocket.wait_closed()
        finally:
            with self._lock:
                self._clients.discard(websocket)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def broadcast(self, message: str):
        with self._lock:
            targets = set(self._clients)

        # デバッグ: クライアント数と message の最初の 100 文字を出力（最初の 5 回だけ）
        if not hasattr(self, "_broadcast_log_count"):
            self._broadcast_log_count = 0
        if self._broadcast_log_count < 5:
            print(
                f"[Broadcaster] broadcast to {len(targets)} clients: {message[:100]}",
                flush=True,
            )
            self._broadcast_log_count += 1

        if not targets:
            return
        await asyncio.gather(
            *[ws.send(message) for ws in targets],
            return_exceptions=True,
        )


# DeepL ターゲット言語マップ。キーは config.yaml の translation.target_language の値。
# 日本語表記と英語表記（lowercase）を両方受ける。lookup 時は _normalize_deepl_lang() で
# ASCII の場合のみ .lower() に正規化し、日本語はそのまま引く（.lower() が無効なので）。
_DEEPL_LANG_MAP = {
    "日本語": "JA",
    "英語": "EN-US",
    "中国語": "ZH",
    "韓国語": "KO",
    "ドイツ語": "DE",
    "フランス語": "FR",
    "japanese": "JA",
    "english": "EN-US",
    "chinese": "ZH",
    "korean": "KO",
    "german": "DE",
    "french": "FR",
}


def _normalize_deepl_lang(target_language: str) -> str:
    """target_language を DeepL のターゲット言語コードに正規化して返す。

    ASCII 文字列は .lower() してから引く（"English" / "ENGLISH" / "english" を統一）。
    非 ASCII（日本語表記）は .lower() が無効なのでそのまま引く。
    マップになければ大文字化してそのまま返す（"EN-US" / "JA" 等の直接指定を許容）。
    """
    key = target_language.strip()
    if key.isascii():
        key = key.lower()
    return _DEEPL_LANG_MAP.get(key, target_language.strip().upper())


class TranslationService:
    """翻訳サービス。config の translation_model に応じて OpenAI または DeepL を使う。"""

    def __init__(self, config: dict):
        trans_cfg = config.get("translation", {})
        self._target_language = trans_cfg.get("target_language", "日本語")
        model = trans_cfg.get("translation_model", "openai").lower()

        if model == "deepl":
            import deepl as _deepl
            self._deepl = _deepl.Translator(decode_api_key(config["deepl"]["api_key"]))
            self._deepl_target = _normalize_deepl_lang(self._target_language)
            self._mode = "deepl"
        else:
            self._client = OpenAI(api_key=decode_api_key(config["openai"]["api_key"]))
            prompt_tmpl = trans_cfg.get("system_prompt",
                "与えられたテキストを自然な{target_language}に翻訳してください。翻訳結果のみ返してください。")
            self._system_prompt = prompt_tmpl.format(target_language=self._target_language)
            self._mode = "openai"

    def translate(self, text: str) -> str:
        if self._mode == "deepl":
            result = self._deepl.translate_text(text, target_lang=self._deepl_target)
            return result.text
        else:
            response = self._client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": self._system_prompt},
                    {"role": "user", "content": text},
                ],
                temperature=0.3,
            )
            return response.choices[0].message.content.strip()


class RouteState(Enum):
    """CaptionSystem の状態を表す列挙型。"""
    IDLE     = "idle"      # 未起動（WS 未接続、capture スレッドなし）
    STARTING = "starting"  # start() 呼び出し中（スレッド起動途中）
    RUNNING  = "running"   # 稼働中（WS 接続済み、capture スレッド稼働）
    STOPPING = "stopping"  # stop() 呼び出し中（シャットダウン処理中）
    ERROR    = "error"     # エラー状態


@dataclass(frozen=True)
class AudioStats:
    """音声処理の共有状態のスナップショット（イミュータブル）。

    capture スレッドが書き込み、GUI / RPC スレッドが読み込む。frozen=True で部分
    更新を防ぎ、threading.Lock 内で参照書き換えするため、読み手は常に整合性の
    取れたスナップショットを得る（古いか新しいかのいずれかで、混在しない）。

    PEP 703（Python 3.13 free-threaded）下でも安全な設計。
    """
    peak: int = 0           # 直近 1 秒間のピーク値（0..32767, int16 範囲）
    peak_now: int = 0       # チャンクごとのピーク値（減衰付き、リアルタイム表示用）
    chunks_per_sec: int = 0  # 1 秒あたりのチャンク数
    gain: float = 1.0       # 実際に適用された直近 gain
    mode: str = "off"       # "off" | "manual" | "auto"
    manual_gain: float = 1.0  # 手動 gain 設定値


class CaptionSystem:
    """文字起こし・翻訳・WebSocket 配信を統合管理するクラス。"""

    def __init__(self, config: dict, device_info: dict, model_name: str,
                 on_result=None, on_ready=None,
                 on_whisper_busy=None, on_trans_busy=None,
                 output_device_index: int | None = None,
                 output_volume: float = 1.0,
                 route_id: str = "a",
                 shared_broadcaster: "SubtitleBroadcaster | None" = None,
                 pa_instance: "pyaudio.PyAudio | None" = None,
                 on_realtime_error_external: "Callable[[str], None] | None" = None,
                 request_source_transcript: bool = True,
                 idle_disconnect_enabled: bool = False,
                 idle_timeout_sec: float = 300.0,
                 idle_audio_threshold: int = 200,  # W-COST-4: TBD-4-2 実機計測で 100→200 に変更
                 **deprecated_kwargs):
        # 後方互換: vad_* キーは無視。それ以外は TypeError
        _VAD_DEPRECATED_KEYS = frozenset({
            "vad_enabled", "vad_threshold", "vad_prefix_padding_ms", "vad_silence_duration_ms"
        })
        for k in deprecated_kwargs:
            if k not in _VAD_DEPRECATED_KEYS:
                raise TypeError(f"__init__() got an unexpected keyword argument '{k}'")

        self._config = config
        self._device_info = device_info
        self._model_name = model_name
        self._on_result = on_result          # callable(original: str, translated: str) | None
        self._on_ready = on_ready            # callable() | None — モデル準備完了時に呼ばれる
        self._on_whisper_busy = on_whisper_busy  # callable(bool) | None
        self._on_trans_busy = on_trans_busy      # callable(bool) | None
        self._on_realtime_error_external = on_realtime_error_external  # callable(str) | None
        # W-COST-2: 原文文字起こし（Whisper）有効フラグ。False にすると Whisper 課金を停止する。
        self._request_source_transcript: bool = request_source_transcript
        # W-COST-4: アイドル時セッション自動切断。False（デフォルト）で既存挙動維持。
        self._idle_disconnect_enabled: bool = idle_disconnect_enabled
        self._idle_timeout_sec: float = idle_timeout_sec
        self._idle_audio_threshold: int = idle_audio_threshold
        self._idle_monitor = None  # _create_realtime_translator で生成（enabled=True のみ）

        # 翻訳モード判定
        trans_model = config.get("translation", {}).get("translation_model", "openai").lower()
        self._realtime_mode: bool = (trans_model == "openai-realtime")

        # 音声出力モード: 出力デバイスが指定されていれば有効
        self._audio_output_mode: bool = output_device_index is not None
        self._output_device_index: int | None = output_device_index
        self._output_volume: float = output_volume
        self._audio_stream = None  # AudioOutputStream インスタンス（起動時に生成）

        if not self._realtime_mode:
            self._translator = TranslationService(config)
            self._realtime_translator = None
            self._cost_monitor = None
        else:
            self._translator = None
            # PR-2: API キーチェックは start() 時に遅延実施（lazy init）。
            # __init__ では ValueError を投げず、_realtime_translator / _cost_monitor を None にする。
            self._realtime_translator = None
            self._cost_monitor = None

        self._route_id: str = route_id
        # route_id を先に確定させてから _log_path / _verbose_log_path を作成する
        # （_make_log_path は route_id をサフィックスとして使うため）
        # 注入された共有 PyAudio インスタンス（None なら各スレッドが自前で生成）
        # MultiCaptionSystem が共有 PyAudio を管理し、PortAudio 二重初期化を防ぐ
        self._pa_instance: "pyaudio.PyAudio | None" = pa_instance
        # shared_broadcaster が注入された場合は WebSocket サーバーを自前で起動しない
        if shared_broadcaster is not None:
            self._broadcaster = shared_broadcaster
            self._owns_broadcaster = False
        else:
            self._broadcaster = SubtitleBroadcaster()
            self._owns_broadcaster = True
        self._loop: asyncio.AbstractEventLoop | None = None
        self._recorder: AudioToTextRecorder | None = None
        self._stop_event = threading.Event()
        self._stop_event_async: asyncio.Event | None = None
        log_dir = config.get("output", {}).get("log_dir", ".")
        self._log_path = self._make_log_path(Path(log_dir), self._route_id)
        # 音声処理の共有状態（capture スレッドと GUI / RPC スレッド間）
        # frozen dataclass + Lock でスナップショット方式（PEP 703 free-threaded 対応）
        self._audio_stats_lock = threading.Lock()
        self._audio_stats = AudioStats()
        # AGC の内部状態（capture スレッド内のみ使用、共有なし）
        self._agc_gain: float = 1.0
        self._agc_envelope: float = 0.0  # 直近の peak 追従値（減衰付き）
        # _capture_thread_body が開いた PyAudio ストリーム（shutdown から stop_stream() で解除）
        self._capture_stream = None
        # 音声出力ストリームの並行アクセスを保護するロック（set_output_device vs _on_audio_delta）
        self._audio_stream_lock = threading.Lock()
        # Verbose ログ（STT 結果・翻訳リクエスト・成功失敗を時系列で別ファイルに残す）
        self.verbose: bool = False
        self._verbose_log_path: Path | None = None
        self._verbose_lock = threading.Lock()
        # Realtime モード: 原文・翻訳の最新バッファ（ペアリング配信用）
        self._latest_source: str = ""
        self._latest_translation: str = ""
        # RouteState: IDLE / STARTING / RUNNING / STOPPING / ERROR
        self._state: RouteState = RouteState.IDLE
        self._state_lock = threading.Lock()

    # ---- スレッドセーフな共有状態アクセス --------------------------------
    @property
    def audio_stats(self) -> AudioStats:
        """整合性の取れた共有状態スナップショットを返す（atomic）。"""
        with self._audio_stats_lock:
            return self._audio_stats

    def _update_audio_stats(self, **kwargs) -> None:
        """共有状態をスレッドセーフに更新する。"""
        with self._audio_stats_lock:
            self._audio_stats = replace(self._audio_stats, **kwargs)

    def _log(self, category: str, message: str) -> None:
        """route_id プレフィックス付きでログを出力するヘルパー。

        出力形式: [CATEGORY][route_id] message
        _route_id が未設定の場合は '?' をフォールバックとして使う。
        """
        rid = getattr(self, "_route_id", "?")
        print(f"[{category}][{rid}] {message}", flush=True)

    # ---- RouteState 管理 ------------------------------------------------
    @property
    def state(self) -> RouteState:
        """現在の RouteState をスレッドセーフに返す。"""
        with self._state_lock:
            return self._state

    def _set_state(self, new_state: RouteState) -> None:
        """内部メソッド。状態を変更してログ出力（thread-safe）。"""
        with self._state_lock:
            old = self._state
            self._state = new_state
        if old != new_state:
            print(
                f"[STATE] CaptionSystem(route_id={self._route_id})"
                f" {old.value} -> {new_state.value}",
                flush=True,
            )
            self._log_verbose("STATE_TRANSITION", old=old.value, new=new_state.value)

    def get_asyncio_thread(self) -> "threading.Thread | None":
        """start() で生成された asyncio スレッドを返す (terminate での join 用)。

        start() 前または stop() 後は None を返す。
        MultiCaptionSystem.start_route() が _thread_a/_thread_b を更新する際に使用する。
        """
        return getattr(self, "_asyncio_thread", None)

    # ---- 後方互換プロパティ（既存の外部呼び出し維持） -------------------
    @property
    def audio_peak(self) -> int:
        return self.audio_stats.peak

    @property
    def audio_peak_now(self) -> int:
        return self.audio_stats.peak_now

    @property
    def audio_chunks_per_sec(self) -> int:
        return self.audio_stats.chunks_per_sec

    @property
    def effective_gain(self) -> float:
        return self.audio_stats.gain

    @property
    def gain_mode(self) -> str:
        return self.audio_stats.mode

    @gain_mode.setter
    def gain_mode(self, value: str) -> None:
        self._update_audio_stats(mode=value)

    @property
    def manual_gain(self) -> float:
        return self.audio_stats.manual_gain

    @manual_gain.setter
    def manual_gain(self, value: float) -> None:
        self._update_audio_stats(manual_gain=value)

    @property
    def output_volume(self) -> float:
        """出力音量倍率を返す。"""
        return self._output_volume

    @output_volume.setter
    def output_volume(self, value: float) -> None:
        """出力音量倍率を設定し、動作中の AudioOutputStream に即座に反映する。"""
        self._output_volume = float(value)
        if self._audio_stream is not None:
            try:
                self._audio_stream.set_volume(float(value))
            except Exception:
                pass

    def _create_realtime_translator(self) -> None:
        """start() 時に呼ばれる RealtimeTranslator / CostMonitor の lazy initialization。

        既に生成済みの場合は no-op。
        """
        if self._realtime_translator is not None:
            return
        rt_cfg = self._config.get("openai_realtime", {})
        api_key = self._config.get("openai", {}).get("api_key", "")
        from realtime_translator import RealtimeTranslator
        # 音声出力 OFF 時は session.update から audio.output を除外し、API 側の音声生成を
        # 停止する（コスト削減）。OFF→ON 動的切替は PR3 で再起動方式により対応。
        # _audio_output_mode は __init__ で output_device_index is not None として設定され、
        # set_output_device() でも更新されるため、stop_route -> start_route による
        # 再生成のたびに最新の値が使われる。
        self._realtime_translator = RealtimeTranslator(
            api_key=api_key,
            target_language_code=rt_cfg.get("target_language_code", "ja"),
            model=rt_cfg.get("model", "gpt-realtime-translate"),
            connect_timeout=rt_cfg.get("connect_timeout", 10),
            reconnect_max_attempts=rt_cfg.get("reconnect_max_attempts", 5),
            reconnect_backoff_base=rt_cfg.get("reconnect_backoff_base", 1.5),
            on_transcript=self._on_realtime_transcript,
            on_source_transcript=self._on_realtime_source_transcript,
            on_error=self._on_realtime_error,
            on_connected=self._on_ready,
            request_audio_output=self._audio_output_mode,
            on_audio_delta=self._on_audio_delta,
            request_source_transcript=self._request_source_transcript,
        )
        from cost_monitor import CostMonitor
        max_min = rt_cfg.get("max_session_minutes", 60)
        self._cost_monitor = CostMonitor(
            max_session_minutes=max_min,
            on_max_reached=self._on_cost_max_reached,
            on_warning=self._on_cost_warning,
        )
        # W-COST-4: アイドル切断モニターを生成（enabled=True かつ未生成の場合のみ）
        if self._idle_disconnect_enabled and self._idle_monitor is None:
            from cost_monitor import IdleDisconnectMonitor
            self._idle_monitor = IdleDisconnectMonitor(
                idle_timeout_sec=self._idle_timeout_sec,
                audio_threshold=self._idle_audio_threshold,
                on_idle_timeout=self._on_idle_timeout,
            )
        # issue #121-B: RealtimeTranslator 生成完了 — 設定値一覧を [ACTION] で記録
        print(
            f"[ACTION] RealtimeTranslator 生成"
            f" route_id={self._route_id}"
            f" request_source_transcript={self._request_source_transcript}"
            f" request_audio_output={self._audio_output_mode}",
            flush=True,
        )
        self._log_verbose(
            "RT_INIT",
            request_source_transcript=self._request_source_transcript,
            request_audio_output=self._audio_output_mode,
            target_language_code=rt_cfg.get("target_language_code", "ja"),
            model=rt_cfg.get("model", "gpt-realtime-translate"),
        )
        # verbose コールバックを繋ぐ
        self._realtime_translator._verbose_callback = self._log_verbose

    def start(self) -> None:
        """IDLE / ERROR 状態から RUNNING へ遷移する。

        - _stop_event をリセット
        - asyncio イベントループスレッドを生成して asyncio.run(self.run()) を実行
        - STARTING / RUNNING 中は no-op（冪等性保証）
        - API キー未設定の場合は state=ERROR に遷移して on_error を呼ぶ

        状態遷移タイミング（W-6）:
          IDLE/ERROR → STARTING（start() 冒頭）
                     → RUNNING（非同期スレッド起動完了後、このメソッドの末尾で遷移）

        Note: スレッド起動完了時点で RUNNING に遷移するため、
        実際の WebSocket 接続確立よりも前に RUNNING 状態になる。
        on_connected コールバックが呼ばれるタイミング（WS 接続完了）とは異なる。
        """
        # 冪等性ガード
        if self.state in (RouteState.STARTING, RouteState.RUNNING):
            return

        self._set_state(RouteState.STARTING)
        try:
            # API キー遅延チェック（realtime モードの場合）
            if self._realtime_mode:
                api_key = self._config.get("openai", {}).get("api_key", "")
                if not api_key or api_key == "your-api-key-here" or "xxx" in api_key:
                    msg = "openai.api_key が未設定です"
                    print(
                        f"[ERROR] CaptionSystem(route_id={self._route_id})"
                        f" start failed: {msg}",
                        flush=True,
                    )
                    self._set_state(RouteState.ERROR)
                    if self._on_realtime_error_external is not None:
                        try:
                            self._on_realtime_error_external(msg)
                        except Exception:
                            pass
                    return
                # RealtimeTranslator / CostMonitor を lazy 生成
                self._create_realtime_translator()

            # _stop_event をクリア（前回 stop() で再生成済みだが念のため）
            self._stop_event.clear()

            # asyncio.run(self.run()) を別スレッドで実行
            def _run_in_thread():
                try:
                    asyncio.run(self.run())
                except Exception as e:
                    import traceback
                    print(
                        f"[ERROR] CaptionSystem(route_id={self._route_id})"
                        f" run() exception: {e}",
                        flush=True,
                    )
                    print(traceback.format_exc(), flush=True)
                    self._set_state(RouteState.ERROR)
                    return
                # 正常終了: IDLE に戻す（stop() が先に IDLE にしている場合は no-op）
                if self.state not in (RouteState.IDLE,):
                    self._set_state(RouteState.IDLE)

            self._asyncio_thread = threading.Thread(
                target=_run_in_thread,
                daemon=True,
                name=f"CaptionSystem-{self._route_id}",
            )
            self._asyncio_thread.start()
            self._set_state(RouteState.RUNNING)
        except Exception as e:
            print(
                f"[ERROR] CaptionSystem(route_id={self._route_id}) start() failed: {e}",
                flush=True,
            )
            self._set_state(RouteState.ERROR)
            raise

    def stop(self) -> None:
        """RUNNING → STOPPING → IDLE への遷移。

        - IDLE / STOPPING 中は no-op（冪等性保証）
        - 内部状態（_stop_event / _loop 等）を stop 完了後にリセットする
        """
        import time as _time

        # 冪等性: IDLE または STOPPING なら no-op
        if self.state in (RouteState.STOPPING,):
            return
        # _stop_event が既にセット済みの場合も no-op（既存の二重 shutdown 対策を継承）
        if self._stop_event.is_set():
            return

        self._set_state(RouteState.STOPPING)
        _t0 = _time.monotonic()
        route_id = getattr(self, "_route_id", "?")

        try:
            self._stop_event.set()
            # capture stream を即座に停止して read() のブロックを解除する。
            # pyaudio.Stream.read() はブロッキング呼び出しのため stop_event だけでは抜けられない。
            # stop_stream() が呼ばれると read() が OSError を投げ、capture loop が脱出できる。
            cs = getattr(self, "_capture_stream", None)
            if cs is not None:
                _t1 = _time.monotonic()
                try:
                    cs.stop_stream()
                except Exception as e:
                    self._log("WARN", f"capture stream stop_stream failed: {e}")
                print(
                    f"[TIMING] CaptionSystem(route_id={route_id}).stop_stream():"
                    f" {_time.monotonic() - _t1:.3f}s",
                    flush=True,
                )
            # capture スレッドを先に停止させて、feed_audio が止まってから recorder.stop() を呼ぶ
            cap = getattr(self, "_capture_thread", None)
            if cap is not None and cap.is_alive():
                _t1 = _time.monotonic()
                cap.join(timeout=5.0)
                print(
                    f"[TIMING] CaptionSystem(route_id={route_id})._capture_thread.join():"
                    f" {_time.monotonic() - _t1:.3f}s",
                    flush=True,
                )
                if cap.is_alive():
                    print(
                        f"[WARN] capture thread (route_id={route_id})"
                        f" did not exit in 5 seconds",
                        flush=True,
                    )
            if self._recorder:
                _t1 = _time.monotonic()
                try:
                    self._recorder.stop()
                except Exception:
                    pass
                print(
                    f"[TIMING] CaptionSystem(route_id={route_id})._recorder.stop():"
                    f" {_time.monotonic() - _t1:.3f}s",
                    flush=True,
                )
            if self._loop and self._stop_event_async:
                self._loop.call_soon_threadsafe(self._stop_event_async.set)
            # Realtime モードの WebSocket 接続を停止
            if getattr(self, "_realtime_translator", None) is not None:
                _t1 = _time.monotonic()
                try:
                    self._realtime_translator.stop()
                except Exception:
                    pass
                print(
                    f"[TIMING] CaptionSystem(route_id={route_id})._realtime_translator.stop():"
                    f" {_time.monotonic() - _t1:.3f}s",
                    flush=True,
                )
            # 音声出力ストリームを停止
            if getattr(self, "_audio_stream", None) is not None:
                _t1 = _time.monotonic()
                try:
                    self._audio_stream.stop()
                except Exception:
                    pass
                print(
                    f"[TIMING] CaptionSystem(route_id={route_id})._audio_stream.stop():"
                    f" {_time.monotonic() - _t1:.3f}s",
                    flush=True,
                )
            # コストモニターを停止
            if getattr(self, "_cost_monitor", None) is not None:
                _t1 = _time.monotonic()
                try:
                    self._cost_monitor.stop()
                except Exception:
                    pass
                print(
                    f"[TIMING] CaptionSystem(route_id={route_id})._cost_monitor.stop():"
                    f" {_time.monotonic() - _t1:.3f}s",
                    flush=True,
                )
            # W-COST-4: アイドル切断モニターを停止
            if getattr(self, "_idle_monitor", None) is not None:
                try:
                    self._idle_monitor.stop()
                except Exception:
                    pass
            print(
                f"[TIMING] CaptionSystem(route_id={route_id}).stop() TOTAL:"
                f" {_time.monotonic() - _t0:.3f}s",
                flush=True,
            )
            # subst ドライブの解除はアプリ終了時のみ（app.py の main() / main.py の main() で実施）。
        finally:
            # 内部状態をリセット（次回 start() が呼べるように）
            # 仕様書 §リスク 1: リセット漏れがあると次回 start() が即 IDLE に落ちる
            self._stop_event = threading.Event()  # 新しい Event を生成（前の Event は set 済み）
            self._loop = None
            self._stop_event_async = None
            self._capture_thread = None
            self._capture_stream = None
            # Fix 1 (issue #102): stop() 後に _realtime_translator / _cost_monitor を None にリセット。
            # これにより次回 start() → _create_realtime_translator() で新インスタンスが生成され、
            # target_language_code 等の最新設定が反映される（stop→start サイクルで言語反映）。
            self._realtime_translator = None
            self._cost_monitor = None
            # W-COST-4: アイドル切断モニターを None にリセット（次回 start() で再生成）
            self._idle_monitor = None
            self._set_state(RouteState.IDLE)

    def shutdown(self) -> None:
        """後方互換: stop() の thin wrapper。"""
        self.stop()

    def _ensure_verbose_log_path(self) -> Path:
        """verbose ログファイルパスを遅延生成。translate ログと同じ N を使う。"""
        if self._verbose_log_path is None:
            base = self._log_path.stem.replace("_translate", "_verbose")
            self._verbose_log_path = self._log_path.parent / f"{base}.txt"
        return self._verbose_log_path

    def _log_verbose(self, event: str, **fields):
        """verbose=True のときのみ、verbose ログにイベントを書く。

        payload キーのみ最大 5000 文字まで許容（WS 全文記録用）。
        その他フィールドは従来通り最大 500 文字。
        """
        if not self.verbose:
            return
        try:
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            # 経路 ID を接頭辞に付与（複数系統で同一ファイルに書く場合の区別用）
            route_prefix = f"[{getattr(self, '_route_id', '?')}]"
            with self._verbose_lock:
                with self._ensure_verbose_log_path().open("a", encoding="utf-8") as f:
                    f.write(f"[{ts}] {route_prefix} {event}\n")
                    for k, v in fields.items():
                        s = str(v).replace("\n", "\\n")
                        # payload フィールドは 5000 文字まで許容（WS 全文記録用）
                        limit = 5000 if k == "payload" else 500
                        if len(s) > limit:
                            s = s[:limit] + "..."
                        f.write(f"  {k}: {s}\n")
                    f.write("\n")
        except Exception:
            pass  # ロギング自体で失敗してもアプリは止めない

    @staticmethod
    def _make_log_path(log_dir: Path, route_id: str = "a") -> Path:
        """YYYY-MM-DD-n[-route-X]_translate.txt 形式のログファイルパスを生成する。

        - route_id="a" の場合（既存・単独モード）: YYYY-MM-DD-n_translate.txt（互換）
        - route_id="b" の場合: YYYY-MM-DD-n-route-b_translate.txt
        """
        today = datetime.now().strftime("%Y-%m-%d")
        suffix = f"-route-{route_id}" if route_id != "a" else ""
        n = 1
        while True:
            path = log_dir / f"{today}-{n}{suffix}_translate.txt"
            if not path.exists():
                return path
            n += 1

    def set_output_device(self, device_index: "int | None") -> None:
        """出力デバイスを動的に変更する（稼働中も呼べる、thread-safe）。

        device_index=None: 出力ストリームを停止し、_audio_stream=None にする
        device_index=int:  既存ストリームを停止し、新デバイスで再生成して start
        """
        from audio_output import AudioOutputStream
        with self._audio_stream_lock:
            # 既存ストリームを停止
            old_stream = self._audio_stream
            self._audio_stream = None  # None に先に設定して _on_audio_delta への write を防ぐ

        if old_stream is not None:
            try:
                old_stream.stop()
            except Exception:
                pass

        if device_index is None:
            self._output_device_index = None
            self._audio_output_mode = False
            return

        # 共有 PyAudio があればそれを使い、なければローカル生成
        if self._pa_instance is not None:
            pa_for_output = self._pa_instance
            owns_pa = False
        else:
            pa_for_output = pyaudio.PyAudio()
            owns_pa = True

        new_stream = AudioOutputStream(
            pyaudio_instance=pa_for_output,
            device_index=device_index,
            volume=self._output_volume,
            owns_pa=owns_pa,
        )
        new_stream.start()

        with self._audio_stream_lock:
            self._audio_stream = new_stream

        self._output_device_index = device_index
        self._audio_output_mode = True

    def set_input_device(self, device_info: dict) -> None:
        """入力デバイスを動的に変更する (C-4, W-1 対応)。

        稼働中なら stop → device 更新 → start で再起動。
        停止中なら _device_info / _recorder のみ更新。

        start() が例外を発生させた場合（API キー未設定等）は state が ERROR のまま残るが、
        例外を呼び出し元（GUI コールバック）に伝播させず、ログにのみ記録する（W-1）。

        Parameters
        ----------
        device_info: 新しい入力デバイス情報（list_audio_devices() が返す dict 形式）
        """
        was_running = self.state == RouteState.RUNNING
        if was_running:
            self.stop()
        self._device_info = device_info
        self._recorder = None  # recorder クリア（次回 start() で再生成）
        if was_running:
            try:
                self.start()
            except Exception as e:
                # state は start() 内で ERROR に遷移済み
                self._log(
                    "WARN",
                    f"set_input_device: start() に失敗 ({type(e).__name__}: {e})",
                )
                # 呼び出し元（GUI コールバック）には例外を伝播させない

    def set_target_language(self, code: str) -> None:
        """翻訳先言語を動的に変更する（Fix 3, issue #102）。

        config の openai_realtime.target_language_code を更新し、
        _realtime_translator を None にクリアする。
        これにより次回 _create_realtime_translator() が呼ばれたとき（stop→start サイクル）
        最新の言語コードが RealtimeTranslator に渡される。

        実際の再起動（stop→start）はコールバック側（app.py の _on_route_a/b_language_change）
        が担う。set_input_device パターンと同様、このメソッドは属性更新のみを行う。

        Parameters
        ----------
        code: 翻訳先言語コード（例: "ja", "en"）。constants.SUPPORTED_LANGUAGES から選択。
        """
        if "openai_realtime" not in self._config:
            self._config["openai_realtime"] = {}
        self._config["openai_realtime"]["target_language_code"] = code
        # RealtimeTranslator をクリアして次回 start() で新インスタンスが生成されるようにする
        self._realtime_translator = None

    def _on_audio_delta(self, pcm16_bytes: bytes) -> None:
        """RealtimeTranslator から音声出力チャンクを受け取るコールバック。"""
        with self._audio_stream_lock:
            stream = self._audio_stream
        if stream is not None:
            stream.write(pcm16_bytes)

    def _on_realtime_transcript(self, text: str):
        """RealtimeTranslator から翻訳テキストを受け取るコールバック。

        API は原文と翻訳を独立した単位でストリーミングするため（1:1 ペアではない）、
        翻訳が確定したら原文側を空文字にしてブロードキャストする。
        旧実装で _latest_source とペアリングしていたため、同じ翻訳が複数の原文に
        重複表示される問題があった。
        """
        self._log_verbose("CALLBACK", name="_on_realtime_transcript")
        if not text:
            return
        self._latest_translation = text
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                self._realtime_broadcast("", text, self._route_id), self._loop
            )

    def _on_realtime_source_transcript(self, text: str):
        """RealtimeTranslator から原文テキストを受け取るコールバック（Issue #23）。

        原文確定時は翻訳側を空文字にしてブロードキャストし、UI/ログ側で
        EN ストリームとして独立表示する。
        """
        self._log_verbose("CALLBACK", name="_on_realtime_source_transcript")
        if not text:
            return
        self._latest_source = text
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                self._realtime_broadcast(text, "", self._route_id), self._loop
            )

    def _on_realtime_error(self, msg: str):
        """RealtimeTranslator からエラーを受け取るコールバック。"""
        print(f"[RT ERROR] {msg}")
        self._log_verbose("RT_ERROR", message=msg)
        if self._on_realtime_error_external is not None:
            try:
                self._on_realtime_error_external(msg)
            except Exception:
                pass

    def _on_cost_max_reached(self) -> None:
        """CostMonitor が最大稼働時間に達したときのコールバック。"""
        max_min = self._config.get("openai_realtime", {}).get("max_session_minutes", 60)
        print(f"\n[COST] 最大稼働時間 {max_min} 分に達したため停止します。", flush=True)
        self._log_verbose("COST_MAX_REACHED", max_session_minutes=max_min)
        # on_cost_warning コールバック経由で GUI にも通知（GUI 側でフラグを立てる）
        if getattr(self, "_on_cost_status", None) is not None:
            self._on_cost_status(f"最大稼働時間 {max_min} 分に達したため停止しました")
        self.shutdown()

    def _on_cost_warning(self, threshold: float) -> None:
        """CostMonitor が警告閾値を超えたときのコールバック。"""
        print(f"\n[COST WARN] 想定コストが ${threshold:.2f} を超えました。", flush=True)
        self._log_verbose("COST_WARNING", threshold_usd=threshold)
        # GUI 側でフラグを立てて警告モーダルを表示させる（dpg 直接呼び出しは安全でない）
        if getattr(self, "_on_cost_warning_cb", None) is not None:
            self._on_cost_warning_cb(threshold)

    def _on_idle_timeout(self) -> None:
        """IdleDisconnectMonitor からのアイドル通知。RealtimeTranslator を切断する。

        W-COST-4: アイドル時間経過時に IdleDisconnectMonitor のウォッチャーループから
        コールバックされる。切断フラグを立て、WebSocket セッションを切断する。
        state は維持（IDLE には戻さない、ユーザー再開待ち）。
        """
        self._log("INFO", f"アイドル {self._idle_timeout_sec:.0f} 秒を検知 → 切断")
        if self._idle_monitor is not None:
            self._idle_monitor.set_disconnected(True)
        if self._realtime_translator is not None:
            self._realtime_translator.disconnect()

    def resume_from_idle(self) -> None:
        """アイドル切断状態から再接続する（ユーザートリガー）。

        W-COST-4 PR2: スケルトン実装。PR3 で app.py の「再開」ボタン / PTT 押下から呼ばれる予定。
        _idle_monitor が None または切断状態でない場合は no-op。
        """
        if self._idle_monitor is None or not self._idle_monitor.is_disconnected():
            return
        self._log("INFO", "アイドルから再接続")
        if self._realtime_translator is not None:
            self._realtime_translator.connect()
        self._idle_monitor.reset_idle_timer()
        self._idle_monitor.set_disconnected(False)

    async def _realtime_broadcast(self, original: str, translated: str, route: str = "a"):
        """Realtime 原文・翻訳テキストを WebSocket とログに配信する。

        Args:
            original: 原文テキスト
            translated: 翻訳テキスト
            route: 経路識別子 ("a" | "b")。既存片方向モードはデフォルト "a"。
        """
        self._log_verbose("RT_BROADCAST", original=original, translated=translated)
        if original:
            print(f"\n[原文(RT)] {original}")
        if translated:
            print(f"[翻訳(RT)] {translated}")

        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with self._log_path.open("a", encoding="utf-8") as f:
                if original or translated:
                    f.write(f"[{ts}]\n")
                    if original:
                        f.write(f"原文(RT): {original}\n")
                    if translated:
                        f.write(f"翻訳(RT): {translated}\n")
                    f.write("\n")
        except Exception:
            pass

        if self._on_result:
            self._on_result(original, translated)

        payload = json.dumps(
            {"original": original, "translated": translated, "route": route},
            ensure_ascii=False,
        )
        # デバッグログ: verbose モード時は payload の最初の 200 文字を出力
        if self.verbose:
            self._log_verbose("RT_WS_SEND", route=route, payload=payload[:200])
        await self._broadcaster.broadcast(payload)

    def _on_transcription(self, text: str):
        """RealtimeSTT から文字起こし結果を受け取るコールバック。
        翻訳は asyncio のスレッドプールで非同期実行し、コールバックをすぐに返す。"""
        text = text.strip()
        if not text:
            return

        if self._on_whisper_busy:
            self._on_whisper_busy(False)

        self._log_verbose("STT", text=text)
        print(f"\n[原文] {text}")

        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                self._translate_and_broadcast(text), self._loop
            )

    async def _translate_and_broadcast(self, text: str):
        """翻訳を asyncio スレッドプールで実行し、WebSocket に配信する。"""
        self._log_verbose("TRANS_REQ", text=text)
        if self._on_trans_busy:
            self._on_trans_busy(True)
        error_msg = None
        try:
            translated = await asyncio.to_thread(self._translator.translate, text)
        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            print(f"[翻訳エラー] {error_msg}")
            translated = ""
        finally:
            if self._on_trans_busy:
                self._on_trans_busy(False)

        if translated:
            self._log_verbose("TRANS_OK", original=text, translated=translated)
        else:
            self._log_verbose("TRANS_ERR", original=text, error=error_msg or "empty response")

        # メイン translate ログには成功・失敗の両方を残す（ブラックホール防止）
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with self._log_path.open("a", encoding="utf-8") as f:
                if translated:
                    f.write(f"[{ts}]\n原文: {text}\n翻訳: {translated}\n\n")
                else:
                    f.write(f"[{ts}]\n原文: {text}\n翻訳: [失敗: {error_msg or '空応答'}]\n\n")
        except Exception:
            pass

        # GUI への反映: 成功時は原文+翻訳、失敗時は原文+エラー表示
        if self._on_result:
            display = translated if translated else f"[翻訳失敗: {error_msg or '空応答'}]"
            self._on_result(text, display)
        elif translated:
            print(f"[翻訳] {translated}")

        payload = json.dumps({"original": text, "translated": translated}, ensure_ascii=False)
        await self._broadcaster.broadcast(payload)

    def _capture_thread_body(self):
        """
        入力デバイス（ループバック / マイク）から音声をキャプチャし、
        recorder または realtime_translator に feed する。
        """
        device_index = self._device_info["index"]
        src_rate = int(self._device_info["defaultSampleRate"])
        channels = max(1, int(self._device_info.get("maxInputChannels", 1)))
        chunk_size = 1024

        # リサンプリング先レートをモードで切替
        target_rate = REALTIME_TRANSLATE_SAMPLE_RATE if self._realtime_mode else REALTIMESTT_SAMPLE_RATE

        # リサンプリング比率を既約分数で求める
        g = gcd(target_rate, src_rate)
        up = target_rate // g
        down = src_rate // g

        # 注入された共有 PyAudio があればそれを使い、なければローカル生成
        if self._pa_instance is not None:
            pa = self._pa_instance
            owns_pa = False
        else:
            pa = pyaudio.PyAudio()
            owns_pa = True
        try:
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=src_rate,
                input=True,
                input_device_index=device_index,
                frames_per_buffer=chunk_size,
            )
        except Exception as e:
            self._log("ERROR", f"キャプチャストリームのオープンに失敗: {e}")
            if owns_pa:
                pa.terminate()
            return

        # shutdown() から stop_stream() を呼べるようにインスタンス変数に保存する
        self._capture_stream = stream

        print(f"[INFO] キャプチャ開始: route={getattr(self, '_route_id', '?')} "
              f"device_index={device_index} name='{self._device_info.get('name', '?')}' "
              f"{src_rate}Hz, {channels}ch -> {target_rate}Hz mono", flush=True)

        # デバッグ用: 1秒ごとに音量レベルを出力
        import time as _time
        level_window_max = 0
        level_window_chunks = 0
        next_log = _time.time() + 1.0
        chunks_per_sec = max(1, src_rate // chunk_size)

        try:
            while not self._stop_event.is_set():
                try:
                    raw = stream.read(chunk_size, exception_on_overflow=False)
                except OSError:
                    # stream が close/terminate された場合（shutdown 中）は静かに抜ける
                    if self._stop_event.is_set():
                        break
                    raise

                # bytes -> numpy int16 配列
                audio = np.frombuffer(raw, dtype=np.int16)

                # ステレオ（またはマルチチャンネル）→ モノラル変換
                if channels > 1:
                    audio = audio.reshape(-1, channels)
                    audio = audio.mean(axis=1)

                # --- Gain 処理 ---
                # 共有状態は冒頭で 1 回スナップショット取得（ロック回数を減らす）
                stats_snapshot = self.audio_stats
                audio_f = audio.astype(np.float32)
                if stats_snapshot.mode == "manual":
                    g = max(1.0, min(float(stats_snapshot.manual_gain), 50.0))
                    new_gain = g
                    audio_f = audio_f * g
                elif stats_snapshot.mode == "auto":
                    # AGC: 直近 peak を指数減衰で追跡、target=20000 (60%) に調整
                    local_peak = float(np.abs(audio_f).max()) if audio_f.size else 0.0
                    # envelope は減衰定数 0.995（約200ms のリリース時定数相当）
                    self._agc_envelope = max(local_peak, self._agc_envelope * 0.995)
                    if self._agc_envelope > 1.0:
                        target = 20000.0
                        desired = target / self._agc_envelope
                        desired = max(1.0, min(desired, 50.0))
                        # 急減は速く、増大は遅く（attack 0.3 / release 0.05）
                        alpha = 0.3 if desired < self._agc_gain else 0.05
                        self._agc_gain += (desired - self._agc_gain) * alpha
                    new_gain = self._agc_gain
                    audio_f = audio_f * self._agc_gain
                else:
                    new_gain = 1.0
                audio = audio_f
                # int16 範囲にクリップ（gain 適用後のオーバーフロー防止）
                audio = np.clip(audio, -32768, 32767)

                # 音量レベル監視（gain適用後の実効peak を採用）
                chunk_max = int(np.abs(audio).max()) if audio.size else 0
                # リアルタイム表示用: ピークホールド（減衰つき、前回 snapshot から計算）
                new_peak_now = max(chunk_max, int(stats_snapshot.peak_now * 0.85))
                level_window_max = max(level_window_max, chunk_max)
                level_window_chunks += 1
                now = _time.time()
                if now >= next_log:
                    # 1 秒間隔の集計更新: gain / peak_now と一緒に 1 回の atomic 更新
                    self._update_audio_stats(
                        gain=new_gain,
                        peak_now=new_peak_now,
                        peak=level_window_max,
                        chunks_per_sec=level_window_chunks,
                    )
                    pct = level_window_max * 100 // 32767
                    bar = "█" * (pct // 5)
                    self._log("AUDIO", f"peak={level_window_max:>5d} ({pct:3d}%) {bar} chunks={level_window_chunks}")
                    # W-COST-4: アイドル切断モニターに 1 秒間のピーク値を報告
                    if self._idle_monitor is not None:
                        self._idle_monitor.report_audio_level(level_window_max)
                    level_window_max = 0
                    level_window_chunks = 0
                    next_log = now + 1.0
                else:
                    # 毎チャンク: peak_now / gain を反映（GUI のリアルタイム表示用）
                    self._update_audio_stats(peak_now=new_peak_now, gain=new_gain)

                # float32 に変換してリサンプリング
                audio_f = audio.astype(np.float32)
                resampled = resample_poly(audio_f, up, down)

                # int16 に戻して bytes に変換
                resampled_int16 = np.clip(resampled, -32768, 32767).astype(np.int16)
                pcm_bytes = resampled_int16.tobytes()

                # モードに応じて音声データの投入先を切替
                if self._realtime_mode:
                    if self._realtime_translator is not None:
                        self._realtime_translator.feed_audio(pcm_bytes)
                else:
                    if self._recorder is not None:
                        self._recorder.feed_audio(pcm_bytes)

        except Exception as e:
            self._log("ERROR", f"キャプチャ中にエラー: {e}")
            import traceback as _tb_mod
            self._log_verbose(
                "CAPTURE_ERROR",
                error_type=type(e).__name__,
                error_msg=str(e),
                traceback=_tb_mod.format_exc(),
            )
        finally:
            # インスタンス変数を先に None に戻す（shutdown の二重 stop_stream を防ぐ）
            self._capture_stream = None
            try:
                stream.stop_stream()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
            # 共有インスタンスは MultiCaptionSystem が shutdown() で terminate するため呼ばない
            if owns_pa:
                pa.terminate()

    def prepare(self):
        """Whisper モデルをロードして recorder を初期化する。
        バックグラウンドスレッドから事前呼び出し可能。run() より前に呼ぶことで起動を高速化できる。"""
        if self._realtime_mode:
            # Realtime モードでは Whisper / Silero ロード不要
            return
        if self._recorder is not None:
            return

        is_loopback = self._device_info.get("isLoopback", False)
        lang = self._config.get("whisper", {}).get("language", None) or None
        vad_cfg = self._config.get("vad", {})

        # on_transcription_start は self を動的参照するため、プリロード後に
        # self._on_whisper_busy を設定しても正しく動作する。
        # RealtimeSTT は audio_copy を引数に渡し、戻り値が truthy なら転記を中断するので注意。
        def _on_transcription_start(audio_copy=None):
            if self._on_whisper_busy:
                self._on_whisper_busy(True)
            return None  # 転記を中断しない

        # Whisper compute_type: config で指定（未指定なら int8 で CPU 高速化）
        whisper_cfg = self._config.get("whisper", {})
        compute_type = whisper_cfg.get("compute_type", "int8")
        device = whisper_cfg.get("device", "auto")  # "auto"/"cpu"/"cuda"

        common_args = dict(
            model=self._model_name,
            language=lang,
            spinner=False,
            enable_realtime_transcription=False,
            silero_sensitivity=vad_cfg.get("silero_sensitivity", 0.4),
            post_speech_silence_duration=vad_cfg.get("post_speech_silence_duration", 0.6),
            min_length_of_recording=0.3,
            on_transcription_start=_on_transcription_start,
            compute_type=compute_type,
            device=device,
        )
        try:
            if is_loopback:
                self._recorder = AudioToTextRecorder(**common_args, use_microphone=False)
            else:
                self._recorder = AudioToTextRecorder(
                    **common_args,
                    input_device_index=self._device_info["index"],
                    use_microphone=True,
                )
        except Exception as e:
            self._log("ERROR", f"AudioToTextRecorder 初期化失敗: {e}")

    def _start_recorder(self):
        """別スレッドで録音ループを起動する。prepare() が未完了なら先に呼ぶ。"""
        if self._realtime_mode:
            # Realtime モード: キャプチャスレッド起動（ループバック / マイク両対応）
            self._capture_thread = threading.Thread(
                target=self._capture_thread_body, daemon=True
            )
            self._capture_thread.start()
            self._log("INFO", "録音を開始しました（Realtimeモード）。")
            # on_ready は RealtimeTranslator の on_connected で呼ばれるため、ここでは呼ばない
            # キャプチャスレッドの終了を待つ（stop_event が set されるまで）
            while not self._stop_event.is_set():
                self._stop_event.wait(timeout=0.5)
            return

        if self._recorder is None:
            self.prepare()
        if self._recorder is None:
            return  # prepare 失敗

        is_loopback = self._device_info.get("isLoopback", False)
        if is_loopback:
            self._capture_thread = threading.Thread(
                target=self._capture_thread_body, daemon=True
            )
            self._capture_thread.start()

        self._log("INFO", "録音を開始しました。")
        if self._on_ready:
            self._on_ready()
        try:
            while not self._stop_event.is_set():
                self._recorder.text(self._on_transcription)
        except Exception as e:
            if not self._stop_event.is_set():
                self._log("ERROR", f"録音中にエラー: {e}")

    async def run(self):
        """WebSocket サーバーを起動し、録音スレッドを開始する。"""
        self._loop = asyncio.get_running_loop()
        self._stop_event_async = asyncio.Event()

        ws_host = self._config["websocket"]["host"]
        ws_port = self._config["websocket"]["port"]

        # 音声出力モードが有効なら AudioOutputStream を起動
        if self._audio_output_mode:
            from audio_output import AudioOutputStream
            ao_cfg = self._config.get("openai_realtime", {}).get("audio_output", {})
            sample_rate = ao_cfg.get("sample_rate", 24000)
            # 共有 PyAudio があればそれを使い（owns_pa=False で terminate しない）、
            # なければ新規生成（owns_pa=True で AudioOutputStream.stop() が terminate する）
            if self._pa_instance is not None:
                pa_for_output = self._pa_instance
                owns_pa = False
            else:
                pa_for_output = pyaudio.PyAudio()
                owns_pa = True
            self._audio_stream = AudioOutputStream(
                pyaudio_instance=pa_for_output,
                device_index=self._output_device_index,
                sample_rate=sample_rate,
                volume=self._output_volume,
                owns_pa=owns_pa,
            )
            self._audio_stream.start()
            self._log("INFO", f"音声出力ストリーム開始: device_index={self._output_device_index}")

        # Realtime モードでは RealtimeTranslator を起動
        if self._realtime_mode and self._realtime_translator is not None:
            # verbose コールバックを繋ぐ
            self._realtime_translator._verbose_callback = self._log_verbose
            self._realtime_translator.start(self._loop)

        # コストモニターを起動（Realtime モードのみ）
        if self._cost_monitor is not None:
            self._cost_monitor.start()

        # W-COST-4: アイドル切断モニターを起動（enabled=True かつ生成済みの場合のみ）
        if self._idle_monitor is not None:
            self._idle_monitor.start()

        recorder_thread = threading.Thread(target=self._start_recorder, daemon=True)
        recorder_thread.start()

        self._log("INFO", f"ログファイル: {self._log_path}")

        try:
            if self._owns_broadcaster:
                # WebSocket サーバーを自前で起動（単体起動 / MultiCaptionSystem の route_a）
                self._log("INFO", f"WebSocket サーバーを起動中: ws://{ws_host}:{ws_port}")
                async with websockets.serve(self._broadcaster.register, ws_host, ws_port):
                    await self._stop_event_async.wait()
            else:
                # shared_broadcaster を持つ系統はサーバー起動をスキップし、stop_event を待つだけ
                # WebSocket サーバーは broadcaster owner（route_a）が管理する
                await self._stop_event_async.wait()
        except OSError as e:
            import errno as _errno
            if e.errno == _errno.EADDRINUSE or e.errno == 10048:  # 10048 = Windows WSAEADDRINUSE
                msg = (
                    f"WebSocket サーバーの起動に失敗しました（ポート {ws_port} が使用中）。"
                    f"アプリの二重起動がないか確認してください。[{e}]"
                )
            else:
                msg = f"WebSocket サーバーの起動に失敗しました: {e}"
            self._log("ERROR", msg)
            if self._on_realtime_error_external:
                try:
                    self._on_realtime_error_external(msg)
                except Exception:
                    pass
            raise
        except asyncio.CancelledError:
            # asyncio 中断時のみ shutdown 必要（Stop ボタン経由の正常終了は呼び出し側が責務）
            self.shutdown()
        finally:
            self._log("INFO", "終了しました。")


def main():
    config = load_config("config.yaml")

    api_key = decode_api_key(config.get("openai", {}).get("api_key", ""))
    if not api_key or api_key == "your-api-key-here":
        print("[ERROR] config.yaml に OpenAI API キーを設定してください。")
        sys.exit(1)

    # openai-realtime モードの場合はコスト警告を表示
    trans_model = config.get("translation", {}).get("translation_model", "").lower()
    if trans_model == "openai-realtime":
        print("[WARN] ========================================")
        print("[WARN]  openai-realtime モードは従量課金制です")
        print("[WARN]  コスト: USD 0.034/分（約 USD 2.04/時間）")
        print("[WARN]  Whisper local + gpt-4o-mini より大幅に高コストです。")
        print("[WARN] ========================================")
        ans = input("続行しますか？ (y/N): ").strip().lower()
        if ans != "y":
            print("[INFO] 起動をキャンセルしました。")
            sys.exit(0)

    device_info = select_audio_device()

    # Realtime モードでは Whisper モデル選択は不要
    if trans_model == "openai-realtime":
        model_name = config.get("whisper", {}).get("model", "small")
    else:
        model_name = select_whisper_model(config["whisper"]["model"])

    system = CaptionSystem(config, device_info, model_name)

    # CLI モード: Realtime モードの場合は経過時間・コストを1秒ごとにコンソール表示
    if trans_model == "openai-realtime" and system._cost_monitor is not None:
        import time as _time

        def _cli_cost_display():
            while not system._stop_event.is_set():
                elapsed_sec = int(system._cost_monitor.elapsed_minutes() * 60)
                h = elapsed_sec // 3600
                m = (elapsed_sec % 3600) // 60
                s = elapsed_sec % 60
                cost = system._cost_monitor.estimated_cost_usd()
                print(
                    f"\r経過: {h:02d}:{m:02d}:{s:02d} / 想定コスト: ${cost:.2f}  ",
                    end="",
                    flush=True,
                )
                system._stop_event.wait(timeout=1.0)

        def _cli_warn_handler(threshold: float):
            print(f"\n[WARN] 想定コストが ${threshold:.2f} を超えました", flush=True)

        system._on_cost_warning_cb = _cli_warn_handler
        threading.Thread(target=_cli_cost_display, daemon=True).start()

    try:
        asyncio.run(system.run())
    except KeyboardInterrupt:
        pass
    finally:
        _release_subst(_subst_letter)


# ---------------------------------------------------------------------------
# 翻訳こんにゃくモード: MultiCaptionSystem / RouteConfig (Issue #38 Phase 2)
# ---------------------------------------------------------------------------


def _classify_realtime_error(msg: str) -> tuple[str, str]:
    """RealtimeTranslator からのエラー文字列を分類し、(category, display_text) を返す。

    Parameters
    ----------
    msg:
        RealtimeTranslator から受け取ったエラーメッセージ文字列。

    Returns
    -------
    (category, display_text)
      category: "quota" | "auth" | "rate_limit" | "connection" | "other"
      display_text: GUI ステータスバーに表示する文言（絵文字付き）
    """
    msg_lower = msg.lower()
    if "insufficient_quota" in msg_lower:
        return ("quota", "⚠️ OpenAI クォータ超過: https://platform.openai.com/usage で確認")
    if "invalid_api_key" in msg_lower or "incorrect api key" in msg_lower or "authentication" in msg_lower:
        return ("auth", "⚠️ OpenAI API キーが無効: 詳細設定で正しいキーを設定してください")
    if "rate_limit" in msg_lower or "rate limit" in msg_lower or "429" in msg_lower:
        return ("rate_limit", "⚠️ OpenAI レート制限。少し時間を空けてください")
    if "connection" in msg_lower or "timeout" in msg_lower or "refused" in msg_lower:
        return ("connection", "⚠️ OpenAI API に接続できません: ネットワークを確認してください")
    # 不明なエラーは原文をそのまま（長すぎる場合は 100 文字に切り詰め）
    truncated = msg[:100] + ("..." if len(msg) > 100 else "")
    return ("other", f"⚠️ OpenAI API エラー: {truncated}")


_QUOTA_USAGE_URL = "https://platform.openai.com/usage"


def _open_quota_usage_page(webbrowser_module=None) -> None:
    """OpenAI クォータ確認ページをデフォルトブラウザで開く。

    Parameters
    ----------
    webbrowser_module:
        テスト時に差し替え可能な webbrowser 互換オブジェクト。
        None の場合は標準ライブラリの webbrowser を使用する。
    """
    if webbrowser_module is None:
        import webbrowser
        webbrowser_module = webbrowser
    webbrowser_module.open(_QUOTA_USAGE_URL)


@dataclass(init=False)
class RouteConfig:
    """MultiCaptionSystem の1経路分の設定。"""

    route_id: str                       # "a" | "b"
    input_device_info: dict
    target_language_code: str           # "ja" | "en"（constants.SUPPORTED_LANGUAGES から選択）
    audio_output_enabled: bool
    output_device_index: int | None
    output_volume: float                # 0.0〜2.0
    request_source_transcript: bool     # W-COST-2: 原文表示（Whisper）有効フラグ。デフォルト True（後方互換）
    idle_disconnect_enabled: bool       # W-COST-4: アイドル切断有効フラグ。デフォルト False（後方互換・安全側）
    idle_timeout_sec: float             # W-COST-4: アイドル判定タイムアウト（秒）
    idle_audio_threshold: int           # W-COST-4: 無音とみなす音量上限（int16 絶対値 max）。TBD-4-2 実機計測で 100→200 に変更

    def __init__(
        self,
        route_id: str,
        input_device_info: dict,
        target_language_code: str,
        audio_output_enabled: bool,
        output_device_index: "int | None",
        output_volume: float,
        request_source_transcript: bool = True,
        idle_disconnect_enabled: bool = False,
        idle_timeout_sec: float = 300.0,
        idle_audio_threshold: int = 200,
        **deprecated_kwargs,
    ):
        # 後方互換: vad_* キーは無視。それ以外は TypeError
        _VAD_DEPRECATED_KEYS = frozenset({
            "vad_enabled", "vad_threshold", "vad_prefix_padding_ms", "vad_silence_duration_ms"
        })
        for k in deprecated_kwargs:
            if k not in _VAD_DEPRECATED_KEYS:
                raise TypeError(f"__init__() got an unexpected keyword argument '{k}'")
        self.route_id = route_id
        self.input_device_info = input_device_info
        self.target_language_code = target_language_code
        self.audio_output_enabled = audio_output_enabled
        self.output_device_index = output_device_index
        self.output_volume = output_volume
        self.request_source_transcript = request_source_transcript
        self.idle_disconnect_enabled = idle_disconnect_enabled
        self.idle_timeout_sec = idle_timeout_sec
        self.idle_audio_threshold = idle_audio_threshold


class MultiCaptionSystem:
    """2 系統の CaptionSystem を並列管理するオーケストレーター。

    経路A・Bそれぞれが独立した CaptionSystem インスタンスを持ち、
    1 本の SubtitleBroadcaster を共有して overlay.html へ配信する。

    仕様: Issue #38 §1 (翻訳こんにゃくモード Phase 2)

    スレッド設計:
      - route_a: _thread_a がスレッドを立て asyncio.run(route_a.run()) を実行
        → route_a._owns_broadcaster=True なので WebSocket サーバーも起動
      - route_b: _thread_b がスレッドを立て asyncio.run(route_b.run()) を実行
        → route_b._owns_broadcaster=False なので WS サーバー起動をスキップ
      - 2スレッドは独立した asyncio イベントループを持つ（ループ競合なし）
    """

    def __init__(
        self,
        config: dict,
        route_a: "RouteConfig | None",
        route_b: "RouteConfig | None",
        on_result_a: Callable[[str, str], None] | None = None,
        on_result_b: Callable[[str, str], None] | None = None,
        on_ready: Callable[[], None] | None = None,
        on_realtime_error: "Callable[[str, str, str], None] | None" = None,
        on_thread_error: Callable[[str, Exception, str], None] | None = None,
    ) -> None:
        if route_a is None and route_b is None:
            raise ValueError("少なくとも1つの route が必要です（route_a, route_b がともに None）")

        # 共有 PyAudio インスタンスを1つだけ生成（PortAudio assertion 回避）
        # 複数の pyaudio.PyAudio() を並列初期化すると WASAPI の状態が破壊され
        # 'Assertion failed: hostApi->info.defaultOutputDevice < hostApi->info.deviceCount'
        # でプロセスがクラッシュする。MultiCaptionSystem が責任を持って1つ管理する。
        self._pa = pyaudio.PyAudio()

        # on_realtime_error ラッパー: route_id を付与してユーザーコールバックに転送する
        def _wrap_error(route_id: str):
            def _handler(msg: str) -> None:
                if on_realtime_error is not None:
                    category, display = _classify_realtime_error(msg)
                    try:
                        on_realtime_error(route_id, category, display)
                    except Exception:
                        pass
            return _handler

        # route_a: 存在すれば生成（shared_broadcaster=None → _owns_broadcaster=True、WS サーバー起動）
        # route_b のみの場合: route_b が broadcaster を所有する（shared_broadcaster=None）
        if route_a is not None:
            config_a = self._build_route_config_dict(config, route_a)
            self._route_a: "CaptionSystem | None" = CaptionSystem(
                config=config_a,
                device_info=route_a.input_device_info,
                model_name=config_a.get("stt", {}).get("model", "tiny"),
                on_result=on_result_a,
                on_ready=on_ready,
                output_device_index=route_a.output_device_index if route_a.audio_output_enabled else None,
                output_volume=route_a.output_volume,
                route_id=route_a.route_id,
                shared_broadcaster=None,  # route_a が broadcaster を所有
                pa_instance=self._pa,     # 共有 PyAudio を注入
                on_realtime_error_external=_wrap_error(route_a.route_id),
                request_source_transcript=route_a.request_source_transcript,
                idle_disconnect_enabled=route_a.idle_disconnect_enabled,
                idle_timeout_sec=route_a.idle_timeout_sec,
                idle_audio_threshold=route_a.idle_audio_threshold,
            )
        else:
            self._route_a = None

        # route_b: 存在すれば生成
        # broadcaster は route_a があれば共有、なければ自前（_owns_broadcaster=True）
        if route_b is not None:
            shared = self._route_a._broadcaster if self._route_a is not None else None
            config_b = self._build_route_config_dict(config, route_b)
            self._route_b: "CaptionSystem | None" = CaptionSystem(
                config=config_b,
                device_info=route_b.input_device_info,
                model_name=config_b.get("stt", {}).get("model", "tiny"),
                on_result=on_result_b,
                on_ready=on_ready,
                output_device_index=route_b.output_device_index if route_b.audio_output_enabled else None,
                output_volume=route_b.output_volume,
                route_id=route_b.route_id,
                shared_broadcaster=shared,  # route_a あれば共有、なければ自前
                pa_instance=self._pa,       # 共有 PyAudio を注入
                on_realtime_error_external=_wrap_error(route_b.route_id),
                request_source_transcript=route_b.request_source_transcript,
                idle_disconnect_enabled=route_b.idle_disconnect_enabled,
                idle_timeout_sec=route_b.idle_timeout_sec,
                idle_audio_threshold=route_b.idle_audio_threshold,
            )
        else:
            self._route_b = None

        # start() で生成するスレッドへの参照（shutdown/join で利用）
        self._thread_a: threading.Thread | None = None
        self._thread_b: threading.Thread | None = None
        # スレッド例外通知コールバック（route_id, exc, traceback_str）
        self._on_thread_error = on_thread_error

    @staticmethod
    def _build_route_config_dict(base_config: dict, route: RouteConfig) -> dict:
        """base_config を浅くコピーし、RouteConfig の言語設定で上書きした dict を返す。"""
        import copy
        cfg = copy.deepcopy(base_config)
        # openai_realtime.target_language_code を RouteConfig の値で上書き
        if "openai_realtime" not in cfg:
            cfg["openai_realtime"] = {}
        cfg["openai_realtime"]["target_language_code"] = route.target_language_code
        return cfg

    def start_route(self, route_id: str) -> None:
        """指定 route を起動する（常駐モデル PR-3）。

        - route_id: "a" | "b"
        - 対象 CaptionSystem.start() を呼ぶ（PR-2 実装済み）
        - 該当 route が None なら no-op
        - start() 成功後に _thread_a/_thread_b を更新する（C-1: terminate() の join 用）
        """
        if route_id == "a" and self._route_a is not None:
            self._route_a.start()
            self._thread_a = self._route_a.get_asyncio_thread()
        elif route_id == "b" and self._route_b is not None:
            self._route_b.start()
            self._thread_b = self._route_b.get_asyncio_thread()

    def stop_route(self, route_id: str) -> None:
        """指定 route を停止する（IDLE に遷移）。インスタンスは破棄しない。

        - route_id: "a" | "b"
        - 該当 route が None なら no-op
        """
        if route_id == "a" and self._route_a is not None:
            self._route_a.stop()
        elif route_id == "b" and self._route_b is not None:
            self._route_b.stop()

    def start_all(self) -> None:
        """有効な全 route を起動する（常駐モデル PR-3）。

        PR-2 で実装した CaptionSystem.start() を呼ぶことで、各 CaptionSystem が
        内部で daemon スレッドを起動する。
        _thread_a/_thread_b は後方互換のため CaptionSystem._asyncio_thread への
        参照を設定する（既存テスト test_multi_caption_system_start_creates_event_loop 等）。
        """
        if self._route_a is not None:
            self._route_a.start()
            # 後方互換: _thread_a = CaptionSystem 内部の asyncio スレッド参照
            self._thread_a = getattr(self._route_a, "_asyncio_thread", None)

        if self._route_b is not None:
            self._route_b.start()
            # 後方互換: _thread_b = CaptionSystem 内部の asyncio スレッド参照
            self._thread_b = getattr(self._route_b, "_asyncio_thread", None)

    def stop_all(self) -> None:
        """全 route を停止する（IDLE に遷移）。インスタンスは破棄しない（常駐モデル PR-3）。"""
        if self._route_a is not None:
            self._route_a.stop()
        if self._route_b is not None:
            self._route_b.stop()

    def terminate(self) -> None:
        """アプリ終了時のみ呼ぶ。stop_all + スレッド join + PyAudio terminate（常駐モデル PR-3）。

        修正理由（Issue #38）:
          capture スレッドが pyaudiowpatch.read() を実行中に pa.terminate() を呼ぶと
          PortAudio が access violation でクラッシュする（実機ログ確認済み）。
          各 CaptionSystem.stop() で stop_event をセットした後、
          asyncio.run() を実行している _thread_a / _thread_b が終了するまで join してから
          共有 PyAudio を terminate することでクラッシュを防ぐ。
        """
        import time as _time
        _t0 = _time.monotonic()

        # 1. 全 route を停止（capture ループ脱出シグナル）
        #    CaptionSystem.stop() は shutdown() の thin wrapper であり、
        #    内部でスレッド join・_stop_event リセット・capture 停止を行う。
        if self._route_a is not None:
            _t1 = _time.monotonic()
            self._route_a.stop()
            print(f"[TIMING] MultiCaptionSystem._route_a.stop(): {_time.monotonic() - _t1:.3f}s", flush=True)

        if self._route_b is not None:
            _t1 = _time.monotonic()
            self._route_b.stop()
            print(f"[TIMING] MultiCaptionSystem._route_b.stop(): {_time.monotonic() - _t1:.3f}s", flush=True)

        # 2. asyncio.run() スレッドが終了するまで待つ（join with timeout）
        #    _thread_a/_thread_b は start_all() で生成される。start_all() 前に terminate() を
        #    呼んだ場合は None なのでスキップする。
        thread_a = getattr(self, "_thread_a", None)
        if thread_a is not None and thread_a.is_alive():
            _t1 = _time.monotonic()
            thread_a.join(timeout=5.0)
            print(f"[TIMING] MultiCaptionSystem._thread_a.join(): {_time.monotonic() - _t1:.3f}s", flush=True)
            if thread_a.is_alive():
                print("[WARN] route_a thread did not exit in 5 seconds", flush=True)

        thread_b = getattr(self, "_thread_b", None)
        if thread_b is not None and thread_b.is_alive():
            _t1 = _time.monotonic()
            thread_b.join(timeout=5.0)
            print(f"[TIMING] MultiCaptionSystem._thread_b.join(): {_time.monotonic() - _t1:.3f}s", flush=True)
            if thread_b.is_alive():
                print("[WARN] route_b thread did not exit in 5 seconds", flush=True)

        # 3. capture スレッドが確実に exit するまで待つ（pa.terminate() の前に必須）
        #    CaptionSystem.shutdown() で既に join 試行済みだが、pa.read() のブロック対策で
        #    ここでもう一度 join。timeout 10 秒で安全マージン。
        cap_a = getattr(self._route_a, "_capture_thread", None)
        if cap_a is not None and cap_a.is_alive():
            print("[INFO] waiting for route_a capture thread to exit...", flush=True)
            _t1 = _time.monotonic()
            cap_a.join(timeout=10.0)
            print(f"[TIMING] MultiCaptionSystem.cap_a.join(): {_time.monotonic() - _t1:.3f}s", flush=True)
            if cap_a.is_alive():
                print(
                    "[ERROR] route_a capture thread STILL ALIVE after 10s join."
                    " terminate may crash.",
                    flush=True,
                )
        cap_b = getattr(self._route_b, "_capture_thread", None)
        if cap_b is not None and cap_b.is_alive():
            print("[INFO] waiting for route_b capture thread to exit...", flush=True)
            _t1 = _time.monotonic()
            cap_b.join(timeout=10.0)
            print(f"[TIMING] MultiCaptionSystem.cap_b.join(): {_time.monotonic() - _t1:.3f}s", flush=True)
            if cap_b.is_alive():
                print(
                    "[ERROR] route_b capture thread STILL ALIVE after 10s join."
                    " terminate may crash.",
                    flush=True,
                )

        # 4. すべてのスレッドが exit してから共有 PyAudio を terminate
        #    getattr: object.__new__ で作られた minimal インスタンスには _pa が存在しない場合がある
        pa = getattr(self, "_pa", None)
        if pa is not None:
            _t1 = _time.monotonic()
            try:
                pa.terminate()
            except Exception as e:
                print(f"[WARN] PyAudio terminate failed: {e}", flush=True)
            print(f"[TIMING] MultiCaptionSystem.PyAudio.terminate(): {_time.monotonic() - _t1:.3f}s", flush=True)
            self._pa = None

        print(f"[TIMING] MultiCaptionSystem.terminate() TOTAL: {_time.monotonic() - _t0:.3f}s", flush=True)

    def start(self) -> None:
        """後方互換: start_all() の thin wrapper（常駐モデル PR-3）。"""
        self.start_all()

    def shutdown(self) -> None:
        """後方互換: terminate() の thin wrapper（常駐モデル PR-3）。"""
        self.terminate()

    @property
    def route_a_system(self) -> CaptionSystem:
        """経路Aの CaptionSystem インスタンス。"""
        return self._route_a

    @property
    def route_b_system(self) -> CaptionSystem:
        """経路Bの CaptionSystem インスタンス。"""
        return self._route_b

    @property
    def total_estimated_cost_usd(self) -> float:
        """存在する系統の CostMonitor の合算コスト（USD）を返す。

        仕様書 §4: CostMonitor 自体は変更せず、呼び出し側で2インスタンス管理。
        None の系統はスキップする（Issue #43 Optional route 対応）。
        """
        cost = 0.0
        if self._route_a is not None and self._route_a._cost_monitor is not None:
            cost += self._route_a._cost_monitor.estimated_cost_usd()
        if self._route_b is not None and self._route_b._cost_monitor is not None:
            cost += self._route_b._cost_monitor.estimated_cost_usd()
        return cost


if __name__ == "__main__":
    main()
