"""
main.py
FastAPI 메인 서버
목소리 녹음 저장 + TTS 생성 API
"""

import gc
import os
import uuid
import subprocess
import asyncio
import torch
import numpy as np
import soundfile as sf
from datetime import datetime
from pathlib import Path
from contextlib import asynccontextmanager
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import aiofiles

from database.db import (
    init_db, save_voice, get_all_voices, get_voice,
    delete_voice, delete_all_voices,
    save_tts_result, get_tts_result, get_all_tts_results,
    delete_tts_result, delete_all_tts_results,
    search_tts_results, toggle_favorite, get_voice_names,
    save_project, update_project, get_all_projects, get_project, delete_project,
    save_checkpoint, get_checkpoints, get_checkpoint, set_active_checkpoint,
    get_active_checkpoint, delete_checkpoint, save_feedback,
    save_chinese_card, get_chinese_cards, get_chinese_card, update_chinese_card,
    delete_chinese_card, save_pronunciation_record, get_pronunciation_records,
    toggle_pronunciation_star, delete_pronunciation_record, get_chinese_study_stats,
    get_chinese_subjects, save_chinese_note, update_chinese_note,
    get_chinese_notes, delete_chinese_note,
    get_chinese_daily_stats, get_chinese_recent_records,
    get_user_settings, save_user_settings
)
from auth.dependencies import get_current_user, get_optional_user
from auth.routes import router as auth_router
from library.routes import router as library_router
from tts.engine import get_engine
from tts.srt_generator import generate_srt
from tts.diarizer import diarize_audio, extract_speaker_audio, generate_speaker_preview, extract_all_speaker_audio
from tts.finetuner import full_finetune_pipeline, get_finetuned_models, load_finetuned_model


