# vc64_240p

[Português](README.md) · **English**

**Real 240p for Nintendo 64 Virtual Console on the Wii.** No deflicker blur, on the
official Nintendo emulator.

Patches an N64 VC channel so it outputs 240p instead of 480i, the way the games looked on
a CRT from real hardware. Optionally also removes the emulator's dark filter.

You keep everything the official emulator gives you — the per-game compatibility work
Nintendo did, native saves, suspend data — and you get the resolution the homebrew
emulators give you. Until now you had to pick one.

> **Why there are no before/after photos.** A still photo cannot show this. At any exposure
> long enough to capture a full frame, a camera integrates both interlaced fields, so 480i
> photographs as a complete progressive image. And the actual artefact of 480i is the
> flicker, which is temporal: it exists between fields, not within one. The difference is
> obvious on a CRT in person and invisible in a JPEG. A short video would show it; a
> screenshot never will.

---

## Why this exists

N64 Virtual Console on the Wii is locked to 480i. It is not a settings problem: the emulator
renders internally at 480 lines, so there is no 240p signal to force. NES, SNES and Genesis
VC titles all output 240p when the console is set to 480i — N64 is the exception.

Every other route is closed:

| route | why it fails |
|---|---|
| vWii (Wii U) | cannot output 240p at all — hardware limitation |
| Not64 / Wii64 | do 240p, but heavy games struggle and several have documented softlocks |
| Swiss / Nintendont | only force 480p/576p; force-240p was requested and never implemented |
| render 480 and downscale | the GX display copy **cannot downscale vertically**, only upscale |

So this patches the emulator binary instead.

## What it is not

**The GUI is not a ROM injector.** It patches N64 VC WADs that already exist — retail
channels, or injects somebody else built.

