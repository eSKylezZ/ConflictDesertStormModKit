"""Small helpers for building game models by script: flat-shaded triangles with an atlas region each, boxes, tubes
along an axis, and the conversion to an evo_write.Part (object space via a 4x4 matrix)."""
import math, os, sys
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from conflict_ds_tools import evo, evo_write  # noqa: E402


class Mesh:
    def __init__(self, texture, regions, size=256):
        self.texture, self.regions, self.size = texture, regions, size
        self.faces = []   # (a, b, c, normal, region)

    def tri(self, a, b, c, region, out=None, both=False):
        a, b, c = np.asarray(a, float), np.asarray(b, float), np.asarray(c, float)
        n = np.cross(b - a, c - a)
        ln = np.linalg.norm(n)
        if ln < 1e-9:
            return
        n /= ln
        if out is not None and np.dot(n, out) < 0:
            b, c, n = c, b, -n
        self.faces.append((a, b, c, n, region))
        if both:
            self.faces.append((a, c, b, -n, region))

    def quad(self, a, b, c, d, region, out=None, both=False):
        self.tri(a, b, c, region, out, both)
        self.tri(a, c, d, region, out, both)

    def box(self, lo, hi, region):
        (x0, y0, z0), (x1, y1, z1) = lo, hi
        c = np.array(((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2))
        for f in (((x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)), ((x1, y0, z0), (x0, y0, z0), (x0, y1, z0), (x1, y1, z0)),
                  ((x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0)), ((x1, y0, z1), (x1, y0, z0), (x1, y1, z0), (x1, y1, z1)),
                  ((x0, y1, z1), (x1, y1, z1), (x1, y1, z0), (x0, y1, z0)), ((x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1))):
            self.quad(*f, region, out=np.mean(f, axis=0) - c)

    def tube(self, centre, axis, length, r_out, r_in, region, n=12, caps=True):
        """A tube along axis 0/1/2 from centre[axis] to centre[axis] + length; r_in 0 = a solid rod (end discs)."""
        u, v = [k for k in range(3) if k != axis]
        def P(a, r, t):
            p = list(centre)
            p[u] += r * math.cos(a)
            p[v] += r * math.sin(a)
            p[axis] += t
            return p
        sgn = 1 if length >= 0 else -1
        for k in range(n):
            a0, a1 = 2 * math.pi * k / n, 2 * math.pi * (k + 1) / n
            radial = np.zeros(3)
            radial[u], radial[v] = math.cos((a0 + a1) / 2), math.sin((a0 + a1) / 2)
            self.quad(P(a0, r_out, 0), P(a1, r_out, 0), P(a1, r_out, length), P(a0, r_out, length), region, out=radial)
            if r_in > 0:
                self.quad(P(a0, r_in, 0), P(a1, r_in, 0), P(a1, r_in, length), P(a0, r_in, length), region, out=-radial)
            if caps:
                for t, s in ((0, -sgn), (length, sgn)):
                    end = np.zeros(3)
                    end[axis] = s
                    if r_in > 0:
                        self.quad(P(a0, r_in, t), P(a1, r_in, t), P(a1, r_out, t), P(a0, r_out, t), region, out=end)
                    else:
                        self.tri(P(0, 0, t), P(a0, r_out, t), P(a1, r_out, t), region, out=end)

    def part(self, to_local=None, tile=20.0):
        to_local = to_local if to_local is not None else evo.mat_identity()
        rot = np.array([row[:3] for row in to_local[:3]], float)
        pos, nrm, uv, tris = [], [], [], []
        for a, b, c, n, region in self.faces:
            u0, v0, u1, v1 = self.regions[region]
            ax = np.argsort(np.abs(n))[:2]
            for p in (a, b, c):
                pos.append(evo.mat_apply(to_local, p))
                nn = rot @ n
                nrm.append(tuple(nn / (np.linalg.norm(nn) + 1e-9)))
                s, t = (p[ax[0]] / tile) % 1.0, (p[ax[1]] / tile) % 1.0
                uv.append(((u0 + 1 + s * (u1 - u0 - 2)) / self.size, (v0 + 1 + t * (v1 - v0 - 2)) / self.size))
            i = len(pos) - 3
            tris.append((i, i + 1, i + 2))
        return evo_write.Part(self.texture, pos, nrm, uv, tris)
