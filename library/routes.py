"""
library/routes.py
음원 라이브러리 API — 개인/공유/배포 폴더 시스템
"""

from fastapi import APIRouter, Form, HTTPException, Depends, Request
from auth.dependencies import get_current_user
from database.db import (
    get_personal_voices, get_published_voices,
    copy_voice_to_personal, copy_voice_to_shared,
    update_voice_meta, get_voice,
    create_shared_folder, get_shared_folders_for_user, get_shared_folder,
    update_shared_folder, delete_shared_folder, get_shared_folder_voices,
    check_folder_permission, add_folder_member, remove_folder_member, get_folder_members,
    create_publish_review, get_pending_reviews,
    approve_publish_review, reject_publish_review, publish_voice_update,
    get_all_users,
)

router = APIRouter(prefix="/api/library", tags=["library"])


# ── 개인 폴더 ──

@router.get("/personal")
async def list_personal(user: dict = Depends(get_current_user)):
    """개인 폴더 음원 목록"""
    voices = await get_personal_voices(user["id"])
    return {"voices": voices}


# ── 배포 폴더 ──

@router.get("/published")
async def list_published(user: dict = Depends(get_current_user)):
    """배포 폴더 음원 목록 (모든 회원 접근)"""
    voices = await get_published_voices()
    return {"voices": voices}


@router.post("/copy-to-personal")
async def api_copy_to_personal(
    voice_id: int = Form(...),
    user: dict = Depends(get_current_user),
):
    """공유/배포 음원을 내 개인 폴더로 복사"""
    new_id = await copy_voice_to_personal(voice_id, user["id"])
    if not new_id:
        raise HTTPException(404, "음원을 찾을 수 없습니다")
    return {"success": True, "voice_id": new_id, "message": "개인 폴더로 복사 완료!"}


# ── 공유 폴더 ──

@router.get("/shared/folders")
async def list_shared_folders(user: dict = Depends(get_current_user)):
    """사용자가 접근 가능한 공유 폴더 목록"""
    is_admin = user["level"] <= 10
    folders = await get_shared_folders_for_user(user["id"], is_admin)
    return {"folders": folders}


@router.post("/shared/folders")
async def create_folder(
    name: str = Form(...),
    description: str = Form(""),
    user: dict = Depends(get_current_user),
):
    """공유 폴더 생성 (관리자 또는 권한 있는 사용자)"""
    if user["level"] > 50:
        raise HTTPException(403, "공유 폴더 생성 권한이 없습니다")
    folder_id = await create_shared_folder(name, description, user["id"])
    return {"success": True, "folder_id": folder_id, "message": f"'{name}' 공유 폴더 생성!"}


@router.put("/shared/folders/{folder_id}")
async def update_folder(
    folder_id: int,
    name: str = Form(...),
    description: str = Form(""),
    user: dict = Depends(get_current_user),
):
    """공유 폴더 수정"""
    perm = await check_folder_permission(folder_id, user["id"], user["level"] <= 10)
    if perm not in ("admin",):
        raise HTTPException(403, "수정 권한이 없습니다")
    await update_shared_folder(folder_id, name, description)
    return {"success": True, "message": "폴더 수정 완료"}


@router.delete("/shared/folders/{folder_id}")
async def remove_folder(
    folder_id: int,
    user: dict = Depends(get_current_user),
):
    """공유 폴더 삭제"""
    perm = await check_folder_permission(folder_id, user["id"], user["level"] <= 10)
    if perm not in ("admin",):
        raise HTTPException(403, "삭제 권한이 없습니다")
    await delete_shared_folder(folder_id)
    return {"success": True, "message": "폴더 삭제 완료"}


@router.get("/shared/folders/{folder_id}/voices")
async def list_folder_voices(
    folder_id: int,
    user: dict = Depends(get_current_user),
):
    """공유 폴더 내 음원 목록"""
    perm = await check_folder_permission(folder_id, user["id"], user["level"] <= 10)
    if not perm:
        raise HTTPException(403, "접근 권한이 없습니다")
    folder = await get_shared_folder(folder_id)
    voices = await get_shared_folder_voices(folder_id)
    return {"folder": folder, "voices": voices, "permission": perm}


@router.post("/copy-to-shared")
async def api_copy_to_shared(
    voice_id: int = Form(...),
    folder_id: int = Form(...),
    user: dict = Depends(get_current_user),
):
    """개인 음원을 공유 폴더로 복사"""
    perm = await check_folder_permission(folder_id, user["id"], user["level"] <= 10)
    if perm not in ("admin", "edit"):
        raise HTTPException(403, "편집 권한이 없습니다")
    new_id = await copy_voice_to_shared(voice_id, folder_id, user["id"])
    if not new_id:
        raise HTTPException(404, "음원을 찾을 수 없습니다")
    return {"success": True, "voice_id": new_id, "message": "공유 폴더로 복사 완료!"}


# ── 공유 폴더 멤버 관리 ──

@router.get("/shared/folders/{folder_id}/members")
async def list_members(
    folder_id: int,
    user: dict = Depends(get_current_user),
):
    """공유 폴더 멤버 목록"""
    perm = await check_folder_permission(folder_id, user["id"], user["level"] <= 10)
    if not perm:
        raise HTTPException(403, "접근 권한이 없습니다")
    members = await get_folder_members(folder_id)
    return {"members": members}


