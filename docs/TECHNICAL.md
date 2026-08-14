# Technical notes

**English** · [Português](TECNICO.md)

How each target is located, what the WAD looks like inside, and — more useful than any of
that — **the hypotheses that were wrong**, so nobody has to spend an evening on them again.

---

## 1. The WAD

A Wii WAD is: header, cert chain, ticket, TMD, the content blob, footer — each section
aligned to `0x40`.

- The **title key** is wrapped with the Wii common key, IV = title id + 8 zero bytes.
- Each **content** is AES-128-CBC with IV = content index as `u16` big endian + 14 zero bytes.
- The TMD carries each content's size and SHA-1. Change a content and both must be rewritten.
- Signatures are handled by **trucha / fakesign**: zero the RSA signature, then brute force
  a padding field until the SHA-1 of the signed blob starts with `0x00`. TMD padding is at
  `0x1E2`, ticket at `0x1F2`. This is why cIOS 249 (trucha-patched) is required to install.

For an N64 VC channel:

| content | what it is |
|---|---|
| 0 | banner — **do not touch**, this is what banner-bricks |
| **1** | **the N64 emulator**, either a raw DOL or LZ77 (type `0x10`) compressed |
| 2, 3, 4, 6 | shared assets (wwwlib, font, HOME button) |
| 5 | U8 archive holding the ROM (`rom` or `romc`), save comments, banner TPL |
| 7 | boot DOL — not the emulator |

When `content1` is compressed, the patched result is stored **decompressed** and not
recompressed. That is what the gz project does and it works on hardware.

---

## 2. Locating the render mode table

The emulator carries a table of `GXRenderModeObj` structs, `0x3C` bytes each, one per TV
format. Found structurally rather than by offset:

- `fbWidth == 640`
- `efbHeight == xfbHeight`
- `viHeight == efbHeight`
- `viTVmode` is a valid `(format << 2) | mode` value
- the 7 `vfilter` taps sum to **64**

Field layout:

```
+0x00 viTVmode     +0x04 fbWidth      +0x06 efbHeight   +0x08 xfbHeight
+0x0A viXOrigin    +0x0C viYOrigin    +0x0E viWidth     +0x10 viHeight
+0x14 xFBmode      +0x18 field_rendering                +0x19 aa
+0x1A sample_pattern[24]               +0x32 vfilter[7]
```

`viTVmode = (format << 2) | mode`, where mode `0` = INT, `1` = DS (240p), `2` = PROG.

Typical result: seven entries — `NTSC_INT`, `NTSC_PROG`, `MPAL_INT`, `PAL_INT`, `PAL_PROG`
(twice) and `EURGB60_INT`.

**Every interlaced entry is patched, not just `NTSC_INT`.** Interlaced means the low two bits
of `viTVmode` are zero. The emulator carries all the formats side by side and picks one at
runtime from the console's video setting — the WAD has no say in it. Patching only one and
guessing which is live produced the worst failure this project had: the patch was written
correctly into a struct nothing reads, and the tool reported success. Writing all of them is
inert for the formats the console never selects, so it costs nothing and removes the guess.

Progressive entries are deliberately left alone: in 480p there is no interlacing to undo.

**The vfilter must still sum to 64 after patching.** A flat `09 09 0A 0A 0A 09 09` sums to
66 and is wrong; `00 00 15 16 15 00 00` (the profile from the progressive entry) is correct
and turns deflicker off.

## 3. Locating the VI field-base `add`

This is the one that matters and the one that is hard.

The VI framebuffer registers (`0xCC002000 + 0x1C..0x28`) **never appear as literal offsets
in any instruction** — the SDK writes them in a loop from a shadow array. Searching for
register offsets finds nothing at all.

What works: search for **four consecutive `srwi r0,r0,5` (`0x5400D97E`) spaced 12 bytes
apart**. That is the four framebuffer registers being packed as `address >> 5`.

From there, `VISetNextFramebuffer` builds a HorVer struct and calls a `calcFbbs` helper with
five output pointers. The struct semantics, recovered from the caller rather than guessed:

| offset | meaning |
|---|---|
| `+0x0A` | field parity (causes a swap) |
| `+0x20` | double-field flag |
| `+0x2C` | line unit (`<< 5`) |
| `+0x30` | main framebuffer |
| `+0x34`, `+0x38` | **the two field bases** |
| `+0x44`, `+0x48` | stereoscopic 3D flag and buffer |
| `+0x4C`, `+0x50` | right-eye versions |

Inside `calcFbbs`, the offset between the two fields is a single instruction:

