"""Mission DLL -> readable Lua mission (stage 2 of the mission translation).

Stage 1 (``mission2lua.py``) recompiles every x86 instruction into Lua and keeps the DLL's memory: exact by
construction, unreadable. Stage 2 (this module) decompiles the *mission code* of the same DLL into idiomatic Lua that a
modder can read and edit, Creation-Kit style:

* the script API as named calls: ``api.add_objective(...)``, ``api.queue_call(cb, a, b, delay)`` (a generated ``api``
  table of wrappers, names and argument names from the ModKit's ``editor/games/ds1/api_slots.json``);
* one Lua function per DLL function, structured control flow (``if`` / ``elseif`` / ``while`` / ``repeat``), ``goto``
  only where the structuring fails (counted);
* the DLL's globals as named Lua variables (``mission_phase``, ``objective_1`` ...; the address stays in a comment);
* message handlers as a dispatch table keyed by message name (``on[MSG.KILLED] = function(msg) ... end``);
* ``queue_call`` callbacks as Lua functions (``api.queue_call(show_camera, 3, 0, 2000.0)``);
* the save block reproduced byte for byte: the DLL's ``save`` / ``load`` copy a range of its globals, the generated
  ``save`` / ``load`` pack / unpack the named variables in the same layout (old saves keep loading).

No x86 is emulated in the output. A small *bridge* (the prelude) connects the Lua to the game the way a DLL is
connected: it uses DesertStormFix's mission runtime (``x86rt``, src/features/missionrt.cpp) only to get a block of
real memory laid out like the DLL image (so string literals, the script object and callback pointers handed to the
game are real pointers at the DLL's own offsets - the API trace of both paths compares 1:1), to call API slots
natively and to receive the game's calls (script object callbacks, queued callbacks). Memory the DLL touched through
pointers (game objects, out-parameter buffers, DLL tables walked by pointer) goes through the ``mem`` helpers and is
counted in the statistics as raw memory use.

Method (per function): own CFG (tail jumps become tail calls), static ``esp`` / x87-depth analysis, lifting every
instruction to statements over *locations* (registers, stack slots, x87 slots) with symbolic expressions, reaching
definitions, propagation of single-definition pure values and single-use expressions (respecting the order of side
effects), dead-code removal, variable recovery (one Lua local per def-use web), structuring by post-dominators and
natural loops, and a few pattern passes (switch -> dispatch table, block copies of the global range -> save / load,
``sprintf`` -> ``string.format``). Exactness is checked against stage 1 / the DLL with the API trace (see
docs/sdk/mission-translation.md in the patch repository).

Generated output is game code: write it only to gitignored folders (``generated_missions/``), never commit / share.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import struct
from collections import defaultdict

from capstone import x86

from . import missionflow as mf
from . import mission2lua as m2l

VERSION = 1
M32 = 0xFFFFFFFF
API_TABLE = 0x5ED238
QUEUE_CALL = 0x518
HERE = os.path.dirname(os.path.abspath(__file__))
SLOTS_JSON = os.path.join(HERE, "..", "editor", "games", "ds1", "api_slots.json")

# x86 condition codes
_JCC = {"jo": "o", "jno": "no", "jb": "b", "jae": "ae", "je": "e", "jne": "ne", "jbe": "be", "ja": "a", "js": "s",
        "jns": "ns", "jp": "p", "jnp": "np", "jl": "l", "jge": "ge", "jle": "le", "jg": "g"}
_SET = {"set" + k[1:]: v for k, v in _JCC.items()}
_INV = {"o": "no", "no": "o", "b": "ae", "ae": "b", "e": "ne", "ne": "e", "be": "a", "a": "be", "s": "ns", "ns": "s",
        "p": "np", "np": "p", "l": "ge", "ge": "l", "le": "g", "g": "le"}
SPRINTF_SIG = bytes.fromhex("558bec83ec208b450856")      # MSVC 6 sprintf prologue (calls _output)
SPRINTF_SIG2 = bytes.fromhex("558bec83ec20568b7508578d4510")   # MSVC 7 (Desert Storm II's DLLs)
# MSVC 6 rand(): call _getptd; mov ecx, [eax+0x14]; imul ecx, ecx, 0x343FD (holdrand in the CRT's per-thread data -
# the multiplayer missions call it; never srand, so it starts at 1 every time the DLL is loaded)
RAND_SIG_AT1 = bytes.fromhex("8b481469c9fd430300")
# __security_check_cookie (/GS, Desert Storm II's DLLs): cmp ecx, [cookie]; jne +1; ret - checks the frame's copy of
# the cookie and only returns (else reports a stack overrun): a call to it is left out
COOKIE_SIG = (bytes.fromhex("3b0d"), bytes.fromhex("7501c3"))

_R32 = {}
_SUB = {}            # capstone reg -> (32-bit name, shift, mask)
for _n in ("eax", "ebx", "ecx", "edx", "esi", "edi", "ebp", "esp"):
    _R32[getattr(x86, "X86_REG_" + _n.upper())] = _n
    _SUB[getattr(x86, "X86_REG_" + _n.upper())] = (_n, 0, M32)
for _n, _p in (("ax", "eax"), ("bx", "ebx"), ("cx", "ecx"), ("dx", "edx"), ("si", "esi"), ("di", "edi"),
               ("bp", "ebp"), ("sp", "esp")):
    _SUB[getattr(x86, "X86_REG_" + _n.upper())] = (_p, 0, 0xFFFF)
for _n, _p in (("al", "eax"), ("bl", "ebx"), ("cl", "ecx"), ("dl", "edx")):
    _SUB[getattr(x86, "X86_REG_" + _n.upper())] = (_p, 0, 0xFF)
for _n, _p in (("ah", "eax"), ("bh", "ebx"), ("ch", "ecx"), ("dh", "edx")):
    _SUB[getattr(x86, "X86_REG_" + _n.upper())] = (_p, 8, 0xFF)


class Unsupported(Exception):
    pass


def s32(v):
    v &= M32
    return v - (1 << 32) if v & 0x80000000 else v


def sx(v, size):
    bits = size * 8
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


def load_slots(path=SLOTS_JSON):
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except OSError:
        return {}
    return {int(k, 16): v for k, v in raw.items()}


# ============================================================================================ expressions ===========
# Expressions are tuples (hashable, compared structurally):
#   ("c", v)                 integer constant (canonical: signed 32-bit for dwords)
#   ("fc", v)                float constant
#   ("param", i)             the function's i-th dword argument (its value on entry)
#   ("loc", name)            a location (register / stack slot / x87 slot) - resolved away or a Lua local
#   ("var", name)            a recovered Lua local
#   ("g", addr, size, kind)  a DLL global (kind "i" signed / "u" unsigned / "f" float)
#   ("gaddr", addr)          the address of DLL data (string literal, table, memory-backed global)
#   ("saddr", off)           the address of a stack slot (a stack buffer)
#   ("api",)                 the script API table pointer
#   ("mem", addr, size, kind) memory through a pointer
#   ("bin", op, a, b)        + - * & | ~ << shr sar idiv imod
#   ("un", op, a)            neg not
#   ("call", target, args, id, ret)   target: ("slot", n) | ("fn", va) | ("sprintf",) | ("ptr", expr); ret "i"/"f"
#   ("cmp", rel, a, b)       rel in == ~= < <= > >= u< u<= u> u>= f< f<= f> f>= f== f~=  (boolean)
#   ("not", c) ("and", a, b) ("or", a, b)  booleans
#   ("b2i", c)               boolean -> 1 / 0
#   ("i2f", a) ("u2f", a) ("fr", a)   int -> float, unsigned -> float, x87 precision rounding
#   ("fbits", a)             float -> its 32-bit pattern as integer   ("fromfbits", a) reverse
#   ("fsw", a, b)            x87 status word after comparing a with b
#   ("ins", x, v, shift, mask)  x with bits (mask << shift) replaced by v
#   ("ext", x, shift, mask)  (x >> shift) & mask
#   ("undef", what)          a value the code never defined (callee-clobbered register ...)
#   ("msg", i, kind)         message parameter i (message callback)  ("evt", off)  queued-callback event field
#   ("str", s)               a Lua string (sprintf result)
C0 = ("c", 0)
C1 = ("c", 1)


def C(v):
    return ("c", s32(v))


def kids(e):
    """Sub-expressions of e (a call's argument tuple flattened)."""
    if e[0] == "call":
        return (e[1],) + tuple(e[2])
    return tuple(x for x in e[1:] if isinstance(x, tuple))


def is_c(e):
    return e[0] == "c"


def is_bool(e):
    return e[0] in ("cmp", "not", "and", "or")


def has_call(e):
    if not isinstance(e, tuple):
        return False
    if e and e[0] == "call":
        return True
    return any(has_call(x) for x in kids(e))


def reads_memory(e):
    """True when the value depends on DLL globals or memory (may change at any side effect)."""
    if not isinstance(e, tuple) or not e:
        return False
    if e[0] in ("g", "mem", "msg", "evt", "call"):
        return True
    return any(reads_memory(x) for x in kids(e))


def locs_in(e, out=None):
    out = set() if out is None else out
    if isinstance(e, tuple) and e:
        if e[0] == "loc":
            out.add(e[1])
        else:
            for x in kids(e):
                locs_in(x, out)
    return out


def subst(e, m):
    """Replace ("loc", n) by m[n] where present."""
    if not isinstance(e, tuple) or not e:
        return e
    if e[0] == "loc":
        return m.get(e[1], e)
    if e[0] == "call":
        return ("call", subst(e[1], m), tuple(subst(a, m) for a in e[2]), e[3], e[4])
    return (e[0],) + tuple(subst(x, m) if isinstance(x, tuple) else x for x in e[1:])


def calls_in(e, out=None):
    out = [] if out is None else out
    if isinstance(e, tuple) and e:
        if e[0] == "call":
            # Lua evaluates the callee's arguments left to right, then calls
            calls_in(e[1], out)
            for a in e[2]:
                calls_in(a, out)
            out.append(e[3])
        else:
            for x in e[1:]:
                if isinstance(x, tuple):
                    calls_in(x, out)
    return out


def is_float(e):
    k = e[0]
    if k in ("fc", "i2f", "u2f", "fr", "fromfbits"):
        return True
    if k in ("g", "mem"):
        return e[3] == "f"
    if k == "msg":
        return e[2] == "f"
    if k == "call":
        return e[4] == "f"
    if k == "bin" and e[1] in ("+", "-", "*", "/"):
        return is_float(e[2]) or is_float(e[3])
    if k == "un" and e[1] == "neg":
        return is_float(e[2])
    return False


def simp(e):
    """Algebraic simplification (exact on canonical 32-bit integers)."""
    if not isinstance(e, tuple) or not e:
        return e
    k = e[0]
    if k == "call":
        return ("call", simp(e[1]), tuple(simp(a) for a in e[2]), e[3], e[4])
    if k in ("c", "fc", "param", "loc", "var", "g", "gaddr", "saddr", "api", "undef", "msg", "evt", "str"):
        return e
    e = (k,) + tuple(simp(x) if isinstance(x, tuple) else x for x in e[1:])
    if k == "bin":
        op, a, b = e[1], e[2], e[3]
        if is_c(a) and is_c(b) and op in ("+", "-", "*", "&", "|", "~", "<<", "shr", "sar"):
            x, y = a[1], b[1]
            r = {"+": lambda: x + y, "-": lambda: x - y, "*": lambda: x * y, "&": lambda: x & y,
                 "|": lambda: x | y, "~": lambda: x ^ y, "<<": lambda: x << y,
                 "shr": lambda: (x & M32) >> y, "sar": lambda: x >> y}[op]()
            return C(r)
        if op in ("+", "-") and is_c(b) and b[1] == 0:
            return a
        if op == "+" and is_c(a) and a[1] == 0:
            return b
        if op in ("+", "-") and is_c(b) and a[0] == "bin" and a[1] in ("+", "-") and is_c(a[3]):
            v = (a[3][1] if a[1] == "+" else -a[3][1]) + (b[1] if op == "+" else -b[1])
            return simp(("bin", "+" if v >= 0 else "-", a[2], C(abs(v))))
        if op == "+" and is_c(b) and b[1] < 0 and b[1] != -0x80000000:
            return ("bin", "-", a, C(-b[1]))
        if op == "-" and is_c(b) and b[1] < 0 and b[1] != -0x80000000:
            return ("bin", "+", a, C(-b[1]))
        if op == "+" and a[0] == "gaddr" and is_c(b):
            return ("gaddr", a[1] + b[1])
        if op == "-" and a[0] == "gaddr" and is_c(b):
            return ("gaddr", a[1] - b[1])
        if op == "+" and a[0] == "saddr" and is_c(b):
            return ("saddr", a[1] + b[1])
        if op == "-" and a[0] == "saddr" and is_c(b):
            return ("saddr", a[1] - b[1])
        if op == "&" and is_c(b) and a[0] == "bin" and a[1] == "&" and is_c(a[3]):
            return simp(("bin", "&", a[2], C(a[3][1] & b[1])))
        if op == "&" and is_c(b) and a[0] == "ins" and (b[1] & ~(a[4] << a[3])) == 0:
            v = simp(("bin", "&", a[2], C(a[4])))
            if a[3]:
                v = simp(("bin", "<<", v, C(a[3])))
            return simp(("bin", "&", v, b))
        if op == "&" and is_c(b) and b[1] == -1:
            return a
        if op == "&" and is_c(b):
            r = vrange(a)
            if r and r[0] >= 0 and r[1] <= b[1] and (b[1] & (b[1] + 1)) == 0:
                return a                 # mask wider than the value
        if op == "&" and is_c(b) and b[1] == 0:
            return C0
        if op in ("|", "~") and is_c(b) and b[1] == 0:
            return a
        if op == "|" and is_c(b) and b[1] == -1:
            return C(-1)                       # `or reg, -1`: -1 whatever reg held
        if op == "~" and a == b:
            return C0
        if op == "*" and is_c(b) and b[1] == 1:
            return a
        return e
    if k == "ext":
        x, sh, mask = e[1], e[2], e[3]
        if is_c(x):
            return C((x[1] >> sh) & mask)
        if x[0] == "ins":
            ix, iv, ish, imask = x[1], x[2], x[3], x[4]
            if ish == sh and imask == mask:
                return simp(("ext", iv, 0, mask))
            if (mask << sh) & (imask << ish) == 0:
                return simp(("ext", ix, sh, mask))
        if sh == 0 and mask == M32:
            return x
        if sh == 0:
            r = vrange(x)
            if r and r[0] >= 0 and r[1] <= mask:
                return x
            return simp(("bin", "&", x, C(mask)))
        return e
    if k == "ins":
        x, v, sh, mask = e[1], e[2], e[3], e[4]
        if sh == 0 and mask == M32:
            return v
        if x == C0 or x[0] == "undef":
            r = vrange(v)
            if sh == 0 and r and r[0] >= 0 and r[1] <= mask:
                return v
            if x == C0:
                return simp(("bin", "<<", ("bin", "&", v, C(mask)), C(sh))) if sh else simp(("bin", "&", v, C(mask)))
        if is_c(x) and is_c(v):
            return C((x[1] & ~(mask << sh)) | ((v[1] & mask) << sh))
        # x with ah := (ah op c) - keep as is; renderer prints a masked form
        if v[0] == "bin" and v[1] == "&" and is_c(v[3]) and v[2] == ("ext", x, sh, mask):
            # x & ~((mask & ~c) << sh)
            clear = (mask & ~v[3][1]) << sh
            return simp(("bin", "&", x, C(~clear)))
        if v[0] == "bin" and v[1] == "|" and is_c(v[3]) and v[2] == ("ext", x, sh, mask):
            return simp(("bin", "|", x, C((v[3][1] & mask) << sh)))
        return e
    if k == "cmp":
        rel, a, b = e[1], e[2], e[3]
        if a[0] == "bin" and a[1] in ("+", "-") and is_c(a[3]) and is_c(b) and rel in ("==", "~="):
            v = b[1] + a[3][1] if a[1] == "-" else b[1] - a[3][1]
            return ("cmp", rel, a[2], C(v))
        if is_c(a) and is_c(b):
            x, y = a[1], b[1]
            ux, uy = x & M32, y & M32
            r = {"==": x == y, "~=": x != y, "<": x < y, "<=": x <= y, ">": x > y, ">=": x >= y,
                 "u<": ux < uy, "u<=": ux <= uy, "u>": ux > uy, "u>=": ux >= uy}.get(rel)
            if r is not None:
                return ("cmp", "==", C0, C0) if r else ("cmp", "~=", C0, C0)
        if rel.startswith("u"):
            ra, rb = vrange(a), vrange(b)
            if ra and rb and ra[0] >= 0 and rb[0] >= 0:
                return ("cmp", rel[1:], a, b)
        if rel in ("<", ">=") and is_c(b) and b[1] == 0 and a[0] == "sx" and a[1][0] == "ext" and a[1][3] == 0xFF                 and a[2] == 1:
            x, sh = a[1][1], a[1][2]
            return ("cmp", "~=" if rel == "<" else "==", ("bin", "&", x, C(0x80 << sh)), C0)
        if rel in ("<", ">=") and is_c(b) and b[1] == 0 and a[0] == "sx" and a[2] in (1, 2):
            return ("cmp", "~=" if rel == "<" else "==", simp(("bin", "&", a[1], C(0x80 if a[2] == 1 else 0x8000))), C0)
        if rel in ("==", "~=") and a[0] == "b2i" and is_c(b) and b[1] == 0:
            return a[1] if rel == "~=" else simp(("not", a[1]))
        return e
    if k == "un" and e[2][0] == "un" and e[2][1] == e[1]:
        return e[2][2]                     # -(-x), ~(~x)
    if k == "not":
        c = e[1]
        if c[0] == "not":
            return c[1]
        if c[0] == "cmp":
            inv = {"==": "~=", "~=": "==", "<": ">=", ">=": "<", ">": "<=", "<=": ">", "u<": "u>=", "u>=": "u<",
                   "u>": "u<=", "u<=": "u>"}
            if c[1] in inv:
                return ("cmp", inv[c[1]], c[2], c[3])
            finv = {"f==": "f~=", "f~=": "f=="}
            if c[1] in finv:
                return ("cmp", finv[c[1]], c[2], c[3])
        if c[0] == "and":
            return ("or", simp(("not", c[1])), simp(("not", c[2])))
        if c[0] == "or":
            return ("and", simp(("not", c[1])), simp(("not", c[2])))
        return e
    return e


def vrange(e):
    """(lo, hi) of an integer expression when known."""
    if e[0] == "t":
        e = e[2]
    k = e[0]
    if k == "c":
        return (e[1], e[1])
    if k == "g" and e[3] == "u":
        return (0, (1 << (8 * e[2])) - 1)
    if k == "mem" and e[3] == "u":
        return (0, (1 << (8 * e[2])) - 1)
    if k == "b2i":
        return (0, 1)
    if k == "bin" and e[1] == "&":
        rb = vrange(e[3])
        if rb and rb[0] >= 0:
            return (0, rb[1])
    if k == "ext":
        return (0, e[3])
    if k == "bin" and e[1] == "shr" and is_c(e[3]) and e[3][1] > 0:
        return (0, M32 >> e[3][1])
    return None


# ============================================================================================ CFG ===================
class Block:
    __slots__ = ("start", "insns", "succ", "kind", "table", "preds", "tail")

    def __init__(self, start):
        self.start, self.insns, self.succ, self.kind, self.table, self.preds, self.tail = start, [], [], "fall", None, [], None


class Func:
    def __init__(self, va):
        self.va = va
        self.blocks: dict[int, Block] = {}
        self.ret_pop = 0
        self.esp_at = {}         # insn address -> esp offset before it (entry: 0 = return address)
        self.fpd_at = {}         # insn address -> x87 depth before it
        self.ebp_at = {}         # insn address -> esp offset ebp holds (frame) or None
        self.float_call = set()  # call insn addresses that return a float in st0
        self.nparams = 0
        self.name = None
        self.buffers = []
        self.kind = None         # update / message / save / load / size / init / setup / queue / plain
        self.returns = False


class Program:
    """The mission code of one DLL: functions reachable from LevelSetup, the script object's callbacks and every
    function pointer the mission code hands out (queue_call callbacks), the CRT left out (``sprintf`` recognised)."""

    def __init__(self, path_or_bytes, name=None):
        self.p = m2l.Program(path_or_bytes, name)
        self.dll = d = self.p.dll
        self.name = self.p.name
        self.base = d.base
        self.obj, self.cbs, self.api_global = mf._script_object(d)
        self.setup = self.p.exports["LevelSetup"]
        self.sections = [(d.base + s.VirtualAddress, d.base + s.VirtualAddress + max(s.Misc_VirtualSize, s.SizeOfRawData),
                          s.Name.rstrip(b"\0").decode(errors="replace")) for s in d.pe.sections]
        self.rdata = [(lo, hi) for lo, hi, n in self.sections if n == ".rdata"]
        self.data = [(lo, hi) for lo, hi, n in self.sections if n in (".data", ".bss")]
        self.sprintf = set()
        self.rand = set()
        self.cookie = set()
        self.crt = set()
        self.entries = set()
        self.funcs: dict[int, Func] = {}
        self.queue_cbs = set()      # function pointers handed to the game by mission code
        self._discover()

    # --- sections ---
    def in_rdata(self, a):
        return any(lo <= a < hi for lo, hi in self.rdata)

    def in_data(self, a):
        return any(lo <= a < hi for lo, hi in self.data)

    def in_image(self, a):
        return self.base <= a < self.base + self.p.size

    def u8(self, a):
        return self.dll.u8(a)

    def u32(self, a):
        return self.dll.u32(a)

    def bytes_at(self, a, n):
        o = a - self.base
        return bytes(self.dll.img[o:o + n])

    # --- discovery ---
    def _discover(self):
        d = self.dll
        known = set(self.p.functions)
        # CRT: everything reachable from the DLL entry point (_DllMainCRTStartup) + sprintf (by signature)
        crt, todo = set(), [self.p.entry]
        while todo:
            v = todo.pop()
            if v in crt or v not in self.p.functions:
                continue
            crt.add(v)
            todo += self._callees_raw(self.p.functions[v])
        for v in known:
            if self.bytes_at(v, len(SPRINTF_SIG)) == SPRINTF_SIG or self.bytes_at(v, len(SPRINTF_SIG2)) == SPRINTF_SIG2:
                self.sprintf.add(v)
            if self.bytes_at(v, 1) == bytes([0xE8]) and self.bytes_at(v + 5, len(RAND_SIG_AT1)) == RAND_SIG_AT1:
                self.rand.add(v)
            if self.bytes_at(v, 2) == COOKIE_SIG[0] and self.bytes_at(v + 6, 3) == COOKIE_SIG[1]:
                self.cookie.add(v)
        self.crt = crt
        roots = [self.setup] + list(self.cbs)
        self.entries = set(roots)
        todo = list(roots)
        while todo:
            v = todo.pop()
            if v in self.funcs or v in self.sprintf or v in self.rand or v in self.cookie:
                continue
            if v in crt:
                raise Unsupported("mission code calls CRT function %X" % v)
            f = self._build(v)
            self.funcs[v] = f
            for c in sorted(self._callees(f)):     # (sorted: the order mustn't depend on Python's set layout)
                if c not in self.funcs:
                    self.entries.add(c)
                    todo.append(c)
        # function ranges may have changed after new entries: rebuild once more until stable
        for _ in range(4):
            changed = False
            for v in list(self.funcs):
                nf = self._build(v)
                if set(nf.blocks) != set(self.funcs[v].blocks):
                    self.funcs[v] = nf
                    changed = True
                for c in sorted(self._callees(nf)):
                    if c not in self.funcs and c not in self.sprintf and c not in self.rand and c not in self.cookie:
                        self.entries.add(c)
                        self.funcs[c] = self._build(c)
                        changed = True
            if not changed:
                break

    def _callees_raw(self, f):
        d = self.dll
        out = set()
        for b in f.blocks.values():
            for i in b.insns:
                if i.mnemonic in ("call", "jmp") and i.operands and i.operands[0].type == x86.X86_OP_IMM:
                    t = i.operands[0].imm & M32
                    if i.mnemonic == "call" or t in self.p.functions:
                        out.add(t)
                for op in i.operands:
                    if op.type == x86.X86_OP_IMM and i.imm_offset and (i.address + i.imm_offset) in self.p.relocs \
                            and d.in_text(op.imm & M32):
                        out.add(op.imm & M32)
        return out

    def _callees(self, f):
        d = self.dll
        out = set()
        for b in f.blocks.values():
            if b.kind == "tail" and b.tail is not None:
                out.add(b.tail)
            for i in b.insns:
                if i.mnemonic == "call" and i.operands[0].type == x86.X86_OP_IMM:
                    out.add(i.operands[0].imm & M32)
                for op in i.operands:
                    if op.type == x86.X86_OP_IMM and i.imm_offset and (i.address + i.imm_offset) in self.p.relocs \
                            and d.in_text(op.imm & M32):
                        out.add(op.imm & M32)
                        self.queue_cbs.add(op.imm & M32)
        return out

    def _limit(self, va):
        """End of the function starting at va: the next known entry (call targets, taken pointers) after it."""
        nxt = [e for e in (set(self.p.functions) | self.entries) if e > va]
        return min(nxt) if nxt else self.dll.text_hi

    def _build(self, va):
        d = self.dll
        f = Func(va)
        lim = self._limit(va)
        others = (set(self.p.functions) | self.entries) - {va}

        def outside(t):
            return t in others or not (va <= t < lim)

        starts, todo, seen = {va}, [va], set()
        while todo:
            a = todo.pop()
            while a not in seen:
                seen.add(a)
                i = d.insn(a)
                if i is None:
                    break
                m = i.mnemonic
                if m in ("ret", "retn"):
                    if i.operands:
                        f.ret_pop = i.operands[0].imm
                    break
                if m == "jmp":
                    op = i.operands[0]
                    if op.type == x86.X86_OP_IMM:
                        t = op.imm & M32
                        if not outside(t):
                            starts.add(t)
                            todo.append(t)
                    else:
                        for t in _table_targets(d, i, va, lim)[0]:
                            starts.add(t)
                            todo.append(t)
                    break
                if m in _JCC:
                    t = i.operands[0].imm & M32
                    starts.update((t, i.address + i.size))
                    todo += [t, i.address + i.size]
                    break
                if m in ("int3", "hlt"):
                    break
                a = i.address + i.size
        for s in sorted(starts):
            b = Block(s)
            a = s
            while True:
                i = d.insn(a)
                if i is None:
                    b.kind = "end"
                    break
                b.insns.append(i)
                m = i.mnemonic
                nxt = i.address + i.size
                if m in ("ret", "retn"):
                    b.kind = "ret"
                    break
                if m == "jmp":
                    op = i.operands[0]
                    if op.type == x86.X86_OP_IMM:
                        t = op.imm & M32
                        if outside(t):
                            b.kind, b.tail = "tail", t
                        else:
                            b.kind, b.succ = "jmp", [t]
                    elif op.type == x86.X86_OP_MEM and op.mem.index == 0:
                        b.kind = "tailmem"
                    else:
                        tg, tab = _table_targets(d, i, va, lim)
                        if not tg:
                            raise Unsupported("indirect jump at %X" % i.address)
                        b.kind, b.succ, b.table = "switch", tg, tab
                    break
                if m in _JCC:
                    b.kind, b.succ = "cond", [i.operands[0].imm & M32, nxt]
                    break
                if m in ("int3", "hlt"):
                    b.kind = "end"
                    break
                if nxt in starts:
                    b.kind, b.succ = "fall", [nxt]
                    break
                a = nxt
            f.blocks[s] = b
        for b in f.blocks.values():
            b.succ = [t for t in b.succ if t in f.blocks]
            for t in b.succ:
                f.blocks[t].preds.append(b.start)
        return f


def _table_targets(d, i, lo=None, hi=None):
    fn = mf.Function.__new__(mf.Function)
    fn.dll = d
    tg = mf.Function._table_targets(fn, i)
    if tg or lo is None:
        return tg, getattr(fn, "_last_table", None)
    # `cmp reg, other_reg; ja default; jmp [reg*4 + table]`: no immediate bound - the table is the run of dwords that
    # point into this function
    op = i.operands[0]
    if op.type != x86.X86_OP_MEM or op.mem.base or not op.mem.index or op.mem.scale != 4:
        return [], None
    table = op.mem.disp & M32
    vals = {}
    k = 0
    while k < 256:
        t = d.u32(table + 4 * k)
        if t is None or not (lo <= t < hi):
            break
        vals[k] = t
        k += 1
    if not vals:
        return [], None
    return sorted(set(vals.values())), {"values": vals, "index_reg": _R32.get(op.mem.index), "byte_table": None}


# ============================================================================================ lifting ===============
class Stmt:
    """One IR statement. kind: set (loc = expr), store (memory target = expr), expr (side effect only), copy (block
    copy), cond / switch / ret / tail (block terminators)."""
    __slots__ = ("kind", "loc", "target", "e", "at", "extra")

    def __init__(self, kind, at, loc=None, target=None, e=None, extra=None):
        self.kind, self.at, self.loc, self.target, self.e, self.extra = kind, at, loc, target, e, extra

    def __repr__(self):
        return "<%s %s %s %s>" % (self.kind, self.loc or self.target, self.e, self.extra or "")


class Lifter:
    """x86 -> IR statements for one function (esp and x87 depth resolved statically)."""

    def __init__(self, prog: "Translator", f: Func):
        self.t, self.p, self.f = prog, prog.prog, f
        self.call_id = 0
        self.regions = []        # (lo, hi) stack ranges that are memory (buffers handed to the game)
        self.api_slots_in = set()  # argument slots that hold the API table (helpers getting `api` passed)
        self.escaped = set()

    # --- static esp / x87 depth ---
    def frame(self):
        f, t = self.f, self.t
        order = mf._rpo(_ShimF(f))
        state = {f.va: (0, 0, None)}
        for va in order:
            if va not in state:
                continue
            esp, fpd, ebp = state[va]
            b = f.blocks[va]
            for k, i in enumerate(b.insns):
                f.esp_at[i.address], f.fpd_at[i.address], f.ebp_at[i.address] = esp, fpd, ebp
                esp, fpd, ebp = self._step(i, esp, fpd, ebp, b.insns[k + 1:])
            for s in b.succ:
                if s in state:
                    if state[s][:2] != (esp, fpd):
                        raise Unsupported("stack mismatch at %X: %s vs %s" % (s, state[s], (esp, fpd)))
                else:
                    state[s] = (esp, fpd, ebp)

    def _step(self, i, esp, fpd, ebp, rest):
        m, ops = i.mnemonic, i.operands
        if m == "push":
            return esp - 4, fpd, ebp
        if m == "pop":
            if ops[0].type == x86.X86_OP_REG and _R32.get(ops[0].reg) == "ebp":
                ebp = None
            return esp + 4, fpd, ebp
        if m in ("sub", "add") and ops[0].type == x86.X86_OP_REG and _R32.get(ops[0].reg) == "esp":
            if ops[1].type != x86.X86_OP_IMM:
                raise Unsupported("esp arithmetic at %X" % i.address)
            v = s32(ops[1].imm)
            return (esp - v if m == "sub" else esp + v), fpd, ebp
        if m == "mov" and ops[0].type == x86.X86_OP_REG and ops[1].type == x86.X86_OP_REG:
            if _R32.get(ops[0].reg) == "ebp" and _R32.get(ops[1].reg) == "esp":
                return esp, fpd, esp
            if _R32.get(ops[0].reg) == "esp" and _R32.get(ops[1].reg) == "ebp":
                return ebp, fpd, ebp
        if m == "leave":
            return ebp + 4, fpd, None
        if m == "call":
            pop = 0
            op = ops[0]
            if op.type == x86.X86_OP_IMM:
                tva = op.imm & M32
                if tva in self.p.funcs:
                    pop = self.p.funcs[tva].ret_pop
            # does the call return a float? (the next x87 instruction reads st0 without pushing first)
            if self._returns_float(rest, fpd):
                self.f.float_call.add(i.address)
                fpd += 1
            return esp + 4 - 4 + pop, fpd, ebp
        if m.startswith("f"):
            return esp, fpd + _fp_delta(i), ebp
        return esp, fpd, ebp

    def _returns_float(self, rest, fpd):
        for j in rest:
            mm = j.mnemonic
            if mm == "call" or mm in ("ret", "jmp") or mm in _JCC:
                return False
            if mm.startswith("f") and mm not in ("fnstsw", "fnstcw", "fldcw", "fnclex", "wait"):
                if mm in ("fld", "fild", "fldz", "fld1"):
                    return False
                return fpd == 0
        return False

    # --- operands ---
    def _stack(self, off):
        """A stack slot, or memory when the slot lies in a buffer whose address is handed out."""
        for lo, hi in self.regions:
            if lo <= off < hi:
                return ("mem", simp(("bin", "+", ("saddr", lo), C(off - lo))))
        return ("stack", off)

    def escape(self, v, st=None):
        if st and isinstance(v, tuple) and v and v[0] == "loc" and v[1] in st:
            v = st[v[1]]
        if isinstance(v, tuple) and _has_kind(v, "saddr"):
            for x in _find_kind(v, "saddr"):
                self.escaped.add(x[1])

    def mem_target(self, i, op, st):
        """Classify a memory operand -> ("stack", off) | ("g", addr) | ("api",) | ("apislot", n) | ("mem", addr_expr)."""
        m = op.mem
        disp = s32(m.disp)
        base = _R32.get(m.base) if m.base else None
        index = _R32.get(m.index) if m.index else None
        if op.mem.segment not in (0, x86.X86_REG_DS, x86.X86_REG_SS, x86.X86_REG_ES):
            raise Unsupported("segment operand at %X" % i.address)
        if base == "esp" and index is None:
            return self._stack(self.f.esp_at[i.address] + disp)
        if base == "ebp" and index is None and self.f.ebp_at[i.address] is not None:
            return self._stack(self.f.ebp_at[i.address] + disp)
        if base in ("esp", "ebp") and index is not None and (base == "esp" or self.f.ebp_at[i.address] is not None):
            # a stack array indexed by a register: a buffer (its slots are memory)
            off = (self.f.esp_at[i.address] if base == "esp" else self.f.ebp_at[i.address]) + disp
            self.escaped.add(off)
            iv = ("loc", index) if m.scale == 1 else ("bin", "*", ("loc", index), C(m.scale))
            return ("mem", simp(("bin", "+", ("saddr", off), iv)))
        if base is None and index is None:
            a = disp & M32
            if a == self.p.api_global:
                return ("apiglobal",)
            return ("g", a)
        # base / index registers with symbolic values
        bv = st.get(base) if base else None
        if bv is not None and index is None:
            if bv == ("api",):
                return ("apislot", disp)
            if bv[0] == "apislotaddr" and disp == 0:
                return ("apislot", bv[1])
            if bv[0] == "saddr":
                return self._stack(bv[1] + disp)
            if bv[0] == "gaddr":
                return ("g", (bv[1] + disp) & M32)
        addr = None
        parts = []
        if base:
            parts.append(("loc", base))
        if index:
            iv = ("loc", index)
            parts.append(iv if m.scale == 1 else ("bin", "*", iv, C(m.scale)))
        addr = parts[0]
        for q in parts[1:]:
            addr = ("bin", "+", addr, q)
        if disp:
            relocated = i.disp_offset and (i.address + i.disp_offset) in self.p.p.relocs
            addr = ("bin", "+", addr, ("gaddr", disp & M32) if relocated or (
                self.p.in_image(disp & M32) and not base and not index) else C(disp))
        return ("mem", addr)

    def run(self):
        """-> {block: [Stmt]} (statements per block) with locations; terminators last."""
        self.frame()
        out = {}
        for va, b in self.f.blocks.items():
            out[va] = self.lift_block(b)
        return out

    def lift_block(self, b, flags=None, st=None):
        out = []
        st = dict(st or {})   # register -> known pure value (for addressing), from the predecessors
        # flags = ("sub"|"add"|"res"|"neg"|"sahf", operands) - entry flags from the predecessors
        f = self.f
        for k, i in enumerate(b.insns):
            m, ops = i.mnemonic, i.operands
            at = i.address
            esp = f.esp_at[at]
            fpd = f.fpd_at[at]
            last = k == len(b.insns) - 1

            def R(op, size=None):
                return self.read(i, op, st, out)

            def W(op, val):
                self.write(i, op, val, st, out)

            mark = len(out)

            def SNAP(fl):   # flag operands are the instruction's inputs: keep them in temps (taken before it)
                vals = []
                for x in fl[1:]:
                    if isinstance(x, tuple) and x and not stable(x) and not _has_kind(x, "fsw"):
                        name = "fl%X_%d" % (at, len(vals))
                        out.insert(mark + len(vals), Stmt("set", at, loc=name, e=x))
                        vals.append(("loc", name))
                    else:
                        vals.append(x)
                return (fl[0],) + tuple(vals)

            def FSW(v):     # fnstsw ax; test ah, m / and eax, m: the flags come from the x87 compare
                return simp(subst(v, {"eax": st["eax_fsw"]})) if "eax_fsw" in st else v

            if m == "nop":
                continue
            if m == "push":
                v = R(ops[0])
                self.escape(v, st)
                if ops[0].size == 2:
                    raise Unsupported("push16 at %X" % at)
                out.append(Stmt("set", at, loc=_sl(esp - 4), e=v))
                continue
            if m == "pop":
                v = ("loc", _sl(esp))
                W(ops[0], v)
                continue
            if m in ("mov", "movzx", "movsx"):
                if ops[0].type == x86.X86_OP_REG and _R32.get(ops[0].reg) in ("esp", "ebp") or \
                        (ops[1].type == x86.X86_OP_REG and _R32.get(ops[1].reg) == "esp" and
                         ops[0].type == x86.X86_OP_REG and _R32.get(ops[0].reg) == "ebp"):
                    if _R32.get(ops[0].reg) == "ebp" and _R32.get(ops[1].reg) == "esp":
                        continue
                    if _R32.get(ops[0].reg) == "esp":
                        continue
                if ops[1].type == x86.X86_OP_REG and _R32.get(ops[1].reg) == "esp":
                    v = ("saddr", esp)
                else:
                    v = R(ops[1])
                if m == "movsx":
                    v = ("sx", v, ops[1].size) if not is_c(v) else C(sx(v[1], ops[1].size))
                    v = _sx_expr(v)
                if m == "movzx" or m == "movsx":
                    # full 32-bit destination
                    self.write(i, ops[0], v, st, out, full=True)
                else:
                    W(ops[0], v)
                continue
            if m == "lea":
                mt = self.mem_target(i, ops[1], st)
                if mt[0] == "stack":
                    v = ("saddr", mt[1])
                elif mt[0] == "g":
                    v = ("gaddr", mt[1])
                elif mt[0] == "apislot":
                    v = ("apislotaddr", mt[1])
                elif mt[0] == "mem":
                    v = mt[1]
                else:
                    raise Unsupported("lea at %X" % at)
                W(ops[0], v)
                continue
            if m == "xchg":
                a, c = R(ops[0]), R(ops[1])
                tmp = "t%X" % at
                out.append(Stmt("set", at, loc=tmp, e=a))
                W(ops[0], c)
                W(ops[1], ("loc", tmp))
                continue
            if m in ("add", "sub", "and", "or", "xor", "adc", "sbb"):
                d_reg = ops[0].type == x86.X86_OP_REG and _R32.get(ops[0].reg) == "esp"
                if d_reg:
                    continue          # esp arithmetic: frame analysis
                a, c = R(ops[0]), R(ops[1])
                size = ops[0].size
                if m == "xor" and ops[0].type == x86.X86_OP_REG and ops[1].type == x86.X86_OP_REG and ops[0].reg == ops[1].reg:
                    W(ops[0], C0)
                    flags = SNAP(("res", C0, size))
                    continue
                if m in ("adc", "sbb"):
                    cf = self.carry(flags)
                    if m == "sbb" and ops[0].type == x86.X86_OP_REG and ops[1].type == x86.X86_OP_REG and ops[0].reg == ops[1].reg:
                        r = ("un", "neg", ("b2i", cf))      # sbb r, r = -CF
                    else:
                        op = "+" if m == "adc" else "-"
                        r = ("bin", op, ("bin", op, a, c), ("b2i", cf))
                    W(ops[0], simp(r))
                    flags = None
                    continue
                if m == "and":
                    a = FSW(a)
                op = {"add": "+", "sub": "-", "and": "&", "or": "|", "xor": "~"}[m]
                if size < 4:
                    c = _canon_c(c, size)
                r = ("bin", op, a, c)
                if size < 4 and op in ("+", "-"):
                    r = ("bin", "&", r, C((1 << (8 * size)) - 1))
                W(ops[0], simp(r))
                if m in ("add", "sub"):
                    flags = SNAP(("sub" if m == "sub" else "add", a, c, size, simp(r)))
                else:
                    flags = SNAP(("res", simp(r), size))
                continue
            if m in ("inc", "dec"):
                a = R(ops[0])
                r = simp(("bin", "+" if m == "inc" else "-", a, C1))
                if ops[0].size < 4:
                    r = simp(("bin", "&", r, C((1 << (8 * ops[0].size)) - 1)))
                W(ops[0], r)
                flags = SNAP(("sub", a, C1 if m == "dec" else C(-1), ops[0].size, r) if m == "dec" else ("add", a, C1, ops[0].size, r))
                continue
            if m == "neg":
                a = R(ops[0])
                r = simp(("un", "neg", a))
                W(ops[0], r)
                flags = SNAP(("neg", a, ops[0].size, r))
                continue
            if m == "not":
                a = R(ops[0])
                W(ops[0], simp(("un", "not", a)))
                continue
            if m in ("shl", "sal", "shr", "sar"):
                a = R(ops[0])
                c = R(ops[1]) if len(ops) > 1 else C1
                op = {"shl": "<<", "sal": "<<", "shr": "shr", "sar": "sar"}[m]
                r = simp(("bin", op, a, c))
                W(ops[0], r)
                flags = SNAP(("res", r, ops[0].size))
                continue
            if m == "imul":
                if len(ops) == 3:
                    r = simp(("bin", "*", R(ops[1]), R(ops[2])))
                elif len(ops) == 2:
                    r = simp(("bin", "*", R(ops[0]), R(ops[1])))
                else:
                    raise Unsupported("one-operand imul at %X" % at)
                W(ops[0], r)
                flags = None
                continue
            if m == "cdq":
                out.append(Stmt("set", at, loc="edx", e=("bin", "sar", ("loc", "eax"), C(31))))
                continue
            if m == "mul":   # edx:eax = eax * op, unsigned (the compiler's division by a constant: x * magic >> 32)
                d_ = R(ops[0])
                num = ("loc", "eax")
                tmph, tmpl = "h%X" % at, "l%X" % at
                out.append(Stmt("set", at, loc=tmph, e=("bin", "umulhi", num, d_)))
                out.append(Stmt("set", at, loc=tmpl, e=simp(("bin", "*", num, d_))))
                out.append(Stmt("set", at, loc="eax", e=("loc", tmpl)))
                out.append(Stmt("set", at, loc="edx", e=("loc", tmph)))
                flags = None
                continue
            if m in ("idiv", "div"):
                d_ = R(ops[0])
                num = ("loc", "eax")
                q = ("bin", "idiv" if m == "idiv" else "udiv", num, d_)
                rm = ("bin", "imod" if m == "idiv" else "umod", num, d_)
                tmpq, tmpr = "q%X" % at, "r%X" % at
                out.append(Stmt("set", at, loc=tmpq, e=q))
                out.append(Stmt("set", at, loc=tmpr, e=rm))
                out.append(Stmt("set", at, loc="eax", e=("loc", tmpq)))
                out.append(Stmt("set", at, loc="edx", e=("loc", tmpr)))
                continue
            if m in ("cmp", "test"):
                a, c = FSW(R(ops[0])), R(ops[1])
                if ops[0].size < 4:
                    c = _canon_c(c, ops[0].size)
                if m == "cmp":
                    flags = SNAP(("sub", a, c, ops[0].size, None))
                else:
                    if ops[0].type == x86.X86_OP_REG and ops[1].type == x86.X86_OP_REG and ops[0].reg == ops[1].reg:
                        flags = SNAP(("res", a, ops[0].size))
                    else:
                        flags = SNAP(("res", simp(("bin", "&", a, c)), ops[0].size))
                continue
            if m in _SET:
                cnd = self.cond(_SET[m], flags, at)
                W(ops[0], simp(("b2i", cnd)))
                continue
            if m == "call":
                self.call(i, st, out, esp, fpd)
                flags = None
                op0 = i.operands[0]
                tva = op0.imm & M32 if op0.type == x86.X86_OP_IMM else None
                clob = self.t.clobbers.get(tva, {"eax", "ecx", "edx"}) | {"eax"}
                for r in ("eax", "ecx", "edx"):
                    if r in clob:
                        st.pop(r, None)
                continue
            if m in ("rep movsd", "rep movsb", "movsd", "movsb", "rep stosd", "rep stosb", "stosd", "stosb"):
                self.string_op(i, st, out)
                continue
            if m in ("ret", "retn"):
                out.append(Stmt("ret", at, e=("loc", "eax")))
                continue
            if m == "leave":
                continue
            if m == "jmp":
                op = ops[0]
                if b.kind == "tail":
                    self.tailcall(i, ("fn", b.tail), st, out, esp)
                elif b.kind == "tailmem":
                    mt = self.mem_target(i, op, st)
                    if mt[0] == "apislot":
                        self.tailcall(i, ("slot", mt[1]), st, out, esp)
                    else:
                        raise Unsupported("indirect tail jump at %X" % at)
                elif b.kind == "switch":
                    idx = ("loc", b.table["index_reg"])
                    out.append(Stmt("switch", at, e=idx, extra=b.table))
                continue
            if m in _JCC:
                out.append(Stmt("cond", at, e=self.cond(_JCC[m], flags, at)))
                continue
            if m == "sahf":
                if flags and flags[0] == "fswah":
                    flags = ("sahf", flags[1])
                elif flags is None or True:
                    # ah = fsw >> 8 from fnstsw ax
                    v = st.get("eax_fsw")
                    if v is None:
                        raise Unsupported("sahf without fnstsw at %X" % at)
                    flags = ("sahf", v)
                continue
            if m.startswith("f"):
                flags = self.fpu(i, st, out, fpd, flags)
                continue
            if m in ("cld", "std", "wait"):
                continue
            raise Unsupported("%s at %X" % (m, at))
        self.exit_flags = flags
        self.exit_st = st
        return out

    def lift_all(self):
        """Blocks in reverse post-order; a block that tests flags its predecessors set gets their flags."""
        f = self.f
        order = mf._rpo(_ShimF(f))
        ex, out, exs = {}, {}, {}
        self.pending_merge = []
        for va in order:
            b = f.blocks[va]
            fl = None
            pf = [ex.get(p, "?") for p in b.preds]
            if pf and all(x == pf[0] and x != "?" for x in pf):
                fl = pf[0]
            if fl is None and pf and all(x not in ("?", None) for x in pf) and len({(x[0], len(x)) for x in pf}) == 1 \
                    and len(set(pf)) > 1:
                kind = pf[0][0]
                merged = [kind]
                for k in range(1, len(pf[0])):
                    vals = [x[k] for x in pf]
                    if all(v == vals[0] for v in vals) or not isinstance(vals[0], tuple):
                        merged.append(vals[0])
                    else:
                        name = "flm%X_%d" % (va, k)
                        merged.append(("loc", name))
                        for pv, x in zip(b.preds, pf):
                            self.pending_merge.append((pv, name, x[k]))
                fl = tuple(merged)
            sts = [exs.get(p) for p in b.preds]
            st0 = {}
            if sts and all(x is not None for x in sts):
                st0 = {k: v for k, v in sts[0].items() if all(x.get(k) == v for x in sts[1:])}
            out[va] = self.lift_block(b, fl, st0)
            ex[va] = self.exit_flags
            exs[va] = self.exit_st
        for pv, name, val in self.pending_merge:
            stmts = out[pv]
            at = stmts[-1].at if stmts else pv
            pos = len(stmts) - 1 if stmts and stmts[-1].kind in ("cond", "switch") else len(stmts)
            stmts.insert(pos, Stmt("set", at, loc=name, e=val))
        return out

    # --- reads / writes ---
    def read(self, i, op, st, out):
        if op.type == x86.X86_OP_IMM:
            return _imm(self.p, i, op)
        if op.type == x86.X86_OP_REG:
            name, sh, mask = _SUB[op.reg]
            if name == "esp":
                return ("saddr", self.f.esp_at[i.address])
            v = ("loc", name)
            if mask == M32:
                return v
            return ("ext", v, sh, mask)
        if op.type == x86.X86_OP_MEM:
            mt = self.mem_target(i, op, st)
            size = op.size
            if mt[0] == "stack":
                if size == 4 and (mt[1] in self.api_slots_in or (mt[1] == 4 and self.f.kind == "setup")):
                    return ("api",)
                if size == 4:
                    return ("loc", _sl(mt[1]))
                if size == 8:
                    return ("qword", ("loc", _sl(mt[1])), ("loc", _sl(mt[1] + 4)))
                base = mt[1] & ~3
                return ("ext", ("loc", _sl(base)), 8 * (mt[1] - base), (1 << (8 * size)) - 1)
            if mt[0] == "apiglobal":
                return ("api",)
            if mt[0] == "apislot":
                return ("apislotptr", mt[1])
            if mt[0] == "g":
                return self.t.global_read(mt[1], size, "i" if size == 4 else "u")
            return ("mem", mt[1], size, "i" if size == 4 else "u")
        raise Unsupported("operand at %X" % i.address)

    def write(self, i, op, val, st, out, full=False):
        at = i.address
        if op.type == x86.X86_OP_REG:
            name, sh, mask = _SUB[op.reg]
            if name == "eax":
                st.pop("eax_fsw", None)
            if full or mask == M32:
                out.append(Stmt("set", at, loc=name, e=val))
                if name in ("esi", "edi", "ebx", "eax", "ecx", "edx") and _pure_addr(val):
                    st[name] = val
                else:
                    st.pop(name, None)
                return
            out.append(Stmt("set", at, loc=name, e=simp(("ins", ("loc", name), val, sh, mask))))
            st.pop(name, None)
            return
        if op.type == x86.X86_OP_MEM:
            mt = self.mem_target(i, op, st)
            size = op.size
            if mt[0] == "stack":
                self.escape(val, st)
                if size == 4:
                    out.append(Stmt("set", at, loc=_sl(mt[1]), e=val))
                elif size == 8:
                    raise Unsupported("qword stack store at %X" % at)
                else:
                    base = mt[1] & ~3
                    out.append(Stmt("set", at, loc=_sl(base),
                                    e=simp(("ins", ("loc", _sl(base)), val, 8 * (mt[1] - base), (1 << (8 * size)) - 1))))
                return
            if mt[0] == "g":
                self.escape(val, st)
                out.append(Stmt("store", at, target=("g", mt[1], size), e=val))
                return
            if mt[0] in ("apiglobal",):
                out.append(Stmt("store", at, target=("apiglobal",), e=val))
                return
            if mt[0] == "mem":
                self.escape(val, st)
                out.append(Stmt("store", at, target=("mem", mt[1], size), e=val))
                return
            raise Unsupported("write to %s at %X" % (mt, at))
        raise Unsupported("write operand at %X" % at)

    # --- conditions ---
    def carry(self, flags):
        if flags is None:
            raise Unsupported("carry without flags")
        if flags[0] == "sub":
            return ("cmp", "u<", flags[1], flags[2])
        if flags[0] == "neg":
            return ("cmp", "~=", flags[1], C0)
        if flags[0] == "res":
            return ("cmp", "~=", C0, C0)      # logic ops clear CF: false
        raise Unsupported("carry from %s" % flags[0])

    def cond(self, cc, flags, at):
        if flags is None:
            raise Unsupported("condition %s without flags at %X" % (cc, at))
        kind = flags[0]
        if kind == "sub":
            a, b = flags[1], flags[2]
            rel = {"e": "==", "ne": "~=", "l": "<", "ge": ">=", "le": "<=", "g": ">", "b": "u<", "ae": "u>=",
                   "be": "u<=", "a": "u>"}.get(cc)
            if rel:
                return simp(("cmp", rel, a, b))
            if cc in ("s", "ns"):
                r = flags[4] if flags[4] is not None else simp(("bin", "-", a, b))
                return simp(("cmp", "<" if cc == "s" else ">=", r, C0))
            raise Unsupported("condition %s after cmp at %X" % (cc, at))
        if kind == "add":
            r = flags[4]
            if cc in ("e", "ne"):
                return simp(("cmp", "==" if cc == "e" else "~=", r, C0))
            if cc in ("s", "ns"):
                return simp(("cmp", "<" if cc == "s" else ">=", _signed_view(r, flags[3]), C0))
            raise Unsupported("condition %s after add at %X" % (cc, at))
        if kind == "neg":
            a = flags[1]
            if cc in ("e", "ne"):
                return simp(("cmp", "==" if cc == "e" else "~=", a, C0))
            if cc in ("b", "ae"):
                return simp(("cmp", "~=" if cc == "b" else "==", a, C0))
            raise Unsupported("condition %s after neg at %X" % (cc, at))
        if kind == "res":
            r, size = flags[1], flags[2]
            if cc in ("e", "ne"):
                fc = fsw_zero_cond(r, "==" if cc == "e" else "~=")
                if fc is not None:
                    return fc
            sv = _signed_view(r, size)
            rel = {"e": ("==", r), "ne": ("~=", r), "s": ("<", sv), "ns": (">=", sv), "l": ("<", sv),
                   "ge": (">=", sv), "le": ("<=", sv), "g": (">", sv)}.get(cc)
            if rel:
                return simp(("cmp", rel[0], rel[1], C0))
            if cc in ("b", "o"):
                return ("cmp", "~=", C0, C0)
            if cc in ("ae", "no"):
                return ("cmp", "==", C0, C0)
            if cc in ("be",):
                return simp(("cmp", "==", r, C0))
            if cc in ("a",):
                return simp(("cmp", "~=", r, C0))
            if cc in ("p", "np"):
                fs = _fsw_parity(r)
                if fs is not None:
                    return _fcond(fs[0], fs[1], cc == "p", fs[2])
            raise Unsupported("condition %s after logic op at %X" % (cc, at))
        if kind == "sahf":
            a, b = flags[1][1], flags[1][2]
            taken = {"b": {"lt"}, "ae": {"eq", "gt"}, "e": {"eq"}, "ne": {"lt", "gt"}, "be": {"lt", "eq"},
                     "a": {"gt"}}.get(cc)
            if taken is None:
                raise Unsupported("condition %s after sahf at %X" % (cc, at))
            return _fset(a, b, taken)
        raise Unsupported("flags %s at %X" % (kind, at))

    # --- calls ---
    def call(self, i, st, out, esp, fpd):
        at = i.address
        op = i.operands[0]
        if op.type == x86.X86_OP_IMM:
            tva = op.imm & M32
            if tva in self.p.cookie:
                return                       # the /GS stack check: no effect on a run that doesn't overrun
            if tva in self.p.sprintf:
                target = ("sprintf",)
                n = None
            elif tva in self.p.rand:
                target = ("rand",)
                n = None
            elif tva in self.p.funcs:
                target = ("fn", tva)
                n = None
            else:
                raise Unsupported("call to %X at %X" % (tva, at))
        else:
            mt = self.mem_target(i, op, st) if op.type == x86.X86_OP_MEM else None
            if op.type == x86.X86_OP_REG:
                v = st.get(_R32[op.reg])
                if v and v[0] == "apislotaddr":
                    mt = ("apislot", v[1])
                elif v and v[0] == "apislotptr":
                    mt = ("apislot", v[1])
                else:
                    mt = ("reg", _R32[op.reg])
            if mt and mt[0] == "apislot":
                target = ("slot", mt[1])
            elif mt and mt[0] == "stack" or (mt and mt[0] == "reg"):
                target = ("ptr", ("loc", mt[1]) if mt[0] == "reg" else ("loc", _sl(mt[1])))
            elif mt and mt[0] == "mem":
                # call [reg]: maybe reg holds an API slot address (lea esi, [eax + slot])
                target = ("ptr", ("mem", mt[1], 4, "i"))
            else:
                raise Unsupported("call %s at %X" % (i.op_str, at))
        self.call_id += 1
        cid = (self.f.va, at)
        args = self.t.call_args(target, esp, at, self.f)
        ret = "f" if at in self.f.float_call else "i"
        e = ("call", target, tuple(("loc", _sl(esp + 4 * k)) for k in range(args)), cid, ret)
        out.append(Stmt("set", at, loc="eax", e=e, extra="call"))
        clob = self.t.clobbers.get(target[1], {"ecx", "edx"}) if target[0] == "fn" else {"ecx", "edx"}
        for r in ("ecx", "edx"):
            if r in clob:
                out.append(Stmt("set", at, loc=r, e=("undef", r)))
        if ret == "f":
            out.append(Stmt("set", at, loc="f%d" % fpd, e=("loc", "eax")))
            out.append(Stmt("set", at, loc="eax", e=("undef", "eax")))

    def tailcall(self, i, target, st, out, esp):
        """jmp f / jmp [api + slot]: the callee gets this function's own arguments (esp + 4 ...)."""
        at = i.address
        n = self.t.call_args(target, esp + 4, at, self.f)
        e = ("call", target, tuple(("loc", _sl(esp + 4 + 4 * k)) for k in range(n)), (self.f.va, at), "i")
        out.append(Stmt("tail", at, e=e))

    def string_op(self, i, st, out):
        m = i.mnemonic
        at = i.address
        if m == "rep movsd":
            out.append(Stmt("copy", at, e=(("loc", "edi"), ("loc", "esi"), ("bin", "*", ("loc", "ecx"), C(4)))))
            out.append(Stmt("set", at, loc="esi", e=simp(("bin", "+", ("loc", "esi"), ("bin", "*", ("loc", "ecx"), C(4))))))
            out.append(Stmt("set", at, loc="edi", e=simp(("bin", "+", ("loc", "edi"), ("bin", "*", ("loc", "ecx"), C(4))))))
            out.append(Stmt("set", at, loc="ecx", e=C0))
            return
        raise Unsupported("%s at %X" % (m, at))

    # --- x87 ---
    def fpu(self, i, st, out, fpd, flags):
        m, ops = i.mnemonic, i.operands
        at = i.address

        def top(k=0):
            return "f%d" % (fpd - 1 - k)

        def src_val(op):
            if op.type == x86.X86_OP_REG:
                k = op.reg - x86.X86_REG_ST0
                return ("loc", top(k))
            mt = self.mem_target(i, op, st)
            size = op.size
            if mt[0] == "stack":
                if size == 4:
                    return ("fromfbits", ("loc", _sl(mt[1])))
                if size == 8:
                    return ("fromdbits", ("loc", _sl(mt[1])), ("loc", _sl(mt[1] + 4)))
                raise Unsupported("x87 stack operand size %d at %X" % (size, at))
            if mt[0] == "g":
                if size == 4:
                    return self.t.global_read(mt[1], 4, "f")
                if size == 8:
                    return self.t.global_read(mt[1], 8, "d")
            if mt[0] == "mem":
                return ("mem", mt[1], size, "f" if size == 4 else "d")
            raise Unsupported("x87 operand at %X" % at)

        def int_val(op):
            mt = self.mem_target(i, op, st)
            size = op.size
            if mt[0] == "stack":
                if size == 4 and mt[1] == 4 and self.f.kind == "setup":
                    return ("api",)
                if size == 4:
                    return ("loc", _sl(mt[1]))
                if size == 8:
                    return ("qword", ("loc", _sl(mt[1])), ("loc", _sl(mt[1] + 4)))
                if size == 2:
                    base = mt[1] & ~3
                    return ("sx", ("ext", ("loc", _sl(base)), 8 * (mt[1] - base), 0xFFFF), 2)
            if mt[0] == "g":
                return self.t.global_read(mt[1], size, "i")
            if mt[0] == "mem":
                return ("mem", mt[1], size, "i")
            raise Unsupported("x87 int operand at %X" % at)

        def store(op, v, as_int=False):
            if op.type == x86.X86_OP_REG:
                k = op.reg - x86.X86_REG_ST0
                out.append(Stmt("set", at, loc=top(k), e=v))
                return
            mt = self.mem_target(i, op, st)
            size = op.size
            val = ("fistp", v) if as_int else (("f32", v) if size == 4 else v)
            if mt[0] == "stack":
                if size == 4:
                    out.append(Stmt("set", at, loc=_sl(mt[1]), e=val if as_int else ("f32", v)))
                    return
                if size == 8 and not as_int:
                    out.append(Stmt("set", at, loc=_sl(mt[1]), e=("dlo", v)))
                    out.append(Stmt("set", at, loc=_sl(mt[1] + 4), e=("dhi", v)))
                    return
                raise Unsupported("x87 store size %d at %X" % (size, at))
            if mt[0] == "g":
                out.append(Stmt("store", at, target=("g", mt[1], size), e=val, extra="f" if not as_int else None))
                return
            if mt[0] == "mem":
                out.append(Stmt("store", at, target=("mem", mt[1], size), e=val, extra="f" if not as_int else None))
                return
            raise Unsupported("x87 store at %X" % at)

        arith = {"fadd": "+", "fsub": "-", "fmul": "*", "fdiv": "/", "fsubr": "r-", "fdivr": "r/",
                 "faddp": "+", "fsubp": "-", "fmulp": "*", "fdivp": "/", "fsubrp": "r-", "fdivrp": "r/",
                 "fiadd": "+", "fisub": "-", "fimul": "*", "fidiv": "/", "fisubr": "r-", "fidivr": "r/"}
        if m == "fld":
            if ops[0].type == x86.X86_OP_REG:
                v = ("loc", top(ops[0].reg - x86.X86_REG_ST0))
            else:
                v = src_val(ops[0])
            out.append(Stmt("set", at, loc="f%d" % fpd, e=v))
            return None if flags is None else flags
        if m == "fild":
            v = int_val(ops[0])
            out.append(Stmt("set", at, loc="f%d" % fpd, e=("i2f", v)))
            return flags
        if m in ("fldz", "fld1"):
            out.append(Stmt("set", at, loc="f%d" % fpd, e=("fc", 0.0 if m == "fldz" else 1.0)))
            return flags
        if m in ("fst", "fstp"):
            store(ops[0], ("loc", top()))
            return flags
        if m in ("fist", "fistp"):
            store(ops[0], ("loc", top()), as_int=True)
            return flags
        if m in arith:
            op = arith[m]
            if m.startswith("fi"):
                a, c = ("loc", top()), ("i2f", int_val(ops[0]))
                dst = top()
            elif len(ops) == 2:
                # fadd st(i), st(0) / fadd st(0), st(i): decode from the opcode bytes like stage 1
                d0, s0 = _x87_regs(i)
                a, c = ("loc", top(d0)), ("loc", top(s0))
                dst = top(d0)
            elif len(ops) == 1 and ops[0].type == x86.X86_OP_MEM:
                a, c = ("loc", top()), src_val(ops[0])
                dst = top()
            elif len(ops) == 1:
                a, c = ("loc", top(ops[0].reg - x86.X86_REG_ST0)), ("loc", top())
                dst = top(ops[0].reg - x86.X86_REG_ST0)
            else:
                a, c = ("loc", top(1)), ("loc", top())
                dst = top(1)
            if op.startswith("r"):
                a, c, op = c, a, op[1:]
            out.append(Stmt("set", at, loc=dst, e=("fr", ("bin", op, a, c))))
            return flags
        if m in ("fcom", "fcomp", "fcompp", "fucom", "fucomp", "fucompp"):
            if ops:
                c = src_val(ops[0]) if ops[0].type == x86.X86_OP_MEM else ("loc", top(ops[0].reg - x86.X86_REG_ST0))
            else:
                c = ("loc", top(1))
            tmpa, tmpb = "ca%X" % at, "cb%X" % at
            out.append(Stmt("set", at, loc=tmpa, e=("loc", top())))
            out.append(Stmt("set", at, loc=tmpb, e=c))
            st["fswcmp"] = (("loc", tmpa), ("loc", tmpb))
            return flags
        if m == "fnstsw":
            cmpv = st.get("fswcmp")
            if cmpv is None:
                raise Unsupported("fnstsw without compare at %X" % at)
            v = ("fsw", cmpv[0], cmpv[1])
            out.append(Stmt("set", at, loc="eax", e=simp(("ins", ("loc", "eax"), v, 0, 0xFFFF))))
            st["eax_fsw"] = v
            return flags
        if m == "fxch":
            k = ops[0].reg - x86.X86_REG_ST0 if ops else 1
            tmp = "x%X" % at
            out.append(Stmt("set", at, loc=tmp, e=("loc", top())))
            out.append(Stmt("set", at, loc=top(), e=("loc", top(k))))
            out.append(Stmt("set", at, loc=top(k), e=("loc", tmp)))
            return flags
        if m == "fchs":
            out.append(Stmt("set", at, loc=top(), e=("un", "neg", ("loc", top()))))
            return flags
        if m in ("fnclex", "fwait"):
            return flags
        raise Unsupported("%s at %X" % (m, at))


class _ShimF:
    def __init__(self, f):
        self.blocks, self.entry = f.blocks, f.va


def _fp_delta(i):
    m = i.mnemonic
    if m in ("fld", "fild", "fldz", "fld1", "fldpi"):
        return 1
    if m in ("fstp", "fistp", "fcomp", "fucomp", "faddp", "fsubp", "fmulp", "fdivp", "fsubrp", "fdivrp"):
        return -1
    if m in ("fcompp", "fucompp"):
        return -2
    return 0


def _x87_regs(i):
    """(dst k, src k) of a two-register x87 arithmetic instruction from its opcode bytes."""
    b0, b1 = i.bytes[0], i.bytes[1]
    k = b1 & 7
    if b0 == 0xD8:
        return 0, k                    # st0 op= st(k)
    if b0 in (0xDC, 0xDE):
        return k, 0                    # st(k) op= st0
    raise Unsupported("x87 encoding %02X %02X" % (b0, b1))


def _has_kind(e, k):
    if not isinstance(e, tuple) or not e:
        return False
    if e[0] == k:
        return True
    return any(_has_kind(x, k) for x in kids(e))


def _find_kind(e, k, out=None):
    out = [] if out is None else out
    if isinstance(e, tuple) and e:
        if e[0] == k:
            out.append(e)
        for x in kids(e):
            _find_kind(x, k, out)
    return out


def _sl(off):
    """Stack slot location name (offset from the entry esp; 4 = first argument)."""
    return "s%d" % off if off >= 0 else "sm%d" % -off


def _pure_addr(v):
    return v[0] in ("api", "saddr", "gaddr", "apislotaddr", "apislotptr", "c")


def _canon_c(c, size):
    if is_c(c):
        return C(c[1] & ((1 << (8 * size)) - 1))
    return c


def _signed_view(r, size):
    if size == 4:
        return r
    return ("sx", r, size)


def _sx_expr(v):
    if v[0] == "sx":
        x, size = v[1], v[2]
        if is_c(x):
            return C(sx(x[1], size))
    return v


def _imm(p, i, op):
    v = op.imm & M32
    if i.imm_offset and (i.address + i.imm_offset) in p.p.relocs:
        if p.dll.in_text(v):
            return ("fnptr", v)
        return ("gaddr", v)
    if op.size < 4:
        return C(v & ((1 << (8 * op.size)) - 1))
    return C(v)


def _fsw_parity(r):
    """r = (fsw & mask) from `test ah, m` / `and eax, m` -> (a, b, mask in fsw bits)."""
    if r[0] == "bin" and r[1] == "&" and is_c(r[3]):
        x, mask = r[2], r[3][1]
        if x[0] == "ext" and x[1][0] == "fsw" and x[2] == 8:
            return x[1][1], x[1][2], mask << 8
        if x[0] == "fsw":
            return x[1], x[2], mask
    return None


_OUT_BITS = {"lt": 0x100, "eq": 0x4000, "gt": 0}


def _fset(a, b, taken):
    if taken == {"lt"}:
        return ("cmp", "f<", a, b)
    if taken == {"gt"}:
        return ("cmp", "f>", a, b)
    if taken == {"eq"}:
        return ("cmp", "f==", a, b)
    if taken == {"lt", "eq"}:
        return ("cmp", "f<=", a, b)
    if taken == {"gt", "eq"}:
        return ("cmp", "f>=", a, b)
    if taken == {"lt", "gt"}:
        return ("cmp", "f~=", a, b)
    if taken == {"lt", "gt", "eq"}:
        return ("cmp", "==", C0, C0)
    return ("cmp", "~=", C0, C0)


def _fcond(a, b, parity_taken, mask):
    taken = set()
    for o, bits in _OUT_BITS.items():
        v = bits & mask
        par = bin((v >> 8) & 0xFF).count("1") % 2 == 0 if mask >> 8 else bin(v & 0xFF).count("1") % 2 == 0
        if par == parity_taken:
            taken.add(o)
    return _fset(a, b, taken)


def fsw_zero_cond(r, rel):
    """(fsw & mask) == 0 / ~= 0 -> float comparison."""
    x = _fsw_parity(r)
    if x is None:
        return None
    a, b, mask = x
    taken = set()
    for o, bits in _OUT_BITS.items():
        nz = (bits & mask) != 0
        if (rel == "~=") == nz:
            taken.add(o)
    return _fset(a, b, taken)


# ============================================================================================ dataflow ==============
STABLE = ("c", "fc", "param", "api", "gaddr", "saddr", "fnptr", "apislotaddr", "apislotptr", "undef")


def stable(e):
    """A value that is the same wherever it is used (no locations, memory, globals or calls)."""
    if not isinstance(e, tuple) or not e:
        return True
    if e[0] in STABLE:
        return True
    if e[0] in ("loc", "var", "g", "mem", "msg", "evt", "call", "t", "fsw"):
        return False
    return all(stable(x) for x in kids(e))


def strip_t(e):
    if not isinstance(e, tuple) or not e:
        return e
    if e[0] == "t":
        return strip_t(e[2])
    if e[0] == "call":
        return ("call", strip_t(e[1]), tuple(strip_t(a) for a in e[2]), e[3], e[4])
    return (e[0],) + tuple(strip_t(x) if isinstance(x, tuple) else x for x in e[1:])


def tag_reads(e, t):
    """Mark the memory reads inside e with their original time (instruction address) for the order check."""
    if not isinstance(e, tuple) or not e:
        return e
    k = e[0]
    if k == "t":
        return e
    if k in ("g", "mem", "msg", "evt"):
        if k == "mem":
            return ("t", t, ("mem", tag_reads(e[1], t), e[2], e[3]))
        return ("t", t, e)
    if k == "call":
        return ("call", tag_reads(e[1], t), tuple(tag_reads(a, t) for a in e[2]), e[3], e[4])
    return (k,) + tuple(tag_reads(x, t) if isinstance(x, tuple) else x for x in e[1:])


def events(e, now, out=None):
    """Side-effect-relevant events of e in Lua evaluation order: ("call", time) / ("read", time)."""
    out = [] if out is None else out
    if not isinstance(e, tuple) or not e:
        return out
    k = e[0]
    if k == "t":
        events(e[2], e[1], out)
        return out
    if k == "call":
        events(e[1], now, out)
        for a in e[2]:
            events(a, now, out)
        out.append(("call", e[3][1]))
        return out
    if k in ("g", "msg", "evt"):
        out.append(("read", now))
        return out
    if k == "mem":
        events(e[1], now, out)
        out.append(("read", now))
        return out
    for x in e[1:]:
        if isinstance(x, tuple):
            events(x, now, out)
    return out


def order_ok(ev):
    for a in range(len(ev)):
        for b in range(a + 1, len(ev)):
            x, y = ev[a], ev[b]
            if (x[0] == "call" or y[0] == "call") and x[1] > y[1]:
                return False
    return True


def stmt_uses(s):
    out = set()
    if s.kind in ("set", "expr", "cond", "switch", "ret", "tail"):
        if s.e is not None:
            locs_in(s.e, out)
    if s.kind == "store":
        locs_in(s.e, out)
        if s.target[0] == "mem":
            locs_in(s.target[1], out)
    if s.kind == "copy":
        for x in s.e:
            locs_in(x, out)
    return out


def stmt_effect(s):
    """'call' (has a call), 'store' (writes memory / globals), 'read' (reads memory), None."""
    if s.kind in ("store", "copy"):
        return "store"
    e = s.e
    if s.kind == "copy":
        return "store"
    if e is not None and has_call(e):
        return "call"
    if e is not None and reads_memory(e):
        return "read"
    return None


class FnOpt:
    """Reaching definitions + propagation + dead code over one function's IR."""

    def __init__(self, f: Func, ir: dict, returns: bool, entry_values: dict):
        self.f, self.ir, self.returns = f, ir, returns
        self.entry_values = entry_values       # loc -> value on entry ("param", k) / ("undef", r)
        self.order = mf._rpo(_ShimF(f))

    def preds(self, va):
        return self.f.blocks[va].preds

    def rd(self):
        """-> (in_state per block, def RHS by id). state: loc -> frozenset(def ids); def id = (va, idx) | ('in', loc)."""
        ir = self.ir
        gen = {}
        for va in self.order:
            g = {}
            for k, s in enumerate(ir[va]):
                if s.kind == "set":
                    g[s.loc] = (va, k)
            gen[va] = g
        ins = {va: None for va in self.order}
        entry = {}
        for va in self.order:
            for s in ir[va]:
                for l in stmt_uses(s):
                    entry[l] = frozenset([("in", l)])
                if s.kind == "set":
                    entry.setdefault(s.loc, frozenset([("in", s.loc)]))
        ins[self.f.va] = dict(entry)
        outs = {}
        changed = True
        while changed:
            changed = False
            for va in self.order:
                if va == self.f.va:
                    st = dict(entry)
                else:
                    st = {}
                    for p in self.preds(va):
                        if p in outs:
                            for l, ds in outs[p].items():
                                st[l] = st.get(l, frozenset()) | ds
                if ins.get(va) != st:
                    ins[va] = st
                    changed = True
                o = dict(st)
                for l, d in gen[va].items():
                    o[l] = frozenset([d])
                outs[va] = o
        return ins

    def run(self):
        for _ in range(100000):
            ch = self.pass_pure()
            ch |= self.pass_inline()
            ch |= self.pass_dead()
            if not ch:
                ch = self.pass_inline_x()
            if not ch:
                ch = self.pass_narrow()
            if not ch:
                break

    def pass_narrow(self):
        """`mov al, [b]` keeps eax's upper bits; when every use of that value only looks at al, the old eax isn't
        really an input: the definition becomes the byte alone (so the earlier eax value is free to go)."""
        ins = self.rd()
        uses = self.uses_of(ins)
        for va in self.order:
            for i, s in enumerate(self.ir[va]):
                if s.kind != "set" or s.e is None or s.e[0] != "ins" or s.e[1] != ("loc", s.loc):
                    continue
                keep = s.e[4] << s.e[3]
                us = uses.get((va, i), [])
                if us and all(_only_bits(_stmt_exprs(self.ir[u[0]][u[1]]), u[2], keep) for u in us):
                    s.e = simp(("ins", C0, s.e[2], s.e[3], s.e[4]))
                    return True
        return False

    def defval(self, d):
        if d[0] == "in":
            return self.entry_values.get(d[1], ("undef", d[1]))
        va, k = d
        return self.ir[va][k].e

    def pass_pure(self):
        ins = self.rd()
        changed = False
        for va in self.order:
            st = dict(ins.get(va) or {})
            for k, s in enumerate(self.ir[va]):
                uses = stmt_uses(s)
                m = {}
                for l in uses:
                    ds = st.get(l, frozenset([("in", l)]))
                    vals = {self.defval(d) for d in ds}
                    if len(vals) == 1:
                        v = next(iter(vals))
                        if stable(v):
                            m[l] = v
                if m:
                    _subst_stmt(s, m)
                    changed = True
                if s.kind == "set":
                    st[s.loc] = frozenset([(va, k)])
        return changed

    def uses_of(self, ins):
        """def id -> list of (va, idx) of statements using it."""
        out = defaultdict(list)
        for va in self.order:
            st = dict(ins.get(va) or {})
            for k, s in enumerate(self.ir[va]):
                for l in stmt_uses(s):
                    for d in st.get(l, frozenset([("in", l)])):
                        out[d].append((va, k, l))
                if s.kind == "set":
                    st[s.loc] = frozenset([(va, k)])
        return out

    def pass_inline(self):
        ins = self.rd()
        uses = self.uses_of(ins)
        changed = False
        for va in self.order:
            stmts = self.ir[va]
            # reaching defs at each statement of this block
            st = dict(ins.get(va) or {})
            rd_at = []
            for k, s in enumerate(stmts):
                rd_at.append(dict(st))
                if s.kind == "set":
                    st[s.loc] = frozenset([(va, k)])
            for i, s in enumerate(stmts):
                if s.kind != "set":
                    continue
                us = uses[(va, i)]
                if len(us) != 1:
                    continue
                uva, j, l = us[0]
                if uva != va or j <= i:
                    continue
                if rd_at[j].get(l) != frozenset([(va, i)]):
                    continue
                if not self.can_inline(stmts, i, j):
                    continue
                if (has_call(s.e) or reads_memory(s.e)) and _occurrences(stmts[j], l) > 1:
                    continue        # `inc [eax+20]` after a call: the call must not be repeated
                e = tag_reads(s.e, s.at)
                t = stmts[j]
                old = (t.e, t.target)
                _subst_stmt(t, {l: e})
                ev = events(_stmt_expr(t), t.at)
                if not order_ok(ev):
                    t.e, t.target = old
                    continue
                del stmts[i]
                changed = True
                break                     # this block's indices moved: the next pass continues here
        return changed

    def can_inline(self, stmts, i, j):
        s = stmts[i]
        e = s.e
        rhs_locs = locs_in(e)
        rhs_mem = reads_memory(e)
        rhs_call = has_call(e)
        for k in range(i + 1, j):
            t = stmts[k]
            if t.kind == "set" and t.loc in rhs_locs:
                return False
            eff = stmt_effect(t)
            if rhs_call and eff is not None:
                return False
            if rhs_mem and eff in ("call", "store"):
                return False
        t = stmts[j]
        if t.kind in ("store",) and rhs_call and t.target[0] == "mem" and has_call(t.target[1]):
            return False
        return True

    def _between(self, dva, uva):
        """Blocks on some path from dva to uva (both excluded), not passing through dva again."""
        fwd, todo = set(), [s for s in self.f.blocks[dva].succ if s != dva]
        while todo:
            v = todo.pop()
            if v in fwd or v == dva:
                continue
            fwd.add(v)
            todo += self.f.blocks[v].succ
        back, todo = set(), [p for p in self.f.blocks[uva].preds]
        while todo:
            v = todo.pop()
            if v in back or v == dva:
                continue
            back.add(v)
            todo += self.f.blocks[v].preds
        return (fwd & back) - {uva}

    def pass_inline_x(self):
        """Single-use definitions used in another block (no calls; nothing in between changes their inputs)."""
        ins = self.rd()
        uses = self.uses_of(ins)
        for va in self.order:
            for i, s in enumerate(self.ir[va]):
                if s.kind != "set" or has_call(s.e):
                    continue
                us = uses.get((va, i), [])
                if not us:
                    continue
                if any(_occurrences(self.ir[u[0]][u[1]], u[2]) > 1 for u in us) and has_call(s.e):
                    continue
                if len(us) > 1 and not _cheap(s.e):
                    continue
                if len(us) == 1 and us[0][0] == va:
                    continue                 # pass_inline's case
                ok = True
                for uva, j, l in us:
                    st = dict(ins.get(uva) or {})
                    for k in range(j):
                        t = self.ir[uva][k]
                        if t.kind == "set":
                            st[t.loc] = frozenset([(uva, k)])
                    if st.get(l) != frozenset([(va, i)]):
                        ok = False
                        break
                    if uva == va:
                        if j <= i:
                            ok = False
                            break
                        seq = self.ir[va][i + 1:j]
                    else:
                        btw = self._between(va, uva)
                        if uva in btw or va in btw:
                            ok = False
                            break
                        seq = self.ir[va][i + 1:] + [t for b in btw for t in self.ir[b]] + self.ir[uva][:j]
                    rl, rm = locs_in(s.e), reads_memory(s.e)
                    for t in seq:
                        if t.kind == "set" and t.loc in rl:
                            ok = False
                            break
                        if rm and stmt_effect(t) in ("call", "store"):
                            ok = False
                            break
                    if not ok:
                        break
                if not ok:
                    continue
                for uva, j, l in us:
                    _subst_stmt(self.ir[uva][j], {l: tag_reads(s.e, s.at)})
                del self.ir[va][i]
                return True
        return False

    def pass_dead(self):
        ins = self.rd()
        uses = self.uses_of(ins)
        changed = False
        for va in self.order:
            stmts = self.ir[va]
            keep = []
            for k, s in enumerate(stmts):
                if s.kind == "set" and s.e == ("loc", s.loc):
                    changed = True
                    continue
                if s.kind == "set" and not uses.get((va, k)):
                    if has_call(s.e):
                        s.kind, s.loc = "expr", None
                        changed = True
                    else:
                        changed = True
                        continue
                keep.append(s)
            self.ir[va] = keep
        return changed


def _occurrences(stmt, loc):
    n = 0
    for e in _stmt_exprs(stmt):
        n += _count_loc(e, loc)
    return n


def _stmt_exprs(s):
    out = []
    if s.kind == "copy":
        out += list(s.e)
    elif s.e is not None:
        out.append(s.e)
    if s.kind == "store" and s.target[0] == "mem":
        out.append(s.target[1])
    return out


def _count_loc(e, loc):
    if not isinstance(e, tuple) or not e:
        return 0
    if e[0] == "loc":
        return 1 if e[1] == loc else 0
    return sum(_count_loc(x, loc) for x in kids(e))


def _only_bits(exprs, loc, keep):
    """True when every occurrence of ("loc", loc) in exprs is masked to bits within `keep`."""
    def ok(e, masked):
        if not isinstance(e, tuple) or not e:
            return True
        if e[0] == "loc":
            return e[1] != loc or masked
        if e[0] == "ext" and e[1] == ("loc", loc):
            return ((e[3] << e[2]) & ~keep) == 0
        if e[0] == "bin" and e[1] == "&" and e[2] == ("loc", loc) and is_c(e[3]):
            return (e[3][1] & M32 & ~keep) == 0
        return all(ok(x, False) for x in kids(e))
    return all(ok(e, False) for e in exprs)


def _cheap(e):
    """Worth repeating at several uses rather than naming: a global, a constant, a parameter, x +- c."""
    e = strip_t(e)
    if e[0] in ("g", "c", "fc", "param", "gaddr", "saddr", "api"):
        return True
    if e[0] == "bin" and e[1] in ("+", "-") and is_c(e[3]) and e[2][0] in ("g", "param"):
        return True
    return False


def _stmt_expr(s):
    if s.kind == "store" and s.target[0] == "mem":
        return ("pair", s.target[1], s.e)
    if s.kind == "copy":
        return ("pair",) + tuple(s.e)
    return s.e


def _subst_stmt(s, m):
    if s.kind == "copy":
        s.e = tuple(simp(subst(x, m)) for x in s.e)
        return
    if s.e is not None:
        s.e = simp(subst(s.e, m))
    if s.kind == "store" and s.target[0] == "mem":
        s.target = ("mem", simp(subst(s.target[1], m)), s.target[2])


# ============================================================================================ translator ============
BUILTIN_SLOTS = {
    0x000: {"name": "check_version", "args": [{"name": "major", "type": "int"}, {"name": "minor", "type": "int"},
                                              {"name": "table_size", "type": "int"}]},
    0x004: {"name": "register_anim", "args": [{"name": "state", "type": "string"}, {"name": "file", "type": "string"}]},
}
KIND_PARAMS = {"update": 0, "message": 2, "save": 1, "load": 1, "size": 0, "init": 0, "setup": 1, "queue": 1}


class Translator:
    def __init__(self, path_or_bytes, name=None, slots=None):
        self.prog = Program(path_or_bytes, name)
        p = self.prog
        self.slots = dict(BUILTIN_SLOTS)
        self.slots.update(slots if slots is not None else load_slots())
        # two slots with one name in the json (is_dead 0x100 / 0x1CC ...): the later ones get their slot appended
        seen = {}
        for slot in sorted(self.slots):
            nm = self.slots[slot].get("name")
            if nm in seen:
                self.slots[slot] = dict(self.slots[slot], name="%s_%03X" % (nm, slot))
            elif nm:
                seen[nm] = slot
        self.gacc = defaultdict(set)      # global address -> {(size, kind)}
        self.gwrite = defaultdict(set)    # address -> {size}
        self.taken = set()                # data addresses taken as values (not strings)
        self.strings = {}                 # address -> text (literals handed to the game)
        self.stats = defaultdict(int)
        self.warnings = []
        self.site_args = {}               # call insn address -> argument count (evidence)
        self.api_params = {}
        self.arg_mismatch = []
        self.funcs = p.funcs
        for va, f in self.funcs.items():
            f.kind = "plain"
        kinds = dict(zip(("init", "update", "message", "save", "load", "size"), p.cbs))
        for k, va in kinds.items():
            if va in self.funcs and self.funcs[va].kind == "plain":
                self.funcs[va].kind = k
        self.funcs[p.setup].kind = "setup"
        for va in p.queue_cbs:
            if va in self.funcs and self.funcs[va].kind == "plain":
                self.funcs[va].kind = "queue"
        # frames first (call sites need the callees' ret n)
        self.lifters = {va: Lifter(self, f) for va, f in self.funcs.items()}
        for va, lf in self.lifters.items():
            lf.frame()
        self._param_counts()
        self._clobbers()
        self._site_args()
        self.ir = {}
        for va, lf in self.lifters.items():
            self.ir[va] = lf.lift_all()
            if lf.escaped:
                # buffers whose address is handed out: from each escaped offset to the next one (or the frame top);
                # one handed to an API argument that is a vector / out pointer is 12 bytes
                offs = sorted(lf.escaped)
                import copy
                tmp = copy.deepcopy(self.ir[va])
                FnOpt(self.funcs[va], tmp, True, self.entry_values(self.funcs[va])).run()
                vec = self._vector_offsets(tmp)
                regs = []
                for k, o in enumerate(offs):
                    hi = offs[k + 1] if k + 1 < len(offs) else (0 if o < 0 else o + 4)
                    if o in vec:
                        hi = min(hi, o + 12)
                    if hi > o:
                        regs.append((o, hi))
                lf.regions = regs
                lf.escaped = set()
                self.ir[va] = lf.lift_all()
                self.funcs[va].buffers = regs
        # helpers that get the API table as an argument (`call [arg + slot]`): that argument is the API table
        import copy
        for va, lf in self.lifters.items():
            f = self.funcs[va]
            if f.kind != "plain":
                continue          # callbacks get the game's pointers (event, message), never the API table
            ir = copy.deepcopy(self.ir[va])
            o = FnOpt(f, ir, True, self.entry_values(f))
            o.run()
            ks = set()
            for b in ir.values():
                for st in b:
                    for e in ([st.e] if st.e is not None and st.kind != "copy" else []):
                        for c in _find_kind(strip_t(e), "call"):
                            if c[1][0] == "ptr":
                                for pp in _find_kind(c[1][1], "param"):
                                    ks.add(pp[1])
            if ks:
                lf.api_slots_in = {4 + 4 * k for k in ks}
                self.ir[va] = lf.lift_all()
                self.api_params[va] = ks

    def _vector_offsets(self, ir):
        """Stack offsets whose address goes to an API argument typed vector / out pointer (float[3])."""
        out, vec_only, other = set(), set(), set()
        for b in ir.values():
            for st in b:
                for e in ([st.e] if st.e is not None and st.kind != "copy" else []):
                    for c in _find_kind(e, "call"):
                        types = [a.get("type") for a in self.slots.get(c[1][1], {}).get("args", [])] \
                            if c[1][0] == "slot" else []
                        for k, a in enumerate(c[2]):
                            a = strip_t(a)
                            if a[0] != "saddr":
                                continue
                            t = types[k] if k < len(types) else None
                            (vec_only if t in ("vector_ptr", "out_ptr") else other).add(a[1])
        return vec_only - other

    # --- arguments ---
    def _param_counts(self):
        n = {}
        for va, f in self.funcs.items():
            mx = KIND_PARAMS.get(f.kind, 0)
            for b in f.blocks.values():
                for i in b.insns:
                    for op in i.operands:
                        if op.type == x86.X86_OP_MEM and op.mem.index == 0:
                            base = _R32.get(op.mem.base) if op.mem.base else None
                            off = None
                            if base == "esp":
                                off = f.esp_at[i.address] + s32(op.mem.disp)
                            elif base == "ebp" and f.ebp_at.get(i.address) is not None:
                                off = f.ebp_at[i.address] + s32(op.mem.disp)
                            if off is not None and off >= 4:
                                mx = max(mx, (off - 4) // 4 + 1)
                # `jmp [api + slot]` hands this function's own stack arguments to the slot (MP_MISSION1 0x10002480:
                # clears three globals, then jmp [edx + 0x7AC] return_item_to_waypoint(item, waypoint))
                last = b.insns[-1] if b.insns else None
                if last is not None and last.mnemonic == "jmp" and last.operands \
                        and last.operands[0].type == x86.X86_OP_MEM and last.operands[0].mem.index == 0 \
                        and last.operands[0].mem.base and 0 <= last.operands[0].mem.disp < 0x8D8 \
                        and last.operands[0].mem.disp % 4 == 0:
                    mx = max(mx, len(self.slots.get(last.operands[0].mem.disp, {}).get("args", [])))
            n[va] = mx
        # tail calls pass this function's own arguments on
        for _ in range(8):
            ch = False
            for va, f in self.funcs.items():
                for b in f.blocks.values():
                    if b.kind == "tail" and b.tail in n and n[b.tail] > n[va]:
                        n[va] = n[b.tail]
                        ch = True
            if not ch:
                break
        for va, f in self.funcs.items():
            f.nparams = n[va]

    def _clobbers(self):
        """Registers each mission function can change (directly, or through what it calls). MSVC knows its own static
        functions: a caller may keep a value in edx across a call to one that never writes edx."""
        WRITES = {"mov", "movzx", "movsx", "lea", "add", "sub", "and", "or", "xor", "adc", "sbb", "inc", "dec",
                  "neg", "not", "shl", "sal", "shr", "sar", "imul", "pop", "xchg", "cdq", "idiv", "div", "mul",
                  "lodsb", "fnstsw"} | set(_SET)
        own, calls = {}, {}
        for va, f in self.funcs.items():
            w, c = set(), set()
            for b in f.blocks.values():
                if b.kind == "tail" and b.tail in self.funcs:
                    c.add(b.tail)
                elif b.kind in ("tail", "tailmem"):
                    w |= {"eax", "ecx", "edx"}
                for i in b.insns:
                    m = i.mnemonic
                    if m == "call":
                        op = i.operands[0]
                        t = op.imm & M32 if op.type == x86.X86_OP_IMM else None
                        if t in self.funcs:
                            c.add(t)
                        else:
                            w |= {"eax", "ecx", "edx"}
                        continue
                    if m in ("cdq",):
                        w.add("edx")
                    if m in ("idiv", "div", "mul") or (m == "imul" and len(i.operands) == 1):
                        w |= {"eax", "edx"}
                    if m.startswith("rep") or m in ("movsd", "movsb", "stosd", "stosb"):
                        w |= {"ecx", "esi", "edi"}
                    if m == "fnstsw":
                        w.add("eax")
                    if m in WRITES and i.operands and i.operands[0].type == x86.X86_OP_REG:
                        r = _SUB.get(i.operands[0].reg, (None,))[0]
                        if r:
                            w.add(r)
                    if m == "xchg" and len(i.operands) > 1 and i.operands[1].type == x86.X86_OP_REG:
                        r = _SUB.get(i.operands[1].reg, (None,))[0]
                        if r:
                            w.add(r)
            own[va], calls[va] = w, c
        total = {va: set(w) for va, w in own.items()}
        changed = True
        while changed:
            changed = False
            for va in total:
                for c in calls[va]:
                    if not total[c] <= total[va]:
                        total[va] |= total[c]
                        changed = True
        self.clobbers = total

    def _site_args(self):
        """Argument count per call site = the stack slots from esp upwards that were written (pushed or stored)
        since the previous call on every path to it. A push of a callee-saved register that the function hasn't
        written yet is the prologue saving it, not an argument."""
        self._wb_memo = {}
        for va, f in self.funcs.items():
            self._saves = self._prologue_saves(f)
            for b in f.blocks.values():
                for k, i in enumerate(b.insns):
                    if i.mnemonic == "call":
                        w = self._written_before(f, b.start, k, set())
                        esp = f.esp_at[i.address]
                        n = 0
                        while esp + 4 * n in w:
                            n += 1
                        self.site_args[i.address] = n

    def _block_writes(self, f, b):
        """insn address -> stack offsets it writes (push, mov [esp+d], mov [reg+d] with reg = a copy of esp)."""
        key = ("w", f.va, b.start)
        if key in self._wb_memo:
            return self._wb_memo[key]
        out = {}
        regs = {}
        for i in b.insns:
            esp = f.esp_at[i.address]
            ops = i.operands
            ws = set()
            if i.mnemonic == "push" and i.address not in self._saves:
                ws.add(esp - 4)
            elif ops and ops[0].type == x86.X86_OP_MEM and i.mnemonic in ("mov", "fstp", "fst", "fistp", "fist")                     and ops[0].mem.index == 0:
                base = _R32.get(ops[0].mem.base) if ops[0].mem.base else None
                off = None
                if base == "esp":
                    off = esp + s32(ops[0].mem.disp)
                elif base in regs:
                    off = regs[base] + s32(ops[0].mem.disp)
                if off is not None:
                    for q in range(0, max(4, ops[0].size), 4):
                        ws.add(off + q)
            if ws:
                out[i.address] = ws
            if ops and ops[0].type == x86.X86_OP_REG:
                r = _R32.get(ops[0].reg)
                if i.mnemonic == "mov" and ops[1].type == x86.X86_OP_REG and _R32.get(ops[1].reg) == "esp":
                    regs[r] = esp
                elif i.mnemonic == "lea" and ops[1].mem.base and _R32.get(ops[1].mem.base) == "esp"                         and not ops[1].mem.index:
                    regs[r] = esp + s32(ops[1].mem.disp)
                elif r in regs and i.mnemonic not in ("push", "cmp", "test"):
                    del regs[r]
            if i.mnemonic == "call":
                for r in ("eax", "ecx", "edx"):
                    regs.pop(r, None)
        self._wb_memo[key] = out
        return out

    def _prologue_saves(self, f):
        saves = set()
        written = set()
        b = f.blocks[f.va]
        for i in b.insns:
            if i.mnemonic == "call":
                break
            if i.mnemonic == "push" and i.operands[0].type == x86.X86_OP_REG:
                r = _R32.get(i.operands[0].reg)
                if r in ("ebx", "esi", "edi", "ebp") and r not in written:
                    saves.add(i.address)
                    continue
            if i.operands and i.operands[0].type == x86.X86_OP_REG and i.mnemonic not in ("push", "cmp", "test"):
                written.add(_R32.get(i.operands[0].reg))
        return saves

    def _written_before(self, f, bva, k, seen):
        """Stack offsets written in block bva before instruction k, back to the previous call."""
        b = f.blocks[bva]
        w = set()
        writes = self._block_writes(f, b)
        for i in reversed(b.insns[:k]):
            if i.mnemonic == "call":
                return w
            w |= writes.get(i.address, set())
        if bva == f.va or bva in seen or not b.preds:
            return w
        key = (f.va, bva)
        if key not in self._wb_memo:
            self._wb_memo[key] = set()        # cycle: nothing known
            seen = seen | {bva}
            common = None
            for pv in b.preds:
                memo = self._wb_memo.get((f.va, ("end", pv)))
                if memo is None:
                    memo = self._written_before(f, pv, len(f.blocks[pv].insns), seen)
                    self._wb_memo[(f.va, ("end", pv))] = memo
                common = memo if common is None else common & memo
            self._wb_memo[key] = common or set()
        return w | self._wb_memo[key]

    def call_args(self, target, esp, at, f):
        if target[0] == "slot":
            s = self.slots.get(target[1])
            ev = self.site_args.get(at)
            js = len(s["args"]) if s is not None and "args" in s else None
            if ev is not None and js is not None and ev != js:
                self.stats["arg_count_differs_from_json"] += 1
                self.arg_mismatch.append((at, target[1], ev, js))
            if js is not None and s["args"] and s["args"][-1].get("name") in ("args", "format_args")                     and ev is not None:
                return max(js - 1, ev)    # printf-style: the arguments the DLL pushed (none past the format)
            if js is not None:
                return js
            return ev or 0
        if target[0] == "fn":
            return self.funcs[target[1]].nparams
        if target[0] == "rand":
            return 0
        if target[0] == "sprintf":
            fmt = self._sprintf_format(f, at, esp)
            return 2 + _format_dwords(fmt)
        return self.site_args.get(at) or 0

    def _sprintf_format(self, f, at, esp):
        """sprintf(buf, fmt, ...): the format(s) = the string literal(s) pushed into the second argument slot (a
        call the compiler shares between paths can have one per path): the longest decides the argument count."""
        for bva, blk in f.blocks.items():
            for k, i in enumerate(blk.insns):
                if i.address == at:
                    vals = self._slot_imm(f, bva, k, esp + 4, frozenset())
                    fmts = [self.prog.dll.cstr(v) for v in vals if v is not None]
                    if fmts and all(x is not None for x in fmts):
                        return max(fmts, key=_format_dwords)
        raise Unsupported("sprintf format at %X" % at)

    def _slot_imm(self, f, bva, k, off, seen):
        """The immediates last pushed into stack offset `off` before instruction k of block bva, over all paths."""
        blk = f.blocks[bva]
        for j in reversed(blk.insns[:k]):
            if j.mnemonic == "push" and f.esp_at[j.address] - 4 == off:
                op = j.operands[0]
                return {op.imm & M32 if op.type == x86.X86_OP_IMM else None}
        if bva in seen or bva == f.va:
            return {None}
        out = set()
        for pv in blk.preds:
            out |= self._slot_imm(f, pv, len(f.blocks[pv].insns), off, seen | {bva})
        return out

    # --- globals ---
    def global_read(self, addr, size, kind):
        p = self.prog
        if p.in_rdata(addr):
            raw = p.bytes_at(addr, size)
            if kind == "f" and size == 4:
                return ("fc", struct.unpack("<f", raw)[0])
            if kind == "d" and size == 8:
                return ("fc", struct.unpack("<d", raw)[0])
            v = int.from_bytes(raw, "little")
            return C(v) if size == 4 else ("c", v)
        if not p.in_data(addr):
            raise Unsupported("global read outside the data at %X" % addr)
        self.gacc[addr].add((size, kind))
        return ("g", addr, size, kind)

    # --- the whole program ---
    def entry_values(self, f):
        ev = {}
        for k in range(max(f.nparams, 0) + 8):
            ev[_sl(4 + 4 * k)] = ("param", k)
        return ev


def _format_dwords(fmt):
    n = 0
    for m in re.finditer(r"%[-+ #0]*(\d+|\*)?(\.\d+)?(hh|h|ll|l|L|I64)?([diouxXcspfeEgGn%])", fmt):
        conv = m.group(4)
        if conv == "%":
            continue
        n += 2 if conv in "feEgG" else 1
    return n


# ============================================================================================ structuring ===========
# AST nodes: ("s", Stmt) | ("if", [(cond, body)], else_body | None) | ("while", cond, body) | ("repeat", body, cond)
#            ("break",) | ("continue", head) | ("goto", label) | ("label", label) | ("return", expr | None)
#            ("switch", on_expr, [(values, body)], default_body)


def _pure_cond(e):
    """A condition that can be evaluated in any order / more than once: no calls, no memory through pointers."""
    e = strip_t(e)
    if not isinstance(e, tuple) or not e:
        return True
    if e[0] in ("call", "mem", "loc"):
        return False
    return all(_pure_cond(x) for x in kids(e))


def _dominators(blocks, entry, order):
    dom = {v: set(order) for v in order}
    dom[entry] = {entry}
    changed = True
    while changed:
        changed = False
        for v in order:
            if v == entry:
                continue
            ps = [dom[p] for p in blocks[v].preds if p in dom]
            new = {v} | (set.intersection(*ps) if ps else set())
            if new != dom[v]:
                dom[v], changed = new, True
    return dom


class Node:
    """A structuring node: one block, or a collapsed region of pure conditions (a multi-way branch)."""
    __slots__ = ("id", "stmts", "kind", "branches", "succ", "preds", "term", "table")

    def __init__(self, nid):
        self.id, self.stmts, self.kind, self.branches, self.succ, self.preds = nid, [], "fall", [], [], []
        self.term, self.table = None, None


class Structurer:
    def __init__(self, tr, f: Func, ir, stats):
        self.tr, self.f, self.ir, self.stats = tr, f, ir, stats
        self.labels = set()
        self.build()

    # --- graph ---
    def build(self):
        f, ir = self.f, self.ir
        order = mf._rpo(_ShimF(f))
        self.order = order
        nodes = {}
        for va in order:
            b = f.blocks[va]
            n = Node(va)
            st = list(ir[va])
            term = st[-1] if st and st[-1].kind in ("cond", "switch", "ret", "tail") else None
            if term is not None:
                st = st[:-1]
            n.stmts, n.term = st, term
            if term is None:
                n.kind = "fall"
                n.succ = list(b.succ[:1])
            elif term.kind == "cond":
                n.kind = "branch"
                taken, fall = b.succ[0], b.succ[1] if len(b.succ) > 1 else b.succ[0]
                n.branches = [(term.e, taken), (None, fall)]
                n.succ = [taken, fall]
            elif term.kind == "switch":
                n.kind = "switch"
                n.table = dict(term.extra["values"])
                n.succ = list(dict.fromkeys(n.table.values()))
            else:
                n.kind = term.kind
                n.succ = []
            nodes[va] = n
        self.nodes = nodes
        self.labels_final = set()
        self.no_goto = set()
        self.flags = []
        self.flag_no = 0
        self._forward_empty()
        self._absorb_switch_bounds()
        self._short_circuit()
        self._collapse_conditions()
        for n in self.nodes.values():
            n.preds = []
        for n in self.nodes.values():
            n.succ = [s for s in dict.fromkeys(n.succ) if s in self.nodes]
            for s in n.succ:
                self.nodes[s].preds.append(n.id)
        self.order = [v for v in mf._rpo(_GraphShim(self.nodes, self.f.va))]
        self.dom = _dominators(self.nodes, self.f.va, self.order)
        pos = {v: k for k, v in enumerate(self.order)}
        self.back = {(n.id, s) for n in self.nodes.values() if n.id in pos for s in n.succ
                     if s in self.dom.get(n.id, ()) and pos[s] <= pos[n.id]}
        self.retreat = {(n.id, s) for n in self.nodes.values() if n.id in pos for s in n.succ
                        if s in pos and pos[s] <= pos[n.id]}
        self.loops = {}
        for (u, h) in sorted(self.back):
            body, todo = {h}, [u]
            while todo:
                v = todo.pop()
                if v in body:
                    continue
                body.add(v)
                todo += self.nodes[v].preds
            self.loops.setdefault(h, set()).update(body)
        self.ipdom = self._ipdom()

    def _forward_empty(self):
        """Blocks with nothing in them that just fall through: edges go straight to their target."""
        nodes = self.nodes
        def final(t, seen=()):
            n = nodes.get(t)
            if n is None or t == self.f.va or n.kind != "fall" or n.stmts or not n.succ or t in seen:
                return t
            return final(n.succ[0], seen + (t,))
        for n in nodes.values():
            if n.kind == "branch":
                n.branches = [(c, final(t)) for c, t in n.branches]
                n.succ = [t for _c, t in n.branches]
            elif n.kind == "switch":
                n.table = {k: final(t) for k, t in n.table.items()}
                n.succ = list(dict.fromkeys(n.table.values()))
            elif n.kind == "fall" and n.succ:
                n.succ = [final(n.succ[0])]
        reach, todo = set(), [self.f.va]
        while todo:
            x = todo.pop()
            if x in reach:
                continue
            reach.add(x)
            todo += nodes[x].succ
        for x in list(nodes):
            if x not in reach:
                del nodes[x]

    def _short_circuit(self):
        """`if a then T elseif b then T` chains -> `if a or b then T` (and the `and` / negated forms). Lua's `and` /
        `or` evaluate left to right and stop early exactly like the jump chain, so conditions with calls qualify."""
        nodes = self.nodes
        changed = True
        while changed:
            changed = False
            preds = defaultdict(list)
            for n in nodes.values():
                for s in n.succ:
                    preds[s].append(n.id)
            for x in list(nodes):
                n = nodes.get(x)
                if n is None or n.kind != "branch" or len(n.branches) != 2:
                    continue
                (c, t), (_n, f) = n.branches
                for side in ("f", "t"):
                    y = f if side == "f" else t
                    m = nodes.get(y)
                    if m is None or y == x or m.kind != "branch" or len(m.branches) != 2 or m.stmts                             or preds[y] != [x]:
                        continue
                    (cy, ty), (_n2, fy) = m.branches
                    nc = None
                    if side == "f" and ty == t:
                        nc, nt, nf = ("or", c, cy), t, fy
                    elif side == "f" and fy == t:
                        nc, nt, nf = ("or", c, simp(("not", cy))), t, ty
                    elif side == "t" and fy == f:
                        nc, nt, nf = ("and", c, cy), ty, f
                    elif side == "t" and ty == f:
                        nc, nt, nf = ("and", c, simp(("not", cy))), fy, f
                    if nc is None:
                        continue
                    n.branches = [(nc, nt), (None, nf)]
                    n.succ = [nt, nf]
                    del nodes[y]
                    self.stats["conditions_joined"] += 1
                    changed = True
                    break
                if changed:
                    break

    def _absorb_switch_bounds(self):
        """`if idx u> N then default else <jump table on idx>`: the range check belongs to the switch (its default)."""
        for n in list(self.nodes.values()):
            if n.kind != "branch" or n.stmts:
                continue
            c, taken = n.branches[0]
            fall = n.branches[1][1]
            c = strip_t(c)
            if c[0] != "cmp" or c[1] not in ("u>", "u>=") or not is_c(c[3]):
                continue
            s = self.nodes.get(fall)
            if s is None or s.kind != "switch" or s.stmts or strip_t(s.term.e) != c[2]:
                continue
            bound = c[3][1] if c[1] == "u>" else c[3][1] - 1
            if max(s.table) > bound:
                continue
            # the switch node gets the default; the range-check node becomes a plain jump to it
            s.term.extra = dict(s.term.extra, default=taken)
            s.succ = list(dict.fromkeys(list(s.succ) + [taken]))
            n.kind, n.branches, n.succ, n.term = "fall", [], [fall], None

    def _collapse_conditions(self):
        """Regions of blocks that only test pure conditions -> one multi-way branch with combined conditions."""
        nodes = self.nodes
        purec = {v for v, n in nodes.items() if n.kind == "branch" and not n.stmts and _pure_cond(n.branches[0][0])}
        preds = defaultdict(list)
        for n in nodes.values():
            for s in n.succ:
                preds[s].append(n.id)
        done = set()
        for v in self.order:
            if v not in purec or v in done:
                continue
            region, todo = [v], [s for s in nodes[v].succ]
            rs = {v}
            changed = True
            while changed:
                changed = False
                for r in list(rs):
                    for s in nodes[r].succ:
                        if s in purec and s not in rs and s != v and all(p in rs for p in preds[s]):
                            rs.add(s)
                            region.append(s)
                            changed = True
            if len(rs) < 2:
                continue
            res = self._formulas(v, rs)
            if res is None:
                continue
            done |= rs
            n = nodes[v]
            n.branches = res
            n.succ = [t for _c, t in res]
            for r in rs - {v}:
                del nodes[r]
            self.stats["conditions_combined"] += len(rs)

    def _formulas(self, root, rs):
        nodes = self.nodes
        atoms = []
        index = {}
        for r in sorted(rs):
            c = strip_t(nodes[r].branches[0][0])
            key, neg = _atom_key(c)
            if key not in index:
                index[key] = len(atoms)
                atoms.append(key)
        if len(atoms) > 12:
            return None
        exits_order = []
        table = defaultdict(list)
        n = len(atoms)
        for bits in range(1 << n):
            val = [(bits >> k) & 1 == 1 for k in range(n)]
            if not _consistent(atoms, val):
                continue
            v = root
            steps = 0
            while v in rs and steps < 64:
                c = strip_t(nodes[v].branches[0][0])
                key, neg = _atom_key(c)
                truth = val[index[key]] != neg
                v = nodes[v].branches[0][1] if truth else nodes[v].branches[1][1]
                steps += 1
            if v not in exits_order:
                exits_order.append(v)
            table[v].append(bits)
        valid = set(b for vs in table.values() for b in vs)
        dontcare = [b for b in range(1 << n) if b not in valid]
        remaining = list(exits_order)
        out = []
        handled = []
        while len(remaining) > 1:
            best = None
            for e in remaining:
                dc = dontcare + [b for h in handled for b in table[h]]
                terms = _minimise(table[e], dc, n)
                cost = sum(len(t) for t in terms) + 0.5 * len(terms)
                if best is None or cost < best[0] - 1e-9:
                    best = (cost, e, terms)
            _cost, e, terms = best
            out.append((_formula_expr(terms, atoms), e))
            handled.append(e)
            remaining.remove(e)
        out.append((None, remaining[0]))
        return out

    def _ipdom(self):
        EXIT = -1
        succ = {v: [s for s in n.succ if (v, s) not in self.retreat] or [EXIT] for v, n in self.nodes.items()}
        allv = list(self.nodes) + [EXIT]
        pdom = {v: set(allv) for v in allv}
        pdom[EXIT] = {EXIT}
        changed = True
        while changed:
            changed = False
            for v in self.nodes:
                ss = [pdom[s] for s in succ[v]]
                new = {v} | (set.intersection(*ss) if ss else set())
                if new != pdom[v]:
                    pdom[v], changed = new, True
        out = {}
        for v in self.nodes:
            cands = pdom[v] - {v}
            best = None
            for c in sorted(cands):
                if all(o == c or o in pdom[c] for o in cands):
                    best = c
                    break
            out[v] = None if best in (None, EXIT) else best
        return out

    # --- structuring ---
    DUP_BUDGET = 400            # statements a function may repeat instead of a goto

    def run(self):
        self.flags = []
        self.flag_no = 0
        self.skip_stack = []
        self.emitted = set()
        self.dup_used = 0
        self.active = []
        self.reach = {}
        body = self.region(self.f.va, None, None)
        return _tidy(body)

    def reachable(self, v):
        if v not in self.reach:
            seen, todo = set(), [v]
            while todo:
                x = todo.pop()
                if x in seen:
                    continue
                seen.add(x)
                todo += [s for s in self.nodes[x].succ if (x, s) not in self.retreat]
            self.reach[v] = seen
        return self.reach[v]

    def size(self, v, limit=200):
        """Statements of the code reachable from v (for the duplication budget)."""
        n = 0
        for x in sorted(self.reachable(v)):
            n += len(self.nodes[x].stmts) + 1
            if n > limit:
                break
        return n

    def loop_follow(self, h):
        body = self.loops[h]
        exits = [s for v in sorted(body) for s in self.nodes[v].succ if s not in body]
        if not exits:
            return None
        ip = self.ipdom.get(h)
        if ip in exits:
            return ip
        return max(sorted(set(exits)), key=exits.count)

    def small_tail(self, v):
        """v leads straight into a return with only a few statements on the way."""
        n, x, steps = 0, v, 0
        while x is not None and steps < 4:
            node = self.nodes.get(x)
            if node is None:
                return False
            n += len(node.stmts)
            if n > 3:
                return False
            if node.kind in ("ret", "tail"):
                return True
            if node.kind != "fall" or not node.succ:
                return False
            x = node.succ[0]
            steps += 1
        return False

    def choose_join(self, v, targets, loop):
        """Where the code continues after a branch -> (join, skip). Usually the immediate post-dominator J (skip
        None). When several arms first meet at a node M before J (`if a then x = 1 elseif b then x = 2 else goto
        past end; <M uses x>`), M is the join and the arms that bypass M jump to J (skip = J, a forward goto to a
        label at J's place in the enclosing sequence). When J is only a shared return, M is the join and the other
        arms repeat the return."""
        J = self.ipdom.get(v)
        if loop is not None and J is not None and J not in loop[1]:
            J = None
        ts = [t for t in dict.fromkeys(targets) if t is not None]
        cover = defaultdict(int)
        for t in ts:
            for x in sorted(self.reachable(t)):
                if x != v and x != J and v in self.dom.get(x, ()) and (loop is None or x in loop[1]):
                    cover[x] += 1
        pos = {x: k for k, x in enumerate(self.order)}
        best = None
        for x, c in cover.items():
            if c < 2 or self.small_tail(x):
                continue
            if J is not None and J not in self.reachable(x):
                continue
            if best is None or (c, -pos.get(x, 0)) > (cover[best], -pos.get(best, 0)):
                best = x
        if J is not None and not self.small_tail(J):
            if best is None:
                return J, None
            if not self.reaches_avoiding(v, J, best):
                return best, None          # every way to J passes M: M is the join
            return best, J                 # some paths bypass M: they set a flag, M..J runs when it isn't set
        return (best if best is not None else J), None

    def reaches_avoiding(self, src, dst, avoid):
        seen, todo = set(), [s for s in self.nodes[src].succ if (src, s) not in self.retreat]
        while todo:
            x = todo.pop()
            if x == dst:
                return True
            if x in seen or x == avoid:
                continue
            seen.add(x)
            todo += [s for s in self.nodes[x].succ if (x, s) not in self.retreat]
        return False

    def jump(self, t, loop, stop):
        """Statements for control reaching t from inside the current construct when t isn't simply next."""
        if loop is not None:
            h, body, follow = loop
            if t == h:
                return [("continue", h)]
            if t not in body:
                if t == follow:
                    return [("break",)]
                return self.dup_or_goto(t, None, None)
        return self.dup_or_goto(t, stop, loop)

    def dup_or_goto(self, t, stop, loop):
        if t in self.active or t in self.loops:
            self.labels.add(t)
            self.stats["gotos"] += 1
            return [("goto", t)]
        cost = self.size(t)
        if self.small_tail(t) or self.dup_used + cost <= self.DUP_BUDGET or \
                (t in self.no_goto and cost <= 60):
            self.dup_used += cost
            self.stats["duplicated_statements"] += cost
            return self.region(t, stop, loop, dup=True)
        self.labels.add(t)
        self.stats["gotos"] += 1
        return [("goto", t)]

    def region(self, v, stop, loop, dup=False):
        out = []
        while v is not None and v != stop:
            hit = next((fl for sk, fl in reversed(self.skip_stack) if sk == v), None)
            if hit is not None:
                out.append(("setflag", hit, True))     # a path that bypasses the join: flag it, go on after
                break
            if loop is not None and v == loop[0] and (v in self.emitted or dup):
                out.append(("continue", v))
                break
            if loop is not None and v not in loop[1]:
                out += self.jump(v, loop, stop)
                break
            if v in self.emitted and not dup:
                out += self.dup_or_goto(v, stop, loop)
                break
            if v in self.active:
                self.labels.add(v)
                self.stats["gotos"] += 1
                out.append(("goto", v))
                break
            if v in self.loops and (loop is None or loop[0] != v):
                body = self.loops[v]
                follow = self.loop_follow(v)
                inner = (v, body, follow)
                self.active.append(v)
                lbody = self.emit_node(v, None, inner, dup)
                nxt = self._next
                if nxt is not None:
                    lbody += self.region(nxt, None, inner, dup)
                self.active.pop()
                out.append(("while", None, lbody))
                v = follow
                continue
            self.active.append(v)
            out += self.emit_node(v, stop, loop, dup)
            self.active.pop()
            v = self._next
        return out

    def emit_node(self, v, stop, loop, dup=False):
        n = self.nodes[v]
        if not dup:
            self.emitted.add(v)
        out = []
        if v in self.labels_final and not dup:
            out.append(("label", v))
        out += [("s", s) for s in n.stmts]
        self._next = None
        if n.kind in ("ret", "tail"):
            out.append(("return", n.term.e, n.term.kind))
            return out
        if n.kind == "fall":
            if not n.succ:
                return out
            t = n.succ[0]
            if loop is not None and (t == loop[0] or t not in loop[1]):
                out += self.jump(t, loop, stop)
                return out
            self._next = t
            return out
        if n.kind == "branch":
            targets = [t for _c, t in n.branches]
        else:
            targets = list(n.table.values()) + ([n.term.extra["default"]] if n.term.extra.get("default") else [])
        join, skip = self.choose_join(v, targets, loop)
        if stop is not None and join is not None and join != stop and stop in self.reachable(v) \
                and (join not in self.reachable(v) or join in self.reachable(stop)):
            join, skip = stop, None        # the enclosing construct continues at `stop`: don't run past it
        flag = None
        if skip is not None:
            self.flag_no += 1
            flag = "skip%d" % self.flag_no if self.flag_no > 1 else "skip"
            self.flags.append(flag)
            out.append(("setflag", flag, False))
            self.skip_stack.append((skip, flag))
        if n.kind == "branch":
            arms = [(c, self.arm(t, join, loop, skip, flag)) for c, t in n.branches]
            if flag:
                self.skip_stack.pop()
            out.append(("if", [(c, b) for c, b in arms[:-1]], arms[-1][1]))
            self._next = join
            if flag:
                out += self.guarded(flag, join, skip, loop)
            return out
        groups = defaultdict(list)
        for val, t in sorted(n.table.items()):
            groups[t].append(val)
        default = n.term.extra.get("default")
        cases = []
        for t, vals in sorted(groups.items(), key=lambda kv: min(kv[1])):
            if t == default:
                continue
            cases.append((vals, self.arm(t, join, loop, skip, flag)))
        dbody = self.arm(default, join, loop, skip, flag) if default is not None else None
        if flag:
            self.skip_stack.pop()
        out.append(("switch", n.term.e, cases, dbody))
        self._next = join
        if flag:
            out += self.guarded(flag, join, skip, loop)
        return out

    def guarded(self, flag, m, j, loop):
        """`if not skip then <code from M up to J> end`, then the sequence goes on at J."""
        body = self.region(m, j, loop)
        self._next = j
        return [("if", [(("not", ("flag", flag)), body)], None)]

    def arm(self, t, join, loop, skip=None, flag=None):
        if t is None or t == join:
            return []
        if loop is not None and (t == loop[0] or t not in loop[1]):
            return self.jump(t, loop, join)
        save = self._next
        r = self.region(t, join, loop)
        self._next = save
        return r


class _GraphShim:
    def __init__(self, nodes, entry):
        self.blocks, self.entry = nodes, entry


def _atom_key(c):
    """(atom, negated): a condition and its negation share one atom."""
    if c[0] == "not":
        k, neg = _atom_key(c[1])
        return k, not neg
    if c[0] == "cmp" and c[1] in ("~=", ">=", ">", "u>=", "u>", "f~="):
        inv = simp(("not", c))
        return inv, True
    return c, False


def _sat_atom(a, x):
    """Truth of atom a when its left side is the integer x (atoms on one variable vs constants)."""
    rel, b = a[1], a[3][1]
    ux, ub = x & M32, b & M32
    return {"==": x == b, "<": x < b, "<=": x <= b, "u<": ux < ub, "u<=": ux <= ub}.get(rel)


def _consistent(atoms, val):
    groups = defaultdict(list)
    for a, v in zip(atoms, val):
        if a[0] == "cmp" and a[1] in ("==", "<", "<=", "u<", "u<=") and is_c(a[3]):
            groups[a[2]].append((a, v))
    for lhs, items in groups.items():
        if len(items) < 2:
            continue
        cands = {0, -1, 1, 0x7FFFFFFF, -0x80000000}
        for a, _v in items:
            cands |= {a[3][1], a[3][1] - 1, a[3][1] + 1}
        if not any(all(_sat_atom(a, x) == v for a, v in items) for x in cands):
            return False
    return True


def _minimise(on, dc, n):
    """Quine-McCluskey (small n): prime implicants covering `on` -> list of terms [(atom index, value)]."""
    if not on:
        return []
    if len(on) + len(dc) == (1 << n):
        return [[]]
    terms = {(b, 0) for b in set(on) | set(dc)}       # (value bits, mask of dropped bits)
    primes = set()
    while terms:
        nxt = set()
        used = set()
        tl = sorted(terms)
        bymask = defaultdict(list)
        for t in tl:
            bymask[t[1]].append(t)
        for mask, ts in bymask.items():
            s = set(ts)
            for (v, m) in ts:
                for k in range(n):
                    bit = 1 << k
                    if m & bit or v & bit:
                        continue
                    o = (v | bit, m)
                    if o in s:
                        nxt.add((v, m | bit))
                        used.add((v, m))
                        used.add(o)
        primes |= terms - used
        terms = nxt
    # cover `on` greedily with primes (essential first)
    def covers(p, b):
        return (b & ~p[1]) == (p[0] & ~p[1])
    left = set(on)
    chosen = []
    while left:
        best = max(sorted(primes), key=lambda p: (sum(1 for b in left if covers(p, b)), bin(p[1]).count("1")))
        chosen.append(best)
        left = {b for b in left if not covers(best, b)}
    out = []
    for v, m in chosen:
        out.append([(k, (v >> k) & 1 == 1) for k in range(n) if not (m >> k) & 1])
    return out


def _formula_expr(terms, atoms):
    ors = []
    for t in terms:
        ands = [atoms[k] if val else simp(("not", atoms[k])) for k, val in t]
        if not ands:
            return ("cmp", "==", C0, C0)
        e = ands[0]
        for a in ands[1:]:
            e = ("and", e, a)
        ors.append(e)
    e = ors[0]
    for o in ors[1:]:
        e = ("or", e, o)
    return e


def _tidy(body):
    """Small AST clean-ups: loops into while / repeat, empty arms, `if c then return end` chains."""
    out = []
    for node in body:
        k = node[0]
        if k == "if":
            arms = [(c, _tidy(b)) for c, b in node[1]]
            els = _tidy(node[2]) if node[2] else []
            # drop empty arms
            if len(arms) == 1 and not arms[0][1] and els:
                arms = [(simp(("not", arms[0][0])), els)]
                els = []
            if len(arms) == 1 and not arms[0][1] and not els and not has_call(arms[0][0]):
                continue
            # else { if ... } -> elseif
            while els and len(els) == 1 and els[0][0] == "if":
                arms += els[0][1]
                els = els[0][2] or []
            if len(arms) > 1 and not arms[0][1]:
                # `if c0 then <nothing> elseif ... end` = `if not c0 then <the rest> end` (folding c0 into the next
                # condition alone would send the c0 case into the else)
                c0 = simp(("not", arms[0][0]))
                if len(arms) == 2 and not els:
                    arms = [(("and", c0, arms[1][0]), arms[1][1])]
                else:
                    arms = [(c0, _tidy([("if", arms[1:], els)]))]
                    els = []
            if len(arms) > 1 and not arms[-1][1] and els:
                arms[-1] = (simp(("not", arms[-1][0])), els)
                els = []
            while len(arms) > 1 and not arms[-1][1] and not els and not has_call(arms[-1][0]):
                arms.pop()
            out.append(("if", arms, els))
        elif k == "while":
            b = _tidy(node[2])
            while b and b[-1][0] == "continue":
                b = b[:-1]
            cond = None
            if b and b[0][0] == "if" and len(b[0][1]) == 1 and not b[0][2] and b[0][1][0][1] == [("break",)]:
                cond = simp(("not", b[0][1][0][0]))
                b = b[1:]
                out.append(("while", cond, b))
                continue
            # body ends `if c then break end` (no other continue) -> repeat ... until c
            if b and b[-1][0] == "if" and len(b[-1][1]) == 1 and not b[-1][2] and b[-1][1][0][1] == [("break",)] \
                    and not _has_continue(b[:-1]):
                out.append(("repeat", b[:-1], b[-1][1][0][0]))
                continue
            if b and b[-1][0] == "if" and len(b[-1][1]) == 1 and b[-1][2] == [("break",)] \
                    and not _has_continue(b[:-1]) and all(x[0] == "continue" for x in b[-1][1][0][1]):
                out.append(("repeat", b[:-1], simp(("not", b[-1][1][0][0]))))
                continue
            out.append(("while", None, b))
        elif k == "switch":
            out.append(("switch", node[1], [(v, _tidy(b)) for v, b in node[2]], _tidy(node[3]) if node[3] else None))
        elif k == "repeat":
            out.append(("repeat", _tidy(node[1]), node[2]))
        else:
            out.append(node)
            if k in ("return", "break", "goto", "continue"):
                break
    return out


def _invisible_gotos(body):
    """Targets of gotos whose label Lua can't see from the goto (a label in a nested block that doesn't enclose it)."""
    bad = set()

    def labels_of(block):
        return {n[1] for n in block if n[0] == "label"}

    def walk(block, visible):
        vis = visible | labels_of(block)
        for n in block:
            k = n[0]
            if k == "goto" and n[1] not in vis:
                bad.add(n[1])
            elif k == "if":
                for _c, b in n[1]:
                    walk(b, vis)
                if n[2]:
                    walk(n[2], vis)
            elif k == "while":
                walk(n[2], vis)
            elif k == "repeat":
                walk(n[1], vis)
            elif k == "switch":
                for _v, b in n[2]:
                    walk(b, vis)
                if n[3]:
                    walk(n[3], vis)
    walk(body, set())
    return bad


def _has_continue(body):
    for n in body:
        if n[0] == "continue":
            return True
        if n[0] == "if" and (any(_has_continue(b) for _c, b in n[1]) or (n[2] and _has_continue(n[2]))):
            return True
        if n[0] == "switch" and (any(_has_continue(b) for _v, b in n[2]) or (n[3] and _has_continue(n[3]))):
            return True
    return False


# ============================================================================================ generator =============
LUA_KEYWORDS = set("and break do else elseif end false for function goto if in local nil not or repeat return then "
                   "true until while".split())
RESERVED = LUA_KEYWORDS | {"api", "mem", "msg", "on", "game", "string", "math", "table", "X", "self", "_G", "_ENV",
                           "init", "update", "on_message", "print", "pairs", "ipairs", "type", "error", "select",
                           "sprintf", "crt_rand", "scratch", "ADDR", "ult", "ule", "ugt", "uge", "f32", "fr", "bits", "asfloat", "b2i", "sx8", "sx16",
                           "idiv", "imod", "udiv", "umod", "umulhi", "float", "qword", "STRINGS", "EVENTS", "REFS", "R",
                           "buffer", "frame", "call_ptr", "load", "save", "next", "setmetatable", "tostring", "tonumber", "D", "obj", "wp", "rgn", "mk"}


def _messages_table():
    """Message id -> (name, description, parameter names), from the ModKit editor's missionlogic.py (parsed as data:
    importing it would need the editor's command layer)."""
    import ast
    path = os.path.join(HERE, "..", "editor", "missionlogic.py")
    try:
        src = open(path, encoding="utf-8").read()
        m = re.search(r"^MESSAGES = (\{.*?^\})", src, re.S | re.M)
        return ast.literal_eval(m.group(1))
    except (OSError, AttributeError, ValueError, SyntaxError):
        return {}


def _ident(text, fallback="v"):
    t = re.sub(r"[^A-Za-z0-9_]", "_", text or "").strip("_").lower()
    t = re.sub(r"_+", "_", t)
    if not t or t[0].isdigit():
        t = fallback + ("_" + t if t else "")
    return t


def _param_word(text):
    """'the victim' -> victim, 'the switch (name hash)' -> switch."""
    t = re.sub(r"\(.*?\)", "", text or "").replace("the ", "").replace("?", "").strip()
    return _ident(t.split(" / ")[0], "a")


def _float_literal(v):
    if v != v:
        return "(0/0)"
    if v in (float("inf"), float("-inf")):
        return "math.huge" if v > 0 else "-math.huge"
    r = repr(float(v))
    if "." not in r and "e" not in r:
        r += ".0"
    return r


def _f32_short(v):
    """Shortest decimal that reads back as the same float32."""
    if v != v or v in (float("inf"), float("-inf")):
        return _float_literal(v)
    for digits in range(1, 10):
        s = "%.*g" % (digits, v)
        if struct.unpack("<f", struct.pack("<f", float(s)))[0] == v:
            r = repr(float(s))
            if "." not in r and "e" not in r:
                r += ".0"
            return r
    return _float_literal(v)


def _looks_float(bits):
    """An integer constant that is clearly a float's bit pattern (1.0, 0.5, 3000.0, 0.15 ...)."""
    bits &= M32
    if bits in (0, 0x80000000):
        return False
    e = (bits >> 23) & 0xFF
    if e < 0x60 or e > 0xA0:
        return False
    v = struct.unpack("<f", struct.pack("<I", bits))[0]
    s = _f32_short(v)
    return len(s.replace("-", "").replace(".", "").replace("e", "").lstrip("0").rstrip("0")) <= 7


def _all_exprs(s):
    out = []
    if s.kind == "copy":
        out += list(s.e)
    elif s.e is not None:
        out.append(s.e)
    if s.kind == "store" and s.target[0] == "mem":
        out.append(s.target[1])
    return out


def _map_expr(e, fn):
    """Bottom-up rewrite: fn(node) -> replacement or None."""
    if not isinstance(e, tuple) or not e:
        return e
    if e[0] == "call":
        e2 = ("call", _map_expr(e[1], fn), tuple(_map_expr(a, fn) for a in e[2]), e[3], e[4])
    else:
        e2 = (e[0],) + tuple(_map_expr(x, fn) if isinstance(x, tuple) else x for x in e[1:])
    r = fn(e2)
    return e2 if r is None else r


def _map_stmt(s, fn):
    if s.kind == "copy":
        s.e = tuple(_map_expr(x, fn) for x in s.e)
        return
    if s.e is not None:
        s.e = _map_expr(s.e, fn)
    if s.kind == "store" and s.target[0] == "mem":
        s.target = ("mem", _map_expr(s.target[1], fn), s.target[2])


class Generator:
    """IR -> Lua source for one DLL."""

    def __init__(self, path_or_bytes, name=None, slots=None, refs=None):
        self.tr = Translator(path_or_bytes, name, slots)
        self.prog = self.tr.prog
        self.stats = self.tr.stats
        self.msgs = _messages_table()
        self.refs = refs or {}            # kind -> {numeric id: editor ID} (refs_from_env)
        self.ast = {}
        self.vars = {}
        self.params = {}
        self.returns = set()
        self.slot_float = set()
        self.slot_used = set()
        self.strings = {}                 # rva -> text (literals handed to the game)
        self.taken_data = set()
        self.memory_abs = []
        self.gvars = {}
        self.fnames = {}
        self.labels_of = {}
        self.flags_of = {}
        self.ref_used = defaultdict(dict)        # table -> {editor ID: number} used by the logic
        self.scene_name = None
        self.objective_titles = []
        self.optimise()

    # ------------------------------------------------------------------------------------------ analysis --
    def optimise(self):
        tr = self.tr
        self.opt = {}
        for va, f in tr.funcs.items():
            o = FnOpt(f, tr.ir[va], True, self._entry_values(f))
            o.run()
            self.opt[va] = o
        game_facing = {"message", "save", "load", "size", "setup", "queue"}
        returns = {va for va, f in tr.funcs.items() if f.kind in game_facing}
        used_sites, tails = set(), defaultdict(set)
        for va, o in self.opt.items():
            for b in o.ir.values():
                for s in b:
                    top = strip_t(s.e) if s.kind in ("expr", "tail") and s.e is not None else None
                    for e in _all_exprs(s):
                        for c in _find_kind(strip_t(e), "call"):
                            if c[1][0] != "fn":
                                continue
                            if s.kind == "tail" and c == top:
                                tails[c[1][1]].add(va)
                            elif not (s.kind == "expr" and c == top):
                                used_sites.add(c[1][1])
        returns |= used_sites
        changed = True
        while changed:
            changed = False
            for callee, callers in tails.items():
                if callee not in returns and any(c in returns for c in callers):
                    returns.add(callee)
                    changed = True
        self.returns = returns
        for va, o in self.opt.items():
            if va not in returns:
                for bva, b in o.ir.items():
                    for k, s in enumerate(list(b)):
                        if s.kind == "ret" and s.e is not None:
                            if has_call(s.e):
                                # the value isn't wanted, the call (inlined into the return) still happens
                                b.insert(b.index(s), Stmt("expr", s.at, e=s.e))
                            s.e = None
                o.run()

    def find_scene(self):
        """The level's scene file name (the string handed to load_scene, API +0x44)."""
        for o in self.opt.values():
            for b in o.ir.values():
                for st in b:
                    for e in _all_exprs(st):
                        for c in _find_kind(strip_t(e), "call"):
                            if c[1] == ("slot", 0x44) and c[2] and strip_t(c[2][0])[0] == "gaddr":
                                return self.prog.dll.cstr(strip_t(c[2][0])[1])
        return None

    def _entry_values(self, f):
        ev = self.tr.entry_values(f)
        if f.kind == "setup":
            ev[_sl(4)] = ("api",)
        return ev

    # ------------------------------------------------------------------------------------------ globals --
    def collect_globals(self):
        p = self.prog
        acc = defaultdict(set)
        for va, o in self.opt.items():
            for b in o.ir.values():
                for s in b:
                    for e in _all_exprs(s):
                        for g in _find_kind(e, "g"):
                            acc[g[1]].add((g[2], g[3]))
                    if s.kind == "store" and s.target[0] == "g":
                        acc[s.target[1]].add((s.target[2], s.extra or ("u" if s.target[2] < 4 else "i")))
        self.gacc = acc
        # DLL data addresses: a string literal where it is handed over as it is (a call argument, a stored value);
        # data (a table, a structure) where the code computes with it or reads through it
        as_value, as_data = set(), set()

        def walk(e, top):
            if not isinstance(e, tuple) or not e:
                return
            if e[0] == "t":
                walk(e[2], top)
                return
            if e[0] == "gaddr":
                (as_value if top else as_data).add(e[1])
                return
            if e[0] == "call":
                walk(e[1], False)
                for a in e[2]:
                    walk(a, True)
                return
            for x in kids(e):
                walk(x, False)
        for va, o in self.opt.items():
            for b in o.ir.values():
                for s in b:
                    for e in _all_exprs(s):
                        walk(e, s.kind in ("set", "store") and e is s.e)
        for a in sorted(as_value | as_data):
            self._note_gaddr(a, a in as_data)
        self.save_range = None
        for va, o in self.opt.items():
            for b in o.ir.values():
                for s in b:
                    if s.kind == "copy":
                        d, srcv, n = (strip_t(x) for x in s.e)
                        g = d if d[0] == "gaddr" else srcv if srcv[0] == "gaddr" else None
                        if g is not None and is_c(n):
                            self.save_range = (g[1], g[1] + n[1])
                            self.taken_data.discard(g[1])

    def _note_gaddr(self, a, data=False):
        p = self.prog
        if p.dll.in_text(a) or not p.in_image(a):
            return
        if data:
            self.taken_data.add(a)
            return
        s = p.dll.cstr(a)
        prev = p.u8(a - 1)
        if s is not None and len(s) >= 1 and (p.in_rdata(a) or p.in_data(a)) and a not in self.gacc                 and (prev == 0 or a % 4 == 0 and prev is not None and not (32 <= prev < 127)):
            self.strings[a - p.base] = s
            return
        self.taken_data.add(a)

    def memory_ranges(self):
        p = self.prog
        out = []
        accessed = sorted(set(self.gacc))
        for a in sorted(self.taken_data):
            # (inside the save block too: its memory-backed fields are packed from the block's memory)
            sec_hi = next((hi for lo, hi, _n in p.sections if lo <= a < hi), a + 4)
            nxt = [x for x in sorted(self.taken_data) if x > a]
            hi = min(nxt + [sec_hi, a + 0x100])
            out.append([a, max(hi, a + 4)])
        # tables walked by pointer (`esi = table + 8; ... [esi - 8]; esi += 16; cmp esi, end`): extend to the end
        # address compared with and back to the lowest offset read through the pointer
        for va, o in self.opt.items():
            for b in o.ir.values():
                for s in b:
                    if s.kind != "cond":
                        continue
                    for c in _find_kind(strip_t(s.e), "cmp"):
                        r = strip_t(c[3])
                        if r[0] == "gaddr":
                            for rg in out:
                                if rg[0] <= r[1] <= rg[0] + 0x1000:
                                    rg[1] = max(rg[1], r[1])
        for rg in out:     # a table walked by pointer is read a little below where the pointer starts
            sec_lo = next((lo for lo, hi, _n in p.sections if lo <= rg[0] < hi), rg[0])
            rg[0] = max(sec_lo, rg[0] - 16)

        def merge(rs):
            m = []
            for lo, hi in sorted(rs):
                if m and lo <= m[-1][1]:
                    m[-1][1] = max(m[-1][1], hi)
                else:
                    m.append([lo, hi])
            return m

        # data the game reaches through pointers stored in that memory (the DLL's relocations): Mission9's zone object
        # table (0xE228) points to its group array (0xE208), which no code names - left out, the game read zeros
        # there (empty zone rosters, nobody activated)
        merged = merge(out)
        relocs = sorted(p.p.relocs)
        starts = sorted(set(self.taken_data))
        for _ in range(16):
            added = []
            for lo, hi in merged:
                for r in relocs:
                    if lo <= r < hi and lo <= r + 4 <= hi:
                        t = p.u32(r)
                        if t is None or not p.in_image(t) or p.dll.in_text(t) or any(a <= t < b for a, b in merged):
                            continue
                        sec_hi = next((h for l, h, _n in p.sections if l <= t < h), t + 4)
                        nxt = [x for x in starts if x > t]
                        added.append([t, max(min(nxt + [sec_hi, t + 0x100]), t + 4)])
            if not added:
                break
            merged = merge(merged + added)
        merged.append([p.obj, p.obj + 24])
        self.memory_abs = [tuple(x) for x in merged]

    def in_memory(self, a):
        return any(lo <= a < hi for lo, hi in self.memory_abs)

    def name_globals(self):
        p = self.prog
        used = set()
        names = {}
        hints = self.global_hints
        for a in sorted(self.gacc):
            sizes = self.gacc[a]
            size = max(sz for sz, _k in sizes)
            kinds = {k for _s, k in sizes}
            kind = "f" if "f" in kinds else ("u" if size < 4 else "i")
            rva = a - p.base
            nm = hints.get(a) or "%s_%X" % ({1: "b", 2: "w"}.get(size, "f" if kind == "f" else "g"), rva)
            base, k = nm, 2
            while nm in used or nm in RESERVED:
                nm = "%s_%d" % (base, k)
                k += 1
            used.add(nm)
            raw = p.bytes_at(a, size)
            if kind == "f" and size == 4:
                init = struct.unpack("<f", raw)[0]
            elif size == 4:
                init = s32(int.from_bytes(raw, "little"))
            else:
                init = int.from_bytes(raw, "little")
            names[a] = {"name": nm, "size": size, "kind": kind, "init": init, "mixed": len(kinds) > 1,
                        "memory": self.in_memory(a)}
        self.gvars = names

    def hint_globals(self):
        hints = {}
        upd = self.prog.cbs[1]
        o = self.opt.get(upd)
        if o:
            cnt = defaultdict(set)
            for b in o.ir.values():
                for s in b:
                    for c in _find_kind(strip_t(s.e), "cmp") if s.kind == "cond" else []:
                        l, r = c[2], c[3]
                        if l[0] == "g" and is_c(r):
                            cnt[l[1]].add(r[1])
            for a, vals in sorted(cnt.items(), key=lambda kv: -len(kv[1])):
                if len(vals) >= 2:
                    hints[a] = "mission_phase"
                    break
        n = 0
        for va in sorted(self.opt):
            for b in sorted(self.opt[va].ir):
                stmts = self.opt[va].ir[b]
                for k, s in enumerate(stmts):
                    if s.kind != "store" or s.target[0] != "g" or s.target[1] in hints:
                        continue
                    e = strip_t(s.e)
                    for l in locs_in(e):      # a register set by a call earlier in the block
                        for j in range(k - 1, -1, -1):
                            if stmts[j].kind == "set" and stmts[j].loc == l:
                                e = ("pair", e, strip_t(stmts[j].e))
                                break
                    calls = _find_kind(e, "call")
                    if not calls or calls[0][1][0] != "slot":
                        continue
                    nm = self.tr.slots.get(calls[0][1][1], {}).get("name")
                    if nm == "add_objective":
                        n += 1
                        hints[s.target[1]] = "objective_%d" % n
                        a0 = strip_t(calls[0][2][0]) if calls[0][2] else None
                        self.objective_titles.append((s.target[1], a0[1] & M32 if a0 and is_c(a0) else None))
                    elif nm and nm.startswith("get_") and va == self.prog.setup:
                        hints[s.target[1]] = nm[4:]
        self.global_hints = hints

    # ------------------------------------------------------------------------------------------ functions --
    def name_functions(self):
        tr, p = self.tr, self.prog
        names = {}
        fixed = {"update": "mission_update", "message": "mission_message", "save": "save_block",
                 "load": "load_block", "size": "save_block_size", "init": "mission_init", "setup": "level_setup"}
        # who calls whom, and from which message case
        from_msg = defaultdict(set)
        msgfn = p.cbs[2]
        o = self.opt.get(msgfn)
        if o:
            for b, stmts in o.ir.items():
                for s in stmts:
                    for e in _all_exprs(s):
                        for c in _find_kind(strip_t(e), "call"):
                            if c[1][0] == "fn":
                                from_msg[c[1][1]].add(b)
        case_of = {}
        if o:
            for b, stmts in o.ir.items():
                if stmts and stmts[-1].kind == "switch":
                    for val, t in stmts[-1].extra["values"].items():
                        case_of.setdefault(t, set()).add(val)
        used = set(RESERVED)
        for va, f in sorted(tr.funcs.items()):
            nm = fixed.get(f.kind)
            if nm is None and f.kind == "queue":
                nm = self._queue_name(va)
            if nm is None and va in from_msg:
                ids = set()
                for b in from_msg[va]:
                    ids |= case_of.get(b, set())
                if len(ids) == 1:
                    mid = next(iter(ids))
                    nm = "on_" + self.msgs.get(mid, ("msg_%02x" % mid,))[0]
            if nm is None:
                nm = "f_%X" % (va - p.base)
            base, k = nm, 2
            while nm in used:
                nm = "%s_%d" % (base, k)
                k += 1
            used.add(nm)
            names[va] = nm
        self.fnames = names

    def _queue_name(self, va):
        """A queue_call callback named after the API slot it calls (`set_view_camera` -> cb_set_view_camera)."""
        o = self.opt[va]
        slots = []
        for b in o.ir.values():
            for s in b:
                for e in _all_exprs(s):
                    for c in _find_kind(strip_t(e), "call"):
                        if c[1][0] == "slot":
                            slots.append(c[1][1])
        slots = list(dict.fromkeys(slots))
        if not slots:
            return None
        names = [self.tr.slots.get(x, {}).get("name") or "slot_%03X" % x for x in slots[-1:]]
        return "cb_" + names[0]

    # ------------------------------------------------------------------------------------------ rewrites --
    def special(self, va):
        """Message parameters, queued-callback event fields, the API-table store in LevelSetup."""
        f = self.tr.funcs[va]
        o = self.opt[va]
        if f.kind == "message":
            def m(e):
                if e[0] == "mem" and e[2] == 4:
                    a = strip_t(e[1])
                    if a == ("param", 1):
                        return ("msg", 0, e[3])
                    if a[0] == "bin" and a[1] == "+" and a[2] == ("param", 1) and is_c(a[3]) and a[3][1] % 4 == 0:
                        return ("msg", a[3][1] // 4, e[3])
                return None
            for b in o.ir.values():
                for s in b:
                    _map_stmt(s, m)
                    if s.kind == "store" and s.target[0] == "mem":
                        t = m(("mem", s.target[1], s.target[2], "i"))
                        if t is not None:
                            s.kind, s.target = "store", ("msg", t[1])
        if f.kind == "queue":
            ok = True
            fields = set()

            def chk(e):
                nonlocal ok
                if not isinstance(e, tuple) or not e:
                    return
                if e[0] == "mem":
                    a = strip_t(e[1])
                    if a[0] == "bin" and a[1] == "+" and a[2] == ("param", 0) and is_c(a[3]) and a[3][1] in (0x34, 0x38):
                        fields.add(a[3][1])
                        return
                if e == ("param", 0):
                    ok = False
                    return
                for x in kids(e):
                    chk(strip_t(x) if x and x[0] == "t" else x)
            for b in o.ir.values():
                for s in b:
                    for e in _all_exprs(s):
                        chk(strip_t(e))
                    if s.kind == "set" and s.loc.startswith("s") and not s.loc.startswith("sm"):
                        pass
            f.event_form = ok and bool(fields)
            if f.event_form:
                def m2(e):
                    if e[0] == "mem" and e[2] == 4:
                        a = strip_t(e[1])
                        if a[0] == "bin" and a[1] == "+" and a[2] == ("param", 0) and is_c(a[3]) and a[3][1] in (0x34, 0x38):
                            return ("evt", a[3][1])
                    return None
                for b in o.ir.values():
                    for s in b:
                        _map_stmt(s, m2)
        if f.kind == "setup":
            for b in o.ir.values():
                b[:] = [s for s in b if not (s.kind == "store" and s.target[0] == "apiglobal")]

    def recover_vars(self, va):
        """Locations left after propagation -> Lua locals, one per def-use web."""
        f, o = self.tr.funcs[va], self.opt[va]
        ins = o.rd()
        parent = {}

        def find(x):
            while parent.get(x, x) != x:
                parent[x] = parent.get(parent[x], parent[x])
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
        use_defs = {}
        for b in o.order:
            st = dict(ins.get(b) or {})
            for k, s in enumerate(o.ir[b]):
                for l in stmt_uses(s):
                    ds = sorted(st.get(l, frozenset([("in", l)])), key=str)
                    use_defs[(b, k, l)] = ds
                    for d in ds:
                        parent.setdefault(d, d)
                    for d in ds[1:]:
                        union(ds[0], d)
                if s.kind == "set":
                    d = (b, k)
                    parent.setdefault(d, d)
                    st[s.loc] = frozenset([d])
        webs = defaultdict(list)
        for d in parent:
            webs[find(d)].append(d)
        if len(webs) > 150:
            # Lua allows 200 locals per function: one variable per location instead of one per def-use web
            def loc_of(d):
                return d[1] if d[0] == "in" else o.ir[d[0]][d[1]].loc
            by_loc = {}
            for d in list(parent):
                l = loc_of(d)
                if l in by_loc:
                    union(by_loc[l], d)
                else:
                    by_loc[l] = d
            webs = defaultdict(list)
            for d in parent:
                webs[find(d)].append(d)
            self.stats["functions_with_merged_locals"] += 1
        # names
        used = set(RESERVED) | set(self.fnames.values()) | {g["name"] for g in self.gvars.values()}
        pnames = self.param_names(va)
        used |= set(pnames)
        web_name = {}
        locals_ = []
        undefined = 0
        for root, ds in sorted(webs.items(), key=lambda kv: str(kv[0])):
            pk = [d for d in ds if d[0] == "in" and d[1].startswith("s") and not d[1].startswith("sm")
                  and (int(d[1][1:]) - 4) // 4 < len(pnames)]
            if pk:
                web_name[root] = pnames[(int(pk[0][1][1:]) - 4) // 4]
                continue
            if any(d[0] == "in" for d in ds):
                undefined += 1
            nm = self._var_name(o, ds)
            base, k = nm, 2
            while nm in used:
                nm = "%s%d" % (base, k)
                k += 1
            used.add(nm)
            web_name[root] = nm
            locals_.append((nm, any(d[0] == "in" for d in ds)))
        self.stats["undefined_reads"] += undefined
        # rewrite
        for b in o.order:
            for k, s in enumerate(o.ir[b]):
                m = {}
                for l in stmt_uses(s):
                    ds = use_defs.get((b, k, l))
                    if ds:
                        m[l] = ("var", web_name[find(ds[0])])
                if m:
                    _subst_stmt(s, m)
                if s.kind == "set":
                    s.loc = web_name[find((b, k))]
        self.vars[va] = locals_

    def param_names(self, va):
        if va in self.params:
            return self.params[va]
        f = self.tr.funcs[va]
        n = f.nparams
        names = None
        if f.kind == "message":
            names = ["id", "msg"]
        elif f.kind in ("save", "load"):
            names = ["buf"]
        elif f.kind == "queue" and getattr(f, "event_form", False):
            names = []
        elif f.kind == "queue":
            names = ["event"]
        elif f.kind == "setup":
            names = ["api_table"]
        if names is None:
            names = []
            nm = self.fnames.get(va, "")
            mid = None
            if nm.startswith("on_"):
                for k, (mname, _d, ps) in self.msgs.items():
                    if "on_" + mname == nm.split("_2")[0] or "on_" + mname == nm:
                        mid = k
                        names = [_param_word(x) for x in ps]
                        break
            names = names[:n]
            while len(names) < n:
                names.append("arg%d" % (len(names) + 1))
            seen = set()
            for k, x in enumerate(names):
                if x in seen or x in RESERVED:
                    names[k] = "arg%d" % (k + 1)
                seen.add(names[k])
        self.params[va] = names
        return names

    def _var_name(self, o, ds):
        for d in ds:
            if d[0] == "in":
                continue
            b, k = d
            e = strip_t(o.ir[b][k].e)
            if e is None:
                continue
            c = _find_kind(e, "call")
            if e[0] == "call" and e[1][0] == "slot":
                nm = self.tr.slots.get(e[1][1], {}).get("name")
                if nm:
                    for pre in ("get_", "find_", "is_", "has_", "count_"):
                        if nm.startswith(pre):
                            nm = nm[len(pre):] if pre in ("get_", "find_") else nm
                            break
                    return _ident(nm)
                return "result"
            if e[0] == "call":
                return "result"
            if e[0] == "g":
                g = self.gvars.get(e[1])
                return "cur_" + (g["name"] if g else "value")
            if e[0] == "gaddr":
                return "p"
            if e[0] in ("c",) or (e[0] == "bin" and e[1] in ("+", "-")):
                continue
            if e[0] in ("fr", "fc", "i2f"):
                return "x"
        if any(d[0] != "in" and strip_t(o.ir[d[0]][d[1]].e)[0] in ("c", "bin") for d in ds):
            return "i"
        return "v"

    # ------------------------------------------------------------------------------------------ main --
    def generate(self, level=None):
        tr = self.tr
        self.collect_globals()
        self.scene_name = self.find_scene()
        self.hint_globals()
        self.memory_ranges()
        self.name_globals()
        self.name_functions()
        for va in tr.funcs:
            self.special(va)
        for va in tr.funcs:
            self.param_names(va)
        for va, f in tr.funcs.items():
            self.recover_vars(va)
            # structure; a goto whose label would not be visible in Lua (inside another block) is redone with that
            # target repeated instead
            no_goto = set()
            for _attempt in range(8):
                st = Structurer(tr, f, self.opt[va].ir, defaultdict(int))
                st.no_goto = set(no_goto)
                st.run()
                labels = set(st.labels)
                st2 = Structurer(tr, f, self.opt[va].ir, defaultdict(int))
                st2.no_goto = set(no_goto)
                st2.labels_final = labels
                ast = st2.run()
                bad = _invisible_gotos(ast)
                if not bad:
                    break
                no_goto |= bad
            st = Structurer(tr, f, self.opt[va].ir, self.stats)
            st.no_goto = set(no_goto)
            st.labels_final = labels
            self.ast[va] = st.run()
            if _invisible_gotos(self.ast[va]):
                self.stats["invalid_gotos"] += len(_invisible_gotos(self.ast[va]))
            self.labels_of[va] = st.labels
            self.flags_of[va] = list(st.flags)
        self.name_phase_functions()
        return Printer(self, level).emit()

    PHASE_NAMES = {0: "intro_update", 1: "overview_update", 2: "play_update"}

    def name_phase_functions(self):
        """The update callback's state machine: `if mission_phase == 0 then return f() ...` -> f is intro_update,
        1 overview_update, 2 play_update (the mission DLLs' phase global: 0 intro film, 1 overview, 2 playing)."""
        phase = next((a for a, n in self.global_hints.items() if n == "mission_phase"), None)
        upd = self.prog.cbs[1]
        if phase is None or upd not in self.ast:
            return
        found = {}

        def note(arm, v):
            calls = [x for x in arm if x[0] == "return" and x[1] is not None]     # `return f()`: the dispatch
            for x in calls:
                e = strip_t(x[1])
                if e is not None and e[0] == "call" and e[1][0] == "fn":
                    found[e[1][1]] = v
                    return

        def walk(body):
            for node in body:
                if node[0] != "if":
                    continue
                for c, arm in node[1]:
                    c = strip_t(c)
                    if c[0] == "cmp" and c[1] == "==" and strip_t(c[2])[:2] == ("g", phase) and is_c(c[3]):
                        note(arm, c[3][1])
                    if c[0] == "cmp" and c[1] == "~=" and strip_t(c[2])[:2] == ("g", phase) and is_c(c[3]) \
                            and node[2] and c == strip_t(node[1][-1][0]):
                        note(node[2], c[3][1])          # if phase ~= N then ... else <phase N> end
                    walk(arm)
                if node[2]:
                    walk(node[2])
        walk(self.ast[upd])
        used = set(self.fnames.values())
        for va, v in found.items():
            nm = self.PHASE_NAMES.get(v)
            if nm and nm not in used and self.fnames.get(va, "").startswith("f_"):
                used.discard(self.fnames[va])
                self.fnames[va] = nm
                used.add(nm)


# ============================================================================================ printer ===============
OBJECT_WORDS = {"victim", "killer", "soldier", "item", "unit", "object", "vehicle", "door", "enemy", "observer",
                "target", "attacker", "giver", "receiver", "launcher", "weapon", "shooter"}
REF_ALIAS = {"objects": "obj", "waypoints": "wp", "regions": "rgn", "markers": "mk"}


def _ref_ident(text, used):
    t = re.sub(r"[^A-Za-z0-9_]", "_", text or "").strip("_")
    t = re.sub(r"_+", "_", t)
    if not t:
        t = "ref"
    if t[0].isdigit():
        t = "_" + t
    base, k = t, 2
    while t in used or t in LUA_KEYWORDS:
        t = "%s_%d" % (base, k)
        k += 1
    used.add(t)
    return t


def refs_from_env(env_bytes):
    """Editor IDs for the numbers a mission DLL uses, from the level's scene (<level>.ENV): objects by HLOI script
    id (the record's stat name - TENT_GUARD, BLACKHAWK_PICKUP - or unit + id when the stat is only a number),
    waypoints by id (NEWENVWAYPT name), regions by their index in the REGN list -> {kind: {number: editor ID}}."""
    from . import env as envmod
    e = envmod.EnvFile.read(env_bytes)
    out = {"objects": {}, "waypoints": {}, "regions": {}, "markers": {}}
    used = set()
    hloi = sorted(e.find("HLOI"), key=lambda r: r.as_dict().get("script_id", 0))
    for r in hloi:
        d = r.as_dict()
        sid, stat, unit = d.get("script_id"), str(d.get("stat") or ""), str(d.get("unit") or "")
        if sid is None or sid < 0 or sid in out["objects"]:
            continue
        if stat and stat.upper() != "NONE" and re.search(r"[A-Za-z]", stat):
            nm = stat
        else:
            u = re.sub(r"^(IR|US|UK|NU|RU)_(VEH|PROP|STRCT|DOOR|WPN)_", "", unit)
            nm = "%s_%d" % (u, sid)
        out["objects"][sid] = _ref_ident(nm, used)
    used_w = set()
    for r in e.find("NEWENVWAYPT"):
        d = r.as_dict()
        if d.get("id") is not None and d["id"] not in out["waypoints"]:
            out["waypoints"][d["id"]] = _ref_ident(d.get("name") or "WP_%d" % d["id"], used_w)
    used_r = set()
    for k, r in enumerate(e.find("REGN")):
        out["regions"][k] = _ref_ident(r.as_dict().get("name") or "REGION_%d" % k, used_r)
    return out


PREC = {"or": 1, "and": 2, "cmp": 3, "|": 4, "~": 5, "&": 6, "<<": 7, ">>": 7, "+": 9, "-": 9, "*": 10, "/": 10,
        "//": 10, "%": 10, "un": 11, "atom": 13}


def lua_str(s):
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif 32 <= o < 127:
            out.append(ch)
        else:
            out.append("\\%d" % o)
    out.append('"')
    return "".join(out)


def _hex(v):
    return "0x%X" % v


def _int_lit(v):
    if -0x10000 < v < 0x10000:
        return str(v)
    if v >= 0:
        return _hex(v)
    return "-" + _hex(-v)


class Printer:
    def __init__(self, g: Generator, level=None):
        self.g, self.tr, self.p = g, g.tr, g.prog
        self.level = level or os.path.splitext(self.p.name)[0].lower()
        self.stats = g.stats
        self.cur = None
        self.lines = []

    # ------------------------------------------------------------------------------------------ expressions --
    def ex(self, e, prec=0, hint=None):
        s, p = self._ex(e, hint)
        return "(%s)" % s if p < prec else s

    def _ex(self, e, hint=None):
        k = e[0]
        g = self.g
        if k == "t":
            return self._ex(e[2], hint)
        if k == "c":
            v = e[1]
            if hint in ("float", "time_ms") and _looks_float(v):
                return _f32_short(struct.unpack("<f", struct.pack("<I", v & M32))[0]), PREC["atom"]
            if hint == "u32":
                if -0x10000 < v < 0:
                    return str(v), PREC["un"]
                if v < 0 or v >= 0x10000:
                    return _hex(v & M32), PREC["atom"]
                if _looks_float(v):
                    return _f32_short(struct.unpack("<f", struct.pack("<I", v & M32))[0]), PREC["atom"]
                return str(v), PREC["atom"]
            if hint and hint.startswith(("ref:", "cref:")):
                r = self.ref_name(hint.split(":")[1], v)
                if r:
                    return r, PREC["atom"]
                if hint.startswith("ref:") and (v <= -0x10000 or v >= 0x10000):
                    return _hex(v & M32), PREC["atom"]
            s = _int_lit(v)
            return s, PREC["atom"] if v >= 0 else PREC["un"]
        if k == "fc":
            v = e[1]
            short = _f32_short(v)
            if float(short) == v or hint in ("float", "u32", "time_ms"):
                s = short
            else:
                s = "f32(%s)" % short
            return s, PREC["atom"] if not s.startswith("-") else PREC["un"]
        if k == "param":
            if e[1] == 1 and self.tr.funcs[self.cur].kind == "message":
                return "msg.p", PREC["atom"]        # the parameter block's address itself
            names = g.params.get(self.cur, [])
            return (names[e[1]] if e[1] < len(names) else "arg%d" % (e[1] + 1)), PREC["atom"]
        if k == "var":
            return e[1], PREC["atom"]
        if k == "loc":
            self.stats["unresolved_locations"] += 1
            return "nil --[[%s]]" % e[1], PREC["atom"]
        if k == "g":
            gv = g.gvars.get(e[1])
            if gv is None:
                self.stats["raw_memory"] += 1
                fn = {1: "u8", 2: "u16", 4: "f32" if e[3] == "f" else "i32", 8: "f64"}[e[2]]
                return "mem.%s(ADDR(%s))" % (fn, _hex(e[1] - self.p.base)), PREC["atom"]
            if gv["memory"]:
                self.stats["memory_globals"] += 1
                return "D.%s" % self.dfield(e[1], e[2], e[3]), PREC["atom"]
            nm = gv["name"]
            if e[3] == "f" and gv["kind"] != "f":
                return "asfloat(%s)" % nm, PREC["atom"]
            if e[3] != "f" and gv["kind"] == "f":
                return "bits(%s)" % nm, PREC["atom"]
            return nm, PREC["atom"]
        if k == "gaddr":
            rva = e[1] - self.p.base
            if rva in g.strings and hint != "addr":
                return lua_str(g.strings[rva]), PREC["atom"]
            for b in sorted(g.strings):
                if b < rva < b + len(g.strings[b]):
                    return "ADDR(%s)" % _hex(rva), PREC["atom"]
            return "ADDR(%s)" % _hex(rva), PREC["atom"]
        if k == "saddr":
            return self.buffer_expr(e[1]), PREC["+"]
        if k == "api":
            return "API_TABLE", PREC["atom"]
        if k == "apislotptr":
            return "api_slot(%s)" % _hex(e[1]), PREC["atom"]
        if k == "apislotaddr":
            return "(API_TABLE + %s)" % _hex(e[1]), PREC["atom"]
        if k == "fnptr":
            return g.fnames.get(e[1], "f_%X" % (e[1] - self.p.base)), PREC["atom"]
        if k == "mem":
            a = strip_t(e[1])
            fn = {1: "u8", 2: "u16", 4: "f32" if e[3] == "f" else "i32", 8: "f64"}[e[2]]
            if _has_kind(a, "saddr"):
                self.stats["buffer_access"] += 1
            else:
                self.stats["raw_memory"] += 1
            return "mem.%s(%s)" % (fn, self.ex(a)), PREC["atom"]
        if k == "msg":
            if e[2] == "f":
                return "msg:float(%d)" % e[1], PREC["atom"]
            return "msg[%d]" % e[1], PREC["atom"]
        if k == "evt":
            return ("a" if e[1] == 0x34 else "b"), PREC["atom"]
        if k == "str":
            return lua_str(e[1]), PREC["atom"]
        if k == "undef":
            self.stats["undefined_values"] += 1
            return "0 --[[undefined %s]]" % e[1], PREC["atom"]
        if k == "bin":
            op, a, b = e[1], e[2], e[3]
            if op in ("+", "-", "*", "/"):
                pr = PREC[op]
                ha = "addr" if strip_t(a)[0] == "gaddr" else None
                hb = "addr" if strip_t(b)[0] == "gaddr" else None
                return "%s %s %s" % (self.ex(a, pr, ha), op, self.ex(b, pr + 1, hb)), pr
            if op in ("&", "|", "~"):
                pr = PREC[op]
                bs = self.mask_lit(b) if is_c(b) else self.ex(b, pr + 1)
                return "%s %s %s" % (self.ex(a, pr), op, bs), pr
            if op == "<<":
                return "%s << %s" % (self.ex(a, 8), self.ex(b, 8)), PREC["<<"]
            if op == "shr":
                r = vrange(a)
                if r and r[0] >= 0:
                    return "%s >> %s" % (self.ex(a, 8), self.ex(b, 8)), PREC[">>"]
                return "(%s & 0xFFFFFFFF) >> %s" % (self.ex(a, 7), self.ex(b, 8)), PREC[">>"]
            if op == "sar":
                if is_c(b):
                    return "%s // %d" % (self.ex(a, 10), 1 << b[1]), PREC["//"]
                return "%s // (1 << %s)" % (self.ex(a, 10), self.ex(b)), PREC["//"]
            if op in ("idiv", "imod", "udiv", "umod", "umulhi"):
                return "%s(%s, %s)" % (op, self.ex(a), self.ex(b)), PREC["atom"]
            raise Unsupported("operator %s" % op)
        if k == "un":
            inner = self.ex(e[2], PREC["un"])
            if inner.startswith(("-", "~")):
                inner = "(%s)" % inner        # `--x` would start a comment
            if e[1] == "neg":
                return "-%s" % inner, PREC["un"]
            return "~%s" % inner, PREC["un"]
        if k == "cmp":
            rel, a, b = e[1], e[2], e[3]
            if a == C0 and b == C0 and rel in ("==", "~="):
                return ("true" if rel == "==" else "false"), PREC["atom"]
            rk = self.is_ref_value(a)
            hint2 = ("cref:" + rk) if rk else None
            if rel in ("==", "~=") and is_c(b) and strip_t(a) == ("param", 0) and                     self.tr.funcs[self.cur].kind == "message":
                return "%s %s EVENTS.%s" % (self.ex(a, 4), rel, self.event_name(b[1])), PREC["cmp"]
            if rel in ("==", "~=", "<", "<=", ">", ">="):
                if strip_t(b)[0] == "gaddr":
                    hint2 = "addr"
                return "%s %s %s" % (self.ex(a, 4, "addr" if strip_t(a)[0] == "gaddr" else None), rel,
                                     self.ex(b, 4, hint2)), PREC["cmp"]
            if rel.startswith("f"):
                return "%s %s %s" % (self.ex(a, 4), rel[1:], self.ex(b, 4)), PREC["cmp"]
            if rel in ("u<", "u<=", "u>", "u>="):      # unsigned 32-bit comparisons (helpers in the runtime)
                fn = {"u<": "ult", "u<=": "ule", "u>": "ugt", "u>=": "uge"}[rel]
                return "%s(%s, %s)" % (fn, self.ex(a), self.ex(b)), PREC["atom"]
            raise Unsupported("comparison %s" % rel)
        if k == "not":
            return "not %s" % self.ex(e[1], PREC["un"]), PREC["un"]
        if k == "and":
            return "%s and %s" % (self.ex(e[1], 2), self.ex(e[2], 3)), PREC["and"]
        if k == "or":
            return "%s or %s" % (self.ex(e[1], 1), self.ex(e[2], 2)), PREC["or"]
        if k == "b2i":
            return "b2i(%s)" % self.ex(e[1]), PREC["atom"]
        if k == "i2f":
            x = strip_t(e[1])
            if is_c(x):
                return _float_literal(float(x[1])), PREC["atom"]
            if x[0] == "qword":
                lo, hi = strip_t(x[1]), strip_t(x[2])
                if hi == C0:
                    return "float(%s & 0xFFFFFFFF)" % self.ex(lo, 6), PREC["atom"]
                return "qword(%s, %s)" % (self.ex(lo), self.ex(hi)), PREC["atom"]
            return "float(%s)" % self.ex(x), PREC["atom"]
        if k == "u2f":
            return "float(%s & 0xFFFFFFFF)" % self.ex(e[1], 6), PREC["atom"]
        if k == "fr":
            return "fr(%s)" % self.ex(e[1]), PREC["atom"]
        if k == "f32":
            x = strip_t(e[1])
            if x[0] == "fr":
                x = x[1]
            if x[0] == "fc":
                return self._ex(x)
            return "f32(%s)" % self.ex(x), PREC["atom"]
        if k == "fromfbits":
            x = strip_t(e[1])
            if is_float(x):
                return self._ex(x)
            if is_c(x):
                return _f32_short(struct.unpack("<f", struct.pack("<I", x[1] & M32))[0]), PREC["atom"]
            return "asfloat(%s)" % self.ex(x), PREC["atom"]
        if k == "fistp":
            return "ftoi(%s)" % self.ex(e[1]), PREC["atom"]
        if k == "sx":
            return "sx%d(%s)" % (8 * e[2], self.ex(e[1])), PREC["atom"]
        if k == "ext":
            x, sh, mask = e[1], e[2], e[3]
            if sh == 0:
                return "%s & %s" % (self.ex(x, 6), _hex(mask)), PREC["&"]
            return "(%s >> %d) & %s" % (self.ex(x, 8), sh, _hex(mask)), PREC["&"]
        if k == "ins":
            x, v, sh, mask = e[1], e[2], e[3], e[4]
            clear = ~(mask << sh)
            return "(%s & %s) | ((%s & %s) << %d)" % (self.ex(x, 6), self.mask_lit(C(clear)), self.ex(v, 6),
                                                       _hex(mask), sh), PREC["|"]
        if k in ("dlo", "dhi"):
            return "%s(%s)" % (k, self.ex(e[1])), PREC["atom"]
        if k == "qword":
            return "qword(%s, %s)" % (self.ex(e[1]), self.ex(e[2])), PREC["atom"]
        if k == "fsw":
            self.stats["unresolved_fpu_status"] += 1
            return "0", PREC["atom"]
        if k == "call":
            return self.call(e), PREC["atom"]
        if k == "pair":
            return ", ".join(self.ex(x) for x in e[1:]), PREC["atom"]
        if k == "flag":
            return e[1], PREC["atom"]
        raise Unsupported("expression %s" % (k,))

    def dfield(self, addr, size, kind):
        """The D.<name> field for a global that lives in the memory block (its address is handed to the game)."""
        gv = self.g.gvars[addr]
        nm = gv["name"]
        fn = {1: "u8", 2: "u16", 4: "f32" if kind == "f" else "i32"}[size]
        key = nm if fn in ("i32", "u8", "u16") or gv["kind"] == "f" else nm + "_f"
        self.dfields[key] = (addr - self.p.base, fn)
        return key

    def mask_lit(self, c):
        v = c[1]
        if v < 0 and v != -1:
            return "~" + _hex(~v)
        if v >= 0x10000:
            return _hex(v)
        return str(v) if v < 0x100 else _hex(v)

    def is_ref_value(self, a):
        """The reference kind of an expression (a message parameter / handler parameter naming an object or a
        waypoint), for comparisons with numbers: `victim == obj.HERO_FOLEY`."""
        a = strip_t(a)
        if a[0] == "msg":
            return None
        if a[0] == "param":
            names = self.g.params.get(self.cur, [])
            nm = names[a[1]] if a[1] < len(names) else ""
            if "waypoint" in nm:
                return "waypoint"
            if nm in OBJECT_WORDS:
                return "object"
        if a[0] == "call" and a[1][0] == "slot":
            ret = (self.tr.slots.get(a[1][1], {}).get("returns") or "").split(" ")[0]
            if ret in ("object", "waypoint"):
                return ret
        return None

    def ref_name(self, kind, v):
        """`obj.HERO_FOLEY` for a script id the level's scene names (refs.lua), else None."""
        table = {"object": "objects", "waypoint": "waypoints", "region": "regions", "marker": "markers"}[kind]
        names = self.g.refs.get(table, {})
        nm = names.get(v)
        if nm is None:
            return None
        self.g.ref_used[table][nm] = v
        self.stats["refs_named"] += 1
        return "%s.%s" % (REF_ALIAS[table], nm)

    def buffer_expr(self, off):
        bufs = self.buffers.get(self.cur, {})
        for lo, (nm, size) in bufs.items():
            if lo <= off < lo + size:
                d = off - lo
                return nm if d == 0 else "%s + %d" % (nm, d)
        self.stats["stack_addresses"] += 1
        return "scratch_at(%d)" % off

    # ------------------------------------------------------------------------------------------ calls --
    def call(self, e):
        tgt, args = e[1], [strip_t(a) for a in e[2]]
        g = self.g
        if tgt[0] == "slot":
            slot = tgt[1]
            g.slot_used.add(slot)
            if e[4] == "f":
                g.slot_float.add(slot)
            info = self.tr.slots.get(slot, {})
            types = [a.get("type") for a in info.get("args", [])]
            out = []
            for k, a in enumerate(args):
                t = types[k] if k < len(types) else None
                h = "float" if t in ("float",) else ("ref:" + t) if t in ("object", "waypoint", "marker", "region") \
                    else "u32"
                out.append(self.ex(a, 0, h))
            nm = self.api_name(slot, e[4] == "f")
            self.stats["api_calls_named" if info.get("name") else "api_calls_unnamed"] += 1
            return "api.%s(%s)" % (nm, ", ".join(out))
        if tgt[0] == "fn":
            nm = g.fnames.get(tgt[1], "f_%X" % tgt[1])
            return "%s(%s)" % (nm, ", ".join(self.ex(a) for a in args))
        if tgt[0] == "sprintf":
            return self.sprintf(args)
        if tgt[0] == "rand":
            return "crt_rand()"
        if tgt[0] == "ptr":
            self.stats["indirect_calls"] += 1
            return "call_ptr(%s%s)" % (self.ex(strip_t(tgt[1])), "".join(", " + self.ex(a) for a in args))
        raise Unsupported("call target %s" % (tgt,))

    def api_name(self, slot, fret):
        info = self.tr.slots.get(slot, {})
        nm = info.get("name") or "slot_%03X" % slot
        return nm + ("_f" if fret and slot in self.g.slot_mixed else "")

    def sprintf(self, args):
        if len(args) < 2:
            raise Unsupported("sprintf arguments")
        fmt = args[1]
        text = self.g.strings.get(fmt[1] - self.p.base) if fmt[0] == "gaddr" else None
        if text is None:
            # the format differs per path: C formatting at run time from the argument dwords
            return "csprintf(%s)" % ", ".join(self.ex(a) for a in args)
        out = [self.ex(args[0]), self.ex(fmt)]
        rest = args[2:]
        k = 0
        convs = re.findall(r"%[-+ #0]*(?:\d+|\*)?(?:\.\d+)?(?:hh|h|ll|l|L|I64)?([diouxXcspfeEgGn%])", text or "")
        for c in convs:
            if c == "%":
                continue
            if c in "feEgG" and k + 1 < len(rest):
                lo, hi = rest[k], rest[k + 1]
                if lo[0] == "dlo" and hi[0] == "dhi" and lo[1] == hi[1]:
                    out.append(self.ex(lo[1]))
                else:
                    out.append("double(%s, %s)" % (self.ex(lo), self.ex(hi)))
                k += 2
            elif k < len(rest):
                out.append(self.ex(rest[k]))
                k += 1
        return "sprintf(%s)" % ", ".join(out)

    # ------------------------------------------------------------------------------------------ statements --
    def stmt(self, s, ind):
        w = self.w
        if s.kind == "set":
            w(ind, "%s = %s" % (s.loc, self.ex(s.e)))
        elif s.kind == "expr":
            e = strip_t(s.e)
            calls = [e] if e[0] == "call" else _top_calls(e)
            for c in calls:
                w(ind, self.ex(c))
        elif s.kind == "store":
            t = s.target
            if t[0] == "g":
                gv = self.g.gvars.get(t[1])
                val = self.store_value(s.e, t[2], s.extra)
                if gv is None:
                    self.stats["raw_memory"] += 1
                    fn = {1: "w8", 2: "w16", 4: "wf" if s.extra == "f" else "w32"}[t[2]]
                    w(ind, "mem.%s(ADDR(%s), %s)" % (fn, _hex(t[1] - self.p.base), val))
                elif gv["memory"]:
                    self.stats["memory_globals"] += 1
                    w(ind, "D.%s = %s" % (self.dfield(t[1], t[2], "f" if s.extra == "f" else ("u" if t[2] < 4 else "i")),
                                          val))
                else:
                    w(ind, "%s = %s" % (gv["name"], val))
                    e = strip_t(s.e)
                    calls = _find_kind(e, "call")
                    if gv["name"].startswith("objective_") and calls and calls[0][1] == ("slot", 0x4B0) \
                            and calls[0][2] and is_c(strip_t(calls[0][2][0])):
                        self.objectives.append((gv["name"], strip_t(calls[0][2][0])[1] & M32))
            elif t[0] == "msg":
                w(ind, "msg[%d] = %s" % (t[1], self.ex(s.e)))
            elif t[0] == "mem":
                a = strip_t(t[1])
                if _has_kind(a, "saddr"):
                    self.stats["buffer_access"] += 1
                else:
                    self.stats["raw_memory"] += 1
                fn = {1: "w8", 2: "w16", 4: "wf" if s.extra == "f" else "w32", 8: "wd"}[t[2]]
                ev = strip_t(s.e)
                if t[2] == 4 and is_c(ev) and _looks_float(ev[1]) and _has_kind(a, "saddr"):
                    val = _f32_short(struct.unpack("<f", struct.pack("<I", ev[1] & M32))[0])   # same bits
                else:
                    val = self.store_value(s.e, t[2], s.extra)
                w(ind, "mem.%s(%s, %s)" % (fn, self.ex(a), val))
            else:
                raise Unsupported("store %s" % (t,))
        elif s.kind == "copy":
            d, src, n = (strip_t(x) for x in s.e)
            rng = self.g.save_range
            if rng and src == ("gaddr", rng[0]) and is_c(n) and n[1] == rng[1] - rng[0]:
                w(ind, "write_state(%s)" % self.ex(d))
            elif rng and d == ("gaddr", rng[0]) and is_c(n) and n[1] == rng[1] - rng[0]:
                w(ind, "read_state(%s)" % self.ex(src))
            else:
                self.stats["raw_memory"] += 1
                w(ind, "mem.copy(%s, %s, %s)" % (self.ex(d), self.ex(src), self.ex(n)))
        else:
            raise Unsupported("statement %s" % s.kind)

    def store_value(self, e, size, extra):
        e = strip_t(e)
        if extra == "f":
            return self.ex(("f32", e))
        if size < 4:
            r = vrange(e)
            if not (r and r[0] >= 0 and r[1] < (1 << (8 * size))):
                return "%s & %s" % (self.ex(e, 6), _hex((1 << (8 * size)) - 1))
        return self.ex(e)

    def block(self, body, ind, loop=None):
        for k, n in enumerate(body):
            kind = n[0]
            if kind == "s":
                self.stmt(n[1], ind)
            elif kind == "if":
                arms = n[1]
                for j, (c, b) in enumerate(arms):
                    self.w(ind, "%s %s then" % ("if" if j == 0 else "elseif", self.cond(c)))
                    self.block(b, ind + 1, loop)
                if n[2]:
                    self.w(ind, "else")
                    self.block(n[2], ind + 1, loop)
                self.w(ind, "end")
            elif kind == "while":
                lab = self.new_loop()
                self.w(ind, "while %s do" % (self.cond(n[1]) if n[1] is not None else "true"))
                self.block(n[2], ind + 1, lab)
                if lab in self.used_loops:
                    self.w(ind + 1, "::%s::" % lab)
                self.w(ind, "end")
            elif kind == "repeat":
                self.w(ind, "repeat")
                self.block(n[1], ind + 1, None)
                self.w(ind, "until %s" % self.cond(n[2]))
            elif kind == "break":
                self.w(ind, "break")
            elif kind == "continue":
                if loop is None:
                    self.stats["gotos"] += 1
                    self.w(ind, "goto continue_outer --[[unstructured]]")
                else:
                    self.used_loops.add(loop)
                    self.stats["continues"] += 1
                    self.w(ind, "goto %s" % loop)
            elif kind == "goto":
                self.w(ind, "goto L_%X" % (n[1] - self.p.base))
            elif kind == "return":
                e, tk = n[1], n[2] if len(n) > 2 else "ret"
                if e is None:
                    self.w(ind, "return")
                else:
                    ee = strip_t(e)
                    if self.cur not in self.g.returns and tk == "tail":
                        self.w(ind, self.ex(ee))
                        if k != len(body) - 1 or ind > 1:
                            self.w(ind, "return")
                    elif self.cur not in self.g.returns:
                        self.w(ind, "return")
                    else:
                        self.w(ind, "return %s" % self.ex(ee, 0, self.ret_hint()))
            elif kind == "switch":
                self.switch(n, ind, loop)
            elif kind == "label":
                self.w(ind, "::L_%X::" % (n[1] - self.p.base))
            elif kind == "setflag":
                self.w(ind, "%s = %s" % (n[1], "true" if n[2] else "false"))
            else:
                raise Unsupported("ast %s" % kind)

    def ret_hint(self):
        return None

    def switch(self, n, ind, loop):
        on = self.ex(n[1], 4)
        first = True
        for vals, body in n[2]:
            c = " or ".join("%s == %s" % (on, _int_lit(v)) for v in vals)
            self.w(ind, "%s %s then" % ("if" if first else "elseif", c))
            self.block(body, ind + 1, loop)
            first = False
        if n[3]:
            if first:
                self.block(n[3], ind, loop)
                return
            self.w(ind, "else")
            self.block(n[3], ind + 1, loop)
        if not first:
            self.w(ind, "end")

    def cond(self, c):
        return self.ex(strip_t(c))

    def new_loop(self):
        self.loop_no += 1
        return "continue_%d" % self.loop_no

    def w(self, ind, text):
        self.lines.append("  " * ind + text)

    # ------------------------------------------------------------------------------------------ functions --
    def function(self, va):
        g, f = self.g, self.tr.funcs[va]
        self.cur = va
        self.loop_no = 0
        self.used_loops = set()
        nm = g.fnames[va]
        params = g.params.get(va, [])
        what = {"update": "update callback (every frame)", "message": "message callback", "save": "save callback",
                "load": "load callback", "size": "save size callback", "init": "init callback",
                "setup": "LevelSetup (the DLL's export)", "queue": "queue_call callback"}.get(f.kind, "")
        self.lines.append("")
        self.lines.append("-- %s%s" % (_hex(va), (" - " + what) if what else ""))
        if f.kind == "queue" and getattr(f, "event_form", False):
            params = ["a", "b"]
        if f.kind == "message":
            self.message_function(va, nm, params)
            return
        self.lines.append("function %s(%s)" % (nm, ", ".join(params)))
        self.locals(va)
        body = list(self.g.ast[va])
        if body and body[-1][0] == "return" and body[-1][1] is None:
            body = body[:-1]
        self.block(body, 1)
        self.lines.append("end")

    def locals(self, va):
        bufs = {}
        f = self.tr.funcs[va]
        n = 0
        for lo, hi in getattr(f, "buffers", []):
            n += 1
            nm = "buf" if n == 1 else "buf%d" % n
            bufs[lo] = (nm, hi - lo)
        self.buffers[va] = bufs
        vs = self.g.vars.get(va, [])
        plain = [v for v, und in vs if not und] + self.g.flags_of.get(va, [])
        und = [v for v, u in vs if u]
        if plain:
            self.w(1, "local " + ", ".join(plain))
        for v in und:
            self.w(1, "local %s = 0 -- read before any assignment in the DLL (a register left over)" % v)
        if len(bufs) == 1:
            for lo, (nm, size) in bufs.items():
                self.w(1, "local %s = scratch(%d)   -- stack buffer [esp%+d] handed to the game" % (nm, size, lo))
        elif bufs:
            # several buffers: one block with the DLL's own layout (an index past one buffer lands in the next one,
            # as on the DLL's stack)
            lo0 = min(bufs)
            hi0 = max(lo + size for lo, (nm, size) in bufs.items())
            self.w(1, "local frame = scratch(%d)   -- stack locals [esp%+d .. esp%+d) handed to the game / indexed"
                   % (hi0 - lo0, lo0, hi0))
            for lo, (nm, size) in sorted(bufs.items()):
                self.w(1, "local %s = frame + %d   -- %d bytes" % (nm, lo - lo0, size))
        # an argument slot the DLL hands out (it keeps a value there and passes its address): the argument's value
        # starts in that memory
        names = self.g.params.get(va, [])
        for lo, (nm, size) in sorted(bufs.items()):
            for k, pn in enumerate(names):
                off = 4 + 4 * k
                if lo <= off < lo + size and f.kind != "queue":
                    self.w(1, "mem.w32(%s, %s)   -- argument %d lives at [esp+%d]" % (
                        nm if off == lo else "%s + %d" % (nm, off - lo), pn, k + 1, off))

    def message_function(self, va, nm, params):
        """The message callback: its guard stays a function, the switch on the message id becomes handlers."""
        body = self.g.ast[va]
        sw = None
        for k, n in enumerate(body):
            if n[0] == "switch" and strip_t(n[1]) == ("param", 0):
                sw = k
                break
        self.lines.append("function %s(%s)" % (nm, ", ".join(params)))
        self.locals(va)
        if sw is None:
            self.block(body, 1)
            self.lines.append("end")
            return
        self.block(body[:sw], 1)
        n = body[sw]
        self.w(1, "local handler = HANDLERS[id]")
        self.w(1, "if handler then return handler(msg, id) end")
        if n[3]:
            self.block(n[3], 1)
        self.lines.append("end")
        self.handlers = []
        for vals, b in n[2]:
            self.handlers.append((vals, b))

    def emit_handlers(self, va):
        self.cur = va
        self.loop_no = 0
        self.used_loops = set()
        out_start = len(self.lines)
        groups = []                     # (event names, body lines): handlers whose code is the same are one
        for vals, b in getattr(self, "handlers", []):
            save = self.lines
            self.lines = []
            self.block(b, 1)
            body, self.lines = self.lines, save
            names = [self.event_name(v) for v in vals]
            for gr in groups:
                if gr[1] == body:
                    gr[0].extend(names)
                    break
            else:
                groups.append((names, body))
        for names, body in groups:
            key = lua_str(names[0]) if len(names) == 1 else "{%s}" % ", ".join(lua_str(x) for x in names)
            self.lines.append("")
            self.lines.append("on(%s, function(msg, id)" % key)
            self.lines += body
            self.lines.append("end)")

    def event_name(self, mid):
        nm = self.g.msgs.get(mid, ("msg_%02x" % mid,))[0]
        self.events_used[nm] = mid
        return nm

    # ------------------------------------------------------------------------------------------ file --
    def emit(self):
        """-> {"mission.lua", "refs.lua", "scripts/events.lua", "scripts/runtime.lua", "bundle"} (texts)."""
        g, p, tr = self.g, self.p, self.tr
        self.buffers = {}
        self.events_used = {}
        self.objectives = []
        self.dfields = {}
        g.slot_mixed = set()
        order = sorted(tr.funcs, key=lambda v: (v not in (p.setup,) + tuple(p.cbs), v))
        for va in order:
            self.function(va)
        self.emit_handlers(p.cbs[2])
        body = self.lines
        self.lines = []
        self.state_section()
        state = self.lines
        events = "\n".join(self.logic_header() + state + ["", "-- ======================== mission code ========================"]
                           + body) + "\n"
        self.lines = []
        self.api_section()
        runtime = "\n".join(["-- Generated glue (mission2lua_readable %d): connects scripts/events.lua to the game. Not "
                             "mission logic - regenerate rather than edit." % VERSION,
                             BRIDGE.replace("%VERSION%", str(VERSION)).replace("%BASE%", _hex(p.base))] + self.lines) + "\n"
        main = MAIN.replace("%SIZE%", _hex(p.p.size)).replace("%BASE%", _hex(p.base)).replace(
            "%NAME%", p.name).replace("%SETUP%", g.fnames[p.setup])
        mission = "-- %s: mission data (literals only). Generated by mission2lua_readable %d from your own %s - " \
                  "game data, never commit or distribute.\nreturn %s\n" % (p.name, VERSION, p.name,
                                                                         lua_literal(self.mission_data(), 0))
        refs = "-- %s: the scene objects, waypoints and regions its logic names (editor ID = number in the level's " \
               "scene).\nreturn %s\n" % (p.name, lua_literal(self.refs_data(), 0))
        bundle = bundle_text(self.level, p.name, p.p.sha256(), mission, refs, runtime, events, main)
        return {"mission.lua": mission, "refs.lua": refs, "scripts/events.lua": events,
                "scripts/runtime.lua": runtime, "scripts/start.lua": main, "bundle": bundle}

    def logic_header(self):
        p = self.p
        return ["-- %s as readable Lua: mission2lua_readable %d (ConflictDesertStormModKit), SHA-256 %s." % (
                    p.name, VERSION, p.p.sha256()),
                "-- Generated from your own game files: game code. Never commit or distribute this file.",
                "-- Functions are the DLL's (address in the comment above each); API calls by name (api.*); the DLL's",
                "-- globals as named variables (address in the comment); messages as on(<event>, handler); scene objects",
                "-- by editor ID (obj.* / wp.* / rgn.*, numbers in refs.lua)."]

    def api_section(self):
        g = self.g
        w = self.lines.append
        w("-- ======================== the script API this mission uses ========================")
        rest = [s for s in sorted(self.tr.slots) if s not in g.slot_used and self.tr.slots[s].get("name")]
        for slot in sorted(g.slot_used) + [None] + rest:
            if slot is None:   # every other named slot, so an edited mission can call any of them by name
                w("-- ======================== the rest of the script API (not called by this mission) ========================")
                continue
            info = self.tr.slots.get(slot, {})
            args = [_ident(a.get("name"), "a") for a in info.get("args", [])]
            seen = set()
            for k, a in enumerate(args):
                if a in seen or a in LUA_KEYWORDS:
                    args[k] = "%s%d" % (a, k + 1)
                seen.add(args[k])
            nm = info.get("name") or "slot_%03X" % slot
            fret = "true" if slot in g.slot_float or (slot not in g.slot_used and str(info.get("returns", ""))
                                                       .startswith("float")) else "false"
            if args and info["args"][-1].get("name") in ("args", "format_args"):
                sig = ", ".join(args[:-1] + ["..."])          # printf-style: only what the mission passes
                body = "return call(%s, %s, %s)" % (_hex(slot), fret, sig)
            elif args:
                sig = ", ".join(args)
                body = "return call(%s, %s, %s)" % (_hex(slot), fret, sig)
            else:
                sig = "..."
                body = "return call(%s, %s, ...)" % (_hex(slot), fret)
            if slot == 0x004:
                body = "return register_anim(%s)" % sig
            ret = info.get("returns")
            note = (" -- -> %s" % ret) if ret and ret != "none" else ""
            w("function api.%s(%s) %s end%s" % (nm, sig, body, note))

    def state_section(self):
        g = self.g
        w = self.lines.append
        w("")
        w("-- ======================== mission state (the DLL's globals) ========================")
        for a, gv in sorted(g.gvars.items()):
            if gv["memory"]:
                w("-- D.%s: %s in the memory block (its address is handed to the game)" % (gv["name"], _hex(a)))
                continue
            init = _f32_short(gv["init"]) if gv["kind"] == "f" else _int_lit(gv["init"])
            tag = {1: "byte", 2: "word", 4: "float" if gv["kind"] == "f" else "dword"}.get(gv["size"], "?")
            inr = g.save_range and g.save_range[0] <= a < g.save_range[1]
            w("%s = %s -- %s %s%s" % (gv["name"], init, _hex(a), tag, ", in the save block" if inr else ""))

    def mission_data(self):
        g, p = self.g, self.p
        d = {"name": os.path.splitext(p.name)[0], "dll": p.name, "sha256": p.p.sha256(), "archive": self.level,
             "translator": "mission2lua_readable %d" % VERSION}
        scene = self.g.scene_name
        if scene:
            d["scene"] = scene
        d["objectives"] = [{"variable": g.gvars[a]["name"], "title": t} for a, t in g.objective_titles
                           if a in g.gvars and t is not None]
        d["events"] = {nm: mid for nm, mid in self.events_used.items()}
        save = {"size": 0, "template": "", "fields": []}
        if g.save_range:
            lo, hi = g.save_range
            save = {"size": hi - lo, "template": p.bytes_at(lo, hi - lo).decode("latin-1"), "fields": [],
                    "rva": lo - p.base, "memory": []}
            for mlo, mhi in g.memory_abs:
                a, b = max(lo, mlo), min(hi, mhi)
                if a < b:
                    save["memory"].append([a - lo, b - lo])
            for a, gv in sorted(g.gvars.items()):
                if lo <= a < hi:
                    fmt = {1: "B", 2: "<I2", 4: "<f" if gv["kind"] == "f" else "<i4"}[gv["size"]]
                    if gv["memory"]:
                        save["fields"].append([a - lo, fmt, False, a - p.base])
                    else:
                        save["fields"].append([a - lo, fmt, gv["name"]])
        d["save"] = save
        secs, relocs = [], []
        for lo, hi in g.memory_abs:
            secs.append([lo - p.base, p.bytes_at(lo, hi - lo).hex()])
            relocs += [r - p.base for r in sorted(p.p.relocs) if lo <= r < hi]
        entry = {}
        for va, f in sorted(self.tr.funcs.items()):
            if f.kind in ("init", "update", "message", "save", "load", "size", "queue") or va in p.queue_cbs:
                entry[va - p.base] = [g.fnames[va], f.kind, bool(getattr(f, "event_form", False))]
        d["fields"] = {k: [rva, fn] for k, (rva, fn) in self.dfields.items()}
        d["image"] = {"size": p.p.size, "base": p.base, "strings": {rva: s for rva, s in g.strings.items()},
                      "memory": secs, "relocs": relocs, "object": p.obj - p.base,
                      "object_callbacks": [c - p.base for c in p.cbs], "entry": entry}
        return d

    def refs_data(self):
        out = {}
        for table in ("objects", "waypoints", "regions", "markers"):
            used = self.g.ref_used.get(table)
            if used:
                out[table] = dict(used)
        return out


def lua_literal(v, ind=0):
    """Canonical Lua literal (data files): tables with sorted keys, one entry per line for dicts, 2-space indent."""
    pad = "  " * (ind + 1)
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return _int_lit(v) if -0x10000 < v < 0x10000 or v < 0 else _hex(v)
    if isinstance(v, float):
        return _float_literal(v)
    if isinstance(v, str):
        return lua_str(v)
    if v is None:
        return "nil"
    if isinstance(v, (list, tuple)):
        items = [lua_literal(x, ind + 1) for x in v]
        one = "{" + ", ".join(items) + "}"
        if len(one) <= 100 and "\n" not in one:
            return one
        return "{\n" + "".join(pad + x + ",\n" for x in items) + "  " * ind + "}"
    if isinstance(v, dict):
        def keytxt(k):
            if isinstance(k, str) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k) and k not in LUA_KEYWORDS:
                return k
            if isinstance(k, int):
                return "[%s]" % (_hex(k) if k >= 0x100 else str(k))
            return "[%s]" % lua_str(str(k))
        keys = sorted(v, key=lambda k: (0, k) if isinstance(k, int) else (1, str(k).lower(), str(k)))
        if not keys:
            return "{}"
        return "{\n" + "".join("%s%s = %s,\n" % (pad, keytxt(k), lua_literal(v[k], ind + 1)) for k in keys) + \
            "  " * ind + "}"
    raise TypeError(type(v))


def bundle_text(level, name, sha, mission, refs, runtime, events, main):
    """The one file the script host runs (<Mission>.lua): data files as tables, the runtime, the logic, start-up."""
    def body(text):
        lines = [x for x in text.splitlines() if not x.startswith("--@")]
        return "\n".join(lines)
    return "\n".join([
        "--@archive %s" % level,
        "-- %s as readable Lua (mission2lua_readable %d), SHA-256 %s. Built from the mod folder: mission.lua," % (
            name, VERSION, sha),
        "-- refs.lua, scripts/runtime.lua, scripts/events.lua, scripts/start.lua. Game code: never commit or distribute.",
        "local MISSION = (function()", body(mission), "end)()",
        "local REFS = (function()", body(refs), "end)()",
        body(runtime),
        "-- ======================== scripts/events.lua ========================",
        body(events),
        "-- ======================== scripts/start.lua ========================",
        body(main), ""])


def _top_calls(e):
    out = []
    if not isinstance(e, tuple) or not e:
        return out
    if e[0] == "call":
        return [e]
    for x in kids(e):
        out += _top_calls(x)
    return out


BRIDGE = r'''
-- ======================== bridge (mission2lua_readable %VERSION%) ========================
-- How this script is connected to the game, as the DLL was: DesertStormFix's mission runtime (x86rt, the same one
-- stage 1 uses) gives a block of real memory laid out like the DLL image - string literals, tables and the script
-- object live at the DLL's own offsets, so pointers handed to the game are real pointers - and calls API slots
-- natively. The game calls back through 5-byte thunks in that block (script object callbacks, queue_call callbacks),
-- which land in __x86_enter below. Nothing of the DLL's code runs.
assert(x86rt and x86rt.install, "this mission needs DesertStormFix's mission runtime (dsfix.missionrt)")
x86rt.install()
local X = X86RT
local M32 = 0xFFFFFFFF
local LO = 0                -- the memory block (DLL base %BASE% <-> LO)
local SP = 0                -- scratch memory (stack buffers, strings, call arguments), reset per game call
local PC24 = true           -- x87 precision control of the game thread: 24-bit (Direct3D) -> float rounding
local INTERN = {}           -- string literal -> offset in the block
API_TABLE = 0x5ED238
local _ENV = setmetatable({}, {__index = _G})   -- the mission's own names (functions, state) live here
api = {}
-- data (mission.lua / refs.lua)
EVENTS = MISSION.events
STATE_SIZE, STATE_TEMPLATE, STATE_FIELDS = MISSION.save.size, MISSION.save.template, MISSION.save.fields
STRINGS, MEMORY, MEMORY_RELOCS = MISSION.image.strings, MISSION.image.memory, MISSION.image.relocs
OBJECT, OBJECT_CALLBACKS = MISSION.image.object, MISSION.image.object_callbacks
obj, wp, rgn, mk = REFS.objects or {}, REFS.waypoints or {}, REFS.regions or {}, REFS.markers or {}
ENTRY, CALLBACKS = {}, {}
-- D.<name>: the DLL globals that live in the memory block because their address is handed to the game (tables,
-- structures read through pointers); mission.lua `fields` = {offset, type}
D = setmetatable({}, {
  __index = function(_, k) local f = MISSION.fields[k]; return mem[f[2]](LO + f[1]) end,
  __newindex = function(_, k, v)
    local f = MISSION.fields[k]
    local w = ({i32 = "w32", u8 = "w8", u16 = "w16", f32 = "wf"})[f[2]]
    mem[w](LO + f[1], v)
  end})

-- integers are kept as signed 32-bit values, floats as Lua floats holding a float32 value
function i32(v) v = v & M32; if v >= 0x80000000 then return v - 0x100000000 end return v end
function ult(a, b) return math.ult(a & M32, b & M32) end          -- unsigned 32-bit a < b
function ule(a, b) return not math.ult(b & M32, a & M32) end      -- a <= b
function ugt(a, b) return math.ult(b & M32, a & M32) end          -- a > b
function uge(a, b) return not math.ult(a & M32, b & M32) end      -- a >= b
function f32(x) return (string.unpack("<f", string.pack("<f", x))) end
function fr(x) if PC24 then return f32(x) end return x + 0.0 end   -- an x87 result at the game's precision
function float(x) return x + 0.0 end
function bits(v) if math.type(v) == "float" then return (string.unpack("<i4", string.pack("<f", v))) end return v end
function asfloat(v) if math.type(v) == "float" then return v end return (string.unpack("<f", string.pack("<I4", v & M32))) end
function b2i(c) return c and 1 or 0 end
function sx8(v) v = v & 0xFF; if v >= 0x80 then return v - 0x100 end return v end
function sx16(v) v = v & 0xFFFF; if v >= 0x8000 then return v - 0x10000 end return v end
function idiv(a, b) local q = a // b; if q < 0 and q * b ~= a then q = q + 1 end return q end
function imod(a, b) return a - idiv(a, b) * b end
function udiv(a, b) return (a & M32) // (b & M32) end
-- high half of an unsigned 32 x 32 multiply (x86 mul's edx): the 64-bit product wraps mod 2^64, >> is logical
function umulhi(a, b) return ((a & M32) * (b & M32)) >> 32 end
function umod(a, b) return (a & M32) % (b & M32) end
function qword(lo, hi) return float((lo & M32) + hi * 4294967296) end
function ftoi(x) return math.tointeger(x >= 0 and math.floor(x + 0.5) or math.ceil(x - 0.5)) or 0 end
function ADDR(rva) return LO + rva end

-- memory through pointers (game objects, buffers handed to the game, DLL tables walked by pointer)
mem = {
  i32 = function(a) return i32(X.r32(a)) end, u8 = X.r8, u16 = X.r16, f32 = X.rf, f64 = X.rd,
  w32 = function(a, v) if math.type(v) == "float" then X.wf(a, v) else X.w32(a, v & M32) end end,
  w8 = function(a, v) X.w8(a, v & 0xFF) end, w16 = function(a, v) X.w16(a, v & 0xFFFF) end,
  wf = function(a, v) X.wf(a, v) end, wd = X.wd, copy = X.copy,
}
function scratch(n) SP = (SP - n) & ~15; return SP end
local function poke_string(a, s)
  for k = 1, #s do X.w8(a + k - 1, s:byte(k)) end
  X.w8(a + #s, 0)
end
local function cstr(s)
  local rva = INTERN[s]
  if rva then return LO + rva end
  local a = scratch(#s + 1)
  poke_string(a, s)
  return a
end
local function dw(v)                 -- one argument dword for the game
  local t = math.type(v)
  if t == "integer" then return v & M32 end
  if t == "float" then return string.unpack("<I4", string.pack("<f", v)) end
  local ty = type(v)
  if ty == "string" then return cstr(v) end
  if ty == "boolean" then return v and 1 or 0 end
  if ty == "function" then
    local rva = CALLBACKS[v]
    if not rva then error("function handed to the game has no thunk") end
    return LO + rva
  end
  if v == nil then return 0 end
  error("can't pass a " .. ty .. " to the game")
end
function call(slot, fret, ...)       -- API slot -> the game function (cdecl)
  local n = select("#", ...)
  local a = {}
  for k = 1, n do a[k] = dw((select(k, ...))) end
  local argp = SP - 96
  for k = 1, n do X.w32(argp + 4 * (k - 1), a[k]) end
  local eax, _, _, _, hasf, st0 = X.call(X.r32(API_TABLE + slot), argp)
  if fret then return st0 end
  return i32(eax)
end
function api_slot(slot) return X.r32(API_TABLE + slot) end
-- a call through a function pointer: one of this mission's own functions (a queued callback handed back to it) runs
-- in Lua the way the game's call would run it; anything else is a native call
function call_ptr(p, ...)
  if p >= LO and p < LO + MISSION.image.size and ENTRY[p - LO] then
    local n = select("#", ...)
    local a = {}
    for k = 1, n do a[k] = dw((select(k, ...))) end
    local argp = scratch(4 * n + 4)
    for k = 1, n do X.w32(argp + 4 * (k - 1), a[k]) end
    return i32((_G.__x86_enter(p - LO, argp)))
  end
  return call_native(p, ...)
end
function call_native(p, ...)
  local n = select("#", ...)
  local a = {}
  for k = 1, n do a[k] = dw((select(k, ...))) end
  local argp = SP - 96
  for k = 1, n do X.w32(argp + 4 * (k - 1), a[k]) end
  local eax = X.call(p, argp)
  return i32(eax)
end
ANIMS_SKIPPED = 0
function register_anim(state, file)
  -- the script host registered the standard animation states already: the game's register would show
  -- "Attempt to load anim twice!" and change nothing - take that path without the box (as stage 1 does)
  local p = cstr(state)
  if X.anim_loaded(p) then ANIMS_SKIPPED = ANIMS_SKIPPED + 1; return 1 end
  return call(0x004, false, state, file)
end
-- sprintf(buffer, format, ...) with C's conversions, into memory
function sprintf(buf, fmt, ...)
  local args, k = {...}, 0
  local out = fmt:gsub("%%([-+ #0]*%d*%.?%d*)([hlL]?)([diouxXcspfeEgG%%])", function(flags, _len, conv)
    if conv == "%" then return "%" end
    k = k + 1
    local v = args[k]
    if conv == "s" then
      if type(v) == "string" then return string.format("%" .. flags .. "s", v) end
      local t, a = {}, v
      while true do local c = X.r8(a); if c == 0 then break end; t[#t + 1] = string.char(c); a = a + 1 end
      return string.format("%" .. flags .. "s", table.concat(t))
    elseif conv == "d" or conv == "i" then return string.format("%" .. flags .. "d", i32(v))
    elseif conv == "u" then return string.format("%" .. flags .. "d", v & M32)
    elseif conv == "x" or conv == "X" or conv == "o" then return string.format("%" .. flags .. conv, v & M32)
    elseif conv == "c" then return string.char(v & 0xFF)
    else return string.format("%" .. flags .. conv, v) end
  end)
  poke_string(buf, out)
  return #out
end
-- the DLL's C runtime rand() (MSVC: holdrand = holdrand * 214013 + 2531011, bits 16-30); the DLL never calls srand,
-- so the sequence starts at 1 each time the level loads (a fresh DLL / a fresh script state)
local crt_holdrand = 1
function crt_rand()
  crt_holdrand = (crt_holdrand * 214013 + 2531011) & M32
  return (crt_holdrand >> 16) & 0x7FFF
end
-- sprintf whose format differs per path (the compiler shared one call): the arguments as raw dwords
function csprintf(buf, fmt, ...)
  local d, k, vals = {...}, 1, {}
  if type(fmt) ~= "string" then
    local t, a = {}, fmt
    while true do local c = X.r8(a); if c == 0 then break end; t[#t + 1] = string.char(c); a = a + 1 end
    fmt = table.concat(t)
  end
  for conv in fmt:gmatch("%%[-+ #0]*%d*%.?%d*[hlL]?([diouxXcspfeEgG%%])") do
    if conv ~= "%" then
      if conv:match("[feEgG]") then
        vals[#vals + 1] = string.unpack("<d", string.pack("<I4I4", (d[k] or 0) & M32, (d[k + 1] or 0) & M32))
        k = k + 2
      else
        vals[#vals + 1] = d[k] or 0
        k = k + 1
      end
    end
  end
  return sprintf(buf, fmt, table.unpack(vals))
end
-- the DLL's save block: the state variables packed in the DLL's layout (old saves keep loading)
local STATE_RAW
function write_state(buf)
  -- the last loaded block (or the DLL's initial bytes) with every field put at its offset; fields that overlap
  -- (a byte inside a dword the DLL also uses) are the same memory, so the later one writes the same bytes
  local raw = STATE_RAW or STATE_TEMPLATE
  local bytes = {}
  for k = 1, STATE_SIZE do bytes[k] = raw:sub(k, k) end
  for _, sp in ipairs(MISSION.save.memory or {}) do   -- parts of the block that live in the memory block
    for k = sp[1], sp[2] - 1 do bytes[k + 1] = string.char(X.r8(LO + MISSION.save.rva + k)) end
  end
  for _, fld in ipairs(STATE_FIELDS) do
    local off, fmt, name, rva = fld[1], fld[2], fld[3], fld[4]
    local v
    if name then v = _ENV[name]
    elseif fmt == "B" then v = X.r8(LO + rva) elseif fmt == "<I2" then v = X.r16(LO + rva)
    else fmt = "<I4"; v = X.r32(LO + rva) end
    if fmt == "<f" and math.type(v) ~= "float" then fmt = "<I4"; v = v & M32 end
    if fmt == "<i4" and math.type(v) == "float" then fmt = "<f" end
    if fmt == "<i4" then v = i32(v) elseif fmt == "B" then v = v & 0xFF elseif fmt == "<I2" then v = v & 0xFFFF end
    local p = string.pack(fmt, v)
    for k = 1, #p do bytes[off + k] = p:sub(k, k) end
  end
  local s = table.concat(bytes)
  for k = 1, #s do X.w8(buf + k - 1, s:byte(k)) end
end
function read_state(buf)
  local t = {}
  for k = 0, STATE_SIZE - 1 do t[k + 1] = string.char(X.r8(buf + k)) end
  local raw = table.concat(t)
  STATE_RAW = raw
  for _, sp in ipairs(MISSION.save.memory or {}) do
    for k = sp[1], sp[2] - 1 do X.w8(LO + MISSION.save.rva + k, raw:byte(k + 1)) end
  end
  for _, fld in ipairs(STATE_FIELDS) do
    local off, fmt, name, rva = fld[1], fld[2], fld[3], fld[4]
    local v = string.unpack(fmt, raw, off + 1)
    if name then _ENV[name] = v
    elseif fmt == "B" then X.w8(LO + rva, v) elseif fmt == "<I2" then X.w16(LO + rva, v)
    else X.w32(LO + rva, (string.unpack("<I4", raw, off + 1))) end
  end
end
-- message parameters: msg[k] = the k-th parameter dword (msg[k] = v answers a query), msg:float(k)
local MSG = {__index = function(t, k) return i32(X.r32(rawget(t, "p") + 4 * k)) end,
             __newindex = function(t, k, v) X.w32(rawget(t, "p") + 4 * k, dw(v)) end}
local function msgview(p) return setmetatable({p = p, float = function(self, k) return X.rf(rawget(self, "p") + 4 * k) end}, MSG) end
-- message handlers by event name: on("killed", function(msg, id) ... end)
HANDLERS = {}
function on(names, fn)
  if type(names) ~= "table" then names = {names} end
  for _, n in ipairs(names) do HANDLERS[EVENTS[n] or n] = fn end
end
-- the game calls a DLL function (a thunk in the block)
_G.__x86_enter = function(va, argp)
  local e = ENTRY[va]
  if not e then error(string.format("no Lua function for DLL address %%08X", va)) end
  local save = SP
  local fn, kind = e[1], e[2]
  local a1, a2 = X.r32(argp), X.r32(argp + 4)
  local r
  if kind == "message" then r = fn(i32(a1), msgview(a2))
  elseif kind == "save" or kind == "load" then r = fn(a1)
  elseif kind == "queue" and e[3] then r = fn(i32(X.r32(a1 + 0x34)), i32(X.r32(a1 + 0x38)))
  elseif kind == "queue" then r = fn(a1)
  else r = fn() end
  SP = save
  return r and dw(r) or 0, 0
end
'''

MAIN = r'''
-- ======================== level start ========================
_G.init = function()
  for rva, e in pairs(MISSION.image.entry) do
    local fn = _ENV[e[1]]
    if type(fn) ~= "function" then error("mission.lua names a function scripts/events.lua doesn't define: " .. e[1]) end
    ENTRY[rva] = {fn, e[2], e[3]}
    CALLBACKS[fn] = rva
  end
  LO = X.load_image({size = %SIZE%, base = 0, sections = MEMORY, relocs = "", thunks = (function()
    local t = {}
    for rva in pairs(ENTRY) do t[#t + 1] = rva end
    for _, rva in ipairs(OBJECT_CALLBACKS) do t[#t + 1] = rva end
    return t end)(), imports = {}})
  for _, rva in ipairs(MEMORY_RELOCS) do X.w32(LO + rva, (X.r32(LO + rva) - %BASE% + LO) & 0xFFFFFFFF) end
  SP = X.stack_top() - 64
  PC24 = (X.fnstcw() & 0x300) == 0
  for rva, s in pairs(STRINGS) do poke_string(LO + rva, s); INTERN[s] = INTERN[s] or rva end
  for k, rva in ipairs(OBJECT_CALLBACKS) do X.w32(LO + OBJECT + 4 * (k - 1), LO + rva) end
  local save = SP
  %SETUP%(API_TABLE)
  SP = save
  X.set_object(LO + OBJECT)
  game.log(string.format("mission2lua_readable: %NAME% running as readable Lua (%d standard animation states already registered)", ANIMS_SKIPPED))
end
_G.update = function() end       -- the game calls this mission's own update / message through its script object
_G.on_message = function() end
'''


# ============================================================================================ entry points ==========
def refs_for(g, game_dir):
    """The level scene's editor IDs (refs_from_env) for a Generator, read from the player's archives."""
    from . import archive
    scene = g.find_scene()
    if not scene or not game_dir:
        return {}
    data = archive.GameFiles(game_dir).get(scene)
    if data is None:
        return {}
    return refs_from_env(data)


def translate(path, game_dir=None, level=None):
    """-> (parts, generator): parts = {"mission.lua", "refs.lua", "scripts/events.lua", "scripts/runtime.lua",
    "scripts/start.lua", "bundle"}; game_dir (default: the DLL's folder) gives the scene's editor IDs."""
    g = Generator(path)
    if game_dir is None and isinstance(path, str):
        game_dir = os.path.dirname(os.path.abspath(path))
    try:
        g.refs = refs_for(g, game_dir) or {}
    except Exception as e:  # noqa: BLE001 - no scene / unreadable archive: numbers stay numbers
        g.warnings = [str(e)]
        g.refs = {}
    return g.generate(level), g


def write_folder(parts, out_dir, name):
    """The mod-folder layout: <out>/<name>/{mission.lua, refs.lua, scripts/...} + <out>/<name>.lua (the bundle the
    script host runs)."""
    root = os.path.join(out_dir, name)
    for rel, text in parts.items():
        if rel == "bundle":
            continue
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    bundle = os.path.join(out_dir, name + ".lua")
    with open(bundle, "w", encoding="utf-8", newline="\n") as f:
        f.write(parts["bundle"])
    return root, bundle


def bundle_folder(folder, level=None):
    """Rebuild the bundle from an (edited) mod folder."""
    def rd(rel):
        with open(os.path.join(folder, *rel.split("/")), encoding="utf-8") as f:
            return f.read()
    mission = rd("mission.lua")
    m = re.search(r'archive = "([^"]+)"', mission)
    lvl = level or (m.group(1) if m else os.path.basename(folder).lower())
    nm = re.search(r'dll = "([^"]+)"', mission)
    sha = re.search(r'sha256 = "([^"]+)"', mission)
    return bundle_text(lvl, nm.group(1) if nm else "?", sha.group(1) if sha else "?", mission, rd("refs.lua"),
                       rd("scripts/runtime.lua"), rd("scripts/events.lua"), rd("scripts/start.lua"))
