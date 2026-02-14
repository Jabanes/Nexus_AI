"""
Ogg/Opus Stream Parser - Robust Version
"""
from typing import List

class OggPageParser:
    def __init__(self):
        self.buffer = bytearray()
        self.partial_packet = bytearray()
        self.packets_sent = 0 # Track to skip headers

    def process(self, chunk: bytes) -> List[bytes]:
        self.buffer.extend(chunk)
        packets = []
        
        while len(self.buffer) >= 27:
            if self.buffer[0:4] != b'OggS':
                try:
                    next_o = self.buffer.index(b'O', 1)
                    del self.buffer[:next_o]
                    continue
                except ValueError:
                    if len(self.buffer) > 3:
                        del self.buffer[:-3]
                    break
            
            num_segments = self.buffer[26]
            header_size = 27 + num_segments
            if len(self.buffer) < header_size:
                break
                
            segment_table = self.buffer[27:header_size]
            body_size = sum(segment_table)
            page_size = header_size + body_size
            
            if len(self.buffer) < page_size:
                break
                
            page_body = self.buffer[header_size:page_size]
            cursor = 0
            for length in segment_table:
                self.partial_packet.extend(page_body[cursor:cursor+length])
                cursor += length
                
                if length < 255:
                    if self.partial_packet:
                        # Skip the first two Ogg packets (OpusHead and OpusTags)
                        if self.packets_sent >= 2:
                            packets.append(bytes(self.partial_packet))
                        self.packets_sent += 1
                        self.partial_packet = bytearray()
            
            del self.buffer[:page_size]
            
        return packets