"""
Mock NVIDIA PersonaPlex Server

This is a simple WebSocket echo server that simulates the PersonaPlex sidecar
for development and testing purposes. It accepts audio on port 9000 and echoes
it back with a small delay to simulate processing.

Usage:
    python tests/mock_personaplex.py
"""

import asyncio
import websockets
import logging
import sys
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [MOCK-PERSONAPLEX] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


class MockPersonaPlex:
    """Simple mock server that echoes audio back to test the audio bridge."""
    
    def __init__(self, host: str = "localhost", port: int = 9000):
        self.host = host
        self.port = port
        self.active_connections = 0
        self.total_bytes_received = 0
        self.total_bytes_sent = 0
    
    async def handle_client(self, websocket, path):
        """
        Handle a client connection.
        
        Receives audio chunks and echoes them back with a small delay
        to simulate GPU processing time.
        """
        client_id = f"{websocket.remote_address[0]}:{websocket.remote_address[1]}"
        self.active_connections += 1
        
        logger.info(f"✅ Client connected: {client_id} (Total: {self.active_connections})")
        
        try:
            async for message in websocket:
                # Handle binary audio data
                if isinstance(message, bytes):
                    bytes_received = len(message)
                    self.total_bytes_received += bytes_received
                    
                    logger.debug(f"📥 Received {bytes_received} bytes from {client_id}")
                    
                    # Simulate processing delay (GPU inference time)
                    await asyncio.sleep(0.1)
                    
                    # Echo back the audio
                    await websocket.send(message)
                    self.total_bytes_sent += bytes_received
                    
                    logger.debug(f"📤 Sent {bytes_received} bytes to {client_id}")
                
                # Handle text/JSON messages (if any)
                elif isinstance(message, str):
                    logger.info(f"📨 Text message from {client_id}: {message[:100]}")
                    # Could handle control messages here
                    await websocket.send(f"ACK: {message}")
        
        except websockets.exceptions.ConnectionClosedOK:
            logger.info(f"👋 Client disconnected normally: {client_id}")
        
        except websockets.exceptions.ConnectionClosedError as e:
            logger.warning(f"⚠️  Client disconnected with error: {client_id} - {e}")
        
        except Exception as e:
            logger.error(f"❌ Error handling client {client_id}: {e}")
        
        finally:
            self.active_connections -= 1
            logger.info(
                f"📊 Stats - Active: {self.active_connections}, "
                f"Received: {self.total_bytes_received/1024:.1f}KB, "
                f"Sent: {self.total_bytes_sent/1024:.1f}KB"
            )
    
    async def start(self):
        """Start the mock server."""
        logger.info("=" * 60)
        logger.info("🚀 Mock NVIDIA PersonaPlex Server Starting")
        logger.info("=" * 60)
        logger.info(f"Host: {self.host}")
        logger.info(f"Port: {self.port}")
        logger.info(f"WebSocket URL: ws://{self.host}:{self.port}/v1/audio-stream")
        logger.info("=" * 60)
        logger.info("")
        logger.info("📡 Waiting for connections...")
        logger.info("   (Press Ctrl+C to stop)")
        logger.info("")
        
        try:
            async with websockets.serve(
                self.handle_client,
                self.host,
                self.port,
                ping_interval=30,
                ping_timeout=10
            ):
                # Run forever
                await asyncio.Future()
        
        except OSError as e:
            if "address already in use" in str(e).lower():
                logger.error("")
                logger.error("=" * 60)
                logger.error(f"❌ ERROR: Port {self.port} is already in use!")
                logger.error("=" * 60)
                logger.error("")
                logger.error("Possible solutions:")
                logger.error(f"  1. Stop the process using port {self.port}")
                logger.error(f"  2. Change PERSONAPLEX_WS_URL in .env")
                logger.error("")
                sys.exit(1)
            raise


async def main():
    """Main entry point."""
    # Allow port override via command line
    import sys
    port = 9000
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            logger.error(f"Invalid port: {sys.argv[1]}")
            sys.exit(1)
    
    server = MockPersonaPlex(port=port)
    
    try:
        await server.start()
    except KeyboardInterrupt:
        logger.info("")
        logger.info("=" * 60)
        logger.info("🛑 Shutting down Mock PersonaPlex...")
        logger.info("=" * 60)
        logger.info("")
        logger.info(f"Final Stats:")
        logger.info(f"  Total Bytes Received: {server.total_bytes_received/1024:.1f} KB")
        logger.info(f"  Total Bytes Sent: {server.total_bytes_sent/1024:.1f} KB")
        logger.info("")
        logger.info("👋 Goodbye!")


if __name__ == "__main__":
    # Ensure UTF-8 output on Windows
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding='utf-8')
    
    asyncio.run(main())
