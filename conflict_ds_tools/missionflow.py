"""Mission logic of a shipped mission DLL (MissionN.dll, TrainingN.dll) as readable, structured steps.

A mission DLL's LevelSetup returns a script object of 6 cdecl callbacks (init, update, message, save, load, size).
Everything a mission does goes through the game's script API: a table of cdecl function pointers the DLL keeps in a
global and calls as ``call [reg + slot]``. This module reads the player's own DLL (nothing of it ships with the
tools), disassembles the callbacks and every function they reach, and rebuilds:

* the message dispatch: message id -> the code that runs (``Script_Message`` switches on the id, usually through a
  byte table), with the message's parameters as named values (``msg[0]``, ``msg[1]`` ...);
* each function as structured statements - ``if`` / ``switch`` / ``loop`` / API calls with their arguments resolved
  (constants, strings, floats, message parameters, the function's own arguments, mission globals, results of earlier
  calls) / local helper calls / writes to mission globals (state flags);
* the mission globals: which values are written where (state machines such as "mission phase 0 intro, 1 overview,
  2 game" show up as one global compared with 0 / 1 / 2).

The output is plain data (``analyse(path)`` -> dict); ``conflict_ds_tools.missionflow_text`` style rendering lives
in the editor, which knows the scene (script ids -> objects, region indices -> names, text hashes -> texts) and the
API slot names (editor/api_slots.json).

Method: recursive-descent disassembly (capstone) per function, basic blocks, jump tables (``jmp [reg*4 + table]``,
optionally indexed through a byte table), forward symbolic evaluation of registers / stack slots per block (merged
over predecessors), conditions from ``cmp`` / ``test`` + ``jcc``, then structuring by immediate post-dominators.
What can't be resolved is kept as ``{"k": "unknown"}`` - the rendering says so instead of guessing.
"""
from __future__ import annotations

import struct

import capstone
from capstone import x86

_MD = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
_MD.detail = True
_REG = {}                                   # capstone register id -> 32-bit name
for _n32, _subs in (("eax", ("eax", "ax", "al", "ah")), ("ebx", ("ebx", "bx", "bl", "bh")),
                    ("ecx", ("ecx", "cx", "cl", "ch")), ("edx", ("edx", "dx", "dl", "dh")),
                    ("esi", ("esi", "si")), ("edi", ("edi", "di")), ("ebp", ("ebp", "bp")), ("esp", ("esp", "sp"))):
    for _s in _subs:
        _REG[getattr(x86, "X86_REG_" + _s.upper())] = _n32
_JCC = {"je": "==", "jz": "==", "jne": "!=", "jnz": "!=", "jl": "<", "jb": "<", "jg": ">", "ja": ">",
        "jle": "<=", "jbe": "<=", "jge": ">=", "jae": ">=", "js": "<0", "jns": ">=0"}
_NEG = {"==": "!=", "!=": "==", "<": ">=", ">=": "<", ">": "<=", "<=": ">", "<0": ">=0", ">=0": "<0"}
MAX_STMTS = 4000
QUEUE_CALL = 0x518                          # queue_call(callback, a, b, delay ms): callback(event) after the delay,
QUEUE_FIELDS = {0x34: 0, 0x38: 1}           # reading a / b at event +0x34 / +0x38                            # per function, against pathological duplication while structuring


def V(k, **kw):
    return {"k": k, **kw}


UNKNOWN = V("unknown")


def _same(a, b):
    return a == b


class Dll:
    def __init__(self, path_or_bytes):
        import pefile
        self.pe = pefile.PE(data=path_or_bytes) if isinstance(path_or_bytes, (bytes, bytearray)) else pefile.PE(path_or_bytes)
        self.base = self.pe.OPTIONAL_HEADER.ImageBase
        self.img = self.pe.get_memory_mapped_image()
        text = [s for s in self.pe.sections if s.Name.startswith(b".text")][0]
        self.text_lo = self.base + text.VirtualAddress
        self.text_hi = self.text_lo + text.Misc_VirtualSize
        self.imports = {}
        for d in getattr(self.pe, "DIRECTORY_ENTRY_IMPORT", []):
            for imp in d.imports:
                if imp.name:
                    self.imports[imp.address] = imp.name.decode(errors="replace")
        self._insn = {}

    def u32(self, va):
        o = va - self.base
        return struct.unpack_from("<I", self.img, o)[0] if 0 <= o <= len(self.img) - 4 else None

    def u8(self, va):
        o = va - self.base
        return self.img[o] if 0 <= o < len(self.img) else None

    def in_text(self, va):
        return self.text_lo <= va < self.text_hi

    def cstr(self, va):
        o = va - self.base
        if not 0 <= o < len(self.img):
            return None
        end = self.img.find(b"\0", o)
        s = self.img[o:end]
        if 0 < len(s) < 200 and all(32 <= c < 127 or c in (9, 10, 13) for c in s):
            return s.decode("latin-1")
        return None

    def insn(self, va):
        if va not in self._insn:
            o = va - self.base
            got = next(_MD.disasm(bytes(self.img[o:o + 16]), va), None)
            self._insn[va] = got
        return self._insn[va]


