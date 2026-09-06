"""
main.py
FastAPI 메인 서버
목소리 녹음 저장 + TTS 생성 API
"""

import gc
import os
import re
import json
import uuid
import random
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
PUBLIC_PREFIXES = ("/static/", "/outputs/", "/icons/", "/api/auth/", "/api/sheets/",
                   "/api/chinese/audio/", "/packs/")


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


class PackStatic(StaticFiles):
    """앱이 학습 자료를 받아 가는 자리.
       index.json 은 판을 확인하러 자주 들르므로 늘 새로 묻게 하고(no-cache),
       하루치 zip 은 바뀌면 rev 가 달라지니 오래 담아 둬도 된다."""

    async def get_response(self, path, scope):
        r = await super().get_response(path, scope)
        if path.endswith("index.json"):
            r.headers["Cache-Control"] = "no-cache"
        else:
            r.headers["Cache-Control"] = "public, max-age=604800"
        r.headers["Access-Control-Allow-Origin"] = "*"
        return r


class RevalidatingStatic(StaticFiles):
    """중국어 음성은 이름은 그대로 둔 채 속만 갈아 끼운다(성조를 고칠 때).
       Cache-Control 이 없으면 브라우저가 나름대로 오래 캐시해 예전 소리를 계속 들려준다
       — 张 을 고쳐 놓아도 '张唐' 이 그대로 나오던 까닭이다.
       no-cache 는 '쓰기 전에 서버에 물어보라'는 뜻이라, 안 바뀌었으면 304 로 끝나 값도 싸다."""

    async def get_response(self, path, scope):
        r = await super().get_response(path, scope)
        if path.startswith("chinese/"):
            r.headers["Cache-Control"] = "no-cache"
        return r


# 프론트엔드 정적 파일 서빙
app.mount("/static", StaticFiles(directory="frontend"), name="static")
app.mount("/outputs", RevalidatingStatic(directory="outputs"), name="outputs")
# 앱(플러터)이 학습 자료를 받아 가는 자리 — 로그인 없이 열어 둔다
app.mount("/packs", PackStatic(directory="finetune_data/_export"), name="packs")
app.mount("/icons", StaticFiles(directory="frontend/icons"), name="icons")
app.mount("/img", StaticFiles(directory="frontend/img"), name="img")   # 바브바브 캐릭터 등


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
    return FileResponse("frontend/login.html", headers=_NOCACHE)


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


# ─── 노래 결과 강제 다운로드 (곡별) ───────────────────────────
from fastapi.responses import FileResponse as _FileResp

