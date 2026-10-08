# Sony WH-1000XM4 Voice Pack Extractor

![Sony WH-1000XM4](https://img.shields.io/badge/Sony%20WH--1000XM4-000000?style=flat&logo=sony&logoColor=white)
![Voice Pack Extractor](https://img.shields.io/badge/Voice%20Pack%20Extractor-1a1a2e?style=flat&logo=headphones&logoColor=e8c547)
![RACE Protocol](https://img.shields.io/badge/Airoha%20RACE%20Protocol-0082FC?style=flat&logo=bluetooth&logoColor=white)
[![Amp](https://img.shields.io/badge/Amp%20Code-191C19.svg?logo=data:image/svg%2bxml;base64,PHN2ZyB3aWR0aD0iMjEiIGhlaWdodD0iMjEiIHZpZXdCb3g9IjAgMCAyMSAyMSIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHBhdGggZD0iTTMuNzY4NzkgMTguMzAxNUw4LjQ5ODM5IDEzLjUwNUwxMC4yMTk2IDIwLjAzOTlMMTIuNzIgMTkuMzU2MUwxMC4yMjg4IDkuODY3NDlMMC44OTA4NzYgNy4zMzg0NEwwLjIyNTk0IDkuODkzMzFMNi42NTEzNCAxMS42Mzg4TDEuOTQxMzggMTYuNDI4MkwzLjc2ODc5IDE4LjMwMTVaIiBmaWxsPSIjRjM0RTNGIi8+CjxwYXRoIGQ9Ik0xNy40MDc0IDEyLjc0MTRMMTkuOTA3OCAxMi4wNTc1TDE3LjQxNjcgMi41Njg5N0w4LjA3ODczIDAuMDM5OTI0Nkw3LjQxMzggMi41OTQ4TDE1LjI5OTIgNC43MzY4NUwxNy40MDc0IDEyLjc0MTRaIiBmaWxsPSIjRjM0RTNGIi8+CjxwYXRoIGQ9Ik0xMy44MTg0IDE2LjM4ODNMMTYuMzE4OCAxNS43MDQ0TDEzLjgyNzYgNi4yMTU4OEw0LjQ4OTcxIDMuNjg2ODNMMy44MjQ3NyA2LjI0MTcxTDExLjcxMDEgOC4zODM3NkwxMy44MTg0IDE2LjM4ODNaIiBmaWxsPSIjRjM0RTNGIi8+Cjwvc3ZnPg==&style=flat)](https://ampcode.com/@helgesverre)
![License: MIT](https://img.shields.io/badge/License-MIT-007ACC.svg?style=flat)

Decrypt and extract the voice guidance MP3 prompts from Sony WH-1000XM4 encrypted firmware files.

The AES-128-CBC key was extracted by dumping the headphones' Airoha MT2811 firmware over Bluetooth Low Energy using the [RACE protocol](https://airoha.com), then disassembling the ARM Cortex-M4 FOTA decryption routine. Full technical writeup: **[docs/WRITEUP.md](docs/WRITEUP.md)**

> [!NOTE]
> **This fork adds the write path.** Upstream decrypts and extracts the voice prompts. This fork also rebuilds voice packs and **pushes them back to the headphones over Bluetooth**, reverse-engineered from a capture of the official Sony Sound Connect app. See [Writing voice packs back](#writing-voice-packs-back-fork-addition) below.

## Quick Start

```bash
# Install Bun (if not already installed)
curl -fsSL https://bun.sh/install | bash

git clone https://github.com/HelgeSverre/sony-vp-extract.git
cd sony-vp-extract
bun install

# Downloads from Sony CDN automatically, then extracts
bun run cli/extract.ts --all
```

## Usage

```bash
# Extract all — auto-downloads from Sony CDN if voice-packs/ is empty
bun run cli/extract.ts --all [input-dir] [output-dir]

# Extract a single voice pack
bun run cli/extract.ts voice-packs/VP_english_UPG_03.bin extracted/

# Show voice pack info
bun run cli/extract.ts --info voice-packs/VP_english_UPG_03.bin

# Download only (no extraction)
bun run cli/extract.ts --download [output-dir]
```

### Python alternative

```bash
uv run extract_all.py
```

Downloads from Sony CDN + decrypts + extracts — all in one command. Dependencies are declared inline ([PEP 723](https://peps.python.org/pep-0723/)) — `uv run` handles everything automatically.

## Available Languages

| Language      | File                       | Prompts | CDN                                                                                               |
| ------------- | -------------------------- | ------- | ------------------------------------------------------------------------------------------------- |
| 🇬🇧 English    | `VP_english_UPG_03.bin`    | 54      | [Download](https://info.update.sony.net/HP002/VGIDLPB0401/contents/0002/VP_english_UPG_03.bin)    |
| 🇫🇷 French     | `VP_french_UPG_03.bin`     | 54      | [Download](https://info.update.sony.net/HP002/VGIDLPB0402/contents/0002/VP_french_UPG_03.bin)     |
| 🇩🇪 German     | `VP_german_UPG_03.bin`     | 54      | [Download](https://info.update.sony.net/HP002/VGIDLPB0403/contents/0002/VP_german_UPG_03.bin)     |
| 🇪🇸 Spanish    | `VP_spanish_UPG_03.bin`    | 54      | [Download](https://info.update.sony.net/HP002/VGIDLPB0404/contents/0002/VP_spanish_UPG_03.bin)    |
| 🇮🇹 Italian    | `VP_italian_UPG_03.bin`    | 54      | [Download](https://info.update.sony.net/HP002/VGIDLPB0405/contents/0002/VP_italian_UPG_03.bin)    |
| 🇵🇹 Portuguese | `VP_portuguese_UPG_03.bin` | 54      | [Download](https://info.update.sony.net/HP002/VGIDLPB0406/contents/0002/VP_portuguese_UPG_03.bin) |
| 🇳🇱 Dutch      | `VP_dutch_UPG_03.bin`      | 54      | [Download](https://info.update.sony.net/HP002/VGIDLPB0407/contents/0002/VP_dutch_UPG_03.bin)      |
| 🇸🇪 Swedish    | `VP_swedish_UPG_03.bin`    | 54      | [Download](https://info.update.sony.net/HP002/VGIDLPB0408/contents/0002/VP_swedish_UPG_03.bin)    |
| 🇫🇮 Finnish    | `VP_finnish_UPG_03.bin`    | 54      | [Download](https://info.update.sony.net/HP002/VGIDLPB0409/contents/0002/VP_finnish_UPG_03.bin)    |
| 🇹🇷 Turkish    | `VP_turkish_UPG_03.bin`    | 54      | [Download](https://info.update.sony.net/HP002/VGIDLPB0410/contents/0002/VP_turkish_UPG_03.bin)    |

## Voice Pack Format

```
┌───────────────────────────────────────┐
│  Header (4096 bytes)                  │
│  ├── 0x000  Random nonce (32 bytes)   │
│  └── 0x100  TLV metadata             │
│       ├── compression_type = 2        │
│       ├── body_size                   │
│       ├── decompressed_size (1 MB)    │
│       └── SHA-256 of decompressed     │
├───────────────────────────────────────┤
│  Body (offset 0x1000+)               │
│  AES-128-CBC encrypted               │
│  └── LZMA1 compressed                │
│       └── Voice guidance image (1MB)  │
│            ├── Header (version+count) │
│            ├── Entry table (54 × 8B)  │
│            │   (size, abs_offset)     │
│            └── MP3 files (48kHz mono) │
└───────────────────────────────────────┘
```

## The Key

```
AES-128-CBC Key: eibohjeCh6uegahf
AES-128-CBC IV:  miefeinuShu9eilo
```

Found hardcoded as ASCII strings in the MT2811 CM4 firmware's `.rodata` section at flash offset `0xD53A` (key) and `0xD529` (IV). Same key is used across all WH-1000XM4 units and all language packs.

## How It Was Found

1. **Connected** to the WH-1000XM4 over Bluetooth (the headphones were paired to a phone)
2. **Discovered** the RACE (Airoha Command Extensions) BLE GATT service
3. **Dumped** 59KB of CM4 firmware from flash using `RACE_STORAGE_PAGE_READ` (cmd `0x0403`)
4. **Located** the AES S-box at firmware offset `0x81F4`
5. **Determined** runtime base address `0x04200000` from ARM vector table literal pool
6. **Disassembled** the FOTA decryption function, traced key loading from literal pool

Full technical details: **[docs/WRITEUP.md](docs/WRITEUP.md)**

## Prerequisites

- [Bun](https://bun.sh) ≥ 1.0 — for the TypeScript CLI
- [uv](https://docs.astral.sh/uv/) — for the Python scripts (dependencies are resolved automatically via inline metadata)

## Extract the Key Yourself

If you want to verify the key independently, you can dump the firmware from your own paired WH-1000XM4 headphones and extract the key from it:

```bash
# Dump firmware over BLE and extract key
uv run cli/extract_key.py

# Or if you already have a firmware dump:
uv run cli/extract_key.py --firmware your_dump.bin

# Bun alternative (firmware dump required):
bun run cli/extract.ts --extract-key your_dump.bin
```

The tool searches the firmware binary for the AES S-box, finds adjacent null-terminated 16-byte ASCII strings, then validates each candidate pair by attempting to decrypt a voice pack and checking for valid LZMA headers.

## Writing voice packs back (fork addition)

Two new tools close the loop from "extract the prompts" to "put new prompts on the headphones."

| Tool | What it does |
| --- | --- |
| [`cli/repack.py`](cli/repack.py) | Rebuilds a valid voice-pack `.bin` from 54 `prompt_NN.mp3` files: LZMA1 with Sony's exact settings, AES-128-CBC, TLV container with a fresh SHA-256. `--roundtrip` decodes a real pack and rebuilds it byte-identical. It passes on all 10 languages. |
| [`cli/push_voice_pack.py`](cli/push_voice_pack.py) | Pushes a `.bin` to a WH-1000XM4 over BLE using the RACE FOTA sequence captured from the real app. **Dry run by default.** `--confirm` plus a typed `yes` is required to write. |

```bash
# Prove the repacker is lossless (no hardware involved)
uv run cli/repack.py --roundtrip voice-packs/VP_english_UPG_03.bin

# Build a custom pack from a folder of 54 prompt_NN.mp3 files
uv run cli/repack.py --build my_prompts/ output/VP_custom.bin voice-packs/VP_english_UPG_03.bin

# Print the push plan without touching Bluetooth
uv run cli/push_voice_pack.py output/VP_custom.bin

# Real write (headphones connected in macOS Bluetooth, Sound Connect app closed)
uv run cli/push_voice_pack.py output/VP_custom.bin --confirm
```

`push_voice_pack.py` needs [race-toolkit](https://github.com/auracast-research/race-toolkit) cloned **next to** this repo, for its `librace` protocol code.

### What it took to match the real app

Confirmed by decoding raw BLE captures byte by byte. Full details in [docs/COMMIT_INVESTIGATION.md](docs/COMMIT_INVESTIGATION.md).

- **Voice packs use the standard RACE FOTA flow,** with four undocumented Sony commands (`0x1c1c`, `0x0433`, `0x0431`, `0x0430`) between `FotaStart` and `FotaStartTransaction`.
- **Every RACE packet goes out as ATT Write Command,** split by hand at the write-without-response size (239 bytes). A single oversized GATT write makes macOS fall back to long writes, which the headphones reject with `Prepare Queue Full`.
- **`storage_type=1`, one 256-byte page per write, and `head=0x15`** for every command once the FOTA session starts. race-toolkit's defaults differ on all three.
- **Two payload fixes:** `FotaIntegrityCheck` needs `01 00 01`, and `FotaCommit` takes an empty payload with type `0x5c`.
- **The CDN voice packs are stale.** The device's async indication after the integrity check returns `0x0d` for the bundled CDN files and `0x00` for what the app pushes today. Rebuilding a pack from a capture of a real transfer fixed `FotaCommit`. The doc covers how to do that rebuild.

### Safety

Writes only touch the voice-guidance partition (`0x00510000`, about 6 MB of external flash), never the main firmware or bootloader. The worst realistic outcome is garbled or silent prompts, which you fix by pushing an original pack again. Still, this writes flash on real hardware. Use it at your own risk.

[docs/BLE_SNOOP_PLAN.md](docs/BLE_SNOOP_PLAN.md) explains how to capture the official app's traffic on Android or iOS.

## Project Structure

```
├── cli/
│   ├── extract.ts          # Bun CLI tool (extract + key finder)
│   ├── extract_key.py      # Python BLE firmware dumper + key finder
│   ├── repack.py           # Rebuild .bin from MP3 prompts (fork)
│   └── push_voice_pack.py  # Push .bin to headphones over BLE (fork)
├── docs/
│   ├── WRITEUP.md               # Full technical writeup
│   ├── BLE_SNOOP_PLAN.md        # Capturing the official app's traffic (fork)
│   └── COMMIT_INVESTIGATION.md  # Debugging FotaCommit, protocol findings (fork)
├── voice-packs/            # Downloaded .bin files (gitignored)
├── extracted/              # Extracted MP3 prompts (gitignored)
├── extract_all.py          # Python alternative extractor
├── package.json
└── README.md
```

## Disclaimer

This project documents security research conducted for educational purposes on personally owned hardware. The headphones analyzed were paired and connected normally — no unauthorized access was involved.

The voice pack `.bin` files and extracted MP3 prompts are copyrighted by Sony Corporation and are **not included** in this repository. The CLI tool downloads them directly from Sony's public CDN at runtime. Do not redistribute decrypted voice packs or extracted MP3 files. This tool is provided for research and interoperability purposes only.

## License

MIT. Original extraction work by [Helge Sverre](https://github.com/HelgeSverre/sony-vp-extract). Write-path additions by [bsmith1698](https://github.com/bsmith1698).
