"""
Raw Opus Decoder - Matches NVIDIA decoderWorker behavior.

Decodes at 48kHz (native Opus) then resamples to 24kHz for client output.
Outputs PCM Float32 mono.
"""
import logging
import struct
import traceback
from typing import Optional

logger = logging.getLogger(__name__)

# --- Simple Linear Resampler (48k -> 24k = factor 2 decimation) ---
def resample_48k_to_24k(pcm_float32_bytes: bytes) -> bytes:
    """
    Downsample Float32 PCM from 48kHz to 24kHz by taking every 2nd sample.
    This is a simple decimation. For production, consider a proper low-pass
    filter before decimation, but for voice this is adequate.
    """
    # Each sample is 4 bytes (float32)
    num_samples = len(pcm_float32_bytes) // 4
    if num_samples == 0:
        return b''
    
    # Unpack all float32 samples
    samples = struct.unpack(f'<{num_samples}f', pcm_float32_bytes)
    
    # Decimation by 2 (simple averaging of adjacent pairs for basic anti-alias)
    out = []
    for i in range(0, num_samples - 1, 2):
        avg = (samples[i] + samples[i + 1]) * 0.5
        out.append(avg)
    
    return struct.pack(f'<{len(out)}f', *out)


class RawOpusDecoder:
    """
    Decodes raw Opus packets to PCM Float32 mono.
    
    Matches NVIDIA decoderWorker:
    - Decode at 48000 Hz (native Opus sample rate)
    - Output resampled to 24000 Hz for client AudioContext
    - Persistent decoder state (never reset between frames)
    """
    
    DECODE_SAMPLE_RATE = 48000  # Match NVIDIA
    OUTPUT_SAMPLE_RATE = 24000  # Client AudioContext rate
    CHANNELS = 1
    FRAME_SIZE_80MS = 3840      # 80ms at 48kHz = 3840 samples
    
    def __init__(self):
        self._decoder = None
        self.decode_count = 0
        self.error_count = 0
        
        try:
            import opuslib
            self._decoder = opuslib.Decoder(self.DECODE_SAMPLE_RATE, self.CHANNELS)
            logger.info(
                f"✅ RawOpusDecoder initialized "
                f"(decode@{self.DECODE_SAMPLE_RATE}Hz → output@{self.OUTPUT_SAMPLE_RATE}Hz, "
                f"{self.CHANNELS}ch, frame_size={self.FRAME_SIZE_80MS})"
            )
        except ImportError:
            logger.error("❌ opuslib not found! Run: pip install opuslib")
        except Exception as e:
            logger.error(f"❌ Failed to initialize libopus: {e}")
            logger.error(traceback.format_exc())

    def decode(self, packet: bytes) -> bytes:
        """
        Decode a single raw Opus packet to Float32 PCM bytes at 24kHz.
        
        Returns empty bytes on failure (never raises).
        """
        if not self._decoder:
            return b''

        try:
            # Decode at 48kHz (NVIDIA behavior)
            if hasattr(self._decoder, 'decode_float'):
                pcm_48k = self._decoder.decode_float(packet, self.FRAME_SIZE_80MS)
            else:
                # Fallback: decode to int16 then convert
                pcm_int16 = self._decoder.decode(packet, self.FRAME_SIZE_80MS)
                num_samples = len(pcm_int16) // 2
                int16_vals = struct.unpack(f'<{num_samples}h', pcm_int16)
                float_vals = [s / 32768.0 for s in int16_vals]
                pcm_48k = struct.pack(f'<{num_samples}f', *float_vals)
            
            self.decode_count += 1
            
            # Resample 48k -> 24k
            pcm_24k = resample_48k_to_24k(pcm_48k)
            return pcm_24k
            
        except Exception as e:
            self.error_count += 1
            logger.error(f"Opus decode error #{self.error_count}: {e}")
            logger.error(traceback.format_exc())
            return b''
