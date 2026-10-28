"""Mission patches: a mod's <Stem>.patch.lua applied to the readable Lua of a mission DLL (mission2lua_readable).

DesertStormFix 3.3+ does this in the game (dsfix.missions; reference: the patch repository's
docs/sdk/reference/mission-patches.md): it makes the readable mission from the player's own DLL - byte for byte the
text mission2lua_readable writes here - applies every enabled mod's patch and runs the result instead of the DLL. A patch
ships no game code, only:

* a header: ``--@patches <Stem>.dll`` and ``--@readable <VERSION>`` (mission2lua_readable.VERSION);
* edit blocks: ``--[[@edit`` / the readable mission's lines / ``--@to`` / their replacement / ``]]`` (any long-bracket
  level) - the lines must appear exactly once, whole and consecutive;
* Lua, run in the mission's environment before its init() (SCENE, TEXT, MOVIE, X come from DesertStormFix).

This module follows DesertStormFix's rules (pse/build/mission2lua/m2r_patch.cpp) so a patch can be checked here.
"""
from __future__ import annotations

import re

from . import mission2lua_readable as m2r

START_MARKER = "-- ======================== scripts/start.lua ========================"


class PatchError(Exception):
    pass


def normalize(raw: bytes | str) -> str:
    t = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    t = t.replace("\r\n", "\n")
    return t[1:] if t.startswith("﻿") else t


def parse(text: str, dll_name: str):
    """-> [(from_lines, to_lines, line)] of a patch for dll_name; PatchError when it isn't one / is broken."""
    lines = text.split("\n")
    for_this, version = False, None
    for raw in lines:
        l = raw.strip(" \t\r")
        if not l.startswith("--"):
            break
        if l.startswith("--@patches "):
            for_this = l[11:].strip(" \t\r").lower() == dll_name.lower()
        if l.startswith("--@readable"):
            try:
                version = int(re.match(r"\s*(-?\d+)", l[11:]).group(1))
            except AttributeError:
                version = 0
    if not for_this:
        raise PatchError('its header has no "--@patches %s" line' % dll_name)
    if version != m2r.VERSION:
        raise PatchError("written for readable missions version %s, this one makes version %d (header line --@readable)"
                         % ("?" if version is None else version, m2r.VERSION))
    edits = []
    k = 0
    while k < len(lines):
        l = lines[k].strip(" \t\r")
        m = re.match(r"--\[(=*)\[@edit", l)
        if not m:
            k += 1
            continue
        close = "]" + m.group(1) + "]"
        start = k + 1
        src, dst, to, closed = [], [], False, False
        k += 1
        while k < len(lines):
            c = lines[k]
            at = c.find(close)
            content = c if at < 0 else c[:at]
            if content.strip(" \t\r") == "--@to":
                to = True
            elif at < 0 or content.strip(" \t\r"):
                (dst if to else src).append(content)
            if at >= 0:
                closed = True
                break
            k += 1
        if not closed or not to or not src:
            raise PatchError("the edit block at line %d %s" % (
                start, "isn't closed" if not closed else "has no --@to line" if not to else "has nothing before --@to"))
        edits.append((src, dst, start))
        k += 1
    return edits


def _count(lines, block):
    n = len(block)
    return sum(1 for i in range(len(lines) - n + 1) if lines[i:i + n] == block)


def make(bundle: str, original_events: str, edited_events: str, dll_name: str, note: str = "") -> str:
    """A patch's text whose edit blocks turn original_events into edited_events (scripts/events.lua of a readable
    mission): one block per changed place, grown by lines of context until its lines appear exactly once in the
    bundle (the text the game applies it to); blocks whose context would overlap are joined."""
    import difflib
    a, b = original_events.split("\n"), edited_events.split("\n")
    bl = bundle.split("\n")
    hunks = [(i1, i2, j1, j2) for tag, i1, i2, j1, j2 in
             difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if tag != "equal"]

    def grow(i1, i2):
        before, after = (1, 0) if i1 == i2 else (0, 0)   # an insertion needs a line to hang on
        for _ in range(64):
            lo, hi = max(0, i1 - before), min(len(a), i2 + after)
            if lo < hi and _count(bl, a[lo:hi]) == 1:
                return lo, hi
            if after <= before and hi < len(a):
                after += 1
            elif lo > 0:
                before += 1
            elif hi < len(a):
                after += 1
            else:
                break
        raise PatchError("no unique context found for the change at events.lua line %d" % (i1 + 1))

    blocks = []   # [lo, hi, [hunks]]: original lines lo..hi replaced, the hunks inside
    for h in hunks:
        lo, hi = grow(h[0], h[1])
        while blocks and lo <= blocks[-1][1]:   # overlaps the previous block: one block for both
            prev = blocks.pop()
            lo, hi = grow(min(prev[0], lo), max(prev[1], hi))
            h_list = prev[2] + [h]
            break
        else:
            h_list = [h]
        blocks.append([lo, hi, h_list])
    out = ["--@patches %s" % dll_name, "--@readable %d" % m2r.VERSION]
    if note:
        out += ["-- " + l for l in note.split("\n")]
    out.append("")
    for lo, hi, hs in blocks:
        new, i = [], lo
        for i1, i2, j1, j2 in hs:   # unchanged lines up to each change, then the edited ones
            new += a[i:i1] + b[j1:j2]
            i = i2
        new += a[i:hi]
        old = a[lo:hi]
        level = 0
        while any(("]" + "=" * level + "]") in l for l in old + new):
            level += 1
        eq = "=" * level
        out += ["--[%s[@edit" % eq] + old + ["--@to"] + new + ["]%s]" % eq, ""]
    out.append("-- your own Lua (runs in the mission's environment before the level starts) goes here")
    text = "\n".join(out) + "\n"
    check = apply(bundle, parse(text, dll_name))
    if original_events in bundle and check != bundle.replace(original_events.rstrip("\n"), edited_events.rstrip("\n"), 1):
        raise PatchError("the made patch doesn't reproduce the edited file (please report)")
    return text


def apply(bundle: str, edits) -> str:
    """The bundle with every edit applied (each must match exactly one place) - PatchError otherwise."""
    work = bundle.split("\n")
    for src, dst, line in edits:
        hits = [i for i in range(len(work) - len(src) + 1) if work[i:i + len(src)] == src]
        if len(hits) != 1:
            raise PatchError('the edit block at line %d ("%s" ...) matches %d places in the mission, not one - written '
                             "for another game version, or another patch changed those lines"
                             % (line, src[0].strip(" \t\r"), len(hits)))
        work[hits[0]:hits[0] + len(src)] = dst
    return "\n".join(work)
