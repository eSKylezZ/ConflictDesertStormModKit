"""Better sound effects for the PC game from a GameCube disc the player owns (cli/ds_gc_audio.py, Blender add-on).

The GameCube version stores every sound effect and voice line as uncompressed 16-bit audio, many at a higher sample
rate than the PC (11 kHz -> 20 or 44.1 kHz). ``convert`` reads the level sound caches from the disc image (gamecube.py),
takes each sample that has a higher rate there than in the PC game, encodes it in the PC's ADPCM format at that rate
and writes one file per level (<mod folder>\\MISSION1.SCH ...). DesertStormFix uses a mod .sch named like a level's
sound cache to replace that level's samples (same id and language). Nothing from the disc ships with the kit.

Pure Python, no Blender in here.
"""
import hashlib
import os
import struct
import time

from . import gamecube, games, modlayout, sound

MOD_NAME = "GameCubeAudio"


class Cancelled(Exception):
    pass


def pc_samples(path):
    """{(language, id): rate} of a PC level sound cache."""
    out = {}
    with open(path, "rb") as f:
        data = f.read()
    for tag, c in sound.chunks(data):
        if tag == b"PFSM":
            lang, sid, _size, rate = struct.unpack_from("<iIIH", c, 8)
            out[(lang, sid)] = rate
    return out


def pfsm(lang, sid, rate, adpcm):
    return b"PFSM" + struct.pack("<IiIIHBB", 16 + len(adpcm), lang, sid, len(adpcm), rate, 4, 0xCC) + adpcm


def default_out(game):
    return os.path.join(game, "Mods", MOD_NAME)


def convert(image, game, out=None, levels=None, all_rates=False, list_only=False, log=print, progress=None,
            cancel=None):
    """Convert a GameCube disc's level sound caches into a PC mod folder.

    image: .iso / .gcm / .ciso; game: PC game folder (DesertStorm.exe and the level .sch files); out: mod folder
    (default <game>\\Mods\\GameCubeAudio); levels: level names to do (default all); all_rates: also re-encode samples
    with the same rate; list_only: report, write nothing. log(text) gets one line per level; progress(fraction, text)
    and cancel() -> bool are for a caller running this in a thread (raises Cancelled).
    Returns a dict: disc id / title, out, levels [(file, higher, same, missing, bytes)], written bytes, encoded count,
    seconds.
    """
    if not game or not os.path.isfile(os.path.join(game, "DesertStorm.exe")):
        raise ValueError("PC game folder not found (no DesertStorm.exe in %s)" % game)
    out = out or default_out(game)
    want = {x.strip().lower() for x in levels} if levels else None
    started = time.time()
    result = {"out": out, "levels": [], "written": 0, "encoded": 0, "warning": None}
    with gamecube.Disc(image) as disc:
        result.update(game_id=disc.game_id, title=disc.title)
        log('GameCube disc %s "%s"; PC game %s' % (disc.game_id, disc.title, game))
        if not disc.game_id.startswith("GCF"):
            result["warning"] = "this doesn't look like Conflict: Desert Storm (game id %s, expected GCF..)" % disc.game_id
            log("warning: " + result["warning"])
        pc_caches = {os.path.basename(p).lower(): p for p in games.game_files(game, exts=(".sch",))}   # (+ PSE/Originals)
        todo = [n for n in disc.names() if os.path.basename(n).lower().endswith(".sch")
                and (not want or os.path.basename(n)[:-4].lower() in want)]
        encoded = {}            # (lang, id, rate, pcm hash) -> chunk, shared between levels
        for i, name in enumerate(todo):
            base = os.path.basename(name)
            if progress:
                progress(i / max(1, len(todo)), base)
            if cancel and cancel():
                raise Cancelled()
            pc_name = pc_caches.get(base.lower())
            if not pc_name:
                log("  %s: no PC cache of that name - skipped" % base)
                continue
            pc = pc_samples(pc_name)
            chunks, higher, same, missing = [], 0, 0, 0
            for lang, sid, rate, pcm in gamecube.sound_cache_samples(disc.file(name)):
                pc_rate = pc.get((lang, sid))
                if pc_rate is None:
                    missing += 1
                    continue
                if rate > pc_rate * 1.02:
                    higher += 1
                elif all_rates and abs(rate - pc_rate) <= pc_rate * 0.02:
                    same += 1
                else:
                    continue
                if cancel and cancel():
                    raise Cancelled()
                key = (lang, sid, rate, hashlib.sha1(sound.pcm_bytes(pcm)).digest())
                if key not in encoded:
                    encoded[key] = pfsm(lang, sid, rate, sound.encode(pcm))
                chunks.append(encoded[key])
            size = sum(map(len, chunks))
            result["levels"].append((pc_name, higher, same, missing, size))
            log("  %-16s %4d at a higher rate" % (pc_name, higher) + (", %d same rate" % same if all_rates else "")
                + (", %d not in the PC cache" % missing if missing else "") + "  -> %5.2f MB" % (size / 1e6))
            if list_only or not chunks:
                continue
            os.makedirs(out, exist_ok=True)
            body = b"".join(chunks)
            with open(modlayout.path_for(out, pc_name.upper()), "wb") as f:   # sounds\ in a new mod
                f.write(b"SCH\0" + struct.pack("<I", len(body)) + body)
            result["written"] += size
        result["encoded"] = len(encoded)
        if not list_only and result["written"]:
            with open(os.path.join(out, "README.txt"), "w", encoding="utf-8") as f:
                f.write("GameCube sound effects for Conflict: Desert Storm (PC), converted from the player's own "
                        "GameCube\ndisc (%s) by ConflictDesertStormModKit. Each LEVEL.SCH replaces\n"
                        "the samples of that level's sound cache that have a higher sample rate on the GameCube.\n"
                        "Needs DesertStormFix 2.1 or later. Switch it off in the MODS menu to go back.\n" % disc.game_id)
    if progress:
        progress(1.0, "done")
    result["seconds"] = time.time() - started
    return result
