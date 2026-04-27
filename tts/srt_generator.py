"""
tts/srt_generator.py
음성 파일 기반 SRT 자막 파일 생성
문장을 분리하고 음성 길이에 맞춰 타임코드 계산
"""

import soundfile as sf
from pathlib import Path


def format_time(seconds: float) -> str:
    """
    초(float) → SRT 타임코드 형식 변환
    예: 3.5 → 00:00:03,500
    """
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def split_sentences(text: str) -> list[str]:
    """
    텍스트를 문장 단위로 분리
    마침표, 느낌표, 물음표, 줄바꿈 기준
    """
    import re
    # 문장 구분자로 분리 후 빈 문자열 제거
    sentences = re.split(r'(?<=[.!?\n])\s*', text.strip())
    return [s.strip() for s in sentences if s.strip()]


def generate_srt(
    text: str,
    audio_path: str,
    output_path: str
) -> str:
    """
    텍스트 + 음성 파일 → SRT 자막 파일 생성
    
    음성 전체 길이를 문장 수로 균등 분배하여 타임코드 계산
    
    Args:
        text: 원본 텍스트
        audio_path: 생성된 WAV 파일 경로
        output_path: 출력 SRT 파일 경로
    
    Returns:
        생성된 SRT 파일 경로
    """
    # 음성 파일 길이 읽기
    audio_data, sample_rate = sf.read(audio_path)
    total_duration = len(audio_data) / sample_rate

    # 문장 분리
    sentences = split_sentences(text)
    if not sentences:
        sentences = [text]

    # 문장당 평균 시간 계산 (0.3초 여백 포함)
    time_per_sentence = total_duration / len(sentences)
    gap = 0.2  # 문장 사이 간격 (초)

    # SRT 내용 생성
    srt_content = []
    current_time = 0.0

    for i, sentence in enumerate(sentences, start=1):
        start_time = current_time
        end_time = current_time + time_per_sentence - gap

        srt_content.append(f"{i}")
        srt_content.append(f"{format_time(start_time)} --> {format_time(end_time)}")
        srt_content.append(sentence)
        srt_content.append("")  # 빈 줄

        current_time += time_per_sentence

    # 파일 저장
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    srt_text = "\n".join(srt_content)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(srt_text)

    return output_path
