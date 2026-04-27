"""
tts/finetuner.py
XTTS v2 파인튜닝 모듈
- 목소리 샘플로 XTTS 모델을 해당 화자에 맞게 학습
- Whisper로 자동 전사 → 데이터셋 생성 → GPT 학습
"""

import gc
import os
import json
import shutil
import torch
import numpy as np
import soundfile as sf
from pathlib import Path
from datetime import datetime


# 파인튜닝 출력 경로 (절대 경로로 변환하여 coqui 내부 os.path.join 충돌 방지)
FINETUNE_DIR = Path("finetune_data").resolve()
FINETUNE_DIR.mkdir(exist_ok=True)


def prepare_dataset(
    audio_paths: list,
    speaker_name: str,
    output_dir: str,
    language: str = "ko",
) -> dict:
    """
    음성 파일들을 Whisper로 전사하여 학습 데이터셋 생성
    - audio_paths: WAV 파일 경로 리스트
    - speaker_name: 화자 이름
    - output_dir: 출력 디렉토리
    반환: {"train_csv": str, "eval_csv": str, "total_duration": float}
    """
    from TTS.demos.xtts_ft_demo.utils.formatter import format_audio_list

    # 절대 경로로 변환 (coqui 내부 os.path.join 경로 충돌 방지)
    output_dir = os.path.abspath(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    print(f"[FINETUNE] 데이터셋 준비: {len(audio_paths)}개 파일, 화자={speaker_name}")

    train_csv, eval_csv, total_duration = format_audio_list(
        audio_files=audio_paths,
        target_language=language,
        out_path=output_dir,
        buffer=0.2,
        eval_percentage=0.15,
        speaker_name=speaker_name,
    )

    print(f"[FINETUNE] 데이터셋 생성 완료: {total_duration:.1f}초")
    print(f"  - 학습: {train_csv}")
    print(f"  - 평가: {eval_csv}")

    return {
        "train_csv": train_csv,
        "eval_csv": eval_csv,
        "total_duration": round(total_duration, 2),
    }


def run_finetune(
    train_csv: str,
    eval_csv: str,
    output_path: str,
    language: str = "ko",
    num_epochs: int = 10,
    batch_size: int = 2,
    grad_acumm: int = 2,
) -> dict:
    """
    XTTS v2 파인튜닝 실행
    - train_csv: 학습 메타데이터 CSV (파일명만, 디렉토리 제외)
    - eval_csv: 평가 메타데이터 CSV (파일명만)
    - output_path: 출력 경로
    - num_epochs: 학습 에포크 수
    - batch_size: 배치 크기 (VRAM 8GB → 2, 10GB → 4)
    - grad_acumm: 그래디언트 누적 스텝
    반환: {"model_path": str, "config_path": str, "vocab_path": str, "speaker_ref": str}
    """
    import subprocess, sys, tempfile

    train_csv = os.path.abspath(train_csv)
    eval_csv = os.path.abspath(eval_csv)
    output_path = os.path.abspath(output_path)

    train_gpu = os.environ.get("FINETUNE_GPU", "2")
    print(f"[FINETUNE] 학습 시작: epochs={num_epochs}, batch={batch_size}, GPU={train_gpu} (격리된 서브프로세스)")

    payload = {
        "language": language,
        "num_epochs": num_epochs,
        "batch_size": batch_size,
        "grad_acumm": grad_acumm,
        "train_csv": train_csv,
        "eval_csv": eval_csv,
        "output_path": output_path,
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as pf:
        import json as _j
        _j.dump(payload, pf)
        payload_path = pf.name
    result_path = payload_path.replace(".json", ".result.json")
    sub_env = os.environ.copy()
    sub_env["CUDA_VISIBLE_DEVICES"] = train_gpu
    sub_env["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
    script = os.path.join(os.path.dirname(__file__), "train_subprocess.py")
    proc = subprocess.run([sys.executable, script, payload_path, result_path], env=sub_env)
    if proc.returncode != 0 or not os.path.exists(result_path):
        raise RuntimeError(f"학습 서브프로세스 실패 (exit {proc.returncode})")
    with open(result_path, "r", encoding="utf-8") as f:
        import json as _j
        r = _j.load(f)
    try: os.unlink(payload_path)
    except Exception: pass
    try: os.unlink(result_path)
    except Exception: pass
    if not r.get("ok"):
        raise RuntimeError(f"학습 실패: {r.get('error')}")
    config_path = r["config_path"]
    xtts_checkpoint = r["xtts_checkpoint"]
    vocab_path = r["vocab_path"]
    trainer_out_path = r["trainer_out_path"]
    speaker_ref = r["speaker_ref"]

    # best_model.pth 찾기
    best_model = os.path.join(trainer_out_path, "best_model.pth")
    if not os.path.exists(best_model):
        # best_model이 없으면 가장 최신 체크포인트 사용
        checkpoints = sorted(Path(trainer_out_path).glob("checkpoint_*.pth"))
        if checkpoints:
            best_model = str(checkpoints[-1])
        else:
            raise FileNotFoundError("학습된 모델을 찾을 수 없습니다")

    print(f"[FINETUNE] 학습 완료!")
    print(f"  - 모델: {best_model}")
    print(f"  - 설정: {config_path}")
    print(f"  - 참조 음성: {speaker_ref}")

    return {
        "model_path": best_model,
        "config_path": config_path,
        "vocab_path": vocab_path,
        "speaker_ref": speaker_ref,
        "trainer_out_path": trainer_out_path,
    }


def load_finetuned_model(model_path: str, config_path: str, vocab_path: str):
    """파인튜닝된 모델 로드"""
    from TTS.tts.configs.xtts_config import XttsConfig
    from TTS.tts.models.xtts import Xtts

    config = XttsConfig()
    config.load_json(config_path)

    model = Xtts.init_from_config(config)
    model.load_checkpoint(
        config,
        checkpoint_path=model_path,
        vocab_path=vocab_path,
        eval=True,
        strict=False,
    )

    if torch.cuda.is_available():
        model.cuda()

    print(f"[FINETUNE] 파인튜닝 모델 로드 완료: {model_path}")
    return model, config


def full_finetune_pipeline(
    voice_name: str,
    audio_paths: list,
    language: str = "ko",
    num_epochs: int = 10,
    batch_size: int = 2,
) -> dict:
    """
    전체 파인튜닝 파이프라인 (데이터 준비 → 학습 → 모델 저장)
    - voice_name: 화자 이름
    - audio_paths: 음성 파일 경로 리스트
    - num_epochs: 학습 에포크 수
    반환: 파인튜닝 결과 정보
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_dir = str(FINETUNE_DIR / f"{voice_name}_{timestamp}")

    # 1. 데이터셋 준비
    dataset_dir = os.path.join(base_dir, "dataset")
    dataset = prepare_dataset(
        audio_paths=audio_paths,
        speaker_name=voice_name,
        output_dir=dataset_dir,
        language=language,
    )

    if dataset["total_duration"] < 120:  # 최소 2분
        print(f"[FINETUNE 경고] 음성이 {dataset['total_duration']}초로 짧습니다. 2분 이상 권장합니다.")

    # 2. 파인튜닝 실행
    result = run_finetune(
        train_csv=dataset["train_csv"],
        eval_csv=dataset["eval_csv"],
        output_path=base_dir,
        language=language,
        num_epochs=num_epochs,
        batch_size=batch_size,
        grad_acumm=2,
    )

    # 3. 결과 정리 (필요한 파일만 별도 폴더에 복사)
    models_dir = str(FINETUNE_DIR / f"{voice_name}_model")
    os.makedirs(models_dir, exist_ok=True)

    final_model = os.path.join(models_dir, "model.pth")
    final_config = os.path.join(models_dir, "config.json")
    final_vocab = os.path.join(models_dir, "vocab.json")
    final_ref = os.path.join(models_dir, "reference.wav")

    shutil.copy2(result["model_path"], final_model)
    shutil.copy2(result["config_path"], final_config)
    shutil.copy2(result["vocab_path"], final_vocab)
    if result["speaker_ref"]:
        shutil.copy2(result["speaker_ref"], final_ref)

    # 메타 정보 저장
    meta = {
        "voice_name": voice_name,
        "model_path": final_model,
        "config_path": final_config,
        "vocab_path": final_vocab,
        "speaker_ref": final_ref,
        "total_duration": dataset["total_duration"],
        "num_epochs": num_epochs,
        "created_at": timestamp,
    }
    with open(os.path.join(models_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"[FINETUNE] 파이프라인 완료! 모델 저장 위치: {models_dir}")

    # VRAM 정리
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    return meta


def get_finetuned_models() -> list:
    """저장된 파인튜닝 모델 목록 조회"""
    models = []
    for d in FINETUNE_DIR.iterdir():
        if d.is_dir() and d.name.endswith("_model"):
            meta_path = d / "meta.json"
            if meta_path.exists():
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                    meta["dir"] = str(d)
                    models.append(meta)
    return sorted(models, key=lambda x: x.get("created_at", ""), reverse=True)
