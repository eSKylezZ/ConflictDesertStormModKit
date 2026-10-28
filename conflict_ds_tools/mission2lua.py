"""Mission DLL -> Lua mission (stage 1: a faithful, low-level translation).

A shipped mission DLL (MissionN.dll, TrainingN.dll) is turned into a ``.lua`` level script that the DesertStormFix
script host (src/features/scripthost.cpp) runs instead of the DLL. Stage 1 is a *static recompilation*: every x86
instruction of every function the DLL can run (its CRT start-up included) becomes Lua, so the behaviour is the DLL's
by construction - no guessing of what the code means. Readability comes later (stage 2: the structured view of
``missionflow`` over the same functions); this stage is the reference that the readable one has to match.

The output is generated on the player's (or developer's) machine from their own game files and is game code: never
commit it, never ship it (docs/sdk/DESIGN.md §12, docs/sdk/mission-translation.md in the patch repository).

Shape of the generated file (all game data comes from the DLL, all helpers from this module):

* ``--@archive <level>`` header: the level's data archive / sound cache (mission1.dat ...); ``--@standard_anims off``
  (VERSION 2): the script host doesn't register its standard animation set first - the DLL's init registers exactly
  what it registers on the DLL path (the host's set has files the training archives lack: "Couldn't open
  upright_relaxed_with_pistol.prs" boxes on Training1-3).
* the runtime prelude (``RUNTIME`` below): emulated registers ``EAX..ESP`` (Lua integers, 0..2^32-1), lazy flags
  (``FK`` kind + operands), an x87 stack (Lua doubles), memory access through the plugin's ``x86rt`` natives
  (src/features/missionrt.cpp: real memory - the DLL image is mapped into a real block, so every pointer the game
  hands out or receives stays a real pointer), calls into the game (``X.call``: native cdecl / stdcall / thiscall
  with the emulated stack as argument block) and back (``__x86_enter``: the game calls a DLL callback -> a 5-byte
  thunk in the image block -> this Lua function).
* ``F[va] = function() ... end`` per DLL function: one label per basic block, ``goto`` between them (Lua 5.4),
  every instruction as a few Lua statements with the original address and disassembly as a comment.
* the image: sections (hex), relocations, address-taken functions (thunks), imports (resolved by name).
* ``init()``: maps the image, runs the DLL's entry point (DLL_PROCESS_ATTACH: the static CRT's start-up, as
  LoadLibrary would), then ``LevelSetup(&api)`` exactly like the game's FUN_00449260, and hands the returned script
  object to the runtime, which installs it as the level's script object ([0x60F578]) when the script host's
  ``pse.lua.level_start`` fires. From then on the game calls the DLL's own callbacks (update, message, save, load,
  size) through their thunks - message answers, query writes and the raw save block are the DLL's own bytes.

Memory model: one real block per level = the DLL image (all sections, relocated to the block), plus a 1 MB emulated
stack. DLL globals are therefore *the DLL's .data at its own offsets* - pointer arithmetic, byte / word / dword
aliasing, pointers handed to the game (out-parameters, strings, callback pointers) all keep working. Nothing of the
DLL is ever executed natively except the thunks.

Usage: ``python -m cli.ds_mission2lua Mission1.dll -o out_dir`` (see cli/ds_mission2lua.py), or ``translate(path)``.
"""
from __future__ import annotations

import hashlib
import os
import struct

from capstone import x86

from . import missionflow as mf

VERSION = 2
M32 = 0xFFFFFFFF
MASK = {1: 0xFF, 2: 0xFFFF, 4: 0xFFFFFFFF}
SIGN = {1: 0x80, 2: 0x8000, 4: 0x80000000}
READ = {1: "r8", 2: "r16", 4: "r32"}
WRITE = {1: "w8", 2: "w16", 4: "w32"}

_R32 = {}
_R16 = {}
_LO8 = {}
_HI8 = {}
for _n in ("eax", "ebx", "ecx", "edx", "esi", "edi", "ebp", "esp"):
    _R32[getattr(x86, "X86_REG_" + _n.upper())] = _n.upper()
for _n, _p in (("ax", "EAX"), ("bx", "EBX"), ("cx", "ECX"), ("dx", "EDX"), ("si", "ESI"), ("di", "EDI"),
               ("bp", "EBP"), ("sp", "ESP")):
    _R16[getattr(x86, "X86_REG_" + _n.upper())] = _p
for _n, _p in (("al", "EAX"), ("bl", "EBX"), ("cl", "ECX"), ("dl", "EDX")):
    _LO8[getattr(x86, "X86_REG_" + _n.upper())] = _p
for _n, _p in (("ah", "EAX"), ("bh", "EBX"), ("ch", "ECX"), ("dh", "EDX")):
    _HI8[getattr(x86, "X86_REG_" + _n.upper())] = _p

# conditions: (generic expression, {flag kind: fast expression on FA/FB/FR/FW})
_COND = {
    "o": "fOF()", "no": "not fOF()", "b": "fCF()", "ae": "not fCF()", "e": "fZF()", "ne": "not fZF()",
    "be": "(fCF() or fZF())", "a": "not (fCF() or fZF())", "s": "fSF()", "ns": "not fSF()", "p": "fPF()",
    "np": "not fPF()", "l": "(fSF() ~= fOF())", "ge": "(fSF() == fOF())", "le": "(fZF() or fSF() ~= fOF())",
    "g": "not (fZF() or fSF() ~= fOF())",
}
_FAST_SUB = {  # after cmp / sub (FK 2): operands FA, FB (masked to the width), FW = sign bit of the width
    "e": "FA == FB", "ne": "FA ~= FB", "b": "FA < FB", "ae": "FA >= FB", "be": "FA <= FB", "a": "FA > FB",
    "l": "(FA ~ FW) < (FB ~ FW)", "ge": "(FA ~ FW) >= (FB ~ FW)", "le": "(FA ~ FW) <= (FB ~ FW)",
    "g": "(FA ~ FW) > (FB ~ FW)", "s": "(FR & FW) ~= 0", "ns": "(FR & FW) == 0",
}
_FAST_LOGIC = {  # after and / or / xor / test (FK 3): CF = OF = 0
    "e": "FR == 0", "ne": "FR ~= 0", "s": "(FR & FW) ~= 0", "ns": "(FR & FW) == 0", "l": "(FR & FW) ~= 0",
    "ge": "(FR & FW) == 0", "le": "(FR == 0 or (FR & FW) ~= 0)", "g": "(FR ~= 0 and (FR & FW) == 0)",
    "b": "false", "ae": "true", "be": "FR == 0", "a": "FR ~= 0", "o": "false", "no": "true",
}
_FAST_RES = {"e": "FR == 0", "ne": "FR ~= 0", "s": "(FR & FW) ~= 0", "ns": "(FR & FW) == 0"}
_JCC = {"jo": "o", "jno": "no", "jb": "b", "jae": "ae", "je": "e", "jne": "ne", "jbe": "be", "ja": "a", "js": "s",
        "jns": "ns", "jp": "p", "jnp": "np", "jl": "l", "jge": "ge", "jle": "le", "jg": "g"}
