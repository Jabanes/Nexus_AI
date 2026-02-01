"""
Nexus Voice Engine - Development Startup Script

This script provides a zero-friction development experience by:
1. Running pre-flight checks (FFmpeg, ports, environment)
2. Starting the Mock PersonaPlex sidecar
3. Starting the Nexus Engine server
4. Handling graceful shutdown of both processes

Usage:
    python scripts/start_dev.py [--port PORT]
"""

import asyncio
import subprocess
import sys
import os
import socket
import shutil
import signal
from pathlib import Path
from typing import Optional, List, Tuple

# Color codes for terminal output
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    BOLD = '\033[1m'
    END = '\033[0m'

# Disable colors on Windows if not supported
if sys.platform == "win32":
    try:
        import colorama
        colorama.init()
    except ImportError:
        # Disable colors if colorama not available
        for attr in dir(Colors):
            if not attr.startswith('_'):
                setattr(Colors, attr, '')


def print_header(title: str):
    """Print a formatted header."""
    print()
    print("=" * 70)
    print(f"{Colors.BOLD}{Colors.CYAN}{title.center(70)}{Colors.END}")
    print("=" * 70)
    print()


def print_check(name: str, passed: bool, message: str = ""):
    """Print a check result."""
    status = f"{Colors.GREEN}✅{Colors.END}" if passed else f"{Colors.RED}❌{Colors.END}"
    print(f"{status} [{Colors.BOLD}CHECK{Colors.END}] {name}", end="")
    if message:
        print(f" {Colors.YELLOW}→{Colors.END} {message}")
    else:
        print()


def print_info(message: str):
    """Print an info message."""
    print(f"{Colors.BLUE}ℹ{Colors.END}  [{Colors.BOLD}INFO{Colors.END}] {message}")


def print_error(message: str):
    """Print an error message."""
    print(f"{Colors.RED}✗{Colors.END}  [{Colors.BOLD}ERROR{Colors.END}] {message}")


def print_success(message: str):
    """Print a success message."""
    print(f"{Colors.GREEN}✓{Colors.END}  [{Colors.BOLD}SUCCESS{Colors.END}] {message}")


