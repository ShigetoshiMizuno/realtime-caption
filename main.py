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


_MODEL_BASE = _Path(__file__).parent.resolve() / "models"
_ascii_models, _subst_letter = _ensure_ascii_path(_MODEL_BASE)
os.environ.setdefault("HF_HOME", str(_ascii_models / "huggingface"))
os.environ.setdefault("TORCH_HOME", str(_ascii_models / "torch"))

import asyncio
import io
import json
import sys
import threading
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

# Windows コンソールの文字化け対策
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Silero VAD モデルを信頼済みリポジトリとして事前登録（初回確認をスキップ）
try:
    _hub_dir = _ascii_models / "torch" / "hub"
    torch.hub.set_dir(str(_hub_dir))
    torch.hub.load('snakers4/silero-vad', 'silero_vad', trust_repo=True, verbose=False)
except Exception:
    pass

# RealtimeSTT が期待するサンプルレート
REALTIMESTT_SAMPLE_RATE = 16000


def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def list_audio_devices() -> list[dict]:
    """通常の入力デバイスとWASAPIループバックデバイスを両方リストアップする。"""
    pa = pyaudio.PyAudio()
    devices = []
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        is_loopback = info.get("isLoopbackDevice", False)
        if info["maxInputChannels"] > 0 or is_loopback:
            devices.append({
                "index": i,
                "name": info["name"],
                "isLoopback": is_loopback,
                "defaultSampleRate": info.get("defaultSampleRate", 44100),
                "maxInputChannels": info.get("maxInputChannels", 2),
            })
    pa.terminate()
    return devices


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
    """WebSocket サーバーで接続中の全クライアントに字幕を配信する。"""

    def __init__(self):
        self._clients: set = set()
        self._lock = asyncio.Lock()

    async def register(self, websocket):
        async with self._lock:
            self._clients.add(websocket)
        try:
            await websocket.wait_closed()
        finally:
            async with self._lock:
                self._clients.discard(websocket)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def broadcast(self, message: str):
        async with self._lock:
            targets = set(self._clients)
        if targets:
            await asyncio.gather(
                *[ws.send(message) for ws in targets],
                return_exceptions=True,
            )


_DEEPL_LANG_MAP = {
    "日本語": "JA", "japanese": "JA",
    "英語": "EN-US", "english": "EN-US",
    "中国語": "ZH", "chinese": "ZH",
    "韓国語": "KO", "korean": "KO",
    "ドイツ語": "DE", "german": "DE",
    "フランス語": "FR", "french": "FR",
}


class TranslationService:
    """翻訳サービス。config の translation_model に応じて OpenAI または DeepL を使う。"""

    def __init__(self, config: dict):
        trans_cfg = config.get("translation", {})
        self._target_language = trans_cfg.get("target_language", "日本語")
        model = trans_cfg.get("translation_model", "openai").lower()

        if model == "deepl":
            import deepl as _deepl
            self._deepl = _deepl.Translator(config["deepl"]["api_key"])
            self._deepl_target = _DEEPL_LANG_MAP.get(
                self._target_language.lower(), self._target_language.upper()
            )
            self._mode = "deepl"
        else:
            self._client = OpenAI(api_key=config["openai"]["api_key"])
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


