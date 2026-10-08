#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["pycryptodome"]
# ///
"""
Repack a Sony WH-1000XM4 voice-guidance image (.bin) with new prompt MP3s.

Reverses extract_all.py's pipeline: takes a decompressed voice-guidance image
(the 8-byte header + 54-entry table + concatenated MP3s that extract_all.py
produces internally before splitting it into prompt_NN.mp3 files), LZMA1-
compresses it with the same properties Sony's own encoder uses, AES-128-CBC
encrypts it with the known key/IV, and wraps it in the same TLV container
format documented in docs/WRITEUP.md and cross-checked against
airoha-firmware-parser's format spec.

This does NOT talk to any headphones. It only builds files on disk. The
--roundtrip mode is the key correctness check: it decodes a real Sony .bin,
rebuilds it with zero content changes, and verifies the rebuilt file
decompresses back to byte-identical content.

Usage:
    # Correctness check against a real file (does not require new audio)
    uv run cli/repack.py --roundtrip voice-packs/VP_english_UPG_03.bin

    # Build a new voice pack from a directory of 54 prompt_NN.mp3 files
    uv run cli/repack.py --build extracted/english/ output/VP_custom.bin
"""
import hashlib
import lzma
import struct
import sys
from pathlib import Path

from Crypto.Cipher import AES

KEY = b"eibohjeCh6uegahf"
IV = b"miefeinuShu9eilo"

LZMA_LC, LZMA_LP, LZMA_PB = 3, 0, 2
LZMA_DICT_SIZE = 0x4000
LZMA_PROPS_BYTE = (LZMA_PB * 5 + LZMA_LP) * 9 + LZMA_LC  # 0x5D, matches WRITEUP.md

VOICE_IMAGE_SIZE = 0x100000  # 1 MB, fixed budget — MOVER_INFO.decompressed_size
ENTRY_TABLE_BASE_OFFSET = 0x80000  # matches Sony's own convention (docs/WRITEUP.md)
DEST_OFFSET = 0x0C380000  # MOVER_INFO.dest_offset, fixed across all real packs — do not change
FIRMWARE_OFFSET = 0x1000  # TLV BASIC_INFO.firmware_offset, i.e. header size before the encrypted body

TLV_TYPE_END = 0xFFFF
TLV_BASIC_INFO = 0x11
TLV_MOVER_INFO = 0x12
TLV_VERSION_INFO = 0x13
TLV_INTEGRITY_VERIFY_INFO = 0x14

VERSION_STRING_PAYLOAD = b"verion_string\x00" + b"\xff" * 14  # Sony's own typo, preserved exactly; 28 bytes


def lzma_compress(decompressed: bytes) -> bytes:
    """LZMA1 raw-compress + prepend the classic 13-byte .lzma header, then
    pad with 0xFF to a 16-byte boundary (matches the real encrypted bodies)."""
    filters = [{"id": lzma.FILTER_LZMA1, "lc": LZMA_LC, "lp": LZMA_LP, "pb": LZMA_PB, "dict_size": LZMA_DICT_SIZE}]
    compressor = lzma.LZMACompressor(format=lzma.FORMAT_RAW, filters=filters)
    compressed = compressor.compress(decompressed) + compressor.flush()

    header = bytes([LZMA_PROPS_BYTE]) + struct.pack("<I", LZMA_DICT_SIZE) + struct.pack("<Q", len(decompressed))
    stream = header + compressed
    pad_len = (-len(stream)) % 16
    stream += b"\xff" * pad_len
    return stream


def lzma_decompress(stream: bytes, expected_size: int) -> bytes:
    props = stream[0]
    lc, lp, pb = props % 9, (props // 9) % 5, (props // 9) // 5
    dict_size = struct.unpack_from("<I", stream, 1)[0]
    filters = [{"id": lzma.FILTER_LZMA1, "lc": lc, "lp": lp, "pb": pb, "dict_size": dict_size}]
    d = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=filters)
    return d.decompress(stream[13:], max_length=expected_size)


def parse_tlvs(data: bytes, start: int = 256):
    """Walk the TLV block starting right after the 256-byte checksum+padding header."""
    tlvs = {}
    off = start
    while True:
        type_, length = struct.unpack_from("<HH", data, off)
        if type_ == TLV_TYPE_END:
            break
        tlvs[type_] = data[off + 4: off + 4 + length]
        off += 4 + length
    return tlvs, off


