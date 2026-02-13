# Nexus Voice Engine 🎙️🤖

**A Production-Ready, Multi-Tenant SaaS Platform for AI Voice Agents with Real-Time Audio Streaming.**

This system orchestrates real-time voice conversations using a **Sidecar Microservice Pattern**, connecting NVIDIA PersonaPlex (external Docker container) for GPU-accelerated audio processing with Google Gemini for conversational intelligence, strictly following a config-driven architecture.

**Status:** ✅ Production Ready with Full-Duplex WebSocket Streaming

---

## 🏗️ Architecture Philosophy

This project adheres to strict architectural guidelines designed for stability and scalability:

1.  **Sidecar Pattern:** NVIDIA PersonaPlex runs as an external Docker container. Nexus acts as a WebSocket proxy/bridge, handling audio transcoding and connection management.
2.  **Core / Tenant Separation:** The `src/core` engine knows **nothing** about specific businesses. It is a generic machine that processes audio and executes tool calls.
3.  **Config Driven:** Tenant behavior (Persona, Knowledge Base, Tool Definitions) is defined strictly in configuration files (`config.yaml`), not in the engine code.
4.  **Tier 2 Complexity:** This is a "Business Logic Feature" set. We use a Modular Monolith approach with sidecar for audio processing.
5.  **SSOT (Single Source of Truth):** All documentation lives in `docs/PROJECT_CONTEXT.md`. Tools and logic are defined once.

---

## 📂 Directory Breakdown

### `src/core/audio/` (Audio Bridge - NEW)
* **Responsibility:** Manages dual WebSocket connections (client + PersonaPlex), audio transcoding via FFmpeg, and barge-in detection.
* **Key File:** `streamer.py` (AudioBridge class) - The heart of real-time audio streaming.

### `src/core/` (The Engine)
* **Responsibility:** Handles WebSocket endpoints, conversation state, and LLM communication.
* **Constraint:** NEVER hardcode business logic here. If you are writing "If tenant is Pizza Hut...", you are violating the architecture.

### `src/tenants/` (The Business Logic)
* **Responsibility:** Contains the specific configuration and executable tools for each client.
* **Structure:** Each folder represents a distinct tenant (business).
* **Components:**
    * `config.yaml`: Defines the System Prompt, Voice ID, and active Tools.
    * `tools.py`: Python functions that interact with the real world (DBs, CRMs, APIs).

### `src/interfaces/` (The Contracts)
* **Responsibility:** Defines the `BaseTool` abstract class. All tenant tools must implement this interface to ensure the Engine can execute them safely.

### `docs/` (Documentation)
* **`PROJECT_CONTEXT.md`**: **SINGLE SOURCE OF TRUTH** for all documentation (1,200+ lines). Includes architecture, quick start, sidecar pattern, deployment, troubleshooting, and everything else.

---

## 🚀 How to Onboard a New Tenant

We follow the **Incremental Evolution Rule**. Adding a client does not require restarting the engine or modifying core code.

1.  **Create Tenant Directory:**
    Copy `src/tenants/_template` to `src/tenants/my_new_client`.

2.  **Configure (`config.yaml`):**
    ```yaml
    tenant_id: "barber_shop_01"
    voice_settings:
      provider: "nvidia_personaplex"
      voice_id: "en_us_male_calm"
    system_prompt: |
      You are a helpful receptionist at 'Joe's Barbershop'.
      Keep answers short.
    enabled_tools:
      - "check_availability"
      - "book_appointment"
    ```

3.  **Implement Tools (`tools.py`):**
    Inherit from `BaseTool` and implement the logic.
    ```python
    from src.interfaces.base_tool import BaseTool

    class CheckAvailabilityTool(BaseTool):
        name = "check_availability"
        description = "Checks if a time slot is free"
        parameters = {"time": "string"}

        async def execute(self, time: str):
            # Connect to client's external calendar API here
            return f"Checking availability for {time}..."
    ```

4.  **Deploy:** The system automatically loads the new tenant configuration on the next request.

---

## ⚠️ Development Rules (Audit Checklist)

Before submitting code, verify against the **CODE_AUDIT_PROMPT**:

* [ ] **Domain Boundaries:** Did I leak tenant logic into the core engine?
* [ ] **Coupling:** Did I import a specific tenant module into `main.py`? (Forbidden. Use the Loader).
* [ ] **Cognitive Load:** Is the code flow obvious? Can an LLM understand the file structure in 1 minute?
* [ ] **Complexity Budget:** Did I introduce a new database or queue? If yes, where is the justification?

---

## 🛠️ Quick Start (One Command!)

### Development Mode (Automatic Setup)

```bash
# Install dependencies (first time only)
pip install -r requirements.txt

# Start everything with one command
python scripts/start_dev.py
```

**That's it!** The script will:
- ✅ Check FFmpeg installation
- ✅ Check port availability (auto-switch if needed)
- ✅ Create `.env` from example if missing
- ✅ Start Mock PersonaPlex sidecar (port 9000)
- ✅ Start Nexus Engine (port 8000)
- ✅ Handle graceful shutdown (Ctrl+C)

