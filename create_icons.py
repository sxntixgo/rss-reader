"""Generate minimal placeholder PWA icons. Run once during Docker build."""
import struct
import zlib
from pathlib import Path


def make_png(size: int, r: int, g: int, b: int) -> bytes:
    def chunk(name: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(name + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", crc)

    ihdr_data = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes([r, g, b] * size)
    idat_data = zlib.compress(row * size)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr_data)
        + chunk(b"IDAT", idat_data)
        + chunk(b"IEND", b"")
    )


if __name__ == "__main__":
    out = Path("static")
    for size in (192, 512):
        path = out / f"icon-{size}.png"
        if not path.exists():
            path.write_bytes(make_png(size, 26, 26, 26))  # #1a1a1a solid
            print(f"Created {path}")
