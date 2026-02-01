"""
Audio Bridge - Manages dual WebSocket connections for real-time audio streaming.

This module implements the core streaming logic that connects:
1. Client WebSocket (User's phone/browser)
2. NVIDIA PersonaPlex WebSocket (External Docker container)

Architecture Pattern: SIDECAR PROXY
- PersonaPlex runs as an external microservice (Docker container)
- This bridge acts as a proxy/transcoder between client and PersonaPlex
- Handles audio format conversion, buffering, and barge-in logic
"""
import asyncio
import logging
import os
import subprocess
from typing import Optional, Callable, TYPE_CHECKING
from enum import Enum
import websockets
from fastapi import WebSocket

if TYPE_CHECKING:
    from src.core.history import SessionRecorder

logger = logging.getLogger(__name__)


class AudioFormat(Enum):
    """Supported audio formats."""
    WEBM_OPUS = "webm_opus"
    WAV = "wav"
    PCM_16 = "pcm_16"  # Raw PCM 16-bit


class ConnectionState(Enum):
    """WebSocket connection states."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"
    CLOSED = "closed"


class AudioBridge:
    """
    Manages bidirectional audio streaming between client and NVIDIA PersonaPlex.
    
    This class handles:
    - Dual WebSocket management (client + model)
    - Audio transcoding (client format <-> PCM for PersonaPlex)
    - Barge-in detection and handling
    - Error recovery and reconnection
    - Concurrent send/receive operations
    
    Architecture:
        [Client WS] <---> [AudioBridge] <---> [PersonaPlex WS]
                            |
                        [Transcoder]
                        [Buffer]
                        [Barge-in Handler]
    """
    
    def __init__(
        self,
        client_ws: WebSocket,
        tenant_id: str,
        session_id: str,
        session_recorder: Optional['SessionRecorder'] = None
    ):
        """
        Initialize the AudioBridge.
        
        Args:
            client_ws: FastAPI WebSocket connection from client
            tenant_id: Tenant identifier for logging/routing
            session_id: Unique session identifier
            session_recorder: Optional SessionRecorder for capturing conversation history
        """
        self.client_ws = client_ws
        self.tenant_id = tenant_id
        self.session_id = session_id
        self.recorder = session_recorder
        
        # PersonaPlex connection
        self.model_ws: Optional[websockets.WebSocketClientProtocol] = None
        self.model_url = os.getenv("PERSONAPLEX_WS_URL", "ws://localhost:9000/v1/audio-stream")
        
        # Audio configuration
        self.sample_rate = int(os.getenv("AUDIO_SAMPLE_RATE", "16000"))
        self.channels = int(os.getenv("AUDIO_CHANNELS", "1"))
        self.chunk_size = int(os.getenv("AUDIO_CHUNK_SIZE", "4096"))
        self.bit_depth = int(os.getenv("AUDIO_BIT_DEPTH", "16"))
        
        # State management
        self.client_state = ConnectionState.CONNECTED
        self.model_state = ConnectionState.DISCONNECTED
        self.is_running = False
        self.is_client_speaking = False
        self.is_model_speaking = False
        
        # Buffers and tasks
        self.client_buffer = asyncio.Queue(maxsize=50)
        self.model_buffer = asyncio.Queue(maxsize=50)
        self.tasks = []
        
        logger.info(
            f"AudioBridge initialized: session={session_id}, "
            f"tenant={tenant_id}, model_url={self.model_url}"
        )
    
    async def connect_model(self):
        """
        Connect to the NVIDIA PersonaPlex WebSocket (sidecar container).
        
        This method establishes the WebSocket connection to the external
        PersonaPlex microservice. It includes retry logic and timeout handling.
        
        Raises:
            ConnectionError: If unable to connect to PersonaPlex
            TimeoutError: If connection attempt times out
        """
        timeout = int(os.getenv("PERSONAPLEX_CONNECT_TIMEOUT", "10"))
        max_attempts = int(os.getenv("PERSONAPLEX_MAX_RECONNECT_ATTEMPTS", "3"))
        retry_delay = int(os.getenv("PERSONAPLEX_RECONNECT_DELAY", "2"))
        
        for attempt in range(1, max_attempts + 1):
            try:
                logger.info(
                    f"Connecting to PersonaPlex (attempt {attempt}/{max_attempts}): "
                    f"{self.model_url}"
                )
                
                self.model_state = ConnectionState.CONNECTING
                
                # Attempt WebSocket connection with timeout
                self.model_ws = await asyncio.wait_for(
                    websockets.connect(
                        self.model_url,
                        ping_interval=30,
                        ping_timeout=10,
                        close_timeout=5
                    ),
                    timeout=timeout
                )
                
                self.model_state = ConnectionState.CONNECTED
                logger.info(f"Successfully connected to PersonaPlex: {self.model_url}")
                return
                
            except asyncio.TimeoutError:
                logger.error(f"PersonaPlex connection timeout (attempt {attempt})")
                self.model_state = ConnectionState.ERROR
                
            except ConnectionRefusedError:
                logger.error(
                    f"PersonaPlex connection refused (attempt {attempt}). "
                    f"Is the Docker container running?"
                )
                self.model_state = ConnectionState.ERROR
                
            except Exception as e:
                logger.exception(f"PersonaPlex connection error (attempt {attempt}): {e}")
                self.model_state = ConnectionState.ERROR
            
            # Wait before retry (except on last attempt)
            if attempt < max_attempts:
                await asyncio.sleep(retry_delay)
        
        # All attempts failed
        raise ConnectionError(
            f"Failed to connect to PersonaPlex after {max_attempts} attempts. "
            f"Ensure Docker container is running at {self.model_url}"
        )
    
    async def disconnect_model(self):
        """Gracefully disconnect from PersonaPlex."""
        if self.model_ws:
            try:
                await self.model_ws.close()
                logger.info("Disconnected from PersonaPlex")
            except Exception as e:
                logger.error(f"Error disconnecting from PersonaPlex: {e}")
            finally:
                self.model_ws = None
                self.model_state = ConnectionState.CLOSED
    
    async def transcode_to_pcm(self, audio_data: bytes, input_format: AudioFormat) -> bytes:
        """
        Transcode audio from client format to PCM for PersonaPlex.
        
        Args:
            audio_data: Raw audio bytes from client
            input_format: Format of input audio
            
        Returns:
            PCM audio bytes (16kHz, 16-bit, mono)
        """
        if input_format == AudioFormat.PCM_16:
            # Already in correct format
            return audio_data
        
        try:
            # Build FFmpeg command for transcoding
            if input_format == AudioFormat.WEBM_OPUS:
                input_args = ["-f", "webm", "-codec:a", "libopus"]
            elif input_format == AudioFormat.WAV:
                input_args = ["-f", "wav"]
            else:
                logger.warning(f"Unsupported input format: {input_format}, passing through")
                return audio_data
            
            # FFmpeg transcoding pipeline
            ffmpeg_cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel", "error",
                *input_args,
                "-i", "pipe:0",  # Input from stdin
                "-f", "s16le",  # Output format: signed 16-bit little-endian
                "-ar", str(self.sample_rate),  # Sample rate
                "-ac", str(self.channels),  # Channels
                "pipe:1"  # Output to stdout
            ]
            
            # Run FFmpeg as subprocess
            process = await asyncio.create_subprocess_exec(
                *ffmpeg_cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            # Send input and get output
            stdout, stderr = await process.communicate(input=audio_data)
            
            if process.returncode != 0:
                logger.error(f"FFmpeg transcoding failed: {stderr.decode()}")
                return b""  # Return empty on error
            
            logger.debug(f"Transcoded {len(audio_data)} bytes to {len(stdout)} bytes PCM")
            return stdout
            
        except Exception as e:
            logger.exception(f"Audio transcoding error: {e}")
            return b""
    
    async def transcode_from_pcm(self, pcm_data: bytes, output_format: AudioFormat) -> bytes:
        """
        Transcode audio from PCM to client format.
        
        Args:
            pcm_data: Raw PCM bytes from PersonaPlex
            output_format: Desired output format for client
            
        Returns:
            Transcoded audio bytes
        """
        if output_format == AudioFormat.PCM_16:
            # Already in correct format
            return pcm_data
        
        try:
            # Build FFmpeg command for encoding
            if output_format == AudioFormat.WEBM_OPUS:
                output_args = ["-f", "webm", "-codec:a", "libopus", "-b:a", "24k"]
            elif output_format == AudioFormat.WAV:
                output_args = ["-f", "wav"]
            else:
                logger.warning(f"Unsupported output format: {output_format}, passing through")
                return pcm_data
            
            # FFmpeg encoding pipeline
            ffmpeg_cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel", "error",
                "-f", "s16le",  # Input format
                "-ar", str(self.sample_rate),
                "-ac", str(self.channels),
                "-i", "pipe:0",
                *output_args,
                "pipe:1"
            ]
            
            # Run FFmpeg
            process = await asyncio.create_subprocess_exec(
                *ffmpeg_cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate(input=pcm_data)
            
            if process.returncode != 0:
                logger.error(f"FFmpeg encoding failed: {stderr.decode()}")
                return b""
            
            logger.debug(f"Encoded {len(pcm_data)} bytes PCM to {len(stdout)} bytes")
            return stdout
            
        except Exception as e:
            logger.exception(f"Audio encoding error: {e}")
            return b""
    
    async def handle_client_to_model(self):
        """
        Stream audio from client to PersonaPlex model.
        
        This coroutine:
        1. Receives audio from client WebSocket
        2. Transcodes to PCM
        3. Sends to PersonaPlex
        4. Implements barge-in detection
        """
        logger.info("Starting client->model audio stream")
        
        try:
            while self.is_running:
                try:
                    # Receive audio from client
                    data = await asyncio.wait_for(
                        self.client_ws.receive_bytes(),
                        timeout=1.0
                    )
                    
                    if not data:
                        continue
                    
                    # Mark client as speaking (for barge-in detection)
                    self.is_client_speaking = True
                    
                    # Transcode to PCM for PersonaPlex
                    pcm_data = await self.transcode_to_pcm(
                        data, 
                        AudioFormat.WEBM_OPUS
                    )
                    
                    if pcm_data and self.model_ws:
                        # Send to PersonaPlex
                        await self.model_ws.send(pcm_data)
                        logger.debug(f"Sent {len(pcm_data)} bytes to PersonaPlex")
                        
                        # Log to session recorder
                        if self.recorder:
                            # Estimate duration: PCM 16-bit @ sample_rate
                            # bytes / (2 bytes_per_sample * channels * sample_rate) * 1000 = ms
                            duration_ms = int((len(pcm_data) / (2 * self.channels * self.sample_rate)) * 1000)
                            self.recorder.log_user_audio(duration_ms, len(data))
                    
                    # Reset speaking flag after short delay
                    await asyncio.sleep(0.1)
                    self.is_client_speaking = False
                    
                except asyncio.TimeoutError:
                    # No data received, continue
                    continue
                    
                except Exception as e:
                    logger.error(f"Error in client->model stream: {e}")
                    break
                    
        finally:
            logger.info("Client->model stream ended")
    
    async def handle_model_to_client(self):
        """
        Stream audio from PersonaPlex model to client.
        
        This coroutine:
        1. Receives PCM audio from PersonaPlex
        2. Transcodes to client format
        3. Sends to client WebSocket
        4. Implements barge-in (interruption) handling
        """
        logger.info("Starting model->client audio stream")
        
        try:
            while self.is_running and self.model_ws:
                try:
                    # Receive PCM audio from PersonaPlex
                    pcm_data = await asyncio.wait_for(
                        self.model_ws.recv(),
                        timeout=1.0
                    )
                    
                    if not pcm_data:
                        continue
                    
                    # BARGE-IN LOGIC: Don't send model audio if client is speaking
                    if self.is_client_speaking:
                        logger.debug("Barge-in detected: Dropping model audio")
                        if self.recorder:
                            self.recorder.log_barge_in()
                        continue
                    
                    self.is_model_speaking = True
                    
                    # Transcode to client format
                    client_data = await self.transcode_from_pcm(
                        pcm_data,
                        AudioFormat.WEBM_OPUS
                    )
                    
                    if client_data:
                        # Send to client
                        await self.client_ws.send_bytes(client_data)
                        logger.debug(f"Sent {len(client_data)} bytes to client")
                        
                        # Log to session recorder
                        if self.recorder:
                            # Estimate duration from PCM data
                            duration_ms = int((len(pcm_data) / (2 * self.channels * self.sample_rate)) * 1000)
                            self.recorder.log_ai_audio(duration_ms, len(client_data))
                    
                    self.is_model_speaking = False
                    
                except asyncio.TimeoutError:
                    # No data from model, continue
                    continue
                    
                except websockets.exceptions.ConnectionClosed:
                    logger.warning("PersonaPlex connection closed")
                    break
                    
                except Exception as e:
                    logger.error(f"Error in model->client stream: {e}")
                    break
                    
        finally:
            logger.info("Model->client stream ended")
    
    async def process_stream(self):
        """
        Main streaming loop - manages bidirectional audio flow.
        
        This method:
        1. Connects to PersonaPlex
        2. Starts concurrent send/receive tasks
        3. Handles graceful shutdown
        4. Manages error recovery
        """
        try:
            # Connect to PersonaPlex sidecar
            await self.connect_model()
            
            # Start streaming
            self.is_running = True
            logger.info(f"AudioBridge streaming started: session={self.session_id}")
            
            # Create concurrent tasks for bidirectional streaming
            client_to_model_task = asyncio.create_task(
                self.handle_client_to_model()
            )
            model_to_client_task = asyncio.create_task(
                self.handle_model_to_client()
            )
            
            self.tasks = [client_to_model_task, model_to_client_task]
            
            # Wait for either task to complete (or error)
            done, pending = await asyncio.wait(
                self.tasks,
                return_when=asyncio.FIRST_COMPLETED
            )
            
            # Cancel remaining tasks
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            
            logger.info("AudioBridge streaming completed")
            
        except ConnectionError as e:
            logger.error(f"Failed to connect to PersonaPlex: {e}")
            # Send error message to client
            try:
                await self.client_ws.send_json({
                    "type": "error",
                    "code": "personaplex_unavailable",
                    "message": "Audio service temporarily unavailable. Please try again later."
                })
            except:
                pass
            raise
            
        except Exception as e:
            logger.exception(f"AudioBridge error: {e}")
            raise
            
        finally:
            # Cleanup
            self.is_running = False
            await self.disconnect_model()
            logger.info(f"AudioBridge closed: session={self.session_id}")
    
    async def stop(self):
        """Stop the audio bridge and cleanup resources."""
        logger.info(f"Stopping AudioBridge: session={self.session_id}")
        self.is_running = False
        
        # Cancel all tasks
        for task in self.tasks:
            if not task.done():
                task.cancel()
        
        # Wait for tasks to finish
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        
        # Disconnect from PersonaPlex
        await self.disconnect_model()
        
        logger.info(f"AudioBridge stopped: session={self.session_id}")
