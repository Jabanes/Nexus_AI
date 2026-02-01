"""
Test script for session persistence functionality.

This script tests:
1. SessionRecorder can capture events
2. SessionRecorder exports correct JSON schema
3. FileSessionRepository can save/retrieve sessions
4. Directory structure is created correctly
"""

import asyncio
import json
import sys
import os
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.core.history import SessionRecorder, FileSessionRepository


def print_result(test_name: str, passed: bool, message: str = ""):
    """Print test result with color."""
    if passed:
        print(f"✅ {test_name}")
        if message:
            print(f"   {message}")
    else:
        print(f"❌ {test_name}")
        if message:
            print(f"   ERROR: {message}")
    print()


async def test_session_recorder():
    """Test SessionRecorder functionality."""
    print("=" * 60)
    print("TEST 1: SessionRecorder")
    print("=" * 60)
    
    try:
        # Create recorder
        recorder = SessionRecorder(tenant_id="test_tenant", session_id="test-session-001")
        print_result("Create SessionRecorder", True, f"tenant=test_tenant, session=test-session-001")
        
        # Log various events
        recorder.log_user_audio(duration_ms=2000, bytes_sent=64000)
        recorder.log_user_text("Hello, I need an appointment")
        recorder.log_ai_text("Of course! Let me check availability for you.")
        recorder.log_tool_usage(
            tool_name="check_availability",
            tool_input={"date": "2026-02-02", "time": "10:00"},
            tool_output="Available slots: 10:00 AM, 2:00 PM",
            execution_time_ms=245
        )
        recorder.log_barge_in()
        recorder.log_ai_audio(duration_ms=3000, bytes_sent=96000)
        recorder.log_error("test_error", "This is a test error")
        
        print_result("Log events", True, f"Logged 7 different event types")
        
        # Finalize
        recorder.finalize(status="COMPLETED")
        print_result("Finalize session", True, f"Status=COMPLETED")
        
        # Export
        session_data = recorder.export()
        
        # Validate schema
        assert "session_id" in session_data, "Missing session_id"
        assert "meta" in session_data, "Missing meta"
        assert "statistics" in session_data, "Missing statistics"
        assert "summary" in session_data, "Missing summary"
        assert "transcript" in session_data, "Missing transcript"
        
        assert session_data["meta"]["tenant_id"] == "test_tenant"
        assert session_data["meta"]["status"] == "COMPLETED"
        assert len(session_data["transcript"]) > 0
        
        # Check statistics
        stats = session_data["statistics"]
        assert stats["user_messages"] == 1  # One text message
        assert stats["ai_messages"] == 1    # One AI text message
        assert stats["tool_calls"] == 1      # One tool execution
        assert stats["errors"] == 1          # One error
        
        print_result(
            "Validate exported schema", 
            True, 
            f"Events: {stats['total_events']}, Duration: {session_data['meta']['duration_seconds']}s"
        )
        
        # Pretty print sample
        print("📋 Sample exported data:")
        print(json.dumps({
            "session_id": session_data["session_id"],
            "meta": session_data["meta"],
            "statistics": session_data["statistics"],
            "transcript_sample": session_data["transcript"][:3]
        }, indent=2))
        print()
        
        return session_data
        
    except Exception as e:
        print_result("SessionRecorder test", False, str(e))
        import traceback
        traceback.print_exc()
        return None


async def test_file_repository(session_data):
    """Test FileSessionRepository functionality."""
    print("=" * 60)
    print("TEST 2: FileSessionRepository")
    print("=" * 60)
    
    if not session_data:
        print_result("FileSessionRepository test", False, "No session data from previous test")
        return False
    
    try:
        # Create repository
        repo = FileSessionRepository(base_path="data/history")
        print_result("Create FileSessionRepository", True, "base_path=data/history")
        
        # Save session
        tenant_id = "test_tenant"
        file_path = await repo.save_session(tenant_id, session_data)
        print_result("Save session", True, f"Saved to: {file_path}")
        
        # Verify file exists
        path = Path(file_path)
        assert path.exists(), f"File not found: {file_path}"
        print_result("Verify file exists", True, f"Size: {path.stat().st_size} bytes")
        
        # Read file directly
        with open(file_path, 'r', encoding='utf-8') as f:
            file_content = json.load(f)
        
        assert file_content["session_id"] == session_data["session_id"]
        print_result("Verify file content", True, "JSON schema intact")
        
        # Retrieve via repository
        session_id = session_data["session_id"]
        retrieved = await repo.get_session(tenant_id, session_id)
        assert retrieved["session_id"] == session_id
        print_result("Retrieve session", True, f"session_id={session_id}")
        
        # List sessions
        sessions = await repo.list_sessions(tenant_id, limit=10)
        assert len(sessions) > 0, "No sessions found"
        assert any(s["session_id"] == session_id for s in sessions)
        print_result("List sessions", True, f"Found {len(sessions)} session(s)")
        
        # Print directory structure
        print("📁 Directory structure:")
        tenant_dir = Path("data/history") / tenant_id
        if tenant_dir.exists():
            files = list(tenant_dir.glob("*.json"))
            for f in files[:5]:  # Show first 5
                print(f"   - {f.name} ({f.stat().st_size} bytes)")
        print()
        
        return True
        
    except Exception as e:
        print_result("FileSessionRepository test", False, str(e))
        import traceback
        traceback.print_exc()
        return False