```
stw   r9,0(r4)      field 1 = base
bne   +8            if HorVer[0x20] != 0
add   r9,r9,r31       field 2 = base + ONE LINE     <-- NOP this
stw   r9,0(r5)      field 2
```

The instruction pattern to match: `stw rS,0(rA)` / `bne +8` (`0x40820008`) / `b +8`
(`0x48000008`) / `add rD,rA,rB` where `rD == rA == rS`.

**It appears twice.** One is the main framebuffer path (preceded by `lwz rX,0x30(rY)`); the
other belongs to stereoscopic 3D and is **dead code** — its block is skipped when the 3D
flag is zero, which it always is. Patch the first. Getting this wrong produces a patch that
changes nothing and looks like the idea does not work.

## 4. Locating the dark filter

The function body compares two bytes against `0xFF`:

```
lwz    r0,4(r4)      80 04 00 04
cmpwi  r0,255        2C 00 00 FF
bne    +0x10         40 82 00 10
lwz    r0,8(r4)      80 04 00 08
cmpwi  r0,255        2C 00 00 FF
```

From there, walk **backwards** to the function prologue `stwu r1,-32(r1)` (`9421FFE0`) and
write `blr` (`4E800020`) over it.

A detail worth knowing: once patched, the `blr` overwrites the very prologue you searched
for, so a naive "is the prologue there?" check reports a patched build as *not found*. Look
for either the prologue or a `blr` and report which.

