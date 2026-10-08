#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["bumble==0.0.208", "tqdm", "hidapi==0.15.0", "bleak", "hexdump"]
# ///
"""
Push a repacked voice-guidance .bin to a WH-1000XM4 over BLE, replaying the
EXACT command sequence captured from a real Sony Sound Connect language
change (see docs/BLE_SNOOP_PLAN.md and docs/COMMIT_INVESTIGATION.md).

This mirrors the real app's behavior, not race-toolkit's generic `fota`
command, because the two differ in ways that matter:
  - storage_type=1 on every erase/write (race-toolkit defaults to 0)
  - one 256-byte page per RACE_STORAGE_PAGE_PROGRAM (race-toolkit batches 3)
  - `head=0x15` on EVERY command from FotaStart onward, including erase
    (race-toolkit's own ErasePartition hardcodes head=0x05, which is what
    the real app uses only *before* a FOTA session starts)
  - 4 extra Sony-specific commands (0x1c1c, 0x0433, 0x0431, 0x0430) sent
    between FotaStart and FotaStartTransaction that race-toolkit doesn't
    send at all. Meaning of 0x1c1c/0x0430 is undetermined; 0x0433/0x0431
    announce the payload size rounded up/down to the nearest 4KB block.

Requires a `race-toolkit` checkout in a sibling directory (next to this repo)
for its `librace` GATT/RACE-protocol code.
Uses librace's Bleak-based transport, which talks to the Mac's built-in
Bluetooth — no external USB dongle needed.

SAFETY: by default this only prints what it WOULD do (--dry-run, implicit).
Pass --confirm to actually connect and write to real hardware. Read
docs/BLE_SNOOP_PLAN.md and the round-trip repacker check (cli/repack.py
--roundtrip) before ever using --confirm. Keep the untouched original
voice-packs/*.bin around — if anything goes wrong, re-running this script
with an original file is the recovery path.

Usage:
    # Safe: prints the plan (addresses, page/erase counts) without touching BLE
    uv run cli/push_voice_pack.py voice-packs/VP_english_UPG_03.bin

    # Real write. Headphones must be connected via macOS Bluetooth (not just
    # paired in the Sony app) and Sound Connect should be closed so it isn't
    # fighting over the same GATT connection.
    uv run cli/push_voice_pack.py voice-packs/VP_english_UPG_03.bin --confirm
"""
import asyncio
import argparse
import logging
import struct
import sys
import time
from pathlib import Path

RACE_TOOLKIT_PATH = Path(__file__).resolve().parent.parent.parent / "race-toolkit"
if not RACE_TOOLKIT_PATH.exists():
    print(f"race-toolkit not found at {RACE_TOOLKIT_PATH} — clone it there first:")
    print(f"  git clone https://github.com/auracast-research/race-toolkit.git {RACE_TOOLKIT_PATH}")
    sys.exit(1)
sys.path.insert(0, str(RACE_TOOLKIT_PATH))

from librace.constants import RaceId, RaceType  # noqa: E402
from librace.packets import (  # noqa: E402
    RaceHeader,
    RacePacket,
    ReturnCodeResponse,
    FotaPartitionInfoQuery,
    FotaPartitionInfoQueryResponse,
    FotaStart,
    FotaStartTransaction,
    FotaWriteState,
    ErasePartition,
    WriteFlashPage,
    WriteFlashPageResponse,
)
from librace.race import RACE  # noqa: E402
from librace.transport import GATTBleakTransport  # noqa: E402
from librace.util import setup_logging  # noqa: E402

STORAGE_TYPE = 1  # confirmed from live capture; race-toolkit defaults to 0
ERASE_BLOCK = 0x1000  # 4KB
PAGE_SIZE = 0x100  # 256B
SESSION_HEAD = 0x15  # every command from FotaStart onward uses this in the real app
FRAGMENT_PACE = 0.01  # seconds between write-without-response chunks; bleak/CoreBluetooth


