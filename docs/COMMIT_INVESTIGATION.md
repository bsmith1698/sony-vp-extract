# Voice-Pack Push: FotaCommit Investigation

## Status: RESOLVED (2026-09-02)

Root cause: **the Sony CDN-hosted voice-pack files bundled with upstream
`sony-vp-extract` (`voice-packs/*.bin`) are stale.** They round-trip
perfectly and pass every self-consistency/file-level integrity check, but
they are not byte-identical to what the current Sony Sound Connect app
actually fetches and pushes today. The device's deeper, previously-unexplained
async verification (the `0x5d` INDICATION following `FotaIntegrityCheck`,
payload byte `0x0d` vs the real app's `0x00`) was correctly detecting this
staleness and causing `FotaCommit` to refuse to finalize — this was never a
bug in the reverse-engineered protocol sequence itself, which was already
fully correct by the time this was found.

**Confirmed by direct test:** captured a real, complete transfer from the
actual Sony app pushing English today (`captures/trial2.pklg`), reconstructed
the exact file it transferred from the raw page-write traffic
(`output/reconstructed_from_trial2.bin`), and pushed that file through our
own script. Result: `FotaCommit` → `return_code=0`, and the async indication
byte read `0x00` — both matching the real successful capture exactly, for
the first time. See "Reconstructing a pushed file from a capture" below for
the method, which is reusable for any future language/content.

**Practical implication:** before pushing custom audio, don't trust the
`voice-packs/*.bin` files as-is — they may be stale for some or all
languages. Capture and reconstruct a fresh real transfer (or otherwise
verify freshness) first, and build custom content from that base rather
than the bundled CDN download.

**Device state as of resolution:** successfully committed the (freshly
reconstructed, unmodified) English pack. Core headphone functions unaffected
throughout the whole investigation — this only ever touched the
voice-guidance content partition.

## What works (verified, not assumed)

Full sequence, run repeatedly and cleanly:

`FotaPartitionInfoQuery` → `FotaStart` → 4 Sony-specific setup commands
(`0x1c1c`, `0x0433`, `0x0431`, `0x0430`) → `FotaStartTransaction` →
`FotaWriteState` → erase N×4KB blocks → write N×256B pages → `FotaIntegrityCheck`
→ `FotaWriteState` (again, `0x11 0x02`) → `FotaCommit`. **Full sequence
confirmed working end-to-end** with a freshly-sourced file (see Status above).

- Every page write succeeds (device-side per-page checksum accepted every time).
- `FotaIntegrityCheck` returns `return_code=0` — the device's own recomputed
  checksum over the full flash contents matches. This is a device-side
  confirmation, not just our own transfer bookkeeping.
- Tested with both English and French `.bin` packs (both byte-identical
  round-trips of Sony's original CDN files, verified via
  `cli/repack.py --roundtrip`) — same result either way.

## Bugs found and fixed during this session

All confirmed by decoding raw capture bytes directly, not guessed:

1. **Transport: oversized single GATT write.** `librace`'s `GATTBleakTransport.send()`
   does one `write_gatt_char()` call per RACE packet. Anything over the
   negotiated MTU (242, confirmed) triggers CoreBluetooth's automatic
   long-write (queued Prepare Write) fallback, which fails immediately and
   consistently against this device (`Prepare Queue Full`, GATT error 0x09).
   The real app never relies on this at all — it's 100% plain ATT Write
   Command, chunked at exactly `max_write_without_response_size` (239 bytes),
   concatenated across RACE packet boundaries with no per-fragment framing.
   `librace`'s own `RACE._recv()` already expects exactly this reassembly
   scheme on the receive side; the send side never had it implemented. Fixed
   via `FragmentingBleakTransport` in `cli/push_voice_pack.py`, which
   subclasses `GATTBleakTransport` rather than editing the upstream
   `race-toolkit` clone.

2. **`FotaIntegrityCheck` payload.** `librace`'s class hardcodes payload
   `b"\x01\x00\x00"`. The real capture's `IntegrityCheck` command (frame
   20012) is `b"\x01\x00\x01"` — confirmed twice, independently, by re-decoding
   raw capture bytes. This was the direct cause of the earlier `return_code=10`
   failure. Fixed by bypassing the class and sending the raw bytes via
   `raw_packet()`.

