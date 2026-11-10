"""Ask the photo-reading AI (Gemini) a question about one image and get JSON back."""
from __future__ import annotations

import json
import re

from app.config import get_settings


def vision_enabled() -> bool:
    return bool(get_settings().GEMINI_API_KEY)


def ask_json(image: bytes, mime: str, prompt: str) -> dict:
    """Blocking call; run it in a thread. Raises ValueError if the answer isn't JSON."""
    from google import genai
    from google.genai import types
    cfg = get_settings()
    client = genai.Client(api_key=cfg.GEMINI_API_KEY)
    response = client.models.generate_content(
        model=cfg.GEMINI_MODEL,
        contents=[types.Part.from_bytes(data=image, mime_type=mime), prompt],
        config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1),
    )
    txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", (response.text or "").strip())
    data = json.loads(txt)
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object")
    return data
