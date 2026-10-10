"""Terrain patches: heightfields sculpted in the editor and exported as game models with collision.

A patch is a square grid of ``n`` x ``n`` vertices ``cell`` cm apart, centred on its own origin (the ENVI record that
places it in a level gives position and yaw); ``heights`` (cm, row by row along z, then x) lift each vertex. It exports
as an ordinary static prop (.EVO): one textured mesh (the game's texture by name, tiled every ``tile`` cm, vertex
colours shaded by slope so the relief reads without baked lighting) and a collision tree over the same surface
(``surface`` = a Default.spl row - non-blocking ground such as Sand, Rock, Grass by default, so the AI walk map treats
it as walkable). Nothing of the game is copied: the model is built over a small template prop (BH_BODGEPLATE: one
object, own-mesh collision) whose geometry, collision and name are all replaced.

The editor keeps the source next to the mission as <MODEL>.terrain.json and writes <MODEL>.EVO on save.
"""
from __future__ import annotations

import json
import math
import random

from . import evo_write

TEMPLATE = "BH_BODGEPLATE"          # single static object, collision "THIS"
ZONE_TEMPLATE = "MISSION12_CONNECTINGLANDSCAPE"   # single world-piece object (type 5), collision "THIS"
MAX_COLLISION_CELLS = 64            # larger grids get a coarser collision mesh (the game's trees stay small)