**Output:**
```
[CHECK] FFmpeg ✅
[CHECK] Port 9000 free ✅
[CHECK] Port 8000 free ✅
[INFO] Starting Mock PersonaPlex Sidecar...
[INFO] Starting Nexus Voice Engine...
```

### Manual Setup (Advanced)

If you need more control:

```bash
# 1. Setup environment
python -m venv env
env\Scripts\activate  # Windows
source env/bin/activate  # macOS/Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure
cp env.example.new .env
# Edit .env: Set GEMINI_API_KEY

# 4. Start Mock Sidecar (Terminal 1)
python tests/mock_personaplex.py

# 5. Start Nexus Engine (Terminal 2)
uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

### Test Real-Time Audio

Open `test_audio.html` in your browser and click "Start Call".

**For complete documentation, see `docs/PROJECT_CONTEXT.md`**

---

## ⚙️ Configuration & Performance

### Gemini Transport Protocol (`GEMINI_TRANSPORT`)
The system supports two transport protocols for communicating with Google Gemini:

1.  **`rest` (Default)**: Uses standard HTTP/1.1 JSON.
    *   **Pros**: Extremely stable, works on Windows/Mac/Linux, compatible with Uvicorn/Gunicorn.
    *   **Cons**: Slightly higher latency (negligible for text).
    *   **Use Case**: Development, Windows production, or ensuring maximum stability.

2.  **`grpc`**: Uses HTTP/2 with Protocol Buffers.
    *   **Pros**: Lowest possible latency and bandwidth.
    *   **Cons**: Known compatibility issues with Uvicorn on Windows (causes deadlocks).
    *   **Use Case**: High-performance Linux/Docker production environments.

**To switch:**
Set the environment variable in `.env` or Docker config:
```bash
GEMINI_TRANSPORT=grpc
```

---

## 📚 Documentation

**All documentation is consolidated in one file:**

**[`docs/PROJECT_CONTEXT.md`](docs/PROJECT_CONTEXT.md)** - Complete Documentation (1,200+ lines)
- Executive Summary
- Architecture Overview (Sidecar Pattern)
- Data Flow Diagrams
- Quick Start Guide (10 minutes)
- Sidecar Architecture Deep Dive
- Tenant Isolation Strategy
- Logging Strategy
- Tool Development Guide
- Deployment Guide
- Troubleshooting
- Future Roadmap

**Additional Files:**
- `docs/ARCHITECT_PROMPT.md` - Architectural principles
- `docs/SYSTEM_EXECUTION_PROMPT.md` - Execution rules
- `SIDECAR_REFACTOR_SUMMARY.md` - Sidecar refactoring summary

---

## 🔥 Key Features

✅ **Full-Duplex Audio Streaming** - Real-time bidirectional audio via WebSocket  
✅ **Sidecar Pattern** - PersonaPlex runs in external Docker container  
✅ **Text-Only Fallback** - Transparently handles PersonaPlex unavailability  
✅ **Audio Transcoding** - FFmpeg pipeline for WebM ↔ PCM conversion  
✅ **Barge-In Detection** - Interrupt AI when user speaks  
✅ **Multi-Tenant** - Complete isolation between tenants  
✅ **Config-Driven** - No hardcoded business logic  
✅ **Context-Aware Logging** - Color-coded, tenant-tagged logs  
✅ **Error Recovery** - Automatic reconnection with retry logic  
✅ **Production-Ready** - Robust error handling throughout

---

## 🛡️ Stability & Graceful Degradation

The Nexus Voice Engine is designed to be "Always-On" even if satellite services fail:

1.  **PersonaPlex Fallback:** If the PersonaPlex sidecar is down or unreachable, the `AudioBridge` automatically degrades to **Text-Only Mode**. The conversation continues via WebSocket text messages until the audio service recovers.
2.  **Gemini REST Transport:** For environments where GRPC is unstable (like Windows), the engine uses a robust **Async REST Wrapper**. It wraps standard synchronous Gemini REST calls in a high-performance producer thread to maintain non-blocking streaming.
3.  **Clean Disconnects:** The orchestration logic handles sudden client disconnections or timeouts gracefully, ensuring all background threads and LLM streams are cancelled immediately to prevent resource leaks.
4.  **Windows Compatibility:** Specific fixes for `starlette` / `uvicorn` race conditions and Windows-specific file system locks (Atomic Replace) are built-in.

---

## 💡 Troubleshooting

*   **"Session not found"**: Ensure you are passing the **Conversation Session ID** (returned in the `connected` message) rather than the raw WebSocket UUID for orchestration calls.
*   **No audio?**: Check if `PERSONAPLEX_WS_URL` is correct and the mock or real sidecar is running. Look for "Starting model->client stream" in the logs.
*   **Latency**: Ensure `GEMINI_TRANSPORT=rest` is set if you are on Windows to avoid the GRPC deadlock.

---
