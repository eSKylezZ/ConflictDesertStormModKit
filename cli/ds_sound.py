r"""Conflict: Desert Storm sounds: list sound banks, save them as WAV, make bank packs for mods.

  python ds_sound.py list <level.sch>                          banks and their samples
  python ds_sound.py wav <level.sch> <BANK> <out folder> [--accent sas|delta]
                                                              every sample of a bank as WAV (to edit)
  python ds_sound.py pack <level.sch> <BANK> <out.sch> [--rename NEW]
                                                              a bank + its samples as a small .sch (bank pack)
  python ds_sound.py encode <in.wav> <out.wav>                 WAV -> the game's ADPCM -> WAV (hear the loss)
  python ds_sound.py stream <in.psf> <out.wav>                 a cutscene / objective stream -> stereo WAV
  python ds_sound.py psf <in.wav> <out.psf>                    WAV -> stream (44.1 kHz stereo) for a mod folder

The level sound caches (mission1.sch ...) are next to DesertStorm.exe. Gun banks are named in Weaps.txt column 64
(M16A2, SAW-LIGHTMG, MP5SILENCEDSUBMG ...); each refers to four samples: click, distant tail, shot 1, shot 2.
The squad's voices are the banks Bradley, Foley, Ramirez (Connors) and Jones: wav saves their lines as
<bank>_line01.wav ... - the numbers a .skin's "voice line N = my.wav" replaces. Each line exists once per language;
--accent picks the SAS (British, default) or the Delta (American) actors.

Cutscenes are rendered by the game; their sound is one stream per scene and language in <game>\Data\Sound\English
(the SAS voices) and \American (Delta's): CS_M03_S01.psf ... (cutscenes) and OBJ_M3_1A.psf ... (objective voice-overs).
Each is the whole mix - music, effects and speech - so to change the speech, decode it with "stream", edit it in an
audio editor (keep the length), and save it with "psf" as Mods\<mod>\Sound\SAS\<same name>.psf (or \Delta\, or
directly in Sound\ for both).

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
    ids = sound.bank_samples(bank, have)
    names = ["click", "tail", "shot1", "shot2"] if len(ids) <= 4 else None   # a gun's four, else numbered lines
    lang = 6 if a.accent == "delta" else 0      # language of the voice actors: 0 English (SAS), 6 American (Delta)
    for k, i in enumerate(ids):
        chunk = next((c for c in have[i] if sound.sample_language(c) == lang), None) or             next((c for c in have[i] if sound.sample_language(c) == -1), have[i][0])
        _sid, rate, bits, data = sound.sample_info(chunk)
        pcm = sound.decode(data, bits)
        if names:
            path = os.path.join(a.out, f"{a.bank}_{names[k]}.wav")
        else:   # squad voices: the line number (what "voice line N" means) and what it is
            what = sound.VOICE_LINES.get(k + 1, "") if len(ids) == 47 else ""
            path = os.path.join(a.out, f"{a.bank}_line{k + 1:02d}" + (f"_{what}" if what else "") + ".wav")
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


def cmd_stream(a):
    rate, left, right = sound.decode_stream(open(a.inp, "rb").read())
    sound.write_wav_stereo(a.out, rate, left, right)
    print(f"{a.out}: {rate} Hz stereo, {len(left) / rate:.2f} s")


def cmd_psf(a):
    rate, left, right = sound.read_wav_stereo(a.inp)
    left, right = (sound.resample(c, rate, sound.STREAM_RATE) for c in (left, right))
    data = sound.encode_stream(left, right)
    open(a.out, "wb").write(data)
    print(f"{a.out}: {len(left) / sound.STREAM_RATE:.2f} s, {len(data)} bytes")


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
    s.add_argument("--accent", choices=["sas", "delta"], default="sas", help="voice banks: SAS (British) or Delta (American) actors")
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
    s = sub.add_parser("stream", help="cutscene / objective stream (.psf) -> stereo WAV")
    s.add_argument("inp")
    s.add_argument("out")
    s.set_defaults(fn=cmd_stream)
    s = sub.add_parser("psf", help="WAV -> cutscene / objective stream (.psf)")
    s.add_argument("inp")
    s.add_argument("out")
    s.set_defaults(fn=cmd_psf)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
