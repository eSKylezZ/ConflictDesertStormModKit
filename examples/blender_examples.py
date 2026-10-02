"""Blender half of build_examples.py: the example models and animations, made the way a modder would in
Blender (import, edit, File > Export), only scripted.

    blender -b --factory-startup --python examples/blender_examples.py -- <work folder> <Mods folder>

<work folder> holds the game files build_examples.py copied out of the archives.
"""
import math
import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import conflict_ds_tools  # noqa: E402

conflict_ds_tools.register()
WORK, OUT = sys.argv[sys.argv.index("--") + 1:][:2]


def reset():
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.images, bpy.data.armatures, bpy.data.actions):
        for d in list(coll):
            coll.remove(d)
    scn = bpy.context.scene
    scn.render.fps, scn.render.fps_base = 24, 1.0


def load(name):
    bpy.ops.import_scene.evo(filepath=os.path.join(WORK, name))
    return [o for o in bpy.context.scene.objects if o.type == "MESH"]


def select(objs):
    for o in bpy.context.scene.objects:
        o.select_set(o in objs)
    bpy.context.view_layer.objects.active = objs[0]


def target(mod, name):
    d = os.path.join(OUT, mod)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


def image(png, name):
    img = bpy.data.images.load(os.path.join(WORK, png))
    img.name = name                        # the exporter names the texture after the image
    return img


def retexture(mat, img):
    for n in mat.node_tree.nodes:
        if n.type == "TEX_IMAGE" and n.image and not n.image.name.upper().endswith("BUMP.PNG") \
                and n.image.colorspace_settings.name != "Non-Color":
            n.image = img
            return


def front_uv(ob, axis):
    """UV of the face nearest the model's front end along world axis (the muzzle's metal)."""
    me, mw = ob.data, ob.matrix_world
    uvs = me.uv_layers.active.data
    best = max(me.polygons, key=lambda p: max((mw @ me.vertices[v].co)[axis] for v in p.vertices))
    return tuple(uvs[best.loop_start].uv)


def paint_uv(ob, uv):
    for loop in ob.data.uv_layers.active.data:
        loop.uv = uv


def done(path):
    print("example:", os.path.relpath(path, OUT), os.path.getsize(path), "bytes")


# ---- SilencedSAW: a suppressor modelled onto the SAW, blue texture -> MYSAW.evo + SAW_Blue.dds ----
reset()
gun = load("LMG01_M249SAW.EVO")
main = next(o for o in gun if any("M249SAW" in m.name.upper() for m in o.data.materials))
blue = image("SAW_Blue.png", "SAW_Blue")
retexture(main.data.materials[0], blue)
verts = [main.matrix_world @ v.co for v in main.data.vertices]
zmax = max(v.z for v in verts)                         # the model points its barrel up (+Z)
front = [v for v in verts if v.z > zmax - 0.03]
cx, cy = sum(v.x for v in front) / len(front), sum(v.y for v in front) / len(front)
bpy.ops.mesh.primitive_cylinder_add(vertices=16, radius=0.022, depth=0.18, location=(cx, cy, zmax + 0.09))
sup = bpy.context.active_object
sup.name = "suppressor"
sup.data.materials.append(main.data.materials[0])
paint_uv(sup, front_uv(main, 2))                       # the barrel's own metal, not a random patch of the texture
select(gun + [sup])
bpy.ops.export_scene.evo(filepath=target("SilencedSAW", "MYSAW.evo"))
done(target("SilencedSAW", "MYSAW.evo"))

# ---- LoudMP5: the MP5SD with its suppressor taken off and a short plain barrel -> MP5BREACHER.evo ----
# The suppressor is the front of the gun: from 25 cm to the muzzle at 47.9 cm (radius 1.7 cm; the handguard in front of
# the receiver ends at 22 cm with radius 1). The model is low-poly - the upper receiver is one box from the stock to the
# muzzle - so the vertices past the cut are pulled back onto it (the suppressor flattens to nothing, the box shortens)
# rather than deleted.
reset()
mp5 = load("SMG01_MP5SD3.EVO")
body = max(mp5, key=lambda o: len(o.data.vertices))
mw = body.matrix_world
co = [mw @ v.co for v in body.data.vertices]
axis = max(range(3), key=lambda i: max(c[i] for c in co) - min(c[i] for c in co))
front, back = max(c[axis] for c in co), min(c[axis] for c in co)
side = [i for i in range(3) if i != axis]


def width(sel):
    return max(max(c[i] for c in sel) - min(c[i] for c in sel) for i in side)


assert width([c for c in co if c[axis] > front - 0.05]) < width([c for c in co if c[axis] < back + 0.05]),     "MP5SD: the muzzle isn't at the positive end - the cut would take the stock"
cut = front - 0.229
tube = [c for c in co if c[axis] > cut + 0.01]
centre = [sum(c[i] for c in tube) / len(tube) for i in range(3)]
metal = front_uv(body, axis)
inv = mw.inverted()
moved = 0
for v, w in zip(body.data.vertices, co):
    if w[axis] > cut:
        w = w.copy()
        w[axis] = cut
        v.co = inv @ w
        moved += 1
