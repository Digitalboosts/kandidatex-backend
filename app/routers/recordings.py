import re

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from ..deps import CurrentUser, Session, assert_can_manage
from ..errors import ApiError, not_found
from ..ids import new_id
from ..models import Interview, Recording, RecordingStatus, UserRole, utcnow
from ..schemas import (
    CompleteRecordingRequest,
    CreateRecordingRequest,
    RecordingDTO,
    RecordingEnvelope,
    RecordingListResponse,
)
from ..serializers import iso
from ..services import recordings as storage
from ..services.interviews import assert_not_ended, get_interview_or_404
from ._docs import errors

router = APIRouter(prefix="/api/recordings", tags=["Recordings"])

MAX_CHUNK_BYTES = 16 * 1024 * 1024  # keep in step with the middleware limit
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


def recording_dto(row: Recording) -> RecordingDTO:
    return RecordingDTO(
        id=row.id,
        interview_id=row.interview_id,
        interview_title=row.interview.title,
        candidate_name=row.interview.candidate_name,
        status=row.status.value,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        duration_seconds=row.duration_seconds,
        started_at=iso(row.started_at),  # type: ignore[arg-type]
        ended_at=iso(row.ended_at),
        created_at=iso(row.created_at),  # type: ignore[arg-type]
        video_url=f"/api/recordings/{row.id}/video" if row.status == RecordingStatus.READY else None,
    )


async def _owned(session, user, recording_id: str) -> Recording:
    row = await session.get(Recording, recording_id)
    if row is None:
        raise not_found("Recording not found")
    assert_can_manage(user, row.interview)  # same rule as the interview itself
    return row


@router.post(
    "",
    status_code=201,
    response_model=RecordingEnvelope,
    summary="Start a recording",
    description="Creates the recording row. Then `PUT /{id}/chunks/{n}` for n = 0, 1, 2 ... and finish with "
    "`POST /{id}/complete`.",
    responses=errors(400, 401, 403, 404, 410, 415, 503),
)
async def start_recording(body: CreateRecordingRequest, user: CurrentUser, session: Session) -> RecordingEnvelope:
    interview = await get_interview_or_404(session, body.interview_id)
    assert_can_manage(user, interview)
    assert_not_ended(interview)
    recording_id = new_id()
    row = Recording(
        id=recording_id,
        interview_id=interview.id,
        status=RecordingStatus.RECORDING,
        blob_path=storage.blob_path(interview.interviewer_id, interview.id, recording_id, body.content_type),
        content_type=body.content_type,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return RecordingEnvelope(recording=recording_dto(row))


@router.put(
    "/{recording_id}/chunks/{index}",
    status_code=204,
    summary="Upload one chunk",
    description="Raw video bytes as the body (any Content-Type, up to 16 MB). Re-sending an index replaces it.",
    responses=errors(400, 401, 403, 404, 409, 413, 503),
)
async def upload_chunk(
    recording_id: str, index: int, request: Request, user: CurrentUser, session: Session
) -> Response:
    if not 0 <= index < 50000:
        raise ApiError(400, "validation_error", "Chunk index must be 0-49999")
    row = await _owned(session, user, recording_id)
    if row.status != RecordingStatus.RECORDING:
        raise ApiError(409, "recording_closed", "This recording is already finished")
    data = await request.body()
    if not data:
        raise ApiError(400, "validation_error", "Chunk is empty")
    if len(data) > MAX_CHUNK_BYTES:
        raise ApiError(413, "payload_too_large", "Chunk is too large")
    await storage.stage_chunk(row.blob_path, index, data)
    return Response(status_code=204)


@router.post(
    "/{recording_id}/complete",
    response_model=RecordingEnvelope,
    summary="Finish a recording",
    description="Commits the uploaded chunks into the final video. 400 `chunks_missing` lists any that never arrived.",
    responses=errors(400, 401, 403, 404, 415, 503),
)
async def complete_recording(
    recording_id: str, body: CompleteRecordingRequest, user: CurrentUser, session: Session
) -> RecordingEnvelope:
    row = await _owned(session, user, recording_id)
    if row.status == RecordingStatus.READY:
        return RecordingEnvelope(recording=recording_dto(row))  # idempotent retry
    row.size_bytes = await storage.commit(row.blob_path, row.content_type, body.chunk_count)
    row.chunk_count = body.chunk_count
    row.duration_seconds = body.duration_seconds
    row.status = RecordingStatus.READY
    row.ended_at = utcnow()
    await session.commit()
    await session.refresh(row)
    return RecordingEnvelope(recording=recording_dto(row))


@router.get(
    "",
    response_model=RecordingListResponse,
    summary="Recording history",
    description="Newest first. Interviewers see recordings of their own interviews; admins see all. "
    "Filter with `interviewId`.",
    responses=errors(401),
)
async def list_recordings(
    user: CurrentUser,
    session: Session,
    interview_id: str | None = Query(default=None, alias="interviewId", max_length=40),
    limit: int = Query(default=50, ge=1, le=200),
) -> RecordingListResponse:
    query = select(Recording).join(Interview, Interview.id == Recording.interview_id)
    if user.role != UserRole.ADMIN:
        query = query.where(Interview.interviewer_id == user.id)
    if interview_id:
        query = query.where(Recording.interview_id == interview_id)
    rows = (await session.scalars(query.order_by(Recording.created_at.desc()).limit(limit))).unique().all()
    return RecordingListResponse(recordings=[recording_dto(r) for r in rows])


@router.get(
    "/{recording_id}", response_model=RecordingEnvelope, summary="Get a recording", responses=errors(401, 403, 404)
)
async def get_recording(recording_id: str, user: CurrentUser, session: Session) -> RecordingEnvelope:
    return RecordingEnvelope(recording=recording_dto(await _owned(session, user, recording_id)))


@router.get(
    "/{recording_id}/video",
    summary="Stream the video",
    description="Supports `Range` requests, so a `<video src>` can seek. Needs the session cookie or Bearer token.",
    responses={**errors(401, 403, 404, 416, 503), 200: {"content": {"video/webm": {}, "video/mp4": {}}}},
)
async def stream_video(recording_id: str, request: Request, user: CurrentUser, session: Session) -> StreamingResponse:
    row = await _owned(session, user, recording_id)
    if row.status != RecordingStatus.READY or not row.size_bytes:
        raise not_found("Recording is not ready")
    size = row.size_bytes
    start, end, status = 0, size - 1, 200
    header = request.headers.get("range")
    if header:
        match = _RANGE.match(header.strip())
        if not match or not (match[1] or match[2]):
            raise ApiError(416, "range_not_satisfiable", "Invalid Range header")
        if match[1]:
            start = int(match[1])
            end = min(int(match[2]), size - 1) if match[2] else size - 1
        else:  # suffix range: last N bytes
            start = max(size - int(match[2]), 0)
        if start > end or start >= size:
            raise ApiError(416, "range_not_satisfiable", "Range is outside the video")
        status = 206
    headers = {"Accept-Ranges": "bytes", "Content-Length": str(end - start + 1), "Cache-Control": "private, no-store"}
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(
        storage.read_range(row.blob_path, start, end - start + 1),
        status_code=status,
        media_type=row.content_type,
        headers=headers,
    )
