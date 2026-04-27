"""
tts/audio_preprocessor.py
XTTS v2용 참조 음성 전처리 모듈 (v2 - 음질 업그레이드)
- 24000Hz 리샘플링
- 모노 변환
- 노이즈 제거 (spectral gating)
- 무음 구간 제거
- 볼륨 정규화
- 길이 제한 (6초~60초)
"""

import librosa
import soundfile as sf
import numpy as np
import os
from scipy import signal as scipy_signal


def _spectral_noise_gate(audio: np.ndarray, sr: int = 24000,
                          n_fft: int = 2048, noise_reduce_db: float = 12.0) -> np.ndarray:
    """
    스펙트럼 노이즈 게이트 (간이 버전)
    - 처음 0.5초를 노이즈 프로파일로 사용
    - 노이즈 수준 이하 주파수 성분 감쇠
    """
    noise_samples = min(int(0.5 * sr), len(audio) // 4)
    if noise_samples < sr * 0.1:
        return audio

    # STFT
    hop = n_fft // 4
    stft = librosa.stft(audio, n_fft=n_fft, hop_length=hop)
    mag, phase = np.abs(stft), np.angle(stft)

    # 노이즈 프로파일 (처음 0.5초의 평균 스펙트럼)
    noise_frames = noise_samples // hop
    noise_profile = np.mean(mag[:, :max(1, noise_frames)], axis=1, keepdims=True)

    # 감쇠 비율
    ratio = 10 ** (noise_reduce_db / 20)
    threshold = noise_profile * ratio

    # 소프트 마스크 (부드럽게 감쇠)
    mask = np.clip((mag - noise_profile) / (threshold - noise_profile + 1e-8), 0, 1)
    mag_clean = mag * mask

    # ISTFT
    stft_clean = mag_clean * np.exp(1j * phase)
    audio_clean = librosa.istft(stft_clean, hop_length=hop, length=len(audio))

    return audio_clean.astype(np.float32)


def _apply_eq(audio: np.ndarray, sr: int = 24000) -> np.ndarray:
    """
    음성 최적화 EQ
    - 하이패스 필터 80Hz (저주파 험 제거)
    - 프레즌스 부스트 2~4kHz (명료도 향상)
    """
    # 80Hz 하이패스 (2차 버터워스)
    nyq = sr / 2
    b, a = scipy_signal.butter(2, 80 / nyq, btype='high')
    audio = scipy_signal.filtfilt(b, a, audio).astype(np.float32)

    # 2.5kHz 프레즌스 부스트 (+3dB 피킹 EQ)
    center_freq = 2500
    Q = 1.5
    w0 = 2 * np.pi * center_freq / sr
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

    return audio


def preprocess_reference_audio(input_path: str, output_path: str = None) -> str:
    """
    XTTS v2용 참조 음성 전처리 (v3 - 원음 최대 보존)
    - 24000Hz 리샘플링
    - 모노 변환
    - 길이 제한 (최소 6초, 최대 30초)
    원음의 음색/다이나믹스를 훼손하지 않도록 최소한만 처리
    """
    if output_path is None:
        base, ext = os.path.splitext(input_path)
        output_path = f"{base}_xtts_ready{ext}"

    # 로드 (스테레오면 모노로)
    audio, sr = librosa.load(input_path, sr=None, mono=True)

    # 24000Hz 리샘플링
    if sr != 24000:
        audio = librosa.resample(audio, orig_sr=sr, target_sr=24000)
        sr = 24000

    # 앞뒤 무음만 가볍게 제거 (top_db=25: 아주 조용한 부분만)
    audio, _ = librosa.effects.trim(audio, top_db=25)

    # 볼륨 정규화 (-1dBFS = 0.89) — 모델이 음성 특성을 강하게 인식하도록
    peak = np.max(np.abs(audio))
    if peak > 0:
        audio = audio / peak * 0.89

    # 길이 제한: 최소 6초, 최대 30초
    min_samples = 6 * 24000
    max_samples = 30 * 24000
    if len(audio) < min_samples:
        raise ValueError(f"참조 음성이 너무 짧습니다. 최소 6초 이상 필요 (현재: {len(audio)/24000:.1f}초)")
    if len(audio) > max_samples:
        audio = audio[:max_samples]

    sf.write(output_path, audio, 24000, subtype='PCM_16')
    print(f"[전처리 완료] {output_path} ({len(audio)/24000:.1f}초)")
    return output_path
