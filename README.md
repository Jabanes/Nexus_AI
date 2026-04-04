# Nexus Voice Engine

**A Production-Ready, Multi-Tenant SaaS Platform for AI Voice Agents with Real-Time Audio Streaming.**

Nexus orchestrates real-time voice conversations using a **provider-agnostic** architecture. Voice processing (ElevenLabs, NVIDIA PersonaPlex, etc.) and LLM intelligence (Google Gemini, with more planned) are abstracted behind clean interfaces, allowing tenants to mix and match providers via configuration alone.

**Status:** Production Ready with Full-Duplex WebSocket Streaming

---

## Architecture Philosophy

1.  **Provider Abstraction:** Voice and LLM capabilities are behind abstract interfaces (`IVoiceProvider`, `ILLMProvider`). New providers are added without touching core orchestration.
2.  **Core / Tenant Separation:** The `src/core` engine knows **nothing** about specific businesses. It is a generic machine that processes audio and executes tool calls.
3.  **Config Driven:** Tenant behavior (persona, voice provider, tool definitions) is defined strictly in configuration files (`config.yaml`), not in the engine code.
4.  **Shared Integrations:** Reusable tool implementations live in `src/integrations/` and can be referenced by any tenant via handler path in their config.
5.  **SSOT (Single Source of Truth):** All documentation lives in `docs/PROJECT_CONTEXT.md`.

---

## Directory Breakdown

### `src/core/voice/` (Voice Provider Layer)
* **Responsibility:** Abstracts voice/audio processing behind a pluggable provider interface.
* **Key Files:**
    * `base_provider.py` — `IVoiceProvider` abstract base class
    * `bridge.py` — `VoiceBridge`, the unified entry point for audio streaming
    * `provider_factory.py` — Factory that instantiates the correct provider from tenant config
    * `personaplex_provider.py` — NVIDIA PersonaPlex provider (legacy, Docker sidecar)
    * `elevenlabs_provider.py` — ElevenLabs Conversational AI provider (recommended)

### `src/core/llm/` (LLM Provider Layer)
* **Responsibility:** Abstracts LLM communication behind a pluggable provider interface.
* **Key Files:**
    * `base_provider.py` — `ILLMProvider` abstract base class
    * `gemini_provider.py` — Google Gemini implementation
    * `factory.py` — Factory that reads the `LLM_PROVIDER` env var to select a provider

### `src/core/audio/` (Audio Transcoding)
* **Responsibility:** FFmpeg-based audio transcoding (WebM to PCM and back), barge-in detection.

### `src/core/` (The Engine)
* **Responsibility:** WebSocket endpoints, conversation orchestration, session/history management.
* **Constraint:** NEVER hardcode business logic here.

### `src/tenants/` (Tenant Configurations)
* **Responsibility:** Per-tenant configuration and optional local tool implementations.
* **Components:**
    * `config.yaml` — System prompt, voice provider/settings, enabled tools.
    * `tools.py` — (Optional) Local tool implementations. Tenants can also reference shared tools from `src/integrations/`.

### `src/integrations/` (Shared Tool Implementations)
* **Responsibility:** Reusable tool modules that any tenant can reference by handler path in `config.yaml`.
* **Packages:** `mock/` (dev/testing tools), `google_calendar/`, and more as needed.

### `src/interfaces/` (Contracts)
* **Responsibility:** Defines `BaseTool` abstract class (now accepts optional `config` dict for per-tenant parameterization).

### `docs/` (Documentation)
* **`PROJECT_CONTEXT.md`**: Single source of truth for architecture, data flows, deployment, and troubleshooting.

---

## Quick Start

### Development Mode (One Command)

```bash
# Install dependencies (first time only)
pip install -r requirements.txt

# Start everything
python scripts/start_dev.py
```

The script will:
- Check FFmpeg installation
- Check port availability (auto-switch if needed)
- Create `.env` from example if missing
- Start the Nexus Engine (port 8000)
- Handle graceful shutdown (Ctrl+C)

### Manual Setup