To build a channel from a ROM, use [FriishProduce](https://github.com/CatmanFan/FriishProduce):
it picks the right base for the emulator revision, handles `romc` compression and the ROM
size allocation. Then run its output through this for 240p.

The two fit together — no injector does 240p, and the GUI does not inject.

> The CLI (`src/vc64tool.py`) does carry an **experimental** `inject` command, `romc`
> support included. It is here because the findings around it are written up in
> [docs/TECHNICAL.md](docs/TECHNICAL.md), not because it beats FriishProduce — it doesn't.
> Per-game compatibility is the wall, not the injector: most ROMs fail on any given base no
> matter which tool built the channel. Writing `romc` also needs Jurai's `romc.exe` beside
> the script or in `./tools`; that binary is third-party and is not redistributed here.

---

## Usage

1. Run `vc64_240p.exe`
2. **Choose a WAD**
3. The panel reports both patches independently, so you know what will happen:
   ```
   240p        : CAN BE APPLIED
   dark filter : CAN BE APPLIED
   ```
4. Tick the dark filter box if you want it, then convert
5. The result is written **to the same folder as the original**, named after what was
   actually applied. The original file is not modified.

The output keeps the same channel id, so installing it **replaces the original channel and
keeps your saves**. You can run an already-converted WAD through again to add only the
dark filter.

### Requirements

- A Wii **common key** (`common-key.bin`, 16 bytes). **Not included.** Generate it once
  with [gzinject](https://github.com/PracticeROM/gzinject):
  ```
  gzinject -a genkey
  ```
  > `genkey` asks you to **type `45e` and press enter**. If you pipe empty input it prints
  > "successfully generated" and produces a **wrong key** — those three characters are the
  > decryption IV. This is not documented anywhere and costs an evening to work out.
- A Wii with **cIOS 249** and **Priiloader** (or BootMii).

Running from source instead of the exe: Python 3.8+ and `cryptography`.

### Installing the result

Put the WAD in `SD:/wad/` and install with **YAWM ModMii Edition** — Wii Mod Lite has no
IOS selector. Choose **IOS249**. Error `-1017` means that IOS has no trucha patch; try
another cIOS slot.

**Have Priiloader installed.** The realistic failure mode for a bad channel WAD is a
*banner brick* — the System Menu hanging while drawing the banner. Recovery: hold RESET
while powering on → Priiloader → Homebrew Channel → uninstall the channel.

### Two hard requirements of the patch itself

- **Set the console to 480i.** In 480p the emulator selects the `NTSC_PROG` render mode
  entry, which is not patched. By design — you would not want a 240p patch fighting a
  progressive display mode.
- **PAL is not supported.** The PAL code path overwrites the heights at runtime with 574,
  so patching that table entry alone does nothing. It would also target 288p, not 240p.

---

## How it works

Six changes in the emulator binary inside the WAD. 14 bytes per TV format, and since it
writes all four interlaced formats, 44 bytes in total:

| # | change | why |
|---|---|---|
| 1 | `viTVmode` `NTSC_INT` → `NTSC_DS` | double-strike output, i.e. 240p |
| 2 | `viHeight` 480 → 240 | the VI window |
| 3 | `efbHeight` / `xfbHeight` **left alone** | the emulator keeps drawing 480 lines, so nothing is cropped or zoomed |
| 4 | `xFBmode` **left as DF** | the double-field stride produces the 2:1 decimation, and therefore the correct geometry |
| 5 | `vfilter` → progressive profile | deflicker **off**, sharpness preserved |
| 6 | **NOP** the `add` that offsets the second VI field base by one line | without it you get 240p with severe flicker: the VI alternates between the even and odd line sets every frame |

Item 6 took the longest to find and is why a naive attempt at this looks broken.

**Nothing is hardcoded to a game.** Every target is located by structural pattern matching,
so it works on emulator builds it has never seen. See **[docs/TECHNICAL.md](docs/TECHNICAL.md)**
for how each one is found, the offsets, and the ten hypotheses that were wrong first.

### Dark filter removal (optional, 4 bytes)

The emulator darkens the picture compared to real hardware. The patch writes a `blr` over
the prologue of the function responsible, so it returns immediately. Global — everything
gets brighter, it is not a selective adjustment.

Method credit: **NoobletCheese / Maeson**, as implemented in FriishProduce. Here it is
located by structure rather than by fixed offset.

---

## Confirmed working

| game | emulator build |
|---|---|
| Majora's Mask (USA, retail VC) | LZ77 `content1` |
| Majora's Mask (PT-BR inject) | same build |
| Ocarina of Time (PT-BR inject) | raw DOL, completely different offsets |
| F-Zero X (PT-BR inject) | different SDK revision, `text1` at `0x800070C0` |
| Spider-Man (inject on a Mario Party base) | fifth build, confirmed after the fix below |

Five different emulator builds, all confirmed on real hardware on a CRT. The locator found
every target with no manual work in each case.

### This is still a testing phase

Only the titles above have been verified in person. The N64 VC library plus injects is a much
larger surface than one person with one CRT can cover, and emulator builds differ between
channels — that is the whole reason the targets are located structurally instead of by
hardcoded offsets.

The tool refuses to write rather than write something broken: if it cannot find a target it
stops and says so. So the failure mode you should expect is "it told me it could not patch
this WAD", not a channel that bricks.

**If a WAD does not work for you, please say which one.** A report that names the game and
what happened — the tool refused, or it patched but the TV stayed at 480i — is worth more
than a report that something worked. That is the only way this gets past five titles.

### One bug worth knowing about, now fixed

Until recently the patch was written into the render mode struct of a *single* TV format,
chosen in the interface. The emulator carries NTSC, PAL, MPAL and EURGB60 side by side and
picks one at runtime from the **console's** video setting, not from the WAD. If your console
used a different format than the one selected, the patch landed in a struct nothing reads —
and the tool still reported success. Silent failure, the worst kind.

It now patches every interlaced format at once. Patching a format the console never selects
is inert, so this is strictly safer than guessing. If you tried an earlier build and got no
240p, it is worth trying again.

This does **not** make PAL work — see "Two hard requirements" above. The PAL code path
overwrites the heights at runtime, so patching that struct is written but ineffective. It is
included because writing it costs nothing and excluding it would mean guessing again.

The **dark filter removal is confirmed in-game** on Majora's Mask (PT-BR inject) on real
hardware, and the target is located correctly in all 8 emulator builds tested here. It has
fewer in-game hours behind it than the 240p patch, which has four confirmed titles.

**Reports of builds where the locator fails are more useful than reports of ones that work.**

---

## Building

Windows, with Python 3.8+ and `pip install cryptography pyinstaller`:

```
build\build.bat
```

Produces `dist\vc64_240p.exe`, standalone. The GUI uses tkinter, which ships with the
standard Windows Python installer.

---

## Credits

- **BirdonWheels** — demonstrated on r/crtgaming in 2026 that 240p on VC N64 was possible,
  patching several emulators with radare2. Those patches were never released and never
  covered Ocarina of Time or Majora's Mask. This is an independent implementation with an
  automatic locator.
- **NoobletCheese / Maeson** — the dark filter method.
- **[FriishProduce](https://github.com/CatmanFan/FriishProduce)** (CatmanFan) — injection,
  and where the dark filter method is implemented.
- **[gzinject](https://github.com/PracticeROM/gzinject)** (KrimtonZ) — WAD handling
  reference and the common key generator.

## License

MIT — see [LICENSE](LICENSE).