_SET = {"set" + k[1:]: v for k, v in _JCC.items()}
_FLAG_WRITERS = {"add", "sub", "adc", "sbb", "and", "or", "xor", "cmp", "test", "inc", "dec", "neg", "shl", "sal",
                 "shr", "sar", "rol", "ror", "rcl", "rcr", "imul", "mul", "sahf", "popfd", "stc", "clc", "cmc",
                 "repe cmpsb", "repne scasb", "cmpsb", "scasb", "bsf", "bsr", "bt", "div", "idiv", "shld", "shrd"}


class Unsupported(Exception):
    pass


def _h(v):
    return "0x%X" % (v & M32)


# ---------------------------------------------------------------------------------------------------- program --
class _Function(mf.Function):
    """missionflow's Function plus one more jump-table shape (VERSION 2): the bound in a register set earlier,
    ``mov esi, 0x2d ... cmp eax, esi; ja default; jmp [eax*4 + table]`` (Training2.dll 0x1000228C: 46 cases;
    missionflow only knows ``cmp reg, imm``, so those cases were never discovered and the run stopped with "jump
    into the image ... (no function)"). Accepted only when every table entry is a relocated pointer into .text."""
    relocs: set = set()

    def _table_targets(self, i):
        tg = super()._table_targets(i)
        if tg:
            return tg
        op = i.operands[0]
        if op.type != x86.X86_OP_MEM:
            return []
        base, index, scale, disp = mf._mem(op)
        if base is not None or index is None or scale != 4:
            return []
        d = self.dll
        back = []
        for k in range(min(256, i.address - d.text_lo), 0, -1):   # linear decode up to 256 bytes back
            out, a = [], i.address - k
            while a < i.address:
                p = d.insn(a)
                if p is None:
                    break
                out.append(p); a += p.size
            if a == i.address and out:
                back = out
                break
        bound, reg2 = None, None
        for p in reversed(back):
            ops = p.operands
            if reg2 is None:
                if p.mnemonic == "cmp" and len(ops) == 2 and ops[0].type == x86.X86_OP_REG and \
                        ops[1].type == x86.X86_OP_REG and mf._REG.get(ops[0].reg) == index:
                    reg2 = mf._REG.get(ops[1].reg)
                continue
            if ops and ops[0].type == x86.X86_OP_REG and mf._REG.get(ops[0].reg) == reg2 and \
                    p.mnemonic not in ("cmp", "test", "push"):
                if p.mnemonic == "mov" and ops[1].type == x86.X86_OP_IMM:
                    bound = ops[1].imm
                break
        if bound is None or bound > 0x400:
            return []
        case_of = {k: d.u32(disp + 4 * k) for k in range(bound + 1) if (disp + 4 * k) in self.relocs}
        if len(case_of) != bound + 1 or any(t is None or not d.in_text(t) for t in case_of.values()):
            return []
        self._last_table = {"values": case_of, "index_reg": index, "byte_table": None}
        return sorted(set(case_of.values()))


class Program:
    """The DLL: relocations, every function reachable from the entry point, the exports and every address-taken
    function (pointers in data / immediates), jump tables."""

    def __init__(self, path_or_bytes, name=None):
        self.raw = path_or_bytes if isinstance(path_or_bytes, (bytes, bytearray)) else open(path_or_bytes, "rb").read()
        self.name = name or (os.path.basename(path_or_bytes) if isinstance(path_or_bytes, str) else "mission.dll")
        self.dll = d = mf.Dll(bytes(self.raw))
        pe = d.pe
        self.base = d.base
        self.size = pe.OPTIONAL_HEADER.SizeOfImage
        self.relocs = set()
        for blk in getattr(pe, "DIRECTORY_ENTRY_BASERELOC", []):
            for e in blk.entries:
                if e.type == 3:
                    self.relocs.add(d.base + e.rva)
                elif e.type != 0:
                    raise Unsupported("relocation type %d" % e.type)
        self.entry = d.base + pe.OPTIONAL_HEADER.AddressOfEntryPoint
        self.exports = {e.name.decode(): d.base + e.address for e in pe.DIRECTORY_ENTRY_EXPORT.symbols if e.name}
        self.imports = []          # (iat va, dll, name)
        for imp_dir in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []):
            for imp in imp_dir.imports:
                self.imports.append((imp.address, imp_dir.dll.decode(), imp.name.decode() if imp.name else None,
                                     imp.ordinal))
        self.taken = {self.entry} | set(self.exports.values())     # address-taken: need a thunk
        for r in self.relocs:
            if not d.in_text(r):
                t = d.u32(r)
                if t is not None and d.in_text(t):
                    self.taken.add(t)
        _Function.relocs = self.relocs
        self.functions: dict[int, mf.Function] = {}
        todo = sorted(self.taken)
        while todo:
            va = todo.pop()
            if va in self.functions:
                continue
            f = _Function(d, va)
            self.functions[va] = f
            for b in f.blocks.values():
                for i in b.insns:
                    if i.mnemonic == "call" and i.operands and i.operands[0].type == x86.X86_OP_IMM \
                            and d.in_text(i.operands[0].imm & M32):
                        todo.append(i.operands[0].imm & M32)
                    for op in i.operands:
                        if op.type == x86.X86_OP_IMM and i.imm_offset and (i.address + i.imm_offset) in self.relocs:
                            t = op.imm & M32
                            if d.in_text(t):
                                self.taken.add(t)
                                todo.append(t)
        self.tables = []           # (table va, entries) of the jump tables
        for f in self.functions.values():
            for b in f.blocks.values():
                if b.kind == "switch" and b.table:
                    i = b.insns[-1]
                    self.tables.append((mf._mem(i.operands[0])[3], b.table))

    def reloc_at(self, va):
        return va in self.relocs

    def sha256(self):
        return hashlib.sha256(self.raw).hexdigest()


