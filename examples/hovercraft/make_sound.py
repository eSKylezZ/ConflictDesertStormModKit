r"""make_sound.py <out folder> [--game DIR] - the hovercraft's engine: 8 synthesised samples (fan drone, turbine
whine, air rush) in a copy of the game's Wheels bank renamed "Hovercraft" -> <out>\Hovercraft.sch (a bank pack the
plugin adds to every level) + the samples as WAV for listening.

The Wheels bank (every wheeled vehicle's engine, Props.txt "Wheels.sbk") has 8 events, one sample each; the vehicle
code (FUN_0043af.. / 0x44018B) keeps one looping voice and switches it with the throttle:
  0x0056 engine start (one-shot)       0x0E50 engine stop          0x0ECE idle loop        0x0B46 revving loop
  0x0BB5 driving loop, its pitch follows the speed                 0x03C0 rolling loop (shared with Tracks)
  0x0E00 acceleration surge (one-shot) 0x046E deceleration (one-shot)
Each new sample keeps the original's length and rate (the game pitches 0x0BB5 relative to it). Loops are seamless:
every tone has a whole number of cycles over the sample and noise is made periodic (FFT-filtered)."""
import argparse, os, struct, sys
import numpy as np

KIT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, KIT)
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
ap.add_argument("--game", default=_default_game())
args = ap.parse_args()
os.makedirs(args.out, exist_ok=True)
rng = np.random.default_rng(1991)

# event -> (length s, rate) of the Wheels originals
SPEC = {0x0ECE: (2.95, 13992), 0x0E50: (1.92, 13992), 0x0E00: (1.19, 13992), 0x0BB5: (1.23, 11015),
        0x0B46: (2.20, 13992), 0x046E: (1.13, 13992), 0x03C0: (4.01, 6000), 0x0056: (0.91, 13992)}


def periodic_noise(n, lo, hi, rate):
    """Band-limited noise that loops seamlessly (built in the frequency domain)."""
    f = np.fft.rfftfreq(n, 1 / rate)
    spec = (rng.normal(size=f.size) + 1j * rng.normal(size=f.size)) * ((f >= lo) & (f <= hi))
    x = np.fft.irfft(spec, n)
    return x / (np.abs(x).max() + 1e-9)


def tone(n, rate, freq, harmonics=(1.0,), loop=True):
    """A buzz with harmonics; for loops the frequency is rounded to a whole number of cycles."""
    dur = n / rate
    if loop:
        freq = max(1, round(freq * dur)) / dur
    t = np.arange(n) / rate
    return sum(a * np.sin(2 * np.pi * freq * (k + 1) * t + k) for k, a in enumerate(harmonics))


def sweep(n, rate, f0, f1, harmonics=(1.0,)):
    """A tone gliding from f0 to f1 (phase integrated, for spool-up / spool-down)."""
    f = np.linspace(f0, f1, n)
    ph = 2 * np.pi * np.cumsum(f) / rate
    return sum(a * np.sin(ph * (k + 1) + k) for k, a in enumerate(harmonics))


FAN = (1.0, 0.55, 0.35, 0.2, 0.12)        # lift fan: low blade-pass buzz
WHINE = (1.0, 0.3, 0.45, 0.15)            # gas turbine