_SONG_DL_FILES = {
    "golden_mp4":       ("outputs/songs/golden_kjy_video_with_audio.mp4",   "video/mp4",  "golden_kjy_karaoke.mp4"),
    "golden_mp3":       ("outputs/songs/golden_kjy_cover.mp3",              "audio/mpeg", "golden_kjy_cover.mp3"),
    "winter_rain_mp4":  ("outputs/songs/winter_rain_kjy_video_with_audio.mp4", "video/mp4",  "winter_rain_kjy_karaoke.mp4"),
    "winter_rain_mp3":  ("outputs/songs/winter_rain_kjy_cover.mp3",         "audio/mpeg", "winter_rain_kjy_cover.mp3"),
    "aroha_mp4":  ("outputs/songs/aroha_karaoke_video.mp4",  "video/mp4",  "aroha_karaoke.mp4"),
    "aroha_mp3":  ("outputs/songs/aroha_mr.mp3",             "audio/mpeg", "aroha_mr.mp3"),
    "ragu_yo_mp4":      ("outputs/songs/ragu_yo_karaoke_video.mp4",      "video/mp4",  "ragu_yo_karaoke.mp4"),
    "ragu_yo_mp3":      ("outputs/songs/ragu_yo_mr.mp3",                 "audio/mpeg", "ragu_yo_mr.mp3"),
    "million_rose_mp4": ("outputs/songs/million_rose_karaoke_video.mp4", "video/mp4",  "million_rose_karaoke.mp4"),
    "million_rose_mp3": ("outputs/songs/million_rose_mr.mp3",            "audio/mpeg", "million_rose_mr.mp3"),
    "firefly_mp4":      ("outputs/songs/firefly_karaoke_video.mp4",      "video/mp4",  "firefly_karaoke.mp4"),
    "firefly_mp3":      ("outputs/songs/firefly_mr.mp3",                 "audio/mpeg", "firefly_mr.mp3"),
    # BTS ARIRANG 14곡
    "bts_swim_mp4":            ("outputs/songs/bts_swim_karaoke_video.mp4",           "video/mp4",  "bts_swim.mp4"),
    "bts_swim_mp3":            ("outputs/songs/bts_swim_mr.mp3",                       "audio/mpeg", "bts_swim_mr.mp3"),
    "bts_body_to_body_mp4":    ("outputs/songs/bts_body_to_body_karaoke_video.mp4",   "video/mp4",  "bts_body_to_body.mp4"),
    "bts_body_to_body_mp3":    ("outputs/songs/bts_body_to_body_mr.mp3",               "audio/mpeg", "bts_body_to_body_mr.mp3"),
    "bts_hooligan_mp4":        ("outputs/songs/bts_hooligan_karaoke_video.mp4",       "video/mp4",  "bts_hooligan.mp4"),
    "bts_hooligan_mp3":        ("outputs/songs/bts_hooligan_mr.mp3",                   "audio/mpeg", "bts_hooligan_mr.mp3"),
    "bts_aliens_mp4":          ("outputs/songs/bts_aliens_karaoke_video.mp4",         "video/mp4",  "bts_aliens.mp4"),
    "bts_aliens_mp3":          ("outputs/songs/bts_aliens_mr.mp3",                     "audio/mpeg", "bts_aliens_mr.mp3"),
    "bts_fya_mp4":             ("outputs/songs/bts_fya_karaoke_video.mp4",            "video/mp4",  "bts_fya.mp4"),
    "bts_fya_mp3":             ("outputs/songs/bts_fya_mr.mp3",                        "audio/mpeg", "bts_fya_mr.mp3"),
    "bts_2_0_mp4":             ("outputs/songs/bts_2_0_karaoke_video.mp4",            "video/mp4",  "bts_2_0.mp4"),
    "bts_2_0_mp3":             ("outputs/songs/bts_2_0_mr.mp3",                        "audio/mpeg", "bts_2_0_mr.mp3"),
    "bts_no29_mp4":            ("outputs/songs/bts_no29_karaoke_video.mp4",           "video/mp4",  "bts_no29.mp4"),
    "bts_no29_mp3":            ("outputs/songs/bts_no29_mr.mp3",                       "audio/mpeg", "bts_no29_mr.mp3"),
    "bts_merry_go_round_mp4":  ("outputs/songs/bts_merry_go_round_karaoke_video.mp4", "video/mp4",  "bts_merry_go_round.mp4"),
    "bts_merry_go_round_mp3":  ("outputs/songs/bts_merry_go_round_mr.mp3",             "audio/mpeg", "bts_merry_go_round_mr.mp3"),
    "bts_normal_mp4":          ("outputs/songs/bts_normal_karaoke_video.mp4",         "video/mp4",  "bts_normal.mp4"),
    "bts_normal_mp3":          ("outputs/songs/bts_normal_mr.mp3",                     "audio/mpeg", "bts_normal_mr.mp3"),
    "bts_like_animals_mp4":    ("outputs/songs/bts_like_animals_karaoke_video.mp4",   "video/mp4",  "bts_like_animals.mp4"),
    "bts_like_animals_mp3":    ("outputs/songs/bts_like_animals_mr.mp3",               "audio/mpeg", "bts_like_animals_mr.mp3"),
    "bts_they_dont_know_mp4":  ("outputs/songs/bts_they_dont_know_karaoke_video.mp4", "video/mp4",  "bts_they_dont_know.mp4"),
    "bts_they_dont_know_mp3":  ("outputs/songs/bts_they_dont_know_mr.mp3",             "audio/mpeg", "bts_they_dont_know_mr.mp3"),
    "bts_one_more_night_mp4":  ("outputs/songs/bts_one_more_night_karaoke_video.mp4", "video/mp4",  "bts_one_more_night.mp4"),
    "bts_one_more_night_mp3":  ("outputs/songs/bts_one_more_night_mr.mp3",             "audio/mpeg", "bts_one_more_night_mr.mp3"),
    "bts_please_mp4":          ("outputs/songs/bts_please_karaoke_video.mp4",         "video/mp4",  "bts_please.mp4"),
    "bts_please_mp3":          ("outputs/songs/bts_please_mr.mp3",                     "audio/mpeg", "bts_please_mr.mp3"),
    "bts_into_the_sun_mp4":    ("outputs/songs/bts_into_the_sun_karaoke_video.mp4",   "video/mp4",  "bts_into_the_sun.mp4"),
    "bts_into_the_sun_mp3":    ("outputs/songs/bts_into_the_sun_mr.mp3",               "audio/mpeg", "bts_into_the_sun_mr.mp3"),
    # AUTO_BIG_BATCH_START
    "ec3462c6204d_mp4": ("outputs/songs/ec3462c6204d_karaoke_video.mp4", "video/mp4", "ec3462c6204d.mp4"),
    "ec3462c6204d_mp3": ("outputs/songs/ec3462c6204d_mr.mp3", "audio/mpeg", "ec3462c6204d_mr.mp3"),
    "f489ba1aed62_mp4": ("outputs/songs/f489ba1aed62_karaoke_video.mp4", "video/mp4", "f489ba1aed62.mp4"),
    "f489ba1aed62_mp3": ("outputs/songs/f489ba1aed62_mr.mp3", "audio/mpeg", "f489ba1aed62_mr.mp3"),
    "61d8435fee43_mp4": ("outputs/songs/61d8435fee43_karaoke_video.mp4", "video/mp4", "61d8435fee43.mp4"),
    "61d8435fee43_mp3": ("outputs/songs/61d8435fee43_mr.mp3", "audio/mpeg", "61d8435fee43_mr.mp3"),
    "f0f80f02d423_mp4": ("outputs/songs/f0f80f02d423_karaoke_video.mp4", "video/mp4", "f0f80f02d423.mp4"),
    "f0f80f02d423_mp3": ("outputs/songs/f0f80f02d423_mr.mp3", "audio/mpeg", "f0f80f02d423_mr.mp3"),
    "0d2fdeb6fbe3_mp4": ("outputs/songs/0d2fdeb6fbe3_karaoke_video.mp4", "video/mp4", "0d2fdeb6fbe3.mp4"),
    "0d2fdeb6fbe3_mp3": ("outputs/songs/0d2fdeb6fbe3_mr.mp3", "audio/mpeg", "0d2fdeb6fbe3_mr.mp3"),
    "aab5605956ea_mp4": ("outputs/songs/aab5605956ea_karaoke_video.mp4", "video/mp4", "aab5605956ea.mp4"),
    "aab5605956ea_mp3": ("outputs/songs/aab5605956ea_mr.mp3", "audio/mpeg", "aab5605956ea_mr.mp3"),
    "361c30834910_mp4": ("outputs/songs/361c30834910_karaoke_video.mp4", "video/mp4", "361c30834910.mp4"),
    "361c30834910_mp3": ("outputs/songs/361c30834910_mr.mp3", "audio/mpeg", "361c30834910_mr.mp3"),
    "ff710fee4a93_mp4": ("outputs/songs/ff710fee4a93_karaoke_video.mp4", "video/mp4", "ff710fee4a93.mp4"),
    "ff710fee4a93_mp3": ("outputs/songs/ff710fee4a93_mr.mp3", "audio/mpeg", "ff710fee4a93_mr.mp3"),
    "40793cae09bc_mp4": ("outputs/songs/40793cae09bc_karaoke_video.mp4", "video/mp4", "40793cae09bc.mp4"),
    "40793cae09bc_mp3": ("outputs/songs/40793cae09bc_mr.mp3", "audio/mpeg", "40793cae09bc_mr.mp3"),
    "70666e49267b_mp4": ("outputs/songs/70666e49267b_karaoke_video.mp4", "video/mp4", "70666e49267b.mp4"),
    "70666e49267b_mp3": ("outputs/songs/70666e49267b_mr.mp3", "audio/mpeg", "70666e49267b_mr.mp3"),
    "47716f4a7f8d_mp4": ("outputs/songs/47716f4a7f8d_karaoke_video.mp4", "video/mp4", "47716f4a7f8d.mp4"),
    "47716f4a7f8d_mp3": ("outputs/songs/47716f4a7f8d_mr.mp3", "audio/mpeg", "47716f4a7f8d_mr.mp3"),
    "3706b4142fd4_mp4": ("outputs/songs/3706b4142fd4_karaoke_video.mp4", "video/mp4", "3706b4142fd4.mp4"),
    "3706b4142fd4_mp3": ("outputs/songs/3706b4142fd4_mr.mp3", "audio/mpeg", "3706b4142fd4_mr.mp3"),
    "237b615a8f75_mp4": ("outputs/songs/237b615a8f75_karaoke_video.mp4", "video/mp4", "237b615a8f75.mp4"),
    "237b615a8f75_mp3": ("outputs/songs/237b615a8f75_mr.mp3", "audio/mpeg", "237b615a8f75_mr.mp3"),
    "4c2bec12bcdc_mp4": ("outputs/songs/4c2bec12bcdc_karaoke_video.mp4", "video/mp4", "4c2bec12bcdc.mp4"),
    "4c2bec12bcdc_mp3": ("outputs/songs/4c2bec12bcdc_mr.mp3", "audio/mpeg", "4c2bec12bcdc_mr.mp3"),
    "dba0bed9fc49_mp4": ("outputs/songs/dba0bed9fc49_karaoke_video.mp4", "video/mp4", "dba0bed9fc49.mp4"),
    "dba0bed9fc49_mp3": ("outputs/songs/dba0bed9fc49_mr.mp3", "audio/mpeg", "dba0bed9fc49_mr.mp3"),
    "d31f294c5c1f_mp4": ("outputs/songs/d31f294c5c1f_karaoke_video.mp4", "video/mp4", "d31f294c5c1f.mp4"),
    "d31f294c5c1f_mp3": ("outputs/songs/d31f294c5c1f_mr.mp3", "audio/mpeg", "d31f294c5c1f_mr.mp3"),
    "06f86ee51cf1_mp4": ("outputs/songs/06f86ee51cf1_karaoke_video.mp4", "video/mp4", "06f86ee51cf1.mp4"),
    "06f86ee51cf1_mp3": ("outputs/songs/06f86ee51cf1_mr.mp3", "audio/mpeg", "06f86ee51cf1_mr.mp3"),
    "797054ba8e19_mp4": ("outputs/songs/797054ba8e19_karaoke_video.mp4", "video/mp4", "797054ba8e19.mp4"),
    "797054ba8e19_mp3": ("outputs/songs/797054ba8e19_mr.mp3", "audio/mpeg", "797054ba8e19_mr.mp3"),
    "02830fa43e1c_mp4": ("outputs/songs/02830fa43e1c_karaoke_video.mp4", "video/mp4", "02830fa43e1c.mp4"),
    "02830fa43e1c_mp3": ("outputs/songs/02830fa43e1c_mr.mp3", "audio/mpeg", "02830fa43e1c_mr.mp3"),
    "a024ad237db9_mp4": ("outputs/songs/a024ad237db9_karaoke_video.mp4", "video/mp4", "a024ad237db9.mp4"),
    "a024ad237db9_mp3": ("outputs/songs/a024ad237db9_mr.mp3", "audio/mpeg", "a024ad237db9_mr.mp3"),
    "4e0e1e424097_mp4": ("outputs/songs/4e0e1e424097_karaoke_video.mp4", "video/mp4", "4e0e1e424097.mp4"),
    "4e0e1e424097_mp3": ("outputs/songs/4e0e1e424097_mr.mp3", "audio/mpeg", "4e0e1e424097_mr.mp3"),
    "7f5e4925f047_mp4": ("outputs/songs/7f5e4925f047_karaoke_video.mp4", "video/mp4", "7f5e4925f047.mp4"),
    "7f5e4925f047_mp3": ("outputs/songs/7f5e4925f047_mr.mp3", "audio/mpeg", "7f5e4925f047_mr.mp3"),
    "54f4d40844bb_mp4": ("outputs/songs/54f4d40844bb_karaoke_video.mp4", "video/mp4", "54f4d40844bb.mp4"),
    "54f4d40844bb_mp3": ("outputs/songs/54f4d40844bb_mr.mp3", "audio/mpeg", "54f4d40844bb_mr.mp3"),
    "99c9087935fc_mp4": ("outputs/songs/99c9087935fc_karaoke_video.mp4", "video/mp4", "99c9087935fc.mp4"),
    "99c9087935fc_mp3": ("outputs/songs/99c9087935fc_mr.mp3", "audio/mpeg", "99c9087935fc_mr.mp3"),
    "a85665c3b7e7_mp4": ("outputs/songs/a85665c3b7e7_karaoke_video.mp4", "video/mp4", "a85665c3b7e7.mp4"),
    "a85665c3b7e7_mp3": ("outputs/songs/a85665c3b7e7_mr.mp3", "audio/mpeg", "a85665c3b7e7_mr.mp3"),
    "6a38284d3226_mp4": ("outputs/songs/6a38284d3226_karaoke_video.mp4", "video/mp4", "6a38284d3226.mp4"),
    "6a38284d3226_mp3": ("outputs/songs/6a38284d3226_mr.mp3", "audio/mpeg", "6a38284d3226_mr.mp3"),
    "206b3b5c41b7_mp4": ("outputs/songs/206b3b5c41b7_karaoke_video.mp4", "video/mp4", "206b3b5c41b7.mp4"),
    "206b3b5c41b7_mp3": ("outputs/songs/206b3b5c41b7_mr.mp3", "audio/mpeg", "206b3b5c41b7_mr.mp3"),
    "0306d61c120a_mp4": ("outputs/songs/0306d61c120a_karaoke_video.mp4", "video/mp4", "0306d61c120a.mp4"),
    "0306d61c120a_mp3": ("outputs/songs/0306d61c120a_mr.mp3", "audio/mpeg", "0306d61c120a_mr.mp3"),
    "bd5e8f2ee3a9_mp4": ("outputs/songs/bd5e8f2ee3a9_karaoke_video.mp4", "video/mp4", "bd5e8f2ee3a9.mp4"),
    "bd5e8f2ee3a9_mp3": ("outputs/songs/bd5e8f2ee3a9_mr.mp3", "audio/mpeg", "bd5e8f2ee3a9_mr.mp3"),
    "57d16a46903b_mp4": ("outputs/songs/57d16a46903b_karaoke_video.mp4", "video/mp4", "57d16a46903b.mp4"),
    "57d16a46903b_mp3": ("outputs/songs/57d16a46903b_mr.mp3", "audio/mpeg", "57d16a46903b_mr.mp3"),
    "7d03218edfb1_mp4": ("outputs/songs/7d03218edfb1_karaoke_video.mp4", "video/mp4", "7d03218edfb1.mp4"),
    "7d03218edfb1_mp3": ("outputs/songs/7d03218edfb1_mr.mp3", "audio/mpeg", "7d03218edfb1_mr.mp3"),
    "fe76565de4fd_mp4": ("outputs/songs/fe76565de4fd_karaoke_video.mp4", "video/mp4", "fe76565de4fd.mp4"),
    "fe76565de4fd_mp3": ("outputs/songs/fe76565de4fd_mr.mp3", "audio/mpeg", "fe76565de4fd_mr.mp3"),
    "00dae6639aaf_mp4": ("outputs/songs/00dae6639aaf_karaoke_video.mp4", "video/mp4", "00dae6639aaf.mp4"),
    "00dae6639aaf_mp3": ("outputs/songs/00dae6639aaf_mr.mp3", "audio/mpeg", "00dae6639aaf_mr.mp3"),
    "3ba172f9ecf9_mp4": ("outputs/songs/3ba172f9ecf9_karaoke_video.mp4", "video/mp4", "3ba172f9ecf9.mp4"),
    "3ba172f9ecf9_mp3": ("outputs/songs/3ba172f9ecf9_mr.mp3", "audio/mpeg", "3ba172f9ecf9_mr.mp3"),
    "2a79f4cbcb8c_mp4": ("outputs/songs/2a79f4cbcb8c_karaoke_video.mp4", "video/mp4", "2a79f4cbcb8c.mp4"),
    "2a79f4cbcb8c_mp3": ("outputs/songs/2a79f4cbcb8c_mr.mp3", "audio/mpeg", "2a79f4cbcb8c_mr.mp3"),
    "e0cff91d8c49_mp4": ("outputs/songs/e0cff91d8c49_karaoke_video.mp4", "video/mp4", "e0cff91d8c49.mp4"),
    "e0cff91d8c49_mp3": ("outputs/songs/e0cff91d8c49_mr.mp3", "audio/mpeg", "e0cff91d8c49_mr.mp3"),
    "a6478dd475d6_mp4": ("outputs/songs/a6478dd475d6_karaoke_video.mp4", "video/mp4", "a6478dd475d6.mp4"),
    "a6478dd475d6_mp3": ("outputs/songs/a6478dd475d6_mr.mp3", "audio/mpeg", "a6478dd475d6_mr.mp3"),
    "4ffb4af94ce2_mp4": ("outputs/songs/4ffb4af94ce2_karaoke_video.mp4", "video/mp4", "4ffb4af94ce2.mp4"),
    "4ffb4af94ce2_mp3": ("outputs/songs/4ffb4af94ce2_mr.mp3", "audio/mpeg", "4ffb4af94ce2_mr.mp3"),
    "af432c6d57a0_mp4": ("outputs/songs/af432c6d57a0_karaoke_video.mp4", "video/mp4", "af432c6d57a0.mp4"),
    "af432c6d57a0_mp3": ("outputs/songs/af432c6d57a0_mr.mp3", "audio/mpeg", "af432c6d57a0_mr.mp3"),
    "b7897deba0dc_mp4": ("outputs/songs/b7897deba0dc_karaoke_video.mp4", "video/mp4", "b7897deba0dc.mp4"),
    "b7897deba0dc_mp3": ("outputs/songs/b7897deba0dc_mr.mp3", "audio/mpeg", "b7897deba0dc_mr.mp3"),
    "a72a9b8a28ac_mp4": ("outputs/songs/a72a9b8a28ac_karaoke_video.mp4", "video/mp4", "a72a9b8a28ac.mp4"),
    "a72a9b8a28ac_mp3": ("outputs/songs/a72a9b8a28ac_mr.mp3", "audio/mpeg", "a72a9b8a28ac_mr.mp3"),
    "dd14dac38355_mp4": ("outputs/songs/dd14dac38355_karaoke_video.mp4", "video/mp4", "dd14dac38355.mp4"),
    "dd14dac38355_mp3": ("outputs/songs/dd14dac38355_mr.mp3", "audio/mpeg", "dd14dac38355_mr.mp3"),
    "2279d05a1718_mp4": ("outputs/songs/2279d05a1718_karaoke_video.mp4", "video/mp4", "2279d05a1718.mp4"),
    "2279d05a1718_mp3": ("outputs/songs/2279d05a1718_mr.mp3", "audio/mpeg", "2279d05a1718_mr.mp3"),
    "38f7204f0d5c_mp4": ("outputs/songs/38f7204f0d5c_karaoke_video.mp4", "video/mp4", "38f7204f0d5c.mp4"),
    "38f7204f0d5c_mp3": ("outputs/songs/38f7204f0d5c_mr.mp3", "audio/mpeg", "38f7204f0d5c_mr.mp3"),
    "9a737871ab21_mp4": ("outputs/songs/9a737871ab21_karaoke_video.mp4", "video/mp4", "9a737871ab21.mp4"),
    "9a737871ab21_mp3": ("outputs/songs/9a737871ab21_mr.mp3", "audio/mpeg", "9a737871ab21_mr.mp3"),
    "9fa4fa3eb126_mp4": ("outputs/songs/9fa4fa3eb126_karaoke_video.mp4", "video/mp4", "9fa4fa3eb126.mp4"),
    "9fa4fa3eb126_mp3": ("outputs/songs/9fa4fa3eb126_mr.mp3", "audio/mpeg", "9fa4fa3eb126_mr.mp3"),
    "d6a5fc1af978_mp4": ("outputs/songs/d6a5fc1af978_karaoke_video.mp4", "video/mp4", "d6a5fc1af978.mp4"),
    "d6a5fc1af978_mp3": ("outputs/songs/d6a5fc1af978_mr.mp3", "audio/mpeg", "d6a5fc1af978_mr.mp3"),
    "1469b71911c1_mp4": ("outputs/songs/1469b71911c1_karaoke_video.mp4", "video/mp4", "1469b71911c1.mp4"),
    "1469b71911c1_mp3": ("outputs/songs/1469b71911c1_mr.mp3", "audio/mpeg", "1469b71911c1_mr.mp3"),
    "4fc75aa6c577_mp4": ("outputs/songs/4fc75aa6c577_karaoke_video.mp4", "video/mp4", "4fc75aa6c577.mp4"),
    "4fc75aa6c577_mp3": ("outputs/songs/4fc75aa6c577_mr.mp3", "audio/mpeg", "4fc75aa6c577_mr.mp3"),
    "eed1b4615348_mp4": ("outputs/songs/eed1b4615348_karaoke_video.mp4", "video/mp4", "eed1b4615348.mp4"),
    "eed1b4615348_mp3": ("outputs/songs/eed1b4615348_mr.mp3", "audio/mpeg", "eed1b4615348_mr.mp3"),
    "3fc17f9f6d71_mp4": ("outputs/songs/3fc17f9f6d71_karaoke_video.mp4", "video/mp4", "3fc17f9f6d71.mp4"),
    "3fc17f9f6d71_mp3": ("outputs/songs/3fc17f9f6d71_mr.mp3", "audio/mpeg", "3fc17f9f6d71_mr.mp3"),
    "b9993b89247c_mp4": ("outputs/songs/b9993b89247c_karaoke_video.mp4", "video/mp4", "b9993b89247c.mp4"),
    "b9993b89247c_mp3": ("outputs/songs/b9993b89247c_mr.mp3", "audio/mpeg", "b9993b89247c_mr.mp3"),
    "25b5bae6437b_mp4": ("outputs/songs/25b5bae6437b_karaoke_video.mp4", "video/mp4", "25b5bae6437b.mp4"),
    "25b5bae6437b_mp3": ("outputs/songs/25b5bae6437b_mr.mp3", "audio/mpeg", "25b5bae6437b_mr.mp3"),
    "782a89291775_mp4": ("outputs/songs/782a89291775_karaoke_video.mp4", "video/mp4", "782a89291775.mp4"),
    "782a89291775_mp3": ("outputs/songs/782a89291775_mr.mp3", "audio/mpeg", "782a89291775_mr.mp3"),
    "9efa53049d7c_mp4": ("outputs/songs/9efa53049d7c_karaoke_video.mp4", "video/mp4", "9efa53049d7c.mp4"),
    "9efa53049d7c_mp3": ("outputs/songs/9efa53049d7c_mr.mp3", "audio/mpeg", "9efa53049d7c_mr.mp3"),
    "87edff9650f4_mp4": ("outputs/songs/87edff9650f4_karaoke_video.mp4", "video/mp4", "87edff9650f4.mp4"),
    "87edff9650f4_mp3": ("outputs/songs/87edff9650f4_mr.mp3", "audio/mpeg", "87edff9650f4_mr.mp3"),
    "9c500ae87613_mp4": ("outputs/songs/9c500ae87613_karaoke_video.mp4", "video/mp4", "9c500ae87613.mp4"),
    "9c500ae87613_mp3": ("outputs/songs/9c500ae87613_mr.mp3", "audio/mpeg", "9c500ae87613_mr.mp3"),
    "f6dc92283a39_mp4": ("outputs/songs/f6dc92283a39_karaoke_video.mp4", "video/mp4", "f6dc92283a39.mp4"),
    "f6dc92283a39_mp3": ("outputs/songs/f6dc92283a39_mr.mp3", "audio/mpeg", "f6dc92283a39_mr.mp3"),
    "37616d619c75_mp4": ("outputs/songs/37616d619c75_karaoke_video.mp4", "video/mp4", "37616d619c75.mp4"),
    "37616d619c75_mp3": ("outputs/songs/37616d619c75_mr.mp3", "audio/mpeg", "37616d619c75_mr.mp3"),
    "7724d1cb5d98_mp4": ("outputs/songs/7724d1cb5d98_karaoke_video.mp4", "video/mp4", "7724d1cb5d98.mp4"),
    "7724d1cb5d98_mp3": ("outputs/songs/7724d1cb5d98_mr.mp3", "audio/mpeg", "7724d1cb5d98_mr.mp3"),
    "ffb63f28cb35_mp4": ("outputs/songs/ffb63f28cb35_karaoke_video.mp4", "video/mp4", "ffb63f28cb35.mp4"),
    "ffb63f28cb35_mp3": ("outputs/songs/ffb63f28cb35_mr.mp3", "audio/mpeg", "ffb63f28cb35_mr.mp3"),
    "04af64db9013_mp4": ("outputs/songs/04af64db9013_karaoke_video.mp4", "video/mp4", "04af64db9013.mp4"),
    "04af64db9013_mp3": ("outputs/songs/04af64db9013_mr.mp3", "audio/mpeg", "04af64db9013_mr.mp3"),
    "7d5b765b38a5_mp4": ("outputs/songs/7d5b765b38a5_karaoke_video.mp4", "video/mp4", "7d5b765b38a5.mp4"),
    "7d5b765b38a5_mp3": ("outputs/songs/7d5b765b38a5_mr.mp3", "audio/mpeg", "7d5b765b38a5_mr.mp3"),
    "b5c1119f1f7e_mp4": ("outputs/songs/b5c1119f1f7e_karaoke_video.mp4", "video/mp4", "b5c1119f1f7e.mp4"),
    "b5c1119f1f7e_mp3": ("outputs/songs/b5c1119f1f7e_mr.mp3", "audio/mpeg", "b5c1119f1f7e_mr.mp3"),
    "35925074c274_mp4": ("outputs/songs/35925074c274_karaoke_video.mp4", "video/mp4", "35925074c274.mp4"),
    "35925074c274_mp3": ("outputs/songs/35925074c274_mr.mp3", "audio/mpeg", "35925074c274_mr.mp3"),
    "61eb438447ab_mp4": ("outputs/songs/61eb438447ab_karaoke_video.mp4", "video/mp4", "61eb438447ab.mp4"),
    "61eb438447ab_mp3": ("outputs/songs/61eb438447ab_mr.mp3", "audio/mpeg", "61eb438447ab_mr.mp3"),
    "d5060e50ff13_mp4": ("outputs/songs/d5060e50ff13_karaoke_video.mp4", "video/mp4", "d5060e50ff13.mp4"),
    "d5060e50ff13_mp3": ("outputs/songs/d5060e50ff13_mr.mp3", "audio/mpeg", "d5060e50ff13_mr.mp3"),
    "9561bc4d6b97_mp4": ("outputs/songs/9561bc4d6b97_karaoke_video.mp4", "video/mp4", "9561bc4d6b97.mp4"),
    "9561bc4d6b97_mp3": ("outputs/songs/9561bc4d6b97_mr.mp3", "audio/mpeg", "9561bc4d6b97_mr.mp3"),
    "c4ba46db8146_mp4": ("outputs/songs/c4ba46db8146_karaoke_video.mp4", "video/mp4", "c4ba46db8146.mp4"),
    "c4ba46db8146_mp3": ("outputs/songs/c4ba46db8146_mr.mp3", "audio/mpeg", "c4ba46db8146_mr.mp3"),
    "8ab2cef0ba37_mp4": ("outputs/songs/8ab2cef0ba37_karaoke_video.mp4", "video/mp4", "8ab2cef0ba37.mp4"),
    "8ab2cef0ba37_mp3": ("outputs/songs/8ab2cef0ba37_mr.mp3", "audio/mpeg", "8ab2cef0ba37_mr.mp3"),
    "e9e09acd4e38_mp4": ("outputs/songs/e9e09acd4e38_karaoke_video.mp4", "video/mp4", "e9e09acd4e38.mp4"),
    "e9e09acd4e38_mp3": ("outputs/songs/e9e09acd4e38_mr.mp3", "audio/mpeg", "e9e09acd4e38_mr.mp3"),
    "44fd69210d36_mp4": ("outputs/songs/44fd69210d36_karaoke_video.mp4", "video/mp4", "44fd69210d36.mp4"),
    "44fd69210d36_mp3": ("outputs/songs/44fd69210d36_mr.mp3", "audio/mpeg", "44fd69210d36_mr.mp3"),
    "e245f5fc7028_mp4": ("outputs/songs/e245f5fc7028_karaoke_video.mp4", "video/mp4", "e245f5fc7028.mp4"),
    "e245f5fc7028_mp3": ("outputs/songs/e245f5fc7028_mr.mp3", "audio/mpeg", "e245f5fc7028_mr.mp3"),
    "5bf258fec71c_mp4": ("outputs/songs/5bf258fec71c_karaoke_video.mp4", "video/mp4", "5bf258fec71c.mp4"),
    "5bf258fec71c_mp3": ("outputs/songs/5bf258fec71c_mr.mp3", "audio/mpeg", "5bf258fec71c_mr.mp3"),
    "c9d672d5cc44_mp4": ("outputs/songs/c9d672d5cc44_karaoke_video.mp4", "video/mp4", "c9d672d5cc44.mp4"),
    "c9d672d5cc44_mp3": ("outputs/songs/c9d672d5cc44_mr.mp3", "audio/mpeg", "c9d672d5cc44_mr.mp3"),
    "9c60304d2033_mp4": ("outputs/songs/9c60304d2033_karaoke_video.mp4", "video/mp4", "9c60304d2033.mp4"),
    "9c60304d2033_mp3": ("outputs/songs/9c60304d2033_mr.mp3", "audio/mpeg", "9c60304d2033_mr.mp3"),
    "598bc4a65fca_mp4": ("outputs/songs/598bc4a65fca_karaoke_video.mp4", "video/mp4", "598bc4a65fca.mp4"),
    "598bc4a65fca_mp3": ("outputs/songs/598bc4a65fca_mr.mp3", "audio/mpeg", "598bc4a65fca_mr.mp3"),
    "5d777a28e563_mp4": ("outputs/songs/5d777a28e563_karaoke_video.mp4", "video/mp4", "5d777a28e563.mp4"),
    "5d777a28e563_mp3": ("outputs/songs/5d777a28e563_mr.mp3", "audio/mpeg", "5d777a28e563_mr.mp3"),
    "6514d58a015d_mp4": ("outputs/songs/6514d58a015d_karaoke_video.mp4", "video/mp4", "6514d58a015d.mp4"),
    "6514d58a015d_mp3": ("outputs/songs/6514d58a015d_mr.mp3", "audio/mpeg", "6514d58a015d_mr.mp3"),
    "267dd2c0b33a_mp4": ("outputs/songs/267dd2c0b33a_karaoke_video.mp4", "video/mp4", "267dd2c0b33a.mp4"),
    "267dd2c0b33a_mp3": ("outputs/songs/267dd2c0b33a_mr.mp3", "audio/mpeg", "267dd2c0b33a_mr.mp3"),
    "a7b20dbefaef_mp4": ("outputs/songs/a7b20dbefaef_karaoke_video.mp4", "video/mp4", "a7b20dbefaef.mp4"),
    "a7b20dbefaef_mp3": ("outputs/songs/a7b20dbefaef_mr.mp3", "audio/mpeg", "a7b20dbefaef_mr.mp3"),
    "fc707f5d0a1a_mp4": ("outputs/songs/fc707f5d0a1a_karaoke_video.mp4", "video/mp4", "fc707f5d0a1a.mp4"),
    "fc707f5d0a1a_mp3": ("outputs/songs/fc707f5d0a1a_mr.mp3", "audio/mpeg", "fc707f5d0a1a_mr.mp3"),
    "c50bede2d0d7_mp4": ("outputs/songs/c50bede2d0d7_karaoke_video.mp4", "video/mp4", "c50bede2d0d7.mp4"),
    "c50bede2d0d7_mp3": ("outputs/songs/c50bede2d0d7_mr.mp3", "audio/mpeg", "c50bede2d0d7_mr.mp3"),
    "283a28c8e6f9_mp4": ("outputs/songs/283a28c8e6f9_karaoke_video.mp4", "video/mp4", "283a28c8e6f9.mp4"),
    "283a28c8e6f9_mp3": ("outputs/songs/283a28c8e6f9_mr.mp3", "audio/mpeg", "283a28c8e6f9_mr.mp3"),
    "f1ec1c543e3f_mp4": ("outputs/songs/f1ec1c543e3f_karaoke_video.mp4", "video/mp4", "f1ec1c543e3f.mp4"),
    "f1ec1c543e3f_mp3": ("outputs/songs/f1ec1c543e3f_mr.mp3", "audio/mpeg", "f1ec1c543e3f_mr.mp3"),
    "c047c16de8fe_mp4": ("outputs/songs/c047c16de8fe_karaoke_video.mp4", "video/mp4", "c047c16de8fe.mp4"),
    "c047c16de8fe_mp3": ("outputs/songs/c047c16de8fe_mr.mp3", "audio/mpeg", "c047c16de8fe_mr.mp3"),
    "2141ae706009_mp4": ("outputs/songs/2141ae706009_karaoke_video.mp4", "video/mp4", "2141ae706009.mp4"),
    "2141ae706009_mp3": ("outputs/songs/2141ae706009_mr.mp3", "audio/mpeg", "2141ae706009_mr.mp3"),
    "d87fa64dedc2_mp4": ("outputs/songs/d87fa64dedc2_karaoke_video.mp4", "video/mp4", "d87fa64dedc2.mp4"),
    "d87fa64dedc2_mp3": ("outputs/songs/d87fa64dedc2_mr.mp3", "audio/mpeg", "d87fa64dedc2_mr.mp3"),
    "9651bd68c11b_mp4": ("outputs/songs/9651bd68c11b_karaoke_video.mp4", "video/mp4", "9651bd68c11b.mp4"),
    "9651bd68c11b_mp3": ("outputs/songs/9651bd68c11b_mr.mp3", "audio/mpeg", "9651bd68c11b_mr.mp3"),
    "0d8faff94f04_mp4": ("outputs/songs/0d8faff94f04_karaoke_video.mp4", "video/mp4", "0d8faff94f04.mp4"),
    "0d8faff94f04_mp3": ("outputs/songs/0d8faff94f04_mr.mp3", "audio/mpeg", "0d8faff94f04_mr.mp3"),
    "ce71d2d0108b_mp4": ("outputs/songs/ce71d2d0108b_karaoke_video.mp4", "video/mp4", "ce71d2d0108b.mp4"),
    "ce71d2d0108b_mp3": ("outputs/songs/ce71d2d0108b_mr.mp3", "audio/mpeg", "ce71d2d0108b_mr.mp3"),
    "26c1a830c4a6_mp4": ("outputs/songs/26c1a830c4a6_karaoke_video.mp4", "video/mp4", "26c1a830c4a6.mp4"),
    "26c1a830c4a6_mp3": ("outputs/songs/26c1a830c4a6_mr.mp3", "audio/mpeg", "26c1a830c4a6_mr.mp3"),
    "c6b0ba796b63_mp4": ("outputs/songs/c6b0ba796b63_karaoke_video.mp4", "video/mp4", "c6b0ba796b63.mp4"),
    "c6b0ba796b63_mp3": ("outputs/songs/c6b0ba796b63_mr.mp3", "audio/mpeg", "c6b0ba796b63_mr.mp3"),
    "c5732826cbbd_mp4": ("outputs/songs/c5732826cbbd_karaoke_video.mp4", "video/mp4", "c5732826cbbd.mp4"),
    "c5732826cbbd_mp3": ("outputs/songs/c5732826cbbd_mr.mp3", "audio/mpeg", "c5732826cbbd_mr.mp3"),
    "8c678fac3643_mp4": ("outputs/songs/8c678fac3643_karaoke_video.mp4", "video/mp4", "8c678fac3643.mp4"),
    "8c678fac3643_mp3": ("outputs/songs/8c678fac3643_mr.mp3", "audio/mpeg", "8c678fac3643_mr.mp3"),
    "77b95df24f19_mp4": ("outputs/songs/77b95df24f19_karaoke_video.mp4", "video/mp4", "77b95df24f19.mp4"),
    "77b95df24f19_mp3": ("outputs/songs/77b95df24f19_mr.mp3", "audio/mpeg", "77b95df24f19_mr.mp3"),
    "7d87c0e695eb_mp4": ("outputs/songs/7d87c0e695eb_karaoke_video.mp4", "video/mp4", "7d87c0e695eb.mp4"),
    "7d87c0e695eb_mp3": ("outputs/songs/7d87c0e695eb_mr.mp3", "audio/mpeg", "7d87c0e695eb_mr.mp3"),
    "708589fa9475_mp4": ("outputs/songs/708589fa9475_karaoke_video.mp4", "video/mp4", "708589fa9475.mp4"),
    "708589fa9475_mp3": ("outputs/songs/708589fa9475_mr.mp3", "audio/mpeg", "708589fa9475_mr.mp3"),
    "78e9eb8c5092_mp4": ("outputs/songs/78e9eb8c5092_karaoke_video.mp4", "video/mp4", "78e9eb8c5092.mp4"),
    "78e9eb8c5092_mp3": ("outputs/songs/78e9eb8c5092_mr.mp3", "audio/mpeg", "78e9eb8c5092_mr.mp3"),
    "e375c22d9dd1_mp4": ("outputs/songs/e375c22d9dd1_karaoke_video.mp4", "video/mp4", "e375c22d9dd1.mp4"),
    "e375c22d9dd1_mp3": ("outputs/songs/e375c22d9dd1_mr.mp3", "audio/mpeg", "e375c22d9dd1_mr.mp3"),
    "f3c1be4ad73f_mp4": ("outputs/songs/f3c1be4ad73f_karaoke_video.mp4", "video/mp4", "f3c1be4ad73f.mp4"),
    "f3c1be4ad73f_mp3": ("outputs/songs/f3c1be4ad73f_mr.mp3", "audio/mpeg", "f3c1be4ad73f_mr.mp3"),
    "ea3a867bcb81_mp4": ("outputs/songs/ea3a867bcb81_karaoke_video.mp4", "video/mp4", "ea3a867bcb81.mp4"),
    "ea3a867bcb81_mp3": ("outputs/songs/ea3a867bcb81_mr.mp3", "audio/mpeg", "ea3a867bcb81_mr.mp3"),
    "fac16c0aada1_mp4": ("outputs/songs/fac16c0aada1_karaoke_video.mp4", "video/mp4", "fac16c0aada1.mp4"),
    "fac16c0aada1_mp3": ("outputs/songs/fac16c0aada1_mr.mp3", "audio/mpeg", "fac16c0aada1_mr.mp3"),
    "e6892c8c3319_mp4": ("outputs/songs/e6892c8c3319_karaoke_video.mp4", "video/mp4", "e6892c8c3319.mp4"),
    "e6892c8c3319_mp3": ("outputs/songs/e6892c8c3319_mr.mp3", "audio/mpeg", "e6892c8c3319_mr.mp3"),
    "f47f7cef8799_mp4": ("outputs/songs/f47f7cef8799_karaoke_video.mp4", "video/mp4", "f47f7cef8799.mp4"),
    "f47f7cef8799_mp3": ("outputs/songs/f47f7cef8799_mr.mp3", "audio/mpeg", "f47f7cef8799_mr.mp3"),
    "c4b48d8deda5_mp4": ("outputs/songs/c4b48d8deda5_karaoke_video.mp4", "video/mp4", "c4b48d8deda5.mp4"),
    "c4b48d8deda5_mp3": ("outputs/songs/c4b48d8deda5_mr.mp3", "audio/mpeg", "c4b48d8deda5_mr.mp3"),
    "39923df63416_mp4": ("outputs/songs/39923df63416_karaoke_video.mp4", "video/mp4", "39923df63416.mp4"),
    "39923df63416_mp3": ("outputs/songs/39923df63416_mr.mp3", "audio/mpeg", "39923df63416_mr.mp3"),
    "d952d29b895f_mp4": ("outputs/songs/d952d29b895f_karaoke_video.mp4", "video/mp4", "d952d29b895f.mp4"),
    "d952d29b895f_mp3": ("outputs/songs/d952d29b895f_mr.mp3", "audio/mpeg", "d952d29b895f_mr.mp3"),
    "6b46583e9b90_mp4": ("outputs/songs/6b46583e9b90_karaoke_video.mp4", "video/mp4", "6b46583e9b90.mp4"),
    "6b46583e9b90_mp3": ("outputs/songs/6b46583e9b90_mr.mp3", "audio/mpeg", "6b46583e9b90_mr.mp3"),
    "acddabe5164e_mp4": ("outputs/songs/acddabe5164e_karaoke_video.mp4", "video/mp4", "acddabe5164e.mp4"),
    "acddabe5164e_mp3": ("outputs/songs/acddabe5164e_mr.mp3", "audio/mpeg", "acddabe5164e_mr.mp3"),
    "27b10190fb65_mp4": ("outputs/songs/27b10190fb65_karaoke_video.mp4", "video/mp4", "27b10190fb65.mp4"),
    "27b10190fb65_mp3": ("outputs/songs/27b10190fb65_mr.mp3", "audio/mpeg", "27b10190fb65_mr.mp3"),
    "2c36555328d0_mp4": ("outputs/songs/2c36555328d0_karaoke_video.mp4", "video/mp4", "2c36555328d0.mp4"),
    "2c36555328d0_mp3": ("outputs/songs/2c36555328d0_mr.mp3", "audio/mpeg", "2c36555328d0_mr.mp3"),
    "582e5d5d8ce7_mp4": ("outputs/songs/582e5d5d8ce7_karaoke_video.mp4", "video/mp4", "582e5d5d8ce7.mp4"),
    "582e5d5d8ce7_mp3": ("outputs/songs/582e5d5d8ce7_mr.mp3", "audio/mpeg", "582e5d5d8ce7_mr.mp3"),
    "5acf85297ecc_mp4": ("outputs/songs/5acf85297ecc_karaoke_video.mp4", "video/mp4", "5acf85297ecc.mp4"),
    "5acf85297ecc_mp3": ("outputs/songs/5acf85297ecc_mr.mp3", "audio/mpeg", "5acf85297ecc_mr.mp3"),
    "a032d4d322be_mp4": ("outputs/songs/a032d4d322be_karaoke_video.mp4", "video/mp4", "a032d4d322be.mp4"),
    "a032d4d322be_mp3": ("outputs/songs/a032d4d322be_mr.mp3", "audio/mpeg", "a032d4d322be_mr.mp3"),
    "fbbbd206a438_mp4": ("outputs/songs/fbbbd206a438_karaoke_video.mp4", "video/mp4", "fbbbd206a438.mp4"),
    "fbbbd206a438_mp3": ("outputs/songs/fbbbd206a438_mr.mp3", "audio/mpeg", "fbbbd206a438_mr.mp3"),
    "98aa6f5b565e_mp4": ("outputs/songs/98aa6f5b565e_karaoke_video.mp4", "video/mp4", "98aa6f5b565e.mp4"),
    "98aa6f5b565e_mp3": ("outputs/songs/98aa6f5b565e_mr.mp3", "audio/mpeg", "98aa6f5b565e_mr.mp3"),
    "315ea7d8fdb9_mp4": ("outputs/songs/315ea7d8fdb9_karaoke_video.mp4", "video/mp4", "315ea7d8fdb9.mp4"),
    "315ea7d8fdb9_mp3": ("outputs/songs/315ea7d8fdb9_mr.mp3", "audio/mpeg", "315ea7d8fdb9_mr.mp3"),
    "2dd4d08ab8a6_mp4": ("outputs/songs/2dd4d08ab8a6_karaoke_video.mp4", "video/mp4", "2dd4d08ab8a6.mp4"),
    "2dd4d08ab8a6_mp3": ("outputs/songs/2dd4d08ab8a6_mr.mp3", "audio/mpeg", "2dd4d08ab8a6_mr.mp3"),
    "7a3e7dd3b089_mp4": ("outputs/songs/7a3e7dd3b089_karaoke_video.mp4", "video/mp4", "7a3e7dd3b089.mp4"),
    "7a3e7dd3b089_mp3": ("outputs/songs/7a3e7dd3b089_mr.mp3", "audio/mpeg", "7a3e7dd3b089_mr.mp3"),
    "334e0d01df2c_mp4": ("outputs/songs/334e0d01df2c_karaoke_video.mp4", "video/mp4", "334e0d01df2c.mp4"),
    "334e0d01df2c_mp3": ("outputs/songs/334e0d01df2c_mr.mp3", "audio/mpeg", "334e0d01df2c_mr.mp3"),
    "dc8575a37d57_mp4": ("outputs/songs/dc8575a37d57_karaoke_video.mp4", "video/mp4", "dc8575a37d57.mp4"),
    "dc8575a37d57_mp3": ("outputs/songs/dc8575a37d57_mr.mp3", "audio/mpeg", "dc8575a37d57_mr.mp3"),
    "acfb1faf5907_mp4": ("outputs/songs/acfb1faf5907_karaoke_video.mp4", "video/mp4", "acfb1faf5907.mp4"),
    "acfb1faf5907_mp3": ("outputs/songs/acfb1faf5907_mr.mp3", "audio/mpeg", "acfb1faf5907_mr.mp3"),
    "c27bdc06916f_mp4": ("outputs/songs/c27bdc06916f_karaoke_video.mp4", "video/mp4", "c27bdc06916f.mp4"),
    "c27bdc06916f_mp3": ("outputs/songs/c27bdc06916f_mr.mp3", "audio/mpeg", "c27bdc06916f_mr.mp3"),
    "83346e77bd06_mp4": ("outputs/songs/83346e77bd06_karaoke_video.mp4", "video/mp4", "83346e77bd06.mp4"),
    "83346e77bd06_mp3": ("outputs/songs/83346e77bd06_mr.mp3", "audio/mpeg", "83346e77bd06_mr.mp3"),
    "4bae9e47f825_mp4": ("outputs/songs/4bae9e47f825_karaoke_video.mp4", "video/mp4", "4bae9e47f825.mp4"),
    "4bae9e47f825_mp3": ("outputs/songs/4bae9e47f825_mr.mp3", "audio/mpeg", "4bae9e47f825_mr.mp3"),
    "f84109273801_mp4": ("outputs/songs/f84109273801_karaoke_video.mp4", "video/mp4", "f84109273801.mp4"),
    "f84109273801_mp3": ("outputs/songs/f84109273801_mr.mp3", "audio/mpeg", "f84109273801_mr.mp3"),
    "63fa0b5af8c9_mp4": ("outputs/songs/63fa0b5af8c9_karaoke_video.mp4", "video/mp4", "63fa0b5af8c9.mp4"),
    "63fa0b5af8c9_mp3": ("outputs/songs/63fa0b5af8c9_mr.mp3", "audio/mpeg", "63fa0b5af8c9_mr.mp3"),
    "2029f291fa3d_mp4": ("outputs/songs/2029f291fa3d_karaoke_video.mp4", "video/mp4", "2029f291fa3d.mp4"),
    "2029f291fa3d_mp3": ("outputs/songs/2029f291fa3d_mr.mp3", "audio/mpeg", "2029f291fa3d_mr.mp3"),
    "07065e79dbd8_mp4": ("outputs/songs/07065e79dbd8_karaoke_video.mp4", "video/mp4", "07065e79dbd8.mp4"),
    "07065e79dbd8_mp3": ("outputs/songs/07065e79dbd8_mr.mp3", "audio/mpeg", "07065e79dbd8_mr.mp3"),
    "ef5c64ef42c1_mp4": ("outputs/songs/ef5c64ef42c1_karaoke_video.mp4", "video/mp4", "ef5c64ef42c1.mp4"),
    "ef5c64ef42c1_mp3": ("outputs/songs/ef5c64ef42c1_mr.mp3", "audio/mpeg", "ef5c64ef42c1_mr.mp3"),
    "84f7f9baaa16_mp4": ("outputs/songs/84f7f9baaa16_karaoke_video.mp4", "video/mp4", "84f7f9baaa16.mp4"),
    "84f7f9baaa16_mp3": ("outputs/songs/84f7f9baaa16_mr.mp3", "audio/mpeg", "84f7f9baaa16_mr.mp3"),
    "e04cda9306a0_mp4": ("outputs/songs/e04cda9306a0_karaoke_video.mp4", "video/mp4", "e04cda9306a0.mp4"),
    "e04cda9306a0_mp3": ("outputs/songs/e04cda9306a0_mr.mp3", "audio/mpeg", "e04cda9306a0_mr.mp3"),
    "e31be57f1793_mp4": ("outputs/songs/e31be57f1793_karaoke_video.mp4", "video/mp4", "e31be57f1793.mp4"),
    "e31be57f1793_mp3": ("outputs/songs/e31be57f1793_mr.mp3", "audio/mpeg", "e31be57f1793_mr.mp3"),
    "aa8e05c80060_mp4": ("outputs/songs/aa8e05c80060_karaoke_video.mp4", "video/mp4", "aa8e05c80060.mp4"),
    "aa8e05c80060_mp3": ("outputs/songs/aa8e05c80060_mr.mp3", "audio/mpeg", "aa8e05c80060_mr.mp3"),
    "660af2739e9c_mp4": ("outputs/songs/660af2739e9c_karaoke_video.mp4", "video/mp4", "660af2739e9c.mp4"),
    "660af2739e9c_mp3": ("outputs/songs/660af2739e9c_mr.mp3", "audio/mpeg", "660af2739e9c_mr.mp3"),
    "7445f910835f_mp4": ("outputs/songs/7445f910835f_karaoke_video.mp4", "video/mp4", "7445f910835f.mp4"),
    "7445f910835f_mp3": ("outputs/songs/7445f910835f_mr.mp3", "audio/mpeg", "7445f910835f_mr.mp3"),
    "5c4510ea5adf_mp4": ("outputs/songs/5c4510ea5adf_karaoke_video.mp4", "video/mp4", "5c4510ea5adf.mp4"),
    "5c4510ea5adf_mp3": ("outputs/songs/5c4510ea5adf_mr.mp3", "audio/mpeg", "5c4510ea5adf_mr.mp3"),
    "d6e615783ed1_mp4": ("outputs/songs/d6e615783ed1_karaoke_video.mp4", "video/mp4", "d6e615783ed1.mp4"),
    "d6e615783ed1_mp3": ("outputs/songs/d6e615783ed1_mr.mp3", "audio/mpeg", "d6e615783ed1_mr.mp3"),
    "bd540e7e99d8_mp4": ("outputs/songs/bd540e7e99d8_karaoke_video.mp4", "video/mp4", "bd540e7e99d8.mp4"),
    "bd540e7e99d8_mp3": ("outputs/songs/bd540e7e99d8_mr.mp3", "audio/mpeg", "bd540e7e99d8_mr.mp3"),
    "994d08670357_mp4": ("outputs/songs/994d08670357_karaoke_video.mp4", "video/mp4", "994d08670357.mp4"),
    "994d08670357_mp3": ("outputs/songs/994d08670357_mr.mp3", "audio/mpeg", "994d08670357_mr.mp3"),
    "61fce2c6714b_mp4": ("outputs/songs/61fce2c6714b_karaoke_video.mp4", "video/mp4", "61fce2c6714b.mp4"),
    "61fce2c6714b_mp3": ("outputs/songs/61fce2c6714b_mr.mp3", "audio/mpeg", "61fce2c6714b_mr.mp3"),
    "c39b519fc5eb_mp4": ("outputs/songs/c39b519fc5eb_karaoke_video.mp4", "video/mp4", "c39b519fc5eb.mp4"),
    "c39b519fc5eb_mp3": ("outputs/songs/c39b519fc5eb_mr.mp3", "audio/mpeg", "c39b519fc5eb_mr.mp3"),
    "ec482d9993e2_mp4": ("outputs/songs/ec482d9993e2_karaoke_video.mp4", "video/mp4", "ec482d9993e2.mp4"),
    "ec482d9993e2_mp3": ("outputs/songs/ec482d9993e2_mr.mp3", "audio/mpeg", "ec482d9993e2_mr.mp3"),
    "20aa2101da7e_mp4": ("outputs/songs/20aa2101da7e_karaoke_video.mp4", "video/mp4", "20aa2101da7e.mp4"),
    "20aa2101da7e_mp3": ("outputs/songs/20aa2101da7e_mr.mp3", "audio/mpeg", "20aa2101da7e_mr.mp3"),
    "2a7b43858839_mp4": ("outputs/songs/2a7b43858839_karaoke_video.mp4", "video/mp4", "2a7b43858839.mp4"),
    "2a7b43858839_mp3": ("outputs/songs/2a7b43858839_mr.mp3", "audio/mpeg", "2a7b43858839_mr.mp3"),
    "b26035fa3144_mp4": ("outputs/songs/b26035fa3144_karaoke_video.mp4", "video/mp4", "b26035fa3144.mp4"),
    "b26035fa3144_mp3": ("outputs/songs/b26035fa3144_mr.mp3", "audio/mpeg", "b26035fa3144_mr.mp3"),
    "11b7b8cad6d1_mp4": ("outputs/songs/11b7b8cad6d1_karaoke_video.mp4", "video/mp4", "11b7b8cad6d1.mp4"),
    "11b7b8cad6d1_mp3": ("outputs/songs/11b7b8cad6d1_mr.mp3", "audio/mpeg", "11b7b8cad6d1_mr.mp3"),
    "b3e2b8beebe0_mp4": ("outputs/songs/b3e2b8beebe0_karaoke_video.mp4", "video/mp4", "b3e2b8beebe0.mp4"),
    "b3e2b8beebe0_mp3": ("outputs/songs/b3e2b8beebe0_mr.mp3", "audio/mpeg", "b3e2b8beebe0_mr.mp3"),
    "f4cb7fbe6b37_mp4": ("outputs/songs/f4cb7fbe6b37_karaoke_video.mp4", "video/mp4", "f4cb7fbe6b37.mp4"),
    "f4cb7fbe6b37_mp3": ("outputs/songs/f4cb7fbe6b37_mr.mp3", "audio/mpeg", "f4cb7fbe6b37_mr.mp3"),
    "497871d7f223_mp4": ("outputs/songs/497871d7f223_karaoke_video.mp4", "video/mp4", "497871d7f223.mp4"),
    "497871d7f223_mp3": ("outputs/songs/497871d7f223_mr.mp3", "audio/mpeg", "497871d7f223_mr.mp3"),
    "de3e0507a9cb_mp4": ("outputs/songs/de3e0507a9cb_karaoke_video.mp4", "video/mp4", "de3e0507a9cb.mp4"),
    "de3e0507a9cb_mp3": ("outputs/songs/de3e0507a9cb_mr.mp3", "audio/mpeg", "de3e0507a9cb_mr.mp3"),
    "9957c57b9335_mp4": ("outputs/songs/9957c57b9335_karaoke_video.mp4", "video/mp4", "9957c57b9335.mp4"),
    "9957c57b9335_mp3": ("outputs/songs/9957c57b9335_mr.mp3", "audio/mpeg", "9957c57b9335_mr.mp3"),
    "f085dcb77296_mp4": ("outputs/songs/f085dcb77296_karaoke_video.mp4", "video/mp4", "f085dcb77296.mp4"),
    "f085dcb77296_mp3": ("outputs/songs/f085dcb77296_mr.mp3", "audio/mpeg", "f085dcb77296_mr.mp3"),
    "32b2d5bd42b0_mp4": ("outputs/songs/32b2d5bd42b0_karaoke_video.mp4", "video/mp4", "32b2d5bd42b0.mp4"),
    "32b2d5bd42b0_mp3": ("outputs/songs/32b2d5bd42b0_mr.mp3", "audio/mpeg", "32b2d5bd42b0_mr.mp3"),
    "1fbead5a58cf_mp4": ("outputs/songs/1fbead5a58cf_karaoke_video.mp4", "video/mp4", "1fbead5a58cf.mp4"),
    "1fbead5a58cf_mp3": ("outputs/songs/1fbead5a58cf_mr.mp3", "audio/mpeg", "1fbead5a58cf_mr.mp3"),
    "3f986379ab85_mp4": ("outputs/songs/3f986379ab85_karaoke_video.mp4", "video/mp4", "3f986379ab85.mp4"),
    "3f986379ab85_mp3": ("outputs/songs/3f986379ab85_mr.mp3", "audio/mpeg", "3f986379ab85_mr.mp3"),
    "c2cdf7d28776_mp4": ("outputs/songs/c2cdf7d28776_karaoke_video.mp4", "video/mp4", "c2cdf7d28776.mp4"),
    "c2cdf7d28776_mp3": ("outputs/songs/c2cdf7d28776_mr.mp3", "audio/mpeg", "c2cdf7d28776_mr.mp3"),
    "084086e3496f_mp4": ("outputs/songs/084086e3496f_karaoke_video.mp4", "video/mp4", "084086e3496f.mp4"),
    "084086e3496f_mp3": ("outputs/songs/084086e3496f_mr.mp3", "audio/mpeg", "084086e3496f_mr.mp3"),
    "c6033f5d474e_mp4": ("outputs/songs/c6033f5d474e_karaoke_video.mp4", "video/mp4", "c6033f5d474e.mp4"),
    "c6033f5d474e_mp3": ("outputs/songs/c6033f5d474e_mr.mp3", "audio/mpeg", "c6033f5d474e_mr.mp3"),
    "2f604b4e561e_mp4": ("outputs/songs/2f604b4e561e_karaoke_video.mp4", "video/mp4", "2f604b4e561e.mp4"),
    "2f604b4e561e_mp3": ("outputs/songs/2f604b4e561e_mr.mp3", "audio/mpeg", "2f604b4e561e_mr.mp3"),
    "84e118bd1b10_mp4": ("outputs/songs/84e118bd1b10_karaoke_video.mp4", "video/mp4", "84e118bd1b10.mp4"),
    "84e118bd1b10_mp3": ("outputs/songs/84e118bd1b10_mr.mp3", "audio/mpeg", "84e118bd1b10_mr.mp3"),
    "ecdf1ccd9d92_mp4": ("outputs/songs/ecdf1ccd9d92_karaoke_video.mp4", "video/mp4", "ecdf1ccd9d92.mp4"),
    "ecdf1ccd9d92_mp3": ("outputs/songs/ecdf1ccd9d92_mr.mp3", "audio/mpeg", "ecdf1ccd9d92_mr.mp3"),
    "592313013e45_mp4": ("outputs/songs/592313013e45_karaoke_video.mp4", "video/mp4", "592313013e45.mp4"),
    "592313013e45_mp3": ("outputs/songs/592313013e45_mr.mp3", "audio/mpeg", "592313013e45_mr.mp3"),
    "7d1ff6eda0bc_mp4": ("outputs/songs/7d1ff6eda0bc_karaoke_video.mp4", "video/mp4", "7d1ff6eda0bc.mp4"),
    "7d1ff6eda0bc_mp3": ("outputs/songs/7d1ff6eda0bc_mr.mp3", "audio/mpeg", "7d1ff6eda0bc_mr.mp3"),
    "9be336df42e2_mp4": ("outputs/songs/9be336df42e2_karaoke_video.mp4", "video/mp4", "9be336df42e2.mp4"),
    "9be336df42e2_mp3": ("outputs/songs/9be336df42e2_mr.mp3", "audio/mpeg", "9be336df42e2_mr.mp3"),
    "ffc34e30aedc_mp4": ("outputs/songs/ffc34e30aedc_karaoke_video.mp4", "video/mp4", "ffc34e30aedc.mp4"),
    "ffc34e30aedc_mp3": ("outputs/songs/ffc34e30aedc_mr.mp3", "audio/mpeg", "ffc34e30aedc_mr.mp3"),
    "2bd74ab9ab02_mp4": ("outputs/songs/2bd74ab9ab02_karaoke_video.mp4", "video/mp4", "2bd74ab9ab02.mp4"),
    "2bd74ab9ab02_mp3": ("outputs/songs/2bd74ab9ab02_mr.mp3", "audio/mpeg", "2bd74ab9ab02_mr.mp3"),
    "9d2b21400222_mp4": ("outputs/songs/9d2b21400222_karaoke_video.mp4", "video/mp4", "9d2b21400222.mp4"),
    "9d2b21400222_mp3": ("outputs/songs/9d2b21400222_mr.mp3", "audio/mpeg", "9d2b21400222_mr.mp3"),
    "759f58f5f3eb_mp4": ("outputs/songs/759f58f5f3eb_karaoke_video.mp4", "video/mp4", "759f58f5f3eb.mp4"),
    "759f58f5f3eb_mp3": ("outputs/songs/759f58f5f3eb_mr.mp3", "audio/mpeg", "759f58f5f3eb_mr.mp3"),
    "6cd4d61e5699_mp4": ("outputs/songs/6cd4d61e5699_karaoke_video.mp4", "video/mp4", "6cd4d61e5699.mp4"),
    "6cd4d61e5699_mp3": ("outputs/songs/6cd4d61e5699_mr.mp3", "audio/mpeg", "6cd4d61e5699_mr.mp3"),
    "7fc0a63bb28c_mp4": ("outputs/songs/7fc0a63bb28c_karaoke_video.mp4", "video/mp4", "7fc0a63bb28c.mp4"),
    "7fc0a63bb28c_mp3": ("outputs/songs/7fc0a63bb28c_mr.mp3", "audio/mpeg", "7fc0a63bb28c_mr.mp3"),
    "c3ae59bc882f_mp4": ("outputs/songs/c3ae59bc882f_karaoke_video.mp4", "video/mp4", "c3ae59bc882f.mp4"),
    "c3ae59bc882f_mp3": ("outputs/songs/c3ae59bc882f_mr.mp3", "audio/mpeg", "c3ae59bc882f_mr.mp3"),
    # AUTO_BIG_BATCH_END
    # 호환성용 — 옛 링크
    "mp4": ("outputs/songs/golden_kjy_video_with_audio.mp4", "video/mp4", "golden_kjy_karaoke.mp4"),
    "mp3": ("outputs/songs/golden_kjy_cover.mp3", "audio/mpeg", "golden_kjy_cover.mp3"),
}