# --------------------------------------------------------------------------------------------------- emitter --
class FunctionEmitter:
    def __init__(self, prog: Program, f: mf.Function, stats):
        self.p, self.f, self.stats = prog, f, stats
        self.out = []
        self.flag_kind = None      # flag kind set earlier in the current block (None = unknown here)

    # --- operands ---
    def addr(self, i, op):
        m = op.mem
        parts = []
        if m.base:
            parts.append(_R32[m.base])
        if m.index:
            r = _R32[m.index]
            parts.append(r if m.scale == 1 else "%s*%d" % (r, m.scale))
        disp = m.disp & M32
        if disp:
            if i.disp_offset and self.p.reloc_at(i.address + i.disp_offset):
                parts.append("(%s+D)" % _h(disp))
            else:
                parts.append(_h(disp))
        if not parts:
            return "0"
        if len(parts) == 1:
            return parts[0]
        return "((%s) & 0xFFFFFFFF)" % " + ".join(parts)

    def seg(self, op):
        return op.type == x86.X86_OP_MEM and op.mem.segment == x86.X86_REG_FS

    def rd(self, i, op, size=None, a=None):
        """Lua expression reading `op` (`a` = precomputed address variable for memory)."""
        size = size or op.size
        if op.type == x86.X86_OP_IMM:
            v = op.imm & MASK[size]
            if i.imm_offset and self.p.reloc_at(i.address + i.imm_offset):
                return "(%s+D)" % _h(v)
            return _h(v)
        if op.type == x86.X86_OP_REG:
            r = op.reg
            if r in _R32:
                return _R32[r]
            if r in _R16:
                return "(%s & 0xFFFF)" % _R16[r]
            if r in _LO8:
                return "(%s & 0xFF)" % _LO8[r]
            if r in _HI8:
                return "((%s >> 8) & 0xFF)" % _HI8[r]
            raise Unsupported("register %s" % i.reg_name(r))
        if op.type == x86.X86_OP_MEM:
            if self.seg(op):
                if size != 4:
                    raise Unsupported("fs access size %d" % size)
                return "(FS[%s] or 0)" % (a or self.addr(i, op))
            return "%s(%s)" % (READ[size], a or self.addr(i, op))
        raise Unsupported("operand type")

    def wr(self, i, op, val, size=None, a=None):
        size = size or op.size
        if op.type == x86.X86_OP_REG:
            r = op.reg
            if r in _R32:
                return "%s = %s" % (_R32[r], val)
            if r in _R16:
                n = _R16[r]
                return "%s = (%s & 0xFFFF0000) | (%s)" % (n, n, val)
            if r in _LO8:
                n = _LO8[r]
                return "%s = (%s & 0xFFFFFF00) | (%s)" % (n, n, val)
            if r in _HI8:
                n = _HI8[r]
                return "%s = (%s & 0xFFFF00FF) | ((%s) << 8)" % (n, n, val)
            raise Unsupported("register %s" % i.reg_name(r))
        if op.type == x86.X86_OP_MEM:
            if self.seg(op):
                return "FS[%s] = %s" % (a or self.addr(i, op), val)
            return "%s(%s, %s)" % (WRITE[size], a or self.addr(i, op), val)
        raise Unsupported("write to operand type %d" % op.type)

    def mem_addr(self, i, op, lines):
        """For read-modify-write: compute a memory operand's address once into `a`."""
        if op.type == x86.X86_OP_MEM:
            lines.append("a = %s" % self.addr(i, op))
            return "a"
        return None

    # --- flags ---
    def flags(self, kind, size, a=None, b=None, r="r"):
        s = ["FK = %d" % kind, "FW = %s" % _h(SIGN[size]), "FR = %s" % r]
        if a is not None:
            s += ["FA = %s" % a, "FB = %s" % b]
        self.flag_kind = kind
        return "; ".join(s)

    def cond(self, cc):
        fast = {2: _FAST_SUB, 3: _FAST_LOGIC, 1: _FAST_RES, 4: _FAST_RES}.get(self.flag_kind, {})
        return fast.get(cc) or _COND[cc]

    # --- one instruction ---
    def insn(self, i, b):
        m = i.mnemonic
        ops = i.operands
        L = []
        if m in ("nop", "wait", "fnclex", "cld") and not (m == "cld"):
            return L
        if m == "cld":
            return ["DF = 1"]
        if m == "std":
            return ["DF = -1"]
        if m == "mov":
            if ops[0].type == x86.X86_OP_MEM and ops[1].type != x86.X86_OP_MEM:
                return [self.wr(i, ops[0], self.rd(i, ops[1], ops[0].size))]
            return [self.wr(i, ops[0], self.rd(i, ops[1], ops[0].size))]
        if m == "movzx":
            return [self.wr(i, ops[0], self.rd(i, ops[1]))]
        if m == "movsx":
            sb = SIGN[ops[1].size]
            return ["x = %s" % self.rd(i, ops[1]),
                    self.wr(i, ops[0], "(((x ~ %s) - %s) & %s)" % (_h(sb), _h(sb), _h(MASK[ops[0].size])))]
        if m == "lea":
            if ops[0].size != 4:
                raise Unsupported("lea16")
            return [self.wr(i, ops[0], self.addr(i, ops[1]))]
        if m == "push":
            if ops[0].size == 2:
                raise Unsupported("push16")
            return ["x = %s" % self.rd(i, ops[0], 4), "ESP = (ESP - 4) & 0xFFFFFFFF", "w32(ESP, x)"]
        if m == "pop":
            return ["x = r32(ESP)", "ESP = (ESP + 4) & 0xFFFFFFFF", self.wr(i, ops[0], "x")]
        if m == "xchg":
            a = self.mem_addr(i, ops[0], L) or self.mem_addr(i, ops[1], L)
            L += ["x = %s" % self.rd(i, ops[0], a=a if ops[0].type == x86.X86_OP_MEM else None),
                  "y = %s" % self.rd(i, ops[1], a=a if ops[1].type == x86.X86_OP_MEM else None),
                  self.wr(i, ops[0], "y", a=a if ops[0].type == x86.X86_OP_MEM else None),
                  self.wr(i, ops[1], "x", a=a if ops[1].type == x86.X86_OP_MEM else None)]
            return L
        if m == "leave":
            return ["ESP = EBP", "EBP = r32(ESP)", "ESP = (ESP + 4) & 0xFFFFFFFF"]
        if m in ("add", "sub", "cmp", "and", "or", "xor", "test", "adc", "sbb"):
            size = ops[0].size
            mk = _h(MASK[size])
            a = self.mem_addr(i, ops[0], L)
            if m in ("xor", "sub") and ops[0].type == x86.X86_OP_REG and ops[1].type == x86.X86_OP_REG \
                    and ops[0].reg == ops[1].reg:
                L.append(self.wr(i, ops[0], "0"))
                L.append(self.flags(3 if m == "xor" else 2, size, "0", "0", "0"))
                return L
            L.append("x = %s" % self.rd(i, ops[0], a=a))
            L.append("y = %s" % self.rd(i, ops[1], size))
            if m in ("add",):
                L += ["r = (x + y) & %s" % mk, self.flags(1, size, "x", "y")]
            elif m in ("sub", "cmp"):
                L += ["r = (x - y) & %s" % mk, self.flags(2, size, "x", "y")]
            elif m in ("and", "test"):
                L += ["r = x & y", self.flags(3, size)]
            elif m == "or":
                L += ["r = x | y", self.flags(3, size)]
            elif m == "xor":
                L += ["r = x ~ y", self.flags(3, size)]
            elif m == "adc":
                L += ["c = fCF() and 1 or 0", "r = x + y + c", "FCF = r > %s" % mk, "r = r & %s" % mk,
                      "FOF = ((x ~ r) & (y ~ r) & %s) ~= 0" % _h(SIGN[size]), self.flags(4, size)]
            elif m == "sbb":
                L += ["c = fCF() and 1 or 0", "FCF = x < y + c", "r = (x - y - c) & %s" % mk,
                      "FOF = ((x ~ y) & (x ~ r) & %s) ~= 0" % _h(SIGN[size]), self.flags(4, size)]
            if m not in ("cmp", "test"):
                L.append(self.wr(i, ops[0], "r", a=a))
            return L
        if m in ("inc", "dec"):
            size = ops[0].size
            a = self.mem_addr(i, ops[0], L)
            sb = SIGN[size]
            L += ["c = fCF()", "x = %s" % self.rd(i, ops[0], a=a),
                  "r = (x %s 1) & %s" % ("+" if m == "inc" else "-", _h(MASK[size])),
                  "FCF = c", "FOF = r == %s" % (_h(sb) if m == "inc" else _h(sb - 1)), self.flags(4, size),
                  self.wr(i, ops[0], "r", a=a)]
            return L
        if m == "neg":
            size = ops[0].size
            a = self.mem_addr(i, ops[0], L)
            L += ["y = %s" % self.rd(i, ops[0], a=a), "r = (-y) & %s" % _h(MASK[size]),
                  self.flags(2, size, "0", "y"), self.wr(i, ops[0], "r", a=a)]
            return L
        if m == "not":
            a = self.mem_addr(i, ops[0], L)
            L += [self.wr(i, ops[0], "(~%s) & %s" % (self.rd(i, ops[0], a=a), _h(MASK[ops[0].size])), a=a)]
            return L
        if m in ("shl", "sal", "shr", "sar", "rol", "ror", "rcr", "rcl"):
            return self.shift(i, m, ops, L)
        if m in ("shld", "shrd"):
            raise Unsupported(m)
        if m == "imul":
            if len(ops) == 1:
                size = ops[0].size
                if size != 4:
                    raise Unsupported("imul8/16")
                L += ["x = SX(EAX) * SX(%s)" % self.rd(i, ops[0]), "EAX = x & 0xFFFFFFFF",
                      "EDX = (x >> 32) & 0xFFFFFFFF", "FCF = SX(EAX) ~= x", "FOF = FCF", "FR = EAX",
                      "FW = 0x80000000", "FK = 4"]
                self.flag_kind = 4
                return L
            src = ops[1] if len(ops) == 2 else ops[1]
            other = ops[0] if len(ops) == 2 else ops[2]
            if ops[0].size != 4:
                raise Unsupported("imul16")
            L += ["x = SX(%s) * SX(%s)" % (self.rd(i, src), self.rd(i, other)), "r = x & 0xFFFFFFFF",
                  "FCF = SX(r) ~= x", "FOF = FCF", self.flags(4, 4), self.wr(i, ops[0], "r")]
            return L
        if m == "mul":
            if ops[0].size != 4:
                raise Unsupported("mul8/16")
            L += ["x = EAX * %s" % self.rd(i, ops[0]), "EAX = x & 0xFFFFFFFF", "EDX = (x >> 32) & 0xFFFFFFFF",
                  "FCF = EDX ~= 0", "FOF = FCF", "FR = EAX", "FW = 0x80000000", "FK = 4"]
            self.flag_kind = 4
            return L
        if m in ("div", "idiv"):
            if ops[0].size != 4:
                raise Unsupported("div8/16")
            L += ["EAX, EDX = %s(EDX, EAX, %s)" % ("UDIV" if m == "div" else "IDIV", self.rd(i, ops[0]))]
            self.flag_kind = None
            return L
        if m == "cdq":
            return ["EDX = (EAX >= 0x80000000) and 0xFFFFFFFF or 0"]
        if m == "cwde":
            return ["EAX = (((EAX & 0xFFFF) ~ 0x8000) - 0x8000) & 0xFFFFFFFF"]
        if m == "cbw":
            return ["EAX = (EAX & 0xFFFF0000) | ((((EAX & 0xFF) ~ 0x80) - 0x80) & 0xFFFF)"]
        if m in _SET:
            return [self.wr(i, ops[0], "(%s) and 1 or 0" % self.cond(_SET[m]))]
        if m == "sahf":
            self.flag_kind = 5
            return ["x = (EAX >> 8) & 0xFF", "FCF = (x & 1) ~= 0", "FPF = (x & 4) ~= 0", "FZF = (x & 0x40) ~= 0",
                    "FSF = (x & 0x80) ~= 0", "FOF = fOF()", "FK = 5"]
        if m == "lahf":
            return ["EAX = (EAX & 0xFFFF00FF) | (FLAGS() & 0xFF) << 8"]
        # pushfd / popfd: the CRT start-up of newer compilers (Desert Storm II's DLLs) toggles the ID bit (21) to see
        # whether the CPU has cpuid; the bit never sticks here, so the CRT takes its no-cpuid / no-SSE2 (x87) paths
        if m == "pushfd":
            return ["ESP = (ESP - 4) & 0xFFFFFFFF", "w32(ESP, FLAGS() | (DF == -1 and 0x400 or 0))"]
        if m == "popfd":
            self.flag_kind = 5
            return ["x = r32(ESP)", "ESP = (ESP + 4) & 0xFFFFFFFF", "FCF = (x & 1) ~= 0", "FPF = (x & 4) ~= 0",
                    "FZF = (x & 0x40) ~= 0", "FSF = (x & 0x80) ~= 0", "FOF = (x & 0x800) ~= 0", "FK = 5",
                    "DF = (x & 0x400) ~= 0 and -1 or 1"]
        if m in ("movsd", "movsw", "movsb", "rep movsd", "rep movsw", "rep movsb"):
            n = {"d": 4, "w": 2, "b": 1}[m[-1]]
            if m.startswith("rep"):
                return ["MOVS(%d, true)" % n]
            return ["MOVS(%d, false)" % n]
        if m in ("stosd", "stosw", "stosb", "rep stosd", "rep stosw", "rep stosb"):
            n = {"d": 4, "w": 2, "b": 1}[m[-1]]
            return ["STOS(%d, %s)" % (n, "true" if m.startswith("rep") else "false")]
        if m == "repne scasb":
            self.flag_kind = 2
            return ["SCASB_NE()"]
        if m == "repe cmpsb":
            self.flag_kind = 2
            return ["CMPSB_E()"]
        if m == "int3" or m == "hlt":
            return ['error("x86: %s at %s")' % (m, _h(i.address))]
        if i.bytes[0] in range(0xD8, 0xE0) or (m.startswith("f") and m != "fs"):
            return self.fpu(i, L)
        raise Unsupported(m)

    def shift(self, i, m, ops, L):
        size = ops[0].size
        bits = size * 8
        mk = _h(MASK[size])
        sb = _h(SIGN[size])
        a = self.mem_addr(i, ops[0], L)
        cnt = self.rd(i, ops[1], 1) if len(ops) > 1 else "1"
        L.append("c = %s & 31" % cnt)
        L.append("if c ~= 0 then")
        L.append("  x = %s" % self.rd(i, ops[0], a=a))
        if m in ("shl", "sal"):
            L += ["  r = (x << c) & %s" % mk, "  FCF = c <= %d and ((x >> (%d - c)) & 1) == 1" % (bits, bits),
                  "  FOF = ((r & %s) ~= 0) ~= FCF" % sb, "  FK = 4; FW = %s; FR = r" % sb]
        elif m == "shr":
            L += ["  r = x >> c", "  FCF = ((x >> (c - 1)) & 1) == 1", "  FOF = (x & %s) ~= 0" % sb,
                  "  FK = 4; FW = %s; FR = r" % sb]
        elif m == "sar":
            L += ["  y = ((x ~ %s) - %s)" % (sb, sb), "  r = (y >> c | (y < 0 and ~(-1 >> c) or 0)) & %s" % mk,
                  "  FCF = ((y >> (c - 1)) & 1) == 1", "  FOF = false", "  FK = 4; FW = %s; FR = r" % sb]
        elif m in ("rol", "ror", "rcl", "rcr"):
            L.append("  FZF, FSF, FPF = fZF(), fSF(), fPF()")
            if m == "rol":
                L += ["  c = c %% %d" % bits, "  r = ((x << c) | (x >> (%d - c))) & %s" % (bits, mk),
                      "  FCF = (r & 1) == 1", "  FOF = ((r & %s) ~= 0) ~= FCF" % sb]
            elif m == "ror":
                L += ["  c = c %% %d" % bits, "  r = ((x >> c) | (x << (%d - c))) & %s" % (bits, mk),
                      "  FCF = (r & %s) ~= 0" % sb, "  FOF = ((r & %s) ~= 0) ~= ((r & %s) ~= 0)" % (sb, _h(SIGN[size] >> 1))]
            else:
                rot = "RCL" if m == "rcl" else "RCR"
                L += ["  r, FCF, FOF = %s(x, c, %d, fCF())" % (rot, bits)]
            L.append("  FK = 5")
        L.append("  " + self.wr(i, ops[0], "r", a=a))
        L.append("end")
        self.flag_kind = None
        return L

    # --- x87 (decoded from the opcode bytes: the reg-reg forms' operand order is ambiguous in disassemblers) ---
    def fpu(self, i, L):
        by = bytes(i.bytes)
        k = 0
        while by[k] in (0x9B, 0x66, 0xF2, 0xF3, 0x2E, 0x3E, 0x26, 0x36, 0x64, 0x65):
            if by[k] == 0x9B and len(by) == 1:
                return L
            k += 1
        op, modrm = by[k], by[k + 1] if k + 1 < len(by) else None
        reg = (modrm >> 3) & 7 if modrm is not None else 0
        memop = next((o for o in i.operands if o.type == x86.X86_OP_MEM), None)
        if modrm is not None and modrm < 0xC0:
            a = self.addr(i, memop)
            L.append("a = %s" % a)
            arith = ("+", "*", "cmp", "cmpp", "-", "-r", "/", "/r")
            if op in (0xD8, 0xDC, 0xDA, 0xDE):
                src = {0xD8: "rf(a)", 0xDC: "rd(a)", 0xDA: "(SX(r32(a)) + 0.0)",
                       0xDE: "((((r16(a)) ~ 0x8000) - 0x8000) + 0.0)"}[op]
                o = arith[reg]
                if o in ("cmp", "cmpp"):
                    L.append("fcmp(ST[FT], %s)" % src)
                    if o == "cmpp":
                        L.append("fpop()")
                else:
                    L.append("ST[FT] = " + self._farith(o, "ST[FT]", src))
                return L
            if op == 0xD9:
                if reg == 0:
                    return L + ["fpush(rf(a))"]
                if reg == 2:
                    return L + ["wf(a, ST[FT])"]
                if reg == 3:
                    return L + ["wf(a, fpop())"]
                if reg == 5:
                    return L + ["FLDCW(r16(a))"]
                if reg == 7:
                    return L + ["w16(a, FNSTCW())"]
            if op == 0xDD:
                if reg == 0:
                    return L + ["fpush(rd(a))"]
                if reg == 2:
                    return L + ["wd(a, ST[FT])"]
                if reg == 3:
                    return L + ["wd(a, fpop())"]
                if reg == 7:
                    return L + ["w16(a, SW())"]
            if op == 0xDB:
                if reg == 0:
                    return L + ["fpush(SX(r32(a)) + 0.0)"]
                if reg == 2:
                    return L + ["w32(a, FIST(ST[FT], 32))"]
                if reg == 3:
                    return L + ["w32(a, FIST(fpop(), 32))"]
            if op == 0xDF:
                if reg == 0:
                    return L + ["fpush((((r16(a)) ~ 0x8000) - 0x8000) + 0.0)"]
                if reg == 2:
                    return L + ["w16(a, FIST(ST[FT], 16))"]
                if reg == 3:
                    return L + ["w16(a, FIST(fpop(), 16))"]
                if reg == 5:
                    return L + ["fpush(I64(r32(a), r32(a + 4)))"]
                if reg == 7:
                    return L + ["x = FIST64(fpop())", "w32(a, x & 0xFFFFFFFF)", "w32(a + 4, (x >> 32) & 0xFFFFFFFF)"]
            raise Unsupported("x87 %02X /%d mem" % (op, reg))
        # register forms
        sti = modrm & 7
        s = "ST[FT - %d]" % sti if sti else "ST[FT]"
        if op == 0xD8:
            o = ("+", "*", "cmp", "cmpp", "-", "-r", "/", "/r")[reg]
            if o in ("cmp", "cmpp"):
                return ["fcmp(ST[FT], %s)" % s] + (["fpop()"] if o == "cmpp" else [])
            return ["ST[FT] = " + self._farith(o, "ST[FT]", s)]
        if op == 0xDC or op == 0xDE:
            # DC: st(i) = st(i) op st0; DE: same + pop. reg 4 (E0) = subr, 5 (E8) = sub, 6 (F0) = divr, 7 (F8) = div
            if op == 0xDE and modrm == 0xD9:
                return ["fcmp(ST[FT], ST[FT - 1])", "fpop()", "fpop()"]
            o = {0: "+", 1: "*", 4: "-r", 5: "-", 6: "/r", 7: "/"}.get(reg)
            if o is None:
                raise Unsupported("x87 %02X %02X" % (op, modrm))
            out = ["%s = %s" % (s, self._farith(o, s, "ST[FT]"))]
            if op == 0xDE:
                out.append("fpop()")
            return out
        if op == 0xD9:
            if 0xC0 <= modrm <= 0xC7:
                return ["fpush(%s)" % s]
            if 0xC8 <= modrm <= 0xCF:
                return ["ST[FT], %s = %s, ST[FT]" % (s, s)]
            simple = {0xE0: "ST[FT] = -ST[FT]", 0xE1: "ST[FT] = math.abs(ST[FT])", 0xE4: "fcmp(ST[FT], 0.0)",
                      0xE8: "fpush(1.0)", 0xEE: "fpush(0.0)", 0xEB: "fpush(FCONST(3))", 0xE9: "fpush(FCONST(1))",
                      0xEA: "fpush(FCONST(2))", 0xEC: "fpush(FCONST(4))", 0xED: "fpush(FCONST(5))",
                      0xFA: "ST[FT] = PR(math.sqrt(ST[FT]))", 0xFC: "ST[FT] = FRND(ST[FT])",
                      0xFE: "ST[FT] = X.fpu(1, ST[FT])", 0xFF: "ST[FT] = X.fpu(2, ST[FT])",
                      0xF3: "x = fpop(); ST[FT] = X.fpu(3, ST[FT], x)",
                      0xF2: "ST[FT] = X.fpu(4, ST[FT]); fpush(1.0)",
                      0xF1: "x = fpop(); ST[FT] = X.fpu(5, ST[FT], x)",
                      0xF0: "ST[FT] = X.fpu(6, ST[FT])", 0xFD: "ST[FT] = X.fpu(7, ST[FT], ST[FT - 1])",
                      0xF8: "ST[FT] = X.fpu(8, ST[FT], ST[FT - 1])", 0xD0: "",
                      0xFB: "x = ST[FT]; ST[FT] = X.fpu(1, x); fpush(X.fpu(2, x))"}
            if modrm in simple:
                return [simple[modrm]] if simple[modrm] else []
        if op == 0xDD:
            if 0xD8 <= modrm <= 0xDF:
                return ["%s = ST[FT]" % s, "fpop()"] if sti else ["fpop()"]
            if 0xD0 <= modrm <= 0xD7:
                return ["%s = ST[FT]" % s]
            if 0xE0 <= modrm <= 0xE7:
                return ["fcmp(ST[FT], %s)" % s]
            if 0xE8 <= modrm <= 0xEF:
                return ["fcmp(ST[FT], %s)" % s, "fpop()"]
            if 0xC0 <= modrm <= 0xC7:
                return []          # ffree
        if op == 0xDA and modrm == 0xE9:
            return ["fcmp(ST[FT], ST[FT - 1])", "fpop()", "fpop()"]
        if op == 0xDB and modrm in (0xE2, 0xE3):
            return [] if modrm == 0xE2 else ["FT = 0; ST = {}; FCW = 0x37F"]
        if op == 0xDF and modrm == 0xE0:
            return ["EAX = (EAX & 0xFFFF0000) | SW()"]
        raise Unsupported("x87 %02X %02X (%s)" % (op, modrm, i.mnemonic))

    def _farith(self, o, d, s):
        return {"+": "PR(%s + %s)", "*": "PR(%s * %s)", "-": "PR(%s - %s)", "/": "PR(%s / %s)"}.get(o, "") % (d, s) \
            if o in ("+", "*", "-", "/") else {"-r": "PR(%s - %s)", "/r": "PR(%s / %s)"}[o] % (s, d)

    # --- blocks ---
    def emit(self):
        f, p = self.f, self.p
        out = self.out
        out.append("F[%s] = function()" % _h(f.entry))
        out.append("local a, c, x, y, r, t")
        order = sorted(f.blocks)
        if order and order[0] != f.entry:
            out.append("goto L%X" % f.entry)
        for k, va in enumerate(order):
            b = f.blocks[va]
            nxt = order[k + 1] if k + 1 < len(order) else None
            out.append("::L%X::" % va)
            self.flag_kind = None
            last = len(b.insns) - 1
            for n, i in enumerate(b.insns):
                text = "%s %s" % (i.mnemonic, i.op_str)
                out.append("-- %08X %s" % (i.address, text.replace("\n", " ")))
                try:
                    if n == last and b.kind not in ("fall",):
                        out += self.terminator(b, i, nxt)
                    else:
                        if i.mnemonic == "call":
                            out += self.call(i)
                            self.flag_kind = None
                        elif i.mnemonic in ("ret", "retn"):
                            out += self.ret(i)
                        else:
                            res = self.insn(i, b)
                            out += res
                            if i.mnemonic not in _FLAG_WRITERS and not i.mnemonic.startswith(("j", "set")):
                                pass
                        if n == last and b.kind == "fall" and b.succ and b.succ[0] != nxt:
                            out.append("goto L%X" % b.succ[0])
                except Unsupported as e:
                    self.stats["unsupported"][str(e)] = self.stats["unsupported"].get(str(e), 0) + 1
                    out.append('error("x86: unsupported %s at %s")' % (str(e).replace('"', "'"), _h(i.address)))
        out.append("end")
        return out

    def call(self, i):
        op = i.operands[0]
        ret = "(%s+D)" % _h(i.address + i.size)
        if op.type == x86.X86_OP_IMM:
            t = op.imm & M32
            if t in self.p.functions:
                return ["ESP = (ESP - 4) & 0xFFFFFFFF", "w32(ESP, %s)" % ret, "F[%s]()" % _h(t)]
            return ['error("x86: call to unknown %s")' % _h(t)]
        return ["CALL(%s, %s)" % (self.rd(i, op, 4), ret)]

    def ret(self, i):
        n = i.operands[0].imm if i.operands else 0
        return ["ESP = (ESP + %d) & 0xFFFFFFFF" % (4 + n), "do return end"]

    def terminator(self, b, i, nxt):
        m = i.mnemonic
        if b.kind == "ret":
            return self.ret(i)
        if b.kind == "cond":
            taken = i.operands[0].imm & M32
            fall = i.address + i.size
            if taken not in self.f.blocks:
                return ['error("x86: branch out of the code at %s")' % _h(i.address)]
            if fall not in self.f.blocks:
                fall = None
            if m == "jecxz":
                c = "ECX == 0"
            elif m in _JCC:
                c = self.cond(_JCC[m])
            elif m == "loop":
                raise Unsupported("loop")
            else:
                raise Unsupported(m)
            out = ["if %s then goto L%X end" % (c, taken)]
            if fall is not None and fall != nxt:
                out.append("goto L%X" % fall)
            elif fall is None:
                out.append('error("x86: fell off %s")' % _h(i.address))
            return out
        if b.kind == "jmp":
            t = b.succ[0] if b.succ else (i.operands[0].imm & M32)
            if t not in self.f.blocks:
                return ['error("x86: jump out of the function at %s")' % _h(i.address)]
            return [] if t == nxt else ["goto L%X" % t]
        if b.kind == "switch":
            op = i.operands[0]
            out = ["t = (r32(%s) - D) & 0xFFFFFFFF" % self.addr(i, op)]
            first = True
            for tgt in sorted(set(b.table["values"].values())):
                out.append("%s t == %s then goto L%X" % ("if" if first else "elseif", _h(tgt), tgt))
                first = False
            out.append("end")
            out.append('error("x86: switch at %s out of range: " .. string.format("%%X", t))' % _h(i.address))
            return out
        if b.kind == "tailcall" or (b.kind == "end" and m == "jmp"):
            return ["do return TAIL(%s) end" % self.rd(i, i.operands[0], 4)]
        if b.kind == "end":
            if m in ("int3", "hlt"):
                return ['error("x86: %s at %s")' % (m, _h(i.address))]
            if m == "call":   # a call that never returns (block ends because the next byte isn't code)
                return self.call(i) + ['error("x86: returned from a no-return call at %s")' % _h(i.address)]
            return self.insn(i, b) + ['error("x86: ran off the decoded code after %s")' % _h(i.address)]
        raise Unsupported("block kind " + b.kind)


