"""Download the recorded conversation audio from ElevenLabs after a call.

ElevenLabs needs 10-30s post-call to finalize the recording, so we use
exponential backoff with retries. Designed to run as a fire-and-forget
asyncio.create_task — never blocks call cleanup.
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

_BASE = "https://api.elevenlabs.io/v1/convai/conversations"


async def download_recording(
    conversation_id: str,
    dest_path: Path,
    max_attempts: int = 6,
    initial_delay: float = 5.0,
) -> Optional[Path]:
    """
    Pull the call recording with exponential backoff.

    Returns the saved path on success, None on failure (file is not partially
    written — we only write if the response is 200 with content).
    """
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key or not conversation_id:
        return None

    url = f"{_BASE}/{conversation_id}/audio"
    headers = {"xi-api-key": api_key}

    delay = initial_delay
    async with aiohttp.ClientSession() as session:
        for attempt in range(1, max_attempts + 1):
            await asyncio.sleep(delay)
            try:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                    if resp.status == 200:
                        content = await resp.read()
                        if not content:
                            logger.warning(f"[audio] {conversation_id} empty body on attempt {attempt}")
                            delay *= 1.5
                            continue
                        dest_path.parent.mkdir(parents=True, exist_ok=True)
                        dest_path.write_bytes(content)
                        logger.info(f"[audio] {conversation_id} → {dest_path} ({len(content)} bytes, attempt {attempt})")
                        return dest_path

                    if resp.status in (404, 425, 202):
                        # Not ready yet — back off and retry
                        logger.debug(f"[audio] {conversation_id} not ready (HTTP {resp.status}), attempt {attempt}")
                        delay *= 1.5
                        continue

                    # Other status — log and give up
                    body = (await resp.text())[:200]
                    logger.warning(f"[audio] {conversation_id} HTTP {resp.status}: {body}")
                    return None

            except asyncio.TimeoutError:
                logger.warning(f"[audio] {conversation_id} timeout on attempt {attempt}")
                delay *= 1.5
            except Exception as e:
                logger.warning(f"[audio] {conversation_id} error on attempt {attempt}: {e}")
                delay *= 1.5

    logger.warning(f"[audio] {conversation_id} failed after {max_attempts} attempts")
    return None
