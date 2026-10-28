r"""make_weapons.py <out folder> [--game DIR] - the aliens' three guns, built by script.

Each gun keeps the behaviour of a game gun (its Weaps.txt row via "based on": fire rate, magazines, reload, animations)
and gets a new model on that gun's object (evo_write.rebuild: the hand connector and the eject point stay, the muzzle
point moves to the new barrel's end), a texture atlas, a HUD / inventory picture (side view of the model) and
synthesised shot sounds (WAV; the plugin encodes them into a sound bank).
Gun space (the game's): y along the barrel (muzzle +y), z up, x sideways; cm.
  ALIEN_PULSE_RIFLE  based on IR_WPN_AK47          "Pulse Rifle"
  ALIEN_NEEDLE_RIFLE based on IR_WPN_SVDDragunov   "Needle Rifle"
  ALIEN_PLASMA_CASTER based on IR_WPN_RPK          "Plasma Caster" """
import argparse, math, os, sys
import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "..", ".."), os.path.join(HERE, "..", "hovercraft")]
from conflict_ds_tools import evo, evo_write, images, sound  # noqa: E402
from gamefiles import read_game_file  # noqa: E402
from meshkit import Mesh  # noqa: E402

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
REG = {"body": (0, 0, 128, 128), "dark": (128, 0, 256, 64), "glow": (128, 64, 192, 128), "trim": (192, 64, 256, 128),
       "grip": (0, 128, 128, 256), "lens": (128, 128, 256, 256)}
rng = np.random.default_rng(5)


def texture(name, body, glow):
    img = np.zeros((256, 256, 4), float)
    def fill(r, rgb, noise=5.0):
        u0, v0, u1, v1 = r
        img[v0:v1, u0:u1, :3] = np.array(rgb) + rng.normal(0, noise, (v1 - v0, u1 - u0, 1))
        img[v0:v1, u0:u1, 3] = 255
    fill(REG["body"], body)
    for k in range(0, 128, 16):                         # organic ridges
        img[k:k + 3, 0:128, :3] *= 0.8
    fill(REG["dark"], (34, 30, 40))
    fill(REG["glow"], glow, 2)
    img[80:112, 140:180, :3] = np.minimum(255, np.array(glow) * 1.35)
    fill(REG["trim"], (150, 150, 165))
    fill(REG["grip"], (52, 44, 58), 7)
    fill(REG["lens"], (20, 60, 70), 3)
    rgba = np.clip(img, 0, 255).astype(np.uint8)
    open(os.path.join(args.out, name + ".dds"), "wb").write(images.encode_dds(256, 256, rgba.tobytes()))


def pulse(m):
    m.box((-2.6, -6, -3.5), (2.6, 28, 6), "body")            # receiver
    m.box((-1.8, -31, -6), (1.8, -6, 3), "dark")              # skeleton stock
    m.box((-1.5, -1, -15), (1.5, 4, -3.5), "grip")            # grip
    m.box((-2.0, 8, -13), (2.0, 15, -3.5), "dark")            # energy cell
    m.box((2.0, 9, -11), (2.9, 14, -5), "glow")
    m.tube((0, 28, 1.5), 1, 26, 2.6, 0, "dark", n=10)        # barrel shroud
    for y in (32, 39, 46):
        m.tube((0, y, 1.5), 1, 3, 3.4, 2.4, "glow", n=10)    # coils
    m.tube((0, 54, 1.5), 1, 4, 3.0, 1.6, "trim", n=10)       # emitter
    m.box((-1.2, 4, 6), (1.2, 18, 8.5), "lens")              # sight
    return (0.0, 58.5, 1.5)


def needle(m):
    m.box((-2.2, -8, -2.5), (2.2, 34, 5.5), "body")
    m.box((-1.6, -31, -7), (1.6, -8, 3.5), "body")            # stock
    m.box((-1.4, 0, -14), (1.4, 5, -2.5), "grip")
    m.box((-1.8, 12, -10), (1.8, 20, -2.5), "dark")           # cell
    m.box((1.6, 13, -9), (2.3, 19, -4), "glow")
    m.tube((0, 34, 1.5), 1, 56, 1.4, 0, "dark", n=8)         # long thin barrel
    m.tube((0, 86, 1.5), 1, 5, 2.2, 1.0, "glow", n=8)        # needle emitter
    m.tube((0, 2, 9.5), 1, 26, 2.2, 0, "dark", n=10)         # scope
    m.tube((0, 27, 9.5), 1, 2, 2.8, 0, "lens", n=10)
    m.box((-1, 8, 5.5), (1, 20, 7.5), "trim")                # scope mount
    return (0.0, 91.5, 1.5)


def plasma(m):
    m.box((-3.4, -10, -4.5), (3.4, 30, 7), "body")
    m.box((-2.2, -31, -7), (2.2, -10, 4), "dark")
    m.box((-1.6, -2, -16), (1.6, 3, -4.5), "grip")
    m.tube((-3.0, 8, -9), 0, 6.0, 6.5, 0, "dark", n=14)      # drum (axis x)
    m.tube((3.0, 8, -9), 0, 0.8, 4.0, 0, "glow", n=12)       # drum window
    m.tube((0, 30, 1.5), 1, 40, 3.2, 0, "dark", n=12)        # heavy barrel
    for y in (34, 42, 50, 58):
        m.tube((0, y, 1.5), 1, 4, 4.6, 3.2, "glow", n=12)    # plasma coils
    m.tube((0, 70, 1.5), 1, 6, 4.2, 2.4, "trim", n=12)
    m.box((-1.5, 6, 7), (1.5, 22, 9.5), "lens")
    return (0.0, 77.0, 1.5)