class FragmentingBleakTransport(GATTBleakTransport):
    """GATTBleakTransport.send() does one write_gatt_char() call per RACE packet,
    which uses CoreBluetooth's automatic long-write (queued Prepare Write) fallback
    for anything over the negotiated MTU. That fallback reliably fails against this
    device with a 'Prepare Queue Full' GATT error, immediately and on every retry
    (confirmed live: page-0 WriteFlashPage, 269 bytes, MTU 242 -> 239-byte usable
    write size, fails 100% of the time even with backoff).

    The real Sony app never uses a GATT write-with-response/long-write for bulk RACE
    traffic at all -- traced from the capture (captures/Untitled 2 - (null).pklg):
    it's 100% plain ATT Write Command (write-without-response, opcode 0x52), chunked
    at exactly max_write_without_response_size bytes, concatenated across RACE packet
    boundaries with no per-fragment framing. librace's own RACE._recv() already
    expects exactly this on the receive side (accumulates raw continuation bytes by
    the header's length field, no header on continuation fragments) -- the send side
    just never had the matching fragmentation implemented. This mirrors that, while
    leaving small (already-working) writes on the original single-shot path.

    CoreBluetooth's write-without-response has no ack/flow-control of its own and
    bleak's macOS backend doesn't wait for send-readiness (write_characteristic()
    fires and returns immediately for the without-response case), so chunks are
    paced with a small delay to avoid silently overrunning the local send queue.
    """

    log_fh = None  # set from do_push; None = no raw logging

    async def send(self, data: bytes):
        if not self.client:
            logging.error("No bleak client.")
            return
        if self.log_fh:
            log_raw(self.log_fh, "TX", data)
        max_chunk = self.client.services.get_characteristic(self.tx_char).max_write_without_response_size
        if len(data) <= max_chunk:
            # Without an explicit response=, bleak/CoreBluetooth silently chose Write
            # Request (ATT 0x12) here -- confirmed via a PacketLogger capture of our own
            # session (captures/trial1.pklg). The real app uses Write Command (ATT 0x52,
            # write-without-response) for every single RACE packet, always, with zero
            # exceptions across the entire original capture. Matching that exactly.
            await self.client.write_gatt_char(self.tx_char, data, response=False)
            return
        for i in range(0, len(data), max_chunk):
            chunk = data[i:i + max_chunk]
            await self.client.write_gatt_char(self.tx_char, chunk, response=False)
            await asyncio.sleep(FRAGMENT_PACE)


def log_raw(fh, direction, data):
    """Log one complete RACE packet (TX: what we constructed, RX: whatever
    RACE._recv() reassembled, including packets our own code never inspects,
    like the async post-response INDICATION we saw follow IntegrityCheck in
    the real capture -- our current code only reads the blocking RESPONSE."""
    ts = time.time()
    line = f"{ts:.3f} {direction} {data.hex()}"
    if len(data) >= RaceHeader.SIZE:
        h = RaceHeader.unpack(data[:RaceHeader.SIZE])
        line += f"  [head=0x{h.head:02x} type=0x{h.type:02x} length={h.length} id=0x{h.id:04x}]"
    fh.write(line + "\n")
    fh.flush()

# Extra Sony-specific IDs seen in the live capture, not in race-toolkit's RaceId enum.
ID_1C1C = 0x1C1C
ID_0433 = 0x0433  # announce size, rounded UP to nearest 4KB
ID_0431 = 0x0431  # announce size, rounded DOWN to nearest 4KB
ID_0430 = 0x0430