class CaptionSystem:
    """文字起こし・翻訳・WebSocket 配信を統合管理するクラス。"""

    def __init__(self, config: dict, device_info: dict, model_name: str,
                 on_result=None, on_ready=None,
                 on_whisper_busy=None, on_trans_busy=None):
        self._config = config
        self._device_info = device_info
        self._model_name = model_name
        self._on_result = on_result          # callable(original: str, translated: str) | None
        self._on_ready = on_ready            # callable() | None — モデル準備完了時に呼ばれる
        self._on_whisper_busy = on_whisper_busy  # callable(bool) | None
        self._on_trans_busy = on_trans_busy      # callable(bool) | None

        self._translator = TranslationService(config)
        self._broadcaster = SubtitleBroadcaster()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._recorder: AudioToTextRecorder | None = None
        self._stop_event = threading.Event()
        self._stop_event_async: asyncio.Event | None = None
        log_dir = config.get("output", {}).get("log_dir", ".")
        self._log_path = self._make_log_path(Path(log_dir))
        # 直近1秒の音量メーター（0〜32767 の int16 peak）
        self.audio_peak: int = 0
        self.audio_chunks_per_sec: int = 0
        # リアルタイム表示用（チャンクごとに更新）
        self.audio_peak_now: int = 0
        # Gain 制御: "off" | "manual" | "auto"（実行中もスレッドセーフに変更可）
        self.gain_mode: str = "off"
        self.manual_gain: float = 1.0
        # AGC の状態
        self._agc_gain: float = 1.0
        self._agc_envelope: float = 0.0  # 直近の peak 追従値（減衰付き）
        self.effective_gain: float = 1.0  # 実際に適用された直近 gain（RPC で参照）

    def shutdown(self):
        self._stop_event.set()
        if self._recorder:
            try:
                self._recorder.stop()
            except Exception:
                pass
        if self._loop and self._stop_event_async:
            self._loop.call_soon_threadsafe(self._stop_event_async.set)
        # subst ドライブの解除はアプリ終了時のみ（app.py の main() / main.py の main() で実施）。
        # ここで呼ぶと Stop や プリロード破棄のたびにドライブが消え、後続のモデル参照で失敗する。

    @staticmethod
    def _make_log_path(log_dir: Path) -> Path:
        """YYYY-MM-DD-n_translate.txt 形式のログファイルパスを生成する。"""
        today = datetime.now().strftime("%Y-%m-%d")
        n = 1
        while True:
            path = log_dir / f"{today}-{n}_translate.txt"
            if not path.exists():
                return path
            n += 1

    def _on_transcription(self, text: str):
        """RealtimeSTT から文字起こし結果を受け取るコールバック。
        翻訳は asyncio のスレッドプールで非同期実行し、コールバックをすぐに返す。"""
        text = text.strip()
        if not text:
            return

        if self._on_whisper_busy:
            self._on_whisper_busy(False)

        print(f"\n[原文] {text}")

        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                self._translate_and_broadcast(text), self._loop
            )

    async def _translate_and_broadcast(self, text: str):
        """翻訳を asyncio スレッドプールで実行し、WebSocket に配信する。"""
        if self._on_trans_busy:
            self._on_trans_busy(True)
        try:
            translated = await asyncio.to_thread(self._translator.translate, text)
        except Exception as e:
            print(f"[翻訳エラー] {e}")
            translated = ""
        finally:
            if self._on_trans_busy:
                self._on_trans_busy(False)

        if translated:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with self._log_path.open("a", encoding="utf-8") as f:
                f.write(f"[{ts}]\n原文: {text}\n翻訳: {translated}\n\n")

        if self._on_result and translated:
            self._on_result(text, translated)
        elif translated:
            print(f"[翻訳] {translated}")

        payload = json.dumps({"original": text, "translated": translated}, ensure_ascii=False)
        await self._broadcaster.broadcast(payload)

    def _loopback_capture_thread(self):
        """WASAPIループバックデバイスから音声をキャプチャし、recorder に feed する。"""
        device_index = self._device_info["index"]
        src_rate = int(self._device_info["defaultSampleRate"])
        channels = max(1, int(self._device_info["maxInputChannels"]))
        chunk_size = 1024

        # リサンプリング比率を既約分数で求める
        g = gcd(REALTIMESTT_SAMPLE_RATE, src_rate)
        up = REALTIMESTT_SAMPLE_RATE // g
        down = src_rate // g

        pa = pyaudio.PyAudio()
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
            print(f"[ERROR] ループバックストリームのオープンに失敗しました: {e}")
            pa.terminate()
            return

        print(f"[INFO] ループバックキャプチャ開始: {src_rate}Hz, {channels}ch -> {REALTIMESTT_SAMPLE_RATE}Hz mono", flush=True)

        # デバッグ用: 1秒ごとに音量レベルを出力
        import time as _time
        level_window_max = 0
        level_window_chunks = 0
        next_log = _time.time() + 1.0
        chunks_per_sec = max(1, src_rate // chunk_size)

        try:
            while not self._stop_event.is_set():
                raw = stream.read(chunk_size, exception_on_overflow=False)

                # bytes -> numpy int16 配列
                audio = np.frombuffer(raw, dtype=np.int16)

                # ステレオ（またはマルチチャンネル）→ モノラル変換
                if channels > 1:
                    audio = audio.reshape(-1, channels)
                    audio = audio.mean(axis=1)

                # --- Gain 処理 ---
                audio_f = audio.astype(np.float32)
                if self.gain_mode == "manual":
                    g = max(1.0, min(float(self.manual_gain), 20.0))
                    self.effective_gain = g
                    audio_f = audio_f * g
                elif self.gain_mode == "auto":
                    # AGC: 直近 peak を指数減衰で追跡、target=20000 (60%) に調整
                    local_peak = float(np.abs(audio_f).max()) if audio_f.size else 0.0
                    # envelope は減衰定数 0.995（約200ms のリリース時定数相当）
                    self._agc_envelope = max(local_peak, self._agc_envelope * 0.995)
                    if self._agc_envelope > 1.0:
                        target = 20000.0
                        desired = target / self._agc_envelope
                        desired = max(1.0, min(desired, 20.0))
                        # 急減は速く、増大は遅く（attack 0.3 / release 0.05）
                        alpha = 0.3 if desired < self._agc_gain else 0.05
                        self._agc_gain += (desired - self._agc_gain) * alpha
                    self.effective_gain = self._agc_gain
                    audio_f = audio_f * self._agc_gain
                else:
                    self.effective_gain = 1.0
                audio = audio_f
                # int16 範囲にクリップ（gain 適用後のオーバーフロー防止）
                audio = np.clip(audio, -32768, 32767)

                # 音量レベル監視（gain適用後の実効peak を採用）
                chunk_max = int(np.abs(audio).max()) if audio.size else 0
                # リアルタイム表示用: ピークホールド（減衰つき）
                self.audio_peak_now = max(chunk_max, int(self.audio_peak_now * 0.85))
                level_window_max = max(level_window_max, chunk_max)
                level_window_chunks += 1
                now = _time.time()
                if now >= next_log:
                    self.audio_peak = level_window_max
                    self.audio_chunks_per_sec = level_window_chunks
                    pct = level_window_max * 100 // 32767
                    bar = "█" * (pct // 5)
                    print(f"[AUDIO] peak={level_window_max:>5d} ({pct:3d}%) {bar} chunks={level_window_chunks}", flush=True)
                    level_window_max = 0
                    level_window_chunks = 0
                    next_log = now + 1.0

                # float32 に変換してリサンプリング
                audio_f = audio.astype(np.float32)
                resampled = resample_poly(audio_f, up, down)

                # int16 に戻して bytes に変換
                resampled_int16 = np.clip(resampled, -32768, 32767).astype(np.int16)
                pcm_bytes = resampled_int16.tobytes()

                if self._recorder is not None:
                    self._recorder.feed_audio(pcm_bytes)

        except Exception as e:
            print(f"[ERROR] ループバックキャプチャ中にエラーが発生しました: {e}")
        finally:
            try:
                stream.stop_stream()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
            pa.terminate()

    def prepare(self):
        """Whisper モデルをロードして recorder を初期化する。
        バックグラウンドスレッドから事前呼び出し可能。run() より前に呼ぶことで起動を高速化できる。"""
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
            print(f"[ERROR] AudioToTextRecorder の初期化に失敗しました: {e}")

    def _start_recorder(self):
        """別スレッドで録音ループを起動する。prepare() が未完了なら先に呼ぶ。"""
        if self._recorder is None:
            self.prepare()
        if self._recorder is None:
            return  # prepare 失敗

        is_loopback = self._device_info.get("isLoopback", False)
        if is_loopback:
            capture_thread = threading.Thread(target=self._loopback_capture_thread, daemon=True)
            capture_thread.start()

        print("\n[INFO] 録音を開始しました。\n")
        if self._on_ready:
            self._on_ready()
        try:
            while not self._stop_event.is_set():
                self._recorder.text(self._on_transcription)
        except Exception as e:
            if not self._stop_event.is_set():
                print(f"[ERROR] 録音中にエラーが発生しました: {e}")

    async def run(self):
        """WebSocket サーバーを起動し、録音スレッドを開始する。"""
        self._loop = asyncio.get_running_loop()
        self._stop_event_async = asyncio.Event()

        ws_host = self._config["websocket"]["host"]
        ws_port = self._config["websocket"]["port"]

        recorder_thread = threading.Thread(target=self._start_recorder, daemon=True)
        recorder_thread.start()

        print(f"[INFO] WebSocket サーバーを起動中: ws://{ws_host}:{ws_port}")
        print(f"[INFO] ログファイル: {self._log_path}")

        try:
            async with websockets.serve(self._broadcaster.register, ws_host, ws_port):
                await self._stop_event_async.wait()
        except asyncio.CancelledError:
            pass
        finally:
            self.shutdown()
            print("[INFO] 終了しました。")


def main():
    config = load_config("config.yaml")

    api_key = config.get("openai", {}).get("api_key", "")
    if not api_key or api_key == "your-api-key-here":
        print("[ERROR] config.yaml に OpenAI API キーを設定してください。")
        sys.exit(1)

    device_info = select_audio_device()
    model_name = select_whisper_model(config["whisper"]["model"])

    system = CaptionSystem(config, device_info, model_name)

    try:
        asyncio.run(system.run())
    except KeyboardInterrupt:
        pass
    finally:
        _release_subst(_subst_letter)


if __name__ == "__main__":
    main()