```bash
# 1. Setup environment
python -m venv env
source env/bin/activate        # macOS/Linux
env\Scripts\activate           # Windows

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure
cp env.example.new .env
# Edit .env — see Environment Variables below

# 4. Start Nexus Engine
uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

### Test Real-Time Audio

Open `test_audio.html` in your browser and click "Start Call".

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `GEMINI_API_KEY` | Yes | Google Gemini API key |
| `LLM_PROVIDER` | No | LLM backend to use (default: `gemini`) |
| `GEMINI_TRANSPORT` | No | `rest` (default, stable) or `grpc` (low-latency Linux/Docker) |
| `ELEVENLABS_API_KEY` | For ElevenLabs | API key for ElevenLabs voice provider |
| `ELEVENLABS_AGENT_ID` | For ElevenLabs | ElevenLabs Conversational AI agent ID |
| `PERSONAPLEX_WS_URL` | For PersonaPlex | WebSocket URL of the PersonaPlex sidecar |
| `CORS_ALLOWED_ORIGINS` | No | Comma-separated allowed origins (default: `*`) |

---

## How to Onboard a New Tenant

Adding a tenant does not require restarting the engine or modifying core code.

1.  **Create Tenant Directory:**
    Copy `src/tenants/_template` to `src/tenants/my_new_client`.

2.  **Configure (`config.yaml`):**
    ```yaml
    tenant_id: "barber_shop_01"
    voice_settings:
      provider: "elevenlabs"          # or "personaplex"
      voice_id: "en_us_male_calm"
    system_prompt: |
      You are a helpful receptionist at 'Joe's Barbershop'.
      Keep answers short.
    enabled_tools:
      - "check_availability"
      - "book_appointment"
    ```

3.  **Implement Tools — pick one approach:**

    **Option A: Shared integration (recommended)**
    Reference a handler path from `src/integrations/` in your config. No local `tools.py` needed.

    **Option B: Local `tools.py`**
    ```python
    from src.interfaces.base_tool import BaseTool

    class CheckAvailabilityTool(BaseTool):
        name = "check_availability"
        description = "Checks if a time slot is free"
        parameters = {"time": "string"}

        async def execute(self, time: str):
            return f"Checking availability for {time}..."
    ```

    `BaseTool` accepts an optional `config` dict for per-tenant parameterization.

4.  **Deploy:** The system automatically loads the new tenant configuration on the next request.

---

## Key Features

- **Full-Duplex Audio Streaming** — Real-time bidirectional audio via WebSocket
- **Pluggable Voice Providers** — ElevenLabs (recommended), NVIDIA PersonaPlex, or add your own
- **Pluggable LLM Providers** — Gemini today, extensible to others via `ILLMProvider`
- **Text-Only Fallback** — Graceful degradation when voice provider is unavailable
- **Audio Transcoding** — FFmpeg pipeline for WebM / PCM conversion
- **Barge-In Detection** — Interrupt AI when user speaks
- **Multi-Tenant** — Complete isolation between tenants
- **Config-Driven** — No hardcoded business logic
- **Shared Integrations** — Reusable tools referenced by handler path across tenants
- **Configurable CORS** — Set allowed origins via `CORS_ALLOWED_ORIGINS`
- **Context-Aware Logging** — Color-coded, tenant-tagged logs (verbose audio logs at DEBUG level)
- **Error Recovery** — Automatic reconnection with retry logic
- **Production-Ready** — Robust error handling, clean FFmpeg/session cleanup

---

## Stability and Graceful Degradation

1.  **Voice Provider Fallback:** If the active voice provider is unreachable, the engine degrades to **Text-Only Mode**. Conversations continue via WebSocket text until the provider recovers.
2.  **Gemini REST Transport:** For environments where gRPC is unstable (e.g., Windows), the engine uses an async REST wrapper for non-blocking streaming.
3.  **Clean Disconnects:** Sudden client disconnections are handled gracefully — all background threads, LLM streams, and FFmpeg processes are cancelled immediately to prevent resource leaks. `close_session` is fully async.
4.  **Windows Compatibility:** Fixes for Starlette/Uvicorn race conditions and Windows-specific file system locks are built-in.

---

## Troubleshooting

*   **"Session not found"**: Ensure you pass the **Conversation Session ID** (from the `connected` message), not the raw WebSocket UUID.
*   **No audio?**: Verify the correct voice provider is configured in your tenant's `config.yaml`. For PersonaPlex, check `PERSONAPLEX_WS_URL`. For ElevenLabs, check `ELEVENLABS_API_KEY` and `ELEVENLABS_AGENT_ID`.
*   **Latency on Windows**: Set `GEMINI_TRANSPORT=rest` to avoid gRPC deadlocks.
*   **CORS errors**: Set `CORS_ALLOWED_ORIGINS` to your frontend's origin.

---

## Documentation

All documentation is consolidated in:

**[`docs/PROJECT_CONTEXT.md`](docs/PROJECT_CONTEXT.md)** — Complete documentation including architecture overview, data flow diagrams, deployment guide, and troubleshooting.

Additional files:
- `docs/ARCHITECT_PROMPT.md` — Architectural principles
- `docs/SYSTEM_EXECUTION_PROMPT.md` — Execution rules

---
