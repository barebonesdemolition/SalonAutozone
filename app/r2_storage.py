"""
Cloudflare R2 storage helper.
Falls back to local disk if R2_* env vars are not set.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

try:
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False


R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY")
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID")
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME", "saloncarparts-uploads")
R2_PUBLIC_URL = os.getenv("R2_PUBLIC_URL", "").rstrip("/")

R2_CONFIGURED = all([R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_ACCOUNT_ID, R2_BUCKET_NAME]) and BOTO3_AVAILABLE


def _client():
    return boto3.client(
        "s3",
        endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def save_to_r2(file_bytes: bytes, extension: str, content_type: str = "image/jpeg") -> str:
    """Save bytes to R2. Returns the public URL."""
    if not R2_CONFIGURED:
        raise RuntimeError("R2 is not configured")

    key = f"uploads/{uuid.uuid4().hex}.{extension}"
    client = _client()
    client.put_object(
        Bucket=R2_BUCKET_NAME,
        Key=key,
        Body=file_bytes,
        ContentType=content_type,
    )
    if R2_PUBLIC_URL:
        return f"{R2_PUBLIC_URL}/{key}"
    return f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com/{R2_BUCKET_NAME}/{key}"