@router.post("/shared/folders/{folder_id}/members")
async def add_member(
    folder_id: int,
    user_id: int = Form(...),
    permission: str = Form("view"),
    user: dict = Depends(get_current_user),
):
    """공유 폴더에 멤버 추가"""
    perm = await check_folder_permission(folder_id, user["id"], user["level"] <= 10)
    if perm not in ("admin",):
        raise HTTPException(403, "멤버 관리 권한이 없습니다")
    if permission not in ("view", "edit", "admin"):
        raise HTTPException(400, "권한은 view, edit, admin 중 하나여야 합니다")
    await add_folder_member(folder_id, user_id, permission)
    return {"success": True, "message": "멤버 추가 완료"}


@router.delete("/shared/folders/{folder_id}/members/{member_user_id}")
async def remove_member(
    folder_id: int,
    member_user_id: int,
    user: dict = Depends(get_current_user),
):
    """공유 폴더에서 멤버 제거"""
    perm = await check_folder_permission(folder_id, user["id"], user["level"] <= 10)
    if perm not in ("admin",):
        raise HTTPException(403, "멤버 관리 권한이 없습니다")
    await remove_folder_member(folder_id, member_user_id)
    return {"success": True, "message": "멤버 제거 완료"}


# ── 음원 메타 수정 ──

@router.post("/voice/{voice_id}/meta")
async def update_meta(
    voice_id: int,
    name: str = Form(None),
    description: str = Form(None),
    can_generate: int = Form(None),
    user: dict = Depends(get_current_user),
):
    """음원 메타데이터 수정 (이름, 설명, 생성 권한)"""
    voice = await get_voice(voice_id)
    if not voice:
        raise HTTPException(404, "음원을 찾을 수 없습니다")

    # 본인 음원이거나 관리자만 수정 가능
    if voice.get("user_id") != user["id"] and user["level"] > 10:
        raise HTTPException(403, "수정 권한이 없습니다")

    # can_generate는 관리자만 변경 가능
    updates = {}
    if name is not None:
        updates["name"] = name
    if description is not None:
        updates["description"] = description
    if can_generate is not None:
        if user["level"] > 10:
            raise HTTPException(403, "음성 생성 권한은 관리자만 부여 가능합니다")
        updates["can_generate"] = can_generate

    if updates:
        await update_voice_meta(voice_id, **updates)
    return {"success": True, "message": "수정 완료"}


# ── 배포 심사 ──

@router.post("/publish/request")
async def request_publish(
    voice_id: int = Form(...),
    user: dict = Depends(get_current_user),
):
    """배포 심사 요청"""
    voice = await get_voice(voice_id)
    if not voice:
        raise HTTPException(404, "음원을 찾을 수 없습니다")

    # 공유 폴더의 음원만 배포 요청 가능
    if voice.get("folder_type") != "shared":
        raise HTTPException(400, "공유 폴더의 음원만 배포 요청 가능합니다")

    # 권한 확인: 폴더 admin이거나 시스템 관리자
    folder_id = voice.get("shared_folder_id")
    perm = await check_folder_permission(folder_id, user["id"], user["level"] <= 10)
    if perm not in ("admin", "edit"):
        raise HTTPException(403, "배포 요청 권한이 없습니다")

    review_id = await create_publish_review(voice_id, folder_id, user["id"])
    return {"success": True, "review_id": review_id, "message": "배포 심사 요청 완료!"}


@router.get("/publish/reviews")
async def list_reviews(user: dict = Depends(get_current_user)):
    """대기 중인 배포 심사 목록 (관리자용)"""
    if user["level"] > 10:
        raise HTTPException(403, "관리자만 조회 가능합니다")
    reviews = await get_pending_reviews()
    return {"reviews": reviews}


@router.post("/publish/approve/{review_id}")
async def approve_review(
    review_id: int,
    review_note: str = Form(""),
    user: dict = Depends(get_current_user),
):
    """배포 심사 승인 (관리자용)"""
    if user["level"] > 10:
        raise HTTPException(403, "관리자만 승인 가능합니다")
    result = await approve_publish_review(review_id, user["id"], review_note)
    if not result:
        raise HTTPException(404, "심사 요청을 찾을 수 없습니다")
    return {"success": True, "message": "배포 승인 완료!"}


@router.post("/publish/reject/{review_id}")
async def reject_review(
    review_id: int,
    review_note: str = Form(""),
    user: dict = Depends(get_current_user),
):
    """배포 심사 거절 (관리자용)"""
    if user["level"] > 10:
        raise HTTPException(403, "관리자만 거절 가능합니다")
    result = await reject_publish_review(review_id, user["id"], review_note)
    if not result:
        raise HTTPException(404, "심사 요청을 찾을 수 없습니다")
    return {"success": True, "message": "배포 거절 완료"}


@router.post("/publish/update/{voice_id}")
async def update_published(
    voice_id: int,
    user: dict = Depends(get_current_user),
):
    """배포된 음원 업데이트 (버전 증가, 관리자용)"""
    if user["level"] > 10:
        raise HTTPException(403, "관리자만 업데이트 가능합니다")
    result = await publish_voice_update(voice_id)
    if not result:
        raise HTTPException(404, "음원을 찾을 수 없습니다")
    return {"success": True, "message": "배포 업데이트 완료!"}


# ── 사용자 목록 (멤버 추가용) ──

@router.get("/users-list")
async def users_for_member(user: dict = Depends(get_current_user)):
    """멤버 추가용 사용자 목록 (간략)"""
    if user["level"] > 50:
        raise HTTPException(403, "권한이 없습니다")
    users = await get_all_users()
    return {"users": [{"id": u["id"], "email": u["email"], "display_name": u["display_name"]} for u in users]}
