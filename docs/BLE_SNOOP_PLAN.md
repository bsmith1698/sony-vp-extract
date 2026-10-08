# Capturing the Voice-Pack FOTA Trigger via BLE HCI Snoop

## Goal

`race-toolkit`'s `fota` command drives a generic firmware-write flow (partition
address handed back by the device via `FotaPartitionInfoQuery`, `0x1C00), confirmed
only on WH-CH720N / WH-1000XM6 for full firmware updates. Neither that tool nor
`sony-vp-extract` shows what the real Sony Sound Connect app sends over BLE when
it pushes a **voice-guidance language pack** specifically, as opposed to a full
CM4 firmware update. That's the missing piece before any write should be
attempted against real headphones.

This capture is purely passive — it logs traffic your own phone already sends to
your own paired headphones. Nothing is written to the headphones as part of this
step.

## Prerequisites

- The WH-1000XM4, paired to a phone with the **Sony Sound Connect** (formerly
  Headphones Connect) app installed.
- Either:
  - **Android** (no root needed) — has a built-in Bluetooth HCI snoop logger.
  - **iOS** — needs a Mac with Xcode's Additional Tools (PacketLogger) and an
    Apple-provided Bluetooth debug profile installed on the iPhone. More
    friction than Android; use Android if there's a choice.
- [Wireshark](https://www.wireshark.org/) on the machine you'll analyze the
  capture on — it parses `btsnoop` logs natively.

## Steps (Android)

1. **Enable capture.** Settings → About phone → tap Build number 7× to unlock
   Developer options. Then Settings → Developer options → enable
   **"Enable Bluetooth HCI snoop log"**. Some OEMs (Samsung, etc.) put this
   under Developer options → Networking, or require Bluetooth to be
   toggled off/on after enabling it to take effect.

2. **Clear old log state.** In Developer options, Bluetooth off then on again
   ensures a fresh session boundary — makes it easier to find your capture
   later rather than digging through unrelated background BLE noise.

3. **Capture a baseline.** Open Sony Sound Connect, connect to the
   headphones, but don't touch the voice-guidance language setting yet. Let
   it sit connected for ~10 seconds. This baseline helps you filter out
   connection/handshake noise that isn't what we're after.

4. **Trigger the actual event.** In the app, navigate to the voice-guidance /
   spoken-language setting and change it to a different language (or
   reinstall the current one, if the app allows re-downloading it). Wait
   for the app to report the update finished.

5. **Pull the log.** The snoop file lives at
   `/sdcard/btsnoop_hci.log` on most devices (some newer Android versions
   write to `/data/misc/bluetooth/logs/btsnoop_hci.log`, which needs `adb
   bugreport` or root to reach). Easiest path:
   ```bash
   adb pull /sdcard/btsnoop_hci.log ./voice_pack_change.log
   ```
   If it's not at that path, `adb shell find /sdcard /data/misc/bluetooth -iname 'btsnoop*' 2>/dev/null` will locate it (the `/data` search needs root).

## Steps (iOS, if Android isn't available)

1. On the iPhone: Settings → Bluetooth, toggle off, back on (fresh session).
2. Install Apple's **Bluetooth diagnostics profile** for the iOS version in
   use (from Apple's developer downloads), which enables deeper BLE logging.
3. On a Mac with Xcode installed, open **PacketLogger** (Xcode → Open
   Developer Tool → More Developer Tools → Additional Tools for Xcode →
   Hardware IO Tools → PacketLogger), connect the iPhone, start capture.
4. Perform the same connect → baseline → change-language steps as above.
5. Export the capture from PacketLogger as a `.pklg` or `.btsnoop` file.

## Analysis

1. Open the capture in Wireshark. Filter to the RACE GATT service traffic —
   the relevant characteristics are already known from `sony-vp-extract`'s
   writeup:
   ```
   btatt.uuid128 == dc405470-a351-4a59-97d8-2e2e3b207fbb   # RACE service
   ```
   or more directly, filter on ATT `Write Request`/`Handle Value
   Notification` opcodes and eyeball the handles once you've identified
   which ones correspond to the TX (`bfd869fa-...`) and RX (`2a6b6575-...`)
   characteristics from the service's discovery response earlier in the log.

2. Every RACE payload starts with the same 6-byte header `race-toolkit`
   already parses (`librace/packets.py`, `RaceHeader`, format `<BBHH`:
   head, type, length, then a 2-byte command ID). Pull the 2-byte ID out of
   each write/notification and cross-reference it against
   `librace/constants.py`'s `RaceId` enum — that's the fastest way to turn
   raw hex into command names without reimplementing the parser.

   Rather than doing this by hand in Wireshark, it's faster to export the
   capture's raw ATT payloads (Wireshark: File → Export Packet Dissections,
   or a `pyshark`/`scapy` pass over the `btsnoop` file) and feed each
   payload through `RaceHeader.unpack()` from the already-cloned
   `race-toolkit` repo — reusing its packet classes instead of writing a
   parser from scratch.

3. What to look for specifically:
   - Does the sequence contain `RACE_FOTA_PARTITION_INFO_QUERY` (`0x1C00`)
     at all, or a different command entirely for voice-pack pushes?
   - If it does appear, what does the response's `start_addr` resolve to?
     Compare it against the partition table from the original writeup —
     partition 6 (`0x0C510000`, external flash, voice guidance) vs.
     partition 3 (`0x081B9000`, FOTA/main firmware).
   - Is there a command sent *before* the partition query that doesn't
     appear in `race-toolkit`'s generic firmware-update flow? That's likely
     the "select voice-guidance content type" step this research is missing.
   - Does the pushed image (visible in the notification/write payload
     bodies after the header) match the same TLV/AES/LZMA structure
     documented in `sony-vp-extract`'s writeup, or does the app send
     something structurally different for this path?

## After the capture

Bring the annotated command sequence back for comparison against
`race-toolkit`'s `librace/fota.py` flow. If it turns out voice-pack pushes go
through the exact same `FotaPartitionInfoQuery` → `FotaStart` →
`FotaStartTransaction` → erase → `WriteFlashPage` × N → `FotaIntegrityCheck` →
`FotaCommit` sequence and just naturally resolve to partition 6 (because
that's whatever the device considers "next" based on some other state), that's
the green light to consider a real write attempt. If it diverges, the
divergent command(s) need to be reverse-engineered before anything touches
real hardware.
