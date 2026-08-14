#!/usr/bin/env python3
"""
vc64tool - Nintendo 64 Virtual Console WAD tool for the Wii.

Three things it does:

  info    Analyse a VC WAD: contents, emulator binary, render mode table,
          and whether the 240p patch targets can be located.

  patch   Apply the 240p patch to an existing N64 VC WAD (retail or inject).

  inject  Build a new channel from a base VC WAD + an N64 ROM, optionally
          with the 240p patch applied.

The 240p patch makes the official Nintendo emulator output 240p instead of
480i, without any blur filter, so N64 games look on a CRT the way they did on
real hardware -- while keeping the compatibility, native saves and suspend
data of the official emulator.

How the 240p patch works (6 changes, ~14 bytes):
  1. viTVmode  NTSC_INT -> NTSC_DS      double-strike, i.e. 240p
  2. viHeight  480 -> 240               the VI window
  3. efbHeight/xfbHeight LEFT ALONE     the emulator keeps drawing 480 lines,
                                        so nothing is cropped or zoomed
  4. xFBmode   LEFT as DF               the double-field stride is what gives
                                        the 2:1 decimation, i.e. the geometry
  5. vfilter -> progressive profile     deflicker OFF, sharpness preserved
  6. NOP the 'add' that offsets the      kills the even/odd line alternation
     second VI field base by one line    that otherwise shows up as heavy flicker

Nothing here is hardcoded to a particular game: every target is located by
structural pattern matching, so it works across different emulator builds.

Requires: Python 3.8+, the 'cryptography' package, and a Wii common key file.
See README for how to obtain the key.
"""
import argparse
import hashlib
import os
import struct
import sys

__version__ = '1.0'

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def align(n, a):
    return (n + a - 1) & ~(a - 1)


def aes_cbc(key, iv, data, encrypt):
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError:
        sys.exit("error: the 'cryptography' package is required (pip install cryptography)")
    c = Cipher(algorithms.AES(key), modes.CBC(iv))
    op = c.encryptor() if encrypt else c.decryptor()
    return op.update(data) + op.finalize()


def human(n):
    return f"{n:,}".replace(',', '.')


# --------------------------------------------------------------------------
# LZ77 (Nintendo type 0x10)
# --------------------------------------------------------------------------

def lz77_is(src):
    return len(src) > 4 and src[0] == 0x10


def lz77_decompress(src):
    if not lz77_is(src):
        return None
    total = src[1] | (src[2] << 8) | (src[3] << 16)
    out = bytearray()
    p = 4
    while len(out) < total and p < len(src):
        flags = src[p]; p += 1
        for bit in range(8):
            if len(out) >= total or p >= len(src):
                break
            if flags & (0x80 >> bit):
                if p + 1 >= len(src):
                    break
                b0, b1 = src[p], src[p + 1]; p += 2
                ln = (b0 >> 4) + 3
                disp = (((b0 & 0xF) << 8) | b1) + 1
                for _ in range(ln):
                    if len(out) >= total:
                        break
                    out.append(out[-disp])
            else:
                out.append(src[p]); p += 1
    return bytes(out)


# --------------------------------------------------------------------------
# U8 archive
# --------------------------------------------------------------------------

U8_MAGIC = 0x55AA382D


def u8_parse(d):
    if len(d) < 0x20 or struct.unpack('>I', d[:4])[0] != U8_MAGIC:
        return None
    root, _hdrsz, _dataoff = struct.unpack('>III', d[4:16])
    if root + 12 > len(d):
        return None
    count = struct.unpack('>III', d[root:root + 12])[2]
    if count == 0 or root + count * 12 > len(d):
        return None
    strs = root + count * 12
    nodes = []
    for i in range(count):
        o = root + i * 12
        t_no, doff, size = struct.unpack('>III', d[o:o + 12])
        typ, noff = t_no >> 24, t_no & 0xFFFFFF
        if typ not in (0, 1):
            return None
        end = d.find(b'\0', strs + noff)
        if end < 0:
            return None
        nodes.append(dict(type=typ, name=d[strs + noff:end].decode('ascii', 'replace'),
                          doff=doff, size=size))
    return nodes


def u8_blobs(d, nodes):
    return {i: d[n['doff']:n['doff'] + n['size']] for i, n in enumerate(nodes) if n['type'] == 0}


def u8_build(nodes, blobs):
    """Serialise exactly the way gzinject's u8.c does."""
    strtab = bytearray(b'\0')          # offset 0 is the root's empty name
    name_off = {0: 0}
    for i, n in enumerate(nodes):
        if i == 0:
            continue
        name_off[i] = len(strtab)
        strtab += n['name'].encode('ascii') + b'\0'

    nodec = len(nodes) - 1
    header_size = len(strtab) + (nodec + 1) * 12
    data_offset = align(0x20 + header_size, 0x20)

    dpos = 0
    for i, n in enumerate(nodes):
        if n['type'] == 0:
            n['doff'] = data_offset + dpos
            n['size'] = len(blobs[i])
            dpos += align(n['size'], 32)

    out = bytearray()
    out += struct.pack('>IIII', U8_MAGIC, 0x20, header_size, data_offset)
    out += b'\0' * 16
    for i, n in enumerate(nodes):
        out += struct.pack('>III', (n['type'] << 24) | name_off[i], n['doff'], n['size'])
    out += strtab
    out += b'\0' * (data_offset - len(out))
    for i, n in enumerate(nodes):
        if n['type'] == 0:
            b = blobs[i]
            out += b + b'\0' * (align(len(b), 32) - len(b))
    return bytes(out)


# --------------------------------------------------------------------------
# DOL
# --------------------------------------------------------------------------

class Dol:
    def __init__(self, d):
        self.d = d
        u32 = lambda o: struct.unpack('>I', d[o:o + 4])[0]
        self.secs = []
        for i in range(7):
            o, a, l = u32(0x00 + i * 4), u32(0x48 + i * 4), u32(0x90 + i * 4)
            if l:
                self.secs.append(('text%d' % i, o, a, l))
        for i in range(11):
            o, a, l = u32(0x1C + i * 4), u32(0x64 + i * 4), u32(0xAC + i * 4)
            if l:
                self.secs.append(('data%d' % i, o, a, l))
        self.entry = u32(0xE0)

    @staticmethod
    def looks_like(d):
        return len(d) > 0x100 and struct.unpack('>I', d[:4])[0] == 0x100

    def f2v(self, fo):
        for _n, o, a, l in self.secs:
            if o <= fo < o + l:
                return a + (fo - o)
        return None

    def is_text(self, fo):
        for n, o, _a, l in self.secs:
            if n.startswith('text') and o <= fo < o + l:
                return True
        return False


# --------------------------------------------------------------------------
# WAD
# --------------------------------------------------------------------------