# 사용자 녹음 → 영상 + MR + 본인 보컬 mux
_SONG_VIDEO_MR = {
    "golden":       ("outputs/songs/golden_huntrix_video.mp4",   "outputs/songs/golden_huntrix_mr.mp3"),
    "winter_rain":  ("outputs/songs/winter_rain_video.mp4",      "outputs/songs/winter_rain_mr.mp3"),
    "aroha":        ("outputs/songs/aroha_video.mp4",            "outputs/songs/aroha_mr.mp3"),
    "ragu_yo":      ("outputs/songs/ragu_yo_video.mp4",          "outputs/songs/ragu_yo_mr.mp3"),
    "million_rose": ("outputs/songs/million_rose_video.mp4",     "outputs/songs/million_rose_mr.mp3"),
    "firefly":      ("outputs/songs/firefly_video.mp4",          "outputs/songs/firefly_mr.mp3"),
    "bts_swim":             ("outputs/songs/bts_swim_video.mp4",            "outputs/songs/bts_swim_mr.mp3"),
    "bts_body_to_body":     ("outputs/songs/bts_body_to_body_video.mp4",    "outputs/songs/bts_body_to_body_mr.mp3"),
    "bts_hooligan":         ("outputs/songs/bts_hooligan_video.mp4",        "outputs/songs/bts_hooligan_mr.mp3"),
    "bts_aliens":           ("outputs/songs/bts_aliens_video.mp4",          "outputs/songs/bts_aliens_mr.mp3"),
    "bts_fya":              ("outputs/songs/bts_fya_video.mp4",             "outputs/songs/bts_fya_mr.mp3"),
    "bts_2_0":              ("outputs/songs/bts_2_0_video.mp4",             "outputs/songs/bts_2_0_mr.mp3"),
    "bts_no29":             ("outputs/songs/bts_no29_video.mp4",            "outputs/songs/bts_no29_mr.mp3"),
    "bts_merry_go_round":   ("outputs/songs/bts_merry_go_round_video.mp4",  "outputs/songs/bts_merry_go_round_mr.mp3"),
    "bts_normal":           ("outputs/songs/bts_normal_video.mp4",          "outputs/songs/bts_normal_mr.mp3"),
    "bts_like_animals":     ("outputs/songs/bts_like_animals_video.mp4",    "outputs/songs/bts_like_animals_mr.mp3"),
    "bts_they_dont_know":   ("outputs/songs/bts_they_dont_know_video.mp4",  "outputs/songs/bts_they_dont_know_mr.mp3"),
    "bts_one_more_night":   ("outputs/songs/bts_one_more_night_video.mp4",  "outputs/songs/bts_one_more_night_mr.mp3"),
    "bts_please":           ("outputs/songs/bts_please_video.mp4",          "outputs/songs/bts_please_mr.mp3"),
    "bts_into_the_sun":     ("outputs/songs/bts_into_the_sun_video.mp4",    "outputs/songs/bts_into_the_sun_mr.mp3"),
    # AUTO_BIG_BATCH_START
    "ec3462c6204d": ("outputs/songs/ec3462c6204d_video.mp4", "outputs/songs/ec3462c6204d_mr.mp3"),
    "f489ba1aed62": ("outputs/songs/f489ba1aed62_video.mp4", "outputs/songs/f489ba1aed62_mr.mp3"),
    "61d8435fee43": ("outputs/songs/61d8435fee43_video.mp4", "outputs/songs/61d8435fee43_mr.mp3"),
    "f0f80f02d423": ("outputs/songs/f0f80f02d423_video.mp4", "outputs/songs/f0f80f02d423_mr.mp3"),
    "0d2fdeb6fbe3": ("outputs/songs/0d2fdeb6fbe3_video.mp4", "outputs/songs/0d2fdeb6fbe3_mr.mp3"),
    "aab5605956ea": ("outputs/songs/aab5605956ea_video.mp4", "outputs/songs/aab5605956ea_mr.mp3"),
    "361c30834910": ("outputs/songs/361c30834910_video.mp4", "outputs/songs/361c30834910_mr.mp3"),
    "ff710fee4a93": ("outputs/songs/ff710fee4a93_video.mp4", "outputs/songs/ff710fee4a93_mr.mp3"),
    "40793cae09bc": ("outputs/songs/40793cae09bc_video.mp4", "outputs/songs/40793cae09bc_mr.mp3"),
    "70666e49267b": ("outputs/songs/70666e49267b_video.mp4", "outputs/songs/70666e49267b_mr.mp3"),
    "47716f4a7f8d": ("outputs/songs/47716f4a7f8d_video.mp4", "outputs/songs/47716f4a7f8d_mr.mp3"),
    "3706b4142fd4": ("outputs/songs/3706b4142fd4_video.mp4", "outputs/songs/3706b4142fd4_mr.mp3"),
    "237b615a8f75": ("outputs/songs/237b615a8f75_video.mp4", "outputs/songs/237b615a8f75_mr.mp3"),
    "4c2bec12bcdc": ("outputs/songs/4c2bec12bcdc_video.mp4", "outputs/songs/4c2bec12bcdc_mr.mp3"),
    "dba0bed9fc49": ("outputs/songs/dba0bed9fc49_video.mp4", "outputs/songs/dba0bed9fc49_mr.mp3"),
    "d31f294c5c1f": ("outputs/songs/d31f294c5c1f_video.mp4", "outputs/songs/d31f294c5c1f_mr.mp3"),
    "06f86ee51cf1": ("outputs/songs/06f86ee51cf1_video.mp4", "outputs/songs/06f86ee51cf1_mr.mp3"),
    "797054ba8e19": ("outputs/songs/797054ba8e19_video.mp4", "outputs/songs/797054ba8e19_mr.mp3"),
    "02830fa43e1c": ("outputs/songs/02830fa43e1c_video.mp4", "outputs/songs/02830fa43e1c_mr.mp3"),
    "a024ad237db9": ("outputs/songs/a024ad237db9_video.mp4", "outputs/songs/a024ad237db9_mr.mp3"),
    "4e0e1e424097": ("outputs/songs/4e0e1e424097_video.mp4", "outputs/songs/4e0e1e424097_mr.mp3"),
    "7f5e4925f047": ("outputs/songs/7f5e4925f047_video.mp4", "outputs/songs/7f5e4925f047_mr.mp3"),
    "54f4d40844bb": ("outputs/songs/54f4d40844bb_video.mp4", "outputs/songs/54f4d40844bb_mr.mp3"),
    "99c9087935fc": ("outputs/songs/99c9087935fc_video.mp4", "outputs/songs/99c9087935fc_mr.mp3"),
    "a85665c3b7e7": ("outputs/songs/a85665c3b7e7_video.mp4", "outputs/songs/a85665c3b7e7_mr.mp3"),
    "6a38284d3226": ("outputs/songs/6a38284d3226_video.mp4", "outputs/songs/6a38284d3226_mr.mp3"),
    "206b3b5c41b7": ("outputs/songs/206b3b5c41b7_video.mp4", "outputs/songs/206b3b5c41b7_mr.mp3"),
    "0306d61c120a": ("outputs/songs/0306d61c120a_video.mp4", "outputs/songs/0306d61c120a_mr.mp3"),
    "bd5e8f2ee3a9": ("outputs/songs/bd5e8f2ee3a9_video.mp4", "outputs/songs/bd5e8f2ee3a9_mr.mp3"),
    "57d16a46903b": ("outputs/songs/57d16a46903b_video.mp4", "outputs/songs/57d16a46903b_mr.mp3"),
    "7d03218edfb1": ("outputs/songs/7d03218edfb1_video.mp4", "outputs/songs/7d03218edfb1_mr.mp3"),
    "fe76565de4fd": ("outputs/songs/fe76565de4fd_video.mp4", "outputs/songs/fe76565de4fd_mr.mp3"),
    "00dae6639aaf": ("outputs/songs/00dae6639aaf_video.mp4", "outputs/songs/00dae6639aaf_mr.mp3"),
    "3ba172f9ecf9": ("outputs/songs/3ba172f9ecf9_video.mp4", "outputs/songs/3ba172f9ecf9_mr.mp3"),
    "2a79f4cbcb8c": ("outputs/songs/2a79f4cbcb8c_video.mp4", "outputs/songs/2a79f4cbcb8c_mr.mp3"),
    "e0cff91d8c49": ("outputs/songs/e0cff91d8c49_video.mp4", "outputs/songs/e0cff91d8c49_mr.mp3"),
    "a6478dd475d6": ("outputs/songs/a6478dd475d6_video.mp4", "outputs/songs/a6478dd475d6_mr.mp3"),
    "4ffb4af94ce2": ("outputs/songs/4ffb4af94ce2_video.mp4", "outputs/songs/4ffb4af94ce2_mr.mp3"),
    "af432c6d57a0": ("outputs/songs/af432c6d57a0_video.mp4", "outputs/songs/af432c6d57a0_mr.mp3"),
    "b7897deba0dc": ("outputs/songs/b7897deba0dc_video.mp4", "outputs/songs/b7897deba0dc_mr.mp3"),
    "a72a9b8a28ac": ("outputs/songs/a72a9b8a28ac_video.mp4", "outputs/songs/a72a9b8a28ac_mr.mp3"),
    "dd14dac38355": ("outputs/songs/dd14dac38355_video.mp4", "outputs/songs/dd14dac38355_mr.mp3"),
    "2279d05a1718": ("outputs/songs/2279d05a1718_video.mp4", "outputs/songs/2279d05a1718_mr.mp3"),
    "38f7204f0d5c": ("outputs/songs/38f7204f0d5c_video.mp4", "outputs/songs/38f7204f0d5c_mr.mp3"),
    "9a737871ab21": ("outputs/songs/9a737871ab21_video.mp4", "outputs/songs/9a737871ab21_mr.mp3"),
    "9fa4fa3eb126": ("outputs/songs/9fa4fa3eb126_video.mp4", "outputs/songs/9fa4fa3eb126_mr.mp3"),
    "d6a5fc1af978": ("outputs/songs/d6a5fc1af978_video.mp4", "outputs/songs/d6a5fc1af978_mr.mp3"),
    "1469b71911c1": ("outputs/songs/1469b71911c1_video.mp4", "outputs/songs/1469b71911c1_mr.mp3"),
    "4fc75aa6c577": ("outputs/songs/4fc75aa6c577_video.mp4", "outputs/songs/4fc75aa6c577_mr.mp3"),
    "eed1b4615348": ("outputs/songs/eed1b4615348_video.mp4", "outputs/songs/eed1b4615348_mr.mp3"),
    "3fc17f9f6d71": ("outputs/songs/3fc17f9f6d71_video.mp4", "outputs/songs/3fc17f9f6d71_mr.mp3"),
    "b9993b89247c": ("outputs/songs/b9993b89247c_video.mp4", "outputs/songs/b9993b89247c_mr.mp3"),
    "25b5bae6437b": ("outputs/songs/25b5bae6437b_video.mp4", "outputs/songs/25b5bae6437b_mr.mp3"),
    "782a89291775": ("outputs/songs/782a89291775_video.mp4", "outputs/songs/782a89291775_mr.mp3"),
    "9efa53049d7c": ("outputs/songs/9efa53049d7c_video.mp4", "outputs/songs/9efa53049d7c_mr.mp3"),
    "87edff9650f4": ("outputs/songs/87edff9650f4_video.mp4", "outputs/songs/87edff9650f4_mr.mp3"),
    "9c500ae87613": ("outputs/songs/9c500ae87613_video.mp4", "outputs/songs/9c500ae87613_mr.mp3"),
    "f6dc92283a39": ("outputs/songs/f6dc92283a39_video.mp4", "outputs/songs/f6dc92283a39_mr.mp3"),
    "37616d619c75": ("outputs/songs/37616d619c75_video.mp4", "outputs/songs/37616d619c75_mr.mp3"),
    "7724d1cb5d98": ("outputs/songs/7724d1cb5d98_video.mp4", "outputs/songs/7724d1cb5d98_mr.mp3"),
    "ffb63f28cb35": ("outputs/songs/ffb63f28cb35_video.mp4", "outputs/songs/ffb63f28cb35_mr.mp3"),
    "04af64db9013": ("outputs/songs/04af64db9013_video.mp4", "outputs/songs/04af64db9013_mr.mp3"),
    "7d5b765b38a5": ("outputs/songs/7d5b765b38a5_video.mp4", "outputs/songs/7d5b765b38a5_mr.mp3"),
    "b5c1119f1f7e": ("outputs/songs/b5c1119f1f7e_video.mp4", "outputs/songs/b5c1119f1f7e_mr.mp3"),
    "35925074c274": ("outputs/songs/35925074c274_video.mp4", "outputs/songs/35925074c274_mr.mp3"),
    "61eb438447ab": ("outputs/songs/61eb438447ab_video.mp4", "outputs/songs/61eb438447ab_mr.mp3"),
    "d5060e50ff13": ("outputs/songs/d5060e50ff13_video.mp4", "outputs/songs/d5060e50ff13_mr.mp3"),
    "9561bc4d6b97": ("outputs/songs/9561bc4d6b97_video.mp4", "outputs/songs/9561bc4d6b97_mr.mp3"),
    "c4ba46db8146": ("outputs/songs/c4ba46db8146_video.mp4", "outputs/songs/c4ba46db8146_mr.mp3"),
    "8ab2cef0ba37": ("outputs/songs/8ab2cef0ba37_video.mp4", "outputs/songs/8ab2cef0ba37_mr.mp3"),
    "e9e09acd4e38": ("outputs/songs/e9e09acd4e38_video.mp4", "outputs/songs/e9e09acd4e38_mr.mp3"),
    "44fd69210d36": ("outputs/songs/44fd69210d36_video.mp4", "outputs/songs/44fd69210d36_mr.mp3"),
    "e245f5fc7028": ("outputs/songs/e245f5fc7028_video.mp4", "outputs/songs/e245f5fc7028_mr.mp3"),
    "5bf258fec71c": ("outputs/songs/5bf258fec71c_video.mp4", "outputs/songs/5bf258fec71c_mr.mp3"),
    "c9d672d5cc44": ("outputs/songs/c9d672d5cc44_video.mp4", "outputs/songs/c9d672d5cc44_mr.mp3"),
    "9c60304d2033": ("outputs/songs/9c60304d2033_video.mp4", "outputs/songs/9c60304d2033_mr.mp3"),
    "598bc4a65fca": ("outputs/songs/598bc4a65fca_video.mp4", "outputs/songs/598bc4a65fca_mr.mp3"),
    "5d777a28e563": ("outputs/songs/5d777a28e563_video.mp4", "outputs/songs/5d777a28e563_mr.mp3"),
    "6514d58a015d": ("outputs/songs/6514d58a015d_video.mp4", "outputs/songs/6514d58a015d_mr.mp3"),
    "267dd2c0b33a": ("outputs/songs/267dd2c0b33a_video.mp4", "outputs/songs/267dd2c0b33a_mr.mp3"),
    "a7b20dbefaef": ("outputs/songs/a7b20dbefaef_video.mp4", "outputs/songs/a7b20dbefaef_mr.mp3"),
    "fc707f5d0a1a": ("outputs/songs/fc707f5d0a1a_video.mp4", "outputs/songs/fc707f5d0a1a_mr.mp3"),
    "c50bede2d0d7": ("outputs/songs/c50bede2d0d7_video.mp4", "outputs/songs/c50bede2d0d7_mr.mp3"),
    "283a28c8e6f9": ("outputs/songs/283a28c8e6f9_video.mp4", "outputs/songs/283a28c8e6f9_mr.mp3"),
    "f1ec1c543e3f": ("outputs/songs/f1ec1c543e3f_video.mp4", "outputs/songs/f1ec1c543e3f_mr.mp3"),
    "c047c16de8fe": ("outputs/songs/c047c16de8fe_video.mp4", "outputs/songs/c047c16de8fe_mr.mp3"),
    "2141ae706009": ("outputs/songs/2141ae706009_video.mp4", "outputs/songs/2141ae706009_mr.mp3"),
    "d87fa64dedc2": ("outputs/songs/d87fa64dedc2_video.mp4", "outputs/songs/d87fa64dedc2_mr.mp3"),
    "9651bd68c11b": ("outputs/songs/9651bd68c11b_video.mp4", "outputs/songs/9651bd68c11b_mr.mp3"),
    "0d8faff94f04": ("outputs/songs/0d8faff94f04_video.mp4", "outputs/songs/0d8faff94f04_mr.mp3"),
    "ce71d2d0108b": ("outputs/songs/ce71d2d0108b_video.mp4", "outputs/songs/ce71d2d0108b_mr.mp3"),
    "26c1a830c4a6": ("outputs/songs/26c1a830c4a6_video.mp4", "outputs/songs/26c1a830c4a6_mr.mp3"),
    "c6b0ba796b63": ("outputs/songs/c6b0ba796b63_video.mp4", "outputs/songs/c6b0ba796b63_mr.mp3"),
    "c5732826cbbd": ("outputs/songs/c5732826cbbd_video.mp4", "outputs/songs/c5732826cbbd_mr.mp3"),
    "8c678fac3643": ("outputs/songs/8c678fac3643_video.mp4", "outputs/songs/8c678fac3643_mr.mp3"),
    "77b95df24f19": ("outputs/songs/77b95df24f19_video.mp4", "outputs/songs/77b95df24f19_mr.mp3"),
    "7d87c0e695eb": ("outputs/songs/7d87c0e695eb_video.mp4", "outputs/songs/7d87c0e695eb_mr.mp3"),
    "708589fa9475": ("outputs/songs/708589fa9475_video.mp4", "outputs/songs/708589fa9475_mr.mp3"),
    "78e9eb8c5092": ("outputs/songs/78e9eb8c5092_video.mp4", "outputs/songs/78e9eb8c5092_mr.mp3"),
    "e375c22d9dd1": ("outputs/songs/e375c22d9dd1_video.mp4", "outputs/songs/e375c22d9dd1_mr.mp3"),
    "f3c1be4ad73f": ("outputs/songs/f3c1be4ad73f_video.mp4", "outputs/songs/f3c1be4ad73f_mr.mp3"),
    "ea3a867bcb81": ("outputs/songs/ea3a867bcb81_video.mp4", "outputs/songs/ea3a867bcb81_mr.mp3"),
    "fac16c0aada1": ("outputs/songs/fac16c0aada1_video.mp4", "outputs/songs/fac16c0aada1_mr.mp3"),
    "e6892c8c3319": ("outputs/songs/e6892c8c3319_video.mp4", "outputs/songs/e6892c8c3319_mr.mp3"),
    "f47f7cef8799": ("outputs/songs/f47f7cef8799_video.mp4", "outputs/songs/f47f7cef8799_mr.mp3"),
    "c4b48d8deda5": ("outputs/songs/c4b48d8deda5_video.mp4", "outputs/songs/c4b48d8deda5_mr.mp3"),
    "39923df63416": ("outputs/songs/39923df63416_video.mp4", "outputs/songs/39923df63416_mr.mp3"),
    "d952d29b895f": ("outputs/songs/d952d29b895f_video.mp4", "outputs/songs/d952d29b895f_mr.mp3"),
    "6b46583e9b90": ("outputs/songs/6b46583e9b90_video.mp4", "outputs/songs/6b46583e9b90_mr.mp3"),
    "acddabe5164e": ("outputs/songs/acddabe5164e_video.mp4", "outputs/songs/acddabe5164e_mr.mp3"),
    "27b10190fb65": ("outputs/songs/27b10190fb65_video.mp4", "outputs/songs/27b10190fb65_mr.mp3"),
    "2c36555328d0": ("outputs/songs/2c36555328d0_video.mp4", "outputs/songs/2c36555328d0_mr.mp3"),
    "582e5d5d8ce7": ("outputs/songs/582e5d5d8ce7_video.mp4", "outputs/songs/582e5d5d8ce7_mr.mp3"),
    "5acf85297ecc": ("outputs/songs/5acf85297ecc_video.mp4", "outputs/songs/5acf85297ecc_mr.mp3"),
    "a032d4d322be": ("outputs/songs/a032d4d322be_video.mp4", "outputs/songs/a032d4d322be_mr.mp3"),
    "fbbbd206a438": ("outputs/songs/fbbbd206a438_video.mp4", "outputs/songs/fbbbd206a438_mr.mp3"),
    "98aa6f5b565e": ("outputs/songs/98aa6f5b565e_video.mp4", "outputs/songs/98aa6f5b565e_mr.mp3"),
    "315ea7d8fdb9": ("outputs/songs/315ea7d8fdb9_video.mp4", "outputs/songs/315ea7d8fdb9_mr.mp3"),
    "2dd4d08ab8a6": ("outputs/songs/2dd4d08ab8a6_video.mp4", "outputs/songs/2dd4d08ab8a6_mr.mp3"),
    "7a3e7dd3b089": ("outputs/songs/7a3e7dd3b089_video.mp4", "outputs/songs/7a3e7dd3b089_mr.mp3"),
    "334e0d01df2c": ("outputs/songs/334e0d01df2c_video.mp4", "outputs/songs/334e0d01df2c_mr.mp3"),
    "dc8575a37d57": ("outputs/songs/dc8575a37d57_video.mp4", "outputs/songs/dc8575a37d57_mr.mp3"),
    "acfb1faf5907": ("outputs/songs/acfb1faf5907_video.mp4", "outputs/songs/acfb1faf5907_mr.mp3"),
    "c27bdc06916f": ("outputs/songs/c27bdc06916f_video.mp4", "outputs/songs/c27bdc06916f_mr.mp3"),
    "83346e77bd06": ("outputs/songs/83346e77bd06_video.mp4", "outputs/songs/83346e77bd06_mr.mp3"),
    "4bae9e47f825": ("outputs/songs/4bae9e47f825_video.mp4", "outputs/songs/4bae9e47f825_mr.mp3"),
    "f84109273801": ("outputs/songs/f84109273801_video.mp4", "outputs/songs/f84109273801_mr.mp3"),
    "63fa0b5af8c9": ("outputs/songs/63fa0b5af8c9_video.mp4", "outputs/songs/63fa0b5af8c9_mr.mp3"),
    "2029f291fa3d": ("outputs/songs/2029f291fa3d_video.mp4", "outputs/songs/2029f291fa3d_mr.mp3"),
    "07065e79dbd8": ("outputs/songs/07065e79dbd8_video.mp4", "outputs/songs/07065e79dbd8_mr.mp3"),
    "ef5c64ef42c1": ("outputs/songs/ef5c64ef42c1_video.mp4", "outputs/songs/ef5c64ef42c1_mr.mp3"),
    "84f7f9baaa16": ("outputs/songs/84f7f9baaa16_video.mp4", "outputs/songs/84f7f9baaa16_mr.mp3"),
    "e04cda9306a0": ("outputs/songs/e04cda9306a0_video.mp4", "outputs/songs/e04cda9306a0_mr.mp3"),
    "e31be57f1793": ("outputs/songs/e31be57f1793_video.mp4", "outputs/songs/e31be57f1793_mr.mp3"),
    "aa8e05c80060": ("outputs/songs/aa8e05c80060_video.mp4", "outputs/songs/aa8e05c80060_mr.mp3"),
    "660af2739e9c": ("outputs/songs/660af2739e9c_video.mp4", "outputs/songs/660af2739e9c_mr.mp3"),
    "7445f910835f": ("outputs/songs/7445f910835f_video.mp4", "outputs/songs/7445f910835f_mr.mp3"),
    "5c4510ea5adf": ("outputs/songs/5c4510ea5adf_video.mp4", "outputs/songs/5c4510ea5adf_mr.mp3"),
    "d6e615783ed1": ("outputs/songs/d6e615783ed1_video.mp4", "outputs/songs/d6e615783ed1_mr.mp3"),
    "bd540e7e99d8": ("outputs/songs/bd540e7e99d8_video.mp4", "outputs/songs/bd540e7e99d8_mr.mp3"),
    "994d08670357": ("outputs/songs/994d08670357_video.mp4", "outputs/songs/994d08670357_mr.mp3"),
    "61fce2c6714b": ("outputs/songs/61fce2c6714b_video.mp4", "outputs/songs/61fce2c6714b_mr.mp3"),
    "c39b519fc5eb": ("outputs/songs/c39b519fc5eb_video.mp4", "outputs/songs/c39b519fc5eb_mr.mp3"),
    "ec482d9993e2": ("outputs/songs/ec482d9993e2_video.mp4", "outputs/songs/ec482d9993e2_mr.mp3"),
    "20aa2101da7e": ("outputs/songs/20aa2101da7e_video.mp4", "outputs/songs/20aa2101da7e_mr.mp3"),
    "2a7b43858839": ("outputs/songs/2a7b43858839_video.mp4", "outputs/songs/2a7b43858839_mr.mp3"),
    "b26035fa3144": ("outputs/songs/b26035fa3144_video.mp4", "outputs/songs/b26035fa3144_mr.mp3"),
    "11b7b8cad6d1": ("outputs/songs/11b7b8cad6d1_video.mp4", "outputs/songs/11b7b8cad6d1_mr.mp3"),
    "b3e2b8beebe0": ("outputs/songs/b3e2b8beebe0_video.mp4", "outputs/songs/b3e2b8beebe0_mr.mp3"),
    "f4cb7fbe6b37": ("outputs/songs/f4cb7fbe6b37_video.mp4", "outputs/songs/f4cb7fbe6b37_mr.mp3"),
    "497871d7f223": ("outputs/songs/497871d7f223_video.mp4", "outputs/songs/497871d7f223_mr.mp3"),
    "de3e0507a9cb": ("outputs/songs/de3e0507a9cb_video.mp4", "outputs/songs/de3e0507a9cb_mr.mp3"),
    "9957c57b9335": ("outputs/songs/9957c57b9335_video.mp4", "outputs/songs/9957c57b9335_mr.mp3"),
    "f085dcb77296": ("outputs/songs/f085dcb77296_video.mp4", "outputs/songs/f085dcb77296_mr.mp3"),
    "32b2d5bd42b0": ("outputs/songs/32b2d5bd42b0_video.mp4", "outputs/songs/32b2d5bd42b0_mr.mp3"),
    "1fbead5a58cf": ("outputs/songs/1fbead5a58cf_video.mp4", "outputs/songs/1fbead5a58cf_mr.mp3"),
    "3f986379ab85": ("outputs/songs/3f986379ab85_video.mp4", "outputs/songs/3f986379ab85_mr.mp3"),
    "c2cdf7d28776": ("outputs/songs/c2cdf7d28776_video.mp4", "outputs/songs/c2cdf7d28776_mr.mp3"),
    "084086e3496f": ("outputs/songs/084086e3496f_video.mp4", "outputs/songs/084086e3496f_mr.mp3"),
    "c6033f5d474e": ("outputs/songs/c6033f5d474e_video.mp4", "outputs/songs/c6033f5d474e_mr.mp3"),
    "2f604b4e561e": ("outputs/songs/2f604b4e561e_video.mp4", "outputs/songs/2f604b4e561e_mr.mp3"),
    "84e118bd1b10": ("outputs/songs/84e118bd1b10_video.mp4", "outputs/songs/84e118bd1b10_mr.mp3"),
    "ecdf1ccd9d92": ("outputs/songs/ecdf1ccd9d92_video.mp4", "outputs/songs/ecdf1ccd9d92_mr.mp3"),
    "592313013e45": ("outputs/songs/592313013e45_video.mp4", "outputs/songs/592313013e45_mr.mp3"),
    "7d1ff6eda0bc": ("outputs/songs/7d1ff6eda0bc_video.mp4", "outputs/songs/7d1ff6eda0bc_mr.mp3"),
    "9be336df42e2": ("outputs/songs/9be336df42e2_video.mp4", "outputs/songs/9be336df42e2_mr.mp3"),
    "ffc34e30aedc": ("outputs/songs/ffc34e30aedc_video.mp4", "outputs/songs/ffc34e30aedc_mr.mp3"),
    "2bd74ab9ab02": ("outputs/songs/2bd74ab9ab02_video.mp4", "outputs/songs/2bd74ab9ab02_mr.mp3"),
    "9d2b21400222": ("outputs/songs/9d2b21400222_video.mp4", "outputs/songs/9d2b21400222_mr.mp3"),
    "759f58f5f3eb": ("outputs/songs/759f58f5f3eb_video.mp4", "outputs/songs/759f58f5f3eb_mr.mp3"),
    "6cd4d61e5699": ("outputs/songs/6cd4d61e5699_video.mp4", "outputs/songs/6cd4d61e5699_mr.mp3"),
    "7fc0a63bb28c": ("outputs/songs/7fc0a63bb28c_video.mp4", "outputs/songs/7fc0a63bb28c_mr.mp3"),
    "c3ae59bc882f": ("outputs/songs/c3ae59bc882f_video.mp4", "outputs/songs/c3ae59bc882f_mr.mp3"),
    # AUTO_BIG_BATCH_END
}

