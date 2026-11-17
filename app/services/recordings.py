"""Interview recordings in Azure Blob Storage.

The recorder uploads numbered chunks; each becomes an uncommitted block of one block blob, and
``commit`` publishes them in order, so a video is never visible half-written. Retrying a chunk
with the same index just replaces that block."""

import base64
from collections.abc import AsyncIterator

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import BlobBlock, ContentSettings
from azure.storage.blob.aio import BlobServiceClient, ContainerClient

from ..config import get_settings
from ..errors import ApiError

_service: BlobServiceClient | None = None
_ready_containers: set[str] = set()


def _container(name: str | None = None) -> ContainerClient:
    global _service
    settings = get_settings()
    if not (settings.azure_storage_endpoint and settings.azure_storage_sas_token):
        raise ApiError(503, "recording_storage_not_configured", "Recording storage is not configured")
    if _service is None:
        _service = BlobServiceClient(
            account_url=settings.azure_storage_endpoint.strip(), credential=settings.azure_storage_sas_token.strip()
        )
    return _service.get_container_client(name or settings.azure_video_container)


async def _ready_container(name: str | None = None) -> ContainerClient:
    container = _container(name)
    if container.container_name not in _ready_containers:
        try:
            await container.create_container()  # private by default
        except ResourceExistsError:
            pass
        _ready_containers.add(container.container_name)
    return container


async def close() -> None:
    global _service
    if _service:
        await _service.close()
    _service = None
    _ready_containers.clear()


def blob_path(interviewer_id: str, interview_id: str, recording_id: str, content_type: str) -> str:
    extension = "mp4" if content_type == "video/mp4" else "webm"
    return f"{interviewer_id}/{interview_id}/{recording_id}.{extension}"


def _block_id(index: int) -> str:
    return base64.b64encode(f"{index:08d}".encode()).decode()


async def stage_chunk(path: str, index: int, data: bytes) -> None:
    blob = (await _ready_container()).get_blob_client(path)
    await blob.stage_block(_block_id(index), data)


async def commit(path: str, content_type: str, chunk_count: int) -> int:
    """Publishes chunks 0..chunk_count-1 and returns the video size in bytes."""
    blob = (await _ready_container()).get_blob_client(path)
    try:
        _, uncommitted = await blob.get_block_list("uncommitted")
    except ResourceNotFoundError:
        uncommitted = []
    sizes = {block.id: block.size for block in uncommitted}
    wanted = [_block_id(i) for i in range(chunk_count)]
    missing = [i for i, block_id in enumerate(wanted) if block_id not in sizes]
    if missing:
        raise ApiError(400, "chunks_missing", f"{len(missing)} chunk(s) were never uploaded", {"missing": missing[:20]})
    await blob.commit_block_list(
        [BlobBlock(block_id=block_id) for block_id in wanted],
        content_settings=ContentSettings(content_type=content_type),
    )
    return sum(sizes[block_id] for block_id in wanted)


async def read_range(path: str, offset: int, length: int) -> AsyncIterator[bytes]:
    blob = (await _ready_container()).get_blob_client(path)
    try:
        stream = await blob.download_blob(offset=offset, length=length)
    except ResourceNotFoundError:
        raise ApiError(404, "not_found", "Recording file not found") from None
    async for chunk in stream.chunks():
        yield chunk


def image_blob_path(interviewer_id: str, interview_id: str, alert_id: str, content_type: str) -> str:
    extension = {"image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}.get(content_type, "png")
    return f"{interviewer_id}/{interview_id}/{alert_id}.{extension}"


async def upload_image(path: str, data: bytes, content_type: str) -> str:
    """Stores a screenshot in the image container and returns its blob URL (no SAS token)."""
    blob = (await _ready_container(get_settings().azure_image_container)).get_blob_client(path)
    await blob.upload_blob(data, overwrite=True, content_settings=ContentSettings(content_type=content_type))
    return blob.url.split("?", 1)[0]


async def download_image(path: str) -> bytes:
    blob = (await _ready_container(get_settings().azure_image_container)).get_blob_client(path)
    try:
        return await (await blob.download_blob()).readall()
    except ResourceNotFoundError:
        raise ApiError(404, "not_found", "Screenshot file not found") from None
