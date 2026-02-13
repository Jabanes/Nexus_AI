"""
Nexus Voice Engine — End-to-End Simulation Client

This script acts as a simulated customer to verify the full pipeline:
1. Connects to the Nexus Engine via HTTP/WebSocket
2. Runs a multi-turn conversation
3. Verifies session recording is saved correctly
4. Verifies the Intelligence Engine produces a valid LeadObject

Usage:
    # Start the server first:
    #   uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
    
    # Then run:
    #   python scripts/simulate_customer.py
    
    # Or with custom settings:
    #   python scripts/simulate_customer.py --tenant barber_shop_demo --port 8000
"""

import asyncio
import json
import sys
import os
import argparse
import time
from pathlib import Path
from datetime import datetime

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import websockets
except ImportError:
    print("❌ 'websockets' package required. Install with: pip install websockets")
    sys.exit(1)

try:
    import requests
except ImportError:
    print("❌ 'requests' package required. Install with: pip install requests")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("⚠️ 'python-dotenv' not found. Environment variables might not be loaded.")


# ─── Configuration ────────────────────────────────────────────

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 8000
DEFAULT_TENANT = "barber_shop_demo"
DEFAULT_PHONE = "+972501234567"

# Scripted conversation turns (simulating a Hebrew-speaking customer)
CONVERSATION_SCRIPT = [
    "שלום, אני רוצה לקבוע תור לתספורת",
    "יש לכם מקום מחר בעשר בבוקר?",
    "כמה עולה תספורת גבר?",
    "מצוין, אני רוצה לקבוע תור. קוראים לי דני.",
]


# ─── Helpers ──────────────────────────────────────────────────

def print_header(text: str):
    width = 60
    print()
    print("=" * width)
    print(f" {text} ".center(width))
    print("=" * width)
    print()


def print_step(emoji: str, text: str):
    print(f"  {emoji}  {text}")


def print_result(passed: bool, text: str, detail: str = ""):
    icon = "✅" if passed else "❌"
    print(f"  {icon}  {text}")
    if detail:
        print(f"       {detail}")


# ─── Phase 1: Health Check ────────────────────────────────────

def check_server_health(base_url: str) -> bool:
    """Verify the server is running and reachable."""
    try:
        resp = requests.get(f"{base_url}/", timeout=5)
        data = resp.json()
        return data.get("status") == "active"
    except Exception as e:
        print_result(False, "Server health check", str(e))
        return False


# ─── Phase 2: Conversation via HTTP API ───────────────────────

