"""One-shot: create the Power Roofing ElevenLabs agent from the tenant config.

Reads system_prompt from src/tenants/power_roofing/config.yaml (via TenantLoader),
configures voice + LLM + analysis + data_collection, POSTs to ElevenLabs.

Prints the new agent_id at the end — copy it into .env as ELEVENLABS_AGENT_ID
(or pass --update-env to do it automatically).
"""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.tenants.loader import TenantLoader  # noqa: E402


def _load_env():
    env_path = Path(".env")
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def build_payload() -> dict:
    ctx = TenantLoader.load_tenant("power_roofing")
    full_prompt = ctx["system_prompt"]
    voice_id = ctx["voice_settings"].get("voice_id", "JBFqnCBsd6RMkjVDRZzb")

    return {
        "name": "Power Roofing Agent",
        "conversation_config": {
            "asr": {"quality": "high", "provider": "elevenlabs", "user_input_audio_format": "pcm_16000"},
            "turn": {"turn_timeout": 20.0, "mode": "turn", "turn_model": "turn_v2"},
            "tts": {
                "model_id": "eleven_multilingual_v2",
                "voice_id": voice_id,
                "agent_output_audio_format": "pcm_16000",
                "optimize_streaming_latency": 3,
                "stability": 0.5,
                "speed": 1.0,
                "similarity_boost": 0.8,
            },
            "conversation": {
                "text_only": False,
                "max_duration_seconds": 600,
                "client_events": [
                    "audio", "interruption", "agent_response",
                    "user_transcript", "agent_response_correction", "agent_tool_response",
                ],
            },
            "agent": {
                "first_message": "Hello, Power Roofing — this is your receptionist. This call is recorded. How can I help you today?",
                "language": "en",
                "prompt": {
                    "prompt": full_prompt,
                    "llm": "gemini-2.5-flash",
                    "temperature": 0.0,
                    "max_tokens": -1,
                    "tool_ids": [],
                    "built_in_tools": {
                        "end_call": {
                            "type": "system",
                            "name": "end_call",
                            "description": "End the call when the customer is done OR when spam is confirmed.",
                            "response_timeout_secs": 20,
                            "disable_interruptions": False,
                            "force_pre_tool_speech": False,
                            "pre_tool_speech": "auto",
                            "params": {"system_tool_type": "end_call"},
                        }
                    },
                },
            },
        },
        "platform_settings": {
            "evaluation": {
                "criteria": [
                    {
                        "id": "is_spam_call",
                        "name": "Spam Call",
                        "type": "prompt",
                        "conversation_goal_prompt": (
                            "Determine if this call was SPAM (sales pitch to us, robocall, "
                            "B2B solicitation, SEO/marketing/insurance pitch, recruitment, "
                            "extended warranty pitch, etc.) vs. a REAL customer call about "
                            "roofing services. Return 'success' if SPAM, 'failure' if REAL CUSTOMER."
                        ),
                    }
                ]
            },
            "data_collection": {
                "is_spam": {
                    "type": "boolean",
                    "description": (
                        "True if this call is spam (sales pitch to us, robocall, B2B solicitation, "
                        "SEO/marketing pitch, recruitment, extended warranty, etc.). "
                        "False if a real customer calling about roofing."
                    ),
                },
                "customer_name": {
                    "type": "string",
                    "description": "Customer's full name if provided. Empty string if not given.",
                },
                "customer_phone": {
                    "type": "string",
                    "description": "Customer's callback phone number if provided. Empty string if not given.",
                },
                "address": {
                    "type": "string",
                    "description": "Service address (street + borough/city). Empty string if not given.",
                },
                "service_requested": {
                    "type": "string",
                    "description": "Short description of the roofing work the customer wants (e.g. 'leaking flat roof in Brooklyn', 'gutter replacement', 'roof inspection').",
                },
                "core_intent": {
                    "type": "string",
                    "description": "One-phrase summary of why they called: 'spam', 'roof_repair', 'roof_replacement', 'inspection', 'gutters', 'siding', 'skylight', 'emergency', 'pricing_inquiry', 'other'.",
                },
            },
            "privacy": {"record_voice": True, "retention_days": -1},
            "call_limits": {"agent_concurrency_limit": -1, "daily_limit": 100000, "bursting_enabled": True},
            "auth": {"enable_auth": False, "allowlist": [], "require_origin_header": False},
        },
    }


def main():
    _load_env()
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        print("ERROR: ELEVENLABS_API_KEY not set in env")
        sys.exit(1)

    payload = build_payload()
    body = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        "https://api.elevenlabs.io/v1/convai/agents/create",
        data=body,
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.reason}")
        print(e.read().decode())
        sys.exit(2)

    agent_id = data.get("agent_id")
    print(f"[OK] Created agent: {agent_id}")
    print(json.dumps(data, indent=2)[:500])

    if "--update-env" in sys.argv:
        update_env_file(agent_id)
        print(f"[OK] Updated .env ELEVENLABS_AGENT_ID={agent_id}")


def update_env_file(agent_id: str) -> None:
    env_path = Path(".env")
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    found = False
    for i, line in enumerate(lines):
        if line.startswith("ELEVENLABS_AGENT_ID="):
            lines[i] = f"ELEVENLABS_AGENT_ID={agent_id}"
            found = True
            break
    if not found:
        lines.append(f"ELEVENLABS_AGENT_ID={agent_id}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