# --------------------------------------------------------------------------------------------------- runtime --
RUNTIME = r'''
-- ======================== x86 runtime (mission2lua %VERSION%) ========================
assert(x86rt and x86rt.install, "this mission needs DesertStormFix's mission runtime (dsfix.missionrt)")
x86rt.install()
local X = X86RT
local r8, r16, r32, w8, w16, w32 = X.r8, X.r16, X.r32, X.w8, X.w16, X.w32
local rf, wf, rd, wd = X.rf, X.wf, X.rd, X.wd
local EAX, EBX, ECX, EDX, ESI, EDI, EBP, ESP = 0, 0, 0, 0, 0, 0, 0, 0
local FK, FA, FB, FR, FW = 3, 0, 0, 0, 0x80000000
local FCF, FOF, FZF, FSF, FPF = false, false, false, false, false
local DF = 1
local FS = {}
local D = 0                  -- image block - DLL base (relocation delta)
local F = {}                 -- DLL va -> function
local FN = {}                -- block address -> function (function pointers)
local ST, FT, FC, FCW, PCS = {}, 0, 0, 0x27F, false
local PAR = {}
for v = 0, 255 do local n, x = 0, v; while x ~= 0 do n = n + (x & 1); x = x >> 1 end; PAR[v] = n % 2 == 0 end
local function SX(v) return ((v ~ 0x80000000) - 0x80000000) end
local function fCF() if FK == 1 then return FR < FA elseif FK == 2 then return FA < FB elseif FK == 3 then return false end return FCF end
local function fZF() if FK == 5 then return FZF end return FR == 0 end
local function fSF() if FK == 5 then return FSF end return (FR & FW) ~= 0 end
local function fOF()
  if FK == 1 then return ((FA ~ FR) & (FB ~ FR) & FW) ~= 0
  elseif FK == 2 then return ((FA ~ FB) & (FA ~ FR) & FW) ~= 0
  elseif FK == 3 then return false end
  return FOF
end
local function fPF() if FK == 5 then return FPF end return PAR[FR & 0xFF] end
local function FLAGS()
  return (fCF() and 1 or 0) | 2 | (fPF() and 4 or 0) | (fZF() and 0x40 or 0) | (fSF() and 0x80 or 0) | (fOF() and 0x800 or 0)
end
local function RCL(x, c, bits, cf)
  c = c % (bits + 1)
  local of
  for _ = 1, c do
    local top = (x >> (bits - 1)) & 1
    x = ((x << 1) | (cf and 1 or 0)) & ((1 << bits) - 1)
    cf = top == 1
  end
  of = (((x >> (bits - 1)) & 1) == 1) ~= cf
  return x, cf, of
end
local function RCR(x, c, bits, cf)
  c = c % (bits + 1)
  local of = (((x >> (bits - 1)) & 1) == 1) ~= cf
  for _ = 1, c do
    local low = x & 1
    x = (x >> 1) | ((cf and 1 or 0) << (bits - 1))
    cf = low == 1
  end
  return x, cf, of
end
local function UDIV(hi, lo, d)
  if d == 0 then error("x86: division by zero") end
  if hi == 0 then return lo // d, lo % d end
  local rem, q = hi, 0
  for k = 1, 2 do
    local chunk = (lo >> (32 - 16 * k)) & 0xFFFF
    rem = (rem << 16) | chunk
    q = (q << 16) | (rem // d)
    rem = rem % d
  end
  return q & 0xFFFFFFFF, rem
end
local function IDIV(hi, lo, d)
  d = SX(d)
  if d == 0 then error("x86: division by zero") end
  local n = (hi << 32) | lo
  local q = n // d
  if n % d ~= 0 and ((n < 0) ~= (d < 0)) then q = q + 1 end
  local r = n - q * d
  return q & 0xFFFFFFFF, r & 0xFFFFFFFF
end
local function MOVS(n, rep)
  local rd_, wr_ = ({r8, r16, nil, r32})[n], ({w8, w16, nil, w32})[n]
  local count = rep and ECX or 1
  if rep and DF == 1 and count > 0 then
    X.copy(EDI, ESI, count * n)
    ESI = (ESI + count * n) & 0xFFFFFFFF; EDI = (EDI + count * n) & 0xFFFFFFFF; ECX = 0
    return
  end
  for _ = 1, count do
    wr_(EDI, rd_(ESI)); ESI = (ESI + DF * n) & 0xFFFFFFFF; EDI = (EDI + DF * n) & 0xFFFFFFFF
  end
  if rep then ECX = 0 end
end
local function STOS(n, rep)
  local count = rep and ECX or 1
  local v = n == 4 and EAX or n == 2 and (EAX & 0xFFFF) or (EAX & 0xFF)
  if rep and DF == 1 and count > 0 then
    X.fill(EDI, v, n, count)
    EDI = (EDI + count * n) & 0xFFFFFFFF; ECX = 0
    return
  end
  local wr_ = ({w8, w16, nil, w32})[n]
  for _ = 1, count do wr_(EDI, v); EDI = (EDI + DF * n) & 0xFFFFFFFF end
  if rep then ECX = 0 end
end
local function SCASB_NE()   -- repne scasb
  local al = EAX & 0xFF
  local x = 0
  while ECX ~= 0 do
    x = r8(EDI); EDI = (EDI + DF) & 0xFFFFFFFF; ECX = (ECX - 1) & 0xFFFFFFFF
    if x == al then break end
  end
  FK = 2; FA = al; FB = x; FR = (al - x) & 0xFF; FW = 0x80
end
local function CMPSB_E()    -- repe cmpsb
  local x, y = 0, 0
  if ECX == 0 then return end
  while ECX ~= 0 do
    x = r8(ESI); y = r8(EDI)
    ESI = (ESI + DF) & 0xFFFFFFFF; EDI = (EDI + DF) & 0xFFFFFFFF; ECX = (ECX - 1) & 0xFFFFFFFF
    if x ~= y then break end
  end
  FK = 2; FA = x; FB = y; FR = (x - y) & 0xFF; FW = 0x80
end
-- x87
local function fpush(v) FT = FT + 1; ST[FT] = v end
local function fpop() local v = ST[FT]; ST[FT] = nil; FT = FT - 1; return v end
local function SW() return FC | (((-FT) & 7) << 11) end
local function fcmp(a, b)
  if a ~= a or b ~= b then FC = 0x4500 elseif a > b then FC = 0 elseif a < b then FC = 0x100 else FC = 0x4000 end
end
local function PR(v) if PCS then return X.f32(v) end return v end
local function FLDCW(v) FCW = v; PCS = ((v >> 8) & 3) == 0; X.fldcw(v) end
local function FNSTCW() FCW = X.fnstcw(); PCS = ((FCW >> 8) & 3) == 0; return FCW end
local function FCONST(k) return X.fpu(9, k) end
local function FRND(v)
  if v ~= v or v == math.huge or v == -math.huge then return v end
  local rc = (FCW >> 10) & 3
  local f
  if rc == 0 then
    f = math.floor(v); local d = v - f
    if d > 0.5 or (d == 0.5 and f % 2 == 1) then f = f + 1 end
  elseif rc == 1 then f = math.floor(v) elseif rc == 2 then f = math.ceil(v)
  else f = v >= 0 and math.floor(v) or math.ceil(v) end
  return f + 0.0
end
local function FIST(v, bits)
  local f = FRND(v)
  local lim = 1 << (bits - 1)
  if f ~= f or f >= lim or f < -lim then return lim end
  return math.tointeger(f) & ((1 << bits) - 1)
end
local function FIST64(v)
  local f = FRND(v)
  if f ~= f or f >= 2^63 or f < -2^63 then return math.mininteger end
  return math.tointeger(f)
end
local function I64(lo, hi) return ((hi << 32) | lo) + 0.0 end
-- calls
local ANIMS_SKIPPED = 0
local function NCALL(t, at)
  -- DS1: the script host has registered the standard animation states already; for those the game's register
  -- (API +0x04) would show "Attempt to load anim twice!" and return 1 changing nothing - take that path, no box
  if t == r32(0x5ED23C) and X.anim_loaded(r32(at)) then
    EAX = 1; ANIMS_SKIPPED = ANIMS_SKIPPED + 1
    return 0
  end
  local eax, edx, ecx, pop, hasf, fv = X.call(t, at, ECX, EDX)
  EAX, EDX, ECX = eax, edx, ecx
  if hasf then fpush(fv) end
  return pop
end
local function CALL(t, ret)
  local f = FN[t]
  if f then ESP = (ESP - 4) & 0xFFFFFFFF; w32(ESP, ret); f(); return end
  if t >= X.image_lo and t < X.image_hi then error(string.format("x86: call into the image at %X (no function)", t)) end
  ESP = (ESP + NCALL(t, ESP)) & 0xFFFFFFFF
end
local function TAIL(t)
  local f = FN[t]
  if f then return f() end
  if t >= X.image_lo and t < X.image_hi then error(string.format("x86: jump into the image at %X (no function)", t)) end
  local pop = NCALL(t, (ESP + 4) & 0xFFFFFFFF)
  ESP = (ESP + 4 + pop) & 0xFFFFFFFF
end
-- the game calls a DLL function (thunk): arguments at argp (the native stack)
function __x86_enter(va, argp, ecx, edx)
  local save = ESP
  FNSTCW()
  ESP = (ESP - 64) & 0xFFFFFFFF
  X.copy(ESP, argp, 64)
  ESP = (ESP - 4) & 0xFFFFFFFF
  w32(ESP, 0)
  local entry = ESP
  ECX, EDX = ecx, edx
  local saveFT, saveST = FT, ST
  FT, ST = 0, {}
  F[va]()
  local pop = (ESP - entry - 4) & 0xFFFFFFFF
  local eax, edx2 = EAX, EDX
  local hasf, fv = FT > 0, ST[FT]
  FT, ST = saveFT, saveST
  ESP = save
  return eax, pop, edx2, hasf, fv
end
-- emulated call with arguments (from Lua)
local function ENTER(va, ...)
  local args = table.pack(...)
  local save = ESP
  for k = args.n, 1, -1 do ESP = (ESP - 4) & 0xFFFFFFFF; w32(ESP, args[k]) end
  ESP = (ESP - 4) & 0xFFFFFFFF
  w32(ESP, 0)
  F[va]()
  ESP = save
  return EAX
end
'''