def icon(m, name, w=128, h=40):
    pts = [(p[1], p[2]) for f in m.faces for p in f[:3]]
    ys, zs = [p[0] for p in pts], [p[1] for p in pts]
    s = min((w - 6) / (max(ys) - min(ys)), (h - 6) / (max(zs) - min(zs)))
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cols = {"body": (190, 200, 210), "dark": (120, 125, 140), "glow": (120, 255, 230), "trim": (230, 230, 235),
            "grip": (150, 145, 160), "lens": (90, 200, 220)}
    for a, b, c, n, reg in sorted(m.faces, key=lambda f: -np.mean([f[0][0], f[1][0], f[2][0]])):
        if n[0] > -0.2:   # the gun's left side, as the game's pictures
            q = [(3 + (p[1] - min(ys)) * s, h - 3 - (p[2] - min(zs)) * s) for p in (a, b, c)]
            d.polygon(q, fill=cols[reg] + (255,))
    img.save(os.path.join(args.out, name + "_ICON.png"))


def wav(name, secs, fn, rate=22050):
    t = np.arange(int(secs * rate)) / rate
    x = fn(t)
    x = x / (np.abs(x).max() + 1e-9) * 0.9
    sound.write_wav(os.path.join(args.out, name), rate, np.round(x * 32767).astype(np.int16).tolist())


def zap(f0, f1, decay, noise, body=0.0):
    def fn(t):
        f = f0 * (f1 / f0) ** (t / t[-1])
        ph = 2 * np.pi * np.cumsum(f) / 22050
        x = np.sin(ph) + 0.35 * np.sin(2 * ph + 1) + noise * rng.normal(size=t.size) * np.exp(-t * 40)
        if body:
            x += body * np.sin(2 * np.pi * 55 * t) * np.exp(-t * 12)
        return x * np.exp(-t * decay) * np.clip(t / 0.002, 0, 1)
    return fn


GUNS = [
    dict(id="ALIEN_PULSE_RIFLE", base="IR_WPN_AK47", model="RIFLE05_AK47", name="Pulse Rifle", build=pulse,
         body=(96, 104, 118), glow=(60, 230, 200), shot=zap(2400, 300, 22, 0.5), shot2=zap(2200, 260, 22, 0.55),
         tail=zap(500, 120, 6, 0.2), extra=""),
    dict(id="ALIEN_NEEDLE_RIFLE", base="IR_WPN_SVDDragunov", model="SNIPER04_SVDDRAGUNOV", name="Needle Rifle",
         build=needle, body=(110, 96, 120), glow=(200, 90, 255), shot=zap(5200, 900, 30, 0.8), shot2=zap(5000, 850, 30, 0.8),
         tail=zap(900, 200, 5, 0.3), extra=""),
    dict(id="ALIEN_PLASMA_CASTER", base="IR_WPN_RPK", model="LMG03_RPK", name="Plasma Caster", build=plasma,
         body=(84, 96, 72), glow=(150, 255, 70), shot=zap(900, 90, 14, 0.9, body=1.2), shot2=zap(850, 80, 14, 0.9, body=1.2),
         tail=zap(240, 50, 4, 0.4, body=0.6), extra=""),
]
for g in GUNS:
    data = read_game_file(args.game, g["model"] + ".EVO")
    tex = g["id"]
    m = Mesh(tex, REG)
    muzzle = g["build"](m)
    texture(tex, g["body"], g["glow"])
    warnings = []
    out = evo_write.rebuild(data, {0: [m.part()]}, model_name=g["id"], warnings=warnings, points={0: {0: muzzle}})
    open(os.path.join(args.out, g["id"] + ".evo"), "wb").write(out)
    icon(m, g["id"])
    wav(g["id"] + "_shot.wav", 0.35, g["shot"])
    wav(g["id"] + "_shot2.wav", 0.35, g["shot2"])
    wav(g["id"] + "_tail.wav", 1.2, g["tail"])
    open(os.path.join(args.out, g["id"] + ".weapon"), "w").write(
        f"# {g['name']}: the {g['base']}'s behaviour with an alien model, picture and sound (examples/aliens)\n"
        f"id           = {g['id']}\n"
        f"based on     = {g['base']}\n"
        f"display name = {g['name']}\n"
        f"model        = {g['id']}\n"
        f"hud icon     = {g['id']}_ICON\n"
        f"shot sound   = {g['id']}_shot.wav\n"
        f"shot sound 2 = {g['id']}_shot2.wav\n"
        f"tail sound   = {g['id']}_tail.wav\n"
        f"faction      = Iraqi\n" + g["extra"])
    print(g["id"], len(m.faces), "triangles, muzzle", muzzle, "warnings:", warnings or "none")
