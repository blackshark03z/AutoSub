"""
AutoSub Cloud V2 — High-Performance, DoS-Resilient Pure-Python Bencode Decoder & Encoder
Compliant with BEP 3 (BitTorrent Protocol Specification) and BEP 52 (BitTorrent v2).
"""

from typing import Any, Dict, List, Tuple, Union, Optional

MAX_TORRENT_FILE_SIZE = 10 * 1024 * 1024  # 10 MB maximum .torrent file
MAX_BENCODE_DEPTH = 32                     # Maximum recursion depth to prevent stack exhaustion DoS
MAX_STRING_LENGTH = 50 * 1024 * 1024      # 50 MB safety cap on single string length


class BencodeError(ValueError):
    """Base exception for bencode processing errors."""
    pass


class BencodeDecodeError(BencodeError):
    """Raised when bencoded data is malformed or truncated."""
    pass


class BencodeDepthError(BencodeError):
    """Raised when nested structures exceed the maximum recursion depth limit."""
    pass


class BencodeSizeError(BencodeError):
    """Raised when data exceeds maximum allowed input size."""
    pass


class BencodeDecoder:
    """
    Stateful bencode decoder with strict bounds checking, depth defense, and offset tracking.
    """

    def __init__(self, data: bytes, max_depth: int = MAX_BENCODE_DEPTH, max_size: int = MAX_TORRENT_FILE_SIZE):
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("Bencode data must be bytes or bytearray")
        if len(data) > max_size:
            raise BencodeSizeError(f"Data size {len(data)} exceeds maximum allowed limit of {max_size} bytes")

        self.data = bytes(data)
        self.length = len(self.data)
        self.pos = 0
        self.max_depth = max_depth
        self.info_slice: Optional[Tuple[int, int]] = None

    def decode(self) -> Any:
        """Decode the root bencoded object."""
        if self.length == 0:
            raise BencodeDecodeError("Cannot decode empty bencoded payload")

        val = self._decode_item(depth=0)
        if self.pos < self.length:
            raise BencodeDecodeError(f"Trailing unparsed bytes at position {self.pos}/{self.length}")
        return val

    def _decode_item(self, depth: int) -> Any:
        if depth > self.max_depth:
            raise BencodeDepthError(f"Bencode recursion depth limit ({self.max_depth}) exceeded")

        if self.pos >= self.length:
            raise BencodeDecodeError("Unexpected end of bencoded data")

        ch = self.data[self.pos : self.pos + 1]

        if ch == b"i":
            return self._decode_int()
        elif ch == b"l":
            return self._decode_list(depth)
        elif ch == b"d":
            return self._decode_dict(depth)
        elif ch.isdigit():
            return self._decode_string()
        else:
            raise BencodeDecodeError(f"Invalid token '{ch.decode('latin1', errors='replace')}' at index {self.pos}")

    def _decode_int(self) -> int:
        # Format: i<digits>e
        self.pos += 1  # Skip 'i'
        end = self.data.find(b"e", self.pos)
        if end == -1:
            raise BencodeDecodeError("Unterminated integer (missing 'e')")

        num_bytes = self.data[self.pos : end]
        if not num_bytes:
            raise BencodeDecodeError("Empty integer token 'ie'")

        # Invariants:
        # - Leading zeroes are invalid: "i03e" -> invalid. "i0e" -> valid.
        # - Negative zero is invalid: "i-0e" -> invalid.
        if num_bytes == b"-0":
            raise BencodeDecodeError("Negative zero is invalid in bencode")
        if len(num_bytes) > 1 and num_bytes.startswith(b"0"):
            raise BencodeDecodeError("Leading zeros are prohibited in bencode integers")
        if len(num_bytes) > 2 and num_bytes.startswith(b"-0"):
            raise BencodeDecodeError("Invalid negative number with leading zero")

        try:
            val = int(num_bytes)
        except ValueError as exc:
            raise BencodeDecodeError(f"Invalid integer literal: {num_bytes!r}") from exc

        self.pos = end + 1
        return val

    def _decode_string(self) -> bytes:
        # Format: <length>:<bytes>
        colon = self.data.find(b":", self.pos)
        if colon == -1:
            raise BencodeDecodeError("Unterminated string length (missing ':')")

        len_bytes = self.data[self.pos : colon]
        if not len_bytes.isdigit():
            raise BencodeDecodeError(f"Invalid string length prefix: {len_bytes!r}")
        if len(len_bytes) > 1 and len_bytes.startswith(b"0"):
            raise BencodeDecodeError("Leading zeros in string length are prohibited")

        str_len = int(len_bytes)
        if str_len > MAX_STRING_LENGTH:
            raise BencodeDecodeError(f"String length {str_len} exceeds max safety cap {MAX_STRING_LENGTH}")

        start = colon + 1
        end = start + str_len
        if end > self.length:
            raise BencodeDecodeError(
                f"String length {str_len} exceeds available payload size (available: {self.length - start})"
            )

        self.pos = end
        return self.data[start:end]

    def _decode_list(self, depth: int) -> List[Any]:
        # Format: l<items>e
        self.pos += 1  # Skip 'l'
        items: List[Any] = []
        while self.pos < self.length:
            if self.data[self.pos : self.pos + 1] == b"e":
                self.pos += 1
                return items
            items.append(self._decode_item(depth + 1))

        raise BencodeDecodeError("Unterminated list (missing 'e')")

    def _decode_dict(self, depth: int) -> Dict[bytes, Any]:
        # Format: d<k1><v1>...e
        dict_start = self.pos
        self.pos += 1  # Skip 'd'
        out: Dict[bytes, Any] = {}

        while self.pos < self.length:
            if self.data[self.pos : self.pos + 1] == b"e":
                self.pos += 1
                return out

            if not self.data[self.pos : self.pos + 1].isdigit():
                raise BencodeDecodeError(f"Dictionary key must be a string at position {self.pos}")

            key_bytes = self._decode_string()

            # Record raw offset slice for the top-level "info" dictionary
            val_start = self.pos
            val = self._decode_item(depth + 1)
            val_end = self.pos

            if depth == 0 and key_bytes == b"info":
                self.info_slice = (val_start, val_end)

            out[key_bytes] = val

        raise BencodeDecodeError("Unterminated dictionary (missing 'e')")


