"""Conflict: Desert Storm sounds: list sound banks, save them as WAV, make bank packs for mods.

  python ds_sound.py list <level.sch>                          banks and their samples
  python ds_sound.py wav <level.sch> <BANK> <out folder>       every sample of a bank as WAV (to edit)
  python ds_sound.py pack <level.sch> <BANK> <out.sch> [--rename NEW]
                                                              a bank + its samples as a small .sch (bank pack)
  python ds_sound.py encode <in.wav> <out.wav>                 WAV -> the game's ADPCM -> WAV (hear the loss)

The level sound caches (mission1.sch ...) are next to DesertStorm.exe. Gun banks are named in Weaps.txt column 64
(M16A2, SAW-LIGHTMG, MP5SILENCEDSUBMG ...); each refers to four samples: click, distant tail, shot 1, shot 2.

For DesertStormFix mods you usually don't need this: a .weapon file's "sound = <weapon>" borrows a game sound and
"shot sound = my.wav" uses your own WAV; the plugin builds the banks. A bank pack (.sch in a mod folder) is added to
every level's sound cache by the plugin.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from conflict_ds_tools import sound  # noqa: E402


def cmd_list(a):
    cl = sound.chunks(open(a.sch, "rb").read())
    have = sound.samples(cl)
    for tag, c in cl:
        if tag != b"BANK":
            continue
        ids = sound.bank_samples(c, have)
        parts = []
        for i in ids:
            _sid, rate, bits, data = sound.sample_info(have[i][0])
            n = len(data) // 16 * (30 if bits == 4 else 15)
            parts.append(f"{i:08X} {n / rate:.2f}s")
        print(f"BANK {int.from_bytes(c[8:12], 'little'):08X}  {', '.join(parts)}")


def cmd_wav(a):
    cl = sound.chunks(open(a.sch, "rb").read())
    bank = sound.bank_chunk(cl, a.bank)
    if bank is None:
        sys.exit(f"bank {a.bank} not in {a.sch}")
    have = sound.samples(cl)
    os.makedirs(a.out, exist_ok=True)
    names = ["click", "tail", "shot1", "shot2"]
    for k, i in enumerate(sound.bank_samples(bank, have)):
        _sid, rate, bits, data = sound.sample_info(have[i][0])
        pcm = sound.decode(data, bits)
        path = os.path.join(a.out, f"{a.bank}_{names[k] if k < len(names) else k}.wav")
        sound.write_wav(path, rate, pcm)
        print(f"{path}: {rate} Hz, {len(pcm) / rate:.2f} s")


def cmd_pack(a):
    out = sound.extract_bank(open(a.sch, "rb").read(), a.bank, a.rename)
    if out is None:
        sys.exit(f"bank {a.bank} not in {a.sch}")
    open(a.out, "wb").write(out)
    print(f"{a.rename or a.bank}: {a.out} ({len(out)} bytes)")


def cmd_encode(a):
    rate, pcm = sound.read_wav(a.inp)
    back = sound.decode(sound.encode(pcm))[:len(pcm)]
    sound.write_wav(a.out, rate, back)
    print(f"{a.out}: {rate} Hz, {len(back) / rate:.2f} s")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("list")
    s.add_argument("sch")
    s.set_defaults(fn=cmd_list)
    s = sub.add_parser("wav")
    s.add_argument("sch")
    s.add_argument("bank")
    s.add_argument("out")
    s.set_defaults(fn=cmd_wav)
    s = sub.add_parser("pack")
    s.add_argument("sch")
    s.add_argument("bank")
    s.add_argument("out")
    s.add_argument("--rename")
    s.set_defaults(fn=cmd_pack)
    s = sub.add_parser("encode")
    s.add_argument("inp")
    s.add_argument("out")
    s.set_defaults(fn=cmd_encode)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
