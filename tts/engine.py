"""
tts/engine.py
Coqui XTTS v2 TTS 엔진 래퍼 (개선 버전)
- 참조 음성 전처리 적용
- 한국어 텍스트 정규화 적용
- 청크별 합성 (250자 버그 회피, 70자 분할로 러시아어 전환 방지)
- GPU 자동 감지
"""

import os
import glob
import torch
import numpy as np
import soundfile as sf
import librosa
from pathlib import Path
from scipy import signal as scipy_signal


def _patch_torchaudio():
    """torchaudio.load/save를 soundfile 기반으로 패치 (torchcodec 미지원 Windows 우회)"""
    import torchaudio

    _original_load = torchaudio.load

    def patched_load(filepath, *args, **kwargs):
        """soundfile로 오디오 로드 후 torch tensor로 변환"""
        try:
            return _original_load(filepath, *args, **kwargs)
        except (ImportError, RuntimeError):
            data, sample_rate = sf.read(str(filepath), dtype="float32")
            if data.ndim == 1:
                data = data[np.newaxis, :]  # (samples,) -> (1, samples)
            else:
                data = data.T  # (samples, channels) -> (channels, samples)
            return torch.from_numpy(data), sample_rate

    torchaudio.load = patched_load


# torchaudio 패치 적용
_patch_torchaudio()

os.environ["COQUI_TOS_AGREED"] = "1"
from TTS.api import TTS
from tts.audio_preprocessor import preprocess_reference_audio
from tts.korean_normalizer import normalize_korean_text, split_text_for_xtts


# XTTS v2 최적 파라미터 (한국어 기준 - v2 업그레이드)
XTTS_KOREAN_PARAMS = {
    "temperature": 0.65,        # 자연스러운 음색 (0.55→0.65)
    "repetition_penalty": 10.0, # 반복 방지
    "top_p": 0.85,
    "top_k": 50,
    "speed": 1.0,               # 속도 고정
    "length_penalty": 1.0,
    "gpt_cond_len": 24,         # 24 유지 (30 이상은 러시아어 유발)
    "gpt_cond_chunk_len": 4,    # 청킹으로 안정성 향상
    "max_ref_length": 30,       # 30초 유지 (안정성)
}

# 부모님(나이든 목소리) 효과 파라미터
XTTS_ELDERLY_PARAMS = {
    **XTTS_KOREAN_PARAMS,
    "temperature": 0.5,         # 더 안정적 (떨림 효과)
    "speed": 0.92,              # 약간 느리게
    "gpt_cond_len": 30,         # 참조 길게 → 뭉개짐 효과
    "max_ref_length": 60,
}

# 톤/효과 프리셋 (기본 파라미터에 오버라이드)
# pitch_shift: 반음 단위 (음수=낮게, 양수=높게). XTTS 파라미터가 아닌 후처리용
TONE_PRESETS = {
    "normal": {},  # 기본
    "fast": {"speed": 1.2},
    "slow": {"speed": 0.85},
    "bright": {"temperature": 0.7, "top_p": 0.9},       # 밝은톤 (변화 많게)
    "sad": {"temperature": 0.4, "speed": 0.92, "pitch_shift": -0.5},   # 우울한톤 (차분하게)
    "polite": {"temperature": 0.4, "speed": 0.95},                     # 정중한톤 (안정적)
    "elderly": {"temperature": 0.48, "speed": 0.94, "pitch_shift": -0.5}, # 부모님 (40~50대)
    "senior": {"temperature": 0.45, "speed": 0.95, "pitch_shift": -1.3}, # 시니어 (50~60대)
    "child": {"pitch_shift": 3.0, "speed": 1.08, "temperature": 0.7, "is_child": True},  # 어린이 9살 (피치+3반음)
    "child_calm": {"pitch_shift": 2.0, "speed": 1.0, "temperature": 0.6, "is_child": True},  # 어린이 차분
}