# ------------------------------------------------------------------------------------------------- functions --
class Block:
    __slots__ = ("start", "insns", "succ", "kind", "table", "preds")

    def __init__(self, start):
        self.start, self.insns, self.succ, self.kind, self.table, self.preds = start, [], [], "fall", None, []


def _mem(op):
    m = op.mem
    return (_REG.get(m.base), _REG.get(m.index), m.scale, m.disp & 0xFFFFFFFF if m.disp >= 0 else m.disp)


class Function:
    """Basic blocks of one function (recursive descent from its entry)."""

    def __init__(self, dll: Dll, entry: int):
        self.dll, self.entry = dll, entry
        self.blocks: dict[int, Block] = {}
        self.ret_pop = 0
        self._build()

    def _build(self):
        d = self.dll
        starts, todo, seen = {self.entry}, [self.entry], set()
        # pass 1: find block starts
        while todo:
            va = todo.pop()
            while va not in seen:
                seen.add(va)
                i = d.insn(va)
                if i is None or not d.in_text(va):
                    break
                m = i.mnemonic
                if m in ("ret", "retn"):
                    if i.operands:
                        self.ret_pop = i.operands[0].imm
                    break
                if m == "jmp":
                    op = i.operands[0]
                    if op.type == x86.X86_OP_IMM:
                        t = op.imm
                        if d.in_text(t):
                            starts.add(t); todo.append(t)
                    else:
                        for t in self._table_targets(i):
                            starts.add(t); todo.append(t)
                    break
                if m in _JCC or m.startswith("j") and m != "jmp" and i.operands and i.operands[0].type == x86.X86_OP_IMM:
                    t = i.operands[0].imm
                    starts.update((t, i.address + i.size)); todo += [t, i.address + i.size]
                    break
                if m == "int3" or m == "hlt":
                    break
                va = i.address + i.size
        # pass 2: cut blocks
        for s in sorted(starts):
            if s in self.blocks or not d.in_text(s):
                continue
            b = Block(s)
            va = s
            while True:
                i = d.insn(va)
                if i is None:
                    b.kind = "end"; break
                b.insns.append(i)
                m = i.mnemonic
                nxt = i.address + i.size
                if m in ("ret", "retn"):
                    b.kind = "ret"; break
                if m == "jmp":
                    op = i.operands[0]
                    if op.type == x86.X86_OP_IMM:
                        b.kind = "jmp"; b.succ = [op.imm]
                    elif op.type == x86.X86_OP_MEM and _mem(op)[0] and _mem(op)[1] is None:
                        b.kind = "tailcall"          # jmp [eax + slot]: an API call in tail position
                    else:
                        tg = self._table_targets(i)
                        b.kind = "switch" if tg else "end"
                        b.succ = tg
                        b.table = getattr(self, "_last_table", None)
                    break
                if m.startswith("j") and m != "jmp":
                    b.kind = "cond"; b.succ = [i.operands[0].imm, nxt]; break
                if m in ("int3", "hlt"):
                    b.kind = "end"; break
                if nxt in starts:
                    b.kind = "fall"; b.succ = [nxt]; break
                va = nxt
            self.blocks[s] = b
        for b in self.blocks.values():
            b.succ = [t for t in b.succ if t in self.blocks]
            for t in b.succ:
                self.blocks[t].preds.append(b.start)

    def _table_targets(self, i):
        """jmp dword ptr [reg*4 + table]; the index may come from a byte table (mov al, [reg + bytes])."""
        self._last_table = None
        op = i.operands[0]
        if op.type != x86.X86_OP_MEM:
            return []
        base, index, scale, disp = _mem(op)
        if base is not None or index is None or scale != 4:
            return []
        d = self.dll
        # bound: look back a few instructions for cmp <reg>, N / ja, and a byte table load
        back = self._back(i.address, 12)
        bound, byte_table, byte_index = None, None, None
        for p in reversed(back):
            if p.mnemonic in ("mov", "movzx") and len(p.operands) == 2 and p.operands[1].type == x86.X86_OP_MEM \
                    and p.operands[1].size == 1 and _REG.get(p.operands[0].reg) == index:
                bb, bi, bs, bd = _mem(p.operands[1])
                if bb is not None and bi is None and bd:
                    byte_table, byte_index = bd, bb
            if p.mnemonic == "cmp" and p.operands[1].type == x86.X86_OP_IMM and p.operands[0].type == x86.X86_OP_REG:
                r = _REG.get(p.operands[0].reg)
                if r in (index, byte_index):
                    bound = p.operands[1].imm
                    break
        if bound is None or bound > 0x400:
            return []
        if byte_table:
            idx = [d.u8(byte_table + k) for k in range(bound + 1)]
            if any(v is None for v in idx):
                return []
            n = max(idx) + 1
            entries = [d.u32(disp + 4 * k) for k in range(n)]
            case_of = {k: entries[idx[k]] for k in range(bound + 1)}
        else:
            entries = [d.u32(disp + 4 * k) for k in range(bound + 1)]
            case_of = {k: entries[k] for k in range(bound + 1)}
        if any(t is None or not d.in_text(t) for t in case_of.values()):
            return []
        self._last_table = {"values": case_of, "index_reg": byte_index if byte_table else index,
                            "byte_table": byte_table}
        return sorted(set(case_of.values()))

    def _back(self, va, n):
        """Up to n instructions before va (linear, by trying start offsets)."""
        d = self.dll
        for k in range(min(64, va - d.text_lo), 0, -1):
            out, a = [], va - k
            ok = False
            while a < va:
                i = d.insn(a)
                if i is None:
                    break
                out.append(i); a += i.size
                if a == va:
                    ok = True
            if ok and len(out) >= 1:
                return out[-n:]
        return []