class Wad:
    def __init__(self, path, common_key):
        d = open(path, 'rb').read()
        self.path = path
        self.common = common_key
        (self.hdr, self.wtype, self.cert_sz, self.crl_sz,
         self.tik_sz, tmd_sz, data_sz, self.foot_sz) = struct.unpack('>IIIIIIII', d[:0x20])

        o = align(self.hdr, 0x40)
        self.cert = d[o:o + self.cert_sz];  o += align(self.cert_sz, 0x40)
        self.crl = d[o:o + self.crl_sz];    o += align(self.crl_sz, 0x40)
        self.tik = d[o:o + self.tik_sz];    o += align(self.tik_sz, 0x40)
        self.tmd = bytearray(d[o:o + tmd_sz]); o += align(tmd_sz, 0x40)
        data_off = o
        self.foot = d[data_off + data_sz:data_off + data_sz + self.foot_sz] if self.foot_sz else b''

        self.title_id = self.tik[0x1DC:0x1E4]
        self.title_key = aes_cbc(common_key, self.title_id + b'\0' * 8, self.tik[0x1BF:0x1CF], False)
        self.ios = struct.unpack('>Q', self.tmd[0x184:0x18C])[0] & 0xFFFFFFFF
        self.region = struct.unpack('>H', self.tmd[0x19C:0x19E])[0]
        self.title_version = struct.unpack('>H', self.tmd[0x1DC:0x1DE])[0]
        self.ncont = struct.unpack('>H', self.tmd[0x1DE:0x1E0])[0]
        self.boot_index = struct.unpack('>H', self.tmd[0x1E0:0x1E2])[0]

        self.contents, self.rec, self.sha_ok = {}, {}, True
        cur = data_off
        for i in range(self.ncont):
            b = 0x1E4 + i * 36
            _cid, idx, _ct = struct.unpack('>IHH', self.tmd[b:b + 8])
            size = struct.unpack('>Q', self.tmd[b + 8:b + 0x10])[0]
            want = bytes(self.tmd[b + 0x10:b + 0x24])
            enc = d[cur:cur + align(size, 16)]
            iv = struct.pack('>H', idx) + b'\0' * 14
            plain = aes_cbc(self.title_key, iv, enc, False)[:size]
            if hashlib.sha1(plain).digest() != want:
                self.sha_ok = False
            self.contents[idx] = plain
            self.rec[idx] = b
            cur += align(size, 0x40)

    @property
    def code(self):
        return self.title_id[4:].decode('ascii', 'replace')

    # -- emulator ---------------------------------------------------------
    def find_emulator(self):
        """-> (index, decompressed_bytes, was_compressed) or (None, None, None)"""
        for idx in sorted(self.contents):
            p = self.contents[idx]
            if Dol.looks_like(p):
                return idx, p, False
            if lz77_is(p):
                dec = lz77_decompress(p)
                if dec and Dol.looks_like(dec):
                    return idx, dec, True
        return None, None, None

    # -- writing ----------------------------------------------------------
    def write(self, path, title_id=None, contents=None):
        contents = contents if contents is not None else self.contents
        tmd = bytearray(self.tmd)
        tik = bytearray(self.tik)

        if title_id is not None:
            # ticket and TMD both carry the title id; both get fakesigned below
            tik[0x1DC:0x1E4] = title_id
            tmd[0x18C:0x194] = title_id
            # re-wrap the title key so it still decrypts to the same value
            enc = aes_cbc(self.common, title_id + b'\0' * 8, self.title_key, True)
            tik[0x1BF:0x1CF] = enc
            key = self.title_key
        else:
            key = self.title_key

        blob = bytearray()
        for idx in sorted(contents):
            plain = bytes(contents[idx])
            b = self.rec[idx]
            tmd[b + 8:b + 0x10] = struct.pack('>Q', len(plain))
            tmd[b + 0x10:b + 0x24] = hashlib.sha1(plain).digest()
            padded = plain + b'\0' * (align(len(plain), 16) - len(plain))
            iv = struct.pack('>H', idx) + b'\0' * 14
            blob += aes_cbc(key, iv, padded, True)
            blob += b'\0' * (align(len(blob), 0x40) - len(blob))

        tmd_new = fakesign_tmd(bytes(tmd))
        tik_new = fakesign_tik(bytes(tik)) if title_id is not None else bytes(tik)

        out = bytearray()
        out += struct.pack('>IIIIIIII', self.hdr, self.wtype, self.cert_sz, self.crl_sz,
                           len(tik_new), len(tmd_new), len(blob), self.foot_sz)
        out += b'\0' * (align(self.hdr, 0x40) - len(out))

        def put(c):
            out.extend(c); out.extend(b'\0' * (align(len(c), 0x40) - len(c)))

        put(self.cert)
        if self.crl_sz:
            put(self.crl)
        put(tik_new)
        put(tmd_new)
        out.extend(blob)
        if self.foot:
            put(self.foot)
        open(path, 'wb').write(bytes(out))
        return len(out)


def fakesign_tmd(tmd):
    t = bytearray(tmd)
    t[0x04:0x104] = b'\0' * 0x100
    for v in range(0x10000):
        t[0x1E2:0x1E4] = struct.pack('>H', v)
        if hashlib.sha1(bytes(t[0x140:])).digest()[0] == 0:
            return bytes(t)
    sys.exit('error: could not fakesign the TMD')


def fakesign_tik(tik):
    t = bytearray(tik)
    t[0x04:0x104] = b'\0' * 0x100
    for v in range(0x10000):
        t[0x1F2:0x1F4] = struct.pack('>H', v)
        if hashlib.sha1(bytes(t[0x140:])).digest()[0] == 0:
            return bytes(t)
    sys.exit('error: could not fakesign the ticket')


# --------------------------------------------------------------------------
# pattern locators
# --------------------------------------------------------------------------

TVMODES = {0: 'NTSC_INT', 1: 'NTSC_DS', 2: 'NTSC_PROG', 4: 'PAL_INT', 5: 'PAL_DS',
           6: 'PAL_PROG', 8: 'MPAL_INT', 9: 'MPAL_DS', 10: 'MPAL_PROG',
           20: 'EURGB60_INT', 21: 'EURGB60_DS', 22: 'EURGB60_PROG'}

TV_BASE = {'NTSC': 0, 'PAL': 4, 'MPAL': 8, 'EURGB60': 20}

PROG_VFILTER = [0x00, 0x00, 0x15, 0x16, 0x15, 0x00, 0x00]

ADD_XO = 266