@app.post("/api/song/my-record")
async def upload_my_song_recording(
    song_id: str = Form(...),
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    """사용자 보컬 녹음 + 곡 영상 + MR → 합쳐서 mp4 생성 (가족 공유용)"""
    if song_id not in _SONG_VIDEO_MR:
        raise HTTPException(404, f"알 수 없는 곡: {song_id}")
    video_path, mr_path = _SONG_VIDEO_MR[song_id]
    if not (os.path.exists(video_path) and os.path.exists(mr_path)):
        raise HTTPException(500, "곡 자산 없음")

    out_dir = OUTPUTS_DIR / "my_songs" / str(user["id"])
    out_dir.mkdir(parents=True, exist_ok=True)
    file_id = uuid.uuid4().hex[:8]
    raw_path = out_dir / f"{file_id}_voice.webm"
    out_mp4 = out_dir / f"{song_id}_{file_id}.mp4"

    async with aiofiles.open(raw_path, "wb") as f:
        await f.write(await file.read())

    # ffmpeg: 영상 + MR 0.6 + 본인 보컬 1.0 mix → mp4
    proc = subprocess.run([
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", str(mr_path),
        "-i", str(raw_path),
        "-filter_complex",
        "[1:a]volume=0.55[mr];[2:a]volume=1.4,aresample=44100[voc];[mr][voc]amix=inputs=2:duration=longest:dropout_transition=0[a]",
        "-map", "0:v:0", "-map", "[a]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(out_mp4),
    ], capture_output=True)
    raw_path.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise HTTPException(500, f"ffmpeg 실패: {proc.stderr.decode()[-400:]}")

    # 길이
    try:
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                     "-of", "default=nw=1:nk=1", str(out_mp4)],
                                    capture_output=True, text=True).stdout.strip() or 0)
    except Exception:
        dur = 0
    return {
        "success": True,
        "video_url": f"/outputs/my_songs/{user['id']}/{out_mp4.name}",
        "duration": dur,
        "size": out_mp4.stat().st_size,
    }


@app.get("/api/song/my-records")
async def list_my_records(user: dict = Depends(get_current_user)):
    """현재 사용자의 녹음 mp4 목록"""
    user_dir = OUTPUTS_DIR / "my_songs" / str(user["id"])
    if not user_dir.exists():
        return {"records": []}
    items = []
    for p in sorted(user_dir.glob("*.mp4"), key=lambda x: x.stat().st_mtime, reverse=True):
        # 파일명: {song_id}_{file_id}.mp4
        stem = p.stem
        song_id = stem.rsplit("_", 1)[0] if "_" in stem else stem
        items.append({
            "filename": p.name,
            "song_id": song_id,
            "url": f"/outputs/my_songs/{user['id']}/{p.name}",
            "size": p.stat().st_size,
            "mtime": p.stat().st_mtime,
        })
    return {"records": items}


@app.delete("/api/song/my-records/{filename}")
async def delete_my_record(filename: str, user: dict = Depends(get_current_user)):
    """녹음 mp4 삭제"""
    if "/" in filename or ".." in filename:
        raise HTTPException(400, "잘못된 파일명")
    p = OUTPUTS_DIR / "my_songs" / str(user["id"]) / filename
    if p.exists() and p.suffix == ".mp4":
        p.unlink()
        return {"success": True}
    raise HTTPException(404, "파일 없음")


@app.get("/api/song/dl/{kind}")
async def download_song(kind: str):
    """가족용 강제 다운로드 (Content-Disposition attachment)"""
    if kind not in _SONG_DL_FILES:
        raise HTTPException(404, f"알 수 없는 종류: {kind}")
    path, mime, name = _SONG_DL_FILES[kind]
    if not os.path.exists(path):
        raise HTTPException(404, "파일 없음")
    return _FileResp(path, media_type=mime, filename=name)


