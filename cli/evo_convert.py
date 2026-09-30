"""Convert Conflict: Desert Storm .EVO meshes to glTF 2.0 (.glb / .gltf) or Wavefront .obj.

  python evo_convert.py <file.evo | folder> ... [-o OUT] [--format glb|gltf|obj] [options]

glTF keeps the node hierarchy, textures + normal maps (PNG, embedded in .glb), vertex colours, character
skeletons + skin weights, animations (--anims), cloth frames (morph targets) and the tracks, wheels and guns the
game attaches to vehicles. OBJ is static geometry in the bind pose with an .mtl.
Files are looked up by name next to the model and in its sibling folders (the layout ds_extract.py writes).
Coordinates: game units are centimetres, left-handed, Y up, models facing -Z -> metres (--scale 0.01),
right-handed (Z mirrored), Y up, facing +Z (glTF convention).
No dependencies beyond Python 3 (numpy speeds up DDS decoding when installed).
"""
import argparse, glob, json, os, struct, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from conflict_ds_tools import evo, images  # noqa: E402


def included(o, helpers):
    if o.role in ("mesh", "skin"):
        return True
    return helpers and o.role in ("bone", "connector", "collision", "shadow")


class Model:
    """A scene plus everything resolved for writing it: textures, normal maps, attached vehicle parts."""

    def __init__(self, path, finder, opts, place=None):
        self.scene = evo.load(path)
        self.place = place
        self.textures, self.skin = evo.resolve_textures(self.scene, finder, opts.skin)
        self.normals = {}
        if not opts.no_normal_maps:
            for o in self.scene.objects:
                for m in o.node.meshes:
                    nm = evo.normal_map_for(opts.rfx, self.scene.name, self.textures.get(id(m), ("",))[0], finder)
                    p = finder.find(nm, (".tga", ".dds", ".png")) if nm else None
                    if p:
                        self.normals[id(m)] = (nm, p)
        self.parts = []            # [(Model, holder object index or None)]
        if not opts.no_parts and not self.scene.skinned and place is None:
            for name, conn, pos, kind in evo.vehicle_parts(self.scene, finder):
                p = finder.find(name, (".evo",))
                if not p:
                    continue
                if conn is not None:
                    pl = self.scene.objects[conn].world
                elif pos is not None:
                    pl = evo.mat_identity()
                    pl[0][3], pl[1][3], pl[2][3] = pos
                else:
                    pl = evo.mat_identity()
                holder = conn
                while holder is not None and not included(self.scene.objects[holder], opts.helpers):
                    holder = self.scene.objects[holder].parent
                try:
                    self.parts.append((Model(p, finder, opts, pl), holder))
                except evo.EvoError:
                    pass

    def missing(self):
        out = {n for n, p in self.textures.values() if n and not p}
        for part, _ in self.parts:
            out |= part.missing()
        return out


# -------------------------------------------------------------------------------------------------- glTF --

