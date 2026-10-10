"""Roads: a strip along a centreline, following the ground, exported as a game model with collision.

``points`` = the centreline (cm, local to the road's ENVI record = the first point at 0), already on the ground - the
editor samples the ground under a smoothed line every ~2 m; ``road_from_clicks`` smooths and resamples a few clicked
points itself when no ground is known (MCP). The strip is ``width`` cm wide, lifted ``lift`` cm so it doesn't flicker
with the ground, textured across (u 0..1) and along (v = distance / ``tile``), and its collision is the same strip
(``surface`` = Tarmac, Baked Earth for dirt - both walkable). Exported over the same template prop as terrain patches.
"""
from __future__ import annotations

import json
import math

from . import evo_write


class Road:
    def __init__(self, points, width=600.0, texture="M01_road01", tile=600.0, surface="Tarmac", lift=6.0, across=False):
        if len(points) < 2:
            raise ValueError("a road needs at least two points")
        self.points = [tuple(float(c) for c in p) for p in points]
        self.width, self.texture, self.tile, self.surface = float(width), texture, float(tile), surface
        self.lift = float(lift)
        self.across = bool(across)          # texture's road runs along u instead of v (rotate it 90 degrees)

    def to_json(self):
        return {"points": [[round(c, 1) for c in p] for p in self.points], "width": self.width, "texture": self.texture,
                "tile": self.tile, "surface": self.surface, "lift": self.lift, "across": self.across}

    @classmethod
    def from_json(cls, d):
        return cls(d["points"], d.get("width", 600.0), d.get("texture", "M01_road01"), d.get("tile", 600.0),
                   d.get("surface", "Tarmac"), d.get("lift", 6.0), d.get("across", False))

    def dumps(self):
        return json.dumps(self.to_json())

    def strip(self):
        """positions, normals, uvs, triangles (game winding, facing up)."""
        pts = self.points
        pos, nrm, uv, tris = [], [], [], []
        dist = 0.0
        prev_edge = {}
        for k, p in enumerate(pts):
            a, b = pts[max(0, k - 2)], pts[min(len(pts) - 1, k + 2)]   # a wider window: smoother sides
            tx, tz = b[0] - a[0], b[2] - a[2]
            ln = math.hypot(tx, tz) or 1.0
            sx, sz = -tz / ln, tx / ln                        # sideways (left of the direction of travel)
            if k:
                dist += math.dist(p, pts[k - 1])
            half = self.width / 2
            for side in (-1, 1):
                v = (p[0] + sx * half * side, p[1] + self.lift, p[2] + sz * half * side)
                last = prev_edge.get(side)
                if last is not None and (v[0] - last[0]) * tx + (v[2] - last[2]) * tz < 0:
                    v = last                                     # inside of a tight bend: don't fold back
                prev_edge[side] = v
                pos.append(v)
                nrm.append((0.0, 1.0, 0.0))
                u, v = (side + 1) / 2, dist / self.tile
                uv.append((v, u) if self.across else (u, v))
        for k in range(len(pts) - 1):
            l0, r0, l1, r1 = 2 * k, 2 * k + 1, 2 * k + 2, 2 * k + 3
            for t in ((l0, l1, r0), (r0, l1, r1)):
                p0, p1, p2 = (pos[i] for i in t)
                cy = (p1[2] - p0[2]) * (p2[0] - p0[0]) - (p1[0] - p0[0]) * (p2[2] - p0[2])
                tris.append(t if cy > 0 else (t[0], t[2], t[1]))   # up in the game's winding
        return pos, nrm, uv, tris

    def to_evo(self, template: bytes, model_name: str, warnings=None) -> bytes:
        pos, nrm, uv, tris = self.strip()
        part = evo_write.Part(self.texture, pos, nrm, uv, tris, [0xFFFFFFFF] * len(pos))
        collision = [((pos[a], pos[b], pos[c]), self.surface) for a, b, c in tris]
        return evo_write.rebuild(template, {0: [part]}, model_name=model_name, warnings=warnings,
                                 collision={0: collision})


def road_from_clicks(clicks, spacing=200.0):
    """Centreline from a few clicked points: a Catmull-Rom curve through them, resampled every ``spacing`` cm; heights
    are interpolated between the clicks (the editor samples the real ground instead)."""
    pts = [tuple(float(c) for c in p) for p in clicks]
    if len(pts) < 2:
        raise ValueError("a road needs at least two points")
    out = [pts[0]]
    for k in range(len(pts) - 1):
        p0, p1, p2, p3 = pts[max(0, k - 1)], pts[k], pts[k + 1], pts[min(len(pts) - 1, k + 2)]
        steps = max(1, int(math.dist(p1, p2) / spacing))
        for s in range(1, steps + 1):
            t = s / steps
            out.append(tuple(0.5 * ((2 * p1[i]) + (-p0[i] + p2[i]) * t + (2 * p0[i] - 5 * p1[i] + 4 * p2[i] - p3[i]) * t * t
                                    + (-p0[i] + 3 * p1[i] - 3 * p2[i] + p3[i]) * t ** 3) for i in range(3)))
    return out