class TTSEngine:
    """XTTS v2 TTS 엔진 (개선 버전)"""

    def __init__(self):
        # GPU 자동 감지 (CUDA 없으면 CPU 사용)
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"[DEVICE] TTS 엔진 디바이스: {self.device}")

        # XTTS v2 모델 로드 (첫 실행 시 자동 다운로드 ~2GB)
        print("[LOAD] TTS 모델 로딩 중...")
        self.tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(self.device)
        print("[OK] TTS 모델 준비 완료!")

        # 전처리된 참조 음성 캐시
        self._ref_cache = {}
        # 스피커 임베딩 캐시 (다중 참조용)
        self._latent_cache = {}

    def _get_preprocessed_ref(self, speaker_wav: str) -> str:
        """참조 음성 전처리 (캐시 적용)"""
        if speaker_wav in self._ref_cache:
            cache_path = self._ref_cache[speaker_wav]
            if os.path.exists(cache_path):
                return cache_path

        # _optimal 또는 _combined 파일은 이미 최적화됨 → 전처리 스킵
        if "_optimal" in speaker_wav or "_combined" in speaker_wav:
            print(f"[전처리] 최적화 파일 — 전처리 스킵: {speaker_wav}")
            self._ref_cache[speaker_wav] = speaker_wav
            return speaker_wav

        # 전처리된 파일 경로
        base, ext = os.path.splitext(speaker_wav)
        cache_path = f"{base}_xtts_ready{ext}"

        if not os.path.exists(cache_path):
            try:
                cache_path = preprocess_reference_audio(speaker_wav, cache_path)
            except ValueError as e:
                # 전처리 실패 시 원본 사용
                print(f"[경고] 참조 음성 전처리 실패, 원본 사용: {e}")
                cache_path = speaker_wav

        self._ref_cache[speaker_wav] = cache_path
        return cache_path

    def _find_multi_refs(self, speaker_wav: str) -> list:
        """
        다중 참조 음성 탐색
        같은 음색의 파일만 사용 (파일명 기반 매칭)
        예: "내목소리_abc123.wav" → "내목소리_" 로 시작하는 파일만
        """
        ref_path = Path(speaker_wav)
        ref_dir = ref_path.parent
        ref_stem = ref_path.stem.replace("_xtts_ready", "")

        # 음색 이름 추출 (첫 번째 _ 앞의 이름, 또는 UUID 제외 부분)
        # 파일명 패턴: "이름_uuid.wav" 또는 "uuid_이름.wav"
        parts = ref_stem.split("_")
        if len(parts) < 2:
            return [speaker_wav]

        # 이름 부분 추출 (UUID가 아닌 부분)
        import re
        name_parts = [p for p in parts if not re.match(r'^[0-9a-f]{6,}$', p)]
        if not name_parts:
            return [speaker_wav]

        voice_name = name_parts[0]

        # 같은 이름으로 시작하는 파일만 탐색
        candidates = []
        for f in ref_dir.glob("*.wav"):
            if "_xtts_ready" in f.name:
                continue
            if str(f) == speaker_wav:
                candidates.append(str(f))
                continue
            # 같은 음색 이름이 포함된 파일만
            if voice_name in f.stem and voice_name != "":
                candidates.append(str(f))

        if len(candidates) <= 1:
            return [speaker_wav]

        print(f"[TTS] 동일 음색 '{voice_name}' 파일 {len(candidates)}개 발견")

        # 전처리 후 반환 (최대 5개)
        refs = []
        for c in candidates[:5]:
            try:
                processed = self._get_preprocessed_ref(c)
                refs.append(processed)
            except Exception:
                pass

        return refs if refs else [speaker_wav]

    def _postprocess_audio(self, audio: np.ndarray, pitch_shift: int = 0, is_child: bool = False,
                           bass_boost_db: float = 2.0, bass_boost_freq: float = 200.0) -> np.ndarray:
        """
        출력 음성 후처리 (v6 - 어린이 음색 지원)
        - DC 오프셋 제거
        - 피치 시프트 (반음 단위, 음수=낮게)
        - 어린이: 고음 부스트 (3kHz, +3dB) + 저음 컷 (150Hz)
        - 성인: 저음 부스트 (200Hz, +4dB) — 중저음 풍부하게
        - 볼륨 정규화 (-3dBFS)
        """
        # DC 오프셋 제거
        audio = audio - np.mean(audio)

        sr = 24000

        # 피치 시프트 (리샘플링 방식 — 위상 보코더 아티팩트 없음)
        if pitch_shift != 0:
            ratio = 2 ** (-pitch_shift / 12)
            new_length = int(len(audio) * ratio)
            audio = np.interp(
                np.linspace(0, len(audio) - 1, new_length),
                np.arange(len(audio)),
                audio
            ).astype(np.float32)

        nyq = sr / 2

        if is_child:
            # 어린이: 고음 부스트 (3kHz, +3dB) — 밝고 가벼운 음색
            w0 = 2 * np.pi * 3000 / sr
            Q = 1.0
            alpha = np.sin(w0) / (2 * Q)
            A = 10 ** (3 / 40)  # +3dB
            b0 = 1 + alpha * A
            b1 = -2 * np.cos(w0)
            b2 = 1 - alpha * A
            a0 = 1 + alpha / A
            a1 = -2 * np.cos(w0)
            a2 = 1 - alpha / A
            b_eq = np.array([b0/a0, b1/a0, b2/a0])
            a_eq = np.array([1, a1/a0, a2/a0])
            audio = scipy_signal.filtfilt(b_eq, a_eq, audio).astype(np.float32)
        else:
            # 성인 음색: 저음 부스트 (조절 가능)
            if abs(bass_boost_db) < 0.1:
                A = None  # EQ 스킵
            else:
                w0 = 2 * np.pi * bass_boost_freq / sr
                Q = 0.7
                alpha = np.sin(w0) / (2 * Q)
                A = 10 ** (bass_boost_db / 40)
            if A is not None:
                b0 = 1 + alpha * A
                b1 = -2 * np.cos(w0)
                b2 = 1 - alpha * A
                a0 = 1 + alpha / A
                a1 = -2 * np.cos(w0)
                a2 = 1 - alpha / A
                b_eq = np.array([b0/a0, b1/a0, b2/a0])
                a_eq = np.array([1, a1/a0, a2/a0])
                audio = scipy_signal.filtfilt(b_eq, a_eq, audio).astype(np.float32)

        # 볼륨 정규화 (-3dBFS = 0.7)
        peak = np.max(np.abs(audio))
        if peak > 0:
            audio = audio / peak * 0.7

        return audio.astype(np.float32)

    def _get_speaker_latents(self, speaker_wav: str, params: dict = None):
        """
        스피커 임베딩 추출 (다중 참조 지원)
        같은 폴더 내 여러 WAV가 있으면 모두 사용하여 더 정확한 음색 추출
        """
        p = params or XTTS_KOREAN_PARAMS

        # 단일 참조 사용 (다중 참조는 러시아어 유발 가능성 있어 비활성)
        ref_list = [speaker_wav]

        # 캐시 키
        cache_key = "|".join(sorted(ref_list)) + f"|{p['gpt_cond_len']}"
        if cache_key in self._latent_cache:
            return self._latent_cache[cache_key]

        synthesizer = self.tts.synthesizer
        gpt_cond_latent, speaker_embedding = synthesizer.tts_model.get_conditioning_latents(
            audio_path=ref_list,
            gpt_cond_len=p["gpt_cond_len"],
            gpt_cond_chunk_len=p["gpt_cond_chunk_len"],
            max_ref_length=p["max_ref_length"],
        )

        self._latent_cache[cache_key] = (gpt_cond_latent, speaker_embedding)
        return gpt_cond_latent, speaker_embedding

    def _synthesize_chunk(
        self,
        text: str,
        gpt_cond_latent,
        speaker_embedding,
        language: str = "ko",
        params: dict = None,
    ) -> np.ndarray:
        """단일 청크 합성 (사전 추출된 스피커 임베딩 사용)"""
        p = params or XTTS_KOREAN_PARAMS
        synthesizer = self.tts.synthesizer

        # TTS 생성 (동일한 스피커 임베딩으로 일관된 속도/음질)
        result = synthesizer.tts_model.inference(
            text=text,
            language=language,
            gpt_cond_latent=gpt_cond_latent,
            speaker_embedding=speaker_embedding,
            temperature=p["temperature"],
            length_penalty=p["length_penalty"],
            repetition_penalty=p["repetition_penalty"],
            top_k=p["top_k"],
            top_p=p["top_p"],
            speed=p["speed"],
            enable_text_splitting=False,
        )

        wav = np.array(result["wav"], dtype=np.float32)

        # 뒷부분 무음/잡음만 가볍게 제거 (음성 보존 우선)
        wav = self._trim_trailing_noise(wav)

        return wav

    def _trim_trailing_noise(self, audio: np.ndarray, frame_ms: int = 20) -> np.ndarray:
        """
        오디오 뒷부분의 무음만 제거 (음성 보존 우선)
        - 에너지 기반 무음 감지 (보수적)
        - 음성 끝 이후 0.4초 여유
        """
        frame_size = int(24000 * frame_ms / 1000)  # 20ms 프레임
        if len(audio) < frame_size * 3:
            return audio

        # 프레임별 에너지 계산
        n_frames = len(audio) // frame_size
        energies = []
        for i in range(n_frames):
            frame = audio[i * frame_size:(i + 1) * frame_size]
            energies.append(np.sqrt(np.mean(frame ** 2)))

        if not energies:
            return audio

        # 보수적 임계치 — 상위 에너지의 5% (0.12→0.05)
        sorted_e = sorted(energies, reverse=True)
        top_energy = np.mean(sorted_e[:max(1, len(sorted_e)//4)])
        silence_threshold = top_energy * 0.05

        # 뒤에서부터 연속 무음 프레임 찾기
        last_voice_frame = n_frames - 1
        for i in range(n_frames - 1, -1, -1):
            if energies[i] >= silence_threshold:
                last_voice_frame = i
                break

        # 마지막 음성 프레임 + 0.4초 여유 (0.15→0.4초)
        cut_sample = min(len(audio), (last_voice_frame + 1) * frame_size + int(0.4 * 24000))
        return audio[:cut_sample]

    def generate_single_line(
        self,
        text: str,
        speaker_wav: str,
        output_path: str,
        language: str = "ko",
        tone: str = "normal",
        custom_params: dict = None,
    ) -> str:
        """
        단일 라인 TTS 생성 (미리듣기/재생성용)
        - tone: normal, fast, slow, bright, sad, polite, elderly
        - custom_params: 커스텀 파라미터 (제공 시 tone 무시)
        """
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        # 파라미터 구성
        params = {**XTTS_KOREAN_PARAMS}
        if custom_params:
            # 커스텀 파라미터 사용 (톤 프리셋 무시)
            params.update(custom_params)
        else:
            # 톤 프리셋 사용 (콤마로 다중 효과 지원)
            for t in tone.split(","):
                t = t.strip()
                if t in TONE_PRESETS:
                    params.update(TONE_PRESETS[t])

        # 텍스트 정규화
        if language == "ko":
            text = normalize_korean_text(text)

        # 청크 분할
        chunks = split_text_for_xtts(text, max_chars=70)
        if not chunks:
            raise ValueError("텍스트가 비어있습니다.")

        # 참조 음성 + 스피커 임베딩
        speaker_wav_clean = self._get_preprocessed_ref(speaker_wav)
        gpt_cond_latent, speaker_embedding = self._get_speaker_latents(speaker_wav_clean, params)

        # 청크별 합성
        audio_parts = []
        for chunk in chunks:
            try:
                wav = self._synthesize_chunk(
                    text=chunk,
                    gpt_cond_latent=gpt_cond_latent,
                    speaker_embedding=speaker_embedding,
                    language=language,
                    params=params,
                )
                audio_parts.append(wav)
            except Exception as e:
                print(f"[TTS 경고] 합성 실패: {e}")
                audio_parts.append(np.zeros(int(0.3 * 24000), dtype=np.float32))

        # 청크 연결 (0.1초 무음)
        silence = np.zeros(int(0.1 * 24000), dtype=np.float32)
        audio = audio_parts[0]
        for part in audio_parts[1:]:
            audio = np.concatenate([audio, silence, part])

        pitch = params.get("pitch_shift", 0)
        is_child = params.get("is_child", False)
        bass_db = params.get("bass_boost_db", 2.0)
        bass_freq = params.get("bass_boost_freq", 200.0)
        audio = self._postprocess_audio(audio, pitch_shift=pitch, is_child=is_child,
                                        bass_boost_db=bass_db, bass_boost_freq=bass_freq)
        sf.write(output_path, audio, 24000)
        return output_path

    def generate(
        self,
        text: str,
        speaker_wav: str,
        output_path: str,
        language: str = "ko",
        elderly_mode: bool = False,
    ) -> str:
        """
        텍스트 -> 목소리 클로닝 TTS 생성 (개선 버전)
        - 텍스트 정규화 (외국어 혼입 방지)
        - 150자 이하로 분할 (250자 버그 회피)
        - 청크별 합성 후 이어붙이기
        - 참조 음성 전처리 적용
        - elderly_mode: 부모님 효과 (나이든 음색)
        """
        params = XTTS_ELDERLY_PARAMS if elderly_mode else XTTS_KOREAN_PARAMS
        if elderly_mode:
            print("[TTS] 부모님 효과 모드 활성화")

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        # 1. 엔터(줄바꿈) 기준으로 라인 분리 (각 라인을 독립적으로 합성)
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        if not lines:
            raise ValueError("입력 텍스트가 비어있습니다.")

        print(f"[TTS] {len(lines)}개 라인 감지")

        # 2. 참조 음성 전처리 (캐시)
        speaker_wav_clean = self._get_preprocessed_ref(speaker_wav)

        # 3. 스피커 임베딩 한 번만 추출 (모든 청크에 동일 적용)
        gpt_cond_latent, speaker_embedding = self._get_speaker_latents(speaker_wav_clean, params)

        # 4. 라인별 → 청크별 합성
        all_audio_parts = []
        total_chunks = 0

        for line_idx, line in enumerate(lines):
            # 라인별 정규화
            if language == "ko":
                line = normalize_korean_text(line)

            # 라인 내 청크 분할 (70자 이하)
            line_chunks = split_text_for_xtts(line, max_chars=70)
            if not line_chunks:
                continue

            total_chunks += len(line_chunks)
            print(f"[TTS] 라인 {line_idx+1}/{len(lines)}: {line[:50]}... ({len(line_chunks)}청크)")

            # 청크별 합성
            line_audio_parts = []
            for i, chunk in enumerate(line_chunks):
                print(f"[TTS]   청크 ({i+1}/{len(line_chunks)}): {chunk[:40]}...")
                try:
                    wav = self._synthesize_chunk(
                        text=chunk,
                        gpt_cond_latent=gpt_cond_latent,
                        speaker_embedding=speaker_embedding,
                        language=language,
                        params=params,
                    )
                    line_audio_parts.append(wav)
                except Exception as e:
                    print(f"[TTS 경고] 합성 실패: {e}")
                    silence = np.zeros(int(0.3 * 24000), dtype=np.float32)
                    line_audio_parts.append(silence)

            # 같은 라인 내 청크는 짧은 무음(0.1초)으로 연결
            if line_audio_parts:
                silence_inner = np.zeros(int(0.1 * 24000), dtype=np.float32)
                line_audio = line_audio_parts[0]
                for part in line_audio_parts[1:]:
                    line_audio = np.concatenate([line_audio, silence_inner, part])
                all_audio_parts.append(line_audio)

        if not all_audio_parts:
            raise ValueError("합성할 텍스트가 없습니다.")

        print(f"[TTS] 총 {total_chunks}개 청크 합성 완료, 라인 간 연결 중...")

        # 5. 라인 사이 무음 삽입 (0.2초 - 엔터 구분 느낌)
        silence_between_lines = np.zeros(int(0.2 * 24000), dtype=np.float32)
        final_audio = all_audio_parts[0]
        for part in all_audio_parts[1:]:
            final_audio = np.concatenate([final_audio, silence_between_lines, part])

        # 6. 출력 후처리 (볼륨 정규화 + 피치 시프트)
        pitch = params.get("pitch_shift", -2 if elderly_mode else 0)
        final_audio = self._postprocess_audio(final_audio, pitch_shift=pitch)

        # 7. WAV 저장 (24000Hz)
        sf.write(output_path, final_audio, 24000)
        print(f"[OK] TTS 생성 완료: {output_path} ({len(final_audio)/24000:.1f}초)")

        return output_path


# 싱글톤 패턴 - 엔진 한 번만 로드
tts_engine = None


def get_engine() -> TTSEngine:
    """싱글톤 패턴 - 엔진 한 번만 로드"""
    global tts_engine
    if tts_engine is None:
        tts_engine = TTSEngine()
    return tts_engine
