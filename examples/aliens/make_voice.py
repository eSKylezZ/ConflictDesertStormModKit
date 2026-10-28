r"""make_voice.py <out folder> --bank NAME --pitch P [--game DIR] - an alien speech bank: a copy of the game's
Iraqi_soldier bank (7 events: alerts, pain, death ... - the soldier code picks the event, the bank the sample) with
every sample replaced by a synthesised alien call (FM chirps through formant-like resonances, clicks, throat growl),
same length, one sample for all languages (-1). -> <out>\<NAME>.sch, a bank pack the plugin adds to every level.
P scales every frequency (warrior ~0.6 = growls, drone 1.0, sniper ~1.5 = chittering)."""
import argparse, os, struct, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
from conflict_ds_tools import sound  # noqa: E402

def _default_game():
    """The installed Desert Storm (env CONFLICT_DS_GAME, else registry / Steam libraries / usual paths)."""
    kit = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
    if kit not in sys.path:
        sys.path.insert(0, kit)
    from conflict_ds_tools import games
    return os.environ.get("CONFLICT_DS_GAME") or games.find_game_dir(games.GAMES["ds1"])


ap = argparse.ArgumentParser()
ap.add_argument("out")
ap.add_argument("--bank", required=True)
ap.add_argument("--pitch", type=float, default=1.0)
ap.add_argument("--game", default=_default_game())
args = ap.parse_args()
os.makedirs(args.out, exist_ok=True)
rng = np.random.default_rng(sound.elf_hash(args.bank))   # stable per bank (hash() changes per run)
RATE = 16000

src = None
for lvl in ("mission3.sch", "mission1.sch", "mission4.sch"):
    p = os.path.join(args.game, lvl)
    if not os.path.exists(p):   # moved by DesertStormFix
        p = os.path.join(args.game, "PSE", "Originals", lvl)
    if os.path.exists(p):
        cl = sound.chunks(open(p, "rb").read())
        src = sound.bank_chunk(cl, "Iraqi_soldier")
        if src:
            have = sound.samples(cl)
            break
assert src, "Iraqi_soldier bank not found"


def resonate(x, freqs, q=12.0):
    """Sum of 2-pole resonators (formant-like) over x."""
    out = np.zeros_like(x)
    for f in freqs:
        r = np.exp(-np.pi * f / (q * RATE))
        a1, a2 = -2 * r * np.cos(2 * np.pi * f / RATE), r * r
        y1 = y2 = 0.0
        y = np.empty_like(x)
        for i, v in enumerate(x):
            y0 = v - a1 * y1 - a2 * y2
            y[i] = y0
            y2, y1 = y1, y0
        out += y / (np.abs(y).max() + 1e-9)
    return out


def call(secs, kind):
    n = int(secs * RATE)
    t = np.arange(n) / RATE
    P = args.pitch
    if kind == 0:   # chirp phrase: a few FM syllables with gaps
        x = np.zeros(n)
        pos = 0.0
        while pos < secs - 0.08:
            dur = rng.uniform(0.06, 0.22)
            i0, i1 = int(pos * RATE), min(n, int((pos + dur) * RATE))
            tt = t[i0:i1] - pos
            f0 = rng.uniform(280, 520) * P
            f = f0 * (1 + 0.6 * np.sin(2 * np.pi * rng.uniform(6, 18) * tt)) * (1 + rng.uniform(-0.5, 0.8) * tt / dur)
            ph = 2 * np.pi * np.cumsum(f) / RATE
            env = np.sin(np.pi * np.clip(tt / dur, 0, 1)) ** 0.6
            x[i0:i1] += env * (np.sign(np.sin(ph)) * 0.5 + np.sin(ph))
            pos += dur + rng.uniform(0.02, 0.12)
        x = resonate(x, [700 * P, 1500 * P, 2800 * P], 8)
    elif kind == 1:   # pain / death: a falling screech into a gurgle
        f = (900 * P) * np.exp(-2.2 * t / secs) + 120 * P
        ph = 2 * np.pi * np.cumsum(f * (1 + 0.08 * np.sin(2 * np.pi * 30 * t))) / RATE
        x = (np.sin(ph) + 0.5 * np.sign(np.sin(ph))) * np.exp(-1.5 * t / secs)
        x += 0.6 * rng.normal(size=n) * np.exp(-3 * t / secs) * (t > secs * 0.5)
        x = resonate(x, [600 * P, 1900 * P], 6)
    else:   # clicks + throat growl (calls to each other)
        x = 0.7 * np.sin(2 * np.pi * 70 * P * t) * (1 + 0.8 * np.sign(np.sin(2 * np.pi * 9 * t)))
        x *= 0.5 + 0.5 * rng.normal(size=n).cumsum() / (np.sqrt(n) + 1)
        for c in rng.uniform(0, secs, int(secs * 14)):
            i = int(c * RATE)
            x[i:i + 60] += np.exp(-np.arange(min(60, n - i)) / 8.0) * rng.choice((-1, 1)) * 2.5
        x = resonate(x, [400 * P, 1100 * P], 5)
    x *= np.clip(t / 0.01, 0, 1) * np.clip((secs - t) / 0.03, 0, 1)
    x = x / (np.abs(x).max() + 1e-9) * 0.85
    return np.round(x * 32767).astype(np.int16)


bank = bytearray(src)
struct.pack_into("<I", bank, 8, sound.bank_key(args.bank))
feed = bank.find(b"\xed\xfe")
count = struct.unpack_from("<H", bank, feed - 2)[0]
events = struct.unpack_from("<%dH" % count, bank, feed + 2)
p = feed + 2 + 2 * count
chunks, k = [], 0
for ev in events:
    n_entries = struct.unpack_from("<H", bank, p)[0]
    p += 2
    for e in range(n_entries):
        old = struct.unpack_from("<I", bank, p + 16 * e)[0]
        secs = 1.0
        for c in have.get(old, []):
            _sid, rate, bits, d = sound.sample_info(c)
            secs = len(d) // 16 * 30 / rate
            break
        kind = 1 if secs > 3.0 or ev in (0x0870,) else (2 if ev in (0x07D5, 0x0A15) else 0)
        sid = sound.elf_hash("%s_%02d" % (args.bank.upper(), k)) | 0x50000000
        struct.pack_into("<I", bank, p + 16 * e, sid)
        pcm = call(min(max(secs, 0.3), 4.5), kind)
        chunks.append(sound.sample_chunk(pcm.tolist(), RATE, sid))
        sound.write_wav(os.path.join(args.out, "%s_%02d.wav" % (args.bank, k)), RATE, pcm.tolist())
        k += 1
    p += 16 * n_entries
open(os.path.join(args.out, args.bank + ".sch"), "wb").write(sound.build([bytes(bank)] + chunks))
print("bank", args.bank, "-", k, "calls, pitch", args.pitch)