# ------------------------------------------------------------------------------------------ symbolic eval --
class State:
    __slots__ = ("reg", "stack", "esp", "flags", "ebp_esp", "floor")

    def __init__(self):
        self.reg = {}            # name -> value
        self.stack = {}          # esp-relative offset (from function entry) -> value
        self.esp = 0             # current esp offset (entry = 0; [esp] = return address)
        self.flags = None        # (left, right, kind)
        self.ebp_esp = None      # esp offset ebp was set to (push ebp; mov ebp, esp)
        self.floor = 0           # esp right after the last call: a call's arguments are what was pushed since

    def copy(self):
        s = State()
        s.reg, s.stack, s.esp, s.flags, s.ebp_esp = dict(self.reg), dict(self.stack), self.esp, self.flags, self.ebp_esp
        s.floor = self.floor
        return s


def _merge(states):
    states = [s for s in states if s is not None]
    if not states:
        return State()
    out = states[0].copy()
    for s in states[1:]:
        out.reg = {k: v for k, v in out.reg.items() if s.reg.get(k) == v}
        out.stack = {k: v for k, v in out.stack.items() if s.stack.get(k) == v}
        if s.esp != out.esp:
            out.esp = out.esp           # keep the first; mismatches are rare in this code
        if s.ebp_esp != out.ebp_esp:
            out.ebp_esp = None
    out.flags = None
    return out