class GltfWriter:
    def __init__(self, model, anims, opts, out_path):
        self.m, self.anims, self.k, self.helpers = model, anims, opts.scale, opts.helpers
        self.embed, self.out_dir = not opts.no_embed, os.path.dirname(os.path.abspath(out_path))
        self.bin = bytearray()
        self.g = {"asset": {"version": "2.0", "generator": "evo_convert.py (Conflict: Desert Storm)"},
                  "scene": 0, "scenes": [{"name": model.scene.name, "nodes": []}], "nodes": [], "meshes": [],
                  "accessors": [], "bufferViews": [], "buffers": []}
        self.materials = {}
        self.images = {}

    # game (x, y, z) left-handed, models face -Z, cm -> glTF (x, y, -z) right-handed, facing +Z, m; triangles
    # reversed (the stored order's cross product is the outward normal in game space)
    def pos(self, v):
        return (v[0] * self.k, v[1] * self.k, -v[2] * self.k)

    @staticmethod
    def nrm(v):
        return (v[0], v[1], -v[2])

    @staticmethod
    def quat(q):
        # rotation mirrored through the z = 0 plane: axis -> (-x, -y, z) (a mirrored axial vector), angle kept
        return [-q[0], -q[1], q[2], q[3]] if q[3] >= 0 else [q[0], q[1], -q[2], -q[3]]

    def matrix(self, m):
        c = [1.0, 1.0, -1.0]
        out = [[m[i][j] * c[i] * c[j] for j in range(3)] + [m[i][3] * c[i] * self.k] for i in range(3)]
        out.append([0.0, 0.0, 0.0, 1.0])
        return [out[r][col] for col in range(4) for r in range(4)]   # column-major

    def view(self, data, target=None):
        while len(self.bin) % 4:
            self.bin.append(0)
        v = {"buffer": 0, "byteOffset": len(self.bin), "byteLength": len(data)}
        if target:
            v["target"] = target
        self.bin += data
        self.g["bufferViews"].append(v)
        return len(self.g["bufferViews"]) - 1

    def accessor(self, values, ctype, typ, target=None, minmax=False):
        fmt = {5126: "f", 5125: "I", 5123: "H", 5121: "B"}[ctype]
        width = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}[typ]
        flat = [x for v in values for x in (v if width > 1 else (v,))]
        a = {"bufferView": self.view(struct.pack("<%d%s" % (len(flat), fmt), *flat), target),
             "componentType": ctype, "count": len(values), "type": typ}
        if minmax:
            a["min"] = [min(v[i] for v in values) for i in range(width)] if width > 1 else [min(values)]
            a["max"] = [max(v[i] for v in values) for i in range(width)] if width > 1 else [max(values)]
        self.g["accessors"].append(a)
        return len(self.g["accessors"]) - 1

    def image(self, path, flip_green=False):
        """Texture index for an image file (PNG embedded or referenced; DDS/TGA converted to PNG)."""
        key = (path, flip_green)
        if key in self.images:
            return self.images[key]
        png, src = None, None
        if path.lower().endswith(".png") and not flip_green:
            if self.embed:
                with open(path, "rb") as f:
                    png = f.read()
            else:
                src = {"uri": os.path.relpath(path, self.out_dir).replace("\\", "/")}
        else:
            try:
                png = images.to_png(path, flip_green)
            except (images.ImageError, OSError, ValueError, struct.error) as e:
                print("  texture %s: %s" % (os.path.basename(path), e))
                self.images[key] = None
                return None
            if not self.embed:     # write the converted PNG next to the output
                name = os.path.splitext(os.path.basename(path))[0] + ("_n" if flip_green else "") + ".png"
                with open(os.path.join(self.out_dir, name), "wb") as f:
                    f.write(png)
                src, png = {"uri": name}, None
        img = {"name": os.path.splitext(os.path.basename(path))[0]}
        if png is not None:
            img.update(mimeType="image/png", bufferView=self.view(png))
        else:
            img.update(src)
        self.g.setdefault("images", []).append(img)
        self.g.setdefault("samplers", [{"magFilter": 9729, "minFilter": 9987}])
        self.g.setdefault("textures", []).append({"sampler": 0, "source": len(self.g["images"]) - 1})
        self.images[key] = len(self.g["textures"]) - 1
        return self.images[key]

    def material(self, name, path, normal):
        key = (name or "", normal[1] if normal else None)
        if key in self.materials:
            return self.materials[key]
        mat = {"name": name or "untextured", "pbrMetallicRoughness": {"metallicFactor": 0.0, "roughnessFactor": 0.9}}
        if path:
            ti = self.image(path)
            if ti is not None:
                mat["pbrMetallicRoughness"]["baseColorTexture"] = {"index": ti}
                if self.has_alpha(path):
                    mat["alphaMode"] = "MASK"
                    mat["alphaCutoff"] = 0.5
        else:
            mat["pbrMetallicRoughness"]["baseColorFactor"] = [0.6, 0.6, 0.6, 1.0]
        if normal:
            ni = self.image(normal[1], flip_green=True)     # DirectX-style map -> glTF (OpenGL) convention
            if ni is not None:
                mat["normalTexture"] = {"index": ni}
        self.g.setdefault("materials", []).append(mat)
        self.materials[key] = len(self.g["materials"]) - 1
        return self.materials[key]

    @staticmethod
    def has_alpha(path):
        try:
            if path.lower().endswith(".png"):
                with open(path, "rb") as f:
                    b = f.read(1 << 16)
                return b[25] in (4, 6) or b"tRNS" in b
            return images.has_alpha(images.decode_file(path)[2])
        except (OSError, images.ImageError, ValueError, struct.error, IndexError):
            return False

    def mesh(self, model, o, weights=None):
        prims = []
        for m in o.node.meshes:
            if not m.triangles:
                continue
            attrs = {"POSITION": self.accessor([self.pos(p) for p in m.positions], 5126, "VEC3", 34962, True),
                     "NORMAL": self.accessor([self.nrm(n) for n in m.normals], 5126, "VEC3", 34962),
                     "TEXCOORD_0": self.accessor(m.uvs, 5126, "VEC2", 34962)}
            if any(c != (1.0, 1.0, 1.0, 1.0) for c in m.colours):
                attrs["COLOR_0"] = self.accessor(m.colours, 5126, "VEC4", 34962)
            if weights is not None:
                joints, ws = [], []
                for vw in model.scene.vertex_weights(o, m):
                    vw = vw[:4]
                    tot = sum(w for _, w in vw) or 1.0
                    joints.append([weights[b] for b, _ in vw] + [0] * (4 - len(vw)))
                    ws.append([x / tot for _, x in vw] + [0.0] * (4 - len(vw)))
                attrs["JOINTS_0"] = self.accessor(joints, 5123, "VEC4", 34962)
                attrs["WEIGHTS_0"] = self.accessor(ws, 5126, "VEC4", 34962)
            idx = [i for t in m.triangles for i in (t[0], t[2], t[1])]   # the mirror flips the winding
            name, tpath = model.textures.get(id(m), ("", None))
            p = {"attributes": attrs,
                 "indices": self.accessor(idx, 5125 if max(idx) > 65535 else 5123, "SCALAR", 34963),
                 "material": self.material(name, tpath, model.normals.get(id(m)))}
            if m.frames:
                p["targets"] = [{"POSITION": self.accessor([self.pos((a[0] - b[0], a[1] - b[1], a[2] - b[2]))
                                                            for a, b in zip(fp, m.positions)], 5126, "VEC3",
                                                           34962, True)} for fp, _ in m.frames]
            prims.append(p)
        if not prims:
            return None
        self.g["meshes"].append({"name": o.name, "primitives": prims})
        if any("targets" in p for p in prims):
            self.g["meshes"][-1]["weights"] = [0.0] * max(len(p.get("targets", [])) for p in prims)
        return len(self.g["meshes"]) - 1

    def add_model(self, model, parent_node=None, place=None):
        """Nodes for a model; place = game-space matrix of a vehicle part relative to its holder node."""
        s = model.scene
        keys = s.nodes[0].keys if len(s.nodes[0].keys) == len(s.nodes) else None
        keep = [o for o in s.objects if included(o, self.helpers) or o.index in s.bone_ids or o.role == "empty"]
        ids = set(o.index for o in keep)
        for o in list(keep):
            j = o.parent
            while j is not None and j not in ids:
                ids.add(j)
                j = s.objects[j].parent
        node_of = {}
        for o in s.objects:
            if o.index not in ids or o.role == "skin":
                continue
            node = {"name": o.name}
            if keys and place is None:          # TRS so animations can drive the node
                pos, scale, q = keys[o.index]
                node["translation"] = list(self.pos(pos))
                node["rotation"] = self.quat((-q[0], -q[1], -q[2], q[3]))
                node["scale"] = list(scale)
            else:
                local = o.local if place is None or o.parent is not None else evo.mat_mul(place, o.local)
                node["matrix"] = self.matrix(local)
            if included(o, self.helpers):
                mi = self.mesh(model, o)
                if mi is not None:
                    node["mesh"] = mi
            self.g["nodes"].append(node)
            node_of[o.index] = len(self.g["nodes"]) - 1
        roots = []
        for o in s.objects:
            if o.index in node_of:
                if o.parent is not None and o.parent in node_of:
                    self.g["nodes"][node_of[o.parent]].setdefault("children", []).append(node_of[o.index])
                else:
                    roots.append(node_of[o.index])
        for r in roots:
            if parent_node is None:
                self.g["scenes"][0]["nodes"].append(r)
            else:
                self.g["nodes"][parent_node].setdefault("children", []).append(r)
        skins = [o for o in s.objects if o.role == "skin"]
        if skins:
            joints = sorted(i for i in s.bone_ids if i in node_of)
            jindex = {b: n for n, b in enumerate(joints)}
            ibm = [self.matrix(evo.mat_invert_affine(s.objects[b].world)) for b in joints]
            jroots = [node_of[b] for b in joints if s.objects[b].parent not in jindex]
            self.g.setdefault("skins", []).append({
                "name": s.name + "_skin", "joints": [node_of[b] for b in joints],
                "inverseBindMatrices": self.accessor(ibm, 5126, "MAT4"),
                "skeleton": jroots[0] if jroots else node_of[joints[0]]})
            for o in skins:
                mi = self.mesh(model, o, weights=jindex)
                if mi is not None:
                    self.g["nodes"].append({"name": o.name, "mesh": mi, "skin": len(self.g["skins"]) - 1})
                    self.g["scenes"][0]["nodes"].append(len(self.g["nodes"]) - 1)
        for part, holder in model.parts:
            if holder is not None and holder in node_of:
                rel = evo.mat_mul(evo.mat_invert_affine(s.objects[holder].world), part.place)
                self.add_model(part, node_of[holder], rel)
            else:
                self.add_model(part, parent_node, part.place if place is None else evo.mat_mul(place, part.place))
        return node_of

    def add_animation(self, model, node_of, anim):
        s = model.scene
        times = [f / anim.fps for f in range(anim.frames)]
        tacc = self.accessor(times, 5126, "SCALAR", None, True)
        samplers, channels = [], []
        for i in range(len(s.objects)):
            if i not in node_of or i >= len(anim.tracks):
                continue
            keys = [anim.key(i, f) for f in range(anim.frames)]
            rots, prev = [], None
            for _, _, q in keys:
                r = self.quat(q)
                if prev is not None and sum(a * b for a, b in zip(r, prev)) < 0:
                    r = [-x for x in r]
                rots.append(r)
                prev = r
            for path, values, typ in (("translation", [list(self.pos(p)) for p, _, _ in keys], "VEC3"),
                                      ("rotation", rots, "VEC4"),
                                      ("scale", [list(sc) for _, sc, _ in keys], "VEC3")):
                samplers.append({"input": tacc, "output": self.accessor(values, 5126, typ),
                                 "interpolation": "LINEAR"})
                channels.append({"sampler": len(samplers) - 1, "target": {"node": node_of[i], "path": path}})
        if channels:
            name = anim.name if not anim.name.startswith("_") else "anim" + anim.name
            self.g.setdefault("animations", []).append({"name": name, "samplers": samplers, "channels": channels})

    def write(self, path, binary):
        node_of = self.add_model(self.m)
        for a in self.anims:
            self.add_animation(self.m, node_of, a)
        while len(self.bin) % 4:
            self.bin.append(0)
        g = self.g
        if binary:
            g["buffers"] = [{"byteLength": len(self.bin)}]
            js = json.dumps(g, separators=(",", ":")).encode()
            js += b" " * (-len(js) % 4)
            with open(path, "wb") as f:
                f.write(struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(self.bin)))
                f.write(struct.pack("<II", len(js), 0x4E4F534A) + js)
                f.write(struct.pack("<II", len(self.bin), 0x004E4942) + bytes(self.bin))
        else:
            binname = os.path.splitext(os.path.basename(path))[0] + ".bin"
            with open(os.path.join(os.path.dirname(os.path.abspath(path)), binname), "wb") as f:
                f.write(self.bin)
            g["buffers"] = [{"byteLength": len(self.bin), "uri": binname}]
            with open(path, "w") as f:
                json.dump(g, f, indent=1)