# ─── 노래/악보 PDF 업로드 ───────────────────────────────────
@app.post("/api/song/pdf-upload")
async def upload_song_pdf(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    """피아노 악보 PDF 업로드 — 정적 서빙 후 OMR 변환 대기"""
    fname_lower = (file.filename or "").lower()
    if not fname_lower.endswith(".pdf"):
        raise HTTPException(400, "PDF 파일만 가능합니다")
    upload_dir = OUTPUTS_DIR / "songs" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_id = uuid.uuid4().hex[:8]
    safe_stem = Path(file.filename).stem.replace(" ", "_")[:40]
    save_path = upload_dir / f"{file_id}_{safe_stem}.pdf"
    async with aiofiles.open(save_path, "wb") as f:
        content = await file.read()
        await f.write(content)
    return {
        "success": True,
        "file_id": file_id,
        "filename": file.filename,
        "size": len(content),
        "saved": str(save_path),
        "url": f"/outputs/songs/uploads/{file_id}_{safe_stem}.pdf",
        "message": "PDF 업로드 완료 — Audiveris OMR 변환은 다음 단계 (현재는 미리보기만)",
    }


_finetuned_cache = {}  # model_dir -> (model, config, mtime)

def _get_or_load_finetuned(model_dir, meta):
    """파인튜닝 모델 캐싱 — 같은 model_dir 재호출 시 재로드 안 함."""
    model_path = meta["model_path"]
    mtime = os.path.getmtime(model_path) if os.path.exists(model_path) else 0
    cached = _finetuned_cache.get(model_dir)
    if cached and cached[2] == mtime:
        return cached[0], cached[1]
    model, config = load_finetuned_model(
        meta["model_path"], meta["config_path"], meta["vocab_path"]
    )
    _finetuned_cache[model_dir] = (model, config, mtime)
    return model, config

@app.post("/api/tts/generate-finetuned")
async def generate_tts_finetuned(
    model_dir: str = Form(...),
    text: str = Form(...),
):
    """파인튜닝된 모델로 TTS 생성 (모델 캐싱 적용)"""
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
            model, config = _get_or_load_finetuned(model_dir, meta)
            ref_wav = meta.get("better_speaker_ref") or meta["speaker_ref"]
            output = model.synthesize(
                normalized,
                config,
                speaker_wav=ref_wav,
                language="ko",
                temperature=0.75,
                repetition_penalty=5.0,
                top_p=0.9,
                top_k=50,
                length_penalty=1.0,
            )
            import soundfile
            soundfile.write(audio_path, output["wav"], 24000)

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
    return FileResponse("frontend/chinese.html", headers=_NOCACHE)


@app.get("/japanese")
async def japanese_page():
    """일본어 발음 학습 페이지"""
    return FileResponse("frontend/japanese.html", headers=_NOCACHE)


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
                gpu_idx = int(os.environ.get("STT_GPU", "0"))
                return WhisperModel("large-v3", device="cuda", device_index=gpu_idx, compute_type="float16")

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

    # ⚠️ 이 엔드포인트는 textbook 카드를 전부 지운다. 실수 클릭 대비 자동 백업.
    #    (2026-08-29 실제로 11,000장이 날아간 적 있음 — 음성파일은 남아 복구 가능했다)
    import shutil as _shutil
    _bak = f"{DB_PATH}.autobak.import_folder.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    try:
        _shutil.copy2(DB_PATH, _bak)
        print(f"[import-folder] 삭제 전 DB 자동 백업 → {_bak}")
    except Exception as _e:
        raise HTTPException(500, f"백업 실패로 중단했습니다: {_e}")

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM chinese_cards WHERE user_id = ? AND category = 'textbook'", (user_id,))
        _n = (await cur.fetchone())[0]
        print(f"[import-folder] 기존 textbook 카드 {_n}장 삭제")
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


# ══════════════════════════════════════════════════════════════
# 단어 학습 시스템 (words / word_progress / study_log)
# ══════════════════════════════════════════════════════════════
_SRS_INTERVALS = [0, 1, 2, 4, 8, 16, 32, 64]   # 박스별 복습 간격(일)

def _wdb():
    import sqlite3 as _s
    from database.db import DB_PATH as _p
    c = _s.connect(_p, timeout=60)
    c.execute("PRAGMA busy_timeout=60000")
    c.row_factory = _s.Row
    return c


_NOCACHE = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache", "Expires": "0"}


@app.get("/words")
async def words_page():
    """단어 학습 PWA 페이지"""
    return FileResponse("frontend/words.html", headers=_NOCACHE)


@app.get("/flash")
async def flash_page():
    """100단어 넘겨보기 — 문제를 풀지 않고 단어·뜻을 빠르게 훑는 페이지"""
    return FileResponse("frontend/flash.html", headers=_NOCACHE)


@app.get("/liangci")
async def liangci_page():
    """양사 문제 풀기 — 「옷은 어느 양사로 세나」를 묻는다"""
    return FileResponse("frontend/liangci.html", headers=_NOCACHE)


@app.get("/api/words/cl-quiz")
async def cl_quiz(n: int = 20, known: int = 1, hsk: int = 0):
    """양사 문제를 만들어 준다.
       CC-CEDICT 의 CL: 표시에서 '무엇을 어느 양사로 세는가'를 뽑아 둔 표를 쓴다.
       ⚠️ 오답 후보는 아무 양사나 고르면 안 된다 — 세는 결이 비슷한 것끼리 섞어야
          문제가 된다(个 처럼 아무 데나 쓰이는 것을 늘 끼워 넣으면 답이 뻔해진다)."""
    import random
    c = _wdb()
    try:
        ours = [r["chinese"] for r in c.execute(
            "SELECT chinese FROM words WHERE wordset='양사' AND COALESCE(excluded,0)=0")]
        q = """SELECT classifier, noun, pinyin, meaning_ko, meaning_en, hsk
               FROM classifier_nouns WHERE classifier IN (%s)""" % ",".join("?" * len(ours))
        args = list(ours)
        if known:
            q += " AND known=1"
        if hsk:
            q += " AND hsk<=? AND hsk>0"; args.append(hsk)
        rows = [dict(r) for r in c.execute(q, args)]
        # 양사마다 몇 낱말을 세는지 — 흔한 양사일수록 오답으로 그럴듯하다
        cnt = {}
        for r in rows:
            cnt[r["classifier"]] = cnt.get(r["classifier"], 0) + 1
        pool = sorted(cnt, key=lambda x: -cnt[x])
        mean = {r["chinese"]: (r["pinyin"], r["meaning_ko"]) for r in c.execute(
            "SELECT chinese,pinyin,meaning_ko FROM words WHERE wordset='양사' AND COALESCE(excluded,0)=0")}
    finally:
        c.close()
    if not rows:
        return {"questions": []}
    random.shuffle(rows)
    out, used = [], set()
    for r in rows:
        if r["noun"] in used:
            continue
        used.add(r["noun"])
        wrong = [x for x in pool if x != r["classifier"]]
        # 흔한 것 위주로 뽑되 늘 같은 얼굴이 나오지 않게 조금 넓게 섞는다
        wrong = random.sample(wrong[:24], 3) if len(wrong) >= 24 else random.sample(wrong, min(3, len(wrong)))
        opts = wrong + [r["classifier"]]
        random.shuffle(opts)
        out.append({
            "noun": r["noun"], "pinyin": r["pinyin"],
            "meaning": r["meaning_ko"] or r["meaning_en"] or "",
            "hsk": r["hsk"] or 0,
            "answer": r["classifier"],
            "options": [{"c": o, "pinyin": mean.get(o, ("", ""))[0],
                         "ko": mean.get(o, ("", ""))[1]} for o in opts],
        })
        if len(out) >= max(1, min(n, 100)):
            break
    return {"questions": out, "total": len(rows)}


# ─── 성공하는스피치커뮤니케이션 ──────────────────────────
# 교안은 기존 교안 뷰어(/api/chinese/textbook/*)를 그대로 쓴다.
# 여기 있는 것은 과제(보이스 자가진단) — 1분 녹음 + 자가진단표 + 종합소감.

SPEECH_SUBJ = "성공하는스피치커뮤니케이션"
SPEECH_DIR = OUTPUTS_DIR / "speech"


def _speech_db():
    c = _wdb()
    c.execute("""CREATE TABLE IF NOT EXISTS speech_takes (
        user_id INTEGER NOT NULL, take INTEGER NOT NULL,
        path TEXT, sec REAL, created_at TEXT,
        PRIMARY KEY (user_id, take))""")
    c.execute("""CREATE TABLE IF NOT EXISTS speech_answers (
        user_id INTEGER PRIMARY KEY, data TEXT, updated_at TEXT)""")
    return c


@app.get("/speech")
async def speech_page():
    """스피치 과목 — 교안보기와 과제(보이스 자가진단)"""
    return FileResponse("frontend/speech.html", headers=_NOCACHE)


# 녹음할 지문 — 교안에서 옮겨 왔다.
# 과제 서식(pptx)에는 「1분 보이스 녹음」이라고만 되어 있고 읽을 글이 없어,
# 교안의 딕션 훈련 문장(2주2교시 66쪽)과 감성 낭독 지문(2주3교시 80쪽)을 실어 둔다.
# 80쪽은 그림이라 글자를 떠서(OCR) 옮겼다.
SPEECH_SCRIPTS = [
    {
        "id": "diction1",
        "title": "딕션 훈련 ①",
        "from": "교안 2주 2교시 · 66쪽",
        "hint": "천천히 또박또박부터. 입을 평소의 두 배로 벌려 과장해 읽어 보세요.",
        "text": "정동진역 앞 정동진 순두부찌개집의 주방장은\n"
                "찹쌀떡과 들깨칼국수의 황금 배합 특허를 취득했다.",
    },
    {
        "id": "diction2",
        "title": "딕션 훈련 ②",
        "from": "교안 2주 2교시 · 66쪽",
        "hint": "치조음(ㄴ·ㄷ·ㅅ)에서 혀끝이 윗잇몸에 닿는지 살펴 가며 읽으세요.",
        "text": "서울특별시 특허허가과 허가과장의\n"
                "특허 허가 고시문 양식은 매우 정교하게 조율되어 있다.",
    },
    {
        "id": "reading",
        "title": "감성 낭독 — 폴 발레리 「해변의 묘지」",
        "from": "교안 2주 3교시 · 80쪽",
        "hint": "장면을 머릿속에 그리며(마인드 컬러링), 핵심어 앞뒤에 멈춤을 두어 읽으세요.",
        "text": "바람이 분다!… 살아야겠다!\n"
                "드넓은 대기가 내 책을 열었다 닫고,\n"
                "물보라가 된 파도가 바위에서 솟쳐 오른다!\n"
                "날아올라라, 눈부신 책장들이여!\n"
                "부수어라, 파도여! 환희의 물결로 부수어라,\n"
                "삼각돛들이 쪼아대던 저 고요한 지붕을!",
    },
]


@app.get("/api/speech/scripts")
async def speech_scripts(user: dict = Depends(get_current_user)):
    """녹음할 지문 목록"""
    return {"scripts": SPEECH_SCRIPTS}


@app.get("/api/speech/state")
async def speech_state(user: dict = Depends(get_current_user)):
    c = _speech_db()
    try:
        takes = [dict(r) for r in c.execute(
            "SELECT take, sec, created_at FROM speech_takes WHERE user_id=? ORDER BY take",
            (user["id"],))]
        r = c.execute("SELECT data FROM speech_answers WHERE user_id=?", (user["id"],)).fetchone()
    finally:
        c.close()
    return {"takes": takes, "answers": json.loads(r["data"]) if r and r["data"] else {}}


@app.post("/api/speech/record")
async def speech_record(take: int = Form(...), sec: float = Form(0),
                        audio: UploadFile = File(...),
                        user: dict = Depends(get_current_user)):
    """녹음 한 벌을 받는다. 같은 자리에 다시 녹음하면 덮어쓴다."""
    if take not in (1, 2, 3):
        raise HTTPException(400, "녹음 자리는 1~3")
    d = SPEECH_DIR / str(user["id"])
    d.mkdir(parents=True, exist_ok=True)
    ext = ".webm" if "webm" in (audio.content_type or "") else ".ogg"
    path = d / f"take{take}{ext}"
    for old in d.glob(f"take{take}.*"):        # 갈아 끼울 때 옛 확장자가 남지 않게
        old.unlink(missing_ok=True)
    with open(path, "wb") as f:
        f.write(await audio.read())
    c = _speech_db()
    try:
        c.execute("""INSERT INTO speech_takes(user_id,take,path,sec,created_at)
                     VALUES(?,?,?,?,?)
                     ON CONFLICT(user_id,take) DO UPDATE SET
                       path=excluded.path, sec=excluded.sec, created_at=excluded.created_at""",
                  (user["id"], take, str(path), round(sec, 1),
                   datetime.now().strftime("%Y-%m-%d %H:%M")))
        c.commit()
    finally:
        c.close()
    return {"ok": True, "take": take, "sec": round(sec, 1)}


@app.get("/api/speech/audio/{take}")
async def speech_audio(take: int, user: dict = Depends(get_current_user)):
    c = _speech_db()
    try:
        r = c.execute("SELECT path FROM speech_takes WHERE user_id=? AND take=?",
                      (user["id"], take)).fetchone()
    finally:
        c.close()
    if not r or not os.path.exists(r["path"]):
        raise HTTPException(404, "녹음이 없습니다")
    return FileResponse(r["path"], headers=_NOCACHE)


@app.delete("/api/speech/audio/{take}")
async def speech_audio_del(take: int, user: dict = Depends(get_current_user)):
    c = _speech_db()
    try:
        r = c.execute("SELECT path FROM speech_takes WHERE user_id=? AND take=?",
                      (user["id"], take)).fetchone()
        if r and r["path"] and os.path.exists(r["path"]):
            os.remove(r["path"])
        c.execute("DELETE FROM speech_takes WHERE user_id=? AND take=?", (user["id"], take))
        c.commit()
    finally:
        c.close()
    return {"ok": True}


@app.post("/api/speech/answers")
async def speech_answers(payload: dict, user: dict = Depends(get_current_user)):
    """자가진단 체크와 종합소감을 담아 둔다 (그때그때 저장)."""
    c = _speech_db()
    try:
        c.execute("""INSERT INTO speech_answers(user_id,data,updated_at) VALUES(?,?,?)
                     ON CONFLICT(user_id) DO UPDATE SET
                       data=excluded.data, updated_at=excluded.updated_at""",
                  (user["id"], json.dumps(payload, ensure_ascii=False),
                   datetime.now().strftime("%Y-%m-%d %H:%M")))
        c.commit()
    finally:
        c.close()
    return {"ok": True}


@app.get("/api/words/audio")
async def words_audio(w: str = "", voice: int = 2, user: dict = Depends(get_current_user)):
    """이미 만들어 둔 낱말 음성을 그대로 내어 준다.
    이게 없으면 화면이 누를 때마다 XTTS 로 새로 합성해 몇 초씩 기다리게 된다."""
    w = (w or "").strip()
    if not w:
        raise HTTPException(400, "낱말이 없습니다")
    c = _wdb()
    try:
        r = c.execute("""SELECT audio1,audio2 FROM words
                         WHERE chinese=? AND COALESCE(excluded,0)=0 LIMIT 1""", (w,)).fetchone()
    finally:
        c.close()
    if not r:
        raise HTTPException(404, "그 낱말이 없습니다")
    order = ("audio1", "audio2") if voice == 1 else ("audio2", "audio1")
    for col in order:
        p = r[col]
        if p and os.path.exists(p):
            return FileResponse(p, headers={"Cache-Control": "public, max-age=604800"})
    raise HTTPException(404, "음성이 없습니다")


@app.get("/api/words/deck")
async def words_deck(request: Request, offset: int = 0, limit: int = 100,
                     level: int = 0, hsk: int = 0, cat: str = "", order: str = "seq",
                     only: str = "", subject: str = "", week: int = 0, cls: int = 0,
                     course: int = 0, wordset: str = ""):
    """넘겨보기용 묶음. 문제가 아니라 '단어+뜻'을 그대로 준다.
    order  seq=쉬운 순 / random=무작위 / due=복습 예정 먼저
    only   new=아직 안 배운 것만 / wrong=틀린 적 있는 것만 / star=별표만"""
    uid = request.state.user_id
    limit = max(1, min(200, limit))
    c = _wdb()
    try:
        cond = ["w.pinyin!=''", "w.meaning_ko!=''", "COALESCE(w.excluded,0)=0"]
        args = []
        if cat in ("단어", "구문", "성어"):
            cond.append("w.category=?"); args.append(cat)
        if hsk:
            cond.append("(w.hsk>0 AND w.hsk<=?)"); args.append(hsk)
        if course:                       # 교재가 정한 4급/5급 어휘
            cond.append("w.hsk_course=?"); args.append(course)
        if wordset:                      # 따로 모은 묶음 (양사·접속사·의성의태)
            cond.append("w.wordset=?"); args.append(wordset)
        if level:
            cond.append("w.level=?"); args.append(level)
        # 한 단어가 여러 교재에 나올 수 있어 교재·단원은 word_units 에서 본다
        if subject or week or cls:
            uc, ua = ["u.word_id=w.id"], []
            if subject: uc.append("u.subject=?");    ua.append(subject)
            if week:    uc.append("u.unit_week=?");  ua.append(week)
            if cls:     uc.append("u.unit_class=?"); ua.append(cls)
            cond.append("EXISTS (SELECT 1 FROM word_units u WHERE " + " AND ".join(uc) + ")")
            args += ua
        if only == "new":
            cond.append("(p.seen IS NULL OR p.seen=0)")
        elif only == "wrong":
            cond.append("p.wrong>0")
        elif only == "star":
            cond.append("p.starred=1")
        where = " AND ".join(cond)
        od = {"random": "RANDOM()",
              "due": "COALESCE(p.due_at,'9999') , w.seq",
              "unit": ("COALESCE((SELECT MIN(u2.unit_week*10+COALESCE(u2.unit_class,0))"
                       " FROM word_units u2 WHERE u2.word_id=w.id"
                       + (" AND u2.subject=?" if subject else "") + "),999), w.seq")
              }.get(order, "w.seq")
        rows = c.execute(f"""SELECT w.*, p.box, p.seen, p.correct, p.wrong, p.starred, p.due_at
            FROM words w LEFT JOIN word_progress p ON p.word_id=w.id AND p.user_id=?
            WHERE {where} ORDER BY {od} LIMIT ? OFFSET ?""",
            [uid] + args + ([subject] if (order == "unit" and subject) else [])
            + [limit, offset]).fetchall()
        # 예문을 한 번에 실어 보낸다 (카드마다 따로 부르지 않도록)
        ex = {}
        if rows:
            ids = [r["id"] for r in rows]
            q = ",".join("?" * len(ids))
            for e in c.execute(f"""SELECT word_id,chinese,pinyin,meaning_ko,audio1,audio2
                                   FROM word_examples WHERE word_id IN ({q})
                                   ORDER BY word_id,seq,id""", ids):
                ex.setdefault(e["word_id"], []).append(
                    {"chinese": e["chinese"], "pinyin": e["pinyin"], "ko": e["meaning_ko"] or "",
                     "a1": ("/" + e["audio1"].lstrip("/")) if e["audio1"] else "",
                     "a2": ("/" + e["audio2"].lstrip("/")) if e["audio2"] else ""})
        total = c.execute(f"""SELECT COUNT(*) FROM words w
            LEFT JOIN word_progress p ON p.word_id=w.id AND p.user_id=?
            WHERE {where}""", [uid] + args).fetchone()[0]
        out = []
        for r in rows:
            d = dict(r)
            d["examples"] = ex.get(r["id"], [])
            out.append(d)
        return {"offset": offset, "total": total, "count": len(rows), "words": out}
    finally:
        c.close()


@app.get("/api/words/examples")
async def word_examples(request: Request, word_id: int = 0, chinese: str = ""):
    """단어의 예문 — 병음·뜻·2인 음성까지 함께 준다.
    양사처럼 '무엇을 세는가'가 핵심인 말은 예문이 있어야 뜻이 잡힌다."""
    c = _wdb()
    try:
        if not word_id and chinese:
            r = c.execute("SELECT id FROM words WHERE chinese=? AND COALESCE(excluded,0)=0",
                          (chinese,)).fetchone()
            word_id = r["id"] if r else 0
        if not word_id:
            return {"examples": []}
        rows = c.execute("""SELECT id,chinese,pinyin,meaning_ko,audio1,audio2
                            FROM word_examples WHERE word_id=? ORDER BY seq,id""",
                         (word_id,)).fetchall()
    except Exception:
        return {"examples": []}
    finally:
        c.close()
    def url(p):
        return ("/" + p.lstrip("/")) if p else ""
    return {"examples": [{"id": r["id"], "chinese": r["chinese"], "pinyin": r["pinyin"],
                          "ko": r["meaning_ko"] or "",
                          "a1": url(r["audio1"]), "a2": url(r["audio2"])} for r in rows]}


@app.get("/api/words/stats")
async def words_stats(request: Request):
    uid = request.state.user_id
    c = _wdb()
    try:
        total = c.execute("SELECT COUNT(*) FROM words WHERE COALESCE(excluded,0)=0").fetchone()[0]
        seen = c.execute("SELECT COUNT(*) FROM word_progress WHERE user_id=? AND seen>0", (uid,)).fetchone()[0]
        mastered = c.execute("SELECT COUNT(*) FROM word_progress WHERE user_id=? AND box>=5", (uid,)).fetchone()[0]
        due = c.execute("""SELECT COUNT(*) FROM word_progress
                           WHERE user_id=? AND due_at IS NOT NULL AND due_at<=datetime('now')""", (uid,)).fetchone()[0]
        today = c.execute("""SELECT COUNT(*), COALESCE(SUM(result),0) FROM study_log
                             WHERE user_id=? AND date(created_at)=date('now')""", (uid,)).fetchone()
        lv = {r[0]: r[1] for r in c.execute("SELECT level,COUNT(*) FROM words WHERE COALESCE(excluded,0)=0 GROUP BY level")}
        # 급수별 '학습 시작' — box>=3은 기준이 너무 높아 막대가 계속 0이었다
        lv_done = {r[0]: r[1] for r in c.execute("""SELECT w.level,COUNT(*) FROM word_progress p
                     JOIN words w ON w.id=p.word_id WHERE p.user_id=? AND p.seen>0 GROUP BY w.level""", (uid,))}
        lv_mast = {r[0]: r[1] for r in c.execute("""SELECT w.level,COUNT(*) FROM word_progress p
                     JOIN words w ON w.id=p.word_id WHERE p.user_id=? AND p.box>=5 GROUP BY w.level""", (uid,))}
        learning = c.execute("""SELECT COUNT(*) FROM word_progress
                                WHERE user_id=? AND seen>0 AND box<5""", (uid,)).fetchone()[0]
        hsk_tot = {r[0]: r[1] for r in c.execute("SELECT hsk,COUNT(*) FROM words WHERE hsk>0 AND COALESCE(excluded,0)=0 GROUP BY hsk")}
        # 분류별(단어/구문/성어) 총계와 학습 시작 개수
        cat_tot = {r[0]: r[1] for r in c.execute(
            "SELECT COALESCE(category,'단어'),COUNT(*) FROM words WHERE COALESCE(excluded,0)=0 GROUP BY 1")}
        cat_done = {r[0]: r[1] for r in c.execute("""SELECT COALESCE(w.category,'단어'),COUNT(*)
                     FROM word_progress p JOIN words w ON w.id=p.word_id
                     WHERE p.user_id=? AND p.seen>0 AND COALESCE(w.excluded,0)=0 GROUP BY 1""", (uid,))}
        hsk_cum = {n: sum(v for k, v in hsk_tot.items() if k <= n) for n in hsk_tot}
        hsk_done = {r[0]: r[1] for r in c.execute("""SELECT w.hsk,COUNT(*) FROM word_progress p
                     JOIN words w ON w.id=p.word_id WHERE p.user_id=? AND p.seen>0 AND w.hsk>0
                     GROUP BY w.hsk""", (uid,))}
        wrongn = c.execute("""SELECT COUNT(*) FROM word_progress
                             WHERE user_id=? AND wrong>0 AND box<5""", (uid,)).fetchone()[0]
        streak = c.execute("""SELECT COUNT(DISTINCT date(created_at)) FROM study_log
                              WHERE user_id=? AND created_at>=datetime('now','-30 day')""", (uid,)).fetchone()[0]
        return {"total": total, "seen": seen, "mastered": mastered, "due": due,
                "today_count": today[0], "today_correct": today[1],
                "levels": lv, "levels_done": lv_done, "levels_mastered": lv_mast,
                "learning": learning, "untouched": total - seen,
                "active_days_30": streak, "wrong_pool": wrongn,
                "hsk": hsk_tot, "hsk_cum": hsk_cum, "hsk_done": hsk_done,
                "cats": cat_tot, "cats_done": cat_done}
    finally:
        c.close()


@app.get("/api/words/session")
async def words_session(request: Request, mode: str = "srs", limit: int = 20,
                        level: int = 0, start: int = 0, end: int = 0, hsk: int = 0,
                        cat: str = "", course: int = 0, wordset: str = ""):
    """학습 세션용 단어 뽑기.
    srs      : 복습 예정(due) 우선 → 없으면 신규를 쉬운 순(seq)으로
    quiz/dictation/writing : 지정 구간에서 미숙달 우선
    """
    uid = request.state.user_id
    limit = max(1, min(100, limit))
    c = _wdb()
    try:
        # 학습에서 제외된 항목(단어가 아닌 교재 조각)은 빼고 낸다
        cond, args = ["w.pinyin!=''", "w.meaning_ko!=''", "COALESCE(w.excluded,0)=0"], []
        if cat in ("단어", "구문", "성어"):
            cond.append("w.category=?"); args.append(cat)
        if course:                       # 교재가 정한 4급/5급 어휘만
            cond.append("w.hsk_course=?"); args.append(course)
        if wordset:                      # 따로 모은 묶음 (양사·접속사·의성의태)
            cond.append("w.wordset=?"); args.append(wordset)
        if hsk:
            # HSK 급수는 누적이다 — 3급 시험 범위 = 1~3급 전체
            cond.append("w.hsk>0 AND w.hsk<=?"); args.append(hsk)
        if level:
            cond.append("w.level=?"); args.append(level)
        if start:
            cond.append("w.seq>=?"); args.append(start)
        if end:
            cond.append("w.seq<=?"); args.append(end)
        if mode in ("dictation",):
            cond.append("w.audio1!=''")
        where = " AND ".join(cond)

        rows = []
        if mode == "review":
            # 틀린 적 있고 아직 숙달 전인 단어. 최근 틀린 순.
            rows = c.execute(f"""SELECT w.*, p.box, p.due_at, p.seen, p.correct, p.wrong, p.starred
                FROM words w JOIN word_progress p ON p.word_id=w.id AND p.user_id=?
                WHERE {where} AND p.wrong>0 AND p.box<5
                ORDER BY (p.wrong*1.0/(p.seen+1)) DESC, p.last_at DESC LIMIT ?""",
                [uid] + args + [limit]).fetchall()
        elif mode == "srs":
            rows = c.execute(f"""SELECT w.*, p.box, p.due_at, p.seen, p.correct, p.wrong, p.starred
                FROM words w JOIN word_progress p ON p.word_id=w.id AND p.user_id=?
                WHERE {where} AND p.due_at IS NOT NULL AND p.due_at<=datetime('now')
                ORDER BY p.due_at LIMIT ?""", [uid] + args + [limit]).fetchall()
        need = 0 if mode == "review" else limit - len(rows)
        if need > 0:
            got = {r["id"] for r in rows}
            extra = c.execute(f"""SELECT w.*, p.box, p.due_at, p.seen, p.correct, p.wrong, p.starred
                FROM words w LEFT JOIN word_progress p ON p.word_id=w.id AND p.user_id=?
                WHERE {where} AND (p.seen IS NULL OR p.box<3)
                ORDER BY COALESCE(p.box,-1), w.seq LIMIT ?""", [uid] + args + [need * 3]).fetchall()
            for r in extra:
                if r["id"] in got: continue
                rows.append(r); got.add(r["id"])
                if len(rows) >= limit: break

        MODES = ["quiz", "dictation", "srs", "writing"]
        out = []
        for r in rows:
            d = {k: r[k] for k in r.keys()}
            if mode == "review":
                # 이미 '정답으로' 통과한 방식은 건너뛰고 아직 안 된 방식으로 물어본다
                done = {x[0] for x in c.execute(
                    "SELECT DISTINCT mode FROM study_log WHERE user_id=? AND word_id=? AND result=1",
                    (uid, r["id"]))}
                nxt = next((m for m in MODES if m not in done), None)
                if nxt is None:                      # 다 통과했으면 가장 오래된 방식으로 다시
                    old_m = c.execute("""SELECT mode FROM study_log
                        WHERE user_id=? AND word_id=? AND result=1
                        ORDER BY created_at LIMIT 1""", (uid, r["id"])).fetchone()
                    nxt = old_m[0] if old_m else "quiz"
                d["ask_mode"] = nxt
                if nxt in ("quiz", "srs", "dictation"):
                    ans = (r["meaning_ko"] or "").strip()
                    dis = []
                    for x in c.execute("""SELECT chinese,meaning_ko,pinyin FROM words
                            WHERE id!=? AND meaning_ko!='' AND COALESCE(excluded,0)=0 AND length(chinese)=?
                            ORDER BY RANDOM() LIMIT 80""", (r["id"], len(r["chinese"]))):
                        m2 = (x[1] or "").strip()
                        if not m2 or m2 == ans: continue
                        if len(m2) >= 2 and (m2 in ans or ans in m2): continue
                        if any(m2 == g["meaning_ko"] for g in dis): continue
                        dis.append({"chinese": x[0], "meaning_ko": m2, "pinyin": x[2] or ""})
                        if len(dis) >= 3: break
                    d["distractors"] = dis
            # 4지선다 오답 보기: 같은 레벨·비슷한 길이에서 무작위
            if mode in ("quiz", "srs", "dictation"):
                # ⚠️ 오답 보기가 정답과 뜻이 겹치면 문제가 성립하지 않는다
                #    (二/两=둘, 汉语/中文=중국어 …) → 뜻 중복을 서버에서 배제
                ans = (r["meaning_ko"] or "").strip()
                def _pick(sql, params, n):
                    got = []
                    for x in c.execute(sql, params).fetchall():
                        m = (x[1] or "").strip()
                        if not m or m == ans: continue
                        # 한쪽이 다른 쪽을 포함해도 모호하다 ("중국" vs "중국어")
                        if len(m) >= 2 and (m in ans or ans in m): continue
                        if any(m == g["meaning_ko"] for g in got): continue
                        got.append({"chinese": x[0], "meaning_ko": m, "pinyin": x[2] or ""})
                        if len(got) >= n: break
                    return got
                dis = _pick("""SELECT chinese,meaning_ko,pinyin FROM words
                    WHERE id!=? AND meaning_ko!='' AND COALESCE(excluded,0)=0 AND level=? AND length(chinese)=?
                    ORDER BY RANDOM() LIMIT 60""", (r["id"], r["level"], len(r["chinese"])), 3)
                if len(dis) < 3:
                    dis += _pick("""SELECT chinese,meaning_ko,pinyin FROM words
                        WHERE id!=? AND meaning_ko!='' AND COALESCE(excluded,0)=0 AND length(chinese)=?
                        ORDER BY RANDOM() LIMIT 80""", (r["id"], len(r["chinese"])), 3 - len(dis))
                if len(dis) < 3:
                    dis += _pick("""SELECT chinese,meaning_ko,pinyin FROM words
                        WHERE id!=? AND meaning_ko!='' AND COALESCE(excluded,0)=0 ORDER BY RANDOM() LIMIT 120""",
                        (r["id"],), 3 - len(dis))
                d["distractors"] = dis
            out.append(d)
        return {"mode": mode, "words": out, "count": len(out)}
    finally:
        c.close()


@app.post("/api/words/answer")
async def words_answer(request: Request):
    """정답/오답 반영 — 라이트너 박스로 다음 복습일 계산"""
    uid = request.state.user_id
    data = await request.json()
    wid = int(data.get("word_id") or 0)
    ok = 1 if data.get("result") else 0
    mode = (data.get("mode") or "srs")[:20]
    ms = int(data.get("ms") or 0)
    if not wid:
        raise HTTPException(400, "word_id 필요")
    c = _wdb()
    try:
        row = c.execute("SELECT * FROM word_progress WHERE user_id=? AND word_id=?", (uid, wid)).fetchone()
        box = row["box"] if row else 0
        streak = row["streak"] if row else 0
        best = row["best_streak"] if row else 0
        if ok:
            box = min(len(_SRS_INTERVALS) - 1, box + 1)
            streak += 1
            best = max(best, streak)
        else:
            box = max(0, box - 1)
            streak = 0
        days = _SRS_INTERVALS[box]
        if row:
            c.execute("""UPDATE word_progress SET box=?, due_at=datetime('now', ?),
                         interval_days=?, seen=seen+1, correct=correct+?, wrong=wrong+?,
                         streak=?, best_streak=?, last_at=datetime('now')
                         WHERE user_id=? AND word_id=?""",
                      (box, f"+{days} day", days, ok, 1 - ok, streak, best, uid, wid))
        else:
            c.execute("""INSERT INTO word_progress
                (user_id,word_id,box,due_at,interval_days,seen,correct,wrong,streak,best_streak,last_at)
                VALUES (?,?,?,datetime('now', ?),?,1,?,?,?,?,datetime('now'))""",
                      (uid, wid, box, f"+{days} day", days, ok, 1 - ok, streak, best))
        c.execute("""INSERT INTO study_log (user_id,word_id,mode,result,ms,created_at)
                     VALUES (?,?,?,?,?,datetime('now'))""", (uid, wid, mode, ok, ms))
        c.commit()
        return {"ok": True, "box": box, "next_days": days, "streak": streak}
    finally:
        c.close()


@app.post("/api/words/flag")
async def words_flag(request: Request):
    """단어 신고 — 뜻이 이상하거나 단어가 아닌 것을 표시해 둔다"""
    uid = request.state.user_id
    data = await request.json()
    wid = int(data.get("word_id") or 0)
    note = (data.get("note") or "")[:200]
    on = 0 if data.get("off") else 1
    if not wid:
        raise HTTPException(400, "word_id 필요")
    c = _wdb()
    try:
        try:
            c.execute("ALTER TABLE word_progress ADD COLUMN flagged INTEGER DEFAULT 0")
            c.execute("ALTER TABLE word_progress ADD COLUMN flag_note TEXT DEFAULT ''")
            c.commit()
        except Exception:
            pass
        r = c.execute("SELECT 1 FROM word_progress WHERE user_id=? AND word_id=?", (uid, wid)).fetchone()
        if r:
            c.execute("UPDATE word_progress SET flagged=?, flag_note=? WHERE user_id=? AND word_id=?",
                      (on, note, uid, wid))
        else:
            c.execute("INSERT INTO word_progress (user_id,word_id,flagged,flag_note) VALUES (?,?,?,?)",
                      (uid, wid, on, note))
        c.commit()
        n = c.execute("SELECT COUNT(*) FROM word_progress WHERE user_id=? AND flagged=1", (uid,)).fetchone()[0]
        return {"ok": True, "flagged": on, "total_flagged": n}
    finally:
        c.close()


@app.get("/api/words/flagged")
async def words_flagged(request: Request, limit: int = 200):
    """신고된 단어 목록 (점검용)"""
    uid = request.state.user_id
    c = _wdb()
    try:
        try:
            c.execute("SELECT flagged FROM word_progress LIMIT 1").fetchone()
        except Exception:
            return {"words": [], "total": 0}
        rows = c.execute("""SELECT w.*, p.flagged, p.flag_note FROM words w
            JOIN word_progress p ON p.word_id=w.id AND p.user_id=?
            WHERE p.flagged=1 ORDER BY w.seq LIMIT ?""", (uid, limit)).fetchall()
        return {"total": len(rows), "words": [{k: r[k] for k in r.keys()} for r in rows]}
    finally:
        c.close()


@app.post("/api/words/star")
async def words_star(request: Request):
    uid = request.state.user_id
    data = await request.json()
    wid = int(data.get("word_id") or 0)
    on = 1 if data.get("on") else 0
    c = _wdb()
    try:
        r = c.execute("SELECT 1 FROM word_progress WHERE user_id=? AND word_id=?", (uid, wid)).fetchone()
        if r:
            c.execute("UPDATE word_progress SET starred=? WHERE user_id=? AND word_id=?", (on, uid, wid))
        else:
            c.execute("INSERT INTO word_progress (user_id,word_id,starred) VALUES (?,?,?)", (uid, wid, on))
        c.commit()
        return {"ok": True, "starred": on}
    finally:
        c.close()


# ── 단어 검색 ────────────────────────────────────────────────────────────────
# 병음이 DB에 성조 기호로 들어있어(hǎo) 'hao' 로는 LIKE 가 걸리지 않는다.
# 그래서 무성조·무공백 키(py_plain)와 성조숫자 키(py_num)를 미리 만들어 두고 그걸로 찾는다.
_PY_MARK = {'ā':'a','á':'a','ǎ':'a','à':'a','ē':'e','é':'e','ě':'e','è':'e',
            'ī':'i','í':'i','ǐ':'i','ì':'i','ō':'o','ó':'o','ǒ':'o','ò':'o',
            'ū':'u','ú':'u','ǔ':'u','ù':'u','ǖ':'u','ǘ':'u','ǚ':'u','ǜ':'u','ü':'u'}


def _norm_py(q: str):
    """입력을 검색키와 같은 모양으로 — 'Nǐ hǎo' / 'ni hao' / 'ni3hao3' 를 모두 받아준다."""
    t = ''.join(_PY_MARK.get(ch, ch) for ch in (q or '').strip().lower())
    t = re.sub(r"[\s'\-·]", "", t)
    num = re.sub(r"[^a-z0-9]", "", t)
    plain = re.sub(r"\d", "", num).replace("v", "u")
    return plain, num


def _search_clauses(q: str):
    """(rank SQL, rank args, where SQL, where args) 를 만든다.
       같은 조건을 rank(CASE)와 where 양쪽에 쓰므로 인자도 각각 따로 모은다."""
    plain, num = _norm_py(q)
    # tones 는 ü 를 v 로 적는다(nv3). 사용자는 nu/nv/nü 아무거나 치므로 v 철자도 같이 본다.
    pv = plain.replace("nu", "nv").replace("lu", "lv")
    rules = []                                   # (조건SQL, 인자들, 점수)
    rules.append(("w.chinese=?", [q], 0))
    if num != plain:                             # 'hao3' 처럼 성조까지 지정하면 그게 최우선
        rules.append(("w.py_num=?", [num], 0))
    if plain:
        rules.append(("(w.py_plain=? OR w.py_num=?)", [plain, num], 1))
    rules.append(("w.chinese LIKE ?", [q + "%"], 2))
    if plain:
        # 음절 경계에서 끊기는 앞부분 일치를 먼저 (nu → 努力·女孩 이 挪 보다 앞)
        rules.append(("(w.py_num GLOB ? OR w.py_num GLOB ?)",
                      [plain + "[0-9]*", pv + "[0-9]*"], 2))
        rules.append(("w.py_plain LIKE ?", [plain + "%"], 3))
    rules.append(("w.meaning_ko=?", [q], 3))
    rules.append(("w.chinese LIKE ?", ["%" + q + "%"], 4))
    if len(plain) >= 2:
        rules.append(("w.py_plain LIKE ?", ["%" + plain + "%"], 5))
    rules.append(("w.meaning_ko LIKE ?", ["%" + q + "%"], 6))
    rules.append(("w.senses_ko LIKE ?", ["%" + q + "%"], 7))
    # 'ni hao' 처럼 띄어 쓰면 낱 음절로도 찾아준다 (여러 단어가 함께 걸리도록)
    toks = [t for t in (_norm_py(x)[0] for x in re.split(r"[\s,]+", q)) if len(t) >= 2]
    if len(toks) > 1:
        for t in toks[:4]:
            rules.append(("w.py_plain=?", [t], 8))
    rank_sql = "CASE " + " ".join(f"WHEN {c} THEN {sc}" for c, _, sc in rules) + " ELSE 9 END"
    rank_args = [a for _, args, _ in rules for a in args]
    where_sql = "(" + " OR ".join(c for c, _, _ in rules) + ")"
    return rank_sql, rank_args, where_sql, list(rank_args), plain


@app.get("/api/words/search")
async def words_search(request: Request, q: str = "", limit: int = 40, cat: str = ""):
    """한자·병음·뜻 통합 검색. 정확도 순으로 돌려준다."""
    q = (q or "").strip()
    if not q:
        return {"q": "", "py": "", "total": 0, "words": []}
    limit = max(1, min(100, limit))
    rank_sql, rank_args, where_sql, where_args, plain = _search_clauses(q)
    uid = request.state.user_id
    c = _wdb()
    try:
        cond = f"{where_sql} AND COALESCE(w.excluded,0)=0"
        extra = []
        if cat in ("단어", "구문", "성어"):
            cond += " AND w.category=?"; extra = [cat]
        rows = c.execute(f"""SELECT w.*, {rank_sql} AS rk, p.box, p.seen, p.correct, p.wrong, p.starred
            FROM words w LEFT JOIN word_progress p ON p.word_id=w.id AND p.user_id=?
            WHERE {cond} ORDER BY rk, length(w.chinese), w.seq LIMIT ?""",
            [uid] + rank_args + where_args + extra + [limit]).fetchall()
        total = c.execute(f"SELECT COUNT(*) FROM words w WHERE {cond}",
                          where_args + extra).fetchone()[0]
        return {"q": q, "py": plain, "total": total, "words": [dict(r) for r in rows]}
    finally:
        c.close()


# ── 카메라 촬영 검색 ─────────────────────────────────────────────────────────
# 폰으로 교재를 찍으면 OCR로 한자를 뽑아 사전에 있는 단어로 끊어 돌려준다.
# tesseract(chi_sim)와 pytesseract 가 필요하다 — Dockerfile 에 넣어 두었다.
_HANZI = re.compile(r"[\u4e00-\u9fff]+")
_LATIN = re.compile(r"[A-Za-z\u00c0-\u01ff]+")
_PY_SYL = None


def _py_syllables():
    """DB의 tones 에서 실제로 쓰이는 병음 음절을 모은다 (v→u 로 통일).
    영어 단어가 병음으로 둔갑하는 걸 막으려면 '음절로 쪼개지는지'를 봐야 한다."""
    global _PY_SYL
    if _PY_SYL is None:
        c = _wdb()
        try:
            out = set()
            for (t,) in c.execute("SELECT DISTINCT tones FROM words WHERE tones IS NOT NULL AND tones!=''"):
                for syl in (t or "").split():
                    syl = re.sub(r"[^a-z]", "", syl.lower()).replace("v", "u")
                    if syl:
                        out.add(syl)
        finally:
            c.close()
        _PY_SYL = out
    return _PY_SYL


# 교재 지면에 흔한 영어 낱말 — 우연히 병음 음절로 쪼개져서 잡음이 된다
_EN_STOP = {"chinese", "page", "banana", "note", "name", "lesson", "unit", "menu",
            "see", "cheese", "china", "man", "men", "money", "no", "one", "on",
            "song", "sang", "same", "some", "line", "nine", "shine", "picture",
            "pen", "pan", "can", "he", "she", "we", "me", "be", "in", "is", "it",
            "an", "as", "at", "so", "to", "do", "go", "up", "us", "you", "your"}


def _split_syllables(tok: str):
    """'nihao' → ['ni','hao'].  못 쪼개면 None (= 병음이 아님).
    이건 1차 거름일 뿐이고, 진짜 판별은 뒤의 py_plain 조회가 한다."""
    syls = _py_syllables()
    if not tok or len(tok) > 24 or tok in _EN_STOP:
        return None
    out, i = [], 0
    while i < len(tok):
        for L in range(min(6, len(tok) - i), 0, -1):     # zhuang·chuang 이 6자
            if tok[i:i + L] in syls:
                out.append(tok[i:i + L]); i += L; break
        else:
            return None
    # 여러 음절로 쪼갤 때 한 글자짜리('a','e','o')가 끼면 대개 영어다 — banana → ban+an+a
    if len(out) > 1 and any(len(x) == 1 for x in out):
        return None
    return out


def _ocr_pinyin(raw: bytes):
    """사진에서 로마자를 읽어 '병음으로 보이는 것'만 남긴다."""
    import io
    from PIL import Image, ImageOps
    import pytesseract
    im = Image.open(io.BytesIO(raw))
    im = ImageOps.exif_transpose(im).convert("L")
    if max(im.size) > 2000:
        im.thumbnail((2000, 2000))
    if max(im.size) < 800:
        im = im.resize((im.width * 2, im.height * 2))
    im = ImageOps.autocontrast(im)
    best = []
    for psm in (6, 4):
        try:
            t = pytesseract.image_to_string(im, lang="eng", config=f"--oem 1 --psm {psm}")
        except Exception:
            continue
        toks = []
        for raw_tok in _LATIN.findall(t or ""):
            tok = "".join(_PY_MARK.get(ch, ch) for ch in raw_tok.lower())
            tok = re.sub(r"[^a-z]", "", tok).replace("v", "u")
            if len(tok) >= 2 and _split_syllables(tok):
                toks.append(tok)
        if len(toks) > len(best):
            best = toks
    return best[:60]


def _ocr_hanzi(raw: bytes) -> str:
    """이미지에서 한자만 뽑아낸다. 사진이라 기울거나 어두운 경우가 많아 전처리를 거친다."""
    import io
    from PIL import Image, ImageOps
    import pytesseract
    im = Image.open(io.BytesIO(raw))
    im = ImageOps.exif_transpose(im)          # 폰 사진의 회전 정보 반영
    im = im.convert("L")
    if max(im.size) > 2000:                   # 너무 크면 느리기만 하다
        im.thumbnail((2000, 2000))
    if max(im.size) < 800:                    # 너무 작으면 글자가 뭉개진다
        im = im.resize((im.width * 2, im.height * 2))
    im = ImageOps.autocontrast(im)
    best = ""
    for psm in (6, 4, 11):                    # 블록 / 단으로 / 흩어진 글자
        try:
            t = pytesseract.image_to_string(im, lang="chi_sim",
                                            config=f"--oem 1 --psm {psm}")
        except Exception:
            continue
        h = "".join(_HANZI.findall(t or ""))
        if len(h) > len(best):
            best = h
    return best[:400]


@app.post("/api/words/ocr")
async def words_ocr(request: Request, file: UploadFile = File(...), cat: str = ""):
    """사진 → 한자·병음 인식 → 사전에 있는 단어로 끊어 검색."""
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "이미지가 비어 있습니다")
    if len(raw) > 12 * 1024 * 1024:
        raise HTTPException(400, "이미지가 너무 큽니다 (12MB 이하)")
    try:
        text = _ocr_hanzi(raw)
        pytoks = _ocr_pinyin(raw)
    except ImportError:
        raise HTTPException(500, "OCR 모듈(pytesseract)이 설치되어 있지 않습니다")
    except Exception as e:
        raise HTTPException(500, f"이미지를 읽지 못했습니다: {e}")
    if not text and not pytoks:
        return {"text": "", "pinyin": "", "chars": [], "words": [],
                "hint": "글자를 찾지 못했습니다. 글자가 크고 또렷하게 나오도록 다시 찍어보세요."}

    # ── 한자 후보: jieba 로 끊고 낱글자도 뒤에 붙인다 (긴 단어 우선) ──
    try:
        import jieba
        segs = [w for w in jieba.cut(text, cut_all=False) if _HANZI.fullmatch(w)]
    except Exception:
        segs = []
    cand, seen = [], set()
    for w in sorted(set(segs), key=len, reverse=True):
        if w not in seen:
            seen.add(w); cand.append(w)
    for ch in text:
        if ch not in seen:
            seen.add(ch); cand.append(ch)
    cand = cand[:120]

    # ── 병음 후보: 낱 토큰 + 이어진 토큰(2~4개)을 붙여 한 단어로도 본다 ──
    #    교재는 'nǐ hǎo' 처럼 음절을 띄어 쓰므로 붙여야 你好 가 걸린다.
    pycand, pyseen = [], set()
    for size in (4, 3, 2, 1):
        for i in range(len(pytoks) - size + 1):
            j = "".join(pytoks[i:i + size])
            if 1 < len(j) <= 24 and j not in pyseen:
                pyseen.add(j); pycand.append(j)
    pycand = pycand[:150]

    uid = request.state.user_id
    c = _wdb()
    try:
        extra, cond = [], ""
        if cat in ("단어", "구문", "성어"):
            cond = " AND w.category=?"; extra = [cat]
        rows, got = [], set()

        def collect(sql, params):
            for r in c.execute(sql, params).fetchall():
                if r["id"] not in got:
                    got.add(r["id"]); rows.append(r)

        if cand:
            qs = ",".join("?" * len(cand))
            collect(f"""SELECT w.*, p.box, p.seen, p.starred
                FROM words w LEFT JOIN word_progress p ON p.word_id=w.id AND p.user_id=?
                WHERE w.chinese IN ({qs}) AND COALESCE(w.excluded,0)=0{cond}
                ORDER BY length(w.chinese) DESC, w.seq LIMIT 60""", [uid] + cand + extra)
        if pycand:
            qs = ",".join("?" * len(pycand))
            collect(f"""SELECT w.*, p.box, p.seen, p.starred
                FROM words w LEFT JOIN word_progress p ON p.word_id=w.id AND p.user_id=?
                WHERE w.py_plain IN ({qs}) AND COALESCE(w.excluded,0)=0{cond}
                ORDER BY length(w.chinese) DESC, w.seq LIMIT 60""", [uid] + pycand + extra)
        rows = sorted(rows, key=lambda r: (-len(r["chinese"]), r["seq"]))[:60]

        # 사진에는 잡글자가 섞인다(한글·표를 한자로 오인하는 등).
        # '단독으로 배우는 글자'이거나 '찾아낸 단어에 실제로 쓰인 글자'만 남긴다.
        # LIKE '%글자%' 로 거르면 흔한 한자는 어딘가엔 다 있어서 노이즈가 그대로 남는다.
        uniq = list(dict.fromkeys(text))[:80]
        in_hit = {ch for r in rows for ch in r["chinese"]}
        solo = set()
        if uniq:
            qs2 = ",".join("?" * len(uniq))
            solo = {r[0] for r in c.execute(
                f"SELECT chinese FROM words WHERE length(chinese)=1 AND chinese IN ({qs2})"
                f" AND COALESCE(excluded,0)=0", uniq)}
        chars = [ch for ch in uniq if ch in in_hit or ch in solo]
        # 병음도 잡음이 섞인다(한글·표를 로마자로 오인). 찾아낸 단어에 실제로 쓰인 음절만 보여준다.
        hit_py = "".join((r["py_plain"] or "") for r in rows)
        shown_py = [t for t in pytoks if t and t in hit_py]
        return {"text": text, "pinyin": " ".join(dict.fromkeys(shown_py))[:200],
                "chars": chars[:40], "words": [dict(r) for r in rows], "hint": ""}
    finally:
        c.close()