length = 0.09                                          # 3 cm inside the handguard, 6 cm out
centre[axis] = cut - 0.03 + length / 2
rot = {0: (0, math.pi / 2, 0), 1: (math.pi / 2, 0, 0), 2: (0, 0, 0)}[axis]
bpy.ops.mesh.primitive_cylinder_add(vertices=10, radius=0.0065, depth=length, location=centre, rotation=rot)
barrel = bpy.context.active_object
barrel.name = "barrel"
barrel.data.materials.append(body.data.materials[0])
paint_uv(barrel, metal)
select(mp5 + [barrel])
bpy.ops.export_scene.evo(filepath=target("LoudMP5", "MP5BREACHER.evo"))
done(target("LoudMP5", "MP5BREACHER.evo"))
print("  MP5: long axis %d, muzzle %.3f, cut at %.3f, %d vertices pulled back" % (axis, front, cut, moved))

# ---- _HazardDrums: the oil drum replaced by name, with a new texture -> OILDRUM.evo + HazardDrum.dds ----
reset()
drum = load("OILDRUM.EVO")
stripes = image("HazardDrum.png", "HazardDrum")
for o in drum:
    for m in o.data.materials:
        retexture(m, stripes)
select(drum)
bpy.ops.export_scene.evo(filepath=target("_HazardDrums", "OILDRUM.evo"))
done(target("_HazardDrums", "OILDRUM.evo"))

# ---- _BigHead: Bradley's body mesh with a 1.5x head, replaced by name -> HERO01_ARMSTRONG.evo ----
reset()
meshes = load("HERO01_ARMSTRONG.EVO")
skin = next(o for o in meshes if any(m.type == "ARMATURE" for m in o.modifiers))
top = max(v.co.z for v in skin.data.vertices)
neck = top - 0.28
cz = neck + 0.10
for v in skin.data.vertices:
    if v.co.z >= neck:
        v.co.x *= 1.5
        v.co.y *= 1.5
        v.co.z = cz + (v.co.z - cz) * 1.5
select([skin])
bpy.ops.export_scene.evo(filepath=target("_BigHead", "HERO01_ARMSTRONG.evo"))
done(target("_BigHead", "HERO01_ARMSTRONG.evo"))
# BigHeadUniform: the same body under a name of its own -> a .skin "body", worn only with that uniform
select([skin])
bpy.ops.export_scene.evo(filepath=target("BigHeadUniform", "BIGHEAD_BRADLEY.evo"))
done(target("BigHeadUniform", "BIGHEAD_BRADLEY.evo"))


# ---- animations ----
def soldier_with(prb):
    reset()
    load("HERO01_ARMSTRONG.EVO")
    arm = next(o for o in bpy.context.scene.objects if o.type == "ARMATURE")
    select([arm])
    bpy.ops.import_anim.evo_prb(filepath=os.path.join(WORK, prb))
    act = arm.animation_data.action
    return arm, act


def export_anim(arm, act, path, template=""):
    select([arm])
    fps = float(act.get("evo_fps", 15.0))
    bpy.ops.export_anim.evo_prb(filepath=path, fps=fps, template=template)
    done(path)


def keyed(act, arm):
    """The action's fcurves (Blender 4.4+ keeps them per slot)."""
    slot = getattr(arm.animation_data, "action_slot", None)
    if slot is not None:
        from bpy_extras import anim_utils
        return list(anim_utils.action_get_channelbag_for_slot(act, slot).fcurves)
    return list(act.fcurves)


# _HeadTilt: the standing idle with the head tilted 35 degrees, replaced by name
from mathutils import Quaternion, Vector  # noqa: E402

arm, act = soldier_with("UPRIGHT_RELAXED_WITH_RIFLE.PRB")
path = 'pose.bones["BIP01 HEAD"].rotation_quaternion'
curves = [next(f for f in keyed(act, arm) if f.data_path == path and f.array_index == i) for i in range(4)]
tilt = Quaternion(Vector((0, 0, 1)), math.radians(35))
for k in range(len(curves[0].keyframe_points)):
    q = Quaternion([curves[i].keyframe_points[k].co[1] for i in range(4)]) @ tilt
    for i in range(4):
        curves[i].keyframe_points[k].co[1] = q[i]
for c in curves:
    c.update()
export_anim(arm, act, target("_HeadTilt", "upright_relaxed_with_rifle.prb"))

# _BouncyRun: the rifle run cycle with a big bounce in every stride; same name, so the game's footstep events are
# kept (Footsteps From = the game animation of that name)
arm, act = soldier_with("UPRIGHT_RELAXED_RUN_WITH_RIFLE.PRB")
root = next(pb for pb in arm.pose.bones if pb.parent is None)
scn = bpy.context.scene
start, end = (int(round(f)) for f in act.frame_range)
for f in range(start, end + 1):
    scn.frame_set(f)
    m = root.matrix.copy()
    m.translation.z += 0.16 * abs(math.sin(2 * math.pi * (f - start) / max(1, end - start)))  # two hops per cycle
    root.matrix = m
    root.keyframe_insert("location", frame=f)
export_anim(arm, act, target("_BouncyRun", "UPRIGHT_RELAXED_RUN_WITH_RIFLE.prb"),
            template=os.path.join(WORK, "UPRIGHT_RELAXED_RUN_WITH_RIFLE.PRB"))

# SpeedReload: the game's reloads played twice as fast under new names. The exporter takes the length from the
# scene frame rate, so doubling it halves the time with the same keys.
for game_anim, new in (("UPRIGHT_RELAXED_RELOAD_RIFLE.PRB", "UPRIGHT_RELOAD_FAST.prb"),
                       ("PRONE_RELOAD_LMG.PRB", "PRONE_RELOAD_FAST.prb")):
    arm, act = soldier_with(game_anim)
    bpy.context.scene.render.fps = 48
    export_anim(arm, act, target("SpeedReload", new))