# --------------------------------------------------------------------------------------------------- OBJ --

def write_obj(model, opts, path):
    out_dir = os.path.dirname(os.path.abspath(path))
    mtl_path = os.path.splitext(path)[0] + ".mtl"
    lines = ["# %s - converted by evo_convert.py" % model.scene.name, "mtllib " + os.path.basename(mtl_path)]
    mats = {}
    base = [1]
    k = opts.scale

    def tex_ref(p):
        if not p:
            return None
        if p.lower().endswith(".png"):
            return os.path.relpath(p, out_dir).replace("\\", "/")
        name = os.path.splitext(os.path.basename(p))[0] + ".png"     # DDS/TGA -> PNG next to the .obj
        try:
            with open(os.path.join(out_dir, name), "wb") as f:
                f.write(images.to_png(p))
            return name
        except (images.ImageError, OSError, ValueError, struct.error):
            return None

    def emit(m_, place):
        for o in m_.scene.objects:
            if not included(o, opts.helpers):
                continue
            world = evo.mat_identity() if o.role == "skin" else o.world
            if place is not None:
                world = evo.mat_mul(place, world)
            lines.append("o " + o.name.replace(" ", "_"))
            for m in o.node.meshes:
                name, tpath = m_.textures.get(id(m), ("", None))
                mname = (name or "untextured").replace(" ", "_")
                mats.setdefault(mname, tpath)
                for p in m.positions:
                    x, y, z = evo.mat_apply(world, p)
                    lines.append("v %.6f %.6f %.6f" % (x * k, y * k, -z * k))
                for n in m.normals:
                    x, y, z = evo._normalise(evo.mat_apply(world, n, 0.0))
                    lines.append("vn %.6f %.6f %.6f" % (x, y, -z))
                for u, v in m.uvs:
                    lines.append("vt %.6f %.6f" % (u, 1.0 - v))
                lines.append("usemtl " + mname)
                for t in m.triangles:
                    a, c, b = (i + base[0] for i in t)       # the mirror flips the winding
                    lines.append("f %d/%d/%d %d/%d/%d %d/%d/%d" % (a, a, a, b, b, b, c, c, c))
                base[0] += len(m.positions)
        for part, _ in m_.parts:
            emit(part, part.place if place is None else evo.mat_mul(place, part.place))

    emit(model, None)
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    with open(mtl_path, "w") as f:
        for mname, tpath in mats.items():
            f.write("newmtl %s\nKd 1 1 1\nKa 0 0 0\nKs 0 0 0\n" % mname)
            ref = tex_ref(tpath)
            if ref:
                f.write("map_Kd %s\n" % ref)
            f.write("\n")