async def test_tenant_isolation():
    """Test that tenant data is properly isolated."""
    print("=" * 60)
    print("TEST 3: Tenant Isolation")
    print("=" * 60)
    
    try:
        repo = FileSessionRepository(base_path="data/history")
        
        # Create sessions for two different tenants
        tenant1_recorder = SessionRecorder("tenant_a", "session-a-001")
        tenant1_recorder.log_user_text("Tenant A message")
        tenant1_recorder.finalize()
        
        tenant2_recorder = SessionRecorder("tenant_b", "session-b-001")
        tenant2_recorder.log_user_text("Tenant B message")
        tenant2_recorder.finalize()
        
        # Save both
        await repo.save_session("tenant_a", tenant1_recorder.export())
        await repo.save_session("tenant_b", tenant2_recorder.export())
        
        print_result("Save sessions for different tenants", True, "tenant_a and tenant_b")
        
        # Verify directories
        tenant_a_dir = Path("data/history/tenant_a")
        tenant_b_dir = Path("data/history/tenant_b")
        
        assert tenant_a_dir.exists(), "Tenant A directory not created"
        assert tenant_b_dir.exists(), "Tenant B directory not created"
        print_result("Verify isolated directories", True, "Both tenant directories exist")
        
        # Verify tenant A can't access tenant B's data
        tenant_a_sessions = await repo.list_sessions("tenant_a")
        tenant_b_sessions = await repo.list_sessions("tenant_b")
        
        assert len(tenant_a_sessions) >= 1
        assert len(tenant_b_sessions) >= 1
        
        # Verify no cross-contamination
        tenant_a_ids = {s["session_id"] for s in tenant_a_sessions}
        tenant_b_ids = {s["session_id"] for s in tenant_b_sessions}
        
        assert not (tenant_a_ids & tenant_b_ids), "Session IDs leaked across tenants"
        print_result("Verify tenant isolation", True, f"tenant_a: {len(tenant_a_sessions)}, tenant_b: {len(tenant_b_sessions)}")
        
        return True
        
    except Exception as e:
        print_result("Tenant isolation test", False, str(e))
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run all tests."""
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    
    print()
    print("=" * 60)
    print(" SESSION PERSISTENCE TEST SUITE ".center(60))
    print("=" * 60)
    print()
    
    # Test 1: SessionRecorder
    session_data = await test_session_recorder()
    
    # Test 2: FileSessionRepository
    repo_success = await test_file_repository(session_data)
    
    # Test 3: Tenant Isolation
    isolation_success = await test_tenant_isolation()
    
    # Summary
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    all_passed = session_data is not None and repo_success and isolation_success
    
    if all_passed:
        print("✅ All tests PASSED!")
        print()
        print("Session persistence is working correctly:")
        print("  • SessionRecorder captures events")
        print("  • FileSessionRepository saves/retrieves sessions")
        print("  • Tenant data is properly isolated")
        print("  • JSON schema is correct")
        print()
        print("Next steps:")
        print("  1. Start the server: uvicorn src.main:app --reload")
        print("  2. Connect via WebSocket: /ws/call/barber_shop_demo")
        print("  3. Check data/history/{tenant_id}/ for saved sessions")
    else:
        print("❌ Some tests FAILED")
        print()
        print("Please check the errors above and fix the issues.")
    
    print("=" * 60)
    print()


if __name__ == "__main__":
    asyncio.run(main())