def find_render_modes(d):
    """GXRenderModeObj structs (0x3C bytes), located structurally."""
    out = []
    for base in range(0, len(d) - 0x3C, 4):
        tv = struct.unpack('>I', d[base:base + 4])[0]
        if tv not in TVMODES:
            continue
        fbw, efb, xfb, vx, vy, vw, vh = struct.unpack('>HHHHHHH', d[base + 4:base + 0x12])
        if fbw != 640 or not (200 <= efb <= 620) or efb != xfb:
            continue
        if vw not in (640, 704) or vh != efb or vx > 64 or vy > 64:
            continue
        if struct.unpack('>I', d[base + 0x14:base + 0x18])[0] not in (0, 1):
            continue
        vf = d[base + 0x32:base + 0x39]
        if sum(vf) != 64:
            continue
        out.append(dict(off=base, tv=tv, name=TVMODES[tv], efb=efb, xfb=xfb, vh=vh,
                        xfbmode=struct.unpack('>I', d[base + 0x14:base + 0x18])[0],
                        vfilter=list(vf)))
    return out


def find_field_adds(d, dol):
    """The 'add' that offsets the second VI field base by one line.

    Structural signature, register-allocation independent:
        stw rS, 0(rA)
        bne +8              0x40820008
        b   +8              0x48000008
        add rD, rA, rB      with rD == rA == rS
    Classified by which struct field was loaded just before it:
        +0x30 -> main framebuffer (the one to patch)
        +0x48 -> stereoscopic 3D buffer (dead code)
    """
    hits = []
    for p in range(0, len(d) - 16, 4):
        if not dol.is_text(p):
            continue
        w0, w1, w2, w3 = struct.unpack('>IIII', d[p:p + 16])
        if (w0 >> 26) != 36 or (w0 & 0xFFFF) != 0:
            continue
        if w1 != 0x40820008 or w2 != 0x48000008:
            continue
        if (w3 >> 26) != 31 or ((w3 >> 1) & 0x3FF) != ADD_XO:
            continue
        rs = (w0 >> 21) & 0x1F
        if not (((w3 >> 21) & 0x1F) == ((w3 >> 16) & 0x1F) == rs):
            continue
        field = None
        for q in range(p - 4, max(0, p - 160), -4):
            ins = struct.unpack('>I', d[q:q + 4])[0]
            if (ins >> 26) == 32 and (ins & 0xFFFF) in (0x30, 0x48):
                field = ins & 0xFFFF
                break
        hits.append(dict(off=p + 12, field=field, va=dol.f2v(p + 12)))
    return hits


def find_srwi5_clusters(d, dol):
    """4x 'srwi r0,r0,5' spaced 12 bytes apart: the VI framebuffer registers
    being packed (address >> 5). Confirms we found the right function."""
    SR = 0x5400D97E
    out = []
    for p in range(0, len(d) - 40, 4):
        if not dol.is_text(p):
            continue
        if all(struct.unpack('>I', d[p + 12 * k:p + 12 * k + 4])[0] == SR for k in range(4)):
            out.append(p)
    return out