def translate(path_or_bytes, name=None, archive=None) -> tuple[str, dict]:
    """(Lua source, stats) for one mission DLL."""
    p = Program(path_or_bytes, name)
    stats = {"functions": len(p.functions), "instructions": 0, "unsupported": {}, "thunks": 0, "warnings": []}
    body = []
    seen = set()
    for va in sorted(p.functions):
        f = p.functions[va]
        for b in f.blocks.values():
            for i in b.insns:
                if i.address not in seen:
                    seen.add(i.address)
        body += FunctionEmitter(p, f, stats).emit()
    stats["instructions"] = len(seen)
    # thunks: every address-taken function; 5 bytes each - warn on overlaps
    thunks = sorted(p.taken & set(p.functions))
    seh = 0
    for a, b in zip(thunks, thunks[1:]):
        if b - a < 5:
            # MSVC __except filter + handler of one scope-table entry (`push 1; pop eax; ret` then the handler): only
            # _except_handler3 would call them, and the emulation never registers an SEH frame with Windows
            if bytes(p.dll.img[a - p.base:a - p.base + 4]) == bytes.fromhex("6a0158c3"):
                seh += 1
            else:
                stats["warnings"].append("thunks at %X and %X overlap" % (a, b))
    stats["seh_pairs"] = seh
    for t, tab in p.tables:
        n = (max(tab["values"].values()) and len(set(tab["values"].values()))) if tab.get("byte_table")             else max(tab["values"]) + 1
        for th in thunks:
            if t - 5 < th < t + 4 * n:
                stats["warnings"].append("thunk %X overlaps jump table %X" % (th, t))
    stats["thunks"] = len(thunks)
    level = archive or os.path.splitext(p.name)[0].lower()
    head = [
        "--@archive %s" % level,
        "--@standard_anims off",   # (VERSION 2) the DLL registers its own animations, as on the DLL path
        "-- %s translated by mission2lua %d (ConflictDesertStormModKit) from %s, SHA-256 %s." % (
            p.name, VERSION, p.name, p.sha256()),
        "-- Generated from your own game files: game code. Never commit or distribute this file.",
        "-- %d functions, %d instructions, %d thunks." % (stats["functions"], stats["instructions"], len(thunks)),
    ]
    lua = ["\n".join(head), RUNTIME.replace("%VERSION%", str(VERSION))]
    lua.append("-- ======================== functions ========================")
    lua += body
    # image
    pe = p.dll.pe
    secs = []
    for s in pe.sections:
        data = s.get_data()[:max(s.Misc_VirtualSize, 0) or s.SizeOfRawData]
        data = data.rstrip(b"\0") if s.Name.startswith(b".data") else data
        if s.Name.startswith(b".reloc"):
            continue
        secs.append('{%s, "%s"}' % (_h(s.VirtualAddress), data.hex()))
    relocs = "".join(struct.pack("<I", r - p.base).hex() for r in sorted(p.relocs))
    imps = []
    for iat, dll, nm, ordinal in p.imports:
        imps.append('{%s, "%s", %s}' % (_h(iat - p.base), dll, ('"%s"' % nm) if nm else str(ordinal)))
    lua.append("-- ======================== image ========================")
    lua.append("local IMAGE = {size = %s, base = %s, text_lo = %s, text_hi = %s," % (
        _h(p.size), _h(p.base), _h(p.dll.text_lo - p.base), _h(p.dll.text_hi - p.base)))
    lua.append("  sections = {%s}," % ",\n    ".join(secs))
    lua.append('  relocs = "%s",' % relocs)
    lua.append("  thunks = {%s}," % ", ".join(_h(t - p.base) for t in thunks))
    lua.append("  imports = {%s}," % ",\n    ".join(imps))
    lua.append("}")
    lua.append(MAIN % {"entry": _h(p.entry), "setup": _h(p.exports["LevelSetup"]), "name": p.name})
    return "\n".join(lua) + "\n", stats