# 저장 폴더 설정
VOICE_SAMPLES_DIR = Path("voice_samples")
OUTPUTS_DIR = Path("outputs")
DIARIZE_TEMP_DIR = Path("diarize_temp")
PROJECTS_DIR = Path("projects")
VOICE_SAMPLES_DIR.mkdir(exist_ok=True)
OUTPUTS_DIR.mkdir(exist_ok=True)
DIARIZE_TEMP_DIR.mkdir(exist_ok=True)
PROJECTS_DIR.mkdir(exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작 시 DB 초기화 + TTS 엔진 예열"""
    await init_db()
    print("[START] 서버 시작! TTS 엔진 로딩 중...")
    get_engine()  # 미리 로드해서 첫 요청 빠르게
    yield
    print("서버 종료")


app = FastAPI(
    title="Voice Clone TTS API",
    description="내 목소리로 TTS + SRT 자막 생성",
    lifespan=lifespan
)


# ─── 인증 미들웨어 ─────────────────────────────────────────
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from auth.security import decode_access_token

# 인증 불필요 경로
PUBLIC_PATHS = {"/", "/login", "/admin", "/promo", "/library", "/chinese", "/japanese", "/manifest.json", "/api/auth/"}
PUBLIC_PREFIXES = ("/static/", "/outputs/", "/icons/", "/api/auth/", "/api/sheets/", "/api/chinese/audio/")


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path
        # 공개 경로는 인증 건너뜀
        if path in PUBLIC_PATHS or any(path.startswith(p) for p in PUBLIC_PREFIXES):
            return await call_next(request)
        # /api/ 경로만 인증 체크
        if path.startswith("/api/"):
            token = None
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
            if not token:
                token = request.cookies.get("access_token")
            if not token:
                return JSONResponse({"detail": "로그인이 필요합니다"}, status_code=401)
            payload = decode_access_token(token)
            if not payload:
                return JSONResponse({"detail": "토큰이 만료되었습니다"}, status_code=401)
            # 사용자 정보를 request.state에 저장
            request.state.user_id = int(payload.get("sub", 0))
            request.state.user_level = int(payload.get("level", 255))
            request.state.user_email = payload.get("email", "")
            request.state.user_name = payload.get("name", "")
        return await call_next(request)


app.add_middleware(AuthMiddleware)


# 프론트엔드 정적 파일 서빙
app.mount("/static", StaticFiles(directory="frontend"), name="static")
app.mount("/outputs", StaticFiles(directory="outputs"), name="outputs")
app.mount("/icons", StaticFiles(directory="frontend/icons"), name="icons")


@app.get("/manifest.json")
async def serve_manifest():
    """PWA 매니페스트 서빙"""
    return FileResponse("frontend/manifest.json", media_type="application/manifest+json")


# ─── 인증 ─────────────────────────────────────────────────
app.include_router(auth_router)
app.include_router(library_router)


@app.get("/library")
async def library_page():
    """음원 라이브러리 페이지"""
    return FileResponse("frontend/library.html")


@app.get("/login")
async def login_page():
    """로그인 페이지"""
    return FileResponse("frontend/login.html")


@app.get("/admin")
async def admin_page():
    """관리자 페이지"""
    return FileResponse("frontend/admin.html")


# ─── 목소리 관련 API ────────────────────────────────────────

@app.post("/api/voice/record")
async def upload_voice(
    name: str = Form(...),
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    """
    목소리 샘플 업로드 + DB 저장
    - name: 목소리 이름 (예: "내목소리")
    - file: WAV 파일
    """
    # 파일 확장자 검사
    if not file.filename.endswith((".wav", ".mp3", ".webm", ".ogg", ".m4a", ".flac")):
        raise HTTPException(400, "WAV, MP3, M4A, WebM, OGG, FLAC 파일만 가능합니다")

    # 저장 경로 생성 (파일 업로드 시 원본 파일명 사용)
    file_id = uuid.uuid4().hex[:8]
    original_ext = Path(file.filename).suffix.lower()
    original_stem = Path(file.filename).stem
    temp_path = VOICE_SAMPLES_DIR / f"{file_id}_temp{original_ext}"
    wav_path = VOICE_SAMPLES_DIR / f"{file_id}_{original_stem}.wav"

    # 원본 파일 임시 저장
    async with aiofiles.open(temp_path, "wb") as f:
        content = await file.read()
        await f.write(content)

    # ffmpeg로 WAV 변환 + 전처리 (24000Hz, 모노, 음량 정규화)
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(temp_path),
                "-ar", "24000",       # XTTS v2 기본 sample rate
                "-ac", "1",           # 모노
                "-af", "highpass=f=60,loudnorm=I=-16:TP=-1.5:LRA=11",  # 노이즈 제거 + 음량 정규화 (로우패스 제거 — 고음 보존)
                str(wav_path)
            ],
            capture_output=True, check=True
        )
        temp_path.unlink()
    except subprocess.CalledProcessError as e:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(500, f"audio conversion failed: {e.stderr.decode()}")

    save_path = wav_path

    # 음성 길이 계산
    try:
        audio_data, sample_rate = sf.read(str(save_path))
        duration = len(audio_data) / sample_rate
    except Exception:
        duration = 0.0

    # DB 저장
    voice_id = await save_voice(
        name=name,
        file_path=str(save_path),
        duration=duration,
        user_id=user["id"],
    )

    return {
        "success": True,
        "voice_id": voice_id,
        "name": name,
        "duration": round(duration, 2),
        "message": f"목소리 '{name}' 저장 완료!"
    }


@app.get("/api/voice/list")
async def list_voices(user: dict = Depends(get_current_user)):
    """저장된 목소리 목록 조회 (본인 것만, 관리자는 전체)"""
    uid = None if user["level"] <= 10 else user["id"]
    voices = await get_all_voices(user_id=uid)
    return {"voices": voices}


# ─── TTS 미리듣기 API (라인별 생성) ──────────────────────────

# 미리듣기 임시 파일 폴더
PREVIEW_DIR = Path("temp/preview")
PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

# 생성 진행률 추적
_progress = {}  # {session_id: {"current": 0, "total": 5, "status": "generating"}}


@app.post("/api/tts/preview")
async def preview_tts(
    voice_id: int = Form(...),
    text: str = Form(...),
    tone: str = Form("normal"),
    params_json: str = Form(""),
):
    """
    텍스트를 엔터 기준으로 분리하여 각 라인별 TTS 미리듣기 생성
    - tone: normal, fast, slow, bright, sad, polite, elderly (전체 적용)
    """
    if not text.strip():
        raise HTTPException(400, "텍스트를 입력해주세요")

    voice = await get_voice(voice_id)
    if not voice:
        raise HTTPException(404, f"목소리 ID {voice_id}를 찾을 수 없습니다")

    lines = [l.strip() for l in text.split('\n') if l.strip()]
    if not lines:
        raise HTTPException(400, "텍스트가 비어있습니다")

    session_id = uuid.uuid4().hex[:8]
    session_dir = PREVIEW_DIR / session_id
    session_dir.mkdir(exist_ok=True)

    # 진행률 초기화 (session_id를 먼저 반환할 수 없으므로 총 라인 수로 추적)
    _progress[session_id] = {"current": 0, "total": len(lines), "status": "generating", "session_id": session_id}

    # 커스텀 파라미터 파싱 (옵션)
    custom_params = None
    if params_json and params_json.strip():
        try:
            import json as json_mod
            custom_params = json_mod.loads(params_json)
        except Exception:
            pass

    results = []
    engine = get_engine()

    for i, line in enumerate(lines):
        _progress[session_id]["current"] = i
        output_path = str(session_dir / f"line_{i}.wav")
        try:
            def _gen(ln=line, op=output_path, t=tone, cp=custom_params):
                engine.generate_single_line(
                    text=ln,
                    speaker_wav=voice["file_path"],
                    output_path=op,
                    tone=t,
                    custom_params=cp,
                )
            await asyncio.get_event_loop().run_in_executor(None, _gen)
            results.append({
                "index": i,
                "text": line,
                "audio_url": f"/api/tts/preview-audio/{session_id}/{i}",
                "success": True,
            })
        except Exception as e:
            print(f"[TTS 미리듣기] 라인 {i} 실패: {e}")
            results.append({
                "index": i,
                "text": line,
                "audio_url": None,
                "success": False,
                "error": str(e),
            })

    _progress[session_id] = {"current": len(lines), "total": len(lines), "status": "done"}

    return {
        "session_id": session_id,
        "voice_id": voice_id,
        "lines": results,
    }


@app.get("/api/tts/progress/{session_id}")
async def get_progress(session_id: str):
    """TTS 생성 진행률 조회"""
    if session_id in _progress:
        p = _progress[session_id]
        return {"current": p["current"], "total": p["total"], "status": p["status"]}
    return {"current": 0, "total": 0, "status": "unknown"}


@app.post("/api/tts/preview-line")
async def preview_single_line(
    session_id: str = Form(...),
    voice_id: int = Form(...),
    line_index: int = Form(...),
    text: str = Form(...),
    tone: str = Form("normal"),
    params_json: str = Form(""),
):
    """단일 라인 재생성 (텍스트 수정 또는 톤 변경)"""
    voice = await get_voice(voice_id)
    if not voice:
        raise HTTPException(404, "목소리를 찾을 수 없습니다")

    # 커스텀 파라미터 파싱 (옵션)
    custom_params = None
    if params_json and params_json.strip():
        try:
            import json as json_mod
            custom_params = json_mod.loads(params_json)
        except Exception:
            pass

    session_dir = PREVIEW_DIR / session_id
    session_dir.mkdir(exist_ok=True)
    output_path = str(session_dir / f"line_{line_index}.wav")

    engine = get_engine()
    try:
        def _gen():
            engine.generate_single_line(
                text=text,
                speaker_wav=voice["file_path"],
                output_path=output_path,
                tone=tone,
                custom_params=custom_params,
            )
        await asyncio.get_event_loop().run_in_executor(None, _gen)
    except Exception as e:
        raise HTTPException(500, f"생성 실패: {str(e)}")

    return {
        "success": True,
        "audio_url": f"/api/tts/preview-audio/{session_id}/{line_index}",
    }


@app.get("/api/tts/preview-audio/{session_id}/{line_index}")
async def get_preview_audio(session_id: str, line_index: int):
    """미리듣기 오디오 파일 서빙"""
    path = PREVIEW_DIR / session_id / f"line_{line_index}.wav"
    if not path.exists():
        raise HTTPException(404, "파일을 찾을 수 없습니다")
    return FileResponse(str(path), media_type="audio/wav")


@app.post("/api/tts/preview-trim")
async def preview_trim(
    session_id: str = Form(...),
    line_index: int = Form(...),
    start_sec: float = Form(0.0),
    end_sec: float = Form(-1.0),
):
    """미리듣기 라인 앞/뒤 컷 (트리밍)"""
    wav_path = PREVIEW_DIR / session_id / f"line_{line_index}.wav"
    if not wav_path.exists():
        raise HTTPException(404, "파일을 찾을 수 없습니다")

    data, sr = sf.read(str(wav_path))
    total_dur = len(data) / sr
    start_sample = int(start_sec * sr)
    end_sample = int(end_sec * sr) if end_sec > 0 else len(data)
    start_sample = max(0, min(start_sample, len(data)))
    end_sample = max(start_sample, min(end_sample, len(data)))

    trimmed = data[start_sample:end_sample]
    if len(trimmed) == 0:
        raise HTTPException(400, "트리밍 후 오디오가 비어있습니다")

    sf.write(str(wav_path), trimmed, sr)
    new_dur = len(trimmed) / sr
    return {
        "success": True,
        "duration": round(new_dur, 2),
        "audio_url": f"/api/tts/preview-audio/{session_id}/{line_index}",
    }


@app.post("/api/tts/preview-split")
async def preview_split(
    session_id: str = Form(...),
    line_index: int = Form(...),
    split_sec: float = Form(...),
):
    """미리듣기 라인을 특정 지점에서 분리"""
    session_dir = PREVIEW_DIR / session_id
    wav_path = session_dir / f"line_{line_index}.wav"
    if not wav_path.exists():
        raise HTTPException(404, "파일을 찾을 수 없습니다")

    data, sr = sf.read(str(wav_path))
    split_sample = int(split_sec * sr)
    if split_sample <= 0 or split_sample >= len(data):
        raise HTTPException(400, "분리 지점이 유효하지 않습니다")

    part_a = data[:split_sample]
    part_b = data[split_sample:]

    # 기존 파일들 뒤로 밀기 (line_index+1 부터 끝까지)
    existing = sorted(session_dir.glob("line_*.wav"), key=lambda p: int(p.stem.split("_")[1]), reverse=True)
    for f in existing:
        idx = int(f.stem.split("_")[1])
        if idx > line_index:
            f.rename(session_dir / f"line_{idx + 1}.wav")

    # 분리된 파일 저장
    sf.write(str(session_dir / f"line_{line_index}.wav"), part_a, sr)
    sf.write(str(session_dir / f"line_{line_index + 1}.wav"), part_b, sr)

    return {
        "success": True,
        "part_a": {
            "index": line_index,
            "duration": round(len(part_a) / sr, 2),
            "audio_url": f"/api/tts/preview-audio/{session_id}/{line_index}",
        },
        "part_b": {
            "index": line_index + 1,
            "duration": round(len(part_b) / sr, 2),
            "audio_url": f"/api/tts/preview-audio/{session_id}/{line_index + 1}",
        },
    }


@app.post("/api/tts/finalize")
async def finalize_tts(
    session_id: str = Form(...),
    voice_id: int = Form(...),
    lines_json: str = Form(...),
):
    """
    미리듣기 라인들을 합쳐서 최종 TTS 파일 생성 + DB 저장
    - lines_json: [{"index": 0, "text": "..."}, ...] 순서대로
    """
    import json as json_mod

    voice = await get_voice(voice_id)
    if not voice:
        raise HTTPException(404, "목소리를 찾을 수 없습니다")

    try:
        lines_data = json_mod.loads(lines_json)
    except Exception:
        raise HTTPException(400, "잘못된 JSON")

    session_dir = PREVIEW_DIR / session_id
    if not session_dir.exists():
        raise HTTPException(404, "세션을 찾을 수 없습니다")

    # 라인 오디오 합치기
    all_audio = []
    full_text_parts = []
    silence_between = np.zeros(int(0.1 * 24000), dtype=np.float32)

    for item in lines_data:
        idx = item["index"]
        full_text_parts.append(item.get("text", ""))
        wav_path = session_dir / f"line_{idx}.wav"
        if wav_path.exists():
            data, sr = sf.read(str(wav_path))
            if sr != 24000:
                import librosa
                data = librosa.resample(data, orig_sr=sr, target_sr=24000)
            all_audio.append(data.astype(np.float32))

    if not all_audio:
        raise HTTPException(400, "합칠 오디오가 없습니다")

    # 라인 간 0.1초 무음 삽입 후 연결
    final = all_audio[0]
    for part in all_audio[1:]:
        final = np.concatenate([final, silence_between, part])

    # 볼륨 정규화
    peak = np.max(np.abs(final))
    if peak > 0:
        final = (final / peak * 0.7).astype(np.float32)

    # 최종 파일 저장
    today = datetime.now().strftime("%Y%m%d")
    output_id = uuid.uuid4().hex[:8]
    output_dir = OUTPUTS_DIR / today
    output_dir.mkdir(exist_ok=True)
    audio_path = str(output_dir / f"{output_id}.wav")
    srt_path = str(output_dir / f"{output_id}.srt")

    sf.write(audio_path, final, 24000)

    # SRT 생성
    full_text = '\n'.join(full_text_parts)
    generate_srt(text=full_text, audio_path=audio_path, output_path=srt_path)

    # DB 저장
    result_id = await save_tts_result(
        voice_id=voice_id,
        input_text=full_text,
        audio_path=audio_path,
        srt_path=srt_path,
    )

    # 임시 파일 정리
    import shutil
    shutil.rmtree(str(session_dir), ignore_errors=True)

    return {
        "success": True,
        "result_id": result_id,
        "audio_url": f"/api/tts/download/{result_id}/audio",
        "srt_url": f"/api/tts/download/{result_id}/srt",
        "message": "TTS 최종 저장 완료!",
    }


# ─── 미리듣기 라인 삭제 ──────────────────────────────────────

@app.post("/api/tts/preview-delete")
async def preview_delete_line(
    session_id: str = Form(...),
    line_index: int = Form(...),
):
    """미리듣기 라인 삭제"""
    wav_path = PREVIEW_DIR / session_id / f"line_{line_index}.wav"
    if wav_path.exists():
        wav_path.unlink()
    return {"success": True}


# ─── YouTube 음성 추출 ──────────────────────────────────────

@app.post("/api/voice/youtube")
async def import_youtube(
    url: str = Form(...),
    name: str = Form("YouTube"),
):
    """YouTube URL에서 음성 추출 후 목소리 저장"""
    import shutil
    import re

    # URL 검증
    if not re.search(r'(youtube\.com|youtu\.be)', url):
        raise HTTPException(400, "유효한 YouTube URL이 아닙니다")

    temp_id = uuid.uuid4().hex[:8]
    temp_dir = Path("temp/youtube")
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_path = temp_dir / f"{temp_id}"

    try:
        # yt-dlp로 오디오 다운로드 (subprocess.run 사용 - Windows 호환)
        cmd = [
            "yt-dlp", "-x", "--audio-format", "wav",
            "--audio-quality", "0",
            "-o", str(temp_path) + ".%(ext)s",
            "--no-playlist",
            url
        ]

        def _download():
            return subprocess.run(
                cmd, capture_output=True, text=True, timeout=300
            )

        proc = await asyncio.get_event_loop().run_in_executor(None, _download)

        if proc.returncode != 0:
            err_msg = (proc.stderr or proc.stdout or "알 수 없는 오류")[:300]
            raise HTTPException(500, f"YouTube 다운로드 실패: {err_msg}")

        # 다운로드된 파일 찾기
        downloaded = list(temp_dir.glob(f"{temp_id}.*"))
        if not downloaded:
            raise HTTPException(500, "다운로드된 파일을 찾을 수 없습니다")
        src_file = downloaded[0]

        # WAV 변환 (24kHz 모노)
        raw_wav = temp_dir / f"{temp_id}_raw.wav"
        ffmpeg_cmd = [
            "ffmpeg", "-i", str(src_file),
            "-ar", "24000", "-ac", "1", "-y",
            str(raw_wav)
        ]

        def _convert():
            return subprocess.run(
                ffmpeg_cmd, capture_output=True, text=True, timeout=120
            )

        proc2 = await asyncio.get_event_loop().run_in_executor(None, _convert)

        if not raw_wav.exists():
            err_msg = (proc2.stderr or "변환 실패")[:200]
            raise HTTPException(500, f"WAV 변환 실패: {err_msg}")

        # 참조 음성 전처리 (노이즈 제거 + EQ + 정규화)
        final_filename = f"{name}_{temp_id}.wav"
        final_path = VOICE_SAMPLES_DIR / final_filename

        def _preprocess():
            from tts.audio_preprocessor import preprocess_reference_audio
            return preprocess_reference_audio(str(raw_wav), str(final_path))

        try:
            await asyncio.get_event_loop().run_in_executor(None, _preprocess)
        except ValueError:
            # 전처리 실패 시 (너무 짧은 등) 원본 WAV 사용
            import shutil as sh
            sh.copy2(str(raw_wav), str(final_path))

        # 임시 raw 파일 정리
        try:
            raw_wav.unlink(missing_ok=True)
        except Exception:
            pass

        # 길이 계산
        data, sr = sf.read(str(final_path))
        duration = round(len(data) / sr, 2)

        # DB 저장
        voice_id = await save_voice(name, str(final_path), duration)

        # 임시 파일 정리
        for f in temp_dir.glob(f"{temp_id}*"):
            try:
                f.unlink()
            except Exception:
                pass

        return {
            "success": True,
            "voice_id": voice_id,
            "name": name,
            "duration": duration,
            "message": f"YouTube 음성 저장 완료! ({duration}초)"
        }
    except HTTPException:
        raise
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "YouTube 다운로드 시간 초과 (5분)")
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"YouTube 추출 실패: {str(e)}")


# ─── 프로젝트 API ────────────────────────────────────────────

@app.post("/api/project/save")
async def api_save_project(
    name: str = Form(...),
    voice_id: int = Form(...),
    input_text: str = Form(""),
    session_id: str = Form(...),
    lines_json: str = Form(...),
    global_tone: str = Form("normal"),
):
    """현재 편집 상태를 프로젝트로 저장"""
    import shutil

    # 미리듣기 파일을 프로젝트 폴더로 복사
    src_dir = PREVIEW_DIR / session_id
    project_session_id = f"proj_{uuid.uuid4().hex[:8]}"
    dst_dir = PROJECTS_DIR / project_session_id

    if src_dir.exists():
        shutil.copytree(str(src_dir), str(dst_dir))
    else:
        dst_dir.mkdir(exist_ok=True)

    project_id = await save_project(
        name=name, voice_id=voice_id, input_text=input_text,
        session_id=project_session_id, lines_json=lines_json, global_tone=global_tone
    )
    return {
        "success": True,
        "project_id": project_id,
        "session_id": project_session_id,
        "message": f"프로젝트 '{name}' 저장 완료!"
    }


@app.put("/api/project/{project_id}")
async def api_update_project(
    project_id: int,
    name: str = Form(...),
    voice_id: int = Form(...),
    input_text: str = Form(""),
    session_id: str = Form(...),
    lines_json: str = Form(...),
    global_tone: str = Form("normal"),
):
    """프로젝트 업데이트 (편집 상태 덮어쓰기)"""
    import shutil

    proj = await get_project(project_id)
    if not proj:
        raise HTTPException(404, "프로젝트를 찾을 수 없습니다")

    # 기존 프로젝트 오디오 업데이트
    old_dir = PROJECTS_DIR / proj["session_id"]
    src_dir = PREVIEW_DIR / session_id

    if src_dir.exists():
        if old_dir.exists():
            shutil.rmtree(str(old_dir), ignore_errors=True)
        shutil.copytree(str(src_dir), str(old_dir))

    await update_project(
        project_id=project_id, name=name, voice_id=voice_id,
        input_text=input_text, session_id=proj["session_id"],
        lines_json=lines_json, global_tone=global_tone
    )
    return {"success": True, "message": f"프로젝트 '{name}' 업데이트 완료!"}


@app.get("/api/project/list")
async def api_list_projects():
    """프로젝트 목록 조회"""
    projects = await get_all_projects()
    return {"projects": projects}


@app.get("/api/project/{project_id}")
async def api_get_project(project_id: int):
    """프로젝트 불러오기"""
    proj = await get_project(project_id)
    if not proj:
        raise HTTPException(404, "프로젝트를 찾을 수 없습니다")
    return {"success": True, "project": proj}


@app.delete("/api/project/{project_id}")
async def api_delete_project(project_id: int):
    """프로젝트 삭제 (오디오 파일 포함)"""
    import shutil
    proj = await get_project(project_id)
    if proj:
        proj_dir = PROJECTS_DIR / proj["session_id"]
        if proj_dir.exists():
            shutil.rmtree(str(proj_dir), ignore_errors=True)
    await delete_project(project_id)
    return {"success": True, "message": "프로젝트 삭제 완료"}


@app.get("/api/project/audio/{session_id}/{line_index}")
async def get_project_audio(session_id: str, line_index: int):
    """프로젝트 오디오 파일 서빙"""
    path = PROJECTS_DIR / session_id / f"line_{line_index}.wav"
    if not path.exists():
        # fallback to preview dir
        path = PREVIEW_DIR / session_id / f"line_{line_index}.wav"
    if not path.exists():
        raise HTTPException(404, "파일을 찾을 수 없습니다")
    return FileResponse(str(path), media_type="audio/wav")


# ─── TTS 생성 API ────────────────────────────────────────────

@app.post("/api/tts/generate")
async def generate_tts(
    voice_id: int = Form(...),
    text: str = Form(...),
    elderly_mode: str = Form("false"),
):
    """
    텍스트 → TTS 음성 + SRT 자막 생성
    - voice_id: 사용할 목소리 ID
    - text: 변환할 텍스트
    - elderly_mode: 부모님 효과 (나이든 음색)
    """
    elderly = elderly_mode.lower() == "true"
    if not text.strip():
        raise HTTPException(400, "텍스트를 입력해주세요")

    # 목소리 파일 경로 조회
    voices = await get_all_voices()
    voice = next((v for v in voices if v["id"] == voice_id), None)

    if not voice:
        raise HTTPException(404, f"목소리 ID {voice_id}를 찾을 수 없습니다")

    # 출력 파일 경로 설정
    today = datetime.now().strftime("%Y%m%d")
    output_id = uuid.uuid4().hex[:8]
    output_dir = OUTPUTS_DIR / today
    output_dir.mkdir(exist_ok=True)

    audio_path = str(output_dir / f"{output_id}.wav")
    srt_path = str(output_dir / f"{output_id}.srt")

    # TTS 생성 (별도 스레드에서 실행 - 긴 텍스트 타임아웃 방지)
    try:
        print(f"[TTS] generation start: {text[:30]}...", flush=True)
        engine = get_engine()

        def _run_tts():
            engine.generate(
                text=text,
                speaker_wav=voice["file_path"],
                output_path=audio_path,
                elderly_mode=elderly,
            )

        await asyncio.get_event_loop().run_in_executor(None, _run_tts)
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"TTS 생성 실패: {str(e)}")

    # SRT 자막 생성
    generate_srt(text=text, audio_path=audio_path, output_path=srt_path)

    # DB 저장
    result_id = await save_tts_result(
        voice_id=voice_id,
        input_text=text,
        audio_path=audio_path,
        srt_path=srt_path
    )

    print(f"[OK] done! ID: {result_id}", flush=True)

    return {
        "success": True,
        "result_id": result_id,
        "audio_url": f"/api/tts/download/{result_id}/audio",
        "srt_url": f"/api/tts/download/{result_id}/srt",
        "message": "TTS 생성 완료!"
    }


@app.get("/api/tts/download/{result_id}/audio")
async def download_audio(result_id: int):
    """생성된 음성 파일 다운로드"""
    result = await get_tts_result(result_id)
    if not result:
        raise HTTPException(404, "결과를 찾을 수 없습니다")
    return FileResponse(result["audio_path"], media_type="audio/wav", filename="tts_output.wav")


@app.get("/api/tts/download/{result_id}/srt")
async def download_srt(result_id: int):
    """생성된 SRT 자막 파일 다운로드"""
    result = await get_tts_result(result_id)
    if not result:
        raise HTTPException(404, "결과를 찾을 수 없습니다")
    return FileResponse(result["srt_path"], media_type="text/plain", filename="subtitles.srt")


# ─── 목소리 삭제 API ─────────────────────────────────────────

@app.delete("/api/voice/all")
async def api_delete_all_voices():
    """목소리 전체 삭제 (DB + 파일)"""
    voices = await get_all_voices()
    for v in voices:
        file_path = Path(v["file_path"])
        if file_path.exists():
            file_path.unlink()
    await delete_all_voices()
    return {"success": True, "message": f"{len(voices)}개 목소리 전체 삭제 완료"}


@app.delete("/api/voice/{voice_id}")
async def api_delete_voice(voice_id: int):
    """목소리 개별 삭제 (DB + 파일)"""
    voice = await get_voice(voice_id)
    if not voice:
        raise HTTPException(404, "목소리를 찾을 수 없습니다")
    # 파일 삭제
    file_path = Path(voice["file_path"])
    if file_path.exists():
        file_path.unlink()
    await delete_voice(voice_id)
    return {"success": True, "message": f"목소리 '{voice['name']}' 삭제 완료"}


# ─── TTS 결과 목록/삭제 API ──────────────────────────────────

@app.get("/api/tts/list")
async def list_tts_results():
    """TTS 생성 결과 전체 조회"""
    results = await get_all_tts_results()
    return {"results": results}


@app.delete("/api/tts/all")
async def api_delete_all_tts_results():
    """TTS 결과 전체 삭제 (DB + 파일)"""
    results = await get_all_tts_results()
    for r in results:
        audio_path = Path(r["audio_path"])
        if audio_path.exists():
            audio_path.unlink()
        srt_path = Path(r["srt_path"])
        if srt_path.exists():
            srt_path.unlink()
    await delete_all_tts_results()
    return {"success": True, "message": f"{len(results)}개 TTS 결과 전체 삭제 완료"}


@app.delete("/api/tts/{result_id}")
async def api_delete_tts_result(result_id: int):
    """TTS 결과 개별 삭제 (DB + 파일)"""
    result = await get_tts_result(result_id)
    if not result:
        raise HTTPException(404, "TTS 결과를 찾을 수 없습니다")
    # 음성 파일 삭제
    audio_path = Path(result["audio_path"])
    if audio_path.exists():
        audio_path.unlink()
    # SRT 파일 삭제
    srt_path = Path(result["srt_path"])
    if srt_path.exists():
        srt_path.unlink()
    await delete_tts_result(result_id)
    return {"success": True, "message": "TTS 결과 삭제 완료"}


# ─── 화자 분리 API ────────────────────────────────────────────

@app.post("/api/voice/diarize")
async def api_diarize(
    file: UploadFile = File(...),
    hf_token: str = Form(""),
    num_speakers: int = Form(None),
):
    """
    다중 화자 오디오 업로드 → 화자 분리 수행
    - file: 오디오 파일 (WAV, MP3 등)
    - hf_token: HuggingFace 토큰 (미입력 시 .env에서 읽음)
    - num_speakers: 화자 수 (선택, 미지정 시 자동 감지)
    """
    # .env에서 토큰 자동 로드
    if not hf_token:
        hf_token = os.getenv("HF_TOKEN", "")
    if not hf_token:
        raise HTTPException(400, "HuggingFace 토큰이 필요합니다")

    # 파일 저장
    file_id = uuid.uuid4().hex[:8]
    original_ext = Path(file.filename).suffix.lower()
    temp_path = DIARIZE_TEMP_DIR / f"{file_id}_original{original_ext}"
    wav_path = DIARIZE_TEMP_DIR / f"{file_id}.wav"

    async with aiofiles.open(temp_path, "wb") as f:
        content = await file.read()
        await f.write(content)

    # ffmpeg로 WAV 변환 (16000Hz, 모노 - pyannote 최적)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(temp_path),
             "-ar", "16000", "-ac", "1", str(wav_path)],
            capture_output=True, check=True,
        )
        temp_path.unlink(missing_ok=True)
    except subprocess.CalledProcessError as e:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(500, f"오디오 변환 실패: {e.stderr.decode()}")

    # 화자 분리 실행 (별도 스레드)
    try:
        def _run_diarize():
            return diarize_audio(
                audio_path=str(wav_path),
                hf_token=hf_token,
                num_speakers=num_speakers if num_speakers and num_speakers > 0 else None,
            )
        speakers = await asyncio.get_event_loop().run_in_executor(None, _run_diarize)
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"화자 분리 실패: {str(e)}")

    # 화자별 미리듣기 오디오 생성
    for i, spk in enumerate(speakers):
        preview_path = str(DIARIZE_TEMP_DIR / f"{file_id}_speaker{i}_preview.wav")
        try:
            def _gen_preview(s=spk, p=preview_path):
                return generate_speaker_preview(str(wav_path), s["segments"], p)
            await asyncio.get_event_loop().run_in_executor(None, _gen_preview)
            spk["preview_url"] = f"/api/voice/diarize/preview/{file_id}/{i}"
        except Exception:
            spk["preview_url"] = None

    return {
        "success": True,
        "session_id": file_id,
        "speakers": speakers,
        "message": f"{len(speakers)}명의 화자가 감지되었습니다!",
    }


@app.get("/api/voice/diarize/preview/{session_id}/{speaker_idx}")
async def diarize_preview(session_id: str, speaker_idx: int):
    """화자 분리 미리듣기 오디오"""
    preview_path = DIARIZE_TEMP_DIR / f"{session_id}_speaker{speaker_idx}_preview.wav"
    if not preview_path.exists():
        raise HTTPException(404, "미리듣기 파일을 찾을 수 없습니다")
    return FileResponse(str(preview_path), media_type="audio/wav")


@app.post("/api/voice/diarize/extract-all")
async def api_extract_all_speaker(
    session_id: str = Form(...),
    speaker_idx: int = Form(...),
    segments_json: str = Form(...),
):
    """
    특정 화자의 전체 세그먼트 추출 (길이 제한 없음)
    - 해당 화자가 맞으면 전체 데이터를 추출하여 미리듣기 제공
    """
    import json
    try:
        segments = json.loads(segments_json)
    except json.JSONDecodeError:
        raise HTTPException(400, "잘못된 JSON 형식입니다")

    wav_path = DIARIZE_TEMP_DIR / f"{session_id}.wav"
    if not wav_path.exists():
        raise HTTPException(404, "분리 세션 파일을 찾을 수 없습니다")

    # 전체 추출
    output_path = str(DIARIZE_TEMP_DIR / f"{session_id}_speaker{speaker_idx}_full.wav")
    try:
        def _extract():
            return extract_all_speaker_audio(str(wav_path), segments, output_path)
        await asyncio.get_event_loop().run_in_executor(None, _extract)
    except Exception as e:
        raise HTTPException(500, f"추출 실패: {str(e)}")

    # 길이 계산
    try:
        audio_data, sample_rate = sf.read(output_path)
        duration = len(audio_data) / sample_rate
    except Exception:
        duration = 0.0

    return {
        "success": True,
        "preview_url": f"/api/voice/diarize/full/{session_id}/{speaker_idx}",
        "duration": round(duration, 2),
    }


@app.get("/api/voice/diarize/full/{session_id}/{speaker_idx}")
async def diarize_full_preview(session_id: str, speaker_idx: int):
    """화자 전체 추출 오디오"""
    full_path = DIARIZE_TEMP_DIR / f"{session_id}_speaker{speaker_idx}_full.wav"
    if not full_path.exists():
        raise HTTPException(404, "전체 추출 파일을 찾을 수 없습니다")
    return FileResponse(str(full_path), media_type="audio/wav")


@app.post("/api/voice/diarize/save")
async def save_diarized_speakers(
    session_id: str = Form(...),
    speakers_json: str = Form(...),
):
    """
    분리된 화자들을 개별 목소리로 저장
    - session_id: 분리 세션 ID
    - speakers_json: JSON 문자열 [{"index": 0, "name": "화자1", "segments": [...]}, ...]
    """
    import json
    try:
        speakers_data = json.loads(speakers_json)
    except json.JSONDecodeError:
        raise HTTPException(400, "잘못된 JSON 형식입니다")

    wav_path = DIARIZE_TEMP_DIR / f"{session_id}.wav"
    if not wav_path.exists():
        raise HTTPException(404, "분리 세션 파일을 찾을 수 없습니다")

    saved = []
    for spk in speakers_data:
        name = spk.get("name", f"화자 {spk['index'] + 1}")
        segments = spk.get("segments", [])
        if not segments:
            continue

        # 화자 음성 추출
        file_id = uuid.uuid4().hex[:8]
        output_path = str(VOICE_SAMPLES_DIR / f"{file_id}_{name}.wav")

        try:
            def _extract(s=segments, o=output_path):
                return extract_speaker_audio(str(wav_path), s, o)
            await asyncio.get_event_loop().run_in_executor(None, _extract)
        except Exception as e:
            print(f"[경고] 화자 '{name}' 추출 실패: {e}")
            continue

        # 음성 길이 계산
        try:
            audio_data, sample_rate = sf.read(output_path)
            duration = len(audio_data) / sample_rate
        except Exception:
            duration = 0.0

        # DB 저장
        voice_id = await save_voice(name=name, file_path=output_path, duration=duration)
        saved.append({"voice_id": voice_id, "name": name, "duration": round(duration, 2)})

    # 임시 파일 정리
    for f in DIARIZE_TEMP_DIR.glob(f"{session_id}*"):
        f.unlink(missing_ok=True)

    return {
        "success": True,
        "saved": saved,
        "message": f"{len(saved)}명의 목소리가 저장되었습니다!",
    }


# ─── 파인튜닝 API ─────────────────────────────────────────────

# 파인튜닝 진행 상태
finetune_status = {"running": False, "progress": "", "result": None}


@app.post("/api/finetune/start")
async def api_start_finetune(
    voice_ids: str = Form(...),
    model_name: str = Form(""),
    epochs: int = Form(10),
    batch_size: int = Form(2),
):
    """
    선택한 목소리들로 XTTS 파인튜닝 시작 (여러 음성 합쳐서 학습 가능)
    - voice_ids: 쉼표 구분 voice ID (예: "1,2,3")
    - model_name: 저장할 모델 이름 (비어있으면 첫번째 음성 이름 사용)
    - epochs: 학습 에포크 수 (기본 10)
    - batch_size: 배치 크기 (기본 2, VRAM에 따라 조절)
    """
    global finetune_status
    if finetune_status["running"]:
        raise HTTPException(409, "이미 파인튜닝이 진행 중입니다")

    # 선택한 목소리 파일 수집
    ids = [int(x.strip()) for x in voice_ids.split(",") if x.strip()]
    audio_paths = []
    voice_names = []
    for vid in ids:
        voice = await get_voice(vid)
        if voice and os.path.exists(voice["file_path"]):
            audio_paths.append(voice["file_path"])
            voice_names.append(voice["name"])

    if not audio_paths:
        raise HTTPException(400, "유효한 목소리 파일이 없습니다")

    # 모델 이름 결정
    voice_name = model_name.strip() if model_name.strip() else voice_names[0]

    # 총 음성 길이 확인
    total_dur = 0
    for p in audio_paths:
        try:
            data, sr = sf.read(p)
            total_dur += len(data) / sr
        except Exception:
            pass

    if total_dur < 30:
        raise HTTPException(400, f"음성이 {total_dur:.0f}초로 너무 짧습니다. 최소 2분 이상 필요합니다.")

    finetune_status = {"running": True, "progress": "학습 준비 중...", "result": None}

    # 백그라운드에서 파인튜닝 실행
    async def _run():
        global finetune_status
        try:
            finetune_status["progress"] = f"데이터셋 생성 중 ({len(audio_paths)}개 음성, Whisper 전사)..."
            def _pipeline():
                return full_finetune_pipeline(
                    voice_name=voice_name,
                    audio_paths=audio_paths,
                    num_epochs=epochs,
                    batch_size=batch_size,
                )
            result = await asyncio.get_event_loop().run_in_executor(None, _pipeline)
            finetune_status["result"] = result
            finetune_status["progress"] = "완료!"
        except Exception as e:
            import traceback
            traceback.print_exc()
            finetune_status["progress"] = f"실패: {str(e)}"
            finetune_status["result"] = None
        finally:
            finetune_status["running"] = False

    asyncio.create_task(_run())

    names_str = ", ".join(voice_names[:3]) + ("..." if len(voice_names) > 3 else "")
    return {
        "success": True,
        "message": f"파인튜닝 시작! [{names_str}] {len(audio_paths)}개 파일, {total_dur:.0f}초, {epochs} 에포크",
    }


@app.get("/api/finetune/status")
async def api_finetune_status():
    """파인튜닝 진행 상태 조회"""
    return finetune_status


@app.get("/api/finetune/models")
async def api_finetune_models():
    """저장된 파인튜닝 모델 목록"""
    models = get_finetuned_models()
    return {"models": models}


@app.post("/api/tts/generate-finetuned")
async def generate_tts_finetuned(
    model_dir: str = Form(...),
    text: str = Form(...),
):
    """파인튜닝된 모델로 TTS 생성"""
    import json as json_mod

    if not text.strip():
        raise HTTPException(400, "텍스트를 입력해주세요")

    meta_path = os.path.join(model_dir, "meta.json")
    if not os.path.exists(meta_path):
        raise HTTPException(404, "파인튜닝 모델을 찾을 수 없습니다")

    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json_mod.load(f)

    today = datetime.now().strftime("%Y%m%d")
    output_id = uuid.uuid4().hex[:8]
    output_dir = OUTPUTS_DIR / today
    output_dir.mkdir(exist_ok=True)
    audio_path = str(output_dir / f"{output_id}_ft.wav")

    try:
        def _run():
            from tts.korean_normalizer import normalize_korean_text
            normalized = normalize_korean_text(text)
            model, config = load_finetuned_model(
                meta["model_path"], meta["config_path"], meta["vocab_path"]
            )
            output = model.synthesize(
                normalized,
                config,
                speaker_wav=meta["speaker_ref"],
                language="ko",
                temperature=0.65,
                repetition_penalty=10.0,
                top_p=0.85,
                top_k=50,
            )
            import soundfile
            soundfile.write(audio_path, output["wav"], 24000)
            del model
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

        await asyncio.get_event_loop().run_in_executor(None, _run)
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"TTS 생성 실패: {str(e)}")

    return {
        "success": True,
        "audio_url": f"/outputs/{today}/{output_id}_ft.wav",
        "message": "파인튜닝 모델 TTS 생성 완료!",
    }


# ─── TTS 검색 API ─────────────────────────────────────────────

@app.get("/api/tts/search")
async def search_tts(
    page: int = 1,
    per_page: int = 20,
    search: str = "",
    voice_name: str = "",
):
    """TTS 결과 검색 (페이지네이션 + 필터링)"""
    return await search_tts_results(page=page, per_page=per_page, search=search, voice_name=voice_name)


@app.get("/api/voice/names")
async def list_voice_names():
    """목소리 이름 목록 (검색 필터용)"""
    names = await get_voice_names()
    return {"names": names}


# ─── 즐겨찾기 API ─────────────────────────────────────────────

@app.post("/api/voice/{voice_id}/favorite")
async def api_toggle_favorite(voice_id: int):
    """목소리 즐겨찾기 토글"""
    is_fav = await toggle_favorite(voice_id)
    return {"success": True, "is_favorite": is_fav}


# ─── 백업 API ──────────────────────────────────────────────────

@app.get("/api/voice/{voice_id}/download")
async def download_voice(voice_id: int):
    """목소리 WAV 파일 다운로드 (백업용)"""
    voice = await get_voice(voice_id)
    if not voice:
        raise HTTPException(404, "목소리를 찾을 수 없습니다")
    file_path = Path(voice["file_path"])
    if not file_path.exists():
        raise HTTPException(404, "파일을 찾을 수 없습니다")
    return FileResponse(
        str(file_path),
        media_type="audio/wav",
        filename=f"{voice['name']}.wav"
    )


@app.get("/api/backup/voices")
async def backup_all_voices():
    """모든 목소리를 ZIP으로 백업 다운로드"""
    import zipfile

    voices = await get_all_voices()
    if not voices:
        raise HTTPException(404, "백업할 목소리가 없습니다")

    zip_path = Path("temp") / f"voices_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    zip_path.parent.mkdir(exist_ok=True)

    with zipfile.ZipFile(str(zip_path), 'w', zipfile.ZIP_DEFLATED) as zf:
        for v in voices:
            file_path = Path(v["file_path"])
            if file_path.exists():
                zf.write(str(file_path), f"{v['name']}_{v['id']}.wav")

    return FileResponse(
        str(zip_path),
        media_type="application/zip",
        filename=f"myvoce_backup_{datetime.now().strftime('%Y%m%d')}.zip"
    )


# ─── 메인 페이지 ─────────────────────────────────────────────

# ══════════════════════════════
#   음성 파라미터 튜닝 API
# ══════════════════════════════

@app.post("/api/tts/preview-params")
async def preview_with_params(
    voice_id: int = Form(...),
    text: str = Form(...),
    params_json: str = Form(...),
):
    """커스텀 파라미터로 빠른 미리듣기"""
    import json as json_mod
    voice = await get_voice(voice_id)
    if not voice:
        raise HTTPException(404, "목소리를 찾을 수 없습니다")

    try:
        custom_params = json_mod.loads(params_json)
    except Exception:
        raise HTTPException(400, "파라미터 JSON 형식 오류")

    # 파라미터 범위 클램핑
    custom_params["temperature"] = max(0.1, min(1.0, custom_params.get("temperature", 0.65)))
    custom_params["speed"] = max(0.5, min(2.0, custom_params.get("speed", 1.0)))
    custom_params["pitch_shift"] = max(-5.0, min(5.0, custom_params.get("pitch_shift", 0)))
    custom_params["top_p"] = max(0.5, min(1.0, custom_params.get("top_p", 0.85)))
    custom_params["top_k"] = max(10, min(100, int(custom_params.get("top_k", 50))))
    custom_params["repetition_penalty"] = max(1.0, min(15.0, custom_params.get("repetition_penalty", 10.0)))
    custom_params["bass_boost_db"] = max(-3.0, min(6.0, custom_params.get("bass_boost_db", 2.0)))
    custom_params["bass_boost_freq"] = max(100, min(400, custom_params.get("bass_boost_freq", 200)))

    temp_id = uuid.uuid4().hex[:8]
    output_path = f"temp/tuning/{temp_id}.wav"
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    engine = get_engine()
    try:
        def _gen():
            engine.generate_single_line(
                text=text,
                speaker_wav=voice["file_path"],
                output_path=output_path,
                custom_params=custom_params,
            )
        await asyncio.get_event_loop().run_in_executor(None, _gen)
    except Exception as e:
        raise HTTPException(500, f"TTS 생성 실패: {e}")

    return {"audio_url": f"/api/tts/tuning-audio/{temp_id}", "temp_id": temp_id}


@app.get("/api/tts/tuning-audio/{temp_id}")
async def get_tuning_audio(temp_id: str):
    """튜닝 미리듣기 오디오 다운로드"""
    path = Path(f"temp/tuning/{temp_id}.wav")
    if not path.exists():
        raise HTTPException(404, "오디오 파일 없음")
    return FileResponse(str(path), media_type="audio/wav")


@app.post("/api/voice/{voice_id}/checkpoint")
async def create_checkpoint(voice_id: int, name: str = Form(...), params_json: str = Form(...)):
    """체크포인트 저장"""
    cp_id = await save_checkpoint(voice_id, name, params_json)
    return {"id": cp_id, "message": f"체크포인트 '{name}' 저장됨"}


@app.get("/api/voice/{voice_id}/checkpoints")
async def list_checkpoints(voice_id: int):
    """체크포인트 목록"""
    cps = await get_checkpoints(voice_id)
    return {"checkpoints": cps}


@app.post("/api/voice/{voice_id}/checkpoint/{cp_id}/activate")
async def activate_checkpoint(voice_id: int, cp_id: int):
    """체크포인트 활성화"""
    await set_active_checkpoint(voice_id, cp_id)
    return {"message": "활성화 완료"}


@app.delete("/api/voice/checkpoint/{cp_id}")
async def remove_checkpoint(cp_id: int):
    """체크포인트 삭제"""
    await delete_checkpoint(cp_id)
    return {"message": "삭제 완료"}


@app.get("/api/voice/{voice_id}/active-params")
async def get_voice_active_params(voice_id: int):
    """활성 체크포인트 파라미터 조회"""
    cp = await get_active_checkpoint(voice_id)
    if cp:
        return {"has_active": True, "checkpoint": cp}
    return {"has_active": False, "checkpoint": None}


@app.post("/api/tts/feedback")
async def submit_feedback(
    checkpoint_id: int = Form(0),
    voice_id: int = Form(...),
    params_json: str = Form(...),
    score: int = Form(...),
    preview_text: str = Form(""),
):
    """피드백 저장 (+1 또는 -1)"""
    if score not in (1, -1):
        raise HTTPException(400, "score는 1 또는 -1이어야 합니다")
    await save_feedback(checkpoint_id, voice_id, params_json, score, preview_text)
    return {"message": "피드백 저장됨"}


@app.post("/api/tts/translate-generate")
async def translate_and_generate(request: Request):
    """텍스트를 번역 후 다국어 TTS 생성"""
    data = await request.json()
    text = data.get("text", "").strip()
    target_lang = data.get("target_lang", "zh")  # zh, ja, en
    voice_id = data.get("voice_id")

    if not text:
        raise HTTPException(400, "텍스트를 입력해주세요")

    # 번역
    translated = text
    try:
        import translators as ts
        lang_map = {"zh": "zh", "ja": "ja", "en": "en"}
        target = lang_map.get(target_lang, "zh")
        translated = ts.translate_text(text, translator='google',
                                        from_language='ko', to_language=target)
    except Exception as e:
        raise HTTPException(500, f"번역 실패: {str(e)}")

    # TTS 생성
    tts_lang_map = {"zh": "zh-cn", "ja": "ja", "en": "en"}
    tts_language = tts_lang_map.get(target_lang, "zh-cn")

    audio_path = str(CHINESE_AUDIO_DIR / f"translate_{uuid.uuid4().hex[:8]}.wav")
    try:
        engine = get_engine()
        ref_voice = None
        if voice_id:
            voice = await get_voice(int(voice_id))
            if voice and voice.get("file_path") and os.path.exists(voice["file_path"]):
                ref_voice = voice["file_path"]

        def _gen():
            nonlocal ref_voice
            if not ref_voice:
                voices = list(Path("voice_samples").glob("*.wav"))
                ref_voice = str(voices[0]) if voices else None
            if not ref_voice:
                raise Exception("음성 파일 없음")
            processed = preprocess_tts_text(translated, tts_language)
            wav_list = engine.tts.tts(text=processed, speaker_wav=ref_voice, language=tts_language)
            wav = np.array(wav_list, dtype=np.float32)
            sf.write(audio_path, wav, 24000)

        await asyncio.get_event_loop().run_in_executor(None, _gen)
    except Exception as e:
        raise HTTPException(500, f"TTS 생성 실패: {str(e)}")

    return {
        "original": text,
        "translated": translated,
        "target_lang": target_lang,
        "audio_path": audio_path,
    }


@app.get("/api/tts/translate-audio/{filename}")
async def get_translate_audio(filename: str):
    """번역 TTS 오디오 파일 반환"""
    file_path = CHINESE_AUDIO_DIR / filename
    if not file_path.exists():
        raise HTTPException(404)
    return FileResponse(str(file_path), media_type="audio/wav")


@app.get("/promo")
async def promo_page():
    """홍보용 프로모 페이지"""
    return FileResponse("frontend/promo.html")


# ─── 중국어 발음 학습 ──────────────────────────────────────
CHINESE_AUDIO_DIR = Path("outputs/chinese")
CHINESE_AUDIO_DIR.mkdir(parents=True, exist_ok=True)


# ── TTS 텍스트 전처리 (숫자/특수어 현지 발음 변환) ──
import re as _re_global

_ZH_DIGITS = '零一二三四五六七八九'
_JA_DIGITS = ['ゼロ','いち','に','さん','よん','ご','ろく','なな','はち','きゅう']

# 영문 약어 → 각국 발음
_ABBR_ZH = {
    'AI': '人工智能', 'GPS': '全球定位系统', 'IT': '信息技术', 'PC': '个人电脑',
    'USB': '优盘接口', 'WiFi': '无线网络', 'APP': '应用程序', 'VR': '虚拟现实',
    'CEO': '首席执行官', 'SNS': '社交网络', 'OK': '好的', 'DNA': '脱氧核糖核酸',
}
_ABBR_JA = {
    'AI': 'エーアイ', 'GPS': 'ジーピーエス', 'IT': 'アイティー', 'PC': 'パソコン',
    'USB': 'ユーエスビー', 'WiFi': 'ワイファイ', 'APP': 'アプリ', 'VR': 'ブイアール',
    'CEO': 'シーイーオー', 'SNS': 'エスエヌエス', 'OK': 'オッケー', 'DNA': 'ディーエヌエー',
    'TV': 'テレビ', 'CD': 'シーディー', 'DVD': 'ディーブイディー',
}
# 영문 알파벳 개별 발음
_ALPHA_ZH = {c: n for c, n in zip('ABCDEFGHIJKLMNOPQRSTUVWXYZ',
    ['诶','必','希','地','衣','爱抚','吉','爱吃','爱','杰','开','爱乐','爱母',
     '恩','哦','屁','酷','啊','爱死','替','优','微','达不溜','爱克斯','歪','贼'])}
_ALPHA_JA = {c: n for c, n in zip('ABCDEFGHIJKLMNOPQRSTUVWXYZ',
    ['エー','ビー','シー','ディー','イー','エフ','ジー','エイチ','アイ','ジェー','ケー','エル','エム',
     'エヌ','オー','ピー','キュー','アール','エス','ティー','ユー','ブイ','ダブリュー','エックス','ワイ','ゼット'])}


def _convert_num_zh(n):
    """정수를 중국어로 변환"""
    if n < 0: return '负' + _convert_num_zh(-n)
    if n < 10: return _ZH_DIGITS[n]
    if n < 100:
        t, o = divmod(n, 10)
        r = ('' if t == 1 else _ZH_DIGITS[t]) + '十'
        return r if o == 0 else r + _ZH_DIGITS[o]
    if n < 1000:
        h, rest = divmod(n, 100)
        r = _ZH_DIGITS[h] + '百'
        if rest == 0: return r
        if rest < 10: return r + '零' + _ZH_DIGITS[rest]
        return r + _convert_num_zh(rest)
    if n < 10000:
        th, rest = divmod(n, 1000)
        r = _ZH_DIGITS[th] + '千'
        if rest == 0: return r
        if rest < 100: return r + '零' + _convert_num_zh(rest)
        return r + _convert_num_zh(rest)
    w, rest = divmod(n, 10000)
    r = _convert_num_zh(w) + '万'
    if rest == 0: return r
    if rest < 1000: return r + '零' + _convert_num_zh(rest)
    return r + _convert_num_zh(rest)


def _convert_num_ja(n):
    """정수를 일본어로 변환"""
    if n < 0: return 'マイナス' + _convert_num_ja(-n)
    if n < 10: return _JA_DIGITS[n]
    if n < 100:
        t, o = divmod(n, 10)
        r = ('' if t == 1 else _JA_DIGITS[t]) + 'じゅう'
        return r if o == 0 else r + _JA_DIGITS[o]
    if n < 1000:
        h, rest = divmod(n, 100)
        p = {3:'さんびゃく', 6:'ろっぴゃく', 8:'はっぴゃく'}.get(h)
        r = p if p else (_JA_DIGITS[h] if h > 1 else '') + 'ひゃく'
        if rest == 0: return r
        return r + _convert_num_ja(rest)
    if n < 10000:
        th, rest = divmod(n, 1000)
        p = {3:'さんぜん', 8:'はっせん'}.get(th)
        r = p if p else (_JA_DIGITS[th] if th > 1 else '') + 'せん'
        if rest == 0: return r
        return r + _convert_num_ja(rest)
    w, rest = divmod(n, 10000)
    r = _convert_num_ja(w) + 'まん'
    if rest == 0: return r
    return r + _convert_num_ja(rest)


_pinyin_dict = None
def _load_pinyin_dict():
    global _pinyin_dict
    import json as _j
    p = Path("database/pinyin_to_hanzi.json")
    if p.exists() and not _pinyin_dict:
        _pinyin_dict = _j.loads(p.read_text())
        print(f"[OK] 병음→한자 사전 로드: 음절 {len(_pinyin_dict.get('syllable',{}))}개, 단어 {len(_pinyin_dict.get('word',{}))}개")

def pinyin_text_to_hanzi(text):
    """병음 텍스트를 한자로 변환 (TTS용)"""
    if not _pinyin_dict:
        _load_pinyin_dict()
    if not _pinyin_dict:
        return text
    # 한자가 이미 있으면 그대로
    if any('\u4e00' <= ch <= '\u9fff' for ch in text):
        return text
    # 성조 기호가 있는지 (병음인지 판단)
    tone_chars = set('āáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜ')
    if not any(ch in tone_chars for ch in text):
        return text

    word_map = _pinyin_dict.get('word', {})
    syl_map = _pinyin_dict.get('syllable', {})
    parts = text.strip().split()
    result = []
    i = 0
    while i < len(parts):
        matched = False
        for length in range(min(4, len(parts) - i), 0, -1):
            combo = ''.join(parts[i:i+length])
            if combo in word_map:
                result.append(word_map[combo])
                i += length
                matched = True
                break
        if not matched:
            p = parts[i]
            if p in syl_map:
                result.append(syl_map[p])
            else:
                result.append(p)
            i += 1
    return ''.join(result)

_load_pinyin_dict()


def preprocess_tts_text(text, lang="zh-cn"):
    """TTS 전송 전 병음→한자 + 숫자/약어 현지 발음 변환"""
    if lang == "zh-cn":
        # 병음만 있는 텍스트는 한자로 변환
        text = pinyin_text_to_hanzi(text)
        abbrs, alpha, num_fn = _ABBR_ZH, _ALPHA_ZH, _convert_num_zh
    elif lang == "ja":
        abbrs, alpha, num_fn = _ABBR_JA, _ALPHA_JA, _convert_num_ja
    else:
        return text  # 영어 등은 그대로

    # 1) 약어 변환 (대소문자 무시)
    for en, local in sorted(abbrs.items(), key=lambda x: -len(x[0])):
        text = _re_global.sub('(?<![A-Za-z])' + en + '(?![A-Za-z])', local, text, flags=_re_global.IGNORECASE)

    # 2) 남은 영문 대문자 연속 → 개별 알파벳 발음
    def alpha_replace(m):
        return ''.join(alpha.get(c, c) for c in m.group(0))
    text = _re_global.sub(r'[A-Z]{2,}', alpha_replace, text)

    # 3) 숫자 변환
    def num_replace(m):
        s = m.group(0)
        n = None
        try: n = int(s)
        except: pass
        # 소수점
        if '.' in s:
            parts = s.split('.')
            dec_point = '点' if lang == 'zh-cn' else 'てん'
            digits_fn = (lambda d: _ZH_DIGITS[int(d)]) if lang == 'zh-cn' else (lambda d: _JA_DIGITS[int(d)])
            return num_fn(int(parts[0])) + dec_point + ''.join(digits_fn(d) for d in parts[1])
        # 전화번호/긴 숫자(4자리+)는 한 자리씩
        if n is not None and (len(s) >= 4 or (len(s) == 3 and n in [110, 119, 120])):
            if lang == 'zh-cn':
                return ' '.join(_ZH_DIGITS[int(d)] for d in s)
            else:
                return ' '.join(_JA_DIGITS[int(d)] for d in s)
        if n is not None:
            return num_fn(n)
        return s
    text = _re_global.sub(r'\d+\.?\d*', num_replace, text)

    return text


@app.get("/chinese")
async def chinese_page():
    """중국어 발음 학습 페이지"""
    return FileResponse("frontend/chinese.html")


@app.get("/japanese")
async def japanese_page():
    """일본어 발음 학습 페이지"""
    return FileResponse("frontend/japanese.html")


@app.post("/api/chinese/card")
async def create_chinese_card(request: Request):
    """학습 카드 등록 (자동 병음/성조 생성)"""
    data = await request.json()
    chinese = data.get("chinese", "").strip()
    meaning_ko = data.get("meaning_ko", "").strip()
    category = data.get("category", "general")
    user_id = request.state.user_id

    if not chinese:
        raise HTTPException(400, "중국어 텍스트를 입력해주세요")

    from pypinyin import pinyin, Style
    # 병음 생성 (성조 포함)
    pinyin_list = pinyin(chinese, style=Style.TONE)
    pinyin_str = " ".join([p[0] for p in pinyin_list])
    # 성조 번호 추출
    tone_list = pinyin(chinese, style=Style.TONE3)
    tones = " ".join([p[0] for p in tone_list])

    card_id = await save_chinese_card(
        user_id=user_id, chinese=chinese, pinyin=pinyin_str,
        tones=tones, meaning_ko=meaning_ko, category=category
    )
    card = await get_chinese_card(card_id)
    return {"card": card}


@app.get("/api/chinese/cards")
async def list_chinese_cards(request: Request, category: str = None):
    """학습 카드 목록 조회"""
    user_id = request.state.user_id
    cards = await get_chinese_cards(user_id, category)
    return {"cards": cards}


@app.get("/api/chinese/card/{card_id}")
async def get_card_detail(card_id: int, request: Request):
    """학습 카드 상세 + 녹음 이력"""
    card = await get_chinese_card(card_id)
    if not card:
        raise HTTPException(404, "카드를 찾을 수 없습니다")
    records = await get_pronunciation_records(card_id, request.state.user_id)
    return {"card": card, "records": records}


@app.delete("/api/chinese/card/{card_id}")
async def remove_chinese_card(card_id: int, request: Request):
    """학습 카드 삭제"""
    card = await get_chinese_card(card_id)
    if not card:
        raise HTTPException(404, "카드를 찾을 수 없습니다")
    # 관련 오디오 파일 정리
    if card.get("ref_audio_path") and os.path.exists(card["ref_audio_path"]):
        os.remove(card["ref_audio_path"])
    records = await get_pronunciation_records(card_id)
    for r in records:
        if r.get("audio_path") and os.path.exists(r["audio_path"]):
            os.remove(r["audio_path"])
    await delete_chinese_card(card_id)
    return {"message": "삭제 완료"}


@app.get("/api/chinese/voices")
async def list_chinese_voices():
    """중국어 TTS용 음성 목록 (즐겨찾기 우선, 전체도 포함)"""
    voices = await get_all_voices()
    result = []
    for v in voices:
        if v.get("file_path"):
            result.append({
                "id": v["id"], "name": v["name"],
                "duration": v.get("duration", 0),
                "is_favorite": bool(v.get("is_favorite", 0))
            })
    return {"voices": result}


@app.post("/api/chinese/tts-test")
async def tts_test(request: Request):
    """목소리 테스트용 TTS (텍스트 직접 입력)"""
    data = await request.json()
    text = data.get("text", "你好")
    voice_id = data.get("voice_id")

    audio_path = str(CHINESE_AUDIO_DIR / f"test_{uuid.uuid4().hex[:8]}.wav")
    try:
        engine = get_engine()
        ref_voice = None
        if voice_id:
            voice = await get_voice(int(voice_id))
            if voice and voice.get("file_path") and os.path.exists(voice["file_path"]):
                ref_voice = voice["file_path"]

        def _gen():
            nonlocal ref_voice
            if not ref_voice:
                voices = list(Path("voice_samples").glob("*.wav"))
                ref_voice = str(voices[0]) if voices else None
            if not ref_voice:
                raise Exception("음성 없음")
            tts_language = data.get("language", "zh-cn")
            processed = preprocess_tts_text(text, tts_language)
            wav_list = engine.tts.tts(text=processed, speaker_wav=ref_voice, language=tts_language)
            wav = np.array(wav_list, dtype=np.float32)
            sf.write(audio_path, wav, 24000)

        await asyncio.get_event_loop().run_in_executor(None, _gen)
    except Exception as e:
        raise HTTPException(500, str(e))

    return FileResponse(audio_path, media_type="audio/wav")


@app.post("/api/chinese/card/{card_id}/ref-audio")
async def generate_ref_audio(card_id: int, request: Request):
    """참조 발음 TTS 생성 (음성 선택 가능)"""
    card = await get_chinese_card(card_id)
    if not card:
        raise HTTPException(404, "카드를 찾을 수 없습니다")

    # voice_id + text_override 파라미터
    voice_id = None
    text_override = None
    try:
        data = await request.json()
        voice_id = data.get("voice_id")
        text_override = data.get("text_override")
        tts_lang = data.get("language", "zh-cn")
    except Exception:
        tts_lang = "zh-cn"

    chinese_text = text_override if text_override else card["chinese"]
    audio_path = str(CHINESE_AUDIO_DIR / f"ref_{card_id}_{uuid.uuid4().hex[:8]}.wav")

    try:
        engine = get_engine()

        # 음성 파일 결정
        ref_voice = None
        if voice_id:
            voice = await get_voice(int(voice_id))
            if voice and voice.get("file_path") and os.path.exists(voice["file_path"]):
                ref_voice = voice["file_path"]

        def _generate():
            import traceback as tb
            nonlocal ref_voice
            try:
                if not ref_voice:
                    voices = list(Path("voice_samples").glob("*.wav"))
                    if not voices:
                        raise Exception("참조 음성 파일이 없습니다")
                    ref_voice = str(voices[0])
                processed_text = preprocess_tts_text(chinese_text, tts_lang)
                print(f"[TTS] text={processed_text}, ref={ref_voice}, lang={tts_lang}")

                # 긴 문장은 청크로 나눠서 생성
                import re as _re_tts
                if len(processed_text) > 15:
                    chunks = _re_tts.split(r'[。，！？、；]', processed_text)
                    chunks = [c.strip() for c in chunks if c.strip()]
                    if len(chunks) > 1:
                        wavs = []
                        for chunk in chunks:
                            wl = engine.tts.tts(text=chunk, speaker_wav=ref_voice, language=tts_lang)
                            wavs.append(np.array(wl, dtype=np.float32))
                            wavs.append(np.zeros(int(24000 * 0.15), dtype=np.float32))
                        wav = np.concatenate(wavs)
                        sf.write(audio_path, wav, 24000)
                    else:
                        wav_list = engine.tts.tts(text=processed_text, speaker_wav=ref_voice, language=tts_lang)
                        wav = np.array(wav_list, dtype=np.float32)
                        sf.write(audio_path, wav, 24000)
                else:
                    wav_list = engine.tts.tts(text=processed_text, speaker_wav=ref_voice, language=tts_lang)
                    wav = np.array(wav_list, dtype=np.float32)
                    sf.write(audio_path, wav, 24000)
                print(f"[중국어TTS] 생성 완료: {len(wav)} samples -> {audio_path}")
            except Exception as e:
                print(f"[중국어TTS] 에러: {e}")
                tb.print_exc()
                raise

        await asyncio.get_event_loop().run_in_executor(None, _generate)
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"참조 발음 생성 실패: {str(e)}")

    await update_chinese_card(card_id, ref_audio_path=audio_path)
    return {"audio_path": audio_path}


@app.get("/api/chinese/audio/{card_id}/ref")
async def get_ref_audio(card_id: int):
    """참조 발음 오디오 (김준용)"""
    card = await get_chinese_card(card_id)
    if not card or not card.get("ref_audio_path"):
        raise HTTPException(404, "참조 발음이 없습니다")
    if not os.path.exists(card["ref_audio_path"]):
        raise HTTPException(404, "오디오 파일 없음")
    return FileResponse(card["ref_audio_path"], media_type="audio/wav")


@app.get("/api/chinese/audio/{card_id}/ref2")
async def get_ref_audio_2(card_id: int):
    """참조 발음 오디오 (수민)"""
    card = await get_chinese_card(card_id)
    if not card or not card.get("ref_audio_path_2"):
        raise HTTPException(404, "참조 발음이 없습니다")
    if not os.path.exists(card["ref_audio_path_2"]):
        raise HTTPException(404, "오디오 파일 없음")
    return FileResponse(card["ref_audio_path_2"], media_type="audio/wav")


@app.post("/api/chinese/card/{card_id}/record")
async def record_pronunciation(card_id: int, request: Request, file: UploadFile = File(...)):
    """발음 녹음 업로드 + 자동 평가"""
    card = await get_chinese_card(card_id)
    if not card:
        raise HTTPException(404, "카드를 찾을 수 없습니다")

    user_id = request.state.user_id
    # 일본어 과목인지 확인
    is_japanese = card.get("subject", "") in ("기초여행일본어", "초급일본어문형연습")
    eval_lang = "ja" if is_japanese else "zh"

    # 파일 저장
    filename = f"pron_{card_id}_{uuid.uuid4().hex[:8]}.wav"
    audio_path = str(CHINESE_AUDIO_DIR / filename)
    async with aiofiles.open(audio_path, "wb") as f:
        content = await file.read()
        await f.write(content)

    # STT + 평가
    stt_text = ""
    score = 0
    tone_score = 0
    tone_detail = ""
    feedback = ""

    try:
        multi_results = []

        def _evaluate_japanese(stt_text_local, card):
            """일본어 발음 평가 — 히라가나 + 모라 + Levenshtein 비교"""
            import json as _json
            import re as _re
            import pykakasi
            import jaconv
            import Levenshtein

            kks = pykakasi.kakasi()
            punct = _re.compile('[、。！？・\\s,.!?()（）「」『』\\d]')

            # 원문/인식 텍스트 정리
            orig_text = punct.sub('', card["chinese"])
            recog_text = punct.sub('', stt_text_local)

            if not recog_text:
                return stt_text_local, 0, 0, 0, "", "음성이 인식되지 않았습니다. 더 크게 말해보세요."

            # 히라가나로 통일 (가타카나 → 히라가나)
            orig_result = kks.convert(orig_text)
            recog_result = kks.convert(recog_text)

            orig_hira = jaconv.kata2hira(''.join([r['hira'] for r in orig_result]))
            recog_hira = jaconv.kata2hira(''.join([r['hira'] for r in recog_result]))

            # 로마자 변환 (cutlet 사용)
            try:
                import cutlet
                katsu = cutlet.Cutlet()
                orig_roma = katsu.romaji(orig_text).lower().replace(' ', '')
                recog_roma = katsu.romaji(recog_text).lower().replace(' ', '')
            except Exception:
                orig_roma = ''.join([r['hepburn'] for r in orig_result])
                recog_roma = ''.join([r['hepburn'] for r in recog_result])

            # ── 3단계 점수 계산 ──

            # 1) 히라가나 Levenshtein 유사도 (메인 점수)
            hira_ratio = Levenshtein.ratio(orig_hira, recog_hira)
            hira_score = int(hira_ratio * 100)

            # 2) 로마자 유사도 (보조 — 비슷한 발음 인정)
            roma_ratio = Levenshtein.ratio(orig_roma, recog_roma)
            roma_score = int(roma_ratio * 100)

            # 3) 단어 단위 비교 (형태소 분석)
            orig_words = [r['hira'] for r in orig_result if r['hira'].strip()]
            recog_words = [r['hira'] for r in recog_result if r['hira'].strip()]
            word_match = sum(1 for a, b in zip(orig_words, recog_words) if a == b)
            word_score = int(word_match / max(len(orig_words), 1) * 100) if orig_words else 0

            # 최종 점수 (히라가나 60% + 로마자 25% + 단어 15%)
            final_score = int(hira_score * 0.6 + roma_score * 0.25 + word_score * 0.15)

            # 길이 차이 페널티
            len_diff = abs(len(orig_hira) - len(recog_hira))
            if len_diff > 2:
                final_score = max(0, final_score - min(15, len_diff * 2))

            # ── 글자별 상세 비교 (원문 형태소 단위) ──
            details = []
            for wi, orig_item in enumerate(orig_result):
                o_orig = orig_item['orig']
                o_hira = jaconv.kata2hira(orig_item['hira'])
                o_roma = orig_item['hepburn']

                # 인식 결과에서 매칭
                if wi < len(recog_result):
                    r_item = recog_result[wi]
                    r_hira = jaconv.kata2hira(r_item['hira'])
                    r_roma = r_item['hepburn']
                    r_orig = r_item['orig']

                    if o_hira == r_hira:
                        status = "perfect"
                    elif Levenshtein.ratio(o_hira, r_hira) >= 0.6:
                        status = "tone_wrong"  # 비슷하지만 정확하지 않음
                    else:
                        status = "wrong"
                else:
                    r_hira = ""
                    r_roma = ""
                    r_orig = ""
                    status = "missing"

                details.append({
                    "char": o_orig,
                    "expected_pinyin": o_hira,
                    "expected_tone": o_roma,
                    "your_char": r_orig,
                    "your_pinyin": r_hira,
                    "your_tone": r_roma,
                    "status": status,
                })

            # 인식이 더 긴 경우
            for wi in range(len(orig_result), len(recog_result)):
                r_item = recog_result[wi]
                details.append({
                    "char": "", "expected_pinyin": "", "expected_tone": "",
                    "your_char": r_item['orig'],
                    "your_pinyin": jaconv.kata2hira(r_item['hira']),
                    "your_tone": r_item['hepburn'],
                    "status": "extra",
                })

            # ── 피드백 ──
            fb_parts = []
            if final_score >= 95:
                fb_parts.append("完璧！발음이 매우 정확합니다!")
            elif final_score >= 80:
                fb_parts.append("上手！대체로 좋습니다.")
            elif final_score >= 60:
                fb_parts.append("もう少し！조금 더 연습해보세요.")
            else:
                fb_parts.append("もう一度！다시 들어보고 연습하세요.")

            # 틀린 단어 지적
            wrong = [d for d in details if d["status"] == "wrong"]
            similar = [d for d in details if d["status"] == "tone_wrong"]
            if wrong:
                fb_parts.append(f"틀림: {''.join([d['char'] for d in wrong[:3]])}({'/'.join([d['expected_pinyin'] for d in wrong[:3]])})")
            if similar:
                fb_parts.append(f"비슷: {''.join([d['char'] for d in similar[:3]])}({'/'.join([d['your_pinyin'] for d in similar[:3]])}→{'/'.join([d['expected_pinyin'] for d in similar[:3]])})")

            fb_parts.append(f"[ひらがな {hira_score} / ローマ字 {roma_score} / 単語 {word_score}]")

            tone_det = _json.dumps(details, ensure_ascii=False)
            # pron_score=hira_score, tone_score=roma_score (일본어는 성조 대신 로마자 점수)
            return stt_text_local, final_score, hira_score, roma_score, tone_det, " ".join(fb_parts)

        def _evaluate():
            nonlocal stt_text, score, tone_score, tone_detail, feedback, multi_results
            import json as _json

            import functools as _ft
            @_ft.lru_cache(maxsize=1)
            def _get_whisper_model():
                from faster_whisper import WhisperModel
                return WhisperModel("large-v3", device="cuda", device_index=1, compute_type="float16")

            # STT 인식
            model = _get_whisper_model()

            if eval_lang == "ja":
                # ── 일본어 평가 ──
                hint_ja = card["chinese"].replace("。", "").replace("、", "")
                segments_list = list(model.transcribe(audio_path, language="ja",
                                                  initial_prompt=f"以下は日本語の発音練習：{hint_ja}",
                                                  vad_filter=True)[0])
                stt_raw = "".join([s.text for s in segments_list]).strip()
                stt_text_local = stt_raw
                return _evaluate_japanese(stt_text_local, card)

            # ── 중국어 평가 ──
            # 원문을 힌트로 제공하여 인식 정확도 향상
            hint = card["chinese"].replace("。", "").replace("，", "").replace("！", "").replace("？", "")
            segments_list = list(model.transcribe(audio_path, language="zh",
                                              initial_prompt=f"以下是中文发音练习：{hint}",
                                              vad_filter=True,
                                              vad_parameters=dict(min_silence_duration_ms=500))[0])
            import opencc
            converter = opencc.OpenCC('t2s')
            seg_texts = [converter.convert(seg.text.strip()) for seg in segments_list if seg.text.strip()]
            stt_raw = "".join([s.text for s in segments_list]).strip()
            stt_text_local = converter.convert(stt_raw)

            # 구두점 제거
            import re as _re
            punct = _re.compile('[，。！？、；：\u201c\u201d\u2018\u2019（）\\s,.!?;:\\\'"()\\u3000]')
            original = punct.sub('', converter.convert(card["chinese"]))
            recognized = punct.sub('', stt_text_local)

            if not recognized:
                return stt_text_local, 0, 0, "", "음성이 인식되지 않았습니다. 더 크게 말해보세요."

            # ── 병음 기반 글자별 상세 평가 ──
            from pypinyin import pinyin, Style

            # 원문 글자별 병음 (성조 변조 적용 — tone_sandhi=True)
            from pypinyin import lazy_pinyin as _lp
            orig_chars = list(original)
            # 성조 변조가 적용된 병음으로 비교 (실제 발음 기준)
            orig_tone_list = _lp(original, style=Style.TONE3, tone_sandhi=True) or []
            orig_notone_list = _lp(original, style=Style.NORMAL) or []
            orig_display_list = _lp(original, style=Style.TONE, tone_sandhi=True) or []
            # 글자 수에 맞게 패딩
            while len(orig_tone_list) < len(orig_chars): orig_tone_list.append('')
            while len(orig_notone_list) < len(orig_chars): orig_notone_list.append('')
            while len(orig_display_list) < len(orig_chars): orig_display_list.append('')
            orig_tone = orig_tone_list[:len(orig_chars)]
            orig_notone = orig_notone_list[:len(orig_chars)]
            orig_display = orig_display_list[:len(orig_chars)]

            # 인식도 성조 변조 적용
            recog_chars = list(recognized)
            recog_tone = _lp(recognized, style=Style.TONE3, tone_sandhi=True) or []
            recog_notone = _lp(recognized, style=Style.NORMAL) or []
            recog_display = _lp(recognized, style=Style.TONE, tone_sandhi=True) or []

            # 글자별 비교 결과 생성
            details = []
            pron_correct = 0  # 발음(성조 무시) 맞은 수
            tone_correct = 0  # 성조까지 맞은 수
            total = len(orig_chars)

            for i in range(total):
                d = {
                    "char": orig_chars[i],
                    "expected_pinyin": orig_display[i] if i < len(orig_display) else "",
                    "expected_tone": orig_tone[i] if i < len(orig_tone) else "",
                }

                if i < len(recog_chars):
                    d["your_char"] = recog_chars[i]
                    d["your_pinyin"] = recog_display[i] if i < len(recog_display) else ""
                    d["your_tone"] = recog_tone[i] if i < len(recog_tone) else ""

                    # 발음 비교 (성조 무시)
                    pron_ok = (i < len(orig_notone) and i < len(recog_notone)
                               and orig_notone[i] == recog_notone[i])
                    # 성조 비교
                    tone_ok = (i < len(orig_tone) and i < len(recog_tone)
                               and orig_tone[i] == recog_tone[i])

                    if tone_ok:
                        d["status"] = "perfect"  # 발음+성조 정확
                        pron_correct += 1
                        tone_correct += 1
                    elif pron_ok:
                        d["status"] = "tone_wrong"  # 발음 맞지만 성조 틀림
                        pron_correct += 1
                    else:
                        d["status"] = "wrong"  # 발음 자체가 틀림
                else:
                    d["your_char"] = ""
                    d["your_pinyin"] = ""
                    d["your_tone"] = ""
                    d["status"] = "missing"  # 발음 누락

                details.append(d)

            # 인식이 더 긴 경우 (불필요한 음절)
            for i in range(total, len(recog_chars)):
                details.append({
                    "char": "", "expected_pinyin": "", "expected_tone": "",
                    "your_char": recog_chars[i],
                    "your_pinyin": recog_display[i] if i < len(recog_display) else "",
                    "your_tone": recog_tone[i] if i < len(recog_tone) else "",
                    "status": "extra",
                })

            # ── 점수 계산 ──
            # 발음 점수 (50점 만점): 성조 무시, 순수 발음
            pron_score = int((pron_correct / total * 100)) if total > 0 else 0
            # 성조 점수 (50점 만점): 성조까지 정확
            tone_sc = int((tone_correct / total * 100)) if total > 0 else 0
            # 총점 (발음 50% + 성조 50%)
            final_score = int(pron_score * 0.5 + tone_sc * 0.5)

            # 길이 차이 페널티
            len_diff = abs(len(orig_chars) - len(recog_chars))
            if len_diff > 0:
                penalty = min(20, len_diff * 5)
                final_score = max(0, final_score - penalty)

            # tone_detail에 글자별 상세 JSON 저장
            tone_det = _json.dumps(details, ensure_ascii=False)

            # 피드백
            wrong_chars = [d for d in details if d["status"] in ("wrong", "missing")]
            tone_wrong_chars = [d for d in details if d["status"] == "tone_wrong"]

            fb_parts = []
            if final_score >= 90:
                fb_parts.append("발음이 매우 정확합니다!")
            elif final_score >= 70:
                fb_parts.append("대체로 좋습니다.")
            elif final_score >= 50:
                fb_parts.append("일부 발음을 교정해보세요.")
            else:
                fb_parts.append("참조 발음을 듣고 다시 연습해보세요.")

            if tone_wrong_chars:
                chars = "".join([d["char"] for d in tone_wrong_chars[:3]])
                fb_parts.append(f"성조 주의: {chars}")
            if wrong_chars:
                chars = "".join([d["char"] for d in wrong_chars[:3]])
                fb_parts.append(f"발음 틀림: {chars}")

            # ── 반복 발화 감지 ──
            # 인식된 전체 텍스트에서 원문 길이 단위로 잘라서 각각 평가
            orig_len = len(original)
            if orig_len >= 2 and len(recognized) >= orig_len * 1.5:
                # 인식 텍스트를 원문 길이로 슬라이딩 윈도우 분할
                chunks = []
                recog_chars = list(recognized)
                i = 0
                while i < len(recog_chars):
                    chunk = "".join(recog_chars[i:i + orig_len])
                    if len(chunk) >= max(2, orig_len - 1):
                        chunks.append(chunk)
                    i += orig_len

                for ci, chunk in enumerate(chunks):
                    c_tone = [p[0] for p in pinyin(chunk, style=Style.TONE3)]
                    c_notone = [p[0] for p in pinyin(chunk, style=Style.NORMAL)]
                    s_pron = sum(1 for a, b in zip(orig_notone, c_notone) if a == b)
                    s_tone = sum(1 for a, b in zip(orig_tone, c_tone) if a == b)
                    s_total = max(len(orig_tone), 1)
                    s_pron_score = int(s_pron / s_total * 100)
                    s_tone_score = int(s_tone / s_total * 100)
                    s_score = int(s_pron_score * 0.5 + s_tone_score * 0.5)
                    s_len_diff = abs(orig_len - len(chunk))
                    if s_len_diff > 0:
                        s_score = max(0, s_score - min(20, s_len_diff * 5))
                    multi_results.append({
                        "index": ci + 1,
                        "text": chunk,
                        "score": s_score,
                        "pron_score": s_pron_score,
                        "tone_score": s_tone_score,
                    })

            return stt_text_local, final_score, pron_score, tone_sc, tone_det, " ".join(fb_parts)

        stt_text, score, pron_sc, tone_score, tone_detail, feedback = await asyncio.get_event_loop().run_in_executor(None, _evaluate)
    except Exception as e:
        import traceback; traceback.print_exc()
        feedback = f"평가 중 오류: {str(e)}"
        pron_sc = 0

    # 반복 발화가 감지되면 각각을 별도 기록으로 저장
    if multi_results and len(multi_results) > 1:
        record_ids = []
        for mr in multi_results:
            rid = await save_pronunciation_record(
                card_id=card_id, user_id=user_id, audio_path=audio_path,
                stt_text=mr["text"], score=mr["score"], tone_score=mr["tone_score"],
                tone_detail="", feedback=f"연속 {mr['index']}번째"
            )
            record_ids.append(rid)
        # 대표 점수 = 최고 점수
        best_multi = max(multi_results, key=lambda x: x["score"])
        return {
            "record_id": record_ids[0],
            "stt_text": stt_text,
            "score": best_multi["score"],
            "pron_score": best_multi["pron_score"],
            "tone_score": best_multi["tone_score"],
            "tone_detail": tone_detail,
            "feedback": feedback,
            "multi_count": len(multi_results),
            "multi_results": multi_results,
        }
    else:
        record_id = await save_pronunciation_record(
            card_id=card_id, user_id=user_id, audio_path=audio_path,
            stt_text=stt_text, score=score, tone_score=tone_score,
            tone_detail=tone_detail, feedback=feedback
        )
        return {
            "record_id": record_id,
            "stt_text": stt_text,
            "score": score,
            "pron_score": pron_sc,
            "tone_score": tone_score,
            "tone_detail": tone_detail,
            "feedback": feedback,
            "multi_count": 1,
            "multi_results": [],
        }


@app.get("/api/chinese/audio/record/{record_id}")
async def get_record_audio(record_id: int):
    """발음 녹음 오디오 파일 반환"""
    from database.db import DB_PATH
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT audio_path FROM pronunciation_records WHERE id = ?", (record_id,))
        row = await cursor.fetchone()
    if not row or not row["audio_path"] or not os.path.exists(row["audio_path"]):
        raise HTTPException(404, "오디오 파일이 없습니다")
    return FileResponse(row["audio_path"], media_type="audio/wav")


@app.post("/api/chinese/record/{record_id}/star")
async def star_pronunciation(record_id: int):
    """발음 녹음 즐겨찾기 토글"""
    result = await toggle_pronunciation_star(record_id)
    return {"is_starred": result}


@app.delete("/api/chinese/record/{record_id}")
async def remove_pronunciation_record(record_id: int):
    """발음 녹음 삭제"""
    from database.db import DB_PATH
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT audio_path FROM pronunciation_records WHERE id = ?", (record_id,))
        row = await cursor.fetchone()
    if row and row["audio_path"] and os.path.exists(row["audio_path"]):
        os.remove(row["audio_path"])
    await delete_pronunciation_record(record_id)
    return {"message": "삭제 완료"}


@app.get("/api/chinese/stats")
async def chinese_stats(request: Request):
    """학습 통계"""
    user_id = request.state.user_id
    stats = await get_chinese_study_stats(user_id)
    return stats


# 중영 사전 로드 (서버 시작 시 한번만)
_cedict = {}
def _load_cedict():
    global _cedict
    import json as _json
    dict_path = Path("database/cedict_en.json")
    if dict_path.exists() and not _cedict:
        _cedict = _json.loads(dict_path.read_text(encoding="utf-8"))
        print(f"[OK] 중영사전 로드: {len(_cedict)}개 항목")
_load_cedict()


@app.get("/api/chinese/translate/{card_id}")
async def translate_card(card_id: int):
    """카드의 한국어/영어 번역 반환 (DB 캐시 우선)"""
    import re as _re
    card = await get_chinese_card(card_id)
    if not card:
        raise HTTPException(404)

    chinese = card["chinese"]
    meaning_ko = card.get("meaning_ko", "")
    meaning_en = card.get("meaning_en", "")
    clean = _re.sub(r'[，。！？、；：\s,.!?]', '', chinese)
    has_hanzi = any('\u4e00' <= ch <= '\u9fff' for ch in clean)

    # DB에 이미 둘 다 있으면 바로 반환 (빠름!)
    if meaning_ko and meaning_en:
        return {
            "chinese": chinese, "english": meaning_en, "english_source": "캐시",
            "meaning_ko": meaning_ko, "ko_source": "캐시",
        }

    # 영어: DB > CEDICT > 번역
    eng = meaning_en
    eng_source = "캐시"
    if not eng:
        if not _cedict: _load_cedict()
        eng = _cedict.get(clean, "")
        eng_source = "사전"
        if not eng and len(clean) >= 2 and has_hanzi:
            parts = []
            i = 0
            while i < len(clean):
                matched = False
                for length in range(min(6, len(clean) - i), 0, -1):
                    word = clean[i:i+length]
                    if word in _cedict:
                        parts.append(_cedict[word])
                        i += length
                        matched = True
                        break
                if not matched:
                    parts.append(clean[i])
                    i += 1
                eng = "; ".join(parts) if parts else ""

    # 한국어: DB > 번역
    ko_translated = False
    if not meaning_ko and has_hanzi and len(clean) >= 2:
        try:
            import translators as ts
            meaning_ko = ts.translate_text(chinese, translator='google',
                                            from_language='zh', to_language='ko')
            ko_translated = True
        except Exception:
            meaning_ko = ""

    eng_translated = False
    if not eng and has_hanzi and len(clean) >= 2:
        try:
            import translators as ts
            eng = ts.translate_text(chinese, translator='google',
                                     from_language='zh', to_language='en')
            eng_translated = True
        except Exception:
            eng = ""

    # 번역 결과를 DB에 캐시 (다음엔 빠르게)
    import aiosqlite as _aiosqlite
    from database.db import DB_PATH as _db_path
    updates = {}
    if ko_translated and meaning_ko:
        updates["meaning_ko"] = meaning_ko
    if eng and not meaning_en:
        updates["meaning_en"] = eng
    if updates:
        async with _aiosqlite.connect(_db_path) as db:
            for col, val in updates.items():
                await db.execute(f"UPDATE chinese_cards SET {col} = ? WHERE id = ?", (val, card_id))
            await db.commit()

    return {
        "chinese": chinese,
        "english": eng,
        "english_source": "번역" if eng_translated else eng_source,
        "meaning_ko": meaning_ko,
        "ko_source": "번역" if ko_translated else "캐시" if card.get("meaning_ko") else "교재",
    }


@app.post("/api/chinese/import-pdf")
async def import_pdf_cards(request: Request, file: UploadFile = File(...),
                           category: str = Form("general")):
    """PDF에서 중국어 문장 추출 → 자동 학습카드 등록"""
    import re
    import pdfplumber
    from pypinyin import pinyin, Style

    user_id = request.state.user_id

    # PDF 임시 저장
    tmp_path = f"/tmp/chinese_pdf_{uuid.uuid4().hex[:8]}.pdf"
    async with aiofiles.open(tmp_path, "wb") as f:
        content = await file.read()
        await f.write(content)

    try:
        # PDF 텍스트 추출
        all_text = ""
        with pdfplumber.open(tmp_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    all_text += text + "\n"

        if not all_text.strip():
            raise HTTPException(400, "PDF에서 텍스트를 추출할 수 없습니다")

        # 중국어 포함 문장/구 추출
        # 한자가 포함된 라인 찾기
        chinese_pattern = re.compile(r'[\u4e00-\u9fff]')
        lines = all_text.split("\n")

        extracted = []
        for line in lines:
            line = line.strip()
            if not line or len(line) < 2:
                continue
            # 한자가 2글자 이상 포함된 라인만
            chinese_chars = chinese_pattern.findall(line)
            if len(chinese_chars) >= 2:
                # 중국어 부분 추출 (한자 + 기본 구두점)
                chinese_part = re.findall(r'[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef，。！？、；：""''（）]+', line)
                chinese_text = "".join(chinese_part).strip()
                if len(chinese_text) >= 2:
                    # 나머지 부분에서 한국어 뜻 추출 시도
                    remaining = line
                    for cp in chinese_part:
                        remaining = remaining.replace(cp, " ")
                    korean_part = re.findall(r'[가-힣]+[가-힣\s]*', remaining)
                    meaning = " ".join(korean_part).strip() if korean_part else ""
                    extracted.append({"chinese": chinese_text, "meaning": meaning})

        if not extracted:
            raise HTTPException(400, "PDF에서 중국어 문장을 찾을 수 없습니다")

        # 중복 제거
        seen = set()
        unique = []
        for item in extracted:
            if item["chinese"] not in seen:
                seen.add(item["chinese"])
                unique.append(item)
        extracted = unique

        # 카�� 일괄 등록
        created_cards = []
        for item in extracted:
            pinyin_list = pinyin(item["chinese"], style=Style.TONE)
            pinyin_str = " ".join([p[0] for p in pinyin_list])
            tone_list = pinyin(item["chinese"], style=Style.TONE3)
            tones = " ".join([p[0] for p in tone_list])

            card_id = await save_chinese_card(
                user_id=user_id, chinese=item["chinese"],
                pinyin=pinyin_str, tones=tones,
                meaning_ko=item["meaning"], category=category
            )
            created_cards.append({
                "id": card_id,
                "chinese": item["chinese"],
                "pinyin": pinyin_str,
                "meaning_ko": item["meaning"],
            })

        return {
            "message": f"{len(created_cards)}개 카드가 등록되었습니다",
            "count": len(created_cards),
            "cards": created_cards,
        }

    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@app.post("/api/chinese/import-folder")
async def import_folder_cards(request: Request):
    """temp/textbook_sections.json 기반 교시별 카드 등록"""
    import re, json as _json
    import pdfplumber
    from pypinyin import pinyin as py_pinyin, Style as PyStyle

    user_id = request.state.user_id
    sections_file = Path("temp/textbook_sections.json")

    if not sections_file.exists():
        raise HTTPException(400, "textbook_sections.json이 없습니다. 먼저 PDF 분석을 실행해주세요.")

    sections = _json.loads(sections_file.read_text())

    # 기존 교재 카드 삭제 (재등록)
    import aiosqlite
    from database.db import DB_PATH
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM chinese_cards WHERE user_id = ? AND category = 'textbook'", (user_id,))
        await db.commit()

    def _extract_all():
        chinese_pattern = re.compile(r'[\u4e00-\u9fff]')
        # 병음 패턴: 성조 부호가 있거나 성조 번호가 붙은 음절
        pinyin_tone = re.compile(
            r'[a-zA-ZüÜ]*[āáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜ][a-zA-ZāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜüÜ]*'
        )
        # 성모/운모 단독 패턴 (b, p, m, f, zh, ch, sh 등)
        shengmu_pattern = re.compile(
            r'\b(b|p|m|f|d|t|n|l|g|k|h|j|q|x|zh|ch|sh|r|z|c|s)\b'
        )
        # 발음 설명 패턴 (→ [쯔] 등)
        pron_hint = re.compile(r'[→\[\]（）]')

        skip_words = {"Copyright", "서울디지털대학교", "들어가기", "학습하기", "정리하기",
                       "학습개요", "학습목표", "학습내용", "학습점검", "학습정리",
                       "차시예고", "생각해보기", "본수업에사용"}
        all_results = []

        for sec in sections:
            pdf_path = sec["pdf"]
            start = sec["start_page"]
            end = sec["end_page"]
            is_pronunciation = "발음" in sec["subject"]

            with pdfplumber.open(pdf_path) as pdf:
                for i in range(start, min(end + 1, len(pdf.pages))):
                    text = pdf.pages[i].extract_text()
                    if not text:
                        continue

                    for line in text.split("\n"):
                        line = line.strip()
                        if not line or len(line) < 2:
                            continue
                        if any(sw in line for sw in skip_words):
                            continue
                        # 순수 숫자/페이지번호 스킵
                        if re.match(r'^\d+$', line):
                            continue

                        chinese_chars = chinese_pattern.findall(line)
                        pinyin_matches = pinyin_tone.findall(line)
                        has_chinese = len(chinese_chars) >= 2
                        has_pinyin = len(pinyin_matches) >= 1

                        # 한자 위주 라인
                        if has_chinese:
                            cn_parts = re.findall(
                                r'[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef，。！？、；：""''（）]+', line)
                            chinese_text = "".join(cn_parts).strip()
                            if len(chinese_text) < 2:
                                continue

                            remaining = line
                            for cp in cn_parts:
                                remaining = remaining.replace(cp, " ")
                            ko_parts = re.findall(r'[가-힣]+[가-힣\s]*', remaining)
                            meaning = " ".join(ko_parts).strip() if ko_parts else ""

                            # 병음도 있으면 함께 저장
                            if pinyin_matches:
                                meaning = " ".join(pinyin_matches) + (" — " + meaning if meaning else "")

                            all_results.append({
                                "subject": sec["subject"],
                                "week": sec["week"],
                                "class_num": sec["class_num"],
                                "chinese": chinese_text,
                                "meaning": meaning,
                            })

                        # 병음 위주 라인 (발음연습 교재)
                        elif is_pronunciation and has_pinyin:
                            pinyin_text = " ".join(pinyin_matches)
                            if len(pinyin_text) < 2:
                                continue

                            # 한국어 설명 추출
                            ko_parts = re.findall(r'[가-힣]+[가-힣\s]*', line)
                            meaning = " ".join(ko_parts).strip() if ko_parts else ""

                            # → [쯔] 같은 발음 힌트
                            hints = re.findall(r'[\[【]([^\]】]+)[\]】]', line)
                            if hints:
                                meaning = "[" + "] [".join(hints) + "]" + (" " + meaning if meaning else "")

                            # 한자가 1개라도 있으면 포함
                            cn_in_line = "".join(chinese_chars)

                            all_results.append({
                                "subject": sec["subject"],
                                "week": sec["week"],
                                "class_num": sec["class_num"],
                                "chinese": cn_in_line if cn_in_line else pinyin_text,
                                "meaning": meaning,
                                "is_pinyin": True,
                                "pinyin_override": pinyin_text,
                            })

        return all_results

    extracted = await asyncio.get_event_loop().run_in_executor(None, _extract_all)

    # 중복 제거
    seen = set()
    unique = []
    for item in extracted:
        key = f"{item['subject']}_{item['week']}_{item['class_num']}_{item['chinese']}"
        if key not in seen:
            seen.add(key)
            unique.append(item)

    created_count = 0
    subject_stats = {}

    for item in unique:
        # 병음이 직접 제공된 경우 (발음연습 교재) 그대로 사용
        if item.get("pinyin_override"):
            pinyin_str = item["pinyin_override"]
            tones = pinyin_str  # 성조 포함 병음 그대로
        else:
            pinyin_list = py_pinyin(item["chinese"], style=PyStyle.TONE)
            pinyin_str = " ".join([p[0] for p in pinyin_list])
            tone_list = py_pinyin(item["chinese"], style=PyStyle.TONE3)
            tones = " ".join([p[0] for p in tone_list])

        await save_chinese_card(
            user_id=user_id, chinese=item["chinese"],
            pinyin=pinyin_str, tones=tones,
            meaning_ko=item["meaning"], category="textbook",
            subject=item["subject"], week=item["week"],
            class_num=item["class_num"]
        )
        created_count += 1
        key = f"{item['subject']} {item['week']}주 {item['class_num']}교시"
        subject_stats[key] = subject_stats.get(key, 0) + 1

    return {
        "message": f"{created_count}개 카드가 등록되었습니다",
        "count": created_count,
        "subjects": subject_stats,
    }


@app.get("/api/chinese/subjects")
async def list_subjects(request: Request):
    """과목 + 주차 목록 조회"""
    user_id = request.state.user_id
    subjects = await get_chinese_subjects(user_id)
    return {"subjects": subjects}


@app.get("/api/chinese/cards-by-week")
async def cards_by_week(request: Request, subject: str = "", week: int = 0):
    """과목/주차별 카드 조회"""
    user_id = request.state.user_id
    cards = await get_chinese_cards(user_id, subject=subject or None,
                                     week=week if week > 0 else None)
    return {"cards": cards}


# ─── 교안 뷰어 API ──────────────────────────────────────
def _load_sections():
    """textbook_sections.json 로드"""
    import json as _json
    sf = Path("temp/textbook_sections.json")
    if sf.exists():
        return _json.loads(sf.read_text())
    return []


@app.get("/api/chinese/textbook/pages")
async def textbook_pages(subject: str, week: int, class_num: int = 0):
    """특정 과목/주차/교시의 교안 페이지 목록"""
    sections = _load_sections()
    matched = [s for s in sections
               if s["subject"] == subject and s["week"] == week
               and (class_num == 0 or s["class_num"] == class_num)]

    if not matched:
        raise HTTPException(404, "해당 교안을 찾을 수 없습니다")

    target_pages = []
    for sec in matched:
        for i in range(sec["start_page"], sec["end_page"] + 1):
            target_pages.append({"pdf": sec["pdf"], "page": i,
                                 "class_num": sec["class_num"], "title": sec.get("title", "")})

    return {
        "subject": subject, "week": week, "class_num": class_num,
        "total_pages": len(target_pages),
        "sections": [{"class_num": s["class_num"], "title": s.get("title", "")} for s in matched],
    }


@app.get("/api/chinese/textbook/image")
async def textbook_image(subject: str, week: int, class_num: int = 1, page_idx: int = 0):
    """교안 페이지를 이미지로 반환 (교시별)"""
    from pdf2image import convert_from_path

    sections = _load_sections()
    matched = [s for s in sections
               if s["subject"] == subject and s["week"] == week and s["class_num"] == class_num]

    if not matched:
        raise HTTPException(404, "교안을 찾을 수 없습니다")

    sec = matched[0]
    abs_page = sec["start_page"] + page_idx
    if abs_page > sec["end_page"]:
        raise HTTPException(404, "페이지를 찾을 수 없습니다")

    cache_dir = Path("outputs/chinese/textbook_cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{subject}_{week}_{class_num}_{page_idx}.jpg"

    if not cache_file.exists():
        pdf_page_num = abs_page + 1
        images = convert_from_path(
            sec["pdf"], first_page=pdf_page_num, last_page=pdf_page_num,
            dpi=150, fmt="jpeg"
        )
        if images:
            images[0].save(str(cache_file), "JPEG", quality=85)

    if not cache_file.exists():
        raise HTTPException(500, "이미지 생성 실패")

    return FileResponse(str(cache_file), media_type="image/jpeg")


# ─── 메모 API ──────────────────────────────────────
@app.post("/api/chinese/note")
async def create_note(request: Request):
    """메모 작성"""
    data = await request.json()
    user_id = request.state.user_id
    content = data.get("content", "").strip()
    if not content:
        raise HTTPException(400, "메모 내용을 입력해주세요")

    note_id = await save_chinese_note(
        user_id=user_id, content=content,
        card_id=data.get("card_id"),
        subject=data.get("subject", ""),
        week=data.get("week", 0)
    )
    return {"id": note_id, "message": "메모 저장됨"}


@app.put("/api/chinese/note/{note_id}")
async def edit_note(note_id: int, request: Request):
    """메모 수정"""
    data = await request.json()
    content = data.get("content", "").strip()
    if not content:
        raise HTTPException(400, "메모 내용을 입력해주세요")
    await update_chinese_note(note_id, content)
    return {"message": "메모 수정됨"}


@app.delete("/api/chinese/note/{note_id}")
async def remove_note(note_id: int):
    """메모 삭제"""
    await delete_chinese_note(note_id)
    return {"message": "메모 삭제됨"}


@app.get("/api/chinese/notes")
async def list_notes(request: Request, subject: str = "", week: int = 0,
                     card_id: int = 0):
    """메모 목록 조회"""
    user_id = request.state.user_id
    notes = await get_chinese_notes(
        user_id, subject=subject or None,
        week=week if week > 0 else None,
        card_id=card_id if card_id > 0 else None
    )
    return {"notes": notes}


@app.post("/api/chinese/card/{card_id}/group")
async def update_card_group(card_id: int, request: Request):
    """카드 그룹 지정/해제"""
    data = await request.json()
    group_name = data.get("group_name", "").strip()

    import aiosqlite
    from database.db import DB_PATH
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE chinese_cards SET group_name = ? WHERE id = ?",
                         (group_name, card_id))
        await db.commit()
    return {"message": "그룹 업데이트됨", "group_name": group_name}


@app.get("/api/chinese/textbook/cards-by-page")
async def cards_by_textbook_page(subject: str, week: int, class_num: int = 1):
    """교안 페이지에 나오는 카드 매칭"""
    import pdfplumber
    sections = _load_sections()
    matched = [s for s in sections
               if s["subject"] == subject and s["week"] == week and s["class_num"] == class_num]
    if not matched:
        return {"pages": []}

    sec = matched[0]

    # 이 교시의 카드 전체
    import aiosqlite
    from database.db import DB_PATH
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM chinese_cards WHERE subject=? AND week=? AND class_num=? ORDER BY id",
            (subject, week, class_num)
        )
        rows = await cursor.fetchall()
        cards = [dict(r) for r in rows]

    # 각 페이지 텍스트에서 카드 찾기
    pages_result = []
    with pdfplumber.open(sec["pdf"]) as pdf:
        for page_idx in range(sec["start_page"], min(sec["end_page"] + 1, len(pdf.pages))):
            text = pdf.pages[page_idx].extract_text() or ""
            page_cards = []
            for c in cards:
                if c["chinese"] and c["chinese"].replace("。", "").replace("，", "") in text.replace("。", "").replace("，", ""):
                    page_cards.append({
                        "id": c["id"], "chinese": c["chinese"],
                        "pinyin": c["pinyin"], "meaning_ko": c["meaning_ko"],
                        "group_name": c.get("group_name", ""),
                    })
            if page_cards:
                pages_result.append({
                    "page_idx": page_idx - sec["start_page"],
                    "cards": page_cards,
                })

    return {"pages": pages_result, "total_cards": len(cards)}


@app.get("/api/chinese/textbook/summary")
async def textbook_summary(subject: str, week: int, class_num: int = 1):
    """교안 텍스트 요약 — PDF에서 핵심 텍스트 추출"""
    import pdfplumber

    sections = _load_sections()
    matched = [s for s in sections
               if s["subject"] == subject and s["week"] == week and s["class_num"] == class_num]
    if not matched:
        raise HTTPException(404, "교안을 찾을 수 없습니다")

    sec = matched[0]
    skip_words = ["Copyright", "서울디지털대학교", "들어가기", "학습하기", "정리하기",
                  "본 수업에 사용된", "본수업에사용", "본 교안에는"]

    def _extract():
        lines = []
        with pdfplumber.open(sec["pdf"]) as pdf:
            for i in range(sec["start_page"], min(sec["end_page"] + 1, len(pdf.pages))):
                text = pdf.pages[i].extract_text()
                if not text:
                    continue
                for line in text.split("\n"):
                    line = line.strip()
                    if not line or len(line) < 3:
                        continue
                    if any(sw in line for sw in skip_words):
                        continue
                    # 순수 숫자/페이지번호 스킵
                    if line.isdigit():
                        continue
                    lines.append(line)
        return lines

    text_lines = await asyncio.get_event_loop().run_in_executor(None, _extract)

    return {
        "subject": subject,
        "week": week,
        "class_num": class_num,
        "title": sec.get("title", ""),
        "lines": text_lines,
        "page_count": sec["end_page"] - sec["start_page"] + 1,
    }


@app.get("/api/chinese/dashboard")
async def chinese_dashboard(request: Request, days: int = 30):
    """학습 대시보드 (날짜별 통계 + 최근 기록)"""
    user_id = request.state.user_id
    stats = await get_chinese_study_stats(user_id)
    daily = await get_chinese_daily_stats(user_id, days)
    recent = await get_chinese_recent_records(user_id, 20)
    return {"stats": stats, "daily": daily, "recent": recent}


@app.post("/api/chinese/card/{card_id}/live-scores")
async def save_live_scores(card_id: int, request: Request):
    """실시간 평가 결과 일괄 저장"""
    data = await request.json()
    scores = data.get("scores", [])
    user_id = request.state.user_id

    for sc in scores:
        await save_pronunciation_record(
            card_id=card_id, user_id=user_id,
            audio_path="live",
            stt_text=str(sc.get("text", "")),
            score=sc.get("score", 0),
            tone_score=0,
            tone_detail="",
            feedback=f"실시간 {sc.get('index', 0)}번째"
        )

    return {"saved": len(scores)}


_tts_batch_running = False
_tts_batch_progress = {"subject": "", "voice": "", "done": 0, "total": 0}

@app.post("/api/chinese/tts-batch-start")
async def tts_batch_start(request: Request):
    """전체 중국어 TTS 배치 생성 시작 (백그라운드)"""
    global _tts_batch_running
    if _tts_batch_running:
        return {"message": "이미 진행 중", "progress": _tts_batch_progress}

    _tts_batch_running = True
    asyncio.create_task(_run_tts_batch())
    return {"message": "TTS 배치 시작!"}


@app.get("/api/chinese/tts-batch-status")
async def tts_batch_status():
    """TTS 배치 진행 상황"""
    return {"running": _tts_batch_running, "progress": _tts_batch_progress}


async def _run_tts_batch():
    """백그라운드 TTS 배치"""
    global _tts_batch_running, _tts_batch_progress
    import aiosqlite

    SUBJECTS = ['기초중국어', '기초중국어2', '생활중국어', '중국어발음연습', '발음연습', '중국인의생활과문화']
    voice_8 = 'voice_samples/54b5509a_118a3ddf-37fe-44e8-b4af-e0783c865480.wav'
    voice_11 = 'voice_samples/2a75fe0f_모바일도서검색기수민.wav'

    engine = get_engine()

    for subject in SUBJECTS:
        for voice_name, ref_voice, col in [("김준용", voice_8, "ref_audio_path"), ("수민", voice_11, "ref_audio_path_2")]:
            async with aiosqlite.connect("database/voices.db") as db:
                cursor = await db.execute(
                    f"SELECT id, chinese FROM chinese_cards WHERE subject=? AND ({col} IS NULL OR {col}='') AND chinese IS NOT NULL AND length(chinese)>=1",
                    (subject,))
                cards = await cursor.fetchall()

            _tts_batch_progress = {"subject": subject, "voice": voice_name, "done": 0, "total": len(cards)}
            print(f"[TTS배치] {subject} {voice_name}: {len(cards)}개")

            for cid, text in cards:
                try:
                    audio_path = str(CHINESE_AUDIO_DIR / f"{col}_{cid}_{uuid.uuid4().hex[:6]}.wav")
                    processed = preprocess_tts_text(text, "zh-cn")

                    def _gen(t, r, p):
                        # 긴 문장은 청크로 나눠서 생성 + 결합
                        if len(t) > 15:
                            import re as _re2
                            chunks = _re2.split(r'[。，！？、；]', t)
                            chunks = [c.strip() for c in chunks if c.strip()]
                            if len(chunks) > 1:
                                wavs = []
                                for chunk in chunks:
                                    wl = engine.tts.tts(text=chunk, speaker_wav=r, language="zh-cn")
                                    wavs.append(np.array(wl, dtype=np.float32))
                                    # 구두점 위치에 짧은 무음 삽입
                                    wavs.append(np.zeros(int(24000 * 0.15), dtype=np.float32))
                                wav = np.concatenate(wavs)
                                sf.write(p, wav, 24000)
                                return
                        wav_list = engine.tts.tts(text=t, speaker_wav=r, language="zh-cn")
                        wav = np.array(wav_list, dtype=np.float32)
                        sf.write(p, wav, 24000)

                    await asyncio.get_event_loop().run_in_executor(None, _gen, processed, ref_voice, audio_path)

                    async with aiosqlite.connect("database/voices.db") as db:
                        await db.execute(f"UPDATE chinese_cards SET {col}=? WHERE id=?", (audio_path, cid))
                        await db.commit()

                    _tts_batch_progress["done"] += 1
                    if _tts_batch_progress["done"] % 20 == 0:
                        print(f"[TTS배치] {subject} {voice_name}: {_tts_batch_progress['done']}/{len(cards)}")
                except Exception as e:
                    print(f"[TTS배치] 에러: {text[:20]} — {e}")

                await asyncio.sleep(0.1)  # 서버 응답성 유지

    _tts_batch_running = False
    _tts_batch_progress = {"subject": "완료!", "voice": "", "done": 0, "total": 0}
    print("[TTS배치] 전체 완료!")


@app.get("/api/sheets/auth")
async def sheets_auth():
    """Google Sheets 권한 승인 시작 (1회만)"""
    from google_auth_oauthlib.flow import Flow
    flow = Flow.from_client_config(
        {"web": {
            "client_id": os.environ.get("GOOGLE_CLIENT_ID"),
            "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET"),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }},
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
        redirect_uri=os.environ.get("BASE_URL", "https://myvoice.901planner.cloud") + "/api/sheets/callback"
    )
    auth_url, _ = flow.authorization_url(access_type='offline', prompt='consent')
    return {"auth_url": auth_url}


@app.get("/api/sheets/callback")
async def sheets_callback(code: str = ""):
    """Google Sheets OAuth 콜백 — 토큰 저장"""
    if not code:
        return {"error": "코드 없음"}
    from google_auth_oauthlib.flow import Flow
    flow = Flow.from_client_config(
        {"web": {
            "client_id": os.environ.get("GOOGLE_CLIENT_ID"),
            "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET"),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }},
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
        redirect_uri=os.environ.get("BASE_URL", "https://myvoice.901planner.cloud") + "/api/sheets/callback"
    )
    flow.fetch_token(code=code)
    creds = flow.credentials

    # 토큰 저장
    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
    }
    import json as _json
    token_path = Path("database/sheets_token.json")
    token_path.write_text(_json.dumps(token_data))
    print(f"[OK] Sheets 토큰 저장됨: {token_path}")

    from starlette.responses import HTMLResponse
    return HTMLResponse("<h2>Google Sheets 연동 완료!</h2><p>이 창을 닫고 학습 페이지로 돌아가세요.</p><script>setTimeout(()=>window.close(),2000)</script>")


def _get_sheets_client():
    """저장된 토큰으로 gspread 클라이언트 반환"""
    import json as _json
    import gspread
    from google.oauth2.credentials import Credentials

    token_path = Path("database/sheets_token.json")
    if not token_path.exists():
        return None

    token_data = _json.loads(token_path.read_text())
    creds = Credentials(
        token=token_data["token"],
        refresh_token=token_data["refresh_token"],
        token_uri=token_data["token_uri"],
        client_id=token_data["client_id"],
        client_secret=token_data["client_secret"],
    )
    return gspread.authorize(creds)


@app.post("/api/chinese/sheet-log")
async def sheet_log(request: Request):
    """학습 기록을 Google Sheets에 기록"""
    data = await request.json()
    user_id = request.state.user_id
    note = data.get("note", "")

    stats = await get_chinese_study_stats(user_id)
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d %H:%M")

    row_data = {
        "date": today,
        "today_count": stats.get("today_count", 0),
        "total_records": stats.get("total_records", 0),
        "avg_score": stats.get("avg_score", 0),
        "note": note,
        "user": request.state.user_name or request.state.user_email,
    }

    # Google Sheets에 기록
    try:
        gc = _get_sheets_client()
        if not gc:
            return {"message": "Sheets 연동 필요 — 설정에서 연동해주세요", "data": row_data, "need_auth": True}

        SHEET_ID = "1RmGuVGPKWwGkvmU8WKVTOrxoo8-JwrWogJjEOsd2GRE"

        def _write():
            sh = gc.open_by_key(SHEET_ID)
            # "김준용" 시트 찾기/생성
            try:
                ws = sh.worksheet("김준용")
            except gspread.exceptions.WorksheetNotFound:
                ws = sh.add_worksheet("김준용", rows=1000, cols=6)
                ws.append_row(["날짜", "오늘연습", "총연습", "평균점수", "사용자", "느낀점"])

            ws.append_row([
                row_data["date"], row_data["today_count"], row_data["total_records"],
                row_data["avg_score"], row_data["user"], row_data["note"]
            ])

        await asyncio.get_event_loop().run_in_executor(None, _write)
        return {"message": "시트 기록 완료!", "data": row_data}

    except Exception as e:
        import traceback; traceback.print_exc()
        # 로컬 백업
        log_path = Path("outputs/chinese/sheet_log.jsonl")
        import json as _json
        with open(log_path, "a") as f:
            f.write(_json.dumps(row_data, ensure_ascii=False) + "\n")
        return {"message": f"시트 기록 실패 (로컬 저장): {str(e)}", "data": row_data}


@app.get("/api/chinese/settings")
async def get_settings(request: Request):
    """사용자 설정 조회"""
    user_id = request.state.user_id
    settings = await get_user_settings(user_id)
    return {"settings": settings}


@app.post("/api/chinese/settings")
async def post_settings(request: Request):
    """사용자 설정 저장"""
    user_id = request.state.user_id
    data = await request.json()
    settings = data.get("settings", {})
    await save_user_settings(user_id, settings)
    return {"message": "설정 저장됨"}


@app.get("/")
async def root():
    return FileResponse("frontend/index.html")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 9090))
    reload = os.environ.get("ENV", "production") == "development"

    # HTTPS 인증서 경로
    ssl_certfile = os.environ.get("SSL_CERT", "certs/cert.pem")
    ssl_keyfile = os.environ.get("SSL_KEY", "certs/key.pem")

    ssl_opts = {}
    if os.path.exists(ssl_certfile) and os.path.exists(ssl_keyfile):
        ssl_opts = {"ssl_certfile": ssl_certfile, "ssl_keyfile": ssl_keyfile}
        print(f"[HTTPS] 인증서 로드됨 — https://0.0.0.0:{port}")
    else:
        print(f"[HTTP] 인증서 없음 — http://0.0.0.0:{port}")

    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=reload, **ssl_opts)