@app.get("/api/chinese/char/{ch}")
async def chinese_char(ch: str):
    """글자 한 자의 상세 — 뜻·번체자·자원(만들어진 내력)."""
    ch = (ch or "").strip()[:1]
    if not ch:
        raise HTTPException(400, "글자가 없습니다")
    # 번체자 대응 (CC-CEDICT 표제어에서 간체≠번체인 1글자만 추림)
    global _TRAD_MAP
    try:
        _TRAD_MAP
    except NameError:
        _TRAD_MAP = {}
        try:
            for line in open("database/cedict_full.txt", encoding="utf-8"):
                if line.startswith("#"): continue
                p2 = line.split(" ", 2)
                if len(p2) >= 2 and len(p2[1]) == 1 and p2[0] != p2[1]:
                    _TRAD_MAP.setdefault(p2[1], p2[0])
        except Exception:
            pass
    global _ORIGIN
    try:
        _ORIGIN
    except NameError:
        try:
            _ORIGIN = json.load(open("database/char_origin.json", encoding="utf-8"))
        except Exception:
            _ORIGIN = {}
    c = _wdb()
    try:
        r = c.execute("""SELECT chinese,pinyin,meaning_ko,senses_ko,meaning_en,hsk,audio1,audio2
                         FROM words WHERE chinese=? AND COALESCE(excluded,0)=0""", (ch,)).fetchone()
        w = dict(r) if r else {}
    finally:
        c.close()
    o = _ORIGIN.get(ch) or {}
    # 교재에 없는 글자는 char_ko(사전 뜻을 한글로 옮겨 둔 표)에서 끌어온다.
    # 쓰기 공부에서 글자마다 음과 뜻을 보여 주는데, 없으면 영어가 그대로 나온다(专 → specialized).
    py, gloss = w.get("pinyin") or "", w.get("meaning_ko") or ""
    if not py or not gloss:
        c2 = _wdb()
        try:
            k = c2.execute("SELECT pinyin,ko FROM char_ko WHERE char=?", (ch,)).fetchone()
        except Exception:
            k = None
        finally:
            c2.close()
        if k:
            py = py or (k["pinyin"] or "")
            gloss = gloss or (k["ko"] or "")
    if not gloss:
        gloss = w.get("meaning_en") or ""
    if not py or not gloss:
        global _CHAR_DICT
        try:
            _CHAR_DICT
        except NameError:
            _CHAR_DICT = {}
            try:
                for line in open("database/cedict_full.txt", encoding="utf-8"):
                    if line.startswith("#"): continue
                    try:
                        simp = line.split(" ", 2)[1]
                        if len(simp) != 1 or simp in _CHAR_DICT: continue
                        pn = line.split("[", 1)[1].split("]", 1)[0]
                        en = line.split("/", 2)[1] if "/" in line else ""
                    except Exception:
                        continue
                    _CHAR_DICT[simp] = (pn, en)
            except Exception:
                pass
        d = _CHAR_DICT.get(ch)
        if d:
            if not py:
                try:
                    from pypinyin import pinyin as _pyf, Style as _St
                    py = _pyf(ch, style=_St.TONE)[0][0]
                except Exception:
                    py = d[0]
            if not gloss:
                gloss = d[1]
    return {"char": ch, "trad": _TRAD_MAP.get(ch, ""), "word": w,
            "pinyin": py, "meaning": gloss,
            "origin_type": o.get("type", ""), "origin_story": o.get("story", "")}