MAIN = r'''
-- ======================== level ========================
local ENTRY, LEVELSETUP = %(entry)s, %(setup)s
function __x86_detach()                     -- pse.lua.level_end: DLL_PROCESS_DETACH, as FreeLibrary would
  if X.loaded() then ENTER(ENTRY, X.image_lo, 0, 0) end
end
function init()
  local base = X.load_image(IMAGE)
  D = base - IMAGE.base
  for va, fn in pairs(F) do FN[(va + D) & 0xFFFFFFFF] = fn end
  ESP = X.stack_top()
  FNSTCW()
  local ok = ENTER(ENTRY, base, 1, 0)       -- DllMain(hinst, DLL_PROCESS_ATTACH, 0): the CRT start-up
  if ok == 0 then error("%(name)s: DLL_PROCESS_ATTACH failed") end
  local object = ENTER(LEVELSETUP, 0x5ED238) -- LevelSetup(&api) -> script object (FUN_00449260)
  X.set_object(object)
  game.log(string.format("mission2lua: %%d animation state(s) already registered by the script host (not again)", ANIMS_SKIPPED))
  game.log(string.format("mission2lua: %(name)s running from Lua (image %%08X, script object %%08X)", base, object))
end
function update() end                        -- the DLL's own update is called through its script object
function on_message() end
'''