def decode_bin(path: Path):
    """Decrypt + decompress a real voice-pack .bin. Returns the 1MB decompressed image."""
    data = path.read_bytes()
    tlvs, _ = parse_tlvs(data)

    ctype, itype, fw_offset, fw_size = struct.unpack_from("<BBII", tlvs[TLV_BASIC_INFO])
    assert ctype == 2, f"unexpected compression_type {ctype}, expected LZMA_AES"
    assert itype == 1, f"unexpected integrity_check_type {itype}, expected SHA256"

    body = data[fw_offset: fw_offset + fw_size]
    cipher = AES.new(KEY, AES.MODE_CBC, IV)
    decrypted = cipher.decrypt(body)

    n_sections = struct.unpack_from("<I", tlvs[TLV_MOVER_INFO])[0]
    assert n_sections == 1, f"expected 1 MOVER_INFO section, got {n_sections}"
    src_off, decompressed_size, dest_offset = struct.unpack_from("<III", tlvs[TLV_MOVER_INFO], 4)

    decompressed = lzma_decompress(decrypted, decompressed_size)

    n_checksums = struct.unpack_from("<I", tlvs[TLV_INTEGRITY_VERIFY_INFO])[0]
    checksum = tlvs[TLV_INTEGRITY_VERIFY_INFO][4:4 + 32]
    actual = hashlib.sha256(decompressed).digest()
    assert actual == checksum, "INTEGRITY_VERIFY_INFO checksum does not match decompressed content!"

    return decompressed, {"dest_offset": dest_offset, "version_string": tlvs[TLV_VERSION_INFO]}


def parse_voice_image_entries(decompressed: bytes):
    """Split a decompressed voice-guidance image back into its 54 MP3 blobs."""
    version, num_entries = struct.unpack_from("<II", decompressed, 0)
    table_end = 8 + num_entries * 8
    base_offset = struct.unpack_from("<I", decompressed, 8 + 4)[0] - table_end

    prompts = []
    for i in range(num_entries):
        off = 8 + i * 8
        size, abs_offset = struct.unpack_from("<II", decompressed, off)
        file_offset = abs_offset - base_offset
        prompts.append(decompressed[file_offset: file_offset + size])
    return prompts, base_offset, version


def pack_voice_image(prompts: list[bytes], base_offset: int = ENTRY_TABLE_BASE_OFFSET, version: int = 1) -> bytes:
    """Build a decompressed voice-guidance image (header + entry table + MP3s + zero padding).

    `version` isn't a constant — real packs carry 1 or 2 depending on language/revision.
    Preserve whatever the source file used rather than assuming."""
    num_entries = len(prompts)
    table_end = 8 + num_entries * 8

    entries = b""
    blob = b""
    file_offset = table_end
    for mp3 in prompts:
        entries += struct.pack("<II", len(mp3), base_offset + file_offset)
        blob += mp3
        file_offset += len(mp3)

    image = struct.pack("<II", version, num_entries) + entries + blob
    if len(image) > VOICE_IMAGE_SIZE:
        raise ValueError(
            f"packed image is {len(image)} bytes, over the {VOICE_IMAGE_SIZE}-byte budget "
            f"by {len(image) - VOICE_IMAGE_SIZE} bytes — shorten/re-encode some prompts"
        )
    image += b"\x00" * (VOICE_IMAGE_SIZE - len(image))
    return image


def build_container(decompressed: bytes, dest_offset: int = DEST_OFFSET, version_string: bytes = VERSION_STRING_PAYLOAD) -> bytes:
    assert len(decompressed) == VOICE_IMAGE_SIZE, f"decompressed image must be exactly {VOICE_IMAGE_SIZE} bytes"

    encrypted_stream = lzma_compress(decompressed)
    cipher = AES.new(KEY, AES.MODE_CBC, IV)
    encrypted_body = cipher.encrypt(encrypted_stream)

    checksum = hashlib.sha256(decompressed).digest()

    def tlv(type_: int, payload: bytes) -> bytes:
        return struct.pack("<HH", type_, len(payload)) + payload

    basic_info = struct.pack("<BBII", 2, 1, FIRMWARE_OFFSET, len(encrypted_body))
    mover_info = struct.pack("<I", 1) + struct.pack("<III", FIRMWARE_OFFSET, len(decompressed), dest_offset)
    integrity_info = struct.pack("<I", 1) + checksum

    tlv_block = (
        tlv(TLV_BASIC_INFO, basic_info)
        + tlv(TLV_VERSION_INFO, version_string)
        + tlv(TLV_MOVER_INFO, mover_info)
        + tlv(TLV_INTEGRITY_VERIFY_INFO, integrity_info)
        + struct.pack("<HH", TLV_TYPE_END, 0)
    )

    header_and_tlv = b"\x00" * 32 + b"\xff" * 224 + tlv_block
    pad2_len = FIRMWARE_OFFSET - len(header_and_tlv)
    assert pad2_len >= 0, "TLV block grew past firmware_offset — shorten version_string or reduce sections"
    header_and_tlv += b"\xff" * pad2_len

    full = header_and_tlv + encrypted_body
    file_checksum = hashlib.sha256(full[32:]).digest()
    full = file_checksum + full[32:]
    return full