async def run_conversation(base_url: str, tenant_id: str, phone: str) -> dict:
    """
    Run a multi-turn conversation using the HTTP conversation API.
    
    Returns:
        Dict with session_id, responses, and tools_used
    """
    result = {
        "session_id": None,
        "responses": [],
        "tools_used": [],
        "success": False
    }

    # Start conversation
    try:
        resp = requests.post(
            f"{base_url}/conversation/start",
            json={"tenant_id": tenant_id, "customer_phone": phone},
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        session_id = data["session_id"]
        result["session_id"] = session_id
        print_step("🟢", f"Conversation started: {session_id}")
        print_step("🔧", f"Available tools: {data.get('available_tools', [])}")
    except Exception as e:
        print_result(False, "Start conversation", str(e))
        return result

    # Send scripted messages
    for i, message in enumerate(CONVERSATION_SCRIPT, 1):
        try:
            print_step("💬", f"Customer [{i}/{len(CONVERSATION_SCRIPT)}]: {message}")
            
            resp = requests.post(
                f"{base_url}/conversation/message",
                json={"session_id": session_id, "message": message},
                timeout=30
            )
            resp.raise_for_status()
            data = resp.json()
            
            agent_response = data.get("response", "(no response)")
            tools = data.get("tools_used", [])
            
            print_step("🤖", f"Agent: {agent_response[:120]}{'...' if len(agent_response) > 120 else ''}")
            if tools:
                print_step("🔧", f"Tools used: {tools}")
                result["tools_used"].extend(tools)
            
            result["responses"].append({
                "user": message,
                "agent": agent_response,
                "tools": tools
            })
            
            # Small delay between turns for realism and to respect rate limits
            await asyncio.sleep(10)
            
        except Exception as e:
            msg = str(e)
            if isinstance(e, requests.exceptions.HTTPError):
                msg += f"\n       Response: {e.response.text}"
            print_result(False, f"Message {i}", msg)
            # Continue trying remaining messages

    # Close conversation
    try:
        resp = requests.delete(f"{base_url}/conversation/{session_id}", timeout=10)
        print_step("📴", "Conversation closed")
    except Exception as e:
        print_step("⚠️", f"Close failed (non-fatal): {e}")

    result["success"] = len(result["responses"]) > 0
    return result


# ─── Phase 3: Session Persistence Verification ───────────────

def verify_session_persistence(tenant_id: str) -> dict:
    """
    Check that a session JSON file was saved for the tenant.
    
    Returns the most recent session data if found.
    """
    history_dir = Path("data/history") / tenant_id

    if not history_dir.exists():
        print_result(False, "Session directory exists", f"Missing: {history_dir}")
        return {}

    session_files = sorted(
        history_dir.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )

    if not session_files:
        print_result(False, "Session files found", "No .json files in directory")
        return {}

    latest = session_files[0]
    print_result(True, f"Latest session file", f"{latest.name} ({latest.stat().st_size} bytes)")

    try:
        with open(latest, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        # Validate schema
        required_keys = ["session_id", "meta", "statistics", "transcript"]
        for key in required_keys:
            if key not in data:
                print_result(False, f"Schema key '{key}'", "Missing")
                return data
        
        print_result(True, "Schema validation", f"{len(data['transcript'])} events recorded")
        
        # Check for intelligence data
        if "intelligence" in data:
            intel = data["intelligence"]
            print_result(
                True,
                "Intelligence data present",
                f"intent={intel.get('core_intent')}, "
                f"sentiment={intel.get('sentiment')}, "
                f"outcome={intel.get('call_outcome')}"
            )
        else:
            print_step("ℹ️", "No intelligence data (normal for HTTP-only sessions)")
        
        return data

    except Exception as e:
        print_result(False, "Read session file", str(e))
        return {}


# ─── Phase 4: Intelligence Engine Verification ───────────────

async def verify_intelligence_engine(session_data: dict) -> bool:
    """
    Manually run the intelligence engine on a session to verify it works.
    """
    if not session_data:
        print_result(False, "Intelligence engine test", "No session data to analyze")
        return False

    try:
        from src.core.intelligence import PostCallIntelligenceEngine, LeadObject
        
        engine = PostCallIntelligenceEngine()
        lead = await engine.analyze_session(session_data, customer_phone=DEFAULT_PHONE)
        
        print_result(True, "Intelligence engine initialized")
        print_result(True, f"LeadObject created", lead.summary[:100] if lead.summary else "(no summary)")
        print()
        print("  📊 Lead Analysis:")
        print(f"       Name:       {lead.customer_name or 'Unknown'}")
        print(f"       Phone:      {lead.customer_phone or 'Unknown'}")
        print(f"       Intent:     {lead.core_intent}")
        print(f"       Sentiment:  {lead.sentiment}")
        print(f"       Outcome:    {lead.call_outcome}")
        print(f"       Topics:     {', '.join(lead.key_topics) if lead.key_topics else 'None'}")
        print(f"       Follow-up:  {'Yes' if lead.follow_up_required else 'No'}")
        print(f"       Summary:    {lead.summary}")
        
        return True

    except Exception as e:
        print_result(False, "Intelligence engine", str(e))
        import traceback
        traceback.print_exc()
        return False


# ─── Phase 5: Config Verification ────────────────────────────

def verify_hybrid_config(tenant_id: str) -> bool:
    """Verify the hybrid config structure loads correctly."""
    try:
        from src.tenants.loader import TenantLoader
        
        context = TenantLoader.load_tenant(tenant_id)
        
        has_prompt = bool(context.get("system_prompt"))
        has_knowledge = context.get("knowledge_base") is not None
        has_tools = len(context.get("tools", [])) > 0
        
        print_result(has_prompt, "System prompt loaded", f"{len(context['system_prompt'])} chars")
        print_result(has_knowledge, "Knowledge base loaded", str(type(context.get("knowledge_base"))))
        print_result(has_tools, "Tools loaded", str([t.name for t in context.get("tools", [])]))
        
        # Check that knowledge was injected into prompt
        if has_knowledge:
            injected = "Business Information" in context["system_prompt"]
            print_result(injected, "Knowledge injected into prompt")
        
        return has_prompt and has_knowledge
        
    except Exception as e:
        print_result(False, "Config load", str(e))
        return False


# ─── Main ─────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="Nexus Voice Engine — Simulation Client")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Server host")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Server port")
    parser.add_argument("--tenant", default=DEFAULT_TENANT, help="Tenant ID")
    parser.add_argument("--phone", default=DEFAULT_PHONE, help="Customer phone")
    parser.add_argument("--skip-server", action="store_true", help="Skip server-dependent tests")
    args = parser.parse_args()

    base_url = f"http://{args.host}:{args.port}"
    
    # Fix console encoding for emojis on Windows
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    print_header("NEXUS VOICE ENGINE — SIMULATION CLIENT")
    print(f"  Server:  {base_url}")
    print(f"  Tenant:  {args.tenant}")
    print(f"  Phone:   {args.phone}")
    print()

    results = {}

    # ─── Phase 1: Config Verification ──────────────────────
    print_header("PHASE 1: Hybrid Config Verification")
    results["config"] = verify_hybrid_config(args.tenant)

    if not args.skip_server:
        # ─── Phase 2: Health Check ─────────────────────────
        print_header("PHASE 2: Server Health Check")
        server_ok = check_server_health(base_url)
        print_result(server_ok, "Server is running")
        results["server"] = server_ok

        if server_ok:
            # ─── Phase 3: Conversation ─────────────────────
            print_header("PHASE 3: Simulated Conversation")
            conv = await run_conversation(base_url, args.tenant, args.phone)
            results["conversation"] = conv["success"]
            print()
            print_result(
                conv["success"],
                "Conversation completed",
                f"{len(conv['responses'])} turns, {len(conv['tools_used'])} tool calls"
            )

            # Wait a moment for session persistence
            await asyncio.sleep(2)

            # ─── Phase 4: Session Persistence ──────────────
            print_header("PHASE 4: Session Persistence")
            session_data = verify_session_persistence(args.tenant)
            results["persistence"] = bool(session_data)
        else:
            print()
            print("  ⚠️  Skipping server-dependent phases (server not reachable)")
            results["conversation"] = False
            results["persistence"] = False
    else:
        print()
        print("  ⚠️  Server tests skipped (--skip-server flag)")
        results["server"] = None
        results["conversation"] = None
        results["persistence"] = None

    # ─── Phase 5: Intelligence Engine ──────────────────────
    print_header("PHASE 5: Intelligence Engine (Direct)")
    
    # Create a synthetic session for testing if no real one exists
    from src.core.history import SessionRecorder
    recorder = SessionRecorder(tenant_id=args.tenant, session_id="sim-test-001")
    recorder.log_user_text("שלום, אני רוצה לקבוע תור לתספורת")
    recorder.log_ai_text("שלום! מתי תרצה להגיע?")
    recorder.log_user_text("מחר בעשר בבוקר, קוראים לי דני")
    recorder.log_ai_text("בסדר דני! בודק עבורך...")
    recorder.log_tool_usage(
        "check_availability",
        {"date": "2026-02-14", "time": "10:00"},
        "יש מקום פנוי ב-2026-02-14 בשעה 10:00."
    )
    recorder.log_ai_text("יש מקום פנוי מחר בעשר! אקבע לך תור?")
    recorder.log_user_text("כן, בבקשה")
    recorder.log_tool_usage(
        "book_appointment",
        {"name": "דני", "time": "10:00"},
        "התור נקבע בהצלחה!"
    )
    recorder.log_ai_text("מצוין דני! התור נקבע למחר בעשר. להתראות!")
    recorder.finalize()
    test_session = recorder.export()
    
    results["intelligence"] = await verify_intelligence_engine(test_session)

    # ─── Summary ──────────────────────────────────────────
    print_header("RESULTS SUMMARY")
    
    total = 0
    passed = 0
    for name, result in results.items():
        if result is None:
            print(f"  ⏭️  {name.title()}: Skipped")
        else:
            total += 1
            if result:
                passed += 1
            icon = "✅" if result else "❌"
            print(f"  {icon}  {name.title()}")
    
    print()
    if total > 0:
        print(f"  Score: {passed}/{total} passed")
    
    if passed == total and total > 0:
        print()
        print("  🎉 All tests PASSED!")
        print()
        return 0
    else:
        print()
        print("  ⚠️  Some tests failed. Check output above for details.")
        print()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