Confirmed in-game on real hardware (Majora's Mask, PT-BR inject) and located correctly in
all 8 emulator builds available here.

Method credit: NoobletCheese / Maeson, as implemented in FriishProduce.

---

## 5. What was wrong first

Ten dead ends, kept because knowing them is worth more than the working patch.

| hypothesis | how it died |
|---|---|
| set `efbHeight` 240 and let the display copy downscale | **black screen.** The GX display copy cannot downscale vertically, only upscale. This is a hardware limit, not a bug. |
| set `efbHeight` = `xfbHeight` = 240 | boots, UI correct, but the game renders **zoomed** — the emulator still draws 480 lines into a 240-line EFB, so it crops instead of shrinking |
| the `data4` clip box `-640,-480,640,480` is the viewport | patched it; nothing changed |
| `field_rendering = 1` | nothing changed |
| inject a call to `GXSetDispCopyYScale(0.5)` | impossible — see the first row; no code injection fixes a hardware limit |
| turn deflicker **on** to hide the flicker | works, but softens the image. Rejected: blurred 240p loses to sharp 480i |
| the `+0x48` field pointer is the bottom field | it is the **stereoscopic 3D buffer**, in a block that never executes. Guessed the semantics from the offset instead of tracing the caller |
| "the emulator never calls `GXSetDispCopyYScale`" | over-generalised from **one** of four display-copy sites. Two of them do read both heights and compute a scale. The empirical results do not change, but do not treat `efb == xfb` as a universal law |
| the emulator hardcodes its ROM size | Mario Kart 64 and Star Fox 64 (12 MB ROMs) contain **no** `0xC00000` constant anywhere. The size comes from the U8 |
| the emulator identifies or validates its ROM | no build contains its own ROM's CRC1/CRC2, internal name, cartridge code, the IPL3 CRC table, or a CIC seed table |

**The one that worked** was to keep `efb = xfb = 480` — leaving the emulator's rendering
completely untouched — and do the 2:1 decimation in the **VI** via the double-field stride,
then remove the one-line offset between the two field bases so they read the same set of
lines. 240p geometry, full image, zero flicker, no blur.

---

## 6. Notes on ROM injection (not implemented here)

Kept because it was measured and the conclusions are not obvious.

- **N64 VC emulator builds come in revisions**, and compatibility differs a lot between
  them. FriishProduce classifies by title id prefix: rev 0 (F-Zero X, Super Mario 64),
  rev 1 (`NAB`/`NAC`/`NAD` — Star Fox 64, Mario Kart 64, Ocarina of Time), rev 2
  (`NAK`/`NAJ`/`NAH` — Pokémon Snap, Sin & Punishment, Yoshi's Story), rev 3
  (`NA3`/`NAE`/`NAP`/`NAU`/`NAY`/`NAZ` — the `romc` ones). **Rev 2 is the most capable.**
- **Cross-game injection does work** — Kirby 64 into an Ocarina of Time base booted and ran
  here. But it is **per-game**: the GBAtemp compatibility list reports roughly 70% of ROMs
  hanging at the Classic Controller screen, and that rate was reproduced here.
- **No Rareware N64 game was ever released on Wii VC** (Microsoft bought Rare in 2002;
  Donkey Kong 64 went to the Wii U instead), so no emulator build was ever tuned against a
  Rare engine. DK64 was tested here on five different bases spanning CIC 6101/6102/6105/6106
  and 8–32 MB slots: it hangs on all five, roughly 6 seconds after boot, and then spins —
  an infinite loop, not a crash.
- The N64 header's game code is at **`0x3C..0x3D`**, not `0x3B`. `0x3B` is the media type.
  Reading two bytes from `0x3B` collides badly: Mario Kart `NKT` and Kirby `NK4` both become
  `NK`, and Perfect Dark `NPD` becomes `NP` and stops matching.
- Read the header **after** converting to z64. Reading it from a `.v64`/`.n64` gives a
  byte-swapped game code — "Silicon Valley" comes out as `iSiloc naVllye`, code `VS`
  instead of `SV`.
- Games that hard-require the 8 MB Expansion Pak: Donkey Kong 64 (`DO`), Majora's Mask
  (`ZS`), Perfect Dark (`PD`). Others merely use it if present.
- Searching an emulator for `EEPROM\0` / `SRAM\0` / `FLASH\0` strings **does not** tell you
  which save chips it implements — that is a generic name table present in every build.
  Super Mario 64 genuinely uses EEPROM and its own build "fails" that test.

### romc, the compressed rom format

Roughly half the emulator builds open a file called `romc` instead of `rom` — same rom,
stored compressed. Header, confirmed against retail WADs and gzinject's `romchu.c`:

```
bytes 0..2   decompressed size / 64, big endian
byte  3      type
```

Paper Mario retail reads `0x0A0000 * 64` = 40 MB, Majora's Mask `0x080000 * 64` = 32 MB.

**Retail bases ship type 1 and type 2. No public compressor produces type 2** — Jurai's
`romc.exe` emits type 1, `romc0.exe` emits type 0 (stored), and `romchu` only decompresses
type 2. FriishProduce has the same limitation.

**Type 1 works in a type-2 base.** Verified in-game: Bomberman 64 injected with type 1
romc into the Bomberman Hero and Ogre Battle 64 bases — both type 2 — booted and ran. The
type byte does not gate the decompressor. This does not appear to be documented anywhere
else.

Worth doing when writing a romc: decompress your own output with the same tool and compare
against the input before packing it. A silently wrong romc yields a channel that boots and
then dies, which is the worst thing to debug.

Two base-specific quirks:

- The **Bomberman Hero** base (`NA3`) will not start unless the rom's cartridge code at
  `0x3B` reads `NBD`. One byte. FriishProduce does the same.
- **Compatibility is per game, and the GBAtemp list is right.** Bomberman 64 is listed there
  as working on the Bomberman Hero and Ogre Battle bases with a "glitchy screen", and that
  is exactly what it does. It failed on all ten uncompressed-`rom` bases tested here,
  spanning emulator revisions 0, 1 and 2 and 8–32 MB slots. It also ran on **Mario Party 2**,
  which that list does not mention.

---

## 7. Verifying a build in Dolphin

Dolphin runs VC WADs directly (`Dolphin.exe -b -e file.wad`) and installs them to its own
NAND, which makes it a usable test bench. Some hard-won details:

- A running channel emits `IOS_ES ReadContent` continuously as the emulator streams the ROM
  from NAND. A hung one drops to near zero. Enable the `IOS_ES` log channel **and set
  `Verbosity = 4`** — `ReadContent` is INFO level and invisible at NOTICE.
- **That signal is not a reliable pass/fail on its own.** A working Kirby 64 peaked at 76
  reads/second while a working Majora's Mask sustained 570. A threshold calibrated on one
  game will throw away real successes.
- The 240p patch makes Dolphin render **magenta** — its VI emulation does not handle
  double-strike. Emulation still runs correctly. Test injection with the patch **off** if
  you want to see the picture.
- Dolphin's virtual NAND fills up fast at ~40 MB per channel, and a full NAND makes channels
  fail in ways that look exactly like a broken patch. Clear
  `Wii/title/00010001/<id>` between runs.
- Dolphin's GDB stub (`[General] GDBPort` in `Dolphin.ini`) works with `powerpc-eabi-gdb`.
  Two traps: it accepts **one** connection, so probing the port with `/dev/tcp` first
  consumes it and gdb then times out; and `GDBPort` persists in the ini, freezing **every**
  later boot while it waits for a debugger.
