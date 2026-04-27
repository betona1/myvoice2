"""
tts/diarizer.py
화자 분리 (Speaker Diarization) 모듈
- speechbrain 기반 화자 감지 (토큰 불필요)
- TTS 클로닝에 최적화된 고품질 구간 선별
- 화자별 음성 구간 추출 및 합치기
"""

import os
import subprocess
import numpy as np
import soundfile as sf
import torch
import torchaudio
from pathlib import Path
from typing import List, Dict
from sklearn.cluster import AgglomerativeClustering

# torchaudio 호환성 패치 (speechbrain이 list_audio_backends를 요구)
if not hasattr(torchaudio, 'list_audio_backends'):
    torchaudio.list_audio_backends = lambda: ["soundfile"]
if not hasattr(torchaudio, 'get_audio_backend'):
    torchaudio.get_audio_backend = lambda: "soundfile"
if not hasattr(torchaudio, 'set_audio_backend'):
    torchaudio.set_audio_backend = lambda x: None

# speechbrain 모델 캐시 (재사용)
_classifier = None


def _get_classifier():
    """speechbrain 스피커 임베딩 모델 (싱글톤)"""
    global _classifier
    if _classifier is None:
        from speechbrain.inference.speaker import EncoderClassifier
        _classifier = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            run_opts={"device": "cuda" if torch.cuda.is_available() else "cpu"},
        )
    return _classifier


def _get_embeddings(audio: np.ndarray, sr: int, window_sec: float = 2.0, step_sec: float = 0.5):
    """
    speechbrain으로 오디오 구간별 스피커 임베딩 추출
    - window 2초, step 0.5초로 더 세밀하게 분석
    - 에너지 + 음성 활동 기반 필터링
    """
    classifier = _get_classifier()

    window_samples = int(window_sec * sr)
    step_samples = int(step_sec * sr)
    embeddings = []
    timestamps = []
    energies = []

    for start in range(0, len(audio) - window_samples, step_samples):
        end = start + window_samples
        chunk = audio[start:end]

        # RMS 에너지 계산
        energy = np.sqrt(np.mean(chunk ** 2))

        # 무음 구간 스킵 (임계값 낮춤 - 더 많은 구간 포착)
        if energy < 0.005:
            continue

        # 제로크로싱률 (음성 vs 잡음 구분)
        zcr = np.sum(np.abs(np.diff(np.sign(chunk)))) / (2 * len(chunk))
        # 순수 잡음은 제로크로싱이 매우 높음 (0.3 이상)
        if zcr > 0.3:
            continue

        waveform = torch.FloatTensor(chunk).unsqueeze(0)
        with torch.no_grad():
            emb = classifier.encode_batch(waveform)
        embeddings.append(emb.squeeze().cpu().numpy())
        timestamps.append({"start": round(start / sr, 3), "end": round(end / sr, 3)})
        energies.append(energy)

    return np.array(embeddings), timestamps, energies


def diarize_audio(
    audio_path: str,
    hf_token: str = "",
    num_speakers: int = None,
    min_speakers: int = 2,
    max_speakers: int = 5,
) -> List[Dict]:
    """
    오디오 파일에서 화자 분리 수행 (speechbrain 기반, 토큰 불필요)
    TTS 클로닝에 적합한 깨끗한 구간을 선별하여 반환
    """
    print(f"[DIARIZE] 화자 분리 시작: {audio_path}")

    # 오디오 로드
    audio, sr = sf.read(audio_path, dtype="float32")
    if audio.ndim > 1:
        audio = np.mean(audio, axis=1)

    # 1. 구간별 스피커 임베딩 추출
    print("[DIARIZE] 스피커 임베딩 추출 중...")
    embeddings, timestamps, energies = _get_embeddings(audio, sr)

    if len(embeddings) < 2:
        raise ValueError("오디오가 너무 짧거나 음성이 감지되지 않습니다")

    print(f"[DIARIZE] {len(embeddings)}개 음성 구간 감지됨")

    # 2. 클러스터링으로 화자 분리
    print("[DIARIZE] 클러스터링 중...")
    if num_speakers:
        n_clusters = num_speakers
    else:
        from sklearn.metrics import silhouette_score
        best_score = -1
        n_clusters = 2
        for k in range(min_speakers, min(max_speakers + 1, len(embeddings))):
            clustering = AgglomerativeClustering(n_clusters=k, metric="cosine", linkage="average")
            labels = clustering.fit_predict(embeddings)
            if len(set(labels)) < 2:
                continue
            score = silhouette_score(embeddings, labels, metric="cosine")
            print(f"  k={k}, silhouette={score:.3f}")
            if score > best_score:
                best_score = score
                n_clusters = k

    clustering = AgglomerativeClustering(n_clusters=n_clusters, metric="cosine", linkage="average")
    labels = clustering.fit_predict(embeddings)

    # 3. 라벨 → 화자별 세그먼트 수집 (에너지 정보 포함)
    speakers_raw = {}
    for i, label in enumerate(labels):
        spk = f"SPEAKER_{label:02d}"
        if spk not in speakers_raw:
            speakers_raw[spk] = []
        speakers_raw[spk].append({
            **timestamps[i],
            "energy": energies[i],
        })

    # 4. 인접 세그먼트 병합 + 에너지 평균 유지
    speakers = {}
    for spk, segs in speakers_raw.items():
        segs.sort(key=lambda s: s["start"])
        merged = [{**segs[0]}]
        for seg in segs[1:]:
            last = merged[-1]
            if seg["start"] - last["end"] < 1.0:  # 1초 이내면 병합
                last["end"] = seg["end"]
                last["energy"] = (last["energy"] + seg["energy"]) / 2
            else:
                merged.append({**seg})
        speakers[spk] = merged

    # 5. TTS용 고품질 구간 선별
    result = []
    for i, (speaker, segments) in enumerate(sorted(speakers.items())):
        # 최소 4초 이상인 구간만 (짧은 "어?", "응" 등 제거)
        good_segments = [s for s in segments if (s["end"] - s["start"]) >= 4.0]

        # 긴 문장 순으로 정렬 (가장 긴 구간부터)
        good_segments.sort(key=lambda s: s["end"] - s["start"], reverse=True)

        # 90초까지 긴 문장부터 채우기
        tts_segments = []
        tts_duration = 0.0
        target_duration = 90.0
        min_duration = 30.0

        for seg in good_segments:
            dur = seg["end"] - seg["start"]
            if tts_duration + dur > target_duration:
                remaining = target_duration - tts_duration
                if remaining >= 3.0:
                    tts_segments.append({
                        "start": seg["start"],
                        "end": round(seg["start"] + remaining, 3),
                    })
                    tts_duration += remaining
                break
            tts_segments.append({"start": seg["start"], "end": seg["end"]})
            tts_duration += dur

        # 순서 상관없이 그대로 (긴 문장 순 유지)

        # 전체 세그먼트 (에너지 제거하여 반환)
        all_segments = [{"start": s["start"], "end": s["end"]} for s in segments]
        total_duration = sum(s["end"] - s["start"] for s in all_segments)

        result.append({
            "speaker": speaker,
            "label": f"화자 {i + 1}",
            "segments": tts_segments,          # TTS용 선별 구간
            "all_segments": all_segments,       # 전체 구간
            "total_duration": round(total_duration, 2),
            "tts_duration": round(tts_duration, 2),
            "segment_count": len(tts_segments),
            "quality": "good" if tts_duration >= min_duration else "short",
        })

    print(f"[DIARIZE] 완료! {len(result)}명의 화자 감지됨")
    for r in result:
        q = "OK" if r["quality"] == "good" else "짧음"
        print(f"  - {r['label']}: 전체 {r['total_duration']}초, TTS용 {r['tts_duration']}초 ({r['segment_count']}개 구간) [{q}]")

    return result


