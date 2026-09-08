"""Bounded-memory canonical log reader, including libzstd-only containers."""

from contextlib import contextmanager
import ctypes
import ctypes.util
import gzip
import io


class Buffer(ctypes.Structure):
    _fields_ = [("data", ctypes.c_void_p), ("size", ctypes.c_size_t),
                ("pos", ctypes.c_size_t)]


def zstd_chunks(path):
    lib = ctypes.CDLL(ctypes.util.find_library("zstd") or "libzstd.so.1")
    lib.ZSTD_createDStream.restype = ctypes.c_void_p
    lib.ZSTD_initDStream.argtypes = [ctypes.c_void_p]
    lib.ZSTD_initDStream.restype = ctypes.c_size_t
    lib.ZSTD_decompressStream.argtypes = [ctypes.c_void_p,
                                        ctypes.POINTER(Buffer), ctypes.POINTER(Buffer)]
    lib.ZSTD_decompressStream.restype = ctypes.c_size_t
    lib.ZSTD_isError.argtypes = [ctypes.c_size_t]
    lib.ZSTD_isError.restype = ctypes.c_uint
    lib.ZSTD_freeDStream.argtypes = [ctypes.c_void_p]
    stream = lib.ZSTD_createDStream()
    if not stream:
        raise MemoryError("ZSTD_createDStream")
    remaining = 1
    try:
        if lib.ZSTD_isError(lib.ZSTD_initDStream(stream)):
            raise ValueError("cannot initialize zstd stream")
        with path.open("rb") as source:
            while True:
                chunk = source.read(131072)
                if not chunk:
                    break
                raw = ctypes.create_string_buffer(chunk)
                inp = Buffer(ctypes.cast(raw, ctypes.c_void_p), len(chunk), 0)
                while inp.pos < inp.size:
                    raw_out = ctypes.create_string_buffer(131072)
                    out = Buffer(ctypes.cast(raw_out, ctypes.c_void_p), 131072, 0)
                    remaining = lib.ZSTD_decompressStream(stream, ctypes.byref(out), ctypes.byref(inp))
                    if lib.ZSTD_isError(remaining):
                        raise ValueError(f"corrupt zstd log: {path}")
                    yield raw_out.raw[:out.pos]
        if remaining != 0:
            raise ValueError(f"truncated zstd log: {path}")
    finally:
        lib.ZSTD_freeDStream(stream)


class ChunkReader(io.RawIOBase):
    def __init__(self, chunks):
        self.chunks = iter(chunks)
        self.pending = b""

    def readable(self):
        return True

    def readinto(self, target):
        while not self.pending:
            self.pending = next(self.chunks, b"")
            if not self.pending:
                return 0
        count = min(len(target), len(self.pending))
        target[:count] = self.pending[:count]
        self.pending = self.pending[count:]
        return count


@contextmanager
def open_log(path):
    if path.suffix == ".zst":
        chunks = zstd_chunks(path)
        try:
            with io.TextIOWrapper(io.BufferedReader(ChunkReader(chunks)),
                                  encoding="utf-8", errors="strict") as stream:
                yield stream
        finally:
            chunks.close()
    elif path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            yield stream
    else:
        with path.open(encoding="utf-8") as stream:
            yield stream


def canonical_log(base):
    """Require exactly one representation; never silently omit a process."""
    matches = [p for p in (base, base.with_name(base.name + ".gz"),
                          base.with_name(base.name + ".zst")) if p.is_file()]
    if len(matches) != 1:
        raise ValueError(f"expected one canonical stream for {base}, got {matches}")
    return matches[0]