class Targets:
    """Everything the 240p patch needs, located in one emulator binary."""

    def __init__(self, emu, tv='NTSC'):
        self.dol = Dol(emu)
        self.modes = find_render_modes(emu)
        self.adds = find_field_adds(emu, self.dol)
        self.clusters = find_srwi5_clusters(emu, self.dol)
        want = TV_BASE[tv]
        cand = [m for m in self.modes if m['tv'] == want and m['efb'] in (480, 528)]
        self.mode = cand[0] if cand else None
        main = [h for h in self.adds if h['field'] == 0x30]
        self.nop = main[0]['off'] if main else None
        self.tv = tv

    @property
    def ok(self):
        return self.mode is not None and self.nop is not None

    @property
    def interlaced(self):
        """Every interlaced render mode in the binary, one per TV format.

        The emulator carries NTSC/PAL/MPAL/EURGB60 side by side and picks one
        at runtime from the console's video setting -- not from the WAD. Mode
        bits are the low 2 of viTVmode, so 0 means interlaced.
        """
        return [m for m in self.modes
                if (m['tv'] & 3) == 0 and m['efb'] in (480, 528)]

    def _mode_ops(self, m):
        ops = [(m['off'], 4, m['tv'] | 1),               # viTVmode -> *_DS
               (m['off'] + 0x10, 2, m['vh'] // 2)]       # viHeight halved
        for i, v in enumerate(PROG_VFILTER):             # deflicker off
            ops.append((m['off'] + 0x32 + i, 1, v))
        return ops

    def patch_ops(self, every_tv=False):
        """-> list of (file_offset, size, value) to write into the emulator.

        With every_tv, patch the interlaced struct of every TV format instead
        of only the selected one. Patching a format the console never selects
        is inert -- that struct is simply not read -- so doing all of them is
        strictly safer than guessing which one is live. Guessing wrong writes
        a correct patch into a struct nobody reads, and the tool still reports
        success, which is the worst possible failure: silent.
        """
        ops = []
        for m in (self.interlaced if every_tv else [self.mode]):
            ops += self._mode_ops(m)
        ops.append((self.nop, 4, 0x60000000))            # NOP the field offset
        return ops


def apply_ops(buf, ops):
    b = bytearray(buf)
    for off, size, val in ops:
        if off + size > len(b):
            sys.exit(f'error: patch offset 0x{off:X} is outside the binary')
        if size == 1:
            b[off] = val & 0xFF
        elif size == 2:
            b[off:off + 2] = struct.pack('>H', val & 0xFFFF)
        else:
            b[off:off + 4] = struct.pack('>I', val & 0xFFFFFFFF)
    return bytes(b)


# --------------------------------------------------------------------------
# ROM header
# --------------------------------------------------------------------------

COUNTRY = {0x45: ('USA', 'NTSC'), 0x4A: ('Japan', 'NTSC'), 0x50: ('Europe', 'PAL'),
           0x44: ('Germany', 'PAL'), 0x46: ('France', 'PAL'), 0x53: ('Spain', 'PAL'),
           0x49: ('Italy', 'PAL'), 0x55: ('Australia', 'PAL'), 0x41: ('Asia', 'NTSC')}

REGION_NAME = {0: 'Japan', 1: 'USA', 2: 'Europe', 3: 'free/all'}


def rom_info(d):
    magic = struct.unpack('>I', d[:4])[0]
    fmt = {0x80371240: 'z64', 0x37804012: 'v64', 0x40123780: 'n64'}.get(magic)
    if fmt is None:
        return None
    name = d[0x20:0x34].decode('ascii', 'replace').strip('\0 ')
    ctry, tv = COUNTRY.get(d[0x3E], ('?', '?'))
    # header: 0x3B media type ('N' cart, 'C' expandable cart, 'D' 64DD disk),
    # 0x3C-0x3D the two-character game code, 0x3E the region.
    # Read the game code from 0x3C. An earlier version read 0x3B:0x3D, which is
    # media+first-letter -- that collides badly (Mario Kart 'NKT' and Kirby 'NK4'
    # both became 'NK', Doom 64 / Diddy Kong / South Park all became 'ND' like
    # Donkey Kong 64) and it missed Perfect Dark entirely.
    return dict(fmt=fmt, name=name, country=ctry, tv=tv,
                media=chr(d[0x3B]) if 32 <= d[0x3B] < 127 else '?',
                cart=d[0x3C:0x3E].decode('ascii', 'replace'), size=len(d))


def to_z64(d):
    magic = struct.unpack('>I', d[:4])[0]
    if magic == 0x80371240:
        return d
    a = bytearray(d)
    if magic == 0x37804012:                       # v64: byteswapped pairs
        a[0::2], a[1::2] = a[1::2], a[0::2]
        return bytes(a)
    if magic == 0x40123780:                       # n64: little-endian words
        return b''.join(d[i:i + 4][::-1] for i in range(0, len(d), 4))
    return None


# --------------------------------------------------------------------------
# operations
# --------------------------------------------------------------------------

def load_key(path):
    if not os.path.exists(path):
        sys.exit(f"error: common key not found at '{path}'.\n"
                 "       Generate it once with:  gzinject -a genkey\n"
                 "       (see README for details)")
    k = open(path, 'rb').read()
    if len(k) != 16:
        sys.exit('error: the common key must be exactly 16 bytes')
    return k




def find_8mb(emu, dol=None):
    """Locate the RDRAM size selection.

    The emulator defaults to 4 MB and only loads 8 MB when a flag is set:
        lis rD, 0x0040        <- 4 MB default
        ...
        b?? +8                <- skipped when the flag is clear   (the target)
        lis rD, 0x0080        <- 8 MB
    NOPing that branch makes 8 MB unconditional, which is what games needing the
    Expansion Pak require. Returns dict(off=, patched=) or None.
    """
    dol = dol or Dol(emu)
    for p in range(0, len(emu) - 32, 4):
        if not dol.is_text(p):
            continue
        w = struct.unpack('>I', emu[p:p + 4])[0]
        if (w >> 26) != 15 or ((w >> 16) & 0x1F) != 0 or (w & 0xFFFF) != 0x0040:
            continue
        rd = (w >> 21) & 0x1F
        for k in range(1, 7):
            q = p + k * 4
            w2 = struct.unpack('>I', emu[q:q + 4])[0]
            if (w2 >> 26) == 15 and ((w2 >> 16) & 0x1F) == 0 and (w2 & 0xFFFF) == 0x0080 \
               and ((w2 >> 21) & 0x1F) == rd:
                br = struct.unpack('>I', emu[q - 4:q])[0]
                if br == 0x60000000:
                    return dict(off=q - 4, patched=True)
                if (br >> 26) == 16 and (br & 0xFFFC) == 8:
                    return dict(off=q - 4, patched=False)
                break
    return None



# Rareware N64 titles, by 2-letter cartridge code.
#
# No Rare game was ever released on Wii Virtual Console -- Microsoft bought Rare
# in 2002, and Donkey Kong 64 went to the Wii U VC instead. So no Wii VC emulator
# build was ever tuned against a Rare engine, and in practice they do not run when
# injected: they hang right after the Classic Controller message. The scene blames
# Rare's custom RSP microcode. Warn instead of silently producing a dead channel.
# Keyed by the two-character game code at header 0x3C. Codes marked (v) were read
# straight out of a rom here; the rest follow the same published scheme.
RARE_GAMES = {
    'DO': 'Donkey Kong 64',        # (v) NDO
    'GE': 'GoldenEye 007',         # (v) NGE
    'BK': 'Banjo-Kazooie',         # (v) NBK
    'B7': 'Banjo-Tooie',           # (v) NB7
    'PD': 'Perfect Dark',          # (v) NPD
    'JF': 'Jet Force Gemini',      # (v) NJF
    'DY': 'Diddy Kong Racing',     # (v) NDY
    'FU': 'Conker\'s Bad Fur Day',
    'KI': 'Killer Instinct Gold',
    'MW': 'Mickey\'s Speedway USA',
    'BD': 'Blast Corps',
}

# Boot code (CIC) of a rom, identified by the CRC32 of its IPL3 (bytes 0x40..0x1000).
# The emulator does NOT detect this -- no build contains the CRC table or a seed
# table -- so it is reported for information, not used to gate anything.
IPL3_CIC = {
    0x6170A4A1: '6101', 0x90BB6CB5: '6102', 0x0B050EE0: '6103',
    0x98BC2C86: '6105', 0xACC8580A: '6106', 0x009E9EA3: '7102',
}


def rom_cic(rom):
    """-> '6102' etc, or None if the boot code is not one we know."""
    import zlib
    if len(rom) < 0x1000:
        return None
    return IPL3_CIC.get(zlib.crc32(rom[0x40:0x1000]) & 0xFFFFFFFF)


# Seed for the header checksum, per boot code. 6101 and 6102 share one.
_CRC_SEED = {'6101': 0xF8CA4DDC, '6102': 0xF8CA4DDC, '6103': 0xA3886759,
             '6105': 0xDF26F436, '6106': 0x1FEA617A, '7102': 0xF8CA4DDC}


def rom_crc(rom, cic):
    """Recompute the header CRC1/CRC2 over the first megabyte, the way the
    cartridge's own IPL3 does at boot. -> (crc1, crc2) or None.

    Worth checking: on real hardware a rom whose stored CRC does not match makes
    IPL3 refuse to start the game. It also catches a bad dump or a byte order
    mix-up instantly, since either destroys the checksum completely.
    """
    seed = _CRC_SEED.get(cic)
    if seed is None or len(rom) < 0x101000:
        return None
    m = 0xFFFFFFFF
    t1 = t2 = t3 = t4 = t5 = t6 = seed
    for i in range(0x1000, 0x101000, 4):
        d = struct.unpack('>I', rom[i:i + 4])[0]
        if ((t6 + d) & m) < t6:
            t4 = (t4 + 1) & m
        t6 = (t6 + d) & m
        t3 ^= d
        b = d & 0x1F
        r = (((d << b) | (d >> (32 - b))) & m) if b else d
        t5 = (t5 + r) & m
        t2 = (t2 ^ r) if t2 > d else (t2 ^ t6 ^ d)
        if cic == '6105':
            o = 0x40 + 0x0710 + (i & 0xFF)
            t1 = (t1 + (struct.unpack('>I', rom[o:o + 4])[0] ^ d)) & m
        else:
            t1 = (t1 + (t5 ^ d)) & m
    if cic == '6103':
        return ((t6 ^ t4) + t3) & m, ((t5 ^ t2) + t1) & m
    if cic == '6106':
        return ((t6 * t4) + t3) & m, ((t5 * t2) + t1) & m
    return (t6 ^ t4 ^ t3) & m, (t5 ^ t2 ^ t1) & m


# The VC emulator darkens the picture -- the "dark filter" people complain about.
# It is a function in the colour path; neutering it restores the original brightness.
#
# Located the same way as everything else here: by structure, not by fixed offset.
# The body compares two bytes against 0xFF (`lwz r0,4(r4)` / `cmpwi r0,255` / `bne`
# / `lwz r0,8(r4)` / `cmpwi r0,255`); from there walk BACKWARDS to the function
# prologue (`stwu r1,-32(r1)`) and write `blr` over it, so the function returns
# immediately.
#
# Credit: the method was reported by NoobletCheese / Maeson on GBAtemp, and is
# implemented in FriishProduce. Verified here to match all 8 emulator builds on hand.
_DARK_BODY = bytes.fromhex('80040004' '2C0000FF' '40820010' '80040008' '2C0000FF')
_DARK_PROLOGUE = bytes.fromhex('9421FFE0')      # stwu r1,-32(r1)
_BLR = 0x4E800020


def dark_filter_state(emu):
    """-> ('can-remove', off) | ('already-removed', off) | ('not-found', None)

    Once patched, the `blr` overwrites the very prologue this searches for, so a
    plain "look for the prologue" check reports the patched build as not-found.
    Look for either, and say which.
    """
    i = emu.find(_DARK_BODY)
    if i < 0:
        return 'not-found', None
    blr = struct.pack('>I', _BLR)
    for k in range(i, max(i - 400, 0), -1):
        if emu[k:k + 4] == _DARK_PROLOGUE:
            return 'can-remove', k
        if emu[k:k + 4] == blr:
            return 'already-removed', k
    return 'not-found', None


def find_dark_filter(emu):
    """-> file offset to write `blr` at, or None if it is not patchable."""
    state, off = dark_filter_state(emu)
    return off if state == 'can-remove' else None


# --------------------------------------------------------------------------
# romc -- the compressed rom format some emulator builds open
# --------------------------------------------------------------------------
#
# Roughly half the N64 VC emulator builds open a file called `romc` instead of
# `rom`. Same rom, stored compressed. There is no compressor in this project:
# it shells out to Jurai's `romc.exe` (type 1) or `romc0.exe` (type 0), the same
# binaries FriishProduce uses.
#
# Header, confirmed against retail WADs and against gzinject's romchu.c:
#   bytes 0..2  decompressed size / 64, big endian
#   byte  3     type
#   then the payload
# Paper Mario retail reads 0x0A0000*64 = 40 MB, Majora's 0x080000*64 = 32 MB.

ROMC_TOOLS = ('romc.exe', 'romc0.exe')


def _tooldir():
    here = os.path.dirname(os.path.abspath(
        sys.executable if getattr(sys, 'frozen', False) else __file__))
    return [here, os.path.join(here, 'tools'), os.getcwd(),
            os.path.join(os.getcwd(), 'tools')]


def find_romc_tool(which='romc.exe'):
    for d in _tooldir():
        p = os.path.join(d, which)
        if os.path.exists(p):
            return p
    return None


def romc_header(blob):
    """-> (decompressed_size, type) read from a romc blob."""
    if len(blob) < 4:
        return None, None
    return ((blob[0] << 16) | (blob[1] << 8) | blob[2]) * 64, blob[3]


def romc_compress(rom, ctype=1, verify=True):
    """Compress a raw z64 into romc. ctype 1 = real compression, 0 = stored.

    verify runs the compressor's own decoder over the result and compares against
    the input. A silently-wrong romc produces a channel that boots and then dies,
    which is the worst failure mode to debug, so this is on by default.
    """
    import subprocess
    import tempfile
    tool = find_romc_tool('romc.exe' if ctype else 'romc0.exe')
    if tool is None:
        raise RuntimeError(
            f"{'romc.exe' if ctype else 'romc0.exe'} not found. It is not shipped "
            "with this tool; take it from FriishProduce's Resources/apps and put "
            "it next to this program or in a 'tools' folder beside it.")
    d = tempfile.mkdtemp(prefix='romc')
    src, dst = os.path.join(d, 'rom'), os.path.join(d, 'romc')
    open(src, 'wb').write(rom)
    cmd = [tool, 'e', src, dst] if ctype else [tool, src, dst]
    subprocess.run(cmd, cwd=d, capture_output=True)
    if not os.path.exists(dst):
        raise RuntimeError('the romc compressor produced no output')
    out = open(dst, 'rb').read()

    size, t = romc_header(out)
    if size != len(rom):
        raise RuntimeError(f'romc header declares {size} bytes but the rom is {len(rom)}')

    if verify and ctype:
        back = os.path.join(d, 'back')
        subprocess.run([tool, 'd', dst, back], cwd=d, capture_output=True)
        if not os.path.exists(back) or open(back, 'rb').read() != rom:
            raise RuntimeError('romc round-trip FAILED -- refusing to write a '
                               'channel that would be silently corrupt')
    return out


def check_rom(rom):
    """-> (ok, message). ok is None when we cannot judge."""
    cic = rom_cic(rom)
    if cic is None:
        return None, 'boot code not recognised, cannot verify the checksum'
    got = rom_crc(rom, cic)
    if got is None:
        return None, f'CIC-{cic}, rom too short to verify the checksum'
    want = struct.unpack('>II', rom[0x10:0x18])
    if want == got:
        return True, f'CIC-{cic}, header checksum OK'
    return False, (f'CIC-{cic}, header checksum MISMATCH: header says '
                   f'{want[0]:08X}/{want[1]:08X}, the data gives {got[0]:08X}/{got[1]:08X}')


# NOTE -- REMOVED, DO NOT BRING BACK.
#
# An earlier version searched the emulator for the strings 'EEPROM\0', 'SRAM\0',
# 'FLASH\0', 'MEMORY-PAK\0' and claimed a base "has no EEPROM handler, this game
# WILL hang". That was WRONG. Super Mario 64 genuinely uses EEPROM and the check
# reported that its own build had none -- those strings are a generic name table,
# not evidence of an implementation. The whole heuristic, and the ROM_SAVE table
# that fed it, have been deleted rather than softened.


def verdict(wad_path, key_path, tv='NTSC'):
    """Silent analysis -> a small dict of yes/no answers, for a GUI banner."""
    out = dict(ok=False, hashes=False, patchable=False, base=False,
               romc=False, code='', compressed=False, reason='')
    try:
        w = Wad(wad_path, load_key(key_path))
    except SystemExit as e:
        out['reason'] = str(e)
        return out
    except Exception as e:
        out['reason'] = f'{type(e).__name__}: {e}'
        return out
    out['hashes'] = w.sha_ok
    out['code'] = w.code
    out['region'] = w.region
    idx, emu, comp = w.find_emulator()
    if emu is None:
        out['reason'] = 'no emulator binary found'
        return out
    out['compressed'] = comp
    out['base'] = b'rom\x00' in emu
    out['romc'] = b'romc\x00' in emu
    out['patchable'] = Targets(emu, tv).ok
    out['dark'], out['dark_off'] = dark_filter_state(emu)
    m8 = find_8mb(emu)
    out['mem8'] = 'yes' if (m8 and m8['patched']) else ('can-enable' if m8 else 'unknown')
    out['ok'] = True
    return out


def report_wad(w, t=None):
    print(f"  title id      : {w.title_id.hex()}  ({w.code})")
    print(f"  region        : {w.region} ({REGION_NAME.get(w.region,'?')})   "
          f"IOS{w.ios}   version {w.title_version}")
    print(f"  contents      : {w.ncont}   boot index {w.boot_index}")
    print(f"  content hashes: {'all match the TMD' if w.sha_ok else '*** MISMATCH ***'}")


def cmd_info(a):
    key = load_key(a.key)
    w = Wad(a.wad, key)
    print(f"=== {a.wad}")
    report_wad(w)
    idx, emu, comp = w.find_emulator()
    if emu is None:
        print("  emulator      : NOT FOUND (no raw or LZ77-compressed DOL)")
        return 3
    print(f"\n  emulator      : content{idx}"
          f"{f' (LZ77 -> {human(len(emu))} bytes)' if comp else ' (raw DOL)'}")
    print(f"    sha1 in wad : {hashlib.sha1(w.contents[idx]).hexdigest()}")
    print(f"    sha1 unpacked: {hashlib.sha1(emu).hexdigest()}")

    t = Targets(emu, a.tv)
    print(f"\n  render modes found: {len(t.modes)}")
    for m in t.modes:
        mark = '  <== target' if t.mode and m['off'] == t.mode['off'] else ''
        print(f"    0x{m['off']:06X}  {m['name']:<12} efb={m['efb']} xfb={m['xfb']} "
              f"viH={m['vh']} xFB={'DF' if m['xfbmode'] else 'SF'}{mark}")
    print(f"\n  VI framebuffer register clusters: {len(t.clusters)}")
    print(f"  field-offset 'add' candidates    : {len(t.adds)}")
    for h in t.adds:
        role = {0x30: 'main framebuffer  <== target', 0x48: '3D buffer (dead code)'}
        print(f"    0x{h['off']:06X} va 0x{h['va'] or 0:08X}  "
              f"field +0x{h['field']:02X}  {role.get(h['field'],'')}"
              if h['field'] is not None else f"    0x{h['off']:06X}  (unclassified)")

    # ROM inside content5
    for i, p in w.contents.items():
        n = u8_parse(p)
        if n:
            roms = [x for x in n if x['type'] == 0 and x['name'] in ('rom', 'romc')]
            if roms:
                print(f"\n  content{i} is a U8 archive with {sum(1 for x in n if x['type']==0)} files")
                for r in roms:
                    print(f"    {r['name']:<5} {human(r['size'])} bytes")
    wants_raw = b'rom\x00' in emu
    wants_comp = b'romc\x00' in emu
    names = ', '.join(n for n, v in (("rom (uncompressed)", wants_raw),
                                     ("romc (compressed)", wants_comp)) if v) or 'none found'
    print(f"  rom file this emulator opens: {names}")
    print(f"  usable as an injection base : "
          f"{'YES' if wants_raw else 'NO (needs a romc compressor)'}")
    print(f"  240p patch    : {'CAN BE APPLIED' if t.ok else '*** TARGETS NOT FOUND ***'}")
    return 0 if t.ok else 4


def do_patch_emulator(w, tv, verbose=True, every_tv=True):
    """Locate + apply the 240p patch. Returns updated contents dict."""
    idx, emu, comp = w.find_emulator()
    if emu is None:
        sys.exit('error: could not find the emulator binary in this WAD')
    t = Targets(emu, tv)
    if not t.ok:
        missing = []
        if t.mode is None:
            missing.append(f'{tv}_INT render mode entry')
        if t.nop is None:
            missing.append("field-offset 'add' instruction")
        sys.exit('error: could not locate: ' + ', '.join(missing) +
                 '\n       This emulator build needs manual analysis.')
    if verbose:
        print(f"  240p targets  : render mode {t.mode['name']} @ 0x{t.mode['off']:06X}, "
              f"NOP @ 0x{t.nop:06X}")
        if comp:
            print(f"                  (content{idx} was LZ77; stored decompressed, "
                  f"which is what the gz project does)")
    contents = dict(w.contents)
    every = every_tv
    if verbose and every:
        outros = [m['name'] for m in t.interlaced if m['off'] != t.mode['off']]
        if outros:
            print(f"                  tambem: {', '.join(outros)}"
                  f"  (o console escolhe o formato, nao a WAD)")
    contents[idx] = apply_ops(emu, t.patch_ops(every_tv=every))
    return contents


def cmd_patch(a):
    key = load_key(a.key)
    w = Wad(a.wad, key)
    print(f"=== patch: {a.wad}")
    report_wad(w)
    if not w.sha_ok:
        sys.exit('error: content hashes do not match the TMD; refusing to touch this WAD')
    contents = do_patch_emulator(w, a.tv, every_tv=not a.only_tv)
    tid = a.id.encode('ascii') if a.id else None
    title_id = (w.title_id[:4] + tid) if tid else None
    n = w.write(a.out, title_id=title_id, contents=contents)
    print(f"  written       : {a.out}  ({human(n)} bytes)")
    if title_id is None:
        print("  note          : same title id, so this REPLACES the original channel")
    return 0


def cmd_inject(a):
    key = load_key(a.key)
    w = Wad(a.base, key)
    print(f"=== inject: {a.rom}  into  {a.base}")
    report_wad(w)
    if not w.sha_ok:
        sys.exit('error: base content hashes do not match the TMD; refusing to use it')

    raw = open(a.rom, 'rb').read()
    src = rom_info(raw)
    if src is None:
        sys.exit('error: not a recognisable N64 ROM (bad header magic)')
    # Convert FIRST, then read the header. Reading it from a .v64/.n64 gives a
    # byte-swapped game code -- 'Silicon Valley' came out as 'iSiloc naVllye' and
    # its code as VS instead of SV -- and that code is what drives the Expansion
    # Pak rule and the Rareware warning.
    rom = to_z64(raw)
    ri = rom_info(rom)
    ri['fmt'] = src['fmt']
    print(f"\n  rom           : {ri['name']!r}  cart {ri['cart']}  "
          f"{ri['country']} ({ri['tv']})  {human(ri['size'])} bytes  [{ri['fmt']}]")
    if ri['fmt'] != 'z64':
        print(f"                  converted {ri['fmt']} -> z64 (big endian)")
    cic = rom_cic(rom)
    if cic:
        print(f"  boot code     : CIC-{cic}")

    if ri['cart'] in RARE_GAMES:
        print(f"\n  ** {RARE_GAMES[ri['cart']]} is a RAREWARE title, and Rare games are not")
        print( "     known to run as Wii VC injects on ANY base. No Rare game was ever")
        print( "     released on Wii Virtual Console -- Microsoft bought Rare in 2002 and")
        print( "     Donkey Kong 64 went to the Wii U instead -- so no emulator build was")
        print( "     ever tuned against a Rare engine. The reported symptom is a hang right")
        print( "     after the Classic Controller message, and that is what we measured too.")
        print( "     Building it anyway, but do not expect it to boot.")

    base_tv = {0: 'NTSC', 1: 'NTSC', 2: 'PAL'}.get(w.region)
    if base_tv and ri['tv'] != '?' and ri['tv'] != base_tv:
        print(f"\n  ** REGION MISMATCH: {ri['tv']} rom into a {base_tv} base.")
        print( "     A PAL rom is timed for 50Hz. What actually decides the video mode is")
        print( "     the CONSOLE's TV format, not the base, so on an NTSC console expect the")
        print( "     game to run fast or misbehave. Matching regions is the safe convention;")
        print( "     test before assuming it works.")

    # which filename does THIS emulator build open? different builds differ, and
    # 'romc' means the compressed format, which we cannot produce.
    _ei, emu_bin, _ec = w.find_emulator()
    if emu_bin is None:
        sys.exit('error: could not find the emulator binary in this base')
    wants_raw = b'rom\x00' in emu_bin
    wants_comp = b'romc\x00' in emu_bin
    print(f"\n  base emulator : opens "
          f"{'rom (uncompressed)' if wants_raw else ''}"
          f"{' and ' if wants_raw and wants_comp else ''}"
          f"{'romc (compressed)' if wants_comp else ''}"
          f"{'?? neither name found' if not (wants_raw or wants_comp) else ''}")
    if not (wants_raw or wants_comp):
        sys.exit("error: this base's emulator opens neither 'rom' nor 'romc'")

    # Bomberman Hero's build (NA3) refuses to start unless the rom's cartridge
    # code says NBD. FriishProduce does the same. Harmless on other bases, so it
    # is applied only when the base actually is NA3.
    if w.code[:3].upper() == 'NA3':
        rom = bytearray(rom)
        rom[0x3B:0x3E] = b'NBD'
        rom = bytes(rom)
        print("  base quirk    : Bomberman Hero base -- rom cartridge code set to NBD")

    # find the U8 content holding the rom
    target = romnode = None
    for i, p in w.contents.items():
        n = u8_parse(p)
        if not n:
            continue
        for j, x in enumerate(n):
            if x['type'] == 0 and x['name'] in ('rom', 'romc'):
                target, romnode, nodes = i, j, n
                break
        if target is not None:
            break
    if target is None:
        sys.exit("error: could not find a 'rom' or 'romc' file in any U8 content")

    blobs = u8_blobs(w.contents[target], nodes)
    old = nodes[romnode]['name']
    oldsz = nodes[romnode]['size']

    # Store the rom in the format THIS build opens. Putting a raw rom in a build
    # that only opens 'romc' is what made an earlier attempt boot and then report
    # corrupted data.
    if wants_raw:
        nodes[romnode]['name'] = 'rom'
        payload = rom
    else:
        _sz, ctype = romc_header(blobs[romnode])
        # Jurai's compressor only emits type 1 (romc.exe) or type 0 (romc0.exe).
        # Nothing public can produce type 2, which is what several retail bases
        # ship. FriishProduce has the same limit and its type 1 output is reported
        # to work, so type 1 is what goes in -- but say so rather than pretending
        # to match.
        print(f"  rom format    : this build opens 'romc' (original is type {ctype})")
        try:
            payload = romc_compress(rom, ctype=1)
        except RuntimeError as e:
            sys.exit(f'error: {e}')
        _s2, t2 = romc_header(payload)
        if t2 != ctype:
            print(f"                  ** writing type {t2}, NOT type {ctype}. No public "
                  f"compressor produces type {ctype};")
            print(f"                     FriishProduce has the same limit. Untested "
                  f"combination -- report back.")
        print(f"                  {human(len(rom))} -> {human(len(payload))} bytes "
              f"({100 * len(payload) / len(rom):.0f}%), round-trip verified")
        nodes[romnode]['name'] = 'romc'
    blobs[romnode] = payload
    newu8 = u8_build(nodes, blobs)
    print(f"\n  content{target}      : replaced '{old}' ({human(oldsz)}) with "
          f"'{nodes[romnode]['name']}' ({human(len(payload))})")
    print(f"                  U8 {human(len(w.contents[target]))} -> {human(len(newu8))} bytes")

    contents = dict(w.contents)
    contents[target] = newu8

    if ri['cart'] in NEEDS_8MB:
        _ei2, emu2, _c2 = w.find_emulator()
        m8 = find_8mb(emu2)
        if m8 is None:
            print("\n  ** this game needs the 8 MB Expansion Pak but I could not find the")
            print("     memory switch in this base. It will probably freeze on startup.")
        elif m8['patched']:
            print(f"\n  expansion pak: base already runs at 8 MB")
        else:
            print(f"\n  expansion pak: switching 8 MB on (NOP at 0x{m8['off']:06X}) -- "
                  f"{ri['name']} requires it")
            contents[_ei2] = apply_ops(emu2, [(m8['off'], 4, 0x60000000)])

    if a.mode == '240p':
        base_emu = contents.get(_ei2) if ri['cart'] in NEEDS_8MB else None
        w2 = Wad(a.base, key)             # locate on a clean copy
        if base_emu is not None:
            w2.contents[_ei2] = base_emu  # keep the 8 MB change
        patched = do_patch_emulator(w2, a.tv, every_tv=not getattr(a, 'only_tv', False))
        for k, v in patched.items():
            if v != w.contents[k]:
                contents[k] = v
    else:
        print("  video         : left at original (480i)")

    tid = a.id.encode('ascii') if a.id else None
    if tid and len(tid) != 4:
        sys.exit('error: --id must be exactly 4 characters')
    title_id = (w.title_id[:4] + tid) if tid else None
    n = w.write(a.out, title_id=title_id, contents=contents)
    print(f"\n  written       : {a.out}  ({human(n)} bytes)")
    if title_id is None:
        print("  ** no --id given, so this REPLACES the base channel. Use --id to install alongside.")
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


# Games that REQUIRE the 8 MB Expansion Pak to run at all, by game code (0x3C).
# Only these three hard-require it; plenty of others merely use it if present.
NEEDS_8MB = {
    'DO',   # Donkey Kong 64        (NDO)
    'ZS',   # Zelda: Majora's Mask  (NZS)
    'PD',   # Perfect Dark          (NPD)
}


def scan_bases(rom_path, folder, key_path, tv='NTSC'):
    """Rank every .wad in a folder as an injection base for one rom.
    Returns (rom_info, rows) where each row is a dict. No printing."""
    key = load_key(key_path)
    # 0x1000 bytes: enough for the header AND the IPL3 the CIC is derived from
    head = to_z64(open(rom_path, 'rb').read(0x1000))
    ri = rom_info(head) if head else None
    if ri is None:
        return None, []
    ri['needs8mb'] = ri['cart'] in NEEDS_8MB
    ri['cic'] = rom_cic(head)
    romsize = os.path.getsize(rom_path)
    ri['filesize'] = romsize

    rows = []
    for f in sorted(x for x in os.listdir(folder) if x.lower().endswith('.wad')):
        full = os.path.join(folder, f)
        v = verdict(full, key_path, tv)
        slot = 0
        try:
            w = Wad(full, key)
            for _i, p in w.contents.items():
                n = u8_parse(p)
                if not n:
                    continue
                for x in n:
                    if x['type'] == 0 and x['name'] in ('rom', 'romc'):
                        slot = max(slot, x['size'])
        except Exception:
            pass
        score, notes = 0, []
        if not v.get('ok'):
            notes.append('unreadable')
        elif not v['base']:
            notes.append("opens 'romc' only, not usable")
        else:
            score += 100
            if v['patchable']:
                score += 10
            else:
                notes.append('already patched, or 240p targets not found')
            if slot >= romsize:
                score += 5
            else:
                notes.append(f'rom slot only {human(slot)}')
        mem8 = v.get('mem8', 'unknown')
        if score >= 100 and ri['needs8mb']:
            if mem8 == 'yes':
                score += 3
            elif mem8 == 'can-enable':
                score += 2
                notes.append('8 MB will be enabled automatically')
            else:
                notes.append('could not find the 8 MB switch -- this game may freeze')
        rows.append(dict(name=f, path=full, score=score, slot=slot, mem8=mem8,
                         usable=score >= 100,
                         patchable=v.get('patchable', False), notes=notes))
    rows.sort(key=lambda r: (-r['score'], r['name']))
    return ri, rows


def cmd_findbase(a):
    ri, rows = scan_bases(a.rom, a.folder, a.key, a.tv)
    if ri is None:
        sys.exit('error: not a recognisable N64 ROM')
    print(f"=== looking for a base for: {ri['name']!r}")
    print(f"  code {ri['media']}{ri['cart']}   {ri['country']} ({ri['tv']})   "
          f"{human(ri['filesize'])} bytes"
          + (f"   boot code CIC-{ri['cic']}" if ri.get('cic') else ''))
    if ri['needs8mb']:
        print("  this game REQUIRES the 8 MB Expansion Pak")
    if ri['cart'] in RARE_GAMES:
        print(f"\n  ** {RARE_GAMES[ri['cart']]} is a RAREWARE title.")
        print( "     No Rare game was ever released on Wii Virtual Console, so no emulator")
        print( "     build was ever tuned against a Rare engine. In practice these hang right")
        print( "     after the Classic Controller message on every known base -- the scene")
        print( "     blames Rare's custom RSP microcode. Expect this not to work.")
    if not rows:
        sys.exit(f'error: no .wad files in {a.folder}')
    print(f"\n  scanned {len(rows)} wad(s) in {a.folder}\n")

    ok = [r for r in rows if r['usable']]
    if ok:
        print("  CAN BE USED AS A BASE")
        print("  " + "-" * 66)
        mtxt = {'yes': '8 MB already on', 'can-enable': '8 MB can be switched on',
                'unknown': '8 MB switch not found'}
        for r in ok:
            print(f"    {r['name']}")
            print(f"      rom slot {human(r['slot'])}   {mtxt.get(r['mem8'], '')}"
                  + (f"\n      note: {'; '.join(r['notes'])}" if r['notes'] else ''))
    bad = [r for r in rows if not r['usable']]
    if bad:
        print("\n  CANNOT be used as a base")
        print("  " + "-" * 66)
        for r in bad:
            print(f"    {r['name']}   ({'; '.join(r['notes'])})")

    print()
    if ok:
        print(f"  >>> RECOMMENDED: {ok[0]['name']}")
    else:
        print("  >>> none of these can be used as an injection base.")
        print("      You need a wad whose emulator opens an uncompressed 'rom'.")
    if ri['needs8mb']:
        print("\n  This game needs the Expansion Pak. 'inject' turns the 8 MB switch on")
        print("  automatically when the base has one.")
    return 0 if ok else 4


def main():
    ap = argparse.ArgumentParser(
        prog='vc64tool',
        description='Nintendo 64 Virtual Console WAD tool for the Wii (240p patch + rom injection)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  vc64tool.py info  "Majora.wad"
  vc64tool.py patch "Majora.wad" -o "Majora 240p.wad" --id NARZ
  vc64tool.py inject "Majora.wad" "DK64.z64" -o "DK64 240p.wad" --id DK64 --mode 240p
  vc64tool.py inject "Majora.wad" "DK64.z64" -o "DK64.wad"      --id DK64 --mode original
""")
    ap.add_argument('--version', action='version', version=f'vc64tool {__version__}')
    sub = ap.add_subparsers(dest='cmd', required=True)

    def common(p):
        p.add_argument('-k', '--key', default='common-key.bin',
                       help='Wii common key file (default: common-key.bin)')
        p.add_argument('--tv', default='NTSC', choices=list(TV_BASE),
                       help="TV format to report on (default: NTSC); the patch "
                            "covers every format unless --only-tv is given")
        p.add_argument('--only-tv', action='store_true',
                       help="patch only the --tv format, not all of them")

    p = sub.add_parser('info', help='analyse a VC WAD')
    p.add_argument('wad'); common(p); p.set_defaults(func=cmd_info)

    p = sub.add_parser('patch', help='apply the 240p patch to an existing N64 VC WAD')
    p.add_argument('wad')
    p.add_argument('-o', '--out', required=True)
    p.add_argument('--id', help='new 4-character channel id (omit to replace the original)')
    common(p); p.set_defaults(func=cmd_patch)

    p = sub.add_parser('findbase', help='scan a folder of WADs and rank them as bases for a rom')
    p.add_argument('rom'); p.add_argument('folder')
    common(p); p.set_defaults(func=cmd_findbase)

    p = sub.add_parser('inject', help='build a channel from a base WAD + an N64 rom')
    p.add_argument('base'); p.add_argument('rom')
    p.add_argument('-o', '--out', required=True)
    p.add_argument('--id', help='new 4-character channel id (omit to replace the base)')
    p.add_argument('--mode', default='240p', choices=['240p', 'original'],
                   help='video output of the result (default: 240p)')
    common(p); p.set_defaults(func=cmd_inject)

    a = ap.parse_args()
    return a.func(a)


if __name__ == '__main__':
    sys.exit(main())