def round_up(n, block):
    return ((n + block - 1) // block) * block


def round_down(n, block):
    return (n // block) * block


def raw_packet(head, type_, id_, payload):
    header = RaceHeader(head=head, type_=type_, id_=id_)
    return RacePacket(header, payload)


def force_session_head(packet):
    packet.header.head = SESSION_HEAD
    return packet


def plan_for(file_path: Path):
    data = file_path.read_bytes()
    size = len(data)
    n_pages = -(-size // PAGE_SIZE)  # ceil
    padded_size = n_pages * PAGE_SIZE
    return {
        "data": data,
        "size": size,
        "padded_size": padded_size,
        "n_pages": n_pages,
        "size_rounded_up": round_up(size, ERASE_BLOCK),
        "size_rounded_down": round_down(size, ERASE_BLOCK),
    }


def print_plan(plan, start_addr, partition_length):
    n_erase_blocks = plan["size_rounded_up"] // ERASE_BLOCK
    print(f"  file size:           {plan['size']} bytes")
    print(f"  target start addr:   0x{start_addr:08x}")
    print(f"  partition length:    0x{partition_length:08x} ({partition_length/1024/1024:.2f} MB)")
    print(f"  fits in partition:   {plan['size'] <= partition_length}")
    print(f"  erase blocks (4KB):  {n_erase_blocks}")
    print(f"  page writes (256B):  {plan['n_pages']}")
    print(f"  size rounded up:     0x{plan['size_rounded_up']:08x}")
    print(f"  size rounded down:   0x{plan['size_rounded_down']:08x}")


async def do_push(file_path: Path, device_names, controller_dongle=None):
    plan = plan_for(file_path)

    transport = FragmentingBleakTransport(addr=None, devices=device_names)
    r = RACE(transport, send_delay=0)

    log_path = Path("output/raw_packet_log.txt")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(log_path, "a")
    log_fh.write(f"\n=== run start {time.time():.3f} ===\n")
    transport.log_fh = log_fh

    def recv_logger(data):
        log_raw(log_fh, "RX", data)

    await r.setup(recv_cb=recv_logger)

    # Write-without-response needs the connection fully "settled" to be reliably
    # delivered -- confirmed live: switching small commands to match the real app's
    # write-without-response usage caused the very first post-connect command to hang
    # forever (no ATT-level ack to signal a drop, so send_sync()'s unbounded wait never
    # returns). A brief settle before the first send avoids sending into that window.
    await asyncio.sleep(0.5)

    _orig_send_sync = r.send_sync

    async def send_sync_with_timeout(packet, timeout=15):
        try:
            return await asyncio.wait_for(_orig_send_sync(packet), timeout=timeout)
        except asyncio.TimeoutError:
            raise RuntimeError(f"No response within {timeout}s (id=0x{packet.header.id:04x}) -- likely a silently dropped write-without-response send")

    r.send_sync = send_sync_with_timeout

    async def expect_ok(packet, label):
        resp = await r.send_sync(packet)
        rc_resp = ReturnCodeResponse.unpack(resp)
        ok = rc_resp.return_code == 0
        print(f"  {'OK  ' if ok else 'FAIL'} {label} -> return_code={rc_resp.return_code}")
        if not ok:
            raise RuntimeError(f"{label} failed with return_code={rc_resp.return_code}")
        return rc_resp

    try:
        print("\n[1/10] Querying FOTA partition info...")
        q = FotaPartitionInfoQuery()
        resp = await r.send_sync(q)
        info = FotaPartitionInfoQueryResponse.unpack(resp)
        print(f"  start_addr=0x{info.start_addr:08x} length=0x{info.length:08x}")
        if plan["size"] > info.length:
            raise RuntimeError("file is larger than the reported partition length — refusing to continue")

        start_addr = info.start_addr

        print("\n[2/10] FotaStart...")
        await expect_ok(FotaStart(), "FotaStart")

        print("\n[3/10] Sony-specific pre-transaction commands...")
        await expect_ok(raw_packet(SESSION_HEAD, RaceType.CMD_EXPECTS_RESPONSE, ID_1C1C, b"\x01\x00"), "0x1c1c")

        announce_up = struct.pack("<BBII", 1, 0, start_addr, plan["size_rounded_up"])
        await expect_ok(raw_packet(SESSION_HEAD, RaceType.CMD_EXPECTS_RESPONSE, ID_0433, announce_up), "0x0433 (size rounded up)")

        announce_down = struct.pack("<BBII", 1, 0, start_addr, plan["size_rounded_down"])
        await expect_ok(raw_packet(SESSION_HEAD, RaceType.CMD_EXPECTS_RESPONSE, ID_0431, announce_down), "0x0431 (size rounded down)")

        await expect_ok(raw_packet(SESSION_HEAD, RaceType.CMD_EXPECTS_RESPONSE, ID_0430, b"\x01\x00\x00"), "0x0430")

        print("\n[4/10] FotaStartTransaction...")
        await expect_ok(force_session_head(FotaStartTransaction()), "FotaStartTransaction")

        print("\n[5/10] FotaWriteState...")
        await expect_ok(force_session_head(FotaWriteState(b"\x00\x02")), "FotaWriteState")

        print(f"\n[6/10] Erasing {plan['size_rounded_up'] // ERASE_BLOCK} blocks of 4KB...")
        addr = start_addr
        end = start_addr + plan["size_rounded_up"]
        block_num = 0
        while addr < end:
            pkt = force_session_head(ErasePartition(address=addr, length=ERASE_BLOCK, storage_type=STORAGE_TYPE))
            resp = await r.send_sync(pkt)
            rc = ReturnCodeResponse.unpack(resp).return_code
            block_num += 1
            if rc != 0:
                raise RuntimeError(f"erase failed at 0x{addr:08x} (block {block_num}), return_code={rc}")
            if block_num % 50 == 0 or addr + ERASE_BLOCK >= end:
                print(f"  erased {block_num} blocks (0x{addr:08x})")
            addr += ERASE_BLOCK

        print(f"\n[7/10] Writing {plan['n_pages']} pages of 256B...")
        padded_data = plan["data"] + b"\xff" * (plan["padded_size"] - plan["size"])
        for i in range(plan["n_pages"]):
            page_addr = start_addr + i * PAGE_SIZE
            page = padded_data[i * PAGE_SIZE:(i + 1) * PAGE_SIZE]
            pkt = force_session_head(WriteFlashPage(start_address=page_addr, data=page, storage_type=STORAGE_TYPE))

            retries = 0
            while True:
                try:
                    resp = await r.send_sync(pkt)
                except Exception as e:
                    retries += 1
                    if retries > 5:
                        raise RuntimeError(f"page write failed at 0x{page_addr:08x} (page {i}) after 5 retries, transport error: {e}")
                    print(f"  transport error at 0x{page_addr:08x} (page {i}, retry {retries}): {e}")
                    await asyncio.sleep(0.5 * retries)
                    continue
                wr = WriteFlashPageResponse.unpack(resp)
                if wr.return_code == 0:
                    break
                retries += 1
                if retries > 5:
                    raise RuntimeError(f"page write failed at 0x{page_addr:08x} (page {i}) after 5 retries, return_code={wr.return_code}")
                print(f"  retry {retries} at 0x{page_addr:08x} (return_code={wr.return_code})")
                await asyncio.sleep(0.2 * retries)

            await asyncio.sleep(0.015)

            if (i + 1) % 50 == 0 or i == plan["n_pages"] - 1:
                print(f"  wrote {i + 1}/{plan['n_pages']} pages (0x{page_addr:08x})")

        print("\n[8/10] FotaIntegrityCheck...")
        try:
            # librace's FotaIntegrityCheck class hardcodes payload b"\x01\x00\x00", but the
            # real capture's IntegrityCheck command (frame 20012) sent b"\x01\x00\x01" --
            # confirmed by re-decoding the raw capture bytes directly, byte-for-byte, not
            # the class. Using raw_packet() to send what the real app actually sent.
            integrity_check = raw_packet(SESSION_HEAD, RaceType.CMD_EXPECTS_RESPONSE, RaceId.RACE_FOTA_INTEGRITY_CHECK, b"\x01\x00\x01")
            await expect_ok(integrity_check, "FotaIntegrityCheck")
        except RuntimeError:
            print("\n  INTEGRITY CHECK FAILED. Not sending FotaCommit.")
            print("  The partition may now be in an inconsistent state. Recovery: re-run this")
            print("  script with a known-good original .bin to restore working prompts.")
            raise

        print("\n[9/10] FotaWriteState (post-integrity)...")
        await expect_ok(force_session_head(FotaWriteState(b"\x11\x02")), "FotaWriteState (post-integrity)")

        print("\n[10/10] FotaCommit...")
        # librace's FotaCommit class sends a 1-byte b"\x00" payload with type
        # CMD_EXPECTS_RESPONSE, but the real capture's Commit command (frame 20032) has
        # an EMPTY payload and type CMD_EXPECTS_NO_RESPONSE (0x5c) -- confirmed from raw
        # bytes "15 5c 02 00 02 1c" (length=2 means id-only, zero payload bytes). The
        # device still replies with a normal 0x5b RESPONSE either way (frame 20033), so
        # send_sync() unblocks correctly regardless of our outgoing type.
        # Commit doesn't touch flash pages (it only finalizes already-verified data), so
        # retrying it a few times immediately is cheap -- testing whether the device needs
        # a repeated commit call to actually latch, since return_code=1 on the first try
        # isn't a documented/explained code from the capture.
        commit_retries = 0
        while True:
            commit = raw_packet(SESSION_HEAD, RaceType.CMD_EXPECTS_NO_RESPONSE, RaceId.RACE_FOTA_COMMIT, b"")
            resp = await r.send_sync(commit)
            rc = ReturnCodeResponse.unpack(resp).return_code
            print(f"  {'OK  ' if rc == 0 else 'FAIL'} FotaCommit -> return_code={rc}")
            if rc == 0:
                break
            commit_retries += 1
            if commit_retries > 4:
                raise RuntimeError(f"FotaCommit failed with return_code={rc} after {commit_retries} tries")
            await asyncio.sleep(0.5 * commit_retries)

        print("\nDone. The device should reboot / reload voice guidance shortly.")

    finally:
        await r.close()
        log_fh.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file", type=Path, help="Path to the .bin to push")
    parser.add_argument("--device-name", default="WH-1000XM4", help="BLE device name filter (default: WH-1000XM4)")
    parser.add_argument("--confirm", action="store_true", help="Actually connect and write to real hardware. Without this, only prints the plan.")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    if not args.file.exists():
        print(f"File not found: {args.file}")
        sys.exit(1)

    plan = plan_for(args.file)
    print(f"=== Plan for {args.file} ===")
    print_plan(plan, start_addr=0x00510000, partition_length=0x005FF000)
    print("(start_addr/partition_length above are from the last live capture — the real")
    print(" FotaPartitionInfoQuery response at run time is authoritative, not this preview.)")

    if not args.confirm:
        print("\nDry run only (default). Re-run with --confirm to actually connect and write.")
        print("Before doing that: headphones connected via macOS Bluetooth, Sony Sound Connect")
        print("app closed, and you've already validated this exact file with:")
        print(f"  uv run cli/repack.py --roundtrip {args.file}")
        return

    setup_logging(args.debug)
    print("\n*** THIS WILL WRITE TO REAL HEADPHONE FLASH. ***")
    answer = input("Type 'yes' to proceed: ")
    if answer.strip().lower() != "yes":
        print("Aborted.")
        return

    asyncio.run(do_push(args.file, device_names=[args.device_name]))


if __name__ == "__main__":
    main()