def bdecode(data: bytes, max_depth: int = MAX_BENCODE_DEPTH, max_size: int = MAX_TORRENT_FILE_SIZE) -> Any:
    """Decode bencoded bytes into Python primitives."""
    decoder = BencodeDecoder(data, max_depth=max_depth, max_size=max_size)
    return decoder.decode()


def bdecode_with_info_slice(
    data: bytes, max_depth: int = MAX_BENCODE_DEPTH, max_size: int = MAX_TORRENT_FILE_SIZE
) -> Tuple[Any, Optional[bytes]]:
    """
    Decode bencoded bytes and extract the raw, untouched slice for infohash computation.
    Returns: (decoded_structure, raw_info_bytes)
    """
    decoder = BencodeDecoder(data, max_depth=max_depth, max_size=max_size)
    result = decoder.decode()
    raw_info = None
    if decoder.info_slice is not None:
        start, end = decoder.info_slice
        raw_info = decoder.data[start:end]
    return result, raw_info


def bencode(obj: Any) -> bytes:
    """
    Encode Python primitives (int, bytes, str, list, dict) into canonical bencode.
    Dictionary keys are sorted lexicographically per BitTorrent specification.
    """
    if isinstance(obj, int) and not isinstance(obj, bool):
        return f"i{obj}e".encode("ascii")
    elif isinstance(obj, bytes):
        return f"{len(obj)}:".encode("ascii") + obj
    elif isinstance(obj, str):
        encoded = obj.encode("utf-8")
        return f"{len(encoded)}:".encode("ascii") + encoded
    elif isinstance(obj, (list, tuple)):
        parts = [b"l"]
        for item in obj:
            parts.append(bencode(item))
        parts.append(b"e")
        return b"".join(parts)
    elif isinstance(obj, dict):
        parts = [b"d"]
        # Convert keys to bytes and sort canonically
        sorted_keys = sorted(
            obj.keys(),
            key=lambda k: k if isinstance(k, bytes) else str(k).encode("utf-8")
        )
        for k in sorted_keys:
            if isinstance(k, bytes):
                k_bytes = k
            else:
                k_bytes = str(k).encode("utf-8")
            parts.append(f"{len(k_bytes)}:".encode("ascii") + k_bytes)
            parts.append(bencode(obj[k]))
        parts.append(b"e")
        return b"".join(parts)
    else:
        raise TypeError(f"Object of type {type(obj).__name__} is not bencode serializable")