def roundtrip(path: Path):
    print(f"=== round-trip check: {path} ===")
    decompressed, meta = decode_bin(path)
    print(f"decoded OK: {len(decompressed)} bytes decompressed, dest_offset=0x{meta['dest_offset']:08x}")

    prompts, base_offset, version = parse_voice_image_entries(decompressed)
    print(f"parsed {len(prompts)} prompt entries, base_offset=0x{base_offset:08x}, version={version}")

    rebuilt_image = pack_voice_image(prompts, base_offset=base_offset, version=version)
    if rebuilt_image != decompressed:
        diff_at = next(i for i in range(len(decompressed)) if decompressed[i] != rebuilt_image[i])
        print(f"  FAIL: rebuilt decompressed image differs from original at byte {diff_at}")
        return False
    print("  OK: rebuilt decompressed image is byte-identical to the original")

    rebuilt_file = build_container(rebuilt_image, dest_offset=meta["dest_offset"], version_string=meta["version_string"])

    original_bytes = path.read_bytes()
    if rebuilt_file == original_bytes:
        print("  OK: rebuilt .bin is byte-IDENTICAL to the original file (best possible outcome)")
    else:
        print(f"  NOTE: rebuilt .bin ({len(rebuilt_file)}B) differs from original ({len(original_bytes)}B) "
              f"at the byte level — expected, since LZMA encoders aren't guaranteed to produce identical "
              f"compressed output. Verifying it still decompresses correctly instead:")

    reverify_decompressed, reverify_meta = decode_bin_from_bytes(rebuilt_file)
    if reverify_decompressed == decompressed:
        print("  OK: re-decoding the rebuilt file recovers byte-identical decompressed content")
        print("  OK: INTEGRITY_VERIFY_INFO checksum matches (checked inside decode_bin_from_bytes)")
        return True
    else:
        print("  FAIL: re-decoding the rebuilt file does NOT recover the original content")
        return False


def decode_bin_from_bytes(data: bytes):
    tlvs, _ = parse_tlvs(data)
    ctype, itype, fw_offset, fw_size = struct.unpack_from("<BBII", tlvs[TLV_BASIC_INFO])
    body = data[fw_offset: fw_offset + fw_size]
    cipher = AES.new(KEY, AES.MODE_CBC, IV)
    decrypted = cipher.decrypt(body)
    n_sections = struct.unpack_from("<I", tlvs[TLV_MOVER_INFO])[0]
    src_off, decompressed_size, dest_offset = struct.unpack_from("<III", tlvs[TLV_MOVER_INFO], 4)
    decompressed = lzma_decompress(decrypted, decompressed_size)
    checksum = tlvs[TLV_INTEGRITY_VERIFY_INFO][4:4 + 32]
    assert hashlib.sha256(decompressed).digest() == checksum, "checksum mismatch on rebuilt file!"
    file_checksum = data[0:32]
    assert hashlib.sha256(data[32:]).digest() == file_checksum, "file_checksum mismatch on rebuilt file!"
    return decompressed, {"dest_offset": dest_offset, "version_string": tlvs[TLV_VERSION_INFO]}


def build_from_dir(prompt_dir: Path, out_path: Path, base_dest_bin: Path | None = None):
    mp3_files = sorted(prompt_dir.glob("prompt_*.mp3"))
    if not mp3_files:
        print(f"No prompt_NN.mp3 files found in {prompt_dir}")
        sys.exit(1)
    prompts = [f.read_bytes() for f in mp3_files]
    print(f"Packing {len(prompts)} prompts ({sum(len(p) for p in prompts)} bytes total, budget is {VOICE_IMAGE_SIZE})")

    dest_offset = DEST_OFFSET
    version_string = VERSION_STRING_PAYLOAD
    image_version = 1
    if base_dest_bin is not None:
        base_decompressed, meta = decode_bin(base_dest_bin)
        dest_offset = meta["dest_offset"]
        version_string = meta["version_string"]
        _, _, image_version = parse_voice_image_entries(base_decompressed)

    image = pack_voice_image(prompts, version=image_version)
    container = build_container(image, dest_offset=dest_offset, version_string=version_string)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(container)
    print(f"Wrote {out_path} ({len(container)} bytes)")

    # self-verify before declaring success
    decoded_back, _ = decode_bin_from_bytes(container)
    assert decoded_back == image, "self-check failed: rebuilt file doesn't decode back to what we packed!"
    print("Self-check OK: the file we just wrote decodes back to exactly what we packed.")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    if sys.argv[1] == "--roundtrip":
        ok = roundtrip(Path(sys.argv[2]))
        sys.exit(0 if ok else 1)
    elif sys.argv[1] == "--build":
        prompt_dir = Path(sys.argv[2])
        out_path = Path(sys.argv[3])
        base_dest_bin = Path(sys.argv[4]) if len(sys.argv) > 4 else None
        build_from_dir(prompt_dir, out_path, base_dest_bin)
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