# -------------------------------------------------------------------------------------------------- main --

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", help=".EVO files, folders or glob patterns")
    ap.add_argument("-o", "--out", help="output file (one input) or folder (default: next to each input)")
    ap.add_argument("-f", "--format", choices=("glb", "gltf", "obj"), default="glb")
    ap.add_argument("--scale", type=float, default=0.01, help="game units (cm) -> output units (default 0.01 = m)")
    ap.add_argument("--anims", nargs="?", const="all", default="",
                    help="characters (glb/gltf): 'all' = every compatible .prb animation found next to the model "
                         "and in its sibling folders, or comma-separated names / .prb files")
    ap.add_argument("--helpers", action="store_true",
                    help="also export connectors, collision/shadow meshes and bone proxy boxes")
    ap.add_argument("--no-parts", action="store_true", help="don't attach vehicle tracks, wheels and guns")
    ap.add_argument("--no-normal-maps", action="store_true", help="don't add the .RFX normal maps")
    ap.add_argument("--skin", default="", help="texture for characters' untextured skin, e.g. HERO01_US_01")
    ap.add_argument("--textures", action="append", default=[], help="extra search folder (repeatable)")
    ap.add_argument("--no-embed", action="store_true", help="glb/gltf: reference texture files instead of embedding")
    a = ap.parse_args()

    files = []
    for i in a.inputs:
        if os.path.isdir(i):
            files += sorted(glob.glob(os.path.join(i, "*.[eE][vV][oO]")))
        elif any(c in i for c in "*?["):
            files += sorted(glob.glob(i))
        else:
            files.append(i)
    if not files:
        sys.exit("no .EVO files found")
    single_out = a.out and len(files) == 1 and os.path.splitext(a.out)[1]
    failed = 0
    finders = {}
    for path in files:
        folder = os.path.dirname(os.path.abspath(path))
        if folder not in finders:
            f = evo.AssetFinder(path, a.textures)
            finders[folder] = (f, evo.read_rfx(f))
        finder, a.rfx = finders[folder]
        try:
            model = Model(path, finder, a)
        except (evo.EvoError, OSError) as e:
            print("FAILED %s: %s" % (path, e))
            failed += 1
            continue
        anims = []
        if a.anims and model.scene.skinned and a.format != "obj":
            if a.anims == "all":
                cands = finder.animations()
            else:
                cands = {}
                for n in a.anims.split(","):
                    p = n if os.path.exists(n) else finder.find(n, (".prb",))
                    if p:
                        cands[os.path.splitext(os.path.basename(n))[0].upper()] = p
                    else:
                        print("  animation %s not found" % n)
            for n, p in sorted(cands.items()):
                try:
                    an = evo.read_prb(p, n)
                except (evo.EvoError, OSError):
                    continue
                if evo.compatible(model.scene, an):
                    anims.append(an)
        stem = os.path.splitext(os.path.basename(path))[0]
        if single_out:
            out = a.out
        else:
            out_dir = a.out or folder
            os.makedirs(out_dir, exist_ok=True)
            out = os.path.join(out_dir, stem + "." + a.format)
        if a.format == "obj":
            write_obj(model, a, out)
        else:
            GltfWriter(model, anims, a, out).write(out, a.format == "glb")
        missing = sorted(model.missing())
        tris = sum(len(m.triangles) for o in model.scene.objects if included(o, a.helpers) for m in o.node.meshes)
        extra = []
        if model.skin:
            extra.append("skin texture " + model.skin)
        if model.parts:
            extra.append("%d parts" % len(model.parts))
        if anims:
            extra.append("%d animations" % len(anims))
        if missing:
            extra.append("missing textures: " + ", ".join(missing))
        print("%s -> %s (%d objects, %d triangles%s)" % (os.path.basename(path), out, len(model.scene.objects), tris,
                                                         "".join(", " + e for e in extra)))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