3. **`FotaCommit` payload/type.** `librace`'s class sends 1-byte `b"\x00"`
   payload with type `CMD_EXPECTS_RESPONSE`. The real capture's `Commit`
   (frame 20032) has an **empty** payload and type `CMD_EXPECTS_NO_RESPONSE`
   (`0x5c`) — `15 5c 02 00 02 1c`, confirmed byte-for-byte, twice. The device
   still replies with a normal `0x5b` RESPONSE either way, so `send_sync()`
   unblocks correctly. This fix did not resolve the `return_code=1` rejection,
   but the byte-level match against a proven-successful transfer is solid.

4. **Missing second `FotaWriteState` call.** The real capture sends
   `FotaWriteState(b"\x11\x02")` again, *after* a successful `IntegrityCheck`
   and *before* `Commit` — independently confirmed both from the capture
   (frame 20030) and from `race-toolkit`'s own `librace/fota.py` reference
   implementation (`FOTAUpdater.update()`, line 214), which our script hadn't
   been doing at all. Added as step 9/10.

## Hypotheses tested and ruled out for the `Commit` rejection

Each of these was an actual live test or a direct check against capture data,
not speculation:

| Hypothesis | Test | Result |
|---|---|---|
| Commit needs to be called twice to latch | 5 immediate retries | Identical `return_code=1`, all 5 |
| Stale device session state from earlier failed attempts | Power-cycled headphones, retried | Identical failure |
| Content language mismatch (pushing English while French is active) | Pushed the actual French pack instead | Identical failure |
| A separate "select language" step exists outside the RACE/FOTA flow | Surveyed *every* ATT handle used anywhere in the full 23,303-frame original capture | Only 3 handles exist total (TX/RX/CCCD) — nothing else to find |
| A required RFCOMM-channel step happens before Commit | Checked all 814 RFCOMM data frames in the original capture | They start at frame 21296, *after* Commit's response (frame 20033) — this is post-reboot reconnection housekeeping, not a prerequisite |
| Connection isn't encrypted/authenticated the way the real app's was | Checked HCI-level encryption events in both the original capture and a fresh capture of our own session (`captures/trial1.pklg`) | Neither shows an encryption-change event during the FOTA window itself — appears equivalent (likely pre-existing encryption from prior OS-level pairing in both cases) |
| Small commands transmitted as ATT Write Request instead of Write Command | Found via `captures/trial1.pklg` that all 237 small commands were going out as opcode `0x12` (Write Request) instead of `0x52` (Write Command) like the real app uses for literally everything. Fixed to force `response=False` everywhere. | Fixed the discrepancy, but `Commit` still fails identically |

## The data point that cracked it: the async post-IntegrityCheck indication

After `FotaIntegrityCheck`'s normal `0x5b` RESPONSE (`return_code=0`), the
device sends a second, asynchronous `0x5d` INDICATION with more detail that
`librace`'s default receive path never surfaces to calling code (has to be
read via a `recv_cb`). This turned out to be the key diagnostic signal:

- **Real successful captures** (both the original, and `trial2` from
  2026-09-02): payload `00 01 00 01`
- **Every one of our attempts pushing the stale CDN file**, regardless of
  content or transport fix: payload `0d 01 00 01`
- **Pushing the freshly-reconstructed file** (from `trial2`): payload
  `00 01 00 01` — matches the real capture, `Commit` succeeds.

So `0x0d` (13) does mean something like "content failed a deeper freshness
check" — the device was catching the stale-file issue at this exact point,
consistently, across every attempt, well before we understood what it meant.
Meaning of the value itself is still not documented anywhere public, but its
correlation with success/failure is now proven, not just observed.

## Reconstructing a pushed file from a capture

Reusable method for getting a guaranteed-fresh `.bin` for any language,
independent of whether the bundled `voice-packs/*.bin` is current:

1. Capture a real transfer of the target language from the actual Sony app
   (PacketLogger on the iPhone via Xcode's device capture, same as
   `BLE_SNOOP_PLAN.md` — needs the phone plugged in and its screen awake for
   the full multi-minute transfer so the capture link doesn't drop early).
2. Extract the raw TX fragment stream for the bulk-transfer frame range
   (`btatt.opcode==0x52 && btatt.handle==0x0053`, the frame range where
   large ~239/478-byte fragments appear) via `tshark -T fields -e btatt.value`.
3. Concatenate all fragment hex into one continuous byte stream, then
   reassemble RACE packets from it by hand (mirrors `RACE._recv()`: read the
   6-byte header once enough bytes are buffered, then consume
   `length + 4` total bytes per packet — no per-fragment header exists).
4. For every packet with `id == 0x0402` (`RACE_STORAGE_PAGE_PROGRAM`), parse
   `<BB>` (storage_type, num_pages) then `num_pages` × `<BI + 256 bytes>`
   (checksum, address, page data) and place each page's data at
   `address - 0x00510000` in a reconstruction buffer sized off the highest
   address seen.
5. The container's own `BASIC_INFO` TLV (first TLV, right at byte 256) has a
   `fw_size` field (`<BBII>`: ctype, itype, fw_offset, fw_size) that gives
   the file's true total length (`fw_offset + fw_size`) — trim the buffer to
   that, since the reconstruction buffer can otherwise include trailing
   bytes past the real file's end.
6. Some early pages may never be individually written at all (observed: a
   14-page gap right after page 1, addresses `0x510200`–`0x510f00`) — these
   appear to be legitimately blank/all-`0xFF` pages that the real app's
   sender skips writing since post-erase flash already reads as `0xFF`.
   Leaving them as `0xFF` in the reconstruction is correct, not a bug.

This won't necessarily produce a file that passes `repack.py`'s own strict
self-consistency decode (a SHA-256 mismatch was still present on the
reconstructed file used here, likely from a remaining small gap/edge case in
step 6's handling) — but it doesn't need to. The device only cares about
what's actually pushed to it; a file good enough to push and successfully
`Commit` is good enough to use as a base for substituting custom audio.

## Where things are in the code

- `cli/push_voice_pack.py` has all fixes above applied and is otherwise
  unchanged from the documented design (dry-run by default, `--confirm` +
  typed `yes` for a real write).
- Raw packet logging was added for this investigation: every complete RACE
  packet sent and received (not just the ones the high-level code reads) is
  hex-dumped with a decoded header to `output/raw_packet_log.txt`, appended
  across runs. This is what surfaced the `0x0d` indication — it's not
  visible in the script's normal terminal output at all.
- `output/null_test_english.bin` / `output/null_test_french.bin` — verified
  byte-identical round-trips of Sony's original CDN files, used for all live
  testing so no new/custom audio content is ever in play while debugging the
  write mechanism itself.
- `captures/trial1.pklg` — a PacketLogger capture of our own live session
  (Mac's own Bluetooth stack, not the iPhone). PacketLogger needed a
  Bluetooth diagnostic profile installed to work on this macOS version
  (26.5) — see the Apple Developer Forums thread "PacketLogger not logging
  packets" for the general issue; installing the profile from
  `developer.apple.com/bug-reporting/profiles-and-logs/?name=bluetooth`
  resolved it here.

## Where things stand now — resuming toward custom audio

The write mechanism is fully proven end-to-end. Next steps for the original
goal (custom ElevenLabs voice prompts):

1. Don't build custom prompt packs from `voice-packs/*.bin` directly without
   checking freshness first — at minimum, English was confirmed stale as of
   2026-09-02. Either reconstruct a fresh base per language (method above)
   or otherwise verify a given language file still round-trips to a
   successful `Commit` before substituting audio into it.
2. `output/reconstructed_from_trial2.bin` is a confirmed-working, real,
   unmodified English base — safe to use as the substitution target for the
   first custom-audio prompt slot.
3. From here, the original plan resumes: generate ElevenLabs audio for one
   low-stakes prompt slot, substitute into the reconstructed base via
   `repack.py --build`, push, and verify it plays correctly before doing
   more slots.
