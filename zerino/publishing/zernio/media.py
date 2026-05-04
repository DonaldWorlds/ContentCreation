# app/integrations/zernio/media.py

from __future__ import annotations

import os
import mimetypes
import httpx

from zerino.publishing.zernio.client import get_zernio_client


def detect_content_type(path: str) -> str:
    ct, _ = mimetypes.guess_type(path)

    # common fallback
    if not ct and path.lower().endswith(".mp4"):
        ct = "video/mp4"

    if not ct:
        raise ValueError(f"Could not detect content-type for {path}")
    return ct


def upload_media(file_path: str) -> str:
    if not os.path.isfile(file_path):
        raise FileNotFoundError(file_path)

    client = get_zernio_client()
    content_type = detect_content_type(file_path)

    presign = client.media.get_media_presigned_url(
        filename=os.path.basename(file_path),
        content_type=content_type,
        size=os.path.getsize(file_path),
    )

    upload_url = presign.get("uploadUrl") or presign.get("upload_url")
    public_url = presign.get("publicUrl") or presign.get("public_url")
    if not upload_url or not public_url:
        raise KeyError(f"Unexpected presign response: {presign}")

    with open(file_path, "rb") as f:
        r = httpx.put(
            upload_url,
            content=f,  # stream
            headers={"Content-Type": content_type},
            timeout=300,
        )
        r.raise_for_status()

    return public_url