def is_port_free(port: int) -> bool:
    """Check if a port is free."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(('0.0.0.0', port))
        sock.close()
        return True
    except OSError:
        return False


def find_free_port(start_port: int, max_attempts: int = 10) -> Optional[int]:
    """Find a free port starting from start_port."""
    for port in range(start_port, start_port + max_attempts):
        if is_port_free(port):
            return port
    return None


def check_ffmpeg() -> bool:
    """Check if FFmpeg is installed and accessible."""
    try:
        result = subprocess.run(
            ['ffmpeg', '-version'],
            capture_output=True,
            timeout=5
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False
    except Exception:
        return False


def check_env_file() -> Tuple[bool, str]:
    """Check if .env file exists, create from example if not."""
    env_path = Path('.env')
    example_path = Path('env.example.new')
    
    if env_path.exists():
        return True, ".env file exists"
    
    if not example_path.exists():
        return False, ".env and env.example.new not found"
    
    # Copy example to .env
    try:
        shutil.copy(example_path, env_path)
        return True, "Created .env from env.example.new"
    except Exception as e:
        return False, f"Failed to create .env: {e}"


def check_virtual_env() -> bool:
    """Check if running in a virtual environment."""
    return hasattr(sys, 'real_prefix') or (
        hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix
    )


def run_preflight_checks() -> Tuple[bool, int]:
    """
    Run all pre-flight checks.
    
    Returns:
        Tuple of (all_passed, nexus_port)
    """
    print_header("PRE-FLIGHT CHECKS")
    
    all_passed = True
    
    # Check 1: Virtual Environment
    venv_active = check_virtual_env()
    print_check(
        "Virtual Environment",
        venv_active,
        "Active" if venv_active else "Not active (recommended to use venv)"
    )
    if not venv_active:
        print_info("Consider running: python -m venv env && env\\Scripts\\activate")
    
    # Check 2: FFmpeg
    ffmpeg_ok = check_ffmpeg()
    print_check("FFmpeg", ffmpeg_ok, "Found in PATH" if ffmpeg_ok else "NOT FOUND")
    if not ffmpeg_ok:
        print_error("FFmpeg is required for audio transcoding")
        print_info("Install FFmpeg:")
        print_info("  Windows: Download from https://ffmpeg.org/download.html")
        print_info("  macOS: brew install ffmpeg")
        print_info("  Linux: sudo apt-get install ffmpeg")
        all_passed = False
    
    # Check 3: Environment File
    env_ok, env_msg = check_env_file()
    print_check("Environment File", env_ok, env_msg)
    if not env_ok:
        all_passed = False
    
    # Check 4: Port 9000 (Mock PersonaPlex)
    mock_port_free = is_port_free(9000)
    print_check(
        "Port 9000 (Mock Sidecar)",
        mock_port_free,
        "Available" if mock_port_free else "In use (will fail to start)"
    )
    if not mock_port_free:
        print_info("Kill the process using port 9000 or change PERSONAPLEX_WS_URL in .env")
    
    # Check 5: Port 8000 (Nexus Engine)
    nexus_port = 8000
    nexus_port_free = is_port_free(nexus_port)
    
    if not nexus_port_free:
        # Try to find alternative port
        alt_port = find_free_port(8001)
        if alt_port:
            print_check(
                f"Port {nexus_port} (Nexus Engine)",
                False,
                f"In use, switching to {alt_port}"
            )
            nexus_port = alt_port
        else:
            print_check(
                f"Port {nexus_port} (Nexus Engine)",
                False,
                "In use and no alternatives found"
            )
            all_passed = False
    else:
        print_check(f"Port {nexus_port} (Nexus Engine)", True, "Available")
    
    # Check 6: Dependencies
    try:
        import fastapi
        import websockets
        import google.generativeai
        deps_ok = True
    except ImportError as e:
        deps_ok = False
        print_check("Python Dependencies", False, f"Missing: {e.name}")
        print_info("Run: pip install -r requirements.txt")
        all_passed = False
    
    if deps_ok:
        print_check("Python Dependencies", True, "All installed")
    
    print()
    
    return all_passed, nexus_port


class ProcessManager:
    """Manages multiple subprocesses with graceful shutdown."""
    
    def __init__(self):
        self.processes: List[subprocess.Popen] = []
        self.shutdown_requested = False
    
    def start_process(self, name: str, command: List[str], cwd: str = None) -> subprocess.Popen:
        """Start a subprocess."""
        print_info(f"Starting {name}...")
        
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True
            )
            self.processes.append(process)
            print_success(f"{name} started (PID: {process.pid})")
            return process
        except Exception as e:
            print_error(f"Failed to start {name}: {e}")
            raise
    
    async def stream_output(self, process: subprocess.Popen, prefix: str):
        """Stream process output with prefix."""
        try:
            while not self.shutdown_requested:
                line = process.stdout.readline()
                if not line:
                    # Process ended
                    break
                print(f"{Colors.CYAN}[{prefix}]{Colors.END} {line.rstrip()}")
        except Exception as e:
            if not self.shutdown_requested:
                print_error(f"Error streaming {prefix}: {e}")
    
    def shutdown_all(self):
        """Shutdown all managed processes."""
        if self.shutdown_requested:
            return
        
        self.shutdown_requested = True
        
        print()
        print_header("SHUTTING DOWN")
        
        for process in self.processes:
            if process.poll() is None:  # Still running
                print_info(f"Stopping process {process.pid}...")
                try:
                    process.terminate()
                    process.wait(timeout=5)
                    print_success(f"Process {process.pid} stopped")
                except subprocess.TimeoutExpired:
                    print_info(f"Force killing process {process.pid}...")
                    process.kill()
                    print_success(f"Process {process.pid} killed")
                except Exception as e:
                    print_error(f"Error stopping process {process.pid}: {e}")
        
        print()
        print_success("All processes stopped")
        print()


async def run_development_environment(nexus_port: int):
    """Run the development environment with both services."""
    print_header("STARTING DEVELOPMENT ENVIRONMENT")
    
    manager = ProcessManager()
    
    # Setup signal handlers for graceful shutdown
    def signal_handler(signum, frame):
        print()
        print_info("Received shutdown signal (Ctrl+C)")
        manager.shutdown_all()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # Start Mock PersonaPlex
        python_exe = sys.executable
        mock_process = manager.start_process(
            "Mock PersonaPlex Sidecar",
            [python_exe, "tests/mock_personaplex.py"]
        )
        
        # Wait a bit for mock to start
        await asyncio.sleep(1)
        
        # Start Nexus Engine
        nexus_process = manager.start_process(
            "Nexus Voice Engine",
            [
                python_exe, "-m", "uvicorn",
                "src.main:app",
                "--host", "0.0.0.0",
                "--port", str(nexus_port),
                "--reload"
            ]
        )
        
        print()
        print_header("SERVICES RUNNING")
        print(f"{Colors.GREEN}✓{Colors.END} Mock PersonaPlex: ws://localhost:9000/v1/audio-stream")
        print(f"{Colors.GREEN}✓{Colors.END} Nexus Engine: http://localhost:{nexus_port}")
        print(f"{Colors.GREEN}✓{Colors.END} WebSocket Endpoint: ws://localhost:{nexus_port}/ws/call/{{tenant_id}}")
        print()
        print(f"{Colors.YELLOW}Press Ctrl+C to stop all services{Colors.END}")
        print()
        print_header("LOGS")
        
        # Stream outputs from both processes
        await asyncio.gather(
            manager.stream_output(mock_process, "MOCK"),
            manager.stream_output(nexus_process, "NEXUS")
        )
    
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print_error(f"Error running services: {e}")
    finally:
        manager.shutdown_all()


def main():
    """Main entry point."""
    # Ensure we're in the project root
    if not Path('src/main.py').exists():
        print_error("Must run from project root directory")
        print_info("cd to the nexus-voice-engine directory first")
        sys.exit(1)
    
    # Parse arguments
    import argparse
    parser = argparse.ArgumentParser(description="Start Nexus Voice Engine development environment")
    parser.add_argument('--port', type=int, default=8000, help='Nexus Engine port (default: 8000)')
    parser.add_argument('--skip-checks', action='store_true', help='Skip pre-flight checks')
    args = parser.parse_args()
    
    print()
    print(f"{Colors.BOLD}{Colors.CYAN}╔{'═' * 68}╗{Colors.END}")
    print(f"{Colors.BOLD}{Colors.CYAN}║{'Nexus Voice Engine - Development Startup'.center(68)}║{Colors.END}")
    print(f"{Colors.BOLD}{Colors.CYAN}╚{'═' * 68}╝{Colors.END}")
    
    # Run pre-flight checks
    if not args.skip_checks:
        checks_passed, nexus_port = run_preflight_checks()
        
        if not checks_passed:
            print()
            print_error("Pre-flight checks FAILED")
            print_info("Fix the issues above and try again")
            print_info("Or use --skip-checks to bypass (not recommended)")
            sys.exit(1)
        
        print_success("All pre-flight checks PASSED")
    else:
        print_info("Skipping pre-flight checks...")
        nexus_port = args.port
    
    # Start the development environment
    try:
        asyncio.run(run_development_environment(nexus_port))
    except KeyboardInterrupt:
        print()
        print_info("Shutdown complete")
    except Exception as e:
        print()
        print_error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    # Ensure UTF-8 output on Windows
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except AttributeError:
            pass
    
    main()
