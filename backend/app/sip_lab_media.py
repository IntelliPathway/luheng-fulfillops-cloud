"""Bounded in-memory G.711 receive primitives, not a network/media-provider adapter."""
from dataclasses import dataclass, field
from struct import pack, unpack_from


class MediaPacketError(ValueError):
    """Fixed error codes only; never include packet bytes or audio in messages."""


@dataclass(frozen=True)
class AudioFrame:
    sequence: int
    timestamp: int
    sample_count: int
    pcm_s16le: bytes = field(repr=False)
    sample_rate: int = 8000
    channels: int = 1


def _sample(value, payload_type):
    if payload_type == 0:
        value = (~value) & 255
        magnitude = (((value & 15) << 3) + 132) << ((value >> 4) & 7)
        magnitude -= 132
        return -magnitude if value & 128 else magnitude
    value ^= 85
    magnitude = (value & 15) << 4
    exponent = (value >> 4) & 7
    magnitude += 8 if exponent == 0 else 264
    if exponent > 1:
        magnitude <<= exponent - 1
    return magnitude if value & 128 else -magnitude


def decode_packet(packet, *, payload_type, ssrc):
    """Only negotiated static PT0/PT8, 8 kHz mono plaintext RTP from a pinned SSRC."""
    if type(payload_type) is not int or payload_type not in {0, 8}:
        raise MediaPacketError('unsupported_payload_type')
    if type(ssrc) is not int or not 0 <= ssrc <= 0xFFFFFFFF:
        raise MediaPacketError('invalid_stream_binding')
    if not isinstance(packet, bytes) or not 12 <= len(packet) <= 4096:
        raise MediaPacketError('invalid_packet_size')
    flags, second, sequence, timestamp, source = unpack_from('!BBHII', packet)
    if flags >> 6 != 2:
        raise MediaPacketError('invalid_rtp_version')
    if second & 127 != payload_type or source != ssrc:
        raise MediaPacketError('stream_binding_mismatch')
    offset = 12 + 4 * (flags & 15)
    if offset > len(packet):
        raise MediaPacketError('truncated_csrc')
    if flags & 16:
        if offset + 4 > len(packet):
            raise MediaPacketError('truncated_extension')
        words = unpack_from('!H', packet, offset + 2)[0]
        offset += 4 + 4 * words
        if offset > len(packet):
            raise MediaPacketError('truncated_extension')
    end = len(packet)
    if flags & 32:
        padding = packet[-1]
        if padding == 0 or padding > end - offset:
            raise MediaPacketError('invalid_padding')
        end -= padding
    payload = packet[offset:end]
    if not 1 <= len(payload) <= 960:
        raise MediaPacketError('invalid_audio_payload_size')
    samples = [_sample(value, payload_type) for value in payload]
    return AudioFrame(sequence, timestamp, len(payload), pack(f'<{len(samples)}h', *samples))


class ReceiveStream:
    """No socket, persistence or jitter buffer; bounded one-stream decode and ordering."""
    def __init__(self, *, payload_type, ssrc, max_samples=480000):
        if type(max_samples) is not int or not 1 <= max_samples <= 480000:
            raise MediaPacketError('invalid_sample_budget')
        if type(payload_type) is not int or payload_type not in {0, 8}:
            raise MediaPacketError('unsupported_payload_type')
        if type(ssrc) is not int or not 0 <= ssrc <= 0xFFFFFFFF:
            raise MediaPacketError('invalid_stream_binding')
        self.payload_type, self.ssrc, self.max_samples = payload_type, ssrc, max_samples
        self.last_sequence = self.last_timestamp = None
        self.last_sample_count = 0
        self.samples_received = self.packets_received = self.missing_packets = 0

    def consume(self, packet):
        frame = decode_packet(packet, payload_type=self.payload_type, ssrc=self.ssrc)
        missing = 0
        if self.last_sequence is not None:
            distance = (frame.sequence - self.last_sequence) & 0xFFFF
            elapsed = (frame.timestamp - self.last_timestamp) & 0xFFFFFFFF
            if not 1 <= distance <= 0x7FFF:
                raise MediaPacketError('duplicate_or_out_of_order')
            if not self.last_sample_count <= elapsed <= 8000:
                raise MediaPacketError('invalid_timestamp_progress')
            missing = distance - 1
        if self.samples_received + frame.sample_count > self.max_samples:
            raise MediaPacketError('sample_budget_exhausted')
        self.last_sequence, self.last_timestamp = frame.sequence, frame.timestamp
        self.last_sample_count = frame.sample_count
        self.samples_received += frame.sample_count
        self.packets_received += 1
        self.missing_packets += missing
        return frame

    def summary(self):
        return {'sample_rate': 8000, 'channels': 1, 'samples_received': self.samples_received,
                'packets_received': self.packets_received, 'missing_packets': self.missing_packets,
                'audio_verified': False, 'ai_dialogue_ready': False, 'network_adapter_ready': False}