@app.get("/api/words/roadmap")
async def words_roadmap(request: Request):
    """학습 순서 안내 — HSK 급수축과 교재 단원축을 함께 준다."""
    uid = request.state.user_id
    c = _wdb()
    try:
        def done_map(sql, *a):
            return {r[0]: r[1] for r in c.execute(sql, a)}
        hsk_tot = done_map("SELECT hsk,COUNT(*) FROM words WHERE hsk>0 AND COALESCE(excluded,0)=0 GROUP BY hsk")
        hsk_done = done_map("""SELECT w.hsk,COUNT(*) FROM word_progress p JOIN words w ON w.id=p.word_id
            WHERE p.user_id=? AND p.seen>0 AND w.hsk>0 AND COALESCE(w.excluded,0)=0 GROUP BY w.hsk""", uid)
        # 교재는 쉬운 것부터 — 이 순서가 곧 학습 로드맵이다
        ORDER = ["기초중국어", "기초중국어2", "중국어발음연습", "생활중국어",
                 "중국어단어장", "중국인의생활과문화", "신HSK쓰기독해", "기능어"]
        subs = []
        for name, tot in c.execute("""SELECT u.subject,COUNT(*) FROM word_units u
                JOIN words w ON w.id=u.word_id
                WHERE COALESCE(w.excluded,0)=0 AND u.subject!='HSK어휘'
                GROUP BY u.subject"""):
            dn = c.execute("""SELECT COUNT(*) FROM word_progress p
                JOIN words w ON w.id=p.word_id JOIN word_units u ON u.word_id=w.id
                WHERE p.user_id=? AND p.seen>0 AND u.subject=? AND COALESCE(w.excluded,0)=0""",
                (uid, name)).fetchone()[0]
            units = []
            for w_, c_, ut in c.execute("""SELECT u.unit_week,u.unit_class,COUNT(*) FROM word_units u
                    JOIN words w ON w.id=u.word_id
                    WHERE u.subject=? AND COALESCE(w.excluded,0)=0 AND u.unit_week IS NOT NULL
                    GROUP BY u.unit_week,u.unit_class ORDER BY u.unit_week,u.unit_class""", (name,)):
                ud = c.execute("""SELECT COUNT(*) FROM word_progress p
                    JOIN words w ON w.id=p.word_id JOIN word_units u ON u.word_id=w.id
                    WHERE p.user_id=? AND p.seen>0 AND u.subject=? AND u.unit_week=? AND u.unit_class=?
                    AND COALESCE(w.excluded,0)=0""", (uid, name, w_, c_)).fetchone()[0]
                units.append({"week": w_, "cls": c_, "total": ut, "done": ud})
            subs.append({"name": name, "total": tot, "done": dn, "units": units})
        subs.sort(key=lambda x: (ORDER.index(x["name"]) if x["name"] in ORDER else 99, x["name"]))
        course = {}
        for lv in (4, 5):
            t = c.execute("SELECT COUNT(*) FROM words WHERE hsk_course=? AND COALESCE(excluded,0)=0",
                          (lv,)).fetchone()[0]
            dn = c.execute("""SELECT COUNT(*) FROM word_progress p JOIN words w ON w.id=p.word_id
                              WHERE p.user_id=? AND p.seen>0 AND w.hsk_course=?
                                AND COALESCE(w.excluded,0)=0""", (uid, lv)).fetchone()[0]
            course[str(lv)] = {"total": t, "done": dn}
        sets = {}
        for name in ("양사", "접속사", "의성의태"):
            t = c.execute("""SELECT COUNT(*) FROM words WHERE wordset=?
                             AND COALESCE(excluded,0)=0""", (name,)).fetchone()[0]
            dn = c.execute("""SELECT COUNT(*) FROM word_progress p JOIN words w ON w.id=p.word_id
                              WHERE p.user_id=? AND p.seen>0 AND w.wordset=?
                                AND COALESCE(w.excluded,0)=0""", (uid, name)).fetchone()[0]
            sets[name] = {"total": t, "done": dn}
        return {"hsk": {"total": hsk_tot, "done": hsk_done}, "subjects": subs,
                "course": course, "sets": sets}
    finally:
        c.close()


@app.get("/api/words/browse")
async def words_browse(request: Request, offset: int = 0, limit: int = 50,
                       level: int = 0, q: str = "", starred: int = 0, hsk: int = 0,
                       cat: str = ""):
    uid = request.state.user_id
    limit = max(1, min(200, limit))
    c = _wdb()
    try:
        cond, args = ["COALESCE(w.excluded,0)=0"], []
        if cat in ("단어", "구문", "성어"):
            cond.append("w.category=?"); args.append(cat)
        if hsk: cond.append("(w.hsk>0 AND w.hsk<=?)"); args.append(hsk)
        if level: cond.append("w.level=?"); args.append(level)
        if q:
            cond.append("(w.chinese LIKE ? OR w.pinyin LIKE ? OR w.meaning_ko LIKE ?)")
            args += [f"%{q}%"] * 3
        if starred: cond.append("p.starred=1")
        where = " AND ".join(cond)
        total = c.execute(f"""SELECT COUNT(*) FROM words w
            LEFT JOIN word_progress p ON p.word_id=w.id AND p.user_id=? WHERE {where}""",
            [uid] + args).fetchone()[0]
        rows = c.execute(f"""SELECT w.*, p.box, p.seen, p.correct, p.wrong, p.starred, p.due_at
            FROM words w LEFT JOIN word_progress p ON p.word_id=w.id AND p.user_id=?
            WHERE {where} ORDER BY w.seq LIMIT ? OFFSET ?""",
            [uid] + args + [limit, offset]).fetchall()
        return {"total": total, "offset": offset,
                "words": [{k: r[k] for k in r.keys()} for r in rows]}
    finally:
        c.close()


# ─────────────────────────────────────────────────────────────
#  4자성어(成语) — words(category='성어') + chengyu(부가정보) 사용
#  진도·정답 기록은 단어와 같은 word_progress / study_log 를 그대로 쓴다
# ─────────────────────────────────────────────────────────────

CHENGYU_MODES = ("meaning", "idiom", "blank", "listen", "card")


def _cy_where(hsk: int = 0, only: str = "", has_story: int = 0):
    cond = ["w.category='성어'", "COALESCE(w.excluded,0)=0", "w.meaning_ko!=''"]
    args = []
    if hsk:                       # 7 = HSK 7~9급(성어가 몰려 있는 구간)
        cond.append("COALESCE(w.hsk_new,0)=?"); args.append(hsk)
    if has_story:
        cond.append("COALESCE(g.story,'')!=''")
    if only == "new":
        cond.append("(p.seen IS NULL OR p.seen=0)")
    elif only == "wrong":
        cond.append("COALESCE(p.wrong,0)>0")
    elif only == "star":
        cond.append("COALESCE(p.starred,0)=1")
    elif only == "due":
        cond.append("(p.due_at IS NOT NULL AND p.due_at<=datetime('now'))")
    elif only == "learning":
        cond.append("COALESCE(p.seen,0)>0 AND COALESCE(p.box,0)<5")
    return " AND ".join(cond), args


_CY_SELECT = """SELECT w.id, w.chinese, w.pinyin, w.tones, w.meaning_ko, w.meaning_en,
        w.hsk_new, w.level, w.audio1, w.audio2,
        COALESCE(g.literal_ko,'') literal_ko, COALESCE(g.ko_idiom,'') ko_idiom,
        COALESCE(g.story,'') story, COALESCE(g.freq,0) freq,
        p.box, p.seen, p.correct, p.wrong, p.starred, p.due_at
    FROM words w
    JOIN chengyu g ON g.word_id=w.id
    LEFT JOIN word_progress p ON p.word_id=w.id AND p.user_id=?"""


@app.get("/chengyu")
async def chengyu_page():
    """4자성어 학습 페이지"""
    return FileResponse("frontend/chengyu.html", headers=_NOCACHE)


@app.get("/api/chengyu/stats")
async def chengyu_stats(request: Request):
    """성어 진도 요약 — 학습 시작 화면의 숫자들"""
    uid = request.state.user_id
    c = _wdb()
    try:
        base = "FROM words w JOIN chengyu g ON g.word_id=w.id WHERE w.category='성어' AND COALESCE(w.excluded,0)=0"
        total = c.execute(f"SELECT COUNT(*) {base}").fetchone()[0]
        story = c.execute(f"SELECT COUNT(*) {base} AND COALESCE(g.story,'')!=''").fetchone()[0]
        prog = f"""FROM words w JOIN chengyu g ON g.word_id=w.id
                   JOIN word_progress p ON p.word_id=w.id AND p.user_id=?
                   WHERE w.category='성어' AND COALESCE(w.excluded,0)=0"""
        seen = c.execute(f"SELECT COUNT(*) {prog} AND p.seen>0", (uid,)).fetchone()[0]
        mastered = c.execute(f"SELECT COUNT(*) {prog} AND p.box>=5", (uid,)).fetchone()[0]
        due = c.execute(f"SELECT COUNT(*) {prog} AND p.due_at IS NOT NULL AND p.due_at<=datetime('now')",
                        (uid,)).fetchone()[0]
        wrong = c.execute(f"SELECT COUNT(*) {prog} AND p.wrong>0 AND COALESCE(p.box,0)<5", (uid,)).fetchone()[0]
        star = c.execute(f"SELECT COUNT(*) {prog} AND p.starred=1", (uid,)).fetchone()[0]
        today = c.execute("""SELECT COUNT(*), COALESCE(SUM(l.result),0) FROM study_log l
                             JOIN chengyu g ON g.word_id=l.word_id
                             WHERE l.user_id=? AND date(l.created_at)=date('now')""", (uid,)).fetchone()
        return {"total": total, "story": story, "seen": seen, "mastered": mastered,
                "learning": max(0, seen - mastered), "untouched": total - seen,
                "due": due, "wrong": wrong, "starred": star,
                "today_count": today[0], "today_correct": today[1]}
    finally:
        c.close()


@app.get("/api/chengyu/list")
async def chengyu_list(request: Request, q: str = "", hsk: int = 0, only: str = "",
                       has_story: int = 0, sort: str = "freq", offset: int = 0, limit: int = 60):
    """성어 목록 · 검색 (한자 · 병음 · 뜻 · 대응 한국 사자성어)"""
    uid = request.state.user_id
    limit = max(1, min(200, limit))
    where, args = _cy_where(hsk, only, has_story)
    if q:
        where += (" AND (w.chinese LIKE ? OR w.pinyin LIKE ? OR REPLACE(w.py_plain,' ','') LIKE ?"
                  " OR w.meaning_ko LIKE ? OR g.ko_idiom LIKE ? OR g.literal_ko LIKE ?)")
        args += [f"%{q}%", f"%{q}%", f"%{q.lower().replace(' ', '')}%"] + [f"%{q}%"] * 3
    od = {"seq": "w.seq", "random": "RANDOM()", "hanzi": "w.chinese",
          "due": "COALESCE(p.due_at,'9999'), g.freq DESC"}.get(sort, "g.freq DESC, w.seq")
    c = _wdb()
    try:
        total = c.execute(f"""SELECT COUNT(*) FROM words w JOIN chengyu g ON g.word_id=w.id
            LEFT JOIN word_progress p ON p.word_id=w.id AND p.user_id=? WHERE {where}""",
            [uid] + args).fetchone()[0]
        rows = c.execute(f"{_CY_SELECT} WHERE {where} ORDER BY {od} LIMIT ? OFFSET ?",
                         [uid] + args + [limit, offset]).fetchall()
        return {"total": total, "offset": offset,
                "items": [{k: r[k] for k in r.keys()} for r in rows]}
    finally:
        c.close()


@app.get("/api/chengyu/session")
async def chengyu_session(request: Request, mode: str = "meaning", limit: int = 12,
                          hsk: int = 0, only: str = "", has_story: int = 0):
    """성어 학습 세션.
    meaning  성어 → 뜻 고르기      idiom  뜻 → 성어 고르기
    blank    네 글자 중 한 글자 채우기   listen  음성 듣고 성어 고르기
    card     문제 없이 카드로 훑어보기(직역·유래까지)
    """
    uid = request.state.user_id
    mode = mode if mode in CHENGYU_MODES else "meaning"
    limit = max(1, min(50, limit))
    where, args = _cy_where(hsk, only, has_story)
    if mode == "listen":                       # 음성이 있는 것만 낼 수 있다
        where += " AND COALESCE(w.audio1,'')!=''"
    # 아직 안 외운 것 → 복습 예정 → 나머지 순으로, 같은 묶음 안에서는 무작위
    order = ("CASE WHEN COALESCE(p.box,0)>=5 THEN 2"
             " WHEN p.due_at IS NOT NULL AND p.due_at<=datetime('now') THEN 0"
             " ELSE 1 END, RANDOM()")
    c = _wdb()
    try:
        rows = c.execute(f"{_CY_SELECT} WHERE {where} ORDER BY {order} LIMIT ?",
                         [uid] + args + [limit]).fetchall()
        items = []
        for r in rows:
            d = {k: r[k] for k in r.keys()}
            if mode in ("meaning", "idiom", "listen"):
                d["choices"] = _cy_choices(c, r, mode)
            elif mode == "blank":
                d.update(_cy_blank(c, r))
            items.append(d)
        if mode in ("meaning", "idiom", "listen"):
            items = [i for i in items if len(i.get("choices") or []) == 4]
        return {"mode": mode, "count": len(items), "items": items}
    finally:
        c.close()


def _cy_choices(c, r, mode):
    """4지선다 보기 — 뜻이 겹치는 오답은 문제가 성립하지 않으므로 걸러낸다"""
    ans_ko = (r["meaning_ko"] or "").strip()
    picked, seen_ko = [], {ans_ko}
    for x in c.execute("""SELECT w.chinese, w.pinyin, w.meaning_ko FROM words w
            JOIN chengyu g ON g.word_id=w.id
            WHERE w.id!=? AND w.meaning_ko!='' ORDER BY RANDOM() LIMIT 60""", (r["id"],)):
        m = (x["meaning_ko"] or "").strip()
        if not m or m in seen_ko:
            continue
        if len(m) >= 3 and (m in ans_ko or ans_ko in m):
            continue
        seen_ko.add(m)
        picked.append({"chinese": x["chinese"], "pinyin": x["pinyin"] or "", "meaning_ko": m})
        if len(picked) >= 3:
            break
    opts = picked + [{"chinese": r["chinese"], "pinyin": r["pinyin"] or "",
                      "meaning_ko": ans_ko, "ok": 1}]
    random.shuffle(opts)
    return opts


def _cy_blank(c, r):
    """네 글자 중 한 글자를 지우고, 같은 자리에 오는 다른 글자를 오답으로 준다"""
    cn = r["chinese"]
    at = random.randrange(len(cn))
    ans = cn[at]
    cands, seen = [], set(cn)
    for x in c.execute("""SELECT w.chinese FROM words w JOIN chengyu g ON g.word_id=w.id
            WHERE w.id!=? AND length(w.chinese)=4 ORDER BY RANDOM() LIMIT 200""", (r["id"],)):
        ch = x["chinese"][at]
        if ch in seen:
            continue
        seen.add(ch)
        cands.append(ch)
        if len(cands) >= 3:
            break
    opts = cands[:3] + [ans]
    random.shuffle(opts)
    return {"blank_at": at, "blank_answer": ans, "blank_choices": opts,
            "masked": cn[:at] + "＿" + cn[at + 1:]}


@app.get("/api/chengyu/detail/{word_id}")
async def chengyu_detail(request: Request, word_id: int):
    uid = request.state.user_id
    c = _wdb()
    try:
        r = c.execute(f"{_CY_SELECT} WHERE w.id=?", (uid, word_id)).fetchone()
        if not r:
            raise HTTPException(404, "성어를 찾을 수 없습니다")
        d = {k: r[k] for k in r.keys()}
        # 같은 글자를 쓰는 다른 성어 — 글자 단위로 묶어 보면 외우기 쉽다
        rel = []
        for ch in dict.fromkeys(d["chinese"]):
            for x in c.execute("""SELECT w.id, w.chinese, w.pinyin, w.meaning_ko FROM words w
                    JOIN chengyu g ON g.word_id=w.id
                    WHERE w.id!=? AND w.chinese LIKE ? ORDER BY g.freq DESC LIMIT 3""",
                    (word_id, f"%{ch}%")):
                if any(y["id"] == x["id"] for y in rel):
                    continue
                rel.append({k: x[k] for k in x.keys()})
        d["related"] = rel[:8]
        return d
    finally:
        c.close()


@app.post("/api/chengyu/tts-batch")
async def chengyu_tts_batch(request: Request):
    """음성이 없는 성어에 XTTS 음성을 생성한다 (백그라운드, GPU 사용)"""
    global _cy_tts_running
    if _cy_tts_running:
        return {"message": "이미 진행 중", "progress": _cy_tts_progress}
    _cy_tts_running = True
    asyncio.create_task(_run_chengyu_tts())
    return {"message": "성어 음성 생성 시작", "progress": _cy_tts_progress}


@app.get("/api/chengyu/tts-status")
async def chengyu_tts_status():
    return {"running": _cy_tts_running, "progress": _cy_tts_progress}


_cy_tts_running = False
_cy_tts_progress = {"done": 0, "total": 0, "voice": ""}


async def _run_chengyu_tts():
    """성어 음성 배치 — 김준용/수민 두 목소리로 audio1/audio2 를 채운다"""
    global _cy_tts_running, _cy_tts_progress
    import aiosqlite
    voice_a = 'voice_samples/54b5509a_118a3ddf-37fe-44e8-b4af-e0783c865480.wav'
    voice_b = 'voice_samples/2a75fe0f_모바일도서검색기수민.wav'
    engine = get_engine()
    try:
        for name, ref, col in (("김준용", voice_a, "audio1"), ("수민", voice_b, "audio2")):
            async with aiosqlite.connect("database/voices.db") as db:
                cur = await db.execute(
                    f"""SELECT w.id, w.chinese FROM words w JOIN chengyu g ON g.word_id=w.id
                        WHERE w.category='성어' AND ({col} IS NULL OR {col}='')""")
                todo = await cur.fetchall()
            _cy_tts_progress = {"done": 0, "total": len(todo), "voice": name}
            print(f"[성어TTS] {name}: {len(todo)}개")
            for wid, text in todo:
                try:
                    out = str(CHINESE_AUDIO_DIR / f"cy_{col}_{wid}_{uuid.uuid4().hex[:6]}.wav")

                    def _gen(t, r, p):
                        wav = np.array(engine.tts.tts(text=t, speaker_wav=r, language="zh-cn"),
                                       dtype=np.float32)
                        sf.write(p, wav, 24000)

                    await asyncio.get_event_loop().run_in_executor(
                        None, _gen, preprocess_tts_text(text, "zh-cn"), ref, out)
                    async with aiosqlite.connect("database/voices.db") as db:
                        await db.execute(f"UPDATE words SET {col}=? WHERE id=?", (out, wid))
                        await db.commit()
                    _cy_tts_progress["done"] += 1
                except Exception as e:
                    print(f"[성어TTS] 실패 {text}: {e}")
                await asyncio.sleep(0.05)
    finally:
        _cy_tts_running = False
        print("[성어TTS] 완료")


# ─────────────────────────────────────────────────────────────
#  搭配(dāpèi) 호응 표현 — 서로 붙어 다니는 짝꿍단어 학습
#  提高+水平 / 严格+要求 처럼 "같이 쓰는 낱말 묶음"을 통째로 외운다.
#  단어·성어와 달리 words 테이블에 없는 조합이라 dapei_progress / dapei_log 를 따로 쓴다.
# ─────────────────────────────────────────────────────────────

DAPEI_MODES = ("meaning", "coll", "head", "blank", "listen", "card")
DAPEI_PATTERNS = {"VO": "동사+목적어", "AN": "형용사+명사", "NA": "명사+술어",
                  "AV": "수식어+동사", "VC": "동사+보어", "NN": "명사+명사"}


def _dp_where(pattern: str = "", topic: str = "", hsk: int = 0, only: str = "",
              has_example: int = 0):
    cond = ["d.meaning_ko!=''"]
    args = []
    if pattern in DAPEI_PATTERNS:
        cond.append("d.pattern=?"); args.append(pattern)
    if topic:
        cond.append("d.topic=?"); args.append(topic)
    if hsk:
        cond.append("(d.hsk>0 AND d.hsk<=?)"); args.append(hsk)
    if has_example:
        cond.append("COALESCE(d.example,'')!=''")
    if only == "new":
        cond.append("(p.seen IS NULL OR p.seen=0)")
    elif only == "wrong":
        cond.append("COALESCE(p.wrong,0)>0")
    elif only == "star":
        cond.append("COALESCE(p.starred,0)=1")
    elif only == "due":
        cond.append("(p.due_at IS NOT NULL AND p.due_at<=datetime('now'))")
    elif only == "learning":
        cond.append("COALESCE(p.seen,0)>0 AND COALESCE(p.box,0)<5")
    return " AND ".join(cond), args


_DP_SELECT = """SELECT d.id, d.chinese, d.head, d.head_py, d.head_ko, d.collocate, d.coll_py,
        d.pinyin, d.meaning_ko, d.pattern, d.topic, d.hsk, d.example, d.example_ko,
        d.audio1, d.audio2, d.seq,
        p.box, p.seen, p.correct, p.wrong, p.starred, p.due_at
    FROM dapei d
    LEFT JOIN dapei_progress p ON p.dapei_id=d.id AND p.user_id=?"""


@app.get("/dapei")
async def dapei_page():
    """호응(짝꿍단어) 학습 페이지"""
    return FileResponse("frontend/dapei.html", headers=_NOCACHE)


@app.get("/api/dapei/stats")
async def dapei_stats(request: Request):
    """호응 진도 요약 — 학습 시작 화면의 숫자들"""
    uid = request.state.user_id
    c = _wdb()
    try:
        total = c.execute("SELECT COUNT(*) FROM dapei").fetchone()[0]
        heads = c.execute("SELECT COUNT(DISTINCT head) FROM dapei").fetchone()[0]
        exs = c.execute("SELECT COUNT(*) FROM dapei WHERE COALESCE(example,'')!=''").fetchone()[0]
        prog = "FROM dapei d JOIN dapei_progress p ON p.dapei_id=d.id AND p.user_id=? WHERE 1=1"
        seen = c.execute(f"SELECT COUNT(*) {prog} AND p.seen>0", (uid,)).fetchone()[0]
        mastered = c.execute(f"SELECT COUNT(*) {prog} AND p.box>=5", (uid,)).fetchone()[0]
        due = c.execute(f"SELECT COUNT(*) {prog} AND p.due_at IS NOT NULL AND p.due_at<=datetime('now')",
                        (uid,)).fetchone()[0]
        wrong = c.execute(f"SELECT COUNT(*) {prog} AND p.wrong>0 AND COALESCE(p.box,0)<5",
                          (uid,)).fetchone()[0]
        star = c.execute(f"SELECT COUNT(*) {prog} AND p.starred=1", (uid,)).fetchone()[0]
        today = c.execute("""SELECT COUNT(*), COALESCE(SUM(result),0) FROM dapei_log
                             WHERE user_id=? AND date(created_at)=date('now')""", (uid,)).fetchone()
        pats = [{"k": r[0], "n": DAPEI_PATTERNS.get(r[0], r[0]), "c": r[1]} for r in
                c.execute("SELECT pattern, COUNT(*) FROM dapei GROUP BY pattern ORDER BY 2 DESC")]
        tops = [{"k": r[0], "c": r[1]} for r in
                c.execute("SELECT topic, COUNT(*) FROM dapei WHERE topic!='' GROUP BY topic ORDER BY 2 DESC")]
        return {"total": total, "heads": heads, "examples": exs,
                "seen": seen, "mastered": mastered, "learning": max(0, seen - mastered),
                "untouched": total - seen, "due": due, "wrong": wrong, "starred": star,
                "today_count": today[0], "today_correct": today[1],
                "patterns": pats, "topics": tops}
    finally:
        c.close()