def extract_speaker_audio(
    audio_path: str,
    segments: List[Dict],
    output_path: str,
    crossfade_ms: int = 50,
) -> str:
    """화자의 세그먼트들을 추출하여 하나의 WAV 파일로 합치기"""
    audio, sr = sf.read(audio_path, dtype="float32")
    if audio.ndim > 1:
        audio = np.mean(audio, axis=1)

    extracted = []
    fade_samples = int(sr * crossfade_ms / 1000)

    for seg in segments:
        start_sample = int(seg["start"] * sr)
        end_sample = int(seg["end"] * sr)
        chunk = audio[start_sample:end_sample].copy()

        if len(chunk) < fade_samples * 2:
            extracted.append(chunk)
            continue

        fade_in = np.linspace(0, 1, fade_samples, dtype=np.float32)
        fade_out = np.linspace(1, 0, fade_samples, dtype=np.float32)
        chunk[:fade_samples] *= fade_in
        chunk[-fade_samples:] *= fade_out
        extracted.append(chunk)

    if not extracted:
        raise ValueError("추출할 음성 구간이 없습니다")

    silence = np.zeros(int(0.15 * sr), dtype=np.float32)
    combined = extracted[0]
    for chunk in extracted[1:]:
        combined = np.concatenate([combined, silence, chunk])

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # 24000Hz로 리샘플링 + 전처리 (XTTS v2 기본)
    if sr != 24000:
        temp_path = output_path + ".temp.wav"
        sf.write(temp_path, combined, sr)
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-i", temp_path,
                    "-ar", "24000", "-ac", "1",
                    "-af", "highpass=f=80,lowpass=f=8000,loudnorm=I=-16:TP=-1.5:LRA=11",
                    output_path,
                ],
                capture_output=True, check=True,
            )
            Path(temp_path).unlink(missing_ok=True)
        except subprocess.CalledProcessError:
            Path(temp_path).replace(output_path)
    else:
        sf.write(output_path, combined, 24000)

    duration = len(combined) / sr
    print(f"[EXTRACT] 화자 음성 추출 완료: {output_path} ({duration:.1f}초)")
    return output_path


def generate_speaker_preview(
    audio_path: str,
    segments: List[Dict],
    output_path: str,
    max_duration: float = 90.0,
) -> str:
    """화자 미리듣기용 오디오 생성 (TTS 선별 구간 전체, 최대 90초)"""
    selected = []
    total = 0.0
    for seg in segments:
        dur = seg["end"] - seg["start"]
        if total + dur > max_duration:
            remaining = max_duration - total
            if remaining > 1.0:
                selected.append({"start": seg["start"], "end": round(seg["start"] + remaining, 3)})
                total += remaining
            break
        selected.append(seg)
        total += dur

    if not selected:
        selected = [segments[0]]

    return extract_speaker_audio(audio_path, selected, output_path, crossfade_ms=30)


def extract_all_speaker_audio(
    audio_path: str,
    segments: List[Dict],
    output_path: str,
) -> str:
    """화자의 전체 세그먼트를 추출 (길이 제한 없음)"""
    return extract_speaker_audio(audio_path, segments, output_path, crossfade_ms=50)