class Terrain:
    def __init__(self, n=33, cell=300.0, heights=None, texture="Mission6Sand", tile=800.0, surface="Sand",
                 steep_texture=None, slope=35.0, zone=False, origin=(0.0, 0.0, 0.0)):
        if not 2 <= n <= 129:
            raise ValueError("terrain grid: 2 to 129 vertices per side")
        self.n, self.cell = int(n), float(cell)
        self.heights = list(heights) if heights else [0.0] * (self.n * self.n)
        if len(self.heights) != self.n * self.n:
            raise ValueError("heights don't match the grid")
        self.texture, self.tile, self.surface = texture, float(tile), surface
        # Faces steeper than `slope` degrees take `steep_texture` (a cliff), mapped from the side - a texture projected
        # from above stretches into streaks on steep faces. None = one texture everywhere.
        self.steep_texture, self.slope = steep_texture, float(slope)
        # zone = the level's ground (a world piece at 0 under Root, vertices in world space around `origin`) rather
        # than a prop patch placed by its record.
        self.zone, self.origin = bool(zone), tuple(float(v) for v in origin)

    # ---- source ----
    def to_json(self) -> dict:
        return {"n": self.n, "cell": self.cell, "texture": self.texture, "tile": self.tile, "surface": self.surface,
                "steep_texture": self.steep_texture, "slope": self.slope, "zone": self.zone, "origin": list(self.origin),
                "heights": [round(h, 1) for h in self.heights]}

    @classmethod
    def from_json(cls, d) -> "Terrain":
        return cls(d["n"], d["cell"], d.get("heights"), d.get("texture", "Mission6Sand"), d.get("tile", 800.0),
                   d.get("surface", "Sand"), d.get("steep_texture"), d.get("slope", 35.0), d.get("zone", False),
                   d.get("origin", (0.0, 0.0, 0.0)))

    def dumps(self) -> str:
        return json.dumps(self.to_json())

    @property
    def size(self) -> float:
        return (self.n - 1) * self.cell

    def vertex(self, i, j):
        """(x, y, z) of grid vertex i (along x), j (along z), local cm."""
        half = self.size / 2
        return (i * self.cell - half, self.heights[j * self.n + i], j * self.cell - half)

    # ---- sculpting (positions local, cm) ----
    def _brush(self, x, z, radius):
        """(index, weight) of the vertices within radius - smooth falloff (1 at the centre, 0 at the edge)."""
        half = self.size / 2
        lo_i, hi_i = max(0, int((x + half - radius) // self.cell)), min(self.n - 1, int((x + half + radius) // self.cell) + 1)
        lo_j, hi_j = max(0, int((z + half - radius) // self.cell)), min(self.n - 1, int((z + half + radius) // self.cell) + 1)
        for j in range(lo_j, hi_j + 1):
            for i in range(lo_i, hi_i + 1):
                d = math.hypot(i * self.cell - half - x, j * self.cell - half - z)
                if d < radius:
                    t = d / radius
                    yield j * self.n + i, 0.5 * (1 + math.cos(math.pi * t))

    def brush(self, op, x, z, radius, strength, target=None):
        """op: raise / lower (strength cm at the centre), smooth (0..1), flatten (towards target cm, or the centre's
        height; strength 0..1)."""
        h = self.heights
        if op in ("raise", "lower"):
            s = strength if op == "raise" else -strength
            for k, w in self._brush(x, z, radius):
                h[k] += s * w
        elif op == "smooth":
            new = {}
            for k, w in self._brush(x, z, radius):
                i, j = k % self.n, k // self.n
                nb = [h[jj * self.n + ii] for ii, jj in ((i - 1, j), (i + 1, j), (i, j - 1), (i, j + 1))
                      if 0 <= ii < self.n and 0 <= jj < self.n]
                new[k] = h[k] + (sum(nb) / len(nb) - h[k]) * min(1.0, strength) * w
            for k, v in new.items():
                h[k] = v
        elif op == "flatten":
            if target is None:
                target = self.height_at(x, z)
            for k, w in self._brush(x, z, radius):
                h[k] += (target - h[k]) * min(1.0, strength) * w
        else:
            raise ValueError(f"unknown brush {op!r} (raise, lower, smooth, flatten)")

    def height_at(self, x, z):
        half = self.size / 2
        fi = min(max((x + half) / self.cell, 0), self.n - 1.001)
        fj = min(max((z + half) / self.cell, 0), self.n - 1.001)
        i, j, u, v = int(fi), int(fj), fi - int(fi), fj - int(fj)
        h = self.heights
        a, b = h[j * self.n + i], h[j * self.n + i + 1]
        c, d = h[(j + 1) * self.n + i], h[(j + 1) * self.n + i + 1]
        return (a * (1 - u) + b * u) * (1 - v) + (c * (1 - u) + d * u) * v

    def noise(self, amplitude=400.0, scale=3000.0, seed=1, octaves=4, edge=0.15):
        """Adds rolling hills: value-noise fBm, ``amplitude`` cm, features about ``scale`` cm across; the outer ``edge``
        fraction fades to 0 so the patch meets the ground around it."""
        rnd = random.Random(seed)
        grid = {}

        def lattice(ix, iz, o):
            key = (ix, iz, o)
            if key not in grid:
                grid[key] = rnd.uniform(-1, 1)
            return grid[key]

        def value(x, z, o):
            ix, iz = math.floor(x), math.floor(z)
            fx, fz = x - ix, z - iz
            sx, sz = fx * fx * (3 - 2 * fx), fz * fz * (3 - 2 * fz)
            a, b = lattice(ix, iz, o), lattice(ix + 1, iz, o)
            c, d = lattice(ix, iz + 1, o), lattice(ix + 1, iz + 1, o)
            return (a + (b - a) * sx) * (1 - sz) + (c + (d - c) * sx) * sz

        for j in range(self.n):
            for i in range(self.n):
                x, _, z = self.vertex(i, j)
                total, amp, freq = 0.0, 1.0, 1.0 / scale
                for o in range(octaves):
                    total += value(x * freq, z * freq, o) * amp
                    amp *= 0.5
                    freq *= 2.0
                e = min(i, j, self.n - 1 - i, self.n - 1 - j) / max(1, (self.n - 1) * edge) if edge > 0 else 1.0
                self.heights[j * self.n + i] += amplitude * total * min(1.0, e) ** 2

    # ---- export ----
    def _normal(self, i, j):
        h, n = self.heights, self.n
        hx = (h[j * n + min(i + 1, n - 1)] - h[j * n + max(i - 1, 0)]) / (self.cell * (min(i + 1, n - 1) - max(i - 1, 0)))
        hz = (h[min(j + 1, n - 1) * n + i] - h[max(j - 1, 0) * n + i]) / (self.cell * (min(j + 1, n - 1) - max(j - 1, 0)))
        ln = math.sqrt(hx * hx + 1 + hz * hz)
        return (-hx / ln, 1 / ln, -hz / ln)

    def _shade(self, nv):
        light = (0.45, 0.8, 0.4)
        ll = math.sqrt(sum(c * c for c in light))
        g = min(255, int(255 * (0.62 + 0.38 * max(0.0, sum(a * b for a, b in zip(nv, light)) / ll))))
        return 0xFF000000 | g << 16 | g << 8 | g

    def surface_uv(self):
        """Texture coordinates by distance along the surface (cm): u along x, v along z, each measured over the
        heights - a slope gets as many repeats as its real length instead of being stretched (u = x would squeeze a
        30 degree slope's texture by 13 %, a 60 degree one by half)."""
        n, h = self.n, self.heights
        half = self.size / 2
        su, sv = [0.0] * (n * n), [0.0] * (n * n)
        for j in range(n):
            acc = -half
            for i in range(n):
                if i:
                    acc += math.hypot(self.cell, h[j * n + i] - h[j * n + i - 1])
                su[j * n + i] = acc
        for i in range(n):
            acc = -half
            for j in range(n):
                if j:
                    acc += math.hypot(self.cell, h[j * n + i] - h[(j - 1) * n + i])
                sv[j * n + i] = acc
        return su, sv

    def parts(self):
        """{texture: (positions, normals, uvs, colours, triangles)} - game winding: (v1 - v0) x (v2 - v0) points up.
        Flat faces share vertices, mapped from above (u = x, v = z over the tile); steep ones (with a steep texture)
        get their own vertices, mapped from the side they face most (x or z across, height down)."""
        n = self.n
        verts = [self.vertex(i, j) for j in range(n) for i in range(n)]
        norms = [self._normal(i, j) for j in range(n) for i in range(n)]
        cos_limit = math.cos(math.radians(self.slope))
        flat_tris, steep_tris = [], []
        for j in range(n - 1):
            for i in range(n - 1):
                a, b, c, d = j * n + i, (j + 1) * n + i, j * n + i + 1, (j + 1) * n + i + 1
                for t in ((a, b, c), (b, d, c)):
                    p0, p1, p2 = (verts[k] for k in t)
                    e1 = [p1[q] - p0[q] for q in range(3)]
                    e2 = [p2[q] - p0[q] for q in range(3)]
                    cr = (e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0])
                    ln = math.sqrt(sum(x * x for x in cr)) or 1.0
                    steep = self.steep_texture and cr[1] / ln < cos_limit
                    (steep_tris if steep else flat_tris).append((t, cr))
        out = {}
        if flat_tris:
            used = sorted({k for t, _ in flat_tris for k in t})
            remap = {k: x for x, k in enumerate(used)}
            su, sv = self.surface_uv()
            out[self.texture] = ([verts[k] for k in used], [norms[k] for k in used],
                                 [(su[k] / self.tile, sv[k] / self.tile) for k in used],
                                 [self._shade(norms[k]) for k in used],
                                 [tuple(remap[k] for k in t) for t, _ in flat_tris])
        if steep_tris:
            pos, nrm, uv, col, tris = [], [], [], [], []
            for t, cr in steep_tris:
                side = 0 if abs(cr[0]) >= abs(cr[2]) else 2      # faces x -> map z across, faces z -> map x across
                across = 2 if side == 0 else 0
                base = len(pos)
                for k in t:
                    p = verts[k]
                    pos.append(p); nrm.append(norms[k]); col.append(self._shade(norms[k]))
                    uv.append((p[across] / self.tile, -p[1] / self.tile))
                tris.append((base, base + 1, base + 2))
            out[self.steep_texture] = (pos, nrm, uv, col, tris)
        return out

    def collision(self):
        """[((v0, v1, v2), surface)] over at most MAX_COLLISION_CELLS cells per side."""
        step = max(1, math.ceil((self.n - 1) / MAX_COLLISION_CELLS))
        idx = list(range(0, self.n - 1, step)) + [self.n - 1]
        out = []
        for jj in range(len(idx) - 1):
            for ii in range(len(idx) - 1):
                i0, i1, j0, j1 = idx[ii], idx[ii + 1], idx[jj], idx[jj + 1]
                a, b, c, d = self.vertex(i0, j0), self.vertex(i0, j1), self.vertex(i1, j0), self.vertex(i1, j1)
                out += [((a, b, c), self.surface), ((b, d, c), self.surface)]
        return out

    def to_evo(self, template: bytes, model_name: str, warnings=None, offset=None) -> bytes:
        """offset = (x, y, z): vertices in world space, for a level's ground zone (a world piece placed at 0, like
        the game's own: MISSION12_CONNECTINGLANDSCAPE as the template); None for a prop patch."""
        ox, oy, oz = offset or (0.0, 0.0, 0.0)
        move = lambda p: (p[0] + ox, p[1] + oy, p[2] + oz)
        parts = [evo_write.Part(tex, [move(p) for p in data[0]], data[1], data[2], data[4], data[3])
                 for tex, data in self.parts().items()]
        collision = [(tuple(move(v) for v in tri), surf) for tri, surf in self.collision()]
        return evo_write.rebuild(template, {0: parts}, model_name=model_name, warnings=warnings,
                                 collision={0: collision})