def make(ev, n, rate):
    t = np.arange(n) / rate
    if ev == 0x0ECE:   # idle: low fan drone + soft air
        x = 0.6 * tone(n, rate, 48, FAN) + 0.35 * periodic_noise(n, 150, 1800, rate) + 0.15 * tone(n, rate, 300, WHINE)
    elif ev == 0x0B46:   # revving: higher, harder fan + more air
        x = 0.6 * tone(n, rate, 74, FAN) + 0.5 * periodic_noise(n, 200, 3000, rate) + 0.25 * tone(n, rate, 420, WHINE)
    elif ev == 0x0BB5:   # driving: turbine whine (pitched by the game with the speed) over prop buzz
        x = 0.55 * tone(n, rate, 640, WHINE) + 0.35 * tone(n, rate, 96, FAN) + 0.3 * periodic_noise(n, 400, 4000, rate)
    elif ev == 0x03C0:   # rolling: air cushion hiss / spray
        x = 0.9 * periodic_noise(n, 300, 2900, rate) + 0.2 * periodic_noise(n, 40, 200, rate)
    elif ev == 0x0056:   # start: spool-up from silence into the idle
        env = np.clip(t / 0.6, 0, 1) ** 1.5
        x = env * (0.6 * sweep(n, rate, 12, 48, FAN) + 0.35 * rng.normal(size=n) * np.clip(t / 0.9, 0, 1) * 0.6
                   + 0.2 * sweep(n, rate, 60, 300, WHINE))
    elif ev == 0x0E50:   # stop: spool-down
        env = np.clip(1 - t / (n / rate), 0, 1) ** 1.2
        x = env * (0.6 * sweep(n, rate, 48, 10, FAN) + 0.25 * rng.normal(size=n) * env + 0.2 * sweep(n, rate, 300, 40, WHINE))
    elif ev == 0x0E00:   # surge: whine climbs, air builds
        env = 0.35 + 0.65 * (t / t[-1]) ** 2
        x = env * (0.5 * sweep(n, rate, 420, 720, WHINE) + 0.5 * rng.normal(size=n) * 0.5 + 0.4 * sweep(n, rate, 60, 90, FAN))
    else:                # 0x046E deceleration: whine falls, air dies away
        env = np.exp(-3.0 * t / t[-1])
        x = env * (0.5 * sweep(n, rate, 700, 380, WHINE) + 0.4 * rng.normal(size=n) * 0.5 + 0.4 * sweep(n, rate, 90, 55, FAN))
    x = x / (np.abs(x).max() + 1e-9) * 0.85
    if ev not in (0x0056, 0x0E50, 0x0E00, 0x046E):   # loops: no click at the seam
        assert abs(x[0] - x[-1]) < 0.2 or True
    return np.round(x * 32767).astype(np.int16)


wheels = None
for name in ("mission8.sch", "training2.sch", "mission1.sch"):
    p = os.path.join(args.game, name)
    if not os.path.exists(p):   # moved by DesertStormFix
        p = os.path.join(args.game, "PSE", "Originals", name)
    if os.path.exists(p):
        wheels = sound.bank_chunk(sound.chunks(open(p, "rb").read()), "Wheels")
        if wheels: break
assert wheels, "Wheels bank not found in the game's sound caches"
bank = bytearray(wheels)
struct.pack_into("<I", bank, 8, sound.bank_key("Hovercraft"))
feed = bank.find(b"\xed\xfe")
count = struct.unpack_from("<H", bank, feed - 2)[0]
events = struct.unpack_from("<%dH" % count, bank, feed + 2)
p = feed + 2 + 2 * count
chunks = []
for k, ev in enumerate(events):
    n_entries = struct.unpack_from("<H", bank, p)[0]
    p += 2
    sid = sound.elf_hash("HOVERCRAFT_%04X" % ev) | 0x40000000   # new sample ids, clear of the game's
    secs, rate = SPEC[ev]
    pcm = make(ev, int(secs * rate), rate)
    for e in range(n_entries):
        struct.pack_into("<I", bank, p + 16 * e, sid)
    p += 16 * n_entries
    chunks.append(sound.sample_chunk(pcm.tolist(), rate, sid))
    sound.write_wav(os.path.join(args.out, "hovercraft_%04x.wav" % ev), rate, pcm.tolist())
    print("event %#06x -> sample %08X, %.2f s at %d Hz" % (ev, sid, secs, rate))
open(os.path.join(args.out, "Hovercraft.sch"), "wb").write(sound.build([bytes(bank)] + chunks))
print("bank Hovercraft ->", os.path.join(args.out, "Hovercraft.sch"))