class Analyser:
    def __init__(self, dll: Dll, api_global: int, globals_lo: int, globals_hi: int):
        self.dll, self.api_global = dll, api_global
        self.glo, self.ghi = globals_lo, globals_hi
        self.functions: dict[int, Function] = {}
        self.bodies: dict[int, list] = {}
        self.calls_out: dict[int, set] = {}
        self.global_writes: dict[int, list] = {}
        self.global_reads: dict[int, set] = {}
        self._msg_ptr_arg = None

    # --- values ---
    def _imm(self, v):
        v &= 0xFFFFFFFF
        s = self.dll.cstr(v) if self.dll.base <= v < self.dll.base + len(self.dll.img) else None
        if s is not None:
            return V("str", s=s)
        if self.dll.in_text(v):
            return V("code", a=v)
        return V("int", v=v - (1 << 32) if v & 0x80000000 and v > 0xFFFF0000 else v)

    def _read_mem(self, st: State, op, fn_entry):
        base, index, scale, disp = _mem(op)
        if base is None and index is None:
            if disp == self.api_global:
                return V("api")
            if self.glo <= disp < self.ghi:
                self.global_reads.setdefault(disp, set()).add(fn_entry)
                return V("global", a=disp)
            imp = self.dll.imports.get(disp)
            if imp:
                return V("import", name=imp)
            return V("mem", a=disp)
        if base == "esp" and index is None:
            off = st.esp + _signed(disp)
            if off in st.stack:
                return st.stack[off]
            if off >= 4 and off % 4 == 0:
                return V("arg", i=(off - 4) // 4)
            return V("local", off=off)
        if base == "ebp" and index is None and st.ebp_esp is not None:
            off = st.ebp_esp + _signed(disp)
            if off in st.stack:
                return st.stack[off]
            if off >= 4 and off % 4 == 0:
                return V("arg", i=(off - 4) // 4)
            return V("local", off=off)
        if index is None and base:
            b = st.reg.get(base)
            if b is not None and b.get("k") == "arg" and _signed(disp) % 4 == 0 and _signed(disp) >= 0 \
                    and b.get("i") == self._msg_ptr_arg:
                return V("msg", i=_signed(disp) // 4)
            if b is not None and b.get("k") == "global":
                return V("field", of=b, off=_signed(disp))
            if b is not None and b.get("k") == "arg" and b.get("i") != self._msg_ptr_arg:
                return V("argfield", i=b["i"], off=_signed(disp))
            if b is not None and b.get("k") == "api":
                return V("apislot", slot=_signed(disp))
        return UNKNOWN

    def _val(self, st: State, op, fn_entry):
        if op.type == x86.X86_OP_IMM:
            return self._imm(op.imm)
        if op.type == x86.X86_OP_REG:
            r = _REG.get(op.reg)
            if r is None:
                return UNKNOWN
            if op.size != 4:
                return UNKNOWN if r not in st.reg else st.reg[r]
            return st.reg.get(r, V("reg", r=r))
        if op.type == x86.X86_OP_MEM:
            return self._read_mem(st, op, fn_entry)
        return UNKNOWN

    def _write(self, st: State, op, val, fn_entry, out, i):
        if op.type == x86.X86_OP_REG:
            r = _REG.get(op.reg)
            if r:
                if op.size == 4:
                    st.reg[r] = val
                elif val.get("k") in ("global", "int", "msg", "arg") and op.size == 1 and op.reg in (
                        x86.X86_REG_AL, x86.X86_REG_BL, x86.X86_REG_CL, x86.X86_REG_DL):
                    st.reg[r] = val             # byte load into al etc.: callees use the low byte (bool flags)
                else:
                    st.reg[r] = UNKNOWN
            return
        if op.type == x86.X86_OP_MEM:
            base, index, scale, disp = _mem(op)
            if base is None and index is None and self.glo <= disp < self.ghi:
                size = op.size
                self.global_writes.setdefault(disp, []).append((fn_entry, val))
                out.append({"op": "set", "var": V("global", a=disp, size=size), "value": val, "at": i.address})
                return
            if base == "esp" and index is None:
                st.stack[st.esp + _signed(disp)] = val
                return
            if base == "ebp" and index is None and st.ebp_esp is not None:
                st.stack[st.ebp_esp + _signed(disp)] = val
                return

    def _call(self, st: State, i, target, out, fn_entry, tail=False):
        """A call: arguments are the stack slots above esp (cdecl); count = pushes the caller pops afterwards, or the
        callee's ret n."""
        base_off = st.esp + (4 if tail else 0)
        # arguments = what was pushed since the previous call (MSVC pops several calls' arguments with one add esp)
        nargs = max(0, (st.floor - base_off) // 4)
        stmt = None
        if target.get("k") == "apislot":
            args = [st.stack.get(base_off + 4 * k, UNKNOWN) for k in range(nargs)]
            stmt = {"op": "api", "slot": target["slot"], "args": args, "at": i.address}
            for v in args:
                if v.get("k") == "code":
                    self.calls_out.setdefault(fn_entry, set()).add(v["a"])
        elif target.get("k") == "code":
            callee = target["a"]
            f = self.function(callee)
            if f.ret_pop:
                nargs = f.ret_pop // 4
            args = [st.stack.get(base_off + 4 * k, UNKNOWN) for k in range(nargs)]
            this = st.reg.get("ecx") if f.ret_pop and nargs * 4 == f.ret_pop else None
            stmt = {"op": "call", "fn": callee, "args": args, "at": i.address}
            if this is not None and this.get("k") not in ("unknown", "reg"):
                stmt["this"] = this
            self.calls_out.setdefault(fn_entry, set()).add(callee)
            st.esp += f.ret_pop
        elif target.get("k") == "import":
            args = [st.stack.get(base_off + 4 * k, UNKNOWN) for k in range(nargs)]
            stmt = {"op": "import", "name": target["name"], "args": args, "at": i.address}
        else:
            stmt = {"op": "call_unknown", "text": f"{i.mnemonic} {i.op_str}", "at": i.address}
        out.append(stmt)
        res = V("result", at=i.address)
        if stmt["op"] == "api":
            res["slot"] = stmt["slot"]
        st.reg.update(eax=res, ecx=UNKNOWN, edx=UNKNOWN)
        st.flags = None
        st.floor = st.esp
        return stmt

    def _pushed(self, st, off):
        n = 0
        while off + 4 * n in st.stack and n < 12:
            n += 1
        return n

    def _exec_block(self, b: Block, st: State, fn_entry):
        out = []
        for i in b.insns:
            m, ops = i.mnemonic, i.operands
            if m == "push":
                v = self._val(st, ops[0], fn_entry)
                st.esp -= 4
                st.stack[st.esp] = v
                if ops[0].type == x86.X86_OP_REG and _REG.get(ops[0].reg) == "ebp":
                    pass
            elif m == "pop":
                v = st.stack.get(st.esp, UNKNOWN)
                st.esp += 4
                st.floor = max(st.floor, st.esp)
                self._write(st, ops[0], v, fn_entry, out, i)
            elif m in ("mov", "movzx", "movsx") and len(ops) == 2:
                if ops[0].type == x86.X86_OP_REG and _REG.get(ops[0].reg) == "ebp" and ops[1].type == x86.X86_OP_REG \
                        and _REG.get(ops[1].reg) == "esp":
                    st.ebp_esp = st.esp
                    continue
                v = self._val(st, ops[1], fn_entry)
                if m != "mov" or ops[1].size != 4:
                    if v.get("k") not in ("global", "msg", "arg", "int", "result"):
                        v = UNKNOWN
                    elif v.get("k") == "global":
                        v = dict(v, size=ops[1].size)
                if m in ("movzx", "movsx") and ops[0].type == x86.X86_OP_REG:
                    st.reg[_REG.get(ops[0].reg)] = v
                    continue
                self._write(st, ops[0], v, fn_entry, out, i)
            elif m == "lea" and ops[0].type == x86.X86_OP_REG:
                base, index, scale, disp = _mem(ops[1])
                r = _REG.get(ops[0].reg)
                if base == "esp" and index is None:
                    st.reg[r] = V("addr", off=st.esp + _signed(disp))
                elif base is None and index is None:
                    st.reg[r] = self._imm(disp)
                else:
                    st.reg[r] = UNKNOWN
            elif m == "xor" and ops[0].type == x86.X86_OP_REG and ops[1].type == x86.X86_OP_REG \
                    and ops[0].reg == ops[1].reg:
                st.reg[_REG[ops[0].reg]] = V("int", v=0)
                st.flags = None
            elif m in ("sub", "add") and ops[0].type == x86.X86_OP_REG and _REG.get(ops[0].reg) == "esp" \
                    and ops[1].type == x86.X86_OP_IMM:
                st.esp += ops[1].imm if m == "add" else -ops[1].imm
                st.floor = max(st.floor, st.esp) if m == "add" else st.esp   # sub esp = locals, not arguments
            elif m in ("sub", "dec", "add", "inc") and ops[0].type == x86.X86_OP_REG and _REG.get(ops[0].reg) \
                    and (m in ("dec", "inc") or ops[1].type == x86.X86_OP_IMM):
                # sub eax, N / dec eax chains before je: compare the original value with the running offset
                r = _REG[ops[0].reg]
                cur = st.reg.get(r, V("reg", r=r))
                n = (1 if m in ("dec", "inc") else ops[1].imm) * (1 if m in ("sub", "dec") else -1)
                if cur.get("k") == "int":
                    st.reg[r] = V("int", v=cur["v"] - n)
                    st.flags = (st.reg[r], V("int", v=0), "cmp")
                    continue
                base, total = (cur["x"], _signed(cur["n"] + n)) if cur.get("k") == "minus" else (cur, _signed(n))
                st.reg[r] = V("minus", x=base, n=total)
                st.flags = (base, V("int", v=total), "cmp")
            elif m == "cmp":
                a, c = self._val(st, ops[0], fn_entry), self._val(st, ops[1], fn_entry)
                if a.get("k") == "minus" and c.get("k") == "int":      # (x - n) == c  ->  x == c + n
                    a, c = a["x"], V("int", v=_signed(c["v"] + a["n"]))
                st.flags = (a, c, "cmp")
            elif m == "test":
                a, c = self._val(st, ops[0], fn_entry), self._val(st, ops[1], fn_entry)
                if ops[0].type == x86.X86_OP_REG and ops[1].type == x86.X86_OP_REG and ops[0].reg == ops[1].reg:
                    st.flags = (a, V("int", v=0), "cmp")
                else:
                    st.flags = (a, c, "test")
            elif m == "call":
                op = ops[0]
                tgt = self._imm(op.imm) if op.type == x86.X86_OP_IMM else self._val(st, op, fn_entry)
                if tgt.get("k") == "mem" and op.type == x86.X86_OP_MEM:
                    tgt = V("import", name=self.dll.imports.get(_mem(op)[3], "?"))
                self._call(st, i, tgt, out, fn_entry)
            elif m == "jmp" and b.kind == "tailcall":
                tgt = self._val(st, ops[0], fn_entry)
                self._call(st, i, tgt, out, fn_entry, tail=True)
                out.append({"op": "return", "value": V("result", at=i.address)})
            elif m in ("inc", "dec", "add", "sub", "and", "or", "xor", "neg", "not", "shl", "shr", "sar", "imul",
                       "sbb", "adc", "setne", "sete", "setg", "setl", "setge", "setle", "seta", "setb", "cdq",
                       "idiv", "div", "mul", "fstp", "fistp", "movsb", "movsd", "rep movsd", "stosd", "rep stosd"):
                if ops and ops[0].type == x86.X86_OP_MEM:
                    base, index, scale, disp = _mem(ops[0])
                    if base is None and index is None and self.glo <= disp < self.ghi:
                        if m in ("inc", "dec"):
                            val = V("op", o=m, x=V("global", a=disp))
                        elif len(ops) > 1:
                            val = V("op", o=m, x=V("global", a=disp), y=self._val(st, ops[1], fn_entry))
                        else:
                            val = UNKNOWN
                        self.global_writes.setdefault(disp, []).append((fn_entry, val))
                        out.append({"op": "set", "var": V("global", a=disp, size=ops[0].size), "value": val,
                                    "at": i.address})
                elif ops and ops[0].type == x86.X86_OP_REG:
                    r = _REG.get(ops[0].reg)
                    if r == "esp":
                        pass
                    elif m in ("inc", "dec") and st.reg.get(r, {}).get("k") == "int":
                        st.reg[r] = V("int", v=st.reg[r]["v"] + (1 if m == "inc" else -1))
                    elif r:
                        st.reg[r] = UNKNOWN
                if m in ("and", "or", "xor", "sub", "add", "dec", "inc"):
                    if ops and ops[0].type == x86.X86_OP_REG and m in ("and", "or") and len(ops) > 1 \
                            and ops[1].type == x86.X86_OP_REG and ops[0].reg == ops[1].reg:
                        st.flags = (st.reg.get(_REG.get(ops[0].reg), UNKNOWN), V("int", v=0), "cmp")
                    else:
                        st.flags = None
                if m in ("cdq", "idiv", "div", "mul"):
                    st.reg.update(eax=UNKNOWN, edx=UNKNOWN)
            elif m.startswith("f"):
                pass                     # x87: float math is shown as unknown values where it matters
            elif m in ("ret", "retn") or m.startswith("j") or m == "nop":
                pass
            else:
                if ops and ops[0].type == x86.X86_OP_REG and _REG.get(ops[0].reg):
                    st.reg[_REG[ops[0].reg]] = UNKNOWN
        return out

    # --- functions ---
    def function(self, entry) -> Function:
        if entry not in self.functions:
            self.functions[entry] = Function(self.dll, entry)
        return self.functions[entry]

    def analyse(self, entry, msg_ptr_arg=None):
        if entry in self.bodies:
            return self.bodies[entry]
        self.bodies[entry] = []          # recursion guard
        prev = self._msg_ptr_arg
        self._msg_ptr_arg = msg_ptr_arg
        f = self.function(entry)
        order = _rpo(f)
        exit_state, stmts, cond = {}, {}, {}
        back = _back_edges(f, order)
        for va in order:
            b = f.blocks[va]
            preds = [exit_state.get(p) for p in b.preds if (p, va) not in back]
            st = State() if va == entry else _merge(preds)
            if any(t == va for (_s, t) in back):     # a loop head: counters change on every turn
                st.reg = {r: v for r, v in st.reg.items() if v.get("k") not in ("int", "minus")}
            stmts[va] = self._exec_block(b, st, entry)
            if b.kind == "cond":
                j = b.insns[-1].mnemonic
                rel = _JCC.get(j)
                if st.flags and rel:
                    l, r, kind = st.flags
                    cond[va] = {"l": l, "op": rel, "r": r, "test": kind == "test"}
                else:
                    cond[va] = {"text": f"{j} (flags from {b.insns[-2].mnemonic if len(b.insns) > 1 else '?'})"}
            if b.kind == "switch" and b.table:
                cond[va] = {"switch_on": st.reg.get(b.table["index_reg"]) or V("reg", r=b.table["index_reg"])}
            exit_state[va] = st
        body = _Structurer(f, stmts, cond, back).run()
        self.bodies[entry] = body
        self._msg_ptr_arg = prev
        return body


def _signed(v):
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v & 0x80000000 else v


def _rpo(f: Function):
    seen, out = set(), []

    def dfs(v):
        stack = [(v, iter(f.blocks[v].succ))]
        seen.add(v)
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                out.append(node); stack.pop()
            elif nxt not in seen:
                seen.add(nxt); stack.append((nxt, iter(f.blocks[nxt].succ)))
    dfs(f.entry)
    return out[::-1]


def _back_edges(f: Function, order):
    pos = {v: k for k, v in enumerate(order)}
    return {(b.start, s) for b in f.blocks.values() if b.start in pos for s in b.succ if pos.get(s, -1) <= pos[b.start]}


class _Structurer:
    """Blocks -> nested statements using immediate post-dominators (if / else join points)."""

    def __init__(self, f, stmts, cond, back):
        self.f, self.stmts, self.cond, self.back = f, stmts, cond, back
        self.ipdom = _ipdom(f, back)
        self.count = 0
        self.loops = {t for (_s, t) in back}

    def run(self):
        return self.region(self.f.entry, None, set())

    def region(self, va, stop, active):
        out = []
        while va is not None and va != stop:
            if self.count > MAX_STMTS:
                out.append({"op": "truncated"}); break
            if va in active:
                out.append({"op": "goto", "to": va}); break
            b = self.f.blocks.get(va)
            if b is None:
                break
            loop = va in self.loops
            if loop:
                inner_active = active | {va}
                body = self._block(b, va, stop, inner_active, loop_head=va)
                out.append({"op": "loop", "head": va, "body": body[0]})
                va = body[1]
                continue
            stmts, nxt = self._block(b, va, stop, active | {va})
            out += stmts
            va = nxt
        return out

    def _block(self, b, va, stop, active, loop_head=None):
        out = list(self.stmts.get(va, []))
        self.count += len(out) + 1
        if b.kind in ("ret", "tailcall"):
            if b.kind == "ret":
                out.append({"op": "return"})
            return out, None
        if b.kind == "end":
            return out, None
        if b.kind in ("fall", "jmp"):
            t = b.succ[0] if b.succ else None
            if (va, t) in self.back:
                out.append({"op": "continue"} if t == loop_head else {"op": "goto", "to": t})
                return out, None
            return out, t
        join = self.ipdom.get(va)
        if b.kind == "cond":
            taken, fall = b.succ[0], (b.succ[1] if len(b.succ) > 1 else None)
            c = self.cond.get(va, {"text": "?"})
            then = self._arm(va, taken, join, active, loop_head)
            els = self._arm(va, fall, join, active, loop_head) if fall is not None else []
            if not then and els:
                c, then, els = negate(c), els, []
            if then or els:
                out.append({"op": "if", "cond": c, "then": then, "else": els, "at": b.insns[-1].address})
            return out, join
        if b.kind == "switch":
            c = self.cond.get(va, {})
            groups = {}
            for value, t in (b.table or {}).get("values", {}).items():
                groups.setdefault(t, []).append(value)
            cases = []
            for t, values in sorted(groups.items(), key=lambda kv: min(kv[1])):
                cases.append({"values": sorted(values), "body": self._arm(va, t, join, active, loop_head)})
            out.append({"op": "switch", "on": c.get("switch_on", UNKNOWN), "cases": cases,
                        "at": b.insns[-1].address})
            return out, join
        return out, None

    def _arm(self, src, t, join, active, loop_head):
        if t is None or t == join:
            return []
        if (src, t) in self.back:
            return [{"op": "continue"} if t == loop_head else {"op": "goto", "to": t}]
        return self.region(t, join, active)


def negate(c):
    if "op" in c and c["op"] in _NEG:
        return dict(c, op=_NEG[c["op"]])
    return {"not": c}


def _ipdom(f: Function, back):
    """Immediate post-dominators (iterative, on the graph without back edges; a virtual exit after every block that
    leaves the function)."""
    EXIT = -1
    succ = {v: [s for s in b.succ if (v, s) not in back] or [EXIT] for v, b in f.blocks.items()}
    nodes = list(f.blocks) + [EXIT]
    pdom = {v: set(nodes) for v in nodes}
    pdom[EXIT] = {EXIT}
    changed = True
    while changed:
        changed = False
        for v in f.blocks:
            ss = [pdom[s] for s in succ[v]]
            new = {v} | (set.intersection(*ss) if ss else set())
            if new != pdom[v]:
                pdom[v], changed = new, True
    out = {}
    for v in f.blocks:
        cands = pdom[v] - {v}
        # the closest: the post-dominator that all other candidates post-dominate
        best = None
        for c in cands:
            if all(o == c or o in pdom[c] for o in cands):
                best = c
                break
        out[v] = None if best in (None, EXIT) else best
    return out


# ------------------------------------------------------------------------------------------------- top level --
def _script_object(dll: Dll):
    ex = [e for e in dll.pe.DIRECTORY_ENTRY_EXPORT.symbols if e.name == b"LevelSetup"][0].address
    code = bytes(dll.img[ex:ex + 0x60])
    k = code.find(b"\xb8")
    obj = struct.unpack_from("<I", code, k + 1)[0]
    cbs = [dll.u32(obj + 4 * n) for n in range(6)]
    # the API pointer global: LevelSetup stores its argument (mov eax,[esp+4]; mov [G], eax) before returning
    api = None
    for i in _MD.disasm(code, dll.base + ex):
        if i.mnemonic == "mov" and i.operands[0].type == x86.X86_OP_MEM and _mem(i.operands[0])[0] is None:
            api = _mem(i.operands[0])[3]
            break
        if i.mnemonic in ("ret", "retn"):
            break
    return obj, cbs, api


def _data_range(dll: Dll):
    lo, hi = None, None
    for s in dll.pe.sections:
        if s.Name.startswith((b".data", b".bss")):
            a = dll.base + s.VirtualAddress
            lo = a if lo is None else min(lo, a)
            hi = max(hi or 0, a + max(s.Misc_VirtualSize, s.SizeOfRawData))
    return lo or 0, hi or 0


CALLBACKS = ("init", "update", "message", "save", "load", "size")


def analyse(path_or_bytes) -> dict:
    """The mission logic of one DLL: {callbacks, api_global, messages: {id: [stmts]}, functions: {va: [stmts]},
    globals: {va: {writes: [values], read_by, written_by}}}. Values / statements as described in the module doc."""
    dll = Dll(path_or_bytes)
    obj, cbs, api = _script_object(dll)
    glo, ghi = _data_range(dll)
    a = Analyser(dll, api, glo, ghi)
    out = {"api_global": api, "callbacks": dict(zip(CALLBACKS, cbs)), "messages": {}, "functions": {},
           "message_fn": cbs[2], "update_fn": cbs[1], "init_fn": cbs[0]}
    # message callback: cdecl(id, param*) -> arg 0 = id, arg 1 = pointer to the message's parameters
    a.analyse(cbs[2], msg_ptr_arg=1)
    for va in (cbs[1], cbs[0]):
        a.analyse(va)
    todo = set(a.calls_out.get(cbs[2], set()) | a.calls_out.get(cbs[1], set()) | a.calls_out.get(cbs[0], set()))
    done = {cbs[0], cbs[1], cbs[2]}
    while todo:
        va = todo.pop()
        if va in done:
            continue
        done.add(va)
        a.analyse(va)
        todo |= a.calls_out.get(va, set()) - done
    out["messages"] = _messages(a.bodies[cbs[2]])
    out["functions"] = {va: body for va, body in a.bodies.items()}
    out["globals"] = {}
    for g in sorted(set(a.global_writes) | set(a.global_reads)):
        out["globals"][g] = {"writes": [{"fn": fn, "value": v} for fn, v in a.global_writes.get(g, [])],
                             "read_by": sorted(a.global_reads.get(g, set()))}
    out["calls"] = {va: sorted(s) for va, s in a.calls_out.items()}
    return out


def _messages(body) -> dict:
    """Message id -> statements, from Script_Message's switch on its first argument (arg 0). Statements outside the
    switch (the early-out checks) are returned under "guard"."""
    out = {}

    def find(stmts, guard):
        for s in stmts:
            if s["op"] == "switch" and s["on"].get("k") == "arg" and s["on"].get("i") == 0:
                for c in s["cases"]:
                    for v in c["values"]:
                        out[v] = c["body"]
                return True
            if s["op"] == "if":
                guard.append(s["cond"])
                if find(s["then"], guard) or find(s["else"], guard):
                    return True
                guard.pop()
            if s["op"] == "loop" and find(s["body"], guard):
                return True
        return False
    guard = []
    find(body, guard)
    if not out:                             # no switch: the whole body runs for every message (ifs on arg 0)
        out["all"] = body
    return out