@app.get("/api/dapei/list")
async def dapei_list(request: Request, q: str = "", pattern: str = "", topic: str = "",
                     hsk: int = 0, only: str = "", has_example: int = 0,
                     sort: str = "seq", offset: int = 0, limit: int = 60):
    """호응 표현 목록 · 검색 (한자 · 병음 · 한국어 뜻)"""
    uid = request.state.user_id
    limit = max(1, min(200, limit))
    where, args = _dp_where(pattern, topic, hsk, only, has_example)
    if q:
        qq = q.strip()
        where += (" AND (d.chinese LIKE ? OR d.head LIKE ? OR d.collocate LIKE ?"
                  " OR d.pinyin LIKE ? OR REPLACE(d.pinyin,' ','') LIKE ?"
                  " OR d.meaning_ko LIKE ? OR d.head_ko LIKE ?)")
        args += [f"%{qq}%"] * 4 + [f"%{qq.replace(' ', '')}%"] + [f"%{qq}%"] * 2
    od = {"random": "RANDOM()", "hanzi": "d.chinese", "hsk": "d.hsk, d.seq",
          "due": "COALESCE(p.due_at,'9999'), d.seq"}.get(sort, "d.seq")
    c = _wdb()
    try:
        total = c.execute(f"""SELECT COUNT(*) FROM dapei d
            LEFT JOIN dapei_progress p ON p.dapei_id=d.id AND p.user_id=? WHERE {where}""",
            [uid] + args).fetchone()[0]
        rows = c.execute(f"{_DP_SELECT} WHERE {where} ORDER BY {od} LIMIT ? OFFSET ?",
                         [uid] + args + [limit, offset]).fetchall()
        return {"total": total, "offset": offset,
                "items": [{k: r[k] for k in r.keys()} for r in rows]}
    finally:
        c.close()


@app.get("/api/dapei/heads")
async def dapei_heads(request: Request, pattern: str = "", topic: str = "", q: str = ""):
    """머리말 묶음 목록 — 提高 아래에 붙는 짝이 몇 개인지까지.
    호응은 '한 낱말이 거느리는 짝을 통째로' 보는 게 가장 잘 외워진다."""
    uid = request.state.user_id
    cond, args = ["1=1"], []
    if pattern in DAPEI_PATTERNS:
        cond.append("pattern=?"); args.append(pattern)
    if topic:
        cond.append("topic=?"); args.append(topic)
    if q:
        cond.append("(head LIKE ? OR head_ko LIKE ? OR head_py LIKE ?)")
        args += [f"%{q}%"] * 3
    c = _wdb()
    try:
        rows = c.execute(f"""SELECT d.head, d.head_py, d.head_ko, d.pattern, d.topic,
                MIN(d.hsk) hsk, COUNT(*) n, MIN(d.seq) seq,
                SUM(CASE WHEN COALESCE(p.box,0)>=5 THEN 1 ELSE 0 END) mastered,
                SUM(CASE WHEN COALESCE(p.seen,0)>0 THEN 1 ELSE 0 END) seen
            FROM dapei d LEFT JOIN dapei_progress p ON p.dapei_id=d.id AND p.user_id=?
            WHERE {' AND '.join(cond)} GROUP BY d.head ORDER BY MIN(d.seq)""",
            [uid] + args).fetchall()
        return {"total": len(rows), "heads": [{k: r[k] for k in r.keys()} for r in rows]}
    finally:
        c.close()


@app.get("/api/dapei/group/{head}")
async def dapei_group(request: Request, head: str):
    """한 머리말이 거느리는 짝 전체 (提高 → 水平·能力·效率…)"""
    uid = request.state.user_id
    c = _wdb()
    try:
        rows = c.execute(f"{_DP_SELECT} WHERE d.head=? ORDER BY d.seq", (uid, head)).fetchall()
        if not rows:
            raise HTTPException(404, "이 머리말의 호응 표현이 없습니다")
        r0 = rows[0]
        return {"head": head, "head_py": r0["head_py"], "head_ko": r0["head_ko"],
                "pattern": r0["pattern"], "pattern_ko": DAPEI_PATTERNS.get(r0["pattern"], ""),
                "topic": r0["topic"], "count": len(rows),
                "items": [{k: r[k] for k in r.keys()} for r in rows]}
    finally:
        c.close()


@app.get("/api/dapei/session")
async def dapei_session(request: Request, mode: str = "meaning", limit: int = 12,
                        pattern: str = "", topic: str = "", hsk: int = 0,
                        only: str = "", has_example: int = 0, head: str = ""):
    """호응 학습 세션.
    meaning  호응 표현 → 뜻 고르기        coll   머리말+뜻 → 어울리는 짝 고르기
    head     짝+뜻 → 어울리는 머리말 고르기  blank  예문 빈칸 채우기
    listen   음성 듣고 고르기              card   문제 없이 카드로 훑어보기
    """
    uid = request.state.user_id
    mode = mode if mode in DAPEI_MODES else "meaning"
    limit = max(1, min(50, limit))
    where, args = _dp_where(pattern, topic, hsk, only, has_example)
    if head:
        where += " AND d.head=?"; args.append(head)
    if mode == "blank":                       # 예문이 있어야 빈칸을 낼 수 있다
        where += " AND COALESCE(d.example,'')!=''"
    if mode == "listen":
        where += " AND COALESCE(d.audio1,'')!=''"
    order = ("CASE WHEN COALESCE(p.box,0)>=5 THEN 2"
             " WHEN p.due_at IS NOT NULL AND p.due_at<=datetime('now') THEN 0"
             " ELSE 1 END, RANDOM()")
    c = _wdb()
    try:
        rows = c.execute(f"{_DP_SELECT} WHERE {where} ORDER BY {order} LIMIT ?",
                         [uid] + args + [limit]).fetchall()
        items = []
        for r in rows:
            d = {k: r[k] for k in r.keys()}
            if mode in ("meaning", "listen"):
                d["choices"] = _dp_choices(c, r, mode)
            elif mode in ("coll", "head"):
                d["choices"] = _dp_pair_choices(c, r, mode)
            elif mode == "blank":
                b = _dp_blank(c, r)
                if not b:
                    continue
                d.update(b)
            items.append(d)
        if mode in ("meaning", "listen", "coll", "head"):
            items = [i for i in items if len(i.get("choices") or []) == 4]
        return {"mode": mode, "count": len(items), "items": items}
    finally:
        c.close()


def _dp_choices(c, r, mode):
    """뜻 고르기 보기 — 뜻이 겹치는 오답은 문제가 성립하지 않으므로 걸러낸다"""
    ans = (r["meaning_ko"] or "").strip()
    picked, seen = [], {ans}
    for x in c.execute("""SELECT chinese, pinyin, meaning_ko FROM dapei
            WHERE id!=? AND meaning_ko!='' ORDER BY RANDOM() LIMIT 80""", (r["id"],)):
        m = (x["meaning_ko"] or "").strip()
        if not m or m in seen:
            continue
        if len(m) >= 3 and (m in ans or ans in m):
            continue
        seen.add(m)
        picked.append({"chinese": x["chinese"], "pinyin": x["pinyin"] or "", "meaning_ko": m})
        if len(picked) >= 3:
            break
    opts = picked + [{"chinese": r["chinese"], "pinyin": r["pinyin"] or "",
                      "meaning_ko": ans, "ok": 1}]
    random.shuffle(opts)
    return opts


def _dp_pair_choices(c, r, mode):
    """호응 학습의 핵심 — 짝을 고르는 문제.
    coll : 提高 + '수준을 높이다' → 水平 / 年龄 / 意思 / 味道
    head : 水平 + '수준을 높이다' → 提高 / 增加 / 举行 / 打破
    오답은 '실제로 이 낱말과는 붙지 않는' 것만 고른다(다른 짝이 정답이 되면 안 되므로).
    같은 유형(동사+목적어 등)에서 뽑아야 문제가 그럴듯해진다."""
    if mode == "coll":
        ans, col, other = r["collocate"], "collocate", "head"
        py_col = "coll_py"
    else:
        ans, col, other = r["head"], "head", "collocate"
        py_col = "head_py"
    # 이 낱말과 실제로 짝을 이루는 것들 — 오답 후보에서 반드시 뺀다
    mine = {x[0] for x in c.execute(
        f"SELECT {col} FROM dapei WHERE {other}=?", (r[other],))}
    mine.add(ans)
    picked, seen = [], set(mine)
    for x in c.execute(
            f"""SELECT DISTINCT {col} v, {py_col} vp FROM dapei
                WHERE pattern=? AND {other}!=? ORDER BY RANDOM() LIMIT 120""",
            (r["pattern"], r[other])):
        v = x["v"]
        if v in seen:
            continue
        seen.add(v)
        picked.append({"text": v, "py": x["vp"] or "", "chinese": (
            r["head"] + v if mode == "coll" else v + r["collocate"])})
        if len(picked) >= 3:
            break
    opts = picked + [{"text": ans, "py": r[py_col] or "", "chinese": r["chinese"], "ok": 1}]
    random.shuffle(opts)
    return opts


def _dp_blank(c, r):
    """예문 속에서 호응의 한쪽을 지운다 — 문장 안에서 쓰이는 모습으로 익힌다.
    중국어 호응은 提高了不少 처럼 사이에 다른 말이 끼어들기 때문에
    표현 전체가 아니라 '머리말' 또는 '짝' 한쪽만 지운다."""
    ex = (r["example"] or "").strip()
    if not ex:
        return None
    if r["head"] in ex:
        mode, ans, py_col, other = "head", r["head"], "head_py", "collocate"
    elif r["collocate"] in ex:
        mode, ans, py_col, other = "coll", r["collocate"], "coll_py", "head"
    else:
        return None
    col = "head" if mode == "head" else "collocate"
    mine = {x[0] for x in c.execute(f"SELECT {col} FROM dapei WHERE {other}=?", (r[other],))}
    mine.add(ans)
    picked, seen = [], set(mine)
    for x in c.execute(f"""SELECT DISTINCT {col} v FROM dapei
            WHERE pattern=? AND {other}!=? ORDER BY RANDOM() LIMIT 120""",
            (r["pattern"], r[other])):
        if x["v"] in seen or x["v"] in ex:
            continue
        seen.add(x["v"])
        picked.append(x["v"])
        if len(picked) >= 3:
            break
    if len(picked) < 3:
        return None
    opts = picked + [ans]
    random.shuffle(opts)
    return {"blank_part": mode, "blank_answer": ans,
            "blank_choices": [{"text": o, "ok": 1 if o == ans else 0} for o in opts],
            "masked": ex.replace(ans, "＿" * len(ans), 1)}


@app.get("/api/dapei/detail/{dapei_id}")
async def dapei_detail(request: Request, dapei_id: int):
    """호응 상세 — 같은 머리말을 쓰는 짝, 같은 짝을 받는 머리말을 함께 보여준다"""
    uid = request.state.user_id
    c = _wdb()
    try:
        r = c.execute(f"{_DP_SELECT} WHERE d.id=?", (uid, dapei_id)).fetchone()
        if not r:
            raise HTTPException(404, "호응 표현을 찾을 수 없습니다")
        d = {k: r[k] for k in r.keys()}
        d["pattern_ko"] = DAPEI_PATTERNS.get(d["pattern"], "")
        d["same_head"] = [{k: x[k] for k in x.keys()} for x in c.execute(
            """SELECT id, chinese, collocate, coll_py, meaning_ko FROM dapei
               WHERE head=? AND id!=? ORDER BY seq LIMIT 20""", (d["head"], dapei_id))]
        d["same_coll"] = [{k: x[k] for k in x.keys()} for x in c.execute(
            """SELECT id, chinese, head, head_py, meaning_ko FROM dapei
               WHERE collocate=? AND id!=? ORDER BY seq LIMIT 12""", (d["collocate"], dapei_id))]
        return d
    finally:
        c.close()


@app.post("/api/dapei/answer")
async def dapei_answer(request: Request):
    """정답/오답 반영 — 단어·성어와 같은 라이트너 박스 간격을 쓴다"""
    uid = request.state.user_id
    data = await request.json()
    did = int(data.get("dapei_id") or 0)
    ok = 1 if data.get("result") else 0
    mode = (data.get("mode") or "srs")[:20]
    ms = int(data.get("ms") or 0)
    if not did:
        raise HTTPException(400, "dapei_id 필요")
    c = _wdb()
    try:
        row = c.execute("SELECT * FROM dapei_progress WHERE user_id=? AND dapei_id=?",
                        (uid, did)).fetchone()
        box = row["box"] if row else 0
        streak = row["streak"] if row else 0
        best = row["best_streak"] if row else 0
        if ok:
            box = min(len(_SRS_INTERVALS) - 1, box + 1)
            streak += 1
            best = max(best, streak)
        else:
            box = max(0, box - 1)
            streak = 0
        days = _SRS_INTERVALS[box]
        if row:
            c.execute("""UPDATE dapei_progress SET box=?, due_at=datetime('now', ?),
                         interval_days=?, seen=seen+1, correct=correct+?, wrong=wrong+?,
                         streak=?, best_streak=?, last_at=datetime('now')
                         WHERE user_id=? AND dapei_id=?""",
                      (box, f"+{days} day", days, ok, 1 - ok, streak, best, uid, did))
        else:
            c.execute("""INSERT INTO dapei_progress
                (user_id,dapei_id,box,due_at,interval_days,seen,correct,wrong,streak,best_streak,last_at)
                VALUES (?,?,?,datetime('now', ?),?,1,?,?,?,?,datetime('now'))""",
                      (uid, did, box, f"+{days} day", days, ok, 1 - ok, streak, best))
        c.execute("""INSERT INTO dapei_log (user_id,dapei_id,mode,result,ms,created_at)
                     VALUES (?,?,?,?,?,datetime('now'))""", (uid, did, mode, ok, ms))
        c.commit()
        return {"ok": True, "box": box, "next_days": days, "streak": streak}
    finally:
        c.close()


@app.post("/api/dapei/star")
async def dapei_star(request: Request):
    uid = request.state.user_id
    data = await request.json()
    did = int(data.get("dapei_id") or 0)
    on = 1 if data.get("on") else 0
    c = _wdb()
    try:
        r = c.execute("SELECT 1 FROM dapei_progress WHERE user_id=? AND dapei_id=?",
                      (uid, did)).fetchone()
        if r:
            c.execute("UPDATE dapei_progress SET starred=? WHERE user_id=? AND dapei_id=?",
                      (on, uid, did))
        else:
            c.execute("INSERT INTO dapei_progress (user_id,dapei_id,starred) VALUES (?,?,?)",
                      (uid, did, on))
        c.commit()
        return {"ok": True, "starred": on}
    finally:
        c.close()


_dp_tts_running = False
_dp_tts_progress = {"done": 0, "total": 0, "voice": ""}


@app.post("/api/dapei/tts-batch")
async def dapei_tts_batch(request: Request):
    """음성이 없는 호응 표현에 XTTS 음성을 생성한다 (백그라운드, GPU 사용)"""
    global _dp_tts_running
    if _dp_tts_running:
        return {"message": "이미 진행 중", "progress": _dp_tts_progress}
    _dp_tts_running = True
    asyncio.create_task(_run_dapei_tts())
    return {"message": "호응 음성 생성 시작", "progress": _dp_tts_progress}


@app.get("/api/dapei/tts-status")
async def dapei_tts_status():
    return {"running": _dp_tts_running, "progress": _dp_tts_progress}


async def _run_dapei_tts():
    """호응 음성 배치 — 성어와 같은 두 목소리로 audio1/audio2 를 채운다"""
    global _dp_tts_running, _dp_tts_progress
    import aiosqlite
    voice_a = 'voice_samples/54b5509a_118a3ddf-37fe-44e8-b4af-e0783c865480.wav'
    voice_b = 'voice_samples/2a75fe0f_모바일도서검색기수민.wav'
    engine = get_engine()
    try:
        for name, ref, col in (("김준용", voice_a, "audio1"), ("수민", voice_b, "audio2")):
            async with aiosqlite.connect("database/voices.db") as db:
                cur = await db.execute(
                    f"SELECT id, chinese FROM dapei WHERE {col} IS NULL OR {col}=''")
                todo = await cur.fetchall()
            _dp_tts_progress = {"done": 0, "total": len(todo), "voice": name}
            print(f"[호응TTS] {name}: {len(todo)}개")
            for did, text in todo:
                try:
                    out = str(CHINESE_AUDIO_DIR / f"dp_{col}_{did}_{uuid.uuid4().hex[:6]}.wav")

                    def _gen(t, r, p):
                        wav = np.array(engine.tts.tts(text=t, speaker_wav=r, language="zh-cn"),
                                       dtype=np.float32)
                        sf.write(p, wav, 24000)

                    await asyncio.get_event_loop().run_in_executor(
                        None, _gen, preprocess_tts_text(text, "zh-cn"), ref, out)
                    async with aiosqlite.connect("database/voices.db") as db:
                        await db.execute(f"UPDATE dapei SET {col}=? WHERE id=?", (out, did))
                        await db.commit()
                    _dp_tts_progress["done"] += 1
                except Exception as e:
                    print(f"[호응TTS] 실패 {text}: {e}")
                await asyncio.sleep(0.05)
    finally:
        _dp_tts_running = False
        print("[호응TTS] 완료")


@app.get("/api/chinese/subjects")
async def list_subjects(request: Request):
    """과목 + 주차 목록 조회"""
    user_id = request.state.user_id
    subjects = await get_chinese_subjects(user_id)
    return {"subjects": subjects}


@app.get("/api/chinese/cards-by-week")
async def cards_by_week(request: Request, subject: str = "", week: int = 0):
    """과목/주차별 카드 조회.
    선택지만 남아 문제 꼴을 잃은 카드는 숨긴다(hidden=1) — 그 문제는 /api/chinese/reading 이 낸다."""
    user_id = request.state.user_id
    cards = await get_chinese_cards(user_id, subject=subject or None,
                                     week=week if week > 0 else None)
    c = _wdb()
    try:
        hid = {r["id"] for r in c.execute(
            "SELECT id FROM chinese_cards WHERE COALESCE(hidden,0)=1")}
    except Exception:
        hid = set()
    finally:
        c.close()
    if hid:
        cards = [x for x in cards if x.get("id") not in hid]
    return {"cards": cards}


@app.get("/api/chinese/lecture-audio")
async def lecture_audio(request: Request, subject: str = "", week: int = 0, class_num: int = 0):
    """교수님이 직접 읽어 주신 본문 음성. 문장(card_id)에 붙은 것과 교시 전체용이 있다."""
    c = _wdb()
    try:
        cond, args = [], []
        if subject: cond.append("subject=?"); args.append(subject)
        if week:    cond.append("week=?");    args.append(week)
        if class_num: cond.append("class_num=?"); args.append(class_num)
        where = (" WHERE " + " AND ".join(cond)) if cond else ""
        rows = c.execute(f"""SELECT id,week,class_num,card_id,title,path
                             FROM lecture_audio{where} ORDER BY week,class_num,id""", args).fetchall()
    except Exception:
        return {"audios": []}
    finally:
        c.close()
    return {"audios": [dict(r) for r in rows]}


@app.get("/api/chinese/writing")
async def writing_puzzles(request: Request, subject: str = "", week: int = 0, class_num: int = 0):
    """HSK 쓰기 1부분 — 흩어 놓은 제시어를 바른 순서로 맞추는 문제.
    정답 순서로 저장해 두고, 보여 줄 때 섞는다."""
    import random as _rnd
    c = _wdb()
    try:
        cond, args = [], []
        if subject: cond.append("subject=?"); args.append(subject)
        if week:    cond.append("week=?");    args.append(week)
        if class_num: cond.append("class_num=?"); args.append(class_num)
        where = (" WHERE " + " AND ".join(cond)) if cond else ""
        rows = c.execute(f"""SELECT id,week,class_num,level,answer,tokens,meaning_ko,
                                    COALESCE(kind,'word') kind
                             FROM writing_puzzles{where}
                             ORDER BY week,class_num,sort_order""", args).fetchall()
    except Exception:
        return {"puzzles": []}
    finally:
        c.close()
    out = []
    for r in rows:
        try:
            toks = json.loads(r["tokens"])
        except Exception:
            continue
        if len(toks) < 2: continue
        sh = toks[:]
        for _ in range(8):                      # 정답 순서와 같아지지 않게 섞는다
            _rnd.shuffle(sh)
            if sh != toks: break
        out.append({"id": r["id"], "week": r["week"], "cls": r["class_num"],
                    "level": r["level"], "answer": r["answer"],
                    # word=낱말을 늘어놓아 한 문장 만들기 / order=세 도막을 차례대로 놓기
                    "kind": r["kind"],
                    "tokens": sh, "order": toks, "ko": r["meaning_ko"] or ""})
    return {"puzzles": out}


@app.get("/api/chinese/reading")
async def reading_items(request: Request, subject: str = "", week: int = 0, class_num: int = 0):
    """독해 2부분 — 지문과 선택지가 교안에서 다른 쪽에 실려 카드로는 흩어져 있었다.
    교안에서 통째로 뽑아 문제 꼴로 되돌린 것. ⚠️ 교안에 정답이 없어 정답은 담지 않는다."""
    c = _wdb()
    try:
        cond, args = [], []
        if subject: cond.append("subject=?"); args.append(subject)
        if week:    cond.append("week=?");    args.append(week)
        if class_num: cond.append("class_num=?"); args.append(class_num)
        where = (" WHERE " + " AND ".join(cond)) if cond else ""
        rows = c.execute(f"""SELECT id,week,class_num,page,no,passage,options,
                                    meaning_ko,options_ko,passage_py,options_py
                             FROM reading_items{where} ORDER BY week,class_num,page""", args).fetchall()
    except Exception:
        return {"items": []}
    finally:
        c.close()
    out = []
    for r in rows:
        try:
            op = json.loads(r["options"])
        except Exception:
            continue
        try:
            opk = json.loads(r["options_ko"]) if r["options_ko"] else []
        except Exception:
            opk = []
        try:
            opp = json.loads(r["options_py"]) if r["options_py"] else []
        except Exception:
            opp = []
        out.append({"id": r["id"], "week": r["week"], "cls": r["class_num"],
                    "no": r["no"], "passage": r["passage"], "options": op,
                    "ko": r["meaning_ko"] or "", "options_ko": opk,
                    "py": r["passage_py"] or "", "options_py": opp})
    return {"items": out}


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


_TB_TEXT: dict = {}


def _tb_pages_text(pdf: str) -> list:
    """교안 쪽마다의 글. 한 번 떠 두고 담아 둔다 (169쪽에 1초 남짓)."""
    if pdf in _TB_TEXT:
        return _TB_TEXT[pdf]
    import subprocess
    try:
        n = int(subprocess.run(["pdfinfo", pdf], capture_output=True, text=True)
                .stdout.split("Pages:")[1].split()[0])
    except Exception:
        _TB_TEXT[pdf] = []
        return []
    out = []
    for p in range(1, n + 1):
        try:
            t = subprocess.run(["pdftotext", "-layout", "-f", str(p), "-l", str(p), pdf, "-"],
                               capture_output=True, text=True).stdout
        except Exception:
            t = ""
        out.append(t)
    _TB_TEXT[pdf] = out
    return out


@app.get("/api/chinese/textbook/search")
async def textbook_search(subject: str, q: str = "", week: int = 0):
    """교안 글 찾기 — 찾은 쪽이 몇 주 몇 교시 몇 쪽째인지 함께 준다."""
    q = (q or "").strip()
    if len(q) < 2:
        return {"hits": []}
    sections = _load_sections()
    secs = [s for s in sections if s["subject"] == subject and (not week or s["week"] == week)]
    if not secs:
        return {"hits": []}
    hits = []
    for sec in secs:
        pages = _tb_pages_text(sec["pdf"])
        for i in range(sec["start_page"], sec["end_page"] + 1):
            if i >= len(pages):
                break
            t = pages[i]
            k = t.find(q)
            if k < 0:
                continue
            snip = " ".join(t[max(0, k - 40):k + len(q) + 60].split())
            hits.append({"week": sec["week"], "cls": sec["class_num"],
                         "page_idx": i - sec["start_page"], "page": i + 1,
                         "title": sec.get("title", ""), "snippet": snip})
            if len(hits) >= 60:
                return {"hits": hits}
    return {"hits": hits}


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

    SUBJECTS = ['기초중국어', '기초중국어2', '생활중국어', '중국어발음연습', '발음연습', '중국인의생활과문화', '신HSK쓰기독해', '중국어단어장']
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
    return FileResponse("frontend/index.html", headers=_NOCACHE)


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
