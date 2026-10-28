"""Blender side of the Conflict: Desert Storm tools (File > Import > Conflict: Desert Storm ...).

Desert Storm and Desert Storm II: models import and export (the game follows the model file: EOBJ 0x2F / 0x35),
.prb animations import and export (DS2's tracks bind to the skeleton by object name hash), collision surfaces include
DS2's extra Default.spl rows (Snow, Gravel ... - refused on a DS1 model).
The Great Escape: models and .prb animations import and export like DS2's (EOBJ 0x34; its own header offsets and
40-byte animation keys - evo_write._Layout / write_prb_ds2).
Into a Mod Folder writes <game>/Mods/<Mod> for every game but Denied Ops: each plugin (DesertStormFix,
GreatEscapeFix, DesertStorm2Fix, VietnamFix, GlobalStormFix) serves Mods files by name over the archives. A new model
name gets a starter .weapon where the plugin reads them (DS1, DS2, The Great Escape; .skin: DS1); in Vietnam / Global
Storm a new name is used only once a game file refers to it - Replace the Game's Model changes the original. Test in
Game starts DS1 straight into a level (+level), the others at their launcher / front end.
Vietnam and Global Storm (EOBJ 0x40 / 0x49): models, characters, level scenes and archives import (+ Vietnam's
animations, Global Storm's .ani animations); export writes static models (weapons, props, vehicle parts, level
pieces), their collision and points, character skins of one or several parts (one skin object per material,
evo_write.rebuild_cv), Vietnam .prb animations (evo_write.write_prb_cv), Global Storm .ani animations
(evo_write.write_ani_cg) and shadow meshes added to static models.

- .evo import: props, vehicles (node hierarchy + the tracks, wheels and guns the game attaches), world pieces,
  cloth (frames as shape keys), characters (armature, skin weights, optionally all their animations).
- .prb import: animations onto a character armature made by the .evo import; .prb export: the armature's action
  back to the game (footstep events kept from the game animation it replaces).
- Archive extraction: the game's .dat files -> folders of meshes, textures (+ PNG), animations and tables.
- .evo export: new geometry for a game model's parts (the model it is based on keeps attachment points,
  materials and layout): static models (weapons, props, vehicles) and characters (the skin, weighted to the
  game's skeleton). Collision: "#BSP_..." objects (one material per surface, "Surface: Blocking Metal") become
  the game's collision tree, "#SHA_..." objects the shadow mesh, "#PT_..." empties its points (muzzle, shell
  ejection, M203 muzzle, ladder climb points); the import shows them all (Helpers). Brand-new props need no game
  model to start from: Based On "Auto / Generic Prop" (or nothing to go on) uses BH_BODGEPLATE from the extracted
  folders or straight out of the player's own game archives.
- .prb footstep events <-> timeline (pose) markers on the action: FS_LEFT / FS_RIGHT (+ _25 = run step), EVENT_<n>.
- 3D View sidebar (N) > Desert Storm: surfaces (assign, pick, show), helpers, points, skin weight check, roads from
  curves, model statistics against the game's limits, one-click re-export into the active mod.
- Level scenes (.ENV / an editor mod's .envpatch): world pieces, props, kit pieces, objects as collection instances of
  each model, lights, trigger regions and ambient sounds (File > Import > Conflict: Desert Storm Scene (.env)).
- Tactical minimap bake (top-down orthographic render -> GW<name>.img + GW<name>0.png in the active mod).
- Asset Browser library: every extracted model as an editable .blend, marked as an asset in a catalog.
- Weapons: Aim Camera down the sights (#PT_REAR_SIGHT / #PT_FRONT_SIGHT, Blender only), Snap to Hand (grip check).
- Vehicles: turret / gun pivots at the game's mount points with rotation limits; uniforms preview for squad soldiers;
  Test in Game (export into the mod, start the game with +level <level>).
Materials: base texture, the .RFX normal map (DirectX green flipped), backface culling like the game.
"""
import collections
import math
import os
import re
import tempfile
import threading
import time

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty,
                       StringProperty)
from bpy_extras.io_utils import ExportHelper, ImportHelper
from mathutils import Color, Matrix, Vector

from . import (archive, assetlib, bsp, evo, evo_write, games, gcaudio, images, kits, levelscene, minimap,
               modlayout, porting, roads, sceneexport)
from .crossgame import character_skin_parts, cross_repose, head_connector_offsets, split_skin  # noqa: F401
from .env import ZONE_TYPES

# One add-on per game (scripts/build_zip.py --game <id>): the zip carries addon_game.py with the game it is for and a
# prefix for everything Blender registers by name (operator ids, the sidebar panel, Scene properties), so the game
# add-ons and the all-games add-on (conflict_ds_tools, no addon_game.py: old ids, every game) install side by side.
try:
    from .addon_game import GAME, PREFIX
except ImportError:
    GAME, PREFIX = None, None


def sp(name):
    """Name of one of this add-on's Scene properties ("surface" -> ds_surface, cds1_surface ...)."""
    return "%s_%s" % (PREFIX or "ds", name)


class _SceneProps:
    """props(scene).surface reads / writes this add-on's Scene property (sp("surface"))."""
    __slots__ = ("_scene",)

    def __init__(self, scene):
        object.__setattr__(self, "_scene", scene)

    def __getattr__(self, name):
        return getattr(self._scene, sp(name))

    def __setattr__(self, name, value):
        setattr(self._scene, sp(name), value)


props = _SceneProps


def op_id(idname):
    """This add-on's id for an operator: import_scene.evo -> import_scene.cds1_evo, object.ds_x -> object.cds1_x."""
    if not PREFIX:
        return idname
    cat, name = idname.split(".", 1)
    if name.startswith(PREFIX + "_"):
        return idname
    return "%s.%s_%s" % (cat, PREFIX, name[3:] if name.startswith("ds_") else name)


def run_op(cls, *args, **kw):
    """Call one of this add-on's operators (bpy.ops.<its id>)."""
    cat, name = cls.bl_idname.split(".", 1)
    return getattr(getattr(bpy.ops, cat), name)(*args, **kw)


HELPER_COLLECTION = "helpers"
SIGHT_ROLES = ("REAR_SIGHT", "FRONT_SIGHT")      # Blender-only helpers for the Aim Camera (not written to the game)


# game: left-handed, Y up, models face -Z, cm  ->  Blender: right-handed, Z up, facing -Y (front view):
# (x, y, z) -> (x, z, y) times scale. The mirror flips the winding, so triangles are reversed (a, c, b).
AXES = ((0, 1.0), (2, 1.0), (1, 1.0))      # Blender axis i = sign * game axis


def conv_point(v, k):
    return Vector([v[a] * s * k for a, s in AXES])


def conv_dir(v):
    return Vector([v[a] * s for a, s in AXES])


def conv_matrix(m, k):
    out = Matrix.Identity(4)
    for i, (a, s) in enumerate(AXES):
        for j, (b, t) in enumerate(AXES):
            out[i][j] = m[a][b] * s * t
        out[i][3] = m[a][3] * s * k
    return out


def translation(p):
    m = evo.mat_identity()
    m[0][3], m[1][3], m[2][3] = p
    return m


# ------------------------------------------------------------------------------------------------ materials --

def load_image(path, non_color=False):
    for img in bpy.data.images:
        if img.filepath and os.path.normcase(bpy.path.abspath(img.filepath)) == os.path.normcase(path):
            return img
    try:
        img = bpy.data.images.load(path)
    except RuntimeError:
        return None
    if non_color:
        try:
            img.colorspace_settings.name = "Non-Color"
        except TypeError:
            pass
    return img


def _node(tree, *types):
    for t in types:
        if hasattr(bpy.types, t):
            return tree.nodes.new(t)
    raise RuntimeError("no node type " + types[0])


def get_material(name, path, cull, normal=None):
    mname = "EVO " + (name or "untextured") + (" +N" if normal else "")
    mat = bpy.data.materials.get(mname)
    if mat:
        return mat
    mat = bpy.data.materials.new(mname)
    mat.use_nodes = True
    mat.use_backface_culling = cull
    tree = mat.node_tree
    bsdf = next((n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if not bsdf:
        return mat
    bsdf.inputs["Roughness"].default_value = 0.9
    spec = bsdf.inputs.get("Specular IOR Level") or bsdf.inputs.get("Specular")
    if spec:
        spec.default_value = 0.1
    img = load_image(path) if path else None
    if img:
        tex = tree.nodes.new("ShaderNodeTexImage")
        tex.image = img
        tex.location = (-500, 300)
        tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        if img.depth in (32, 64, 128) or img.channels == 4:
            tree.links.new(tex.outputs["Alpha"], bsdf.inputs["Alpha"])
            if hasattr(mat, "surface_render_method"):
                mat.surface_render_method = "DITHERED"
            if hasattr(mat, "blend_method"):
                try:
                    mat.blend_method = "CLIP"
                except TypeError:
                    pass
    else:
        bsdf.inputs["Base Color"].default_value = (0.6, 0.6, 0.6, 1.0)
    nimg = load_image(normal, non_color=True) if normal else None
    if nimg:
        # the game's normal maps are DirectX style (green = down): flip green for Blender
        ntex = tree.nodes.new("ShaderNodeTexImage")
        ntex.image = nimg
        ntex.location = (-900, -250)
        sep = _node(tree, "ShaderNodeSeparateColor", "ShaderNodeSeparateRGB")
        comb = _node(tree, "ShaderNodeCombineColor", "ShaderNodeCombineRGB")
        inv = tree.nodes.new("ShaderNodeMath")
        inv.operation = "SUBTRACT"
        inv.inputs[0].default_value = 1.0
        nmap = tree.nodes.new("ShaderNodeNormalMap")
        sep.location, inv.location, comb.location, nmap.location = (-650, -250), (-450, -300), (-300, -250), (-150, -250)
        tree.links.new(ntex.outputs["Color"], sep.inputs[0])
        tree.links.new(sep.outputs[0], comb.inputs[0])
        tree.links.new(sep.outputs[1], inv.inputs[1])
        tree.links.new(inv.outputs[0], comb.inputs[1])
        tree.links.new(sep.outputs[2], comb.inputs[2])
        tree.links.new(comb.outputs[0], nmap.inputs["Color"])
        tree.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


def surface_material(index):
    """Material for a collision surface (Default.spl row): "Surface: <name>", a colour per surface."""
    name = "Surface: " + bsp.surface_name(index)
    mat = bpy.data.materials.get(name)
    if mat:
        return mat
    mat = bpy.data.materials.new(name)
    h = (index * 0.61803) % 1.0                     # golden-ratio hues: neighbours look different
    col = Color()
    col.hsv = (h, 0.55 if index % 2 else 0.8, 0.9)
    mat.diffuse_color = (col.r, col.g, col.b, 1.0)
    mat.use_nodes = True
    bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf:
        bsdf.inputs["Base Color"].default_value = mat.diffuse_color
    return mat


def surface_of(mat, default):
    """Surface row for a material: "Surface: <name>", a surface name, or `default`. (row, known?)"""
    if mat is None:
        return default, True
    name = base_name(mat.name)
    if name.lower().startswith("surface:"):
        name = name.split(":", 1)[1]
    try:
        return bsp.surface_index(name), True
    except ValueError:
        return default, False


def build_collision_mesh(name, tree_bytes, k):
    """Blender mesh of a collision tree (in its object's space): one face per polygon, materials per surface."""
    polys = bsp.polygons(bsp.read(tree_bytes))
    verts, faces, face_mat, mats = [], [], [], []
    for p in polys:
        mat = surface_material(p.surface)
        if mat not in mats:
            mats.append(mat)
        base = len(verts)
        verts += [conv_point(v, k) for v in p.verts]
        faces.append(tuple(base + i for i in reversed(range(len(p.verts)))))     # the mirror flips the winding
        face_mat.append(mats.index(mat))
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    for mat in mats:
        me.materials.append(mat)
    me.polygons.foreach_set("material_index", face_mat)
    me.validate()
    me.update()
    return me


# ----------------------------------------------------------------------------------------------------- meshes --

def build_mesh(name, meshes, textures, normals, k, opts):
    """One Blender mesh from a node's draw batches (one material slot per texture)."""
    verts, vnormals, uvs, cols, faces, face_mat, mats = [], [], [], [], [], [], []
    for m in meshes:
        base = len(verts)
        tname, tpath = textures.get(id(m), ("", None))
        mat = get_material(tname, tpath, opts["cull"], normals.get(id(m)))
        if mat not in mats:
            mats.append(mat)
        mi = mats.index(mat)
        for p, n in zip(m.positions, m.normals):
            verts.append(conv_point(p, k))
            vnormals.append(conv_dir(n).normalized())
        uvs += m.uvs
        cols += m.colours
        for t in m.triangles:
            faces.append((t[0] + base, t[2] + base, t[1] + base))
            face_mat.append(mi)
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    for mat in mats:
        me.materials.append(mat)
    me.polygons.foreach_set("material_index", face_mat)
    me.polygons.foreach_set("use_smooth", [True] * len(faces))
    uv = me.uv_layers.new(name="UVMap")
    loop_v = [0] * len(me.loops)
    me.loops.foreach_get("vertex_index", loop_v)
    flat = []
    for vi in loop_v:
        u, v = uvs[vi]
        flat += (u, 1.0 - v)
    uv.data.foreach_set("uv", flat)
    if opts["colours"] and any(c != (1.0, 1.0, 1.0, 1.0) for c in cols):
        if hasattr(me, "color_attributes"):
            ca = me.color_attributes.new("Col", "BYTE_COLOR", "POINT")
        else:
            ca = me.vertex_colors.new(name="Col")
        if len(ca.data) == len(cols):
            # the game's colours are display (sRGB) bytes: color_srgb keeps them exact (color would round-trip
            # through linear and lose a step here and there)
            prop = "color_srgb" if len(ca.data) and hasattr(ca.data[0], "color_srgb") else "color"
            ca.data.foreach_set(prop, [x for c in cols for x in c])
    me.validate(clean_customdata=False)
    if hasattr(me, "use_auto_smooth"):
        me.use_auto_smooth = True
    if len(vnormals) == len(me.vertices):
        me.normals_split_custom_set_from_vertices(vnormals)
    me.update()
    return me


# ------------------------------------------------------------------------------------------------- animation --

def _fcurve_container(action, obj):
    """F-curve collection for an action on obj: Blender 4.4+ slotted actions or the older action.fcurves."""
    if hasattr(action, "slots"):
        from bpy_extras import anim_utils
        slot = action.slots.new(id_type="OBJECT", name=obj.name)
        return anim_utils.action_ensure_channelbag_for_slot(action, slot).fcurves, slot
    return action.fcurves, None


# .prb events <-> pose markers. Footsteps (type 1) carry a value whose sign is the foot (+ / -: shown as LEFT / RIGHT -
# which foot is which is not confirmed in game) and whose size is 1 on walks, 25 on runs and jogs; types 2 and 3 sit on
# stance / idle animations (value 0). Names: FS_LEFT, FS_RIGHT, FS_LEFT_25 ..., EVENT_<type>[_<value>].
EVENT_KEY = "evo_events"        # action ID property: {"<marker>@<frame>": seconds} - exact times of imported events
_MARKER = re.compile(r"^(?:FS_(LEFT|RIGHT)|EVENT_(\d+))(?:_(-?\d+(?:\.\d+)?))?$")


def event_marker_name(etype, value):
    def num(v):
        return ("%g" % v).replace("+", "")
    if etype == 1 and value:
        side = "LEFT" if value > 0 else "RIGHT"
        return "FS_" + side + ("" if abs(value) == 1 else "_" + num(abs(value)))
    return "EVENT_%d" % etype + ("_" + num(value) if value else "")


def parse_event_marker(name):
    """(type, value) for a marker name, or None for markers that aren't events."""
    m = _MARKER.match(base_name(name.strip().upper()))
    if not m:
        return None
    side, etype, value = m.groups()
    if side:
        v = float(value) if value else 1.0
        return 1, (v if side == "LEFT" else -v)
    return int(etype), float(value) if value else 0.0


def add_event_markers(action, events, anim_fps, scene_fps):
    """Pose markers for a .prb animation's events at the frames its keys were baked to (1 + time * scene / anim fps)."""
    exact = {}
    for etype, etime, value in events:
        name = event_marker_name(etype, value)
        frame = int(round(1.0 + etime * scene_fps / anim_fps))
        action.pose_markers.new(name).frame = frame
        exact["%s@%d" % (name, frame)] = etime / anim_fps
    if exact:
        action[EVENT_KEY] = exact


def marker_events(action, start, scene_fps, keys_fps):
    """.prb events [(type, time in key frames, value)] from an action's pose markers (imported ones keep their exact
    time while they stay on their frame), and the names of markers that aren't events."""
    exact = action.get(EVENT_KEY, {})
    events, unknown = [], []
    for m in action.pose_markers:
        ev = parse_event_marker(m.name)
        if ev is None:
            unknown.append(m.name)
            continue
        seconds = exact.get("%s@%d" % (base_name(m.name.strip().upper()), m.frame))
        if seconds is None:
            seconds = (m.frame - start) / scene_fps
        events.append((ev[0], max(0.0, seconds * keys_fps), ev[1]))
    return events, unknown


def apply_animation(arm, scene, anim, k, assign=True):
    """Bake a .prb animation into an action on an armature built by the importer (bones named after the
    nodes, rest pose = the model's bind pose). Returns the action."""
    bones = arm.data.bones
    idx = [i for i in sorted(scene.bone_ids) if scene.objects[i].name in bones]
    rest = {i: bones[scene.objects[i].name].matrix_local.copy() for i in idx}
    corr = {i: conv_matrix(scene.objects[i].world, k).inverted() @ rest[i] for i in idx}
    fps = bpy.context.scene.render.fps / (bpy.context.scene.render.fps_base or 1.0)
    frames = [1.0 + f * fps / anim.fps for f in range(anim.frames)]
    data = {i: ([], [], []) for i in idx}
    for f in range(anim.frames):
        worlds = evo.anim_worlds(scene, anim, f)
        cur = {i: conv_matrix(worlds[i], k) @ corr[i] for i in idx}
        for i in idx:
            p = scene.objects[i].parent
            if p in cur:
                basis = rest[i].inverted() @ rest[p] @ cur[p].inverted() @ cur[i]
            else:
                basis = rest[i].inverted() @ cur[i]
            loc, rot, sca = basis.decompose()
            prev = data[i][1][-1] if data[i][1] else None
            if prev is not None and rot.dot(prev) < 0:
                rot.negate()
            data[i][0].append(loc)
            data[i][1].append(rot)
            data[i][2].append(sca)
    name = anim.name if not anim.name.startswith("_") else "anim" + anim.name
    action = bpy.data.actions.new(name)
    action.use_fake_user = True
    action["evo_fps"] = anim.fps
    if arm.animation_data is None:
        arm.animation_data_create()
    fcurves, slot = _fcurve_container(action, arm)
    for i in idx:
        bname = scene.objects[i].name
        arm.pose.bones[bname].rotation_mode = "QUATERNION"
        for prop, values, width in (("location", data[i][0], 3), ("rotation_quaternion", data[i][1], 4),
                                    ("scale", data[i][2], 3)):
            for c in range(width):
                fc = fcurves.new('pose.bones["%s"].%s' % (bname.replace('"', '\\"'), prop), index=c,
                                 action_group=bname) if slot is None else \
                    fcurves.new('pose.bones["%s"].%s' % (bname.replace('"', '\\"'), prop), index=c)
                fc.keyframe_points.add(len(frames))
                co = []
                for fr, v in zip(frames, values):
                    co += (fr, v[c])
                fc.keyframe_points.foreach_set("co", co)
                fc.keyframe_points.foreach_set("interpolation", [1] * len(frames))   # LINEAR
                fc.update()
    try:
        action.frame_range = (frames[0], max(frames[-1], frames[0] + 1))
        action.use_frame_range = True
    except (AttributeError, TypeError):
        pass
    add_event_markers(action, getattr(anim, "events", ()), anim.fps, fps)
    if assign:
        arm.animation_data.action = action
        if slot is not None:
            arm.animation_data.action_slot = slot
    return action


# -------------------------------------------------------------------------------------------------- importer --

class EvoImporter:
    def __init__(self, context, path, opts):
        self.context = context
        self.path = path
        self.o = opts
        self.k = opts["scale"]
        self.missing = set()
        self.notes = []

    def normal_maps(self, scene, textures):
        out = {}
        if not self.o["normal_maps"]:
            return out
        for so in scene.objects:
            for m in so.node.meshes:
                tname = textures.get(id(m), ("", None))[0]
                nm = evo.normal_map_for(self.rfx, scene.name, tname, self.finder)
                path = self.finder.find(nm, (".png", ".tga", ".dds")) if nm else None
                if path:
                    out[id(m)] = path
        return out

    def run(self):
        scene = evo.load(self.path)
        self.scene = scene
        # a level import passes one finder (and its .RFX list) for all its models: no folder scan per model
        self.finder = self.o.get("finder") or evo.AssetFinder(self.path, [self.o["texture_dir"]] if self.o["texture_dir"] else [])
        if "rfx" in self.o:
            self.rfx = self.o["rfx"]
        else:
            self.rfx = evo.read_rfx(self.finder) if self.o["normal_maps"] else None
        coll = bpy.data.collections.new(os.path.splitext(os.path.basename(self.path))[0])
        self.context.collection.children.link(coll)
        coll["evo_source"] = self.path
        coll["evo_scale"] = self.k
        self.helpers = None
        if self.o["helpers"]:
            self.helpers = bpy.data.collections.new(HELPER_COLLECTION)
            coll.children.link(self.helpers)
            self.helpers.hide_render = True
        arm = None
        if scene.skinned and self.o["armature"]:
            arm = self.build_armature(scene, coll)
        objs = self.build_objects(scene, coll, arm, self.o["skin"])
        if self.o["vehicle_parts"] and not scene.skinned:
            self.attach_parts(scene, coll, objs)
        if arm and self.o["animations"] != "NONE":
            self.import_animations(scene, arm)
        return coll

    def build_objects(self, scene, coll, arm=None, skin="", place=None, mesh_cache=None):
        """Objects for a scene; place = game-space matrix applied to the whole scene (vehicle parts)."""
        k = self.k
        textures, _ = evo.resolve_textures(scene, self.finder, skin)
        self.missing |= {n for n, p in textures.values() if n and not p}
        normals = self.normal_maps(scene, textures)
        objs = {}
        for so in scene.objects:
            role = so.role
            if role == "collision":
                continue        # the #BSP_ helper mesh carries no surfaces: its owner's tree is imported instead
            if role in ("shadow", "connector", "hitbox") and not self.o["helpers"]:
                continue
            if role == "bone" and arm and not self.o["helpers"]:
                continue
            target = self.helpers if so.is_helper and self.helpers else coll
            if role == "skin":
                ob = bpy.data.objects.new(so.name, build_mesh(so.name, so.node.meshes, textures, normals, k, self.o))
                coll.objects.link(ob)
                if arm:
                    self.skin(ob, so, scene, arm)
                objs[so.index] = ob
                continue
            if so.node.meshes and role != "hitbox":
                key = so.index
                me = mesh_cache.get(key) if mesh_cache is not None else None
                if me is None:
                    me = build_mesh(so.name, so.node.meshes, textures, normals, k, self.o)
                    if mesh_cache is not None:
                        mesh_cache[key] = me
                ob = bpy.data.objects.new(so.name, me)
                target.objects.link(ob)
                if so.node.meshes[0].frames and self.o["frames"] and me.shape_keys is None:
                    self.shape_keys(ob, so.node.meshes)
            else:
                ob = bpy.data.objects.new(so.name, None)
                target.objects.link(ob)
                ob.empty_display_type = "ARROWS" if role == "connector" else "PLAIN_AXES"
                ob.empty_display_size = 10 * k
            if role in ("shadow", "bone"):
                ob.display_type = "WIRE"
            if place is None:
                ob["evo_node"] = so.index           # the model's own objects (not attached parts): vehicle rig
            objs[so.index] = ob
        self.context.view_layer.update()
        collision = []
        if self.o["helpers"] and self.helpers:
            for so in scene.objects:
                tree = next((t for u, t in so.node.trees if u == 0 and t), None)
                if tree is None or so.index not in objs:
                    continue
                src = so.node.collision_source
                cname = src if src.upper().startswith("#BSP_") else "#BSP_" + (so.node.name or scene.name)
                cob = bpy.data.objects.new(cname, build_collision_mesh(cname, tree, k))
                self.helpers.objects.link(cob)
                cob.display_type = "SOLID" if getattr(bpy.context.scene, sp("show_surfaces"), False) else "WIRE"
                cob["evo_role"] = "collision"
                cob["evo_collision_of"] = so.node.name
                cob["evo_object_index"] = so.index
                collision.append((cob, objs[so.index]))
            for so in scene.objects:
                if so.index not in objs:
                    continue
                for role, i in enumerate(so.node.point_roles):
                    if not 0 <= i < len(so.node.points):
                        continue
                    pob = bpy.data.objects.new("#PT_" + evo.POINT_ROLES[role], None)
                    self.helpers.objects.link(pob)
                    pob.empty_display_type = "SINGLE_ARROW" if role in (0, 1) else "SPHERE"
                    pob.empty_display_size = 3 * k
                    pob["evo_point_role"] = evo.POINT_ROLES[role]
                    pob["evo_point_of"] = so.node.name
                    pob["evo_object_index"] = so.index
                    collision.append((pob, objs[so.index], so.node.points[i]))
        for so in scene.objects:
            ob = objs.get(so.index)
            if ob is None or so.role == "skin":
                continue
            if arm and so.index in scene.bone_ids:
                ob.parent, ob.parent_type, ob.parent_bone = arm, "BONE", so.name
            elif arm and so.parent is not None and so.parent in scene.bone_ids:
                ob.parent, ob.parent_type, ob.parent_bone = arm, "BONE", scene.objects[so.parent].name
            elif so.parent is not None and so.parent in objs:
                ob.parent = objs[so.parent]
                ob.matrix_basis = conv_matrix(so.local, k)
                continue
            self.context.view_layer.update()
            world = so.world if place is None else evo.mat_mul(place, so.world)
            ob.matrix_world = conv_matrix(world, k)
        for cob, owner, *point in collision:    # trees and points are in their owner's space
            cob.parent = owner
            cob.matrix_parent_inverse = Matrix.Identity(4)
            cob.matrix_basis = Matrix.Translation(conv_point(point[0], k)) if point else Matrix.Identity(4)
        return objs

    def attach_parts(self, scene, coll, objs):
        parts = evo.vehicle_parts(scene, self.finder)
        if not parts:
            return
        pcoll = bpy.data.collections.new("parts")
        coll.children.link(pcoll)
        caches = {}
        roots = [o for o in scene.objects if o.parent is None and o.index in objs]
        vehicle_root = objs[roots[0].index] if roots else None
        for name, conn, pos, kind in parts:
            path = self.finder.find(name, (".evo",))
            if not path:
                self.notes.append("part %s not found" % name)
                continue
            try:
                pscene = evo.load(path)
            except evo.EvoError as e:
                self.notes.append("part %s: %s" % (name, e))
                continue
            if conn is not None:
                place = scene.objects[conn].world
            elif pos is not None:
                place = translation(pos)
            else:
                place = evo.mat_identity()
            pobjs = self.build_objects(pscene, pcoll, place=place, mesh_cache=caches.setdefault(name.upper(), {}))
            # keep the part with the vehicle piece that carries it (turret for guns, hull for wheels/tracks)
            holder = None
            j = conn
            while j is not None and holder is None:
                holder = objs.get(j)
                j = scene.objects[j].parent
            holder = holder or vehicle_root
            if holder is None:
                continue
            self.context.view_layer.update()
            for so in pscene.objects:
                ob = pobjs.get(so.index)
                if ob is not None and ob.parent is None:
                    mw = ob.matrix_world.copy()
                    ob.parent = holder
                    ob.matrix_world = mw
                    if conn is not None:
                        ob["evo_part_conn"] = conn  # the vehicle connector it hangs on (vehicle rig)

    def import_animations(self, scene, arm):
        anims = self.finder.animations()
        first = None
        count = 0
        wm = self.context.window_manager
        wm.progress_begin(0, max(1, len(anims)))
        for n, (name, path) in enumerate(sorted(anims.items())):
            wm.progress_update(n)
            try:
                anim = evo.read_prb(path, name)
            except (evo.EvoError, OSError):
                continue
            if not evo.compatible(scene, anim):
                continue
            act = apply_animation(arm, scene, anim, self.k, assign=first is None)
            first = first or act
            count += 1
        wm.progress_end()
        self.notes.append("%d animations" % count)

    def shape_keys(self, ob, meshes):
        ob.shape_key_add(name="Basis", from_mix=False)
        for f in range(len(meshes[0].frames)):
            sk = ob.shape_key_add(name="Frame %d" % (f + 1), from_mix=False)
            sk.value = 0.0              # Blender 5 creates new keys at 1.0
            co = []
            for m in meshes:
                frame = m.frames[f][0] if f < len(m.frames) else m.positions
                for p in frame:
                    co.extend(conv_point(p, self.k))
            if len(co) == 3 * len(sk.data):
                sk.data.foreach_set("co", co)

    def build_armature(self, scene, coll):
        k = self.k
        data = bpy.data.armatures.new(scene.name + "_rig")
        arm = bpy.data.objects.new(scene.name + "_rig", data)
        coll.objects.link(arm)
        arm["evo_path"] = os.path.abspath(self.path)
        arm["evo_scale"] = k
        data.display_type = "STICK"
        arm.show_in_front = True
        view_layer = self.context.view_layer
        prev = view_layer.objects.active
        view_layer.objects.active = arm
        bpy.ops.object.mode_set(mode="EDIT")
        ebones = {}
        ids = sorted(scene.bone_ids)
        for i in ids:
            so = scene.objects[i]
            w = conv_matrix(so.world, k)
            head = w.translation
            # Character Studio bones point along their local X; length = distance to the child joint
            axis = Vector((w[0][0], w[1][0], w[2][0])).normalized()
            length = 0.0
            for c in so.children:
                if c in scene.bone_ids:
                    d = conv_matrix(scene.objects[c].world, k).translation - head
                    if d.length > 1e-6 and d.dot(axis) > length and d.normalized().dot(axis) > 0.7:
                        length = d.dot(axis)
            if length < 1e-4:
                length = 6 * k
            eb = data.edit_bones.new(so.name)
            eb.head = head
            eb.tail = head + axis * length
            eb.align_roll(Vector((w[0][2], w[1][2], w[2][2])))
            ebones[i] = eb
        for i in ids:
            p = scene.objects[i].parent
            if p in ebones:
                ebones[i].parent = ebones[p]
        bpy.ops.object.mode_set(mode="OBJECT")
        view_layer.objects.active = prev
        return arm

    def skin(self, ob, so, scene, arm):
        groups = {}
        base = 0
        for m in so.node.meshes:
            for vi, vw in enumerate(scene.vertex_weights(so, m)):
                for b, w in vw:
                    name = scene.objects[b].name
                    if name not in groups:
                        groups[name] = ob.vertex_groups.new(name=name)
                    groups[name].add([base + vi], w, "REPLACE")
            base += len(m.positions)
        mod = ob.modifiers.new("Armature", "ARMATURE")
        mod.object = arm
        ob.parent = arm


# ------------------------------------------------------------------------------------------------- operators --

# ------------------------------------------------------------------------------------------ game profiles --
# File > Import / Export have one entry per game (Conflict: Desert Storm (.evo), Conflict: Desert Storm II (.evo) ...);
# the entry passes game= to the operator, which makes it the current game: imports start in its extracted folder,
# exports use its game folder and active mod, and a model or animation of the other game is refused on export. The
# sidebar's Export to Mod / Test in Game use the game of the last import / export. Each game keeps its own folders and
# mod (add-on preferences, so every .blend shares them).
PROFILES = (("ds1", "Desert Storm", "Conflict: Desert Storm (2002)"),
            ("ge", "Great Escape", "The Great Escape (2003)"),
            ("ds2", "Desert Storm II", "Conflict: Desert Storm II (2003)"),
            ("cv", "Vietnam", "Conflict: Vietnam (2004)"),
            ("cg", "Global Storm", "Conflict: Global Storm (2005)"),
            ("do", "Denied Ops", "Conflict: Denied Ops (2008)"))
MENU_NAMES = {"ds1": "Conflict: Desert Storm", "ge": "The Great Escape", "ds2": "Conflict: Desert Storm II",
              "cv": "Conflict: Vietnam", "cg": "Conflict: Global Storm",
              "do": "Conflict: Denied Ops"}                                         # File > Import / Export entries
MODEL_GAMES = {0x2F: "ds1", 0x35: "ds2", 0x34: "ge", 0x40: "cv", 0x49: "cg", 0x54: "do"}   # EOBJ version -> game
# Denied Ops (its own add-on only): models (the EOBJ 0x54 streams apkg.py unpacks from the level archives' packages,
# textured through their materials), characters on C5_SKELETON, level scenes and archives import; nothing is written
# yet (models go back inside APKG packages - not built), so it has no export entries and no export tools.
# what each game gets: models / scenes / archives import for all five; writing models (evo_write's layouts) and
# animations (.prb written: DS1 by object order, DS2 / The Great Escape by name hash) only for these. Vietnam's .prb
# (keys relative to rest) and Global Storm's .ani (the unnamed STATE_* / OVERLAY_* .BIN files, posed through the
# skeleton's .SKL) import.
WRITE_GAMES = ("ds1", "ge", "ds2", "cv", "cg")   # Vietnam / Global Storm: static models, collision, points so far
ANIM_IMPORT_GAMES = ("ds1", "ge", "ds2", "cv", "cg")
MOD_GAMES = ("ds1", "ge", "ds2", "cv", "cg")   # a plugin serves <game>\Mods by file name (all but Denied Ops)
STARTER_GAMES = ("ds1", "ge", "ds2")       # plugins that read .weapon files (.skin: DS1 - starter_file's HEROES)
TEST_GAMES = MOD_GAMES                     # Test in Game: DS1 +level, the others start at the launcher / front end
ANIM_GAMES = ("ds1", "ge", "ds2", "cv", "cg")
# a game add-on: its own game only; the all-games add-on: the five it always had (Denied Ops has its own add-on)
MENU_PROFILES = tuple(p for p in PROFILES if (p[0] == GAME if GAME else p[0] != "do"))
SCENE_WRITE_GAMES = ("ds1", "ge", "ds2", "cv", "cg")   # Scene (.env) export


def prefs():
    """The add-on's preferences, or None (not registered, e.g. the command-line tools)."""
    try:
        return bpy.context.preferences.addons[__package__].preferences
    except (KeyError, AttributeError):
        return None


def profile():
    """The game picked in the profile (a game add-on: always its game); also made games.current() (archive name
    hashing, folder lookups)."""
    p = prefs()
    game = games.GAMES[GAME] if GAME else games.GAMES[p.game] if p else games.current()
    if games.current() is not game:
        games.set_current(game)
    return game


def profile_value(field, game=None):
    p = prefs()
    return getattr(p, "%s_%s" % ((game or profile()).id, field)) if p else ""


def extract_dir(game=None):
    """The game's extraction folder: its profile's, else Documents\\Conflict <game> extracted."""
    game = game or profile()
    d = profile_value("extract_dir", game)
    if d:
        return bpy.path.abspath(d)
    return DEFAULT_EXTRACT_DIR if game.id == "ds1" else os.path.join(
        os.path.expanduser("~"), "Documents", "Conflict %s extracted" % game.short)


def model_game(path):
    """The game an .evo is from (by its EOBJ version), or None if it can't be read."""
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except OSError:
        return None
    if len(head) < 8 or head[:4] != b"EOBJ":
        return None
    return games.GAMES.get(MODEL_GAMES.get(int.from_bytes(head[4:8], "little") & 0xFFFF))


def wrong_game(game, what):
    """Message for a model / animation that isn't the current game's ("" when it is)."""
    if game is None or game is profile():
        return ""
    in_menus = any(p[0] == game.id for p in MENU_PROFILES)       # Denied Ops isn't in the all-games add-on's menus
    where = ("File > Import / Export > %s" % MENU_NAMES.get(game.id, game.title)) if in_menus else \
        ("the %s add-on" % game.title)
    return "%s is a %s %s, not a %s one - use %s, or base it on a %s %s" % (
        what[0], game.short, what[1], profile().short, where, profile().short, what[1])


def _profile_update(self, context):
    profile()


def _profile_get(field):
    return lambda self: profile_value(field)


def _profile_set(field):
    def set_(self, value):
        p = prefs()
        if p:
            setattr(p, "%s_%s" % (profile().id, field), value)
    return set_


def _prefs_props():
    out = {"game": EnumProperty(name="Game", items=[(g, short, title) for g, short, title in PROFILES],
                                default=GAME or "ds1", update=_profile_update,
                                description="The game of the last import / export (the sidebar's Export to Mod "
                                            "and Test in Game use it)"),
           "mod_layout": EnumProperty(name="New Mods", default=modlayout.ORGANISED,
                                      items=((modlayout.ORGANISED, "Organised",
                                              "Files in folders by type: evos, dds, anims, sounds, tables, defs, "
                                              "levels"),
                                             (modlayout.FLAT, "Flat", "Every file in the mod's top folder (the "
                                                                      "layout of mods made before 1.9)")),
                                      description="How a new mod's files are laid out. A mod that already has files "
                                                  "keeps its layout, and a file it has is rewritten where it is - "
                                                  "the games find a mod's files in any folder")}
    for gid, short, _ in MENU_PROFILES:
        out[gid + "_game_dir"] = StringProperty(name="Game Folder", default="", subtype="DIR_PATH",
                                                description="%s's folder (with its exe); empty = found from the "
                                                            "registry / Steam" % short)
        out[gid + "_extract_dir"] = StringProperty(name="Extracted Folder", default="", subtype="DIR_PATH",
                                                   description="Where %s's archives are extracted (empty = "
                                                               "Documents\\Conflict ... extracted)" % short)
        out[gid + "_active_mod"] = StringProperty(name="Active Mod", default="",
                                                  description="%s's mod folder under <game>\\Mods" % short)
    return out


def mod_layout():
    """The preference New Mods (organised unless set to flat; organised without preferences)."""
    p = prefs()
    return getattr(p, "mod_layout", modlayout.ORGANISED) if p else modlayout.ORGANISED


def mod_path(mod_dir, name):
    """Where a file of a mod is written (conflict_ds_tools.modlayout: existing file in place, flat mods stay flat)."""
    return modlayout.path_for(mod_dir, name, layout=mod_layout())


def _prefs_draw(self, context):
    lay = self.layout
    lay.prop(self, "mod_layout")
    for gid, short, _ in MENU_PROFILES:
        box = lay.box()
        box.label(text=short)
        for f in ("game_dir", "extract_dir", "active_mod"):
            box.prop(self, "%s_%s" % (gid, f))


DS_PT_preferences = type("DS_PT_preferences" + ("_" + PREFIX if PREFIX else ""), (bpy.types.AddonPreferences,),
                         {"bl_idname": __package__, "__annotations__": _prefs_props(), "draw": _prefs_draw})


def game_prop():
    """The game an import / export is for: set by its File menu entry (one per game), not shown in the dialog."""
    return EnumProperty(name="Game", items=[(g, short, title) for g, short, title in PROFILES], default=GAME or "ds1",
                        options={"HIDDEN", "SKIP_SAVE"},
                        description="The game this import / export is for (File > Import / Export has an entry per "
                                    "game); not set = the game of the last import / export")


def apply_game(op):
    """The game the operator was started for (its menu entry's / a script's game="ds2") becomes the current one."""
    p = prefs()
    if op.properties.is_property_set("game"):
        if p and p.game != op.game:
            p.game = op.game
        elif p is None:
            games.set_current(op.game)          # no add-on preferences (registered from a script): the game alone
    profile()


class IMPORT_SCENE_OT_evo(bpy.types.Operator, ImportHelper):
    """Import Conflict: Desert Storm .EVO meshes"""
    bl_idname = "import_scene.evo"
    bl_label = "Import EVO"
    bl_options = {"REGISTER", "UNDO", "PRESET"}

    filename_ext = ".evo"
    filter_glob: StringProperty(default="*.evo;*.EVO", options={"HIDDEN"})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement, options={"HIDDEN", "SKIP_SAVE"})
    directory: StringProperty(subtype="DIR_PATH", options={"HIDDEN", "SKIP_SAVE"})

    game: game_prop()
    scale: FloatProperty(name="Scale", default=0.01, min=1e-5, max=100.0,
                         description="Game units are centimetres; 0.01 imports in metres")
    armature: BoolProperty(name="Armature", default=True,
                           description="Characters: build the biped skeleton and bind the skin to it")
    animations: EnumProperty(name="Animations", default="NONE",
                             items=(("NONE", "None", "Bind pose only (import animations later with "
                                                     "File > Import > Conflict: Desert Storm Animation (.prb))"),
                                    ("ALL", "All Compatible", "Every .prb animation found next to the model and "
                                                              "in its sibling folders that fits the skeleton, "
                                                              "one action each")),
                             description="Characters: animations to import as actions")
    vehicle_parts: BoolProperty(name="Vehicle Parts", default=True,
                                description="Vehicles: attach the tracks, wheels and guns the game adds at run "
                                            "time (from <MODEL>.DYN and the catalog tables)")
    normal_maps: BoolProperty(name="Normal Maps", default=True,
                              description="Add the normal maps named in the game's .RFX effect lists")
    helpers: BoolProperty(name="Helpers", default=False,
                          description="Also import connectors (attachment points), the collision (#BSP_, one "
                                      "material per surface), shadow meshes (#SHA_), hit regions and bone proxy "
                                      "boxes into a 'helpers' collection")
    frames: BoolProperty(name="Cloth Frames as Shape Keys", default=True,
                         description="Flags and other cloth: one shape key per animation frame")
    colours: BoolProperty(name="Vertex Colours", default=True,
                          description="Import vertex colours (baked lighting on world pieces)")
    cull: BoolProperty(name="Backface Culling", default=True,
                       description="Enable backface culling on the materials like the game")
    skin: StringProperty(name="Character Skin", default="",
                         description="Texture for characters' run-time skin, e.g. HERO01_US_01 "
                                     "(empty = HERO0n_UK_01, then _US_01 ...)")
    texture_dir: StringProperty(name="Extra Folder", default="", subtype="DIR_PATH",
                                description="Extra folder to search for textures and parts (the model's folder "
                                            "and its sibling folders are always searched)")

    OPTIONS = ("scale", "armature", "animations", "vehicle_parts", "normal_maps", "helpers", "frames", "colours",
               "cull", "skin")

    def invoke(self, context, event):
        apply_game(self)
        start = extract_dir()                               # the profile's game (sidebar > Desert Storm > Game)
        if os.path.isdir(start) and not (self.filepath and os.path.isdir(os.path.dirname(self.filepath))
                                         and games.detect_extracted(self.extracted_root(self.filepath)) is profile()):
            self.filepath = start + os.sep
        return ImportHelper.invoke(self, context, event)

    @staticmethod
    def extracted_root(path):
        return os.path.dirname(os.path.dirname(os.path.abspath(path)))

    def execute(self, context):
        apply_game(self)
        opts = {n: getattr(self, n) for n in self.OPTIONS}
        opts["texture_dir"] = bpy.path.abspath(self.texture_dir) if self.texture_dir else ""
        paths = [os.path.join(self.directory, f.name) for f in self.files if f.name] or [self.filepath]
        if context.object and context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        done = 0
        for path in paths:
            imp = EvoImporter(context, path, opts)
            try:
                imp.run()
            except (evo.EvoError, OSError) as e:
                self.report({"ERROR"}, "%s: %s" % (os.path.basename(path), e))
                continue
            done += 1
            msg = list(imp.notes)
            wrong = wrong_game(model_game(path), (os.path.basename(path), "model"))
            if wrong:
                msg.append(wrong.replace(" - use File > Import / Export >", " - export it with File > Export >"))
            if imp.missing:
                msg.append("textures not found: " + ", ".join(sorted(imp.missing)))
            if msg:
                self.report({"WARNING"} if imp.missing else {"INFO"}, "%s: %s" % (os.path.basename(path),
                                                                                  "; ".join(msg)))
        return {"FINISHED"} if done else {"CANCELLED"}

    def draw(self, context):
        lay = self.layout
        lay.use_property_split = True
        for p in self.OPTIONS + ("texture_dir",):
            lay.prop(self, p)


# an animation of another game is posed through that game's skeleton (found next to the animation file) and
# retargeted onto the armature by bone name (evo.retarget) - the games' Biped rigs share their bone names
ANIM_SKELETONS = {"ds1": "HERO01_ARMSTRONG", "ds2": "534_POLY_SOLDIER", "ge": "GEN_GER_SOLDIER",
                  "cv": "MPD_US_MOTOWN", "cg": "C04_500_POLY"}


def anim_game(anim):
    """The game an animation's file layout belongs to (Animation.game / raw key size)."""
    if anim.game == "ds2":
        return games.GAMES["ge"] if anim.raw.get("key") == 40 else games.GAMES["ds2"]
    return games.GAMES.get(anim.game)


def cross_game_animation(path, anim, scene, scene_game):
    """(retargeted Animation, note) when `anim` (read from `path`) is another game's than `scene_game` and that game's
    skeleton is found next to the file; (None, why) when it should be but can't; (anim, None) when it's the same
    game's."""
    agame = anim_game(anim)
    if agame is None or scene_game is None or agame is scene_game:
        return anim, None
    name = ANIM_SKELETONS.get(agame.id)
    sk = evo.AssetFinder(path).find(name, (".evo",)) if name else None
    if not sk:
        return None, "%s is a %s animation - its skeleton (%s) isn't in the extracted folders next to it" % (
            anim.name, agame.short, name)
    try:
        source = evo.load(sk)
    except (evo.EvoError, OSError) as e:
        return None, "%s: can't read %s's skeleton %s (%s)" % (anim.name, agame.short, os.path.basename(sk), e)
    if not evo.compatible(source, anim):
        return None, "%s doesn't fit %s's soldier skeleton" % (anim.name, agame.short)
    out = evo.retarget(anim, source, scene)
    if out is None:
        return None, "%s: no bone of %s's skeleton matches this armature" % (anim.name, agame.short)
    return out, "%s retargeted from %s" % (anim.name, agame.short)


class IMPORT_ANIM_OT_prb(bpy.types.Operator, ImportHelper):
    """Import Conflict: Desert Storm .prb animations onto the active character armature"""
    bl_idname = "import_anim.evo_prb"
    bl_label = "Import PRB Animation"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".prb"
    # Global Storm's are .ani (<NAME>.ANI from the extraction / a mod, _HASH.BIN from an older extraction)
    filter_glob: StringProperty(default="*.prb;*.PRB;*.ani;*.ANI;_*.BIN;_*.bin", options={"HIDDEN"})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement, options={"HIDDEN", "SKIP_SAVE"})
    directory: StringProperty(subtype="DIR_PATH", options={"HIDDEN", "SKIP_SAVE"})

    game: game_prop()

    def invoke(self, context, event):
        apply_game(self)
        if self.game == "cg":
            self.filter_glob = "*.ani;*.ANI;_*.BIN;_*.bin"
        return ImportHelper.invoke(self, context, event)

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob is not None and ob.type == "ARMATURE" and "evo_path" in ob

    def execute(self, context):
        apply_game(self)
        arm = context.active_object
        try:
            scene = evo.load(arm["evo_path"])
        except (evo.EvoError, OSError) as e:
            self.report({"ERROR"}, "can't read the armature's model %s: %s" % (arm["evo_path"], e))
            return {"CANCELLED"}
        k = arm.get("evo_scale", 0.01)
        paths = [os.path.join(self.directory, f.name) for f in self.files if f.name] or [self.filepath]
        done, skipped, retargeted = 0, [], []
        for path in paths:
            try:
                anim = evo.read_prb(path)
            except (evo.EvoError, OSError) as e:
                skipped.append("%s (%s)" % (os.path.basename(path), e))
                continue
            anim, why = cross_game_animation(path, anim, scene, model_game(arm["evo_path"]))
            if anim is None:
                skipped.append(why)
                continue
            if why:
                retargeted.append(why)
            if not evo.compatible(scene, anim):
                skipped.append("%s (%d tracks, the model has %d objects)" % (anim.name, len(anim.tracks),
                                                                              len(scene.objects)))
                continue
            apply_animation(arm, scene, anim, k, assign=True)
            done += 1
        if retargeted:
            self.report({"INFO"}, "; ".join(retargeted))
        if skipped:
            self.report({"WARNING"}, "skipped: " + ", ".join(skipped))
        return {"FINISHED"} if done else {"CANCELLED"}


class IMPORT_SCENE_OT_ds_extract(bpy.types.Operator):
    """Extract the Conflict: Desert Storm .dat archives (meshes, textures, animations, tables) to a folder"""
    bl_idname = "import_scene.ds_extract"
    bl_label = "Extract Game Archives"

    game: game_prop()
    game_dir: StringProperty(name="Game Folder", subtype="DIR_PATH",
                             description="Folder with the game's exe and the .dat archives")
    out_dir: StringProperty(name="Output Folder", subtype="DIR_PATH",
                            description="Where to write one folder per archive")
    png: BoolProperty(name="PNG Copies", default=False,
                      description="Also write PNG copies of the DDS/TGA textures (Blender reads DDS/TGA itself; "
                                  "PNG is handy for other programs)")
    editable: BoolProperty(name="Editable Models (.blend)", default=False,
                           description="Afterwards save every model as its own .blend in <output>\\_blend\\<archive>: "
                                       "textured, with its collision, shadow mesh and points as helpers, ready to "
                                       "edit and export back (Based On is filled in). Takes a while - Esc stops it")
    level_pieces: BoolProperty(name="Include Level Pieces", default=False,
                               description="Also the level pieces (streets, cliffs, buildings placed as part of a "
                                           "level): thousands of large models")

    def invoke(self, context, event):
        apply_game(self)
        self.game_dir = game_dir_of(context.scene)        # the profile's game
        self.out_dir = extract_dir()
        return context.window_manager.invoke_props_dialog(self, width=520)

    def draw(self, context):
        lay = self.layout
        lay.use_property_split = True
        for p in ("game_dir", "out_dir", "png", "editable"):
            lay.prop(self, p)
        row = lay.row()
        row.enabled = self.editable
        row.prop(self, "level_pieces")

    @classmethod
    def poll(cls, context):
        return _job.get("thread") is None or not _job["thread"].is_alive()

    def execute(self, context):
        apply_game(self)
        game = bpy.path.abspath(self.game_dir)
        out = bpy.path.abspath(self.out_dir)
        if not os.path.isdir(game) or not archive.list_archives(game):
            self.report({"ERROR"}, "no .dat archives in " + game)
            return {"CANCELLED"}
        # the extraction runs in a worker thread (pure Python file work, no Blender calls); this modal operator
        # only polls it on a timer and draws the progress bar in the status bar, so Blender stays usable
        _job.clear()
        _job.update(fraction=0.0, text="Starting", cancel=False, error=None, summary=None, out=out,
                    start=time.time())

        def work():
            try:
                _job["summary"] = archive.extract(
                    game, out, None, self.png,
                    progress=lambda f, text: _job.update(fraction=f, text=text),
                    log=lambda s: None, cancel=lambda: _job["cancel"])
            except archive.Cancelled:
                _job["error"] = "cancelled"
            except Exception as e:      # reported back on the main thread
                _job["error"] = "%s: %s" % (type(e).__name__, e)

        _job["thread"] = threading.Thread(target=work, name="ds_extract", daemon=True)
        _job["thread"].start()
        _status_job[0] = _job
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.1, window=context.window)
        wm.modal_handler_add(self)
        context.workspace.status_text_set(_draw_status)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type == "ESC" and event.value == "PRESS":
            _job["cancel"] = True
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        context.workspace.status_text_set(_draw_status)     # redraws the status bar
        if _job["thread"].is_alive():
            return {"PASS_THROUGH"}
        if self.editable and not _job["error"]:
            if "models" not in _job:
                _job["models"] = editable_models(_job["out"], self.level_pieces)
                _job.update(done=0, saved=0, failed=[], phase="models", fraction=0.0, text="",
                            classify=Classifier(_job["out"]))
            if not _job["cancel"] and _job["done"] < len(_job["models"]):
                save_editable_batch(context, _job, budget=0.25)
                return {"PASS_THROUGH"}
        context.window_manager.event_timer_remove(self._timer)
        context.workspace.status_text_set(None)
        secs = time.time() - _job["start"]
        if _job["error"] == "cancelled":
            self.report({"WARNING"}, "Extraction cancelled after %.0f s (files so far are in %s)" % (secs, _job["out"]))
            return {"CANCELLED"}
        if _job["error"]:
            self.report({"ERROR"}, "Extraction failed: " + _job["error"])
            return {"CANCELLED"}
        summary = _job["summary"] or []
        named = sum(s[1] for s in summary)
        total = named + sum(s[2] for s in summary)
        msg = "Extracted %d files (%d named) in %.0f s to %s" % (total, named, secs, _job["out"])
        if "models" in _job:
            bpy.data.orphans_purge(do_recursive=True)
            finish_library(_job)
            msg += "; %d editable models in %s (Asset Browser catalogs)" % (_job["saved"], os.path.join(_job["out"], "_blend"))
            if _job["cancel"]:
                msg += " (stopped after %d of %d)" % (_job["done"], len(_job["models"]))
            if _job["failed"]:
                msg += "; %d could not be converted: %s" % (len(_job["failed"]), ", ".join(_job["failed"][:8]))
        self.report({"WARNING"} if _job.get("failed") or _job["cancel"] else {"INFO"}, msg)
        return {"FINISHED"}


_job = {}


class IMPORT_SCENE_OT_ds_gc_audio(bpy.types.Operator, ImportHelper):
    """Better sound effects from your own GameCube disc: samples the GameCube keeps at a higher rate are re-encoded
for the PC into a mod (one sound cache per level)"""
    bl_idname = "import_scene.ds_gc_audio"
    bl_label = "Convert GameCube Audio"

    filter_glob: StringProperty(default="*.iso;*.gcm;*.ciso", options={"HIDDEN"})
    game_dir: StringProperty(name="PC Game Folder", subtype="DIR_PATH",
                             description="Folder with DesertStorm.exe and the level sound caches (mission1.sch ...)")
    mod_name: StringProperty(name="Mod", default=gcaudio.MOD_NAME,
                             description="Written to <game>\\Mods\\<Mod>; switch it on and off in the game's MODS menu")
    levels: StringProperty(name="Levels", default="",
                           description="Comma-separated level names (mission1, training1 ...); empty = all of them")
    all_rates: BoolProperty(name="Same-Rate Samples Too", default=False,
                            description="Also re-encode samples whose rate is the same on both. No audible gain: the "
                                        "PC only plays 4-bit ADPCM, so the cleaner source ends up with the same noise")

    def invoke(self, context, event):
        if not self.game_dir:
            self.game_dir = game_dir_of(context.scene) or archive.find_game_dir() or ""
        return ImportHelper.invoke(self, context, event)

    def draw(self, context):
        lay = self.layout
        lay.label(text="A GameCube disc image you own (.iso / .gcm / .ciso)")
        for p in ("game_dir", "mod_name", "levels", "all_rates"):
            lay.prop(self, p)

    @classmethod
    def poll(cls, context):
        return _gc_job.get("thread") is None or not _gc_job["thread"].is_alive()

    def execute(self, context):
        image = bpy.path.abspath(self.filepath)
        game = bpy.path.abspath(self.game_dir).rstrip("\\/")
        if not os.path.isfile(image):
            self.report({"ERROR"}, "Pick a GameCube disc image (.iso / .gcm / .ciso)")
            return {"CANCELLED"}
        if games.detect(game) is not games.GAMES["ds1"]:       # the GameCube disc is Desert Storm's
            self.report({"ERROR"}, "No DesertStorm.exe in the PC Game Folder (%s)" % game)
            return {"CANCELLED"}
        mod = self.mod_name.strip() or gcaudio.MOD_NAME
        levels = [x for x in self.levels.replace(" ", "").split(",") if x] or None
        out = os.path.join(game, "Mods", mod)
        # pure Python work in a thread (no Blender calls), polled by this modal operator like the archive extraction
        _gc_job.clear()
        _gc_job.update(label="Converting GameCube audio", fraction=0.0, text="Reading the disc", cancel=False,
                       error=None, result=None, lines=[])

        def work():
            try:
                _gc_job["result"] = gcaudio.convert(
                    image, game, out, levels, self.all_rates, log=_gc_job["lines"].append,
                    progress=lambda f, text: _gc_job.update(fraction=f, text=text), cancel=lambda: _gc_job["cancel"])
            except gcaudio.Cancelled:
                _gc_job["error"] = "cancelled"
            except Exception as e:      # reported back on the main thread
                _gc_job["error"] = "%s: %s" % (type(e).__name__, e)

        _gc_job["thread"] = threading.Thread(target=work, name="ds_gc_audio", daemon=True)
        _gc_job["thread"].start()
        _status_job[0] = _gc_job
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.1, window=context.window)
        wm.modal_handler_add(self)
        context.workspace.status_text_set(_draw_status)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type == "ESC" and event.value == "PRESS":
            _gc_job["cancel"] = True
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        context.workspace.status_text_set(_draw_status)
        if _gc_job["thread"].is_alive():
            return {"PASS_THROUGH"}
        context.window_manager.event_timer_remove(self._timer)
        context.workspace.status_text_set(None)
        _status_job[0] = None
        for line in _gc_job["lines"]:
            print(line)
        if _gc_job["error"] == "cancelled":
            self.report({"WARNING"}, "GameCube audio cancelled (levels finished so far are written)")
            return {"CANCELLED"}
        if _gc_job["error"]:
            self.report({"ERROR"}, "GameCube audio failed: " + _gc_job["error"])
            return {"CANCELLED"}
        r = _gc_job["result"]
        done = [lv for lv in r["levels"] if lv[4]]
        if not done:
            self.report({"WARNING"}, "Nothing to convert (%s)" % (r["warning"] or "no level has higher-rate samples"))
            return {"FINISHED"}
        msg = "%d levels, %d samples, %.1f MB written to %s in %.0f s - switch it on in the game's MODS menu" % (
            len(done), sum(lv[1] + lv[2] for lv in done), r["written"] / 1e6, r["out"], r["seconds"])
        self.report({"WARNING"} if r["warning"] else {"INFO"}, msg + ("; " + r["warning"] if r["warning"] else ""))
        return {"FINISHED"}


class IMPORT_SCENE_OT_ds_port(bpy.types.Operator, ImportHelper):
    """Port models, weapons, soldiers and animations from another Conflict game into a Desert Storm mod, following a
recipe file (docs/PORTING_PLAN.md) - converted from your own copies of both games, no game files are shipped"""
    bl_idname = "import_scene.ds_port"
    bl_label = "Port Between Games"

    filter_glob: StringProperty(default="*.port;*.txt", options={"HIDDEN"})
    source_dir: StringProperty(name="Source Game Folder", subtype="DIR_PATH",
                               description="The other game's folder or a folder extracted from it (empty = the "
                                           "recipe's 'source =' line, else the installed game)")
    game_dir: StringProperty(name="Desert Storm Folder", subtype="DIR_PATH",
                             description="Folder with DesertStorm.exe: the mod goes to its Mods folder")

    def invoke(self, context, event):
        if not self.game_dir:
            self.game_dir = game_dir_of(context.scene) or archive.find_game_dir() or ""
        return ImportHelper.invoke(self, context, event)

    def draw(self, context):
        lay = self.layout
        lay.label(text="A porting recipe (.port): name, from, model / weapon / unit / character / animation lines")
        lay.prop(self, "source_dir")
        lay.prop(self, "game_dir")

    def execute(self, context):
        path = bpy.path.abspath(self.filepath)
        game = bpy.path.abspath(self.game_dir).rstrip("\\/")
        if not os.path.isfile(path):
            self.report({"ERROR"}, "Pick a recipe file")
            return {"CANCELLED"}
        if games.detect(game) is not games.GAMES["ds1"]:
            self.report({"ERROR"}, "No DesertStorm.exe in the Desert Storm Folder (%s)" % game)
            return {"CANCELLED"}
        try:
            with open(path, encoding="utf-8-sig") as f:
                recipe = porting.read_recipe(f.read())
        except (OSError, porting.PortError) as e:
            self.report({"ERROR"}, "Recipe: %s" % e)
            return {"CANCELLED"}
        source = bpy.path.abspath(self.source_dir).rstrip("\\/") if self.source_dir else None
        wm = context.window_manager
        wm.progress_begin(0, max(1, len(recipe.items)))
        steps = [0]

        def log(line):                                      # one call per recipe line
            steps[0] += 1
            wm.progress_update(steps[0])
            print("Port:", line)

        # runs here, not on a thread: the porter switches the archive name rule (games.set_current) while it reads
        try:
            report = porting.run(recipe, os.path.join(game, "Mods"), source, game, log=log)
        except (porting.PortError, evo.EvoError, OSError) as e:
            self.report({"ERROR"}, "Port failed: %s" % e)
            return {"CANCELLED"}
        finally:
            wm.progress_end()
        text = bpy.data.texts.get("port-report.txt") or bpy.data.texts.new("port-report.txt")
        text.from_string("\n".join(report) + "\n")
        fails = sum(1 for line in report if line.startswith("[fail]"))
        msg = "%s -> %s (%s; the full report is in the Text Editor as port-report.txt) - switch it on in the " \
              "game's MODS menu" % (recipe.name, os.path.join(game, "Mods", recipe.name), report[-1])
        self.report({"WARNING"} if fails else {"INFO"}, msg)
        return {"FINISHED"}


_gc_job = {}


def editable_models(out, level_pieces):
    """[(archive, model name, path)] of every model in an extraction, each name once (the first archive that has it),
    level pieces (first object of type 5) only with level_pieces."""
    seen, out_list = set(), []
    for archive_dir in sorted(os.listdir(out)):
        folder = os.path.join(out, archive_dir)
        if archive_dir.startswith("_") or not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            stem, ext = os.path.splitext(name)
            if ext.lower() != ".evo" or stem.upper() in seen:
                continue
            path = os.path.join(folder, name)
            if not level_pieces:
                try:
                    with open(path, "rb") as f:
                        head = f.read(0x4000)
                except OSError:
                    continue
                try:
                    if evo.is_world_piece(evo.read_node(evo.Reader(head, 0))):
                        continue
                except Exception:           # a header longer than the bytes read, or unreadable: keep it
                    pass
            seen.add(stem.upper())
            out_list.append((archive_dir, stem, path))
    return out_list


EDITABLE_OPTS = {"scale": 0.01, "armature": True, "animations": "NONE", "vehicle_parts": True, "normal_maps": True,
                 "helpers": True, "frames": True, "colours": True, "cull": True, "skin": "", "texture_dir": ""}


def library_title():
    """The asset library / catalog tree name of the profile's game (Desert Storm's stays "Conflict Desert Storm")."""
    game = profile()
    return assetlib.DS1_TITLE if game.id == "ds1" else game.title.replace(":", "")


def mark_asset(coll, cat, tags=()):
    """Mark a model's collection as an Asset Browser asset in catalog `cat` (assetlib.CATALOGS)."""
    coll.asset_mark()
    data = coll.asset_data
    data.catalog_id = assetlib.catalog_id(cat, library_title())
    for t in (cat,) + tuple(tags):
        if t and t not in data.tags:
            data.tags.new(t)
    src = coll.get("evo_source", "")
    if src:
        data.description = "%s model %s" % (profile().title, os.path.basename(src))


class Classifier:
    """Catalog per model for an extraction (assetlib.category): the WEAPS / VEHICLE tables and .DYN files once."""

    def __init__(self, out):
        profile()                   # the add-on's game current: unnamed tables are found by its name hashing
        dirs = [os.path.join(out, d) for d in sorted(os.listdir(out))
                if not d.startswith("_") and os.path.isdir(os.path.join(out, d))]
        self.finder = evo.AssetFinder(None, dirs)
        # model names in any column (category() only asks about real model stems): the games keep the model in
        # different columns (DS1 WEAPS 1, Global Storm 2)
        self.weapons = assetlib.table_models(evo._table(self.finder, "WEAPS"))
        self.vehicles = assetlib.table_models(evo._table(self.finder, "VEHICLE"))

    def __call__(self, scene, stem):
        first = scene.objects[0].node if scene.objects else None
        node_type = 5 if first is not None and evo.is_world_piece(first) else (first.type if first else 0)
        return assetlib.category(stem, node_type, bool(scene.skinned), self.weapons, self.vehicles,
                                 self.finder.find(stem, (".dyn",)) is not None,
                                 by_name=not self.weapons and not self.vehicles)


def save_editable(context, path, target, classify=None, tags=()):
    """Import one model into a scene of its own and write that scene as a .blend (paths relative to it).
    classify(scene, stem) -> catalog: the model's collection is marked as an asset in it."""
    scene = bpy.data.scenes.new(os.path.splitext(os.path.basename(path))[0])
    try:
        # the importer works on the context's scene / collection: point those at the new scene (no window needed)
        with context.temp_override(scene=scene, view_layer=scene.view_layers[0], collection=scene.collection):
            imp = EvoImporter(bpy.context, path, dict(EDITABLE_OPTS))
            coll = imp.run()
        if classify is not None:
            mark_asset(coll, classify(imp.scene, scene.name), tags)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        bpy.data.libraries.write(target, {scene}, path_remap="RELATIVE_ALL", compress=True)
    finally:
        for coll in list(scene.collection.children_recursive):
            for ob in list(coll.objects):
                bpy.data.objects.remove(ob)
            bpy.data.collections.remove(coll)
        for ob in list(scene.collection.objects):
            bpy.data.objects.remove(ob)
        bpy.data.scenes.remove(scene)


def save_editable_batch(context, job, budget=0.25):
    """Convert models until `budget` seconds are used (called from the modal timer, so Blender stays usable)."""
    start = time.time()
    models = job["models"]
    while job["done"] < len(models) and time.time() - start < budget and not job["cancel"]:
        arch, stem, path = models[job["done"]]
        target = os.path.join(job["out"], "_blend", arch, stem + ".blend")
        job["text"] = "Editable models %d / %d: %s" % (job["done"] + 1, len(models), stem)
        if job.get("skip_existing") and os.path.isfile(target):
            job["done"] += 1
            continue
        try:
            save_editable(context, path, target, job.get("classify"), (arch,))
            job["saved"] += 1
        except Exception as e:      # a model the importer can't read: listed in the final report
            job["failed"].append("%s (%s)" % (stem, e))
        job["done"] += 1
        if job["done"] % 25 == 0:
            bpy.data.orphans_purge(do_recursive=True)
    job["fraction"] = job["done"] / max(1, len(models))


def finish_library(job):
    """After an editable-models run: the catalog file in <out>\\_blend (and the library in the Preferences when
    job["register"]). Returns a note."""
    lib = os.path.join(job["out"], "_blend")
    if not os.path.isdir(lib):
        return None
    assetlib.merge_catalog_file(os.path.join(lib, "blender_assets.cats.txt"), title=library_title())
    if not job.get("register"):
        return None
    libs = bpy.context.preferences.filepaths.asset_libraries
    if any(os.path.normcase(bpy.path.abspath(a.path)) == os.path.normcase(lib) for a in libs):
        return "already an asset library"
    try:
        if hasattr(libs, "new"):
            libs.new(name=library_title(), directory=lib)
        else:
            bpy.ops.preferences.asset_library_add(directory=lib)
            libs[-1].name = library_title()
    except (RuntimeError, TypeError, AttributeError):
        return "add %s in Preferences > File Paths > Asset Libraries" % lib
    return "added as asset library \"%s\" (save the Preferences to keep it)" % library_title()


_status_job = [None]        # the job the status bar shows (extraction or asset library)


def _draw_status(header, context):
    """Status bar while extracting: progress bar (Blender 4.0+) or text, and the cancel hint."""
    job = _status_job[0] if _status_job[0] is not None else _job
    layout = header.layout
    frac = job.get("fraction", 0.0)
    text = job.get("text", "")
    layout.label(text=job.get("label") or ("Saving editable models" if job.get("phase") == "models"
                                            else "Extracting game archives"), icon="FILE_ARCHIVE")
    if hasattr(layout, "progress"):
        row = layout.row()
        row.ui_units_x = 12
        row.progress(factor=frac, type="BAR", text="%d%%" % (frac * 100))
    else:
        layout.label(text="%d%%" % (frac * 100))
    layout.label(text=text)
    layout.separator()
    layout.label(text="Cancel", icon="EVENT_ESC")


# --------------------------------------------------------------------------------------------------- export --

def game_matrix_inverse(m):
    """Inverse of a game-space 4x4 (rotation/scale + translation)."""
    return [list(r) for r in Matrix([list(r) for r in m]).inverted()]


def to_game_point(v, k):
    return (v[0] / k, v[2] / k, v[1] / k)


def to_game_dir(v):
    return (v[0], v[2], v[1])


def texture_of(mat):
    """Texture name for a material: its first image's file name, else the importer's "EVO <name>" material name."""
    if mat is None:
        return "Default"
    if mat.use_nodes and mat.node_tree:
        for n in mat.node_tree.nodes:
            if n.type == "TEX_IMAGE" and n.image and n.outputs[0].is_linked:
                path = n.image.filepath or n.image.name
                stem = os.path.splitext(os.path.basename(bpy.path.abspath(path)) or n.image.name)[0]
                if not (re.fullmatch(r"_[0-9A-Fa-f]{8}", stem) and mat.name.startswith("EVO ")):
                    return stem
                break       # extracted without its name (_<HASH>.DDS): the import's material keeps the real one
    name = mat.name
    if name.startswith("EVO "):
        name = name[4:]
    if name.endswith(" +N"):
        name = name[:-3]
    return name.split(".")[0] if name.count(".") == 1 and name.rsplit(".", 1)[1].isdigit() else name


def colour_layer(me):
    """(colour data, per corner?) of a mesh's active colour attribute (as the importer makes it), or (None, False)."""
    if hasattr(me, "color_attributes"):
        ca = getattr(me.color_attributes, "active_color", None) or (me.color_attributes[0] if len(me.color_attributes) else None)
        if ca is not None and ca.domain in ("POINT", "CORNER") and len(ca.data):
            return ca.data, ca.domain == "CORNER"
        return None, False
    if getattr(me, "vertex_colors", None) and me.vertex_colors.active:      # Blender < 3.2: per corner
        return me.vertex_colors.active.data, True
    return None, False


def pack_colour(c):
    """Blender RGBA floats -> the game's 0xAARRGGBB."""
    b = [max(0, min(255, int(round(x * 255)))) for x in (tuple(c) + (1.0,))[:4]]
    return b[3] << 24 | b[0] << 16 | b[1] << 8 | b[2]


def mesh_parts(context, ob, to_local, k):
    """evo_write.Part list (one per texture) for a Blender object; to_local = game-space matrix from the model's
    space into the template object's space."""
    deps = context.evaluated_depsgraph_get()
    eob = ob.evaluated_get(deps)
    me = eob.to_mesh()
    try:
        me.calc_loop_triangles()
        if hasattr(me, "calc_normals_split"):
            me.calc_normals_split()
        corner_normals = me.corner_normals if hasattr(me, "corner_normals") else None
        uv = me.uv_layers.active.data if me.uv_layers.active else None
        colours, per_corner = colour_layer(me)
        mw = ob.matrix_world
        rot = mw.to_3x3()
        conv = Matrix([list(r) for r in to_local])
        conv3 = conv.to_3x3()
        mirrored = rot.determinant() * conv3.determinant() < 0     # negative scale turns the faces inside out
        parts = {}
        for tri in me.loop_triangles:
            mat = ob.material_slots[tri.material_index].material if tri.material_index < len(ob.material_slots) else None
            tex = texture_of(mat)
            part = parts.setdefault(tex, {"verts": {}, "p": [], "n": [], "uv": [], "c": [], "t": []})
            idx = []
            for li in tri.loops:
                vi = me.loops[li].vertex_index
                n = corner_normals[li].vector if corner_normals is not None else me.loops[li].normal
                u = tuple(uv[li].uv) if uv else (0.0, 0.0)
                if colours is not None:
                    cd = colours[li if per_corner else vi]
                    col = pack_colour(cd.color_srgb if hasattr(cd, "color_srgb") else cd.color)
                else:
                    col = 0xFFFFFFFF
                key = (vi, round(u[0], 5), round(u[1], 5), round(n[0], 3), round(n[1], 3), round(n[2], 3), col)
                if key not in part["verts"]:
                    part["verts"][key] = len(part["p"])
                    wp = mw @ me.vertices[vi].co
                    gp = conv @ Vector(to_game_point(wp, k) + (1.0,))
                    gn = conv3 @ Vector(to_game_dir(rot @ n))
                    if gn.length > 0:
                        gn.normalize()
                    part["p"].append(tuple(gp[:3]))
                    part["n"].append(tuple(gn))
                    part["uv"].append((u[0], 1.0 - u[1]))
                    part["c"].append(col)
                idx.append(part["verts"][key])
            # the axis mirror flips the winding back; a negatively scaled object needs it flipped once more
            part["t"].append(tuple(idx) if mirrored else (idx[0], idx[2], idx[1]))
        return [evo_write.Part(tex, d["p"], d["n"], d["uv"], d["t"],
                               d["c"] if any(c != 0xFFFFFFFF for c in d["c"]) else None)
                for tex, d in parts.items() if d["t"]]
    finally:
        eob.to_mesh_clear()


def skin_part(context, ob, arm, k):
    """evo_write.SkinPart for a character mesh bound to an armature: rest pose, model space, weights from the vertex
    groups (named after the bones, as the importer makes them). All its materials in one part."""
    return skin_groups(context, ob, arm, k, by_material=False)[0][1]


def skin_groups(context, ob, arm, k, by_material=True):
    """[(texture name, evo_write.SkinPart)] for a character mesh bound to an armature (rest pose, model space,
    weights from the vertex groups): one per material when by_material, else one for the whole mesh."""
    rest = arm.data.pose_position if arm else None
    if arm:
        arm.data.pose_position = "REST"
        context.view_layer.update()
    deps = context.evaluated_depsgraph_get()
    eob = ob.evaluated_get(deps)
    me = eob.to_mesh()
    try:
        me.calc_loop_triangles()
        if hasattr(me, "calc_normals_split"):
            me.calc_normals_split()
        corner_normals = me.corner_normals if hasattr(me, "corner_normals") else None
        uv = me.uv_layers.active.data if me.uv_layers.active else None
        to_model = (arm.matrix_world.inverted() if arm else Matrix.Identity(4)) @ ob.matrix_world
        rot = to_model.to_3x3()
        mirrored = rot.determinant() < 0
        groups = {g.index: g.name for g in ob.vertex_groups}
        src = ob.data                               # vertex groups live on the original mesh
        out = {}                                    # material slot (or 0) -> per-part buffers
        for tri in me.loop_triangles:
            slot = tri.material_index if by_material else 0
            d = out.get(slot)
            if d is None:
                d = out[slot] = {"verts": {}, "p": [], "n": [], "uv": [], "w": [], "t": []}
            verts = d["verts"]
            idx = []
            for li in tri.loops:
                vi = me.loops[li].vertex_index
                n = corner_normals[li].vector if corner_normals is not None else me.loops[li].normal
                u = tuple(uv[li].uv) if uv else (0.0, 0.0)
                key = (vi, round(u[0], 5), round(u[1], 5), round(n[0], 3), round(n[1], 3), round(n[2], 3))
                if key not in verts:
                    verts[key] = len(d["p"])
                    d["p"].append(to_game_point(to_model @ me.vertices[vi].co, k))
                    gn = Vector(to_game_dir(rot @ n))
                    if gn.length > 0:
                        gn.normalize()
                    d["n"].append(tuple(gn))
                    d["uv"].append((u[0], 1.0 - u[1]))
                    vw = src.vertices[vi].groups if vi < len(src.vertices) else []
                    d["w"].append([(groups[g.group], g.weight) for g in vw if g.group in groups and g.weight > 0])
                idx.append(verts[key])
            d["t"].append(tuple(idx) if mirrored else (idx[0], idx[2], idx[1]))     # see mesh_parts
        result = []
        for slot in sorted(out):
            d = out[slot]
            mat = ob.material_slots[slot].material if by_material and slot < len(ob.material_slots) else None
            texture = texture_of(mat) if mat is not None else "Default"
            result.append((texture, evo_write.SkinPart(d["p"], d["n"], d["uv"], d["t"], d["w"])))
        return result or [("Default", evo_write.SkinPart([], [], [], [], []))]
    finally:
        eob.to_mesh_clear()
        if arm:
            arm.data.pose_position = rest
            context.view_layer.update()


def skin_report(parts, ds2):
    # ds2: the extra parts are objects of their own (Desert Storm II, The Great Escape), not hosted on DS1 bones
    """Export note for a character's skin parts (None for the plain one-part skin)."""
    if len(parts) == 1 and parts[0].texture is None:
        return None
    textures = ", ".join("%s (%d vertices)" % (p.texture or "the model's own", len(p.positions)) for p in parts)
    note = "skin: %d part(s) - %s" % (len(parts), textures)
    if len(parts) > 1:
        note += ("; squad soldiers still wear their uniform texture on every part (the game's run-time skin) - "
                 "own textures show on characters without one" if not ds2 else
                 "; a soldier given a run-time skin texture wears it on every part")
    return note


def armature_of(ob):
    for m in ob.modifiers:
        if m.type == "ARMATURE" and m.object:
            return m.object
    return ob.parent if ob.parent and ob.parent.type == "ARMATURE" else None


def image_of(mat):
    if mat is not None and mat.use_nodes and mat.node_tree:
        for n in mat.node_tree.nodes:
            if n.type == "TEX_IMAGE" and n.image and n.outputs[0].is_linked:
                return n.image
    return None


def save_dds(img, path):
    """A Blender image as the game's .dds (DXT1, DXT5 with transparency). Returns a problem or None."""
    w, h = img.size
    if not w or not h:
        return "%s has no pixels" % img.name
    px = list(img.pixels[:])                       # float RGBA, bottom row first
    rgba = bytearray(w * h * 4)
    for y in range(h):
        src = (h - 1 - y) * w * 4
        row = px[src:src + w * 4]
        rgba[y * w * 4:(y + 1) * w * 4] = bytes(max(0, min(255, int(v * 255 + 0.5))) for v in row)
    with open(path, "wb") as f:
        f.write(images.encode_dds(w, h, bytes(rgba)))
    if w & (w - 1) or h & (h - 1):
        return "%s is %d x %d - the game wants powers of two (256, 512 ...)" % (img.name, w, h)
    return None


def collision_tris(context, ob, to_local, k, default):
    """[((v0, v1, v2), surface)] of a Blender object in the template object's space (game winding); surface from
    each face's material (see surface_of). Returns (triangles, names of materials that aren't surfaces)."""
    deps = context.evaluated_depsgraph_get()
    eob = ob.evaluated_get(deps)
    me = eob.to_mesh()
    try:
        me.calc_loop_triangles()
        mw = ob.matrix_world
        conv = Matrix([list(r) for r in to_local])
        mirrored = mw.to_3x3().determinant() * conv.to_3x3().determinant() < 0
        out, unknown = [], set()
        for tri in me.loop_triangles:
            mat = ob.material_slots[tri.material_index].material if tri.material_index < len(ob.material_slots) else None
            surface, known = surface_of(mat, default)
            if not known:
                unknown.add(mat.name)
            p = [tuple((conv @ Vector(to_game_point(mw @ me.vertices[vi].co, k) + (1.0,)))[:3]) for vi in tri.vertices]
            out.append(((p[0], p[1], p[2]) if mirrored else (p[0], p[2], p[1]), surface))   # see mesh_parts
        return out, unknown
    finally:
        eob.to_mesh_clear()


def open_edges(parts):
    """Edges of evo_write.Parts not shared by exactly two triangles (by position)."""
    count = {}
    for part in parts:
        key = [tuple(round(c, 2) for c in p) for p in part.positions]
        for t in part.triangles:
            for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
                e = frozenset((key[a], key[b]))
                count[e] = count.get(e, 0) + 1
    return sum(1 for n in count.values() if n != 2)


def helper_kind(ob):
    """'collision', 'shadow' or None for an export object."""
    name = base_name(ob.name).upper()
    if ob.get("evo_role") == "collision" or name.startswith("#BSP_"):
        return "collision"
    if name.startswith("#SHA_"):
        return "shadow"
    return None


def owner_from(scene, ob, prop):
    """The template object a helper belongs to: its recorded object index when that object still has the recorded
    name (level pieces have many unnamed objects - only the index tells them apart), else the only object of that
    name, else None."""
    name = ob.get(prop, None)
    idx = ob.get("evo_object_index", None)
    if isinstance(idx, int) and 0 <= idx < len(scene.objects) and (name is None or scene.objects[idx].node.name == name):
        return scene.objects[idx]
    same = [so for so in scene.objects if name is not None and so.node.name == name]
    return same[0] if len(same) == 1 else None


HEROES = {"HERO01_ARMSTRONG": ("Bradley", "HERO01_UK_01"), "HERO02_RAMIREZ": ("Connors", "HERO02_UK_01"),
          "HERO03_JONES": ("Jones", "HERO03_UK_01"), "HERO04_FOLEY": ("Foley", "HERO04_UK_01")}


def check_export(scene, parts, collision, obs, game_textures, compare_size=True, game="ds1"):
    """(errors, warnings) for an export about to be written: what the writer or the game would trip over.
    compare_size: warn when a part is far bigger / smaller than the original's (off for the generic prop);
    game: the model's game id (its Default.spl length; texture limits are only tested in Desert Storm)."""
    errors, warnings = [], []
    rows = bsp.SURFACE_COUNTS.get(game, len(bsp.SURFACES))
    wrong = sorted({s & 0x3FF for tris in (collision or {}).values() for _t, s in tris if not 0 <= s & 0x3FF < rows})
    if wrong:
        errors.append("surface %s isn't in this %s model's Default.spl (rows 0-%d)"
                      % (", ".join(bsp.surface_name(s) for s in wrong), games.GAMES[game].short, rows - 1))
    for node, ps in parts.items():                     # keys: object indices
        if not isinstance(ps, list) or any(isinstance(p, evo_write.SkinPart) for p in ps):
            continue                                    # character skins: the writer checks those
        for p in evo_write.merge(ps):
            label = "%s / %s" % (scene.objects[node].name, p.texture)
            if len(p.positions) > 65535:
                errors.append("%s has %d vertices - the game's limit is 65535 per texture; split it or use fewer "
                              "UV seams / hard edges" % (label, len(p.positions)))
            if len(p.texture) > 63:
                errors.append("texture name %s is too long (63 characters at most)" % p.texture)
            try:
                p.texture.encode("latin-1")
            except UnicodeEncodeError:
                errors.append("texture name %s has characters the game can't store - use plain letters" % p.texture)
        # size against the original: catches a wrong Scale (0.01 = the model is in metres)
        so = scene.objects[node]
        if compare_size and so.node.bbox and so.role == "mesh":
            mn, mx = so.node.bbox
            old = max(mx[i] - mn[i] for i in range(3))
            pts = [v for p in ps for v in p.positions]
            new = max(max(v[i] for v in pts) - min(v[i] for v in pts) for i in range(3)) if pts else 0
            if old > 1 and new > 0 and not 0.2 <= new / old <= 5:
                warnings.append("%s is %.1fx the size of the original (%.0f cm against %.0f) - check the Scale "
                                "(0.01 = metres)" % (so.name, new / old, new, old))
    for ob in obs:
        textured = any(texture_of(s.material).lower() not in ("default", "untextured") for s in ob.material_slots)
        if textured and ob.type == "MESH" and not ob.data.uv_layers:
            warnings.append("%s has no UV map - its texture will be one colour" % ob.name)
        for slot in ob.material_slots:
            tex = texture_of(slot.material)
            img = image_of(slot.material)
            if tex.lower() in ("default", "untextured") or tex.lower() in game_textures:
                continue
            if img is None:
                warnings.append("%s: material %s has no image and %s isn't a game texture - it will show "
                                "untextured" % (ob.name, slot.material.name if slot.material else "-", tex))
            elif img.size[0] > 2048 or img.size[1] > 2048:
                warnings.append("%s is %d x %d - textures over 2048 can run the game out of memory"
                                % (img.name, img.size[0], img.size[1]))
            elif (img.size[0] > 1024 or img.size[1] > 1024) and game != "ds1":
                warnings.append("%s is %d x %d - textures over 1024 are untested in %s; keep them "
                                "DXT-compressed" % (img.name, img.size[0], img.size[1], games.GAMES[game].short))
            elif img.size[0] > 1024 or img.size[1] > 1024:
                # the original game reads every texture file into a 1 MB buffer: bigger files fail ("Out of
                # memory") - DesertStormFix 3.2+ sizes it to the largest mod file (2048 DXT5 tested in game)
                warnings.append("%s is %d x %d - textures over 1024 need DesertStormFix 3.2+ (the plain game can't "
                                "load them); keep them DXT-compressed" % (img.name, img.size[0], img.size[1]))
    for node, tris in (collision or {}).items():
        if len(tris) > 3000:
            warnings.append("collision of %s has %d triangles - keep collision simple (boxes, big faces)"
                            % (scene.objects[node].name, len(tris)))
    return errors, warnings


def weapons_with_model(template, model):
    """Weapon ids whose Weaps.txt row uses `model` (from the extracted catalog next to the template)."""
    path = evo.AssetFinder(template).find("WEAPS", (".txt", ".TXT"))
    if not path:
        return []
    out = []
    with open(path, "r", encoding="latin-1") as f:
        for line in f:
            cols = line.strip().split(",")
            if len(cols) > 1 and cols[1].strip().upper() == model.upper():
                out.append(cols[0].strip())
    return out


def mod_plugin(game_id):
    """The plugin that serves a game's Mods folder, as its report file names it (Mods/<plugin>-report.txt)."""
    return {"cg": "GlobalStormFix"}.get(game_id, games.GAMES[game_id].plugin)


def starter_file(folder, name, template):
    """Write a starter .weapon / .skin for a new model exported into mod folder `folder` (never overwrites; placed
    by the mod's layout).
    Returns a note or None."""
    stem = os.path.splitext(os.path.basename(template))[0].upper()
    if stem in HEROES:
        soldier, texture = HEROES[stem]
        if modlayout.find(folder, name + ".skin"):
            return None
        path = mod_path(folder, name + ".skin")
        with open(path, "w", encoding="latin-1") as f:
            f.write("# A uniform with its own body (made in Blender from %s).\n"
                    "# Copy %s.dds from the extracted chardata folder into this mod as %s_SKIN.dds (its dds folder;\n"
                    "# any folder of the mod works) and repaint it:\n"
                    "# the body goes with that texture, so other uniforms keep the normal body.\n\n"
                    "uniform      = SAS\n"
                    "uniform name = SAS (%s)\n"
                    "soldier      = %s\n"
                    "texture      = %s_SKIN\n"
                    "body         = %s\n" % (stem, texture, name, name, soldier, name, name))
        return "wrote %s.skin (needs %s_SKIN.dds)" % (name, name)
    ids = weapons_with_model(template, stem)
    if ids:
        if modlayout.find(folder, name + ".weapon"):
            return None
        path = mod_path(folder, name + ".weapon")
        with open(path, "w", encoding="latin-1") as f:
            f.write("# A new weapon with the model %s.evo (made in Blender from %s).\n\n"
                    "id           = %s_%s\n"
                    "based on     = %s\n"
                    "model        = %s\n"
                    "display name = %s\n" % (name, stem, ids[0], name, ids[0], name, name))
        return "wrote %s.weapon (based on %s)" % (name, ids[0])
    return None


def base_name(name):
    head, _, tail = name.rpartition(".")
    return head if head and tail.isdigit() else name


GENERIC_TEMPLATE = "BH_BODGEPLATE"      # one static object, own-mesh collision (as terrain.py / roads.py use it)
# the same kind of model per game: one static object (flags 0x30), its own mesh as collision, two triangles
# (The Great Escape: "104", a plain textured quad of levels 10 / 12 / 13 - every one-object prop there is a level
# picture or sign; Vietnam: the 3 m invisible blocker of 12 levels; Global Storm: INVISPOLY of 16 levels - both
# flags 0x30, collision THIS, untextured, like BH_BODGEPLATE)
GENERIC_TEMPLATES = {"ds1": GENERIC_TEMPLATE, "ge": "104", "ds2": "WALLMAP", "cv": "MPD_INVISBLOCKING3M",
                     "cg": "INVISPOLY"}
# a character converted from another game lands on this body unless Based On names one of the game's characters
# (DS1: Bradley's body - squad soldiers wear the run-time uniform; DS2: the generic soldier)
GENERIC_CHARACTERS = {"ds1": "HERO01_ARMSTRONG", "ds2": "534_POLY_SOLDIER", "cv": "MPD_US_MOTOWN",
                      "cg": "C04_500_POLY"}
DEFAULT_EXTRACT_DIR = os.path.join(os.path.expanduser("~"), "Documents", "Conflict Desert Storm extracted")




def extracted_roots():
    """Extraction folders known in this session: the default one and the roots of the models imported."""
    roots = [extract_dir()]
    for coll in bpy.data.collections:
        src = coll.get("evo_source")
        if src:
            roots.append(os.path.dirname(os.path.dirname(os.path.abspath(bpy.path.abspath(src)))))
    return list(dict.fromkeys(os.path.normcase(os.path.abspath(r)) for r in roots))


def is_game_image(img, roots):
    """An image loaded from the extracted game folders: the game has it already (not saved into mods)."""
    if img is None or not img.filepath:
        return False
    path = os.path.normcase(os.path.abspath(bpy.path.abspath(img.filepath)))
    return any(path.startswith(r + os.sep) for r in roots)


def generic_template(game_dir="", character=False):
    """(path, where) of the generic prop template (character: the generic character body, GENERIC_CHARACTERS) of the
    game in game_dir (else the current game): from extracted folders of that game (the default extraction folder,
    folders models were imported from), else read out of the player's own game archives into a cache file. (None,
    why) if neither is there. Nothing of the game ships with the add-on."""
    gdir = bpy.path.abspath(game_dir) if game_dir else ""
    game = games.detect(gdir) if gdir else None
    game = games.set_current(game) if game else games.current()       # the archives hash names by the game's rule
    name = (GENERIC_CHARACTERS if character else GENERIC_TEMPLATES).get(game.id)
    if name is None:
        return None, "there is no generic %s for %s - base the model on one of its game models" % (
            "character" if character else "prop", game.short)
    dirs = []
    for root in extracted_roots():
        if games.detect_extracted(root) not in (None, game):
            continue                                  # another game's extraction
        try:
            dirs += [os.path.join(root, d) for d in sorted(os.listdir(root))
                     if not d.startswith("_") and os.path.isdir(os.path.join(root, d))]
        except OSError:
            pass
    hit = evo.AssetFinder(None, dirs).find(name, (".evo",)) if dirs else None
    if hit:
        return hit, "extracted"
    cache = os.path.join(tempfile.gettempdir(), "conflict_ds_tools", name + ".EVO")    # names differ per game
    if not gdir or not os.path.isdir(gdir):
        gdir = archive.find_game_dir(game) or ""
    if character and game.id in ("cv", "cg") and gdir:
        # Vietnam / Global Storm bodies are skinned to a skeleton file of their own: it goes next to the body
        try:
            gf = archive.GameFiles(gdir)
            # (+ Global Storm's .SKL bone lists, which its animations bind to)
            for sk in [f for e in evo.SKELETON_FILES for f in (e, os.path.splitext(e)[0] + ".SKL")]:
                dst = os.path.join(os.path.dirname(cache), sk)
                if not os.path.isfile(dst):
                    sk_data = gf.get(sk)
                    if sk_data:
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        with open(dst, "wb") as f:
                            f.write(sk_data)
        except OSError:
            pass
    if os.path.isfile(cache):
        return cache, "cache"
    if not gdir:
        return None, "no extracted folder has %s.EVO and %s wasn't found - set Game Folder or extract the " \
                     "archives (File > Import > Conflict: Desert Storm Archives (extract))" % (name, game.short)
    try:
        data = archive.GameFiles(gdir).get(name + ".EVO")
    except OSError as e:
        return None, "can't read the game archives in %s: %s" % (gdir, e)
    if not data:
        return None, "%s.EVO isn't in the archives in %s" % (name, gdir)
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    with open(cache, "wb") as f:
        f.write(data)
    return cache, "game archives"


# collision surfaces: Desert Storm's Default.spl rows, then the rows each later game appended (the writer refuses rows
# the model's game doesn't have)
_FROM = {"ds2": " (Desert Storm II, The Great Escape and later)", "cv": " (Vietnam and later)", "cg": " (Global Storm)"}
SURFACE_ITEMS = [(str(i), n, "Default.spl row %d" % i + _FROM.get(bsp.first_game_with(i), ""))
                 for i, n in enumerate(bsp.ALL_SURFACES)]


LAST_EXPORT = "ds_last_export"          # scene ID property: the export options the quick export repeats
LAST_OPTIONS = ("template", "base", "scale", "collision", "surface", "save_textures", "replace_game", "game_dir")


class EXPORT_SCENE_OT_evo(bpy.types.Operator, ExportHelper):
    """Write a Conflict: Desert Storm model (.evo) based on a game model: new geometry for its parts"""
    bl_idname = "export_scene.evo"
    bl_label = "Export EVO"
    bl_options = {"REGISTER"}

    game: game_prop()
    filename_ext = ".evo"
    filter_glob: StringProperty(default="*.evo;*.EVO", options={"HIDDEN"})
    base: EnumProperty(name="Based On", default="MODEL",
                       items=(("MODEL", "Game Model", "A game model (the file below, or the model the selected objects "
                                                      "were imported from); with neither, the generic prop"),
                              ("GENERIC", "Auto / Generic Prop", "A brand-new static prop: one part, collision from "
                                                                 "#BSP_ objects or else its own mesh (template "
                                                                 + GENERIC_TEMPLATE + ", Desert Storm II: "
                                                                 + GENERIC_TEMPLATES["ds2"] + ", The Great Escape: "
                                                                 + GENERIC_TEMPLATES["ge"] + ", Vietnam: "
                                                                 + GENERIC_TEMPLATES["cv"] + ", Global Storm: "
                                                                 + GENERIC_TEMPLATES["cg"] + ", found in the "
                                                                 "extracted folders or the game's archives)")),
                       description="What the new model is built on")
    template: StringProperty(name="Model File", subtype="FILE_PATH",
                             description="The game model this one replaces (.evo from the extracted folders): its "
                                         "attachment points, materials and part layout are kept. Empty = the model "
                                         "the selected objects were imported from")
    scale: FloatProperty(name="Scale", default=0.01, min=1e-5, max=100.0,
                         description="Blender units per game centimetre (0.01 = the model is in metres)")
    selected_only: BoolProperty(name="Selected Only", default=True)
    collision: EnumProperty(
        name="Collision", default="HELPERS",
        items=(("HELPERS", "From #BSP_ Objects", "Exported objects named #BSP_... (as the import makes them) become the "
                                                "collision; without any, the original model's collision is kept"),
               ("MESH", "From the Model", "Every exported part collides with its own mesh (one surface)"),
               ("KEEP", "Keep the Original", "The collision of the model this one is based on"),
               ("NONE", "None", "Nothing collides")),
        description="What soldiers, bullets and vehicles hit")
    surface: EnumProperty(name="Default Surface", default="4",
                          items=SURFACE_ITEMS,
                          description="Surface for collision faces whose material isn't a surface (\"Surface: Metal\" "
                                      "or just \"Metal\"): footstep sounds and bullet-hit effects. Every surface "
                                      "stops soldiers except the barrier for the other side; computer soldiers "
                                      "route around Blocking ... faces (walls, props) and walk on the plain ones")
    check_only: BoolProperty(name="Check Only", default=False,
                             description="Report what the game would trip over (sizes, textures, limits) and write "
                                         "nothing")
    to_mod: BoolProperty(name="Into a Mod Folder", default=False,
                         description="Write the model (and its new textures) into <game>\\Mods\\<Mod> instead of the "
                                     "file picked here; the picked file's name is the model's name")
    game_dir: StringProperty(name="Game Folder", subtype="DIR_PATH",
                             description="Folder with the game's exe (found from the registry - check it if you have "
                                         "the game twice, e.g. Steam and GOG)")
    mod_name: StringProperty(name="Mod", default="MyMod",
                             description="Mod folder name under <game>\\Mods (a _ in front = switched off)")
    replace_game: BoolProperty(name="Replace the Game's Model", default=False,
                               description="Export under the original's own name (OILDRUM.evo ...): it changes that "
                                           "model everywhere. Off = a new model; a new weapon or body gets a "
                                           "starter .weapon / .skin file next to it")
    save_textures: BoolProperty(name="Save Textures", default=True,
                                description="Save new textures (materials whose image isn't one of the game model's own) "
                                            "as .dds next to the model, named after the image")

    def source_of(self, obs):
        for ob in obs:
            for coll in ob.users_collection:
                if coll.get("evo_source"):
                    return coll["evo_source"]
        return ""

    def helpers(self, context, scene, helper_obs, parts, k, notes, used):
        """(collision for evo_write.rebuild or None = keep, {shadow object: [Part]}) from the #BSP_ / #SHA_ objects
        and the Collision option; (None, None) after reporting an error."""
        default = int(self.surface)
        meshes = [so for so in scene.objects if so.node.kind in (0, 3) and so.node.meshes and so.role == "mesh"]
        root = next((so for so in scene.objects if so.parent is None), scene.objects[0])
        collision = None
        col_obs = [o for o in helper_obs if helper_kind(o) == "collision"]
        if self.collision == "HELPERS" and col_obs:
            collision = {}
            for ob in col_obs:
                owner = owner_from(scene, ob, "evo_collision_of")
                name = base_name(ob.name).upper()
                if owner is None:      # "#BSP_X": the object naming that helper, the object X, the only part, the root
                    owner = next((so for so in scene.objects if so.node.collision_source.upper() == name), None)
                if owner is None:
                    owner = next((so for so in scene.objects if so.node.name and "#BSP_" + so.node.name.upper() == name),
                                 None)
                if owner is None:
                    owner = meshes[0] if len(meshes) == 1 else root
                tris, unknown = collision_tris(context, ob, game_matrix_inverse(owner.world), k, default)
                if unknown:
                    notes.append("%s: materials %s aren't surfaces - used %s" % (
                        ob.name, ", ".join(sorted(unknown)), bsp.surface_name(default)))
                collision.setdefault(owner.index, []).extend(tris)
                used.add(ob.name)
        elif self.collision == "MESH" or (self.collision == "HELPERS" and self._generic):
            if self.collision == "HELPERS":
                notes.append("no #BSP_ object - the new prop collides with its own mesh")
            collision = {}
            for node, ps in parts.items():
                if isinstance(ps, list):
                    collision[node] = [((p.positions[a], p.positions[b], p.positions[c]), default)
                                       for p in ps for a, b, c in p.triangles]
        elif self.collision == "NONE":
            collision = {so.index: [] for so in scene.objects if any(u == 0 and t for u, t in so.node.trees)}
        if self.collision != "HELPERS" and col_obs:
            used.update(o.name for o in col_obs)           # deliberately not used: the Collision option says so
        if self.collision == "NONE":
            notes.append("collision removed")
        elif collision:
            surfaces = sorted({s for tris in collision.values() for _t, s in tris})
            notes.append("collision %d triangles on %s, surfaces %s" % (
                sum(len(t) for t in collision.values()), ", ".join(scene.objects[n].name for n in collision),
                ", ".join(bsp.surface_name(s & 0x3FF) for s in surfaces)))
        shadows = {}
        self.add_shadow = None
        targets = [so for so in scene.objects if so.role == "shadow"]
        static = scene.objects[0].node.type == 0 and not scene.objects[0].node.bones
        for ob in (o for o in helper_obs if helper_kind(o) == "shadow"):
            so = next((t for t in targets if t.node.name.upper() == base_name(ob.name).upper()), None)
            if so is None and len(targets) == 1:
                so = targets[0]
            if so is None and not targets and static and self.add_shadow is None:
                so = scene.objects[0]            # a new shadow object, in the model's own (root) space
                adding = True
            elif so is None:
                notes.append("%s: %s" % (ob.name, "only static models (props, weapons, buildings) can get a new shadow "
                                         "mesh" if not targets else "the original model has no shadow mesh of that name"))
                continue
            else:
                adding = False
            new = mesh_parts(context, ob, game_matrix_inverse(so.world), k)
            if not new:
                continue
            ntri = sum(len(p.triangles) for p in new)
            holes = open_edges(new)
            if holes:
                notes.append("%s has %d open edges - the game casts no shadow from them (close the mesh)" % (ob.name, holes))
            if ntri > 2000:
                notes.append("%s has %d triangles - the game searches neighbours one by one at load, keep shadow meshes "
                             "small" % (ob.name, ntri))
            if adding:
                self.add_shadow = new
            else:
                shadows[so.index] = new
            used.add(ob.name)
            notes.append("shadow %s%d triangles" % ("added, " if adding else so.node.name + " ", ntri))
        return collision, shadows

    def remember(self, context, name):
        """Keep this export's options (and the mod / model name) for the sidebar's Export to Mod."""
        scn = context.scene
        last = {p: getattr(self, p) for p in LAST_OPTIONS}
        last["save_textures"] = int(last["save_textures"])
        last["replace_game"] = int(last["replace_game"])
        scn[LAST_EXPORT] = last
        props(scn).last_exported_model = name
        if self.to_mod:
            props(scn).active_mod = self.mod_name.strip().strip("\\/")

    def invoke(self, context, event):
        obs = context.selected_objects or context.view_layer.objects
        if not self.template:
            self.template = self.source_of(obs)
        if not self.template and props(context.scene).generic_prop:
            self.base = "GENERIC"
        if props(context.scene).active_mod:                     # the profile's game's active mod
            self.mod_name = props(context.scene).active_mod
        apply_game(self)
        self.game_dir = game_dir_of(context.scene)        # the profile's game (sidebar > Desert Storm > Game)
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        apply_game(self)
        obs = [o for o in (context.selected_objects if self.selected_only else context.view_layer.objects)
               if o.type == "MESH"]
        if self.base == "GENERIC":
            template = ""
        else:
            template = bpy.path.abspath(self.template) if self.template else self.source_of(obs)
        # objects imported from another game's model (or Based On another game's model): converted - built on this
        # game's generic prop as one static part (cross_game_parts), surfaces / textures adapted below
        # (also when Based On is one of this game's models - e.g. to replace it - the objects' own game decides)
        cross = None
        tgame = model_game(template) if template and os.path.isfile(template) else None
        if tgame is not None and tgame is not profile():
            cross = (tgame, template)
            template = ""
        src = bpy.path.abspath(self.source_of(obs) or "")
        src_game = model_game(src) if src and os.path.isfile(src) else None
        if src_game is not None and src_game is not profile():
            cross = (src_game, src)
        self._generic = not template                 # nothing to go on: a brand-new prop
        generic_note = None
        cross_character = bool(cross) and any(armature_of(o) for o in obs)
        if self._generic:
            template, where = generic_template(self.game_dir, character=cross_character)
            if not template:
                self.report({"ERROR"}, "Generic prop: " + where)
                return {"CANCELLED"}
            generic_note = "generic prop (%s from the %s)" % (os.path.splitext(os.path.basename(template))[0], where)
            if cross:
                generic_note = "converted from %s (%s) onto the %s generic %s %s" % (
                    os.path.basename(cross[1]), cross[0].short, profile().short,
                    "character" if cross_character else "prop", os.path.splitext(os.path.basename(template))[0])
        elif not os.path.isfile(template):
            self.report({"ERROR"}, "Based On: %s doesn't exist - pick the game model this one is based on, or "
                                   "Auto / Generic Prop" % template)
            return {"CANCELLED"}
        elif cross:
            generic_note = "converted from %s (%s) onto %s" % (os.path.basename(cross[1]), cross[0].short,
                                                               os.path.basename(template))
        if not obs:
            self.report({"ERROR"}, "No mesh objects to export")
            return {"CANCELLED"}
        k = self.scale
        helper_obs = [o for o in obs if helper_kind(o)]
        obs = [o for o in obs if not helper_kind(o)]
        cross_dropped = []
        cross_rest = {}                 # source file -> (rest pose by bone name, parent by bone name)
        if cross:
            # every selected object against the model it was imported from: a character may come in pieces (Global
            # Storm keeps heads apart - C04_MILITARY_UNIFORM + C04_HEAVY_HEAD), each with its own objects and rest pose
            by_source = {}
            for o in obs:
                path = bpy.path.abspath(self.source_of([o]) or "") or cross[1]
                by_source.setdefault(path if os.path.isfile(path) else cross[1], []).append(o)
            for path, sobs in by_source.items():
                try:
                    src_scene = evo.load(path)
                except (evo.EvoError, OSError) as e:
                    self.report({"ERROR"}, "can't read %s: %s" % (path, e))
                    return {"CANCELLED"}
                roles = {so.index: so.role for so in src_scene.objects}
                if cross_character:
                    # the source skeleton's rest pose (game space, by bone name): the skin is moved from it onto the
                    # target's (cross_repose) - the games' Biped rigs share their bone names
                    cross_rest[path] = ({so.name.upper(): so.world for so in src_scene.objects if so.name},
                                        {so.name.upper(): src_scene.objects[so.parent].name.upper()
                                         for so in src_scene.objects if so.name and so.parent is not None})
                    # untextured marker meshes of the source's objects (ROOT_OBJECT's triangle) aren't body geometry
                    untextured = {so.index for so in src_scene.objects if so.role != "skin" and
                                  all((t or "default").lower() == "default" for _h, t in so.node.textures)}
                    cross_dropped += [o.name for o in sobs if o.get("evo_node") in untextured]
                # the source's connector / hit-region / bone marker meshes aren't geometry (attached parts such as
                # wheels have no evo_node and stay)
                cross_dropped += [o.name for o in sobs if o.get("evo_node") is not None
                                  and roles.get(o["evo_node"]) not in ("mesh", None)]
            obs = [o for o in obs if o.name not in cross_dropped]
            if cross_character and cross[0].id == "cg" and len(by_source) == 1 and \
                    not os.path.basename(cross[1]).upper().endswith("_HEAD.EVO"):
                skin_note_head = ("Global Storm keeps heads apart from bodies: import the head model too (e.g. "
                                  "C04_RIFLEMAN_HEAD) and select both to export a soldier with his head")
            else:
                skin_note_head = None
        else:
            skin_note_head = None
        point_obs = [o for o in (context.selected_objects if self.selected_only else context.view_layer.objects)
                     if o.get("evo_point_role") or base_name(o.name).upper().startswith("#PT_")]
        cross_helpers_note = None
        if cross_character and (helper_obs or point_obs):
            # a converted body keeps the target body's own hit regions / collision / points: the source's per-bone
            # helpers would land on the bones that carry extra skin parts (DS1) and aren't the target's anyway
            cross_helpers_note = "the %s character's %d helper object(s) not used - the body keeps %s's hit regions" % (
                cross[0].short, len(helper_obs) + len(point_obs), os.path.splitext(os.path.basename(template))[0])
            helper_obs, point_obs = [], []
        data = open(template, "rb").read()
        scene = evo.Scene(evo.read(data))
        # Vietnam / Global Storm characters: the body file holds only the skin - work on it with its skeleton
        # attached (bone names, rest pose) and hand the skeleton file to the writer; the scene's object numbers are
        # the file's shifted by the skeleton's object count
        skeleton_bytes, file_offset = None, 0
        if scene.nodes[0].version & 0xFFFF in evo_write.CV_VERSIONS and any(n.kind == 2 for n in scene.nodes):
            attached = evo.load(template)
            if getattr(attached, "skeleton_path", None):
                file_offset = len(attached.objects) - len(scene.objects)
                skeleton_bytes = open(attached.skeleton_path, "rb").read()
                scene = attached
        mgame = MODEL_GAMES.get(scene.nodes[0].version & 0xFFFF)          # the model's game decides the layout
        if mgame not in WRITE_GAMES:
            self.report({"ERROR"}, "%s is a %s model - writing models is supported for %s so far" % (
                os.path.basename(template), games.GAMES[mgame].short if mgame else "unknown",
                ", ".join(games.GAMES[g].short for g in WRITE_GAMES)))
            return {"CANCELLED"}
        ds2 = mgame != "ds1"           # DS2 / The Great Escape: extra skin parts become objects of their own
        wrong = wrong_game(games.GAMES.get(mgame), (os.path.basename(template), "model"))
        if wrong:
            self.report({"ERROR"}, wrong)
            return {"CANCELLED"}
        if self.to_mod and mgame not in MOD_GAMES:
            self.report({"ERROR"}, "%s has no plugin that loads a Mods folder yet - untick Into a Mod Folder and "
                                   "export to a file" % games.GAMES[mgame].short)
            return {"CANCELLED"}
        targets = [so for so in scene.objects if so.node.kind in (0, 3) and so.node.meshes and so.role == "mesh"]
        skins = [so for so in scene.objects if so.role == "skin"]
        if len(skins) > 1:
            # an exported multi-part character (no game model has two skin objects): its extra parts would stay
            self.report({"ERROR"}, "%s already has %d skin parts - base the export on the game's own model (Based On)"
                        % (os.path.basename(template), len(skins)))
            return {"CANCELLED"}
        if skins:
            targets = []                                    # characters: the skin is what gets replaced
        by_name = {}
        for ob in obs:
            by_name.setdefault(base_name(ob.name).upper(), []).append(ob)
        parts = {}
        used = set()
        skin_notes = []
        key_offsets = {}                # cross-game heads: hat / face connectors refitted
        for so in targets:
            match = by_name.get(so.name.upper(), [])
            if len(targets) == 1:
                # one-part model: every selected mesh is that part - except the model's other objects (Desert Storm
                # II's connectors carry small marker meshes the import brings in)
                others = {o.name.upper() for o in scene.objects if o.index != so.index and o.name}
                match = [o for o in obs if base_name(o.name).upper() not in others]
            if not match:
                continue
            to_local = game_matrix_inverse(so.world)
            new = []
            for ob in match:
                new += mesh_parts(context, ob, to_local, k)
                used.add(ob.name)
            if new:
                parts[so.index] = new
        for so in skins:
            match = by_name.get(so.name.upper(), [])
            if not match and len(skins) == 1:
                others = {o.name.upper() for o in scene.objects if o.index != so.index and o.name}
                obs_ = [o for o in obs if base_name(o.name).upper() not in others]   # not the model's marker meshes
                match = [o for o in obs_ if armature_of(o)] or obs_
            if not match:
                continue
            # one skin part per material (several meshes / materials -> several skin objects, each with its own
            # texture; over 65535 vertices -> split): see character_skin_parts / evo_write.rebuild
            groups = []
            group_source = []
            heads = {p for p in cross_rest if os.path.basename(p).upper().endswith("_HEAD.EVO")}
            for ob in match:
                rigid = not ob.vertex_groups and ob.parent_type == "BONE" and ob.parent_bone
                if cross and rigid and heads and bpy.path.abspath(self.source_of([ob]) or "") not in heads:
                    # a head model of its own brings its eyes and teeth: the body's are left out (as the porter)
                    skin_notes.append("%s left out - the head model has its own" % ob.name)
                    used.add(ob.name)
                    continue
                parts_of_ob = skin_groups(context, ob, armature_of(ob), k)
                if cross and rigid:
                    # a rigid part hung on a bone (DS2's eyes, eyelids, teeth): all its weight on that bone
                    for _tex, sp in parts_of_ob:
                        sp.weights = [[(ob.parent_bone, 1.0)] for _ in sp.positions]
                groups += parts_of_ob
                path = bpy.path.abspath(self.source_of([ob]) or "") if cross else ""
                group_source += [path if path in cross_rest else cross[1] if cross else ""] * len(parts_of_ob)
                used.add(ob.name)
            if cross and cross_rest:
                target_rest = {o.name.upper(): o.world for o in scene.objects if o.name}
                moved = {}
                for (_tex, sp), path in zip(groups, group_source):
                    rest, parents = cross_rest.get(path) or next(iter(cross_rest.values()))
                    moved.update(cross_repose(sp, rest, target_rest, parents))
                if moved:
                    skin_notes.append("bones the %s skeleton doesn't have (weight moved to): %s" % (
                        profile().short, ", ".join("%s -> %s" % kv for kv in sorted(moved.items()))))
            own = next((t for _h, t in so.node.textures if t and t.lower() != "default"), "")
            skin_parts = character_skin_parts(groups, own)
            if not skin_parts:
                self.report({"ERROR"}, "%s has no triangles" % ", ".join(o.name for o in match))
                return {"CANCELLED"}
            parts[so.index] = skin_parts[0] if len(skin_parts) == 1 else skin_parts
            if cross and mgame not in ("cv", "cg"):
                # the body's hat / face connectors onto the converted head (hats, glasses sit on it, not over it)
                fit = head_connector_offsets(scene, skin_parts)
                if fit:
                    key_offsets.update(fit)
                    skin_notes.append("head gear points moved to the new head: %s" % ", ".join(
                        "%s %+.1f cm" % (scene.objects[i].name, max(d, key=abs)) for i, d in sorted(fit.items())))
            skin_notes.append(skin_report(skin_parts, ds2))
        notes = [generic_note] if generic_note else []
        if cross_helpers_note:
            notes.append(cross_helpers_note)
        if skin_note_head and skins:
            notes.append(skin_note_head)
        notes += [n for n in skin_notes if n]
        collision, shadows = self.helpers(context, scene, helper_obs, parts, k, notes, used)
        if shadows is None:
            return {"CANCELLED"}
        if cross:
            if cross_dropped:
                notes.append("left out %d marker object(s) of the %s model (connectors / hit regions)"
                             % (len(cross_dropped), cross[0].short))
            converted = {}
            for node, tris in (collision or {}).items():
                mapped = []
                for t, surface in tris:
                    new, old = bsp.convert_surface(surface, mgame)
                    if old:
                        converted[old] = bsp.surface_name(new)
                    mapped.append((t, new))
                collision[node] = mapped
            if converted:
                notes.append("surfaces %s has no row for: %s" % (profile().short, ", ".join(
                    "%s -> %s" % kv for kv in sorted(converted.items()))))
            # the other game's textures go into the mod under a prefixed name: a file named like one of this game's
            # textures would replace that texture everywhere in the game
            prefix = cross[0].id.upper() + "_"
            for ps in list(parts.values()) + list(shadows.values()) + [self.add_shadow or []]:
                for p in (ps if isinstance(ps, list) else [ps]):
                    if isinstance(p, evo_write.Part) and p.texture.lower() not in ("default", "untextured", ""):
                        p.texture = (prefix + p.texture)[:63]
                    elif isinstance(p, evo_write.SkinPart) and p.texture:
                        p.texture = (prefix + p.texture)[:63]
        parts.update(shadows)
        if not parts and collision is None and not point_obs and not self.add_shadow:
            self.report({"ERROR"}, "No object matches a part of %s (%s)" % (
                os.path.basename(template), ", ".join(so.name for so in targets)))
            return {"CANCELLED"}
        points = {}
        sights = []
        for ob in point_obs:
            role = str(ob.get("evo_point_role") or base_name(ob.name)[4:]).upper()
            if role in SIGHT_ROLES:                    # Aim Camera helpers: not game data
                sights.append(ob.name)
                used.add(ob.name)
                continue
            if role not in evo.POINT_ROLES:
                notes.append("%s: unknown point (use %s)" % (ob.name, ", ".join("#PT_" + r for r in evo.POINT_ROLES)))
                continue
            owner = owner_from(scene, ob, "evo_point_of")
            if owner is None:
                meshes = [so for so in scene.objects if so.role == "mesh"]
                owner = meshes[0] if len(meshes) == 1 else next(so for so in scene.objects if so.parent is None)
            local = Matrix([list(r) for r in game_matrix_inverse(owner.world)])
            pos = local @ Vector(to_game_point(ob.matrix_world.translation, k) + (1.0,))
            points.setdefault(owner.index, {})[role] = tuple(pos[:3])
            used.add(ob.name)
        if points:
            notes.append("points " + ", ".join("%s %s" % (scene.objects[n].name, "/".join(sorted(r.lower() for r in d)))
                                               for n, d in points.items()))
        if sights:
            notes.append("not written (sight helpers for the Aim Camera, Blender only): " + ", ".join(sights))
        posed = posed_pivots(obs)
        if posed:
            notes.append("%s turned away from rest - the geometry is written as posed (Reset Vehicle Pose first)"
                         % ", ".join(posed))
        game = {name.lower() for n in scene.nodes for _h, name in n.textures}
        roots = extracted_roots()       # images from the extracted folders are game textures the game already has
        if cross:                       # ... of this game only
            roots = [r for r in roots if games.detect_extracted(r) in (None, profile())]
        from_game = {texture_of(s.material).lower(): texture_of(s.material) for o in obs if o.name in used
                     for s in o.material_slots if is_game_image(image_of(s.material), roots)}
        from_game = {k: v for k, v in from_game.items() if k not in game}
        game |= set(from_game)
        errors, problems = check_export(scene, parts, collision, [o for o in obs if o.name in used], game,
                                        compare_size=not self._generic, game=mgame)
        if errors:
            self.report({"ERROR"}, "; ".join(errors))
            return {"CANCELLED"}
        if self.check_only:
            self.report({"WARNING"} if problems else {"INFO"},
                        "; ".join(problems) if problems else "No problems found (nothing written)")
            return {"FINISHED"}
        notes += problems
        name = os.path.splitext(os.path.basename(self.filepath))[0]
        filepath = self.filepath
        if self.to_mod:
            gdir = bpy.path.abspath(self.game_dir) if self.game_dir else ""
            if not gdir or not games.use_folder(gdir):
                self.report({"ERROR"}, "Game Folder must be the folder with the game's exe (%s)"
                            % " / ".join(e for g in games.GAMES.values() for e in g.exes[:1]))
                return {"CANCELLED"}
            if games.current().id != mgame:
                self.report({"ERROR"}, "%s is a %s model, but Game Folder is %s's - pick that game's folder" % (
                    os.path.basename(template), games.GAMES[mgame].short, games.current().short))
                return {"CANCELLED"}
            mod = self.mod_name.strip().strip("\\/") or "MyMod"
            if any(c in mod for c in '<>:"/\\|?*'):
                self.report({"ERROR"}, "The mod name can't contain < > : \" / \\ | ? *")
                return {"CANCELLED"}
            if self.replace_game:
                name = os.path.splitext(os.path.basename(template))[0]
            mod_dir = os.path.join(gdir, "Mods", mod)
            filepath = mod_path(mod_dir, name + ".evo")
        try:
            if file_offset:
                def to_file(d):
                    return None if d is None else {(k - file_offset if isinstance(k, int) else k): v
                                                   for k, v in d.items() if not isinstance(k, int) or k >= file_offset}
                parts, collision, points = to_file(parts), to_file(collision), to_file(points)
                key_offsets = to_file(key_offsets)
            out = evo_write.rebuild(data, parts, model_name=name, warnings=notes, collision=collision, points=points,
                                    add_shadow=self.add_shadow, skeleton=skeleton_bytes,
                                    key_offsets=key_offsets or None)
        except evo.EvoError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        with open(filepath, "wb") as f:
            f.write(out)
        self.remember(context, name)
        starter = None
        if self.to_mod and not self.replace_game and mgame in STARTER_GAMES:
            starter = starter_file(mod_dir, name, template)   # the plugin's .weapon (DS1 also .skin)
            if starter:
                notes.append(starter)
        if self.to_mod and mgame != "ds1":
            # the other plugins' Mods folders replace files by name: a new name is only used when something refers
            # to it (a starter .weapon, a table row) - Replace the Game's Model changes the game's own
            plugin = mod_plugin(mgame)
            served = ("%s.log in the game folder (dev builds) lists" % plugin if mgame == "cv" else   # no report
                      "Mods/%s-report.txt lists" % plugin)
            notes.append("written for %s's Mods folder (files replace the game's by name; %s what it served)"
                         % (plugin, served) if self.replace_game or starter else
                         "written for %s's Mods folder - %s uses a model by its file name, so a new name shows "
                         "nowhere until a game file refers to it (tick Replace the Game's Model to change the "
                         "original)" % (plugin, games.GAMES[mgame].short))
        if self.save_textures:
            saved = {}                                      # lower-case name -> file name
            folder = os.path.dirname(filepath)
            for ob in obs:
                if ob.name not in used:
                    continue
                for slot in ob.material_slots:
                    img = image_of(slot.material)
                    tex = texture_of(slot.material)
                    if cross and tex.lower() not in ("default", "untextured", ""):
                        tex = (cross[0].id.upper() + "_" + tex)[:63]
                    if img is None or tex.lower() in game or tex.lower() == "default" or tex.lower() in saved:
                        continue
                    saved[tex.lower()] = tex + ".dds"
                    problem = save_dds(img, mod_path(mod_dir, tex + ".dds") if self.to_mod else
                                       os.path.join(folder, tex + ".dds"))
                    if problem:
                        notes.append(problem)
            if saved:
                notes.append("saved " + ", ".join(sorted(saved.values())))
            if from_game:
                notes.append("not saved (images from the extracted game folders - the game has them): "
                             + ", ".join(sorted(from_game.values())))
        # the model's own marker meshes (Desert Storm II's connectors carry small ones) aren't parts to report
        markers = {so.name.upper() for so in scene.objects if so.role in ("connector", "hitbox") and so.name}
        skipped = [o.name for o in obs + helper_obs if o.name not in used and base_name(o.name).upper() not in markers]
        textures = sorted({p.texture for n, ps in parts.items() if isinstance(ps, list) and n not in shadows
                           for p in ps if isinstance(p, evo_write.Part)}) or ["(character skin)"]
        msg = "Wrote %s: %d part(s), textures %s" % (filepath if self.to_mod else os.path.basename(filepath), len(parts),
                                                     ", ".join(textures))
        if skipped:
            msg += "; not exported (no matching part): " + ", ".join(skipped)
        if notes:
            msg += "; " + "; ".join(notes)
        warn = skipped or any(not n.startswith(("saved ", "collision ", "shadow ", "wrote ", "points ", "generic prop ",
                                                "converted from ", "left out ",
                                                "no #BSP_ object", "not saved (images", "not written (sight",
                                                "written for %s's Mods folder (files" % mod_plugin(mgame)))
                              for n in notes)
        self.report({"WARNING"} if warn else {"INFO"}, msg)
        return {"FINISHED"}


def action_of(arm):
    ad = arm.animation_data
    return ad.action if ad else None


def prb_ds2_tracks(scene, template, bone_ids, tracks, frames):
    """(keys per track, hashes, flags) of a Desert Storm II animation from the per-object keys sampled off the
    armature. With a game animation as template: its tracks in its order and with its flags - objects of this model
    get the new keys, tracks for objects this body doesn't have (the game binds by hash: another body's connector) keep
    the template's keys, resampled to the new length. Without one: the bones (skin bones and their parents, file
    order) plus the connectors hanging on them - like the game's own (534_POLY_SOLDIER: 29 of its 40 objects;
    ROOT_OBJECT, the eyes and hit regions have none)."""
    if template is not None and template.hashes is not None:
        by_hash = {o.node.hash: o.index for o in scene.objects}
        if any(h in by_hash for h in template.hashes):
            out = []
            for t, h in enumerate(template.hashes):
                if h in by_hash:
                    out.append(tracks[by_hash[h]])
                else:
                    old = template.tracks[t]
                    out.append(old if len(old) == 1 else
                               [old[min(len(old) - 1, int(round(f * (len(old) - 1) / max(1, frames - 1))))]
                                for f in range(frames)])
            return out, list(template.hashes), list(template.track_flags)
    root = next((o.index for o in scene.objects if o.parent is None and o.name.upper() == "ROOT_OBJECT"), None)
    sel = [o.index for o in scene.objects if o.index != root and
           (o.index in bone_ids or (o.role == "connector" and o.parent in bone_ids))]
    return ([tracks[i] for i in sel], [scene.objects[i].node.hash for i in sel],
            [evo_write.prb_track_flags(scene.objects[i].name) for i in sel])


def prb_cv_tracks(scene, template, bone_ids, tracks, frames):
    """(rest-relative deltas per track, hashes, flags) of a Vietnam animation from the per-object keys sampled off the
    armature (evo_write.cv_deltas; flag 4 where a bone leaves its rest position). With a Vietnam game animation as
    template: its tracks in its order and flags - this model's objects get the new keys, tracks for objects it doesn't
    have keep the template's deltas, resampled to the new length. Without one: what the game's own animations carry
    (all 257 that fit MPD_US_MOTOWN): ROOT_OBJECT (the walk's travel - 256 of them), the body bones and the connectors
    on them; never the face (jaw, eyes, teeth, mouth, eyebrow, eyelid bones)."""
    face = ("JAW", "EYE", "TEETH", "MOUTH", "BROW", "LID")
    def resample(old):
        return old if len(old) == 1 else [old[min(len(old) - 1, int(round(f * (len(old) - 1) / max(1, frames - 1))))]
                                          for f in range(frames)]

    if template is not None and template.game == "cv" and template.deltas is not None:
        by_hash = {o.node.hash: o.index for o in scene.objects}
        mine = [t for t, h in enumerate(template.hashes) if h in by_hash]
        if mine:
            conv, bits = evo_write.cv_deltas(scene, [(by_hash[template.hashes[t]], tracks[by_hash[template.hashes[t]]])
                                                     for t in mine])
            new = dict(zip(mine, zip(conv, bits)))
            deltas, flags = [], list(template.track_flags)
            for t in range(len(template.hashes)):
                if t in new:
                    deltas.append(new[t][0])
                    flags[t] = (flags[t] & ~4) | new[t][1]
                else:
                    deltas.append(resample(template.deltas[t]))
            return deltas, list(template.hashes), flags
    sel = [o.index for o in scene.objects if not any(w in (o.name or "").upper() for w in face) and
           (o.index in bone_ids or (o.role == "connector" and o.parent in bone_ids) or
            (o.parent is None and (o.name or "").upper() == "ROOT_OBJECT"))]
    conv, bits = evo_write.cv_deltas(scene, [(i, tracks[i]) for i in sel])
    return (conv, [scene.objects[i].node.hash for i in sel],
            [evo_write.prb_track_flags(scene.objects[i].name) | b for i, b in zip(sel, bits)])


class EXPORT_ANIM_OT_prb(bpy.types.Operator, ExportHelper):
    """Write the armature's current action as a Conflict: Desert Storm animation (.prb)"""
    bl_idname = "export_anim.evo_prb"
    bl_label = "Export PRB Animation"
    bl_options = {"REGISTER"}

    game: game_prop()
    filename_ext = ".prb"
    filter_glob: StringProperty(default="*.prb;*.PRB", options={"HIDDEN"})
    fps: FloatProperty(name="Keys per Second", default=15.0, min=1.0, max=60.0,
                       description="How often the animation is sampled (the game's own use 6-15)")
    template: StringProperty(name="Footsteps From", subtype="FILE_PATH",
                             description="A game animation whose footstep events (walk / run cycles) the new one "
                                         "keeps when the action has no event markers. Empty = the game animation "
                                         "with the same name, if there is one")
    markers: BoolProperty(name="Events from Markers", default=True,
                          description="The action's pose markers (FS_LEFT, FS_RIGHT, FS_LEFT_25 for a run step, "
                                      "EVENT_2 ...) become the footstep events - the import makes them from the "
                                      "game's. Without markers the events come from Footsteps From")

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob is not None and ob.type == "ARMATURE" and ob.get("evo_path") and action_of(ob) is not None

    def invoke(self, context, event):
        apply_game(self)
        act = action_of(context.active_object)
        if act is not None:
            if act.get("evo_fps"):
                self.fps = float(act["evo_fps"])
            if not self.filepath or os.path.basename(self.filepath) in ("", "untitled.prb"):
                self.filepath = act.name + (".ani" if self.game == "cg" else ".prb")
        if self.game == "cg":
            self.filter_glob = "*.ani;*.ANI"
        return ExportHelper.invoke(self, context, event)

    def check(self, context):
        # ExportHelper.check would add .prb to a Global Storm "<name>.ani" (all-games add-on: one class, five games)
        ext = ".ani" if self.game == "cg" else ".prb"
        path = bpy.path.ensure_ext(os.path.splitext(self.filepath)[0] if self.filepath.lower().endswith(
            (".prb", ".ani")) else self.filepath, ext) if self.filepath else self.filepath
        if path != self.filepath:
            self.filepath = path
            return True
        return False

    def execute(self, context):
        apply_game(self)
        arm = context.active_object
        act = action_of(arm)
        k = arm.get("evo_scale", 0.01)
        try:
            scene = evo.load(arm["evo_path"])
        except (evo.EvoError, OSError) as e:
            self.report({"ERROR"}, "Can't read the character's model %s: %s" % (arm["evo_path"], e))
            return {"CANCELLED"}
        wrong = wrong_game(model_game(arm["evo_path"]), (arm.name, "skeleton"))
        if wrong:
            self.report({"ERROR"}, wrong)
            return {"CANCELLED"}
        bones = arm.data.bones
        bone_ids = [i for i in sorted(scene.bone_ids) if scene.objects[i].name in bones]
        rest = {i: bones[scene.objects[i].name].matrix_local.copy() for i in bone_ids}
        corr_inv = {i: (conv_matrix(scene.objects[i].world, k).inverted() @ rest[i]).inverted() for i in bone_ids}
        # the template animation: footstep events, keys for objects that aren't bones
        tpath = bpy.path.abspath(self.template) if self.template else ""
        if not tpath:
            finder = evo.AssetFinder(arm["evo_path"])
            tpath = finder.animations().get(act.name.upper()) or finder.animations().get(act.name) or ""
            if not tpath:
                for stem, p in finder.animations().items():
                    if stem.lower() == act.name.lower():
                        tpath = p
                        break
        template = None
        if tpath and os.path.isfile(tpath):
            try:
                tdata = open(tpath, "rb").read()
                # a Global Storm .ani has no footstep events (its template gives the root motion and the name)
                template = (evo.read_prb(tdata), None if evo.is_ani_cg(tdata) else evo_write.prb_events(tdata))
            except evo.EvoError:
                template = None
        scn = context.scene
        scene_fps = scn.render.fps / (scn.render.fps_base or 1.0)
        start, end = act.frame_range
        frames = max(1, int(round((end - start) * self.fps / scene_fps)) + 1)
        keep = scn.frame_current
        tracks = [[] for _ in scene.objects]
        points = []
        try:
            for f in range(frames):
                t = start + f * scene_fps / self.fps
                scn.frame_set(int(t), subframe=t - int(t))
                world = {}

                def world_of(i, depth=0):
                    if i in world:
                        return world[i]
                    so = scene.objects[i]
                    if i in bone_ids:
                        pb = arm.pose.bones[so.name]
                        world[i] = conv_matrix(pb.matrix @ corr_inv[i], 1.0 / k)
                    else:
                        parent = world_of(so.parent, depth + 1) if so.parent is not None and depth < 256 else Matrix.Identity(4)
                        local = Matrix([list(r) for r in so.local])
                        ti = template[0].tracks_for(scene)[i] if template and evo.compatible(scene, template[0]) \
                            else None
                        if ti is not None:
                            local = Matrix([list(r) for r in template[0].local(ti, min(f, template[0].frames - 1))])
                        world[i] = parent @ local
                    return world[i]

                for i, so in enumerate(scene.objects):
                    w = world_of(i)
                    parent = world_of(so.parent) if so.parent is not None else Matrix.Identity(4)
                    loc, rot, sca = (parent.inverted() @ w).decompose()
                    tracks[i].append((tuple(loc), tuple(sca), (rot.x, rot.y, rot.z, rot.w)))
                    if i in bone_ids:
                        points.append(tuple(w.translation))
        finally:
            scn.frame_set(keep)
        marked, unknown = marker_events(act, start, scene_fps, self.fps) if self.markers else ([], [])
        if marked:
            events, nevents = evo_write.pack_events([e for e in marked if e[1] < frames])
            args = (events, nevents)
        elif template and template[1]:
            events, nevents, flag, tframes = template[1]
            args = (events, nevents, flag, tframes)
        else:
            args = ()
        agame = MODEL_GAMES.get(scene.nodes[0].version & 0xFFFF)
        if agame not in ANIM_GAMES:
            self.report({"ERROR"}, "%s animations can't be written yet" % games.GAMES[agame].short if agame else
                        "the armature's model is from an unknown game")
            return {"CANCELLED"}
        root_from = ""
        if agame == "cg":
            # Global Storm: tracks per .SKL bone (cg_tracks: the skeleton file's local rotations / position offsets);
            # the game loads Animations\<name>.ani by the name inside, so a game animation of the same name keeps
            # its name and its root motion (30 Hz samples)
            try:
                rot, pos, const = evo_write.cg_tracks(scene, tracks)
            except evo.EvoError as e:
                self.report({"ERROR"}, str(e))
                return {"CANCELLED"}
            tcg = template[0].cg if template and template[0].cg is not None else None
            if tcg is not None:
                root_from = os.path.basename(tpath)
            out = evo_write.write_ani_cg(tcg["name_raw"] if tcg else act.name, (frames - 1) / self.fps if frames > 1
                                         else (tcg["duration"] if tcg else 0.0), rot, pos, const,
                                         tcg["root"] if tcg else (), rot_frames=frames)
            if self.filepath.lower().endswith(".prb"):
                self.filepath = self.filepath[:-4] + ".ani"
        elif agame == "cv":
            # Vietnam: by name hash like DS2, keys relative to each bone's rest rotation (rotation vectors, positions
            # only for tracks that move a bone off its rest position)
            deltas, hashes, flags = prb_cv_tracks(scene, template[0] if template else None, bone_ids, tracks, frames)
            out = evo_write.write_prb_cv(deltas, self.fps, points, hashes, flags, *args)
        elif agame in evo_write.PRB_BY_HASH:
            # Desert Storm II / The Great Escape: tracks by object name hash, for the objects the game's animations of
            # this skeleton carry (the template's, else the bones and connectors), in that order; The Great Escape's
            # keys keep a scale (40 bytes)
            keys, hashes, flags = prb_ds2_tracks(scene, template[0] if template else None, bone_ids, tracks, frames)
            out = evo_write.write_prb_ds2(keys, self.fps, points, hashes, flags, *args,
                                          key_size=40 if agame == "ge" else 28)
        else:
            out = evo_write.write_prb(tracks, self.fps, points, *args)
        with open(self.filepath, "wb") as f:
            f.write(out)
        msg = "Wrote %s: %d frames at %g per second" % (os.path.basename(self.filepath), frames, self.fps)
        if marked:
            msg += ", %d event(s) from the timeline markers" % nevents
            if nevents < len(marked):
                msg += " (%d after the last frame left out)" % (len(marked) - nevents)
        elif root_from:
            msg += ", name and root motion from %s" % root_from
        elif template and template[1]:
            msg += ", footsteps from %s" % os.path.basename(tpath)
        if unknown:
            msg += "; markers that aren't events (use FS_LEFT, FS_RIGHT, FS_LEFT_25, EVENT_2 ...): " + ", ".join(unknown)
        self.report({"WARNING"} if unknown else {"INFO"}, msg)
        return {"FINISHED"}


def menu_func_export(self, context):
    for gid, short, _ in MENU_PROFILES:                     # one entry per game: the entry decides the layout checks
        menu = MENU_NAMES[gid]
        if gid in WRITE_GAMES:
            self.layout.operator(EXPORT_SCENE_OT_evo.bl_idname, text="%s (.evo)" % menu).game = gid
        if gid in SCENE_WRITE_GAMES:
            self.layout.operator(EXPORT_SCENE_OT_ds_env.bl_idname, text="%s Scene (.env)" % menu).game = gid
        if gid in ANIM_GAMES:
            self.layout.operator(EXPORT_ANIM_OT_prb.bl_idname,
                                 text="%s Animation (%s)" % (menu, ".ani" if gid == "cg" else ".prb")).game = gid


def menu_func_import(self, context):
    for gid, short, _ in MENU_PROFILES:                     # one entry per game: folders / mod are that game's
        lay = self.layout
        menu = MENU_NAMES[gid]
        lay.operator(IMPORT_SCENE_OT_evo.bl_idname, text="%s (.evo)" % menu).game = gid
        if gid in ANIM_IMPORT_GAMES:
            lay.operator(IMPORT_ANIM_OT_prb.bl_idname,
                         text="%s Animation (%s)" % (menu, ".ani" if gid == "cg" else ".prb")).game = gid
        lay.operator(IMPORT_SCENE_OT_ds_env.bl_idname, text="%s Scene (.env)" % menu).game = gid
        lay.operator(IMPORT_SCENE_OT_ds_extract.bl_idname, text="%s Archives (extract)" % menu).game = gid
    if GAME in (None, "ds1"):                               # both into Desert Storm (the GameCube disc is its own)
        self.layout.operator(IMPORT_SCENE_OT_ds_gc_audio.bl_idname,
                             text="Conflict: Desert Storm GameCube Audio (.iso)")
        self.layout.operator(IMPORT_SCENE_OT_ds_port.bl_idname,
                             text="Conflict: Desert Storm - Port From Another Game (recipe)" if GAME else
                             "Conflict: Port Between Games (recipe)")


# ----------------------------------------------------------------------------------------------- side panel --
def _helper_target(context, ob):
    """Collection for a new helper: the model's 'helpers' collection if it has one, else the object's own."""
    for coll in ob.users_collection:
        for child in coll.children:
            if child.name.startswith(HELPER_COLLECTION):
                return child
    return ob.users_collection[0] if ob.users_collection else context.scene.collection


def _set_surface(ob, index, faces=None):
    """Give faces of a mesh object (all, or the indices in `faces`) the surface material `index`."""
    mat = surface_material(index)
    slots = [s.material for s in ob.material_slots]
    if mat not in slots:
        ob.data.materials.append(mat)
        slots.append(mat)
    mi = slots.index(mat)
    for p in ob.data.polygons:
        if faces is None or p.index in faces:
            p.material_index = mi


class OBJECT_OT_ds_assign_surface(bpy.types.Operator):
    """Give the selected faces (Edit Mode) or the whole selected objects the chosen collision surface"""
    bl_idname = "object.ds_assign_surface"
    bl_label = "Assign Surface"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == "MESH"

    def execute(self, context):
        index = int(props(context.scene).surface)
        edit = context.mode == "EDIT_MESH"
        if edit:
            bpy.ops.object.mode_set(mode="OBJECT")
        done = 0
        obs = [o for o in context.selected_objects if o.type == "MESH"] or [context.active_object]
        for ob in obs:
            faces = {p.index for p in ob.data.polygons if p.select} if edit else None
            if faces == set():
                continue
            _set_surface(ob, index, faces)
            done += len(faces) if faces is not None else len(ob.data.polygons)
        if edit:
            bpy.ops.object.mode_set(mode="EDIT")
        self.report({"INFO"}, "%s on %d faces" % (bsp.surface_name(index), done))
        return {"FINISHED"}


class OBJECT_OT_ds_make_collision(bpy.types.Operator):
    """Make a collision (#BSP_) or shadow (#SHA_) object from the active mesh: its box, its convex hull or a copy"""
    bl_idname = "object.ds_make_collision"
    bl_label = "Make Collision"
    bl_options = {"REGISTER", "UNDO"}

    shape: EnumProperty(name="Shape", default="HULL",
                        items=(("BOX", "Box", "The mesh's bounding box"),
                               ("HULL", "Convex Hull", "The smallest convex shape around the mesh - tight and simple"),
                               ("COPY", "Copy of the Mesh", "Every face of the mesh (detailed, slower in game)")))
    kind: EnumProperty(name="Kind", default="COLLISION",
                       items=(("COLLISION", "Collision", "#BSP_ object: what soldiers and bullets hit"),
                              ("SHADOW", "Shadow", "#SHA_ object: the shadow mesh (needs the original model to have one)")))

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == "MESH"

    def execute(self, context):
        import bmesh
        src = context.active_object
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bm = bmesh.new()
        if self.shape == "BOX":
            corners = [Vector(c) for c in src.bound_box]
            verts = [bm.verts.new(c) for c in corners]
            for f in ((0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)):
                bm.faces.new([verts[i] for i in f])
            bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        else:
            deps = context.evaluated_depsgraph_get()
            bm.from_object(src, deps)
            if self.shape == "HULL":
                hull = bmesh.ops.convex_hull(bm, input=bm.verts[:])
                bmesh.ops.delete(bm, geom=[g for g in hull["geom_interior"] + hull["geom_unused"]], context="VERTS")
                bmesh.ops.dissolve_limit(bm, angle_limit=0.0175, verts=bm.verts[:], edges=bm.edges[:])
                bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        prefix = "#BSP_" if self.kind == "COLLISION" else "#SHA_"
        name = prefix + base_name(src.name).removeprefix("#BSP_").removeprefix("#SHA_")
        me = bpy.data.meshes.new(name)
        bm.to_mesh(me)
        bm.free()
        me.materials.clear()
        ob = bpy.data.objects.new(name, me)
        _helper_target(context, src).objects.link(ob)
        ob.parent = src
        ob.matrix_parent_inverse = Matrix.Identity(4)
        ob.display_type = "SOLID" if self.kind == "COLLISION" and props(context.scene).show_surfaces else "WIRE"
        if self.kind == "COLLISION":
            ob["evo_role"] = "collision"
            _set_surface(ob, int(props(context.scene).surface))
        self.report({"INFO"}, "%s: %d faces%s" % (name, len(me.polygons),
                                                   ", " + bsp.surface_name(int(props(context.scene).surface))
                                                   if self.kind == "COLLISION" else ""))
        return {"FINISHED"}


class OBJECT_OT_ds_add_point(bpy.types.Operator):
    """Add a point (muzzle, shell ejection, ladder top ...) at the 3D cursor, parented to the active object"""
    bl_idname = "object.ds_add_point"
    bl_label = "Add Point"
    bl_options = {"REGISTER", "UNDO"}

    role: EnumProperty(name="Point", items=[(r, r.replace("_", " ").title(), "") for r in evo.POINT_ROLES] +
                       [(r, r.replace("_", " ").title() + " (Aim Camera)", "Blender only: the Aim Camera looks from the "
                         "rear sight over the front sight; not written to the game") for r in SIGHT_ROLES])

    def execute(self, context):
        src = context.active_object
        ob = bpy.data.objects.new("#PT_" + self.role, None)
        (_helper_target(context, src) if src else context.scene.collection).objects.link(ob)
        ob.empty_display_type = "SINGLE_ARROW" if self.role in ("MUZZLE", "LAUNCHER") else             "PLAIN_AXES" if self.role in SIGHT_ROLES else "SPHERE"
        ob.empty_display_size = 0.01 if self.role in SIGHT_ROLES else 0.03
        ob["evo_point_role"] = self.role
        ob.matrix_world = Matrix.Translation(context.scene.cursor.location)
        if src:
            mw = ob.matrix_world.copy()
            ob.parent = src
            ob.matrix_world = mw
        return {"FINISHED"}


def show_surfaces(scene, context=None):
    """Collision objects drawn solid in their surface colours (Show Collision Surfaces) or as wireframes."""
    on = props(scene).show_surfaces
    for ob in scene.objects:
        if ob.type == "MESH" and helper_kind(ob) == "collision":
            ob.display_type = "SOLID" if on else "WIRE"
    space = getattr(context, "space_data", None) if context else None
    if on and space is not None and space.type == "VIEW_3D" and space.shading.type == "SOLID":
        space.shading.color_type = "MATERIAL"       # the surface materials' viewport colours


def _surfaces_update(self, context):
    show_surfaces(self, context)


class OBJECT_OT_ds_pick_surface(bpy.types.Operator):
    """Take the collision surface of the active (or first selected) face as the chosen surface"""
    bl_idname = "object.ds_pick_surface"
    bl_label = "Pick Surface"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "EDIT_MESH" and context.active_object is not None

    def execute(self, context):
        import bmesh
        ob = context.edit_object
        bm = bmesh.from_edit_mesh(ob.data)
        face = bm.faces.active if bm.faces.active is not None and bm.faces.active.select else \
            next((f for f in bm.faces if f.select), None)
        if face is None:
            self.report({"ERROR"}, "Select a face")
            return {"CANCELLED"}
        mat = ob.material_slots[face.material_index].material if face.material_index < len(ob.material_slots) else None
        if mat is None:
            self.report({"ERROR"}, "The face has no material - it gets the export's Default Surface")
            return {"CANCELLED"}
        index, known = surface_of(mat, 0)
        if not known or not 0 <= index < len(bsp.ALL_SURFACES):
            self.report({"ERROR"}, "%s isn't a surface material (\"Surface: Metal\" or a surface name)" % mat.name)
            return {"CANCELLED"}
        props(context.scene).surface = str(index)
        self.report({"INFO"}, "Surface: " + bsp.surface_name(index))
        return {"FINISHED"}


class OBJECT_OT_ds_validate_skin_weights(bpy.types.Operator):
    """Check a character mesh's weights against the game: at most 4 bones per vertex (the strongest kept, weights
    normalised), every vertex weighted, vertex groups named after the armature's bones. Unweighted vertices are
    selected in Edit Mode"""
    bl_idname = "object.ds_validate_skin_weights"
    bl_label = "Check Skin Weights"
    bl_options = {"REGISTER", "UNDO"}

    clamp: BoolProperty(name="Clamp and Normalise", default=True,
                        description="Remove all but the strongest bones per vertex and scale the rest to add up to 1 "
                                    "(the export does the same quietly)")
    max_influences: IntProperty(name="Bones per Vertex", default=4, min=1, max=4,
                                description="The game blends at most 4 bones per vertex")

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob is not None and ob.type == "MESH"

    def execute(self, context):
        ob = context.active_object
        arm = armature_of(ob)
        if arm is None:
            self.report({"ERROR"}, "%s isn't bound to an armature (Armature modifier or armature parent)" % ob.name)
            return {"CANCELLED"}
        edit = context.mode == "EDIT_MESH"
        if edit:
            bpy.ops.object.mode_set(mode="OBJECT")
        bones = {b.name.upper() for b in arm.data.bones}
        bone_groups = {g.index for g in ob.vertex_groups if g.name.upper() in bones}
        stray = sorted(g.name for g in ob.vertex_groups if g.index not in bone_groups)
        missing = len(bones) - len({ob.vertex_groups[i].name.upper() for i in bone_groups})
        me = ob.data
        unweighted, over, normalised = [], 0, 0
        for v in me.vertices:
            ws = sorted(((g.group, g.weight) for g in v.groups if g.group in bone_groups and g.weight > 0),
                        key=lambda t: -t[1])
            if not ws:
                unweighted.append(v.index)
                continue
            if len(ws) > self.max_influences:
                over += 1
            if not self.clamp:
                continue
            keep, drop = ws[:self.max_influences], ws[self.max_influences:]
            for gi, _w in drop:
                ob.vertex_groups[gi].remove([v.index])
            total = sum(w for _g, w in keep)
            if abs(total - 1.0) > 1e-4:
                for gi, w in keep:
                    ob.vertex_groups[gi].add([v.index], w / total, "REPLACE")
                normalised += 1
        if unweighted:
            wanted = set(unweighted)
            for v in me.vertices:
                v.select = v.index in wanted
            for e in me.edges:
                e.select = all(i in wanted for i in e.vertices)
            for p in me.polygons:
                p.select = all(i in wanted for i in p.vertices)
        if edit or unweighted:
            bpy.ops.object.mode_set(mode="EDIT")
            if unweighted:
                bpy.ops.mesh.select_mode(type="VERT")
        msg = []
        if unweighted:
            msg.append("%d vertices have no bone weight (selected) - the game ties them to the root bone"
                       % len(unweighted))
        if over:
            msg.append("%d vertices had more than %d bones%s" % (over, self.max_influences,
                                                                 " - clamped" if self.clamp else ""))
        if normalised:
            msg.append("%d vertices normalised" % normalised)
        if stray:
            msg.append("vertex groups that aren't bones of %s (ignored by the export): %s"
                       % (arm.name, ", ".join(stray[:10]) + (" ..." if len(stray) > 10 else "")))
        if missing and not bone_groups:
            msg.append("no vertex group matches a bone")
        warn = bool(unweighted or stray or not bone_groups or (over and not self.clamp))
        self.report({"WARNING"} if warn else {"INFO"},
                    "; ".join(msg) if msg else "%s: %d vertices, all weighted, at most %d bones each"
                    % (ob.name, len(me.vertices), self.max_influences))
        return {"FINISHED"}


class OBJECT_OT_ds_quick_export(bpy.types.Operator):
    """Export the selected objects again into <game>\\Mods\\<Active Mod>\\<Model>.evo with the options of the last
    export (Based On, Scale, Collision, textures)"""
    bl_idname = "object.ds_quick_export"
    bl_label = "Export to Mod"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return props(context.scene).active_mod.strip() != "" and \
            any(o.type == "MESH" for o in context.selected_objects)

    def execute(self, context):
        scn = context.scene
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        last = scn.get(LAST_EXPORT)
        last = last.to_dict() if hasattr(last, "to_dict") else dict(last or {})
        name = props(scn).last_exported_model.strip() or base_name(context.active_object.name
                                                                if context.active_object else "MODEL")
        game_dir = game_dir_of(scn)                          # the profile's game
        opts = dict(filepath=os.path.join(bpy.path.abspath(game_dir) or tempfile.gettempdir(), name + ".evo"),
                    to_mod=True, mod_name=props(scn).active_mod.strip(), game_dir=game_dir, selected_only=True,
                    check_only=False,
                    base="GENERIC" if props(scn).generic_prop else str(last.get("base", "MODEL")),
                    template=str(last.get("template", "")), scale=float(last.get("scale", 0.01)),
                    collision=str(last.get("collision", "HELPERS")), surface=str(last.get("surface", "4")),
                    save_textures=bool(last.get("save_textures", True)),
                    replace_game=bool(last.get("replace_game", False)))
        return run_op(EXPORT_SCENE_OT_evo, "EXEC_DEFAULT", **opts)


def curve_points(ob, spacing):
    """World-space points every `spacing` (Blender units) along the curve's first spline."""
    from mathutils.geometry import interpolate_bezier
    if not ob.data.splines:
        return []
    sp = ob.data.splines[0]
    mw = ob.matrix_world
    pts = []
    if sp.type == "BEZIER":
        bp = list(sp.bezier_points)
        pairs = list(zip(bp, bp[1:])) + ([(bp[-1], bp[0])] if sp.use_cyclic_u and len(bp) > 1 else [])
        for a, b in pairs:
            seg = interpolate_bezier(a.co, a.handle_right, b.handle_left, b.co, 64)
            pts += seg if not pts else seg[1:]
    else:
        pts = [p.co.xyz for p in sp.points]
        if sp.use_cyclic_u and pts:
            pts.append(pts[0])
    pts = [mw @ Vector(p) for p in pts]
    if len(pts) < 2:
        return pts
    lengths = [0.0]
    for a, b in zip(pts, pts[1:]):
        lengths.append(lengths[-1] + (b - a).length)
    total = lengths[-1]
    steps = max(1, int(round(total / spacing)))
    out, seg = [], 0
    for s in range(steps + 1):
        d = total * s / steps                   # evenly spaced, both ends kept
        while seg < len(pts) - 2 and lengths[seg + 1] < d:
            seg += 1
        span = lengths[seg + 1] - lengths[seg]
        out.append(pts[seg].lerp(pts[seg + 1], (d - lengths[seg]) / span if span > 0 else 0.0))
    return out


WALKABLE_ITEMS = [(str(i), n, "Default.spl row %d (walkable ground)" % i) for i, n in enumerate(bsp.SURFACES)
                  if n in ("Tarmac", "Baked Earth", "Concrete", "Sand", "Rock", "Grass", "Marble Tile", "Metal", "Wood",
                           "Forest Floor", "River Bed")]


class OBJECT_OT_ds_generate_road(bpy.types.Operator):
    """Make a road from the active curve: a textured strip along it plus its #BSP_ collision (walkable surface).
    Export both with Based On: Auto / Generic Prop"""
    bl_idname = "object.ds_generate_road"
    bl_label = "Generate Road"
    bl_options = {"REGISTER", "UNDO"}

    width: FloatProperty(name="Width", default=6.0, min=0.5, max=200.0, unit="LENGTH",
                         description="Road width (metres at Scale 0.01)")
    spacing: FloatProperty(name="Sample Every", default=2.0, min=0.1, max=50.0, unit="LENGTH",
                           description="Distance between cross-sections along the curve")
    texture: StringProperty(name="Texture", default="M01_ROAD01",
                            description="Game texture by name (M01_ROAD01 ... from mission1), or your own image's name")
    tile: FloatProperty(name="Texture Repeat", default=6.0, min=0.1, max=1000.0, unit="LENGTH",
                        description="Length of road one copy of the texture covers")
    across: BoolProperty(name="Texture Runs Across", default=False,
                         description="The texture's road runs left-right (turn it 90 degrees)")
    surface: EnumProperty(name="Surface", items=WALKABLE_ITEMS, default=str(bsp.SURFACES.index("Tarmac")),
                          description="Collision surface: footstep sounds and bullet hits; walkable for the AI")
    lift: FloatProperty(name="Lift (cm)", default=6.0, min=0.0, max=100.0,
                        description="Raise the road this far above the curve so it doesn't flicker with the ground")
    scale: FloatProperty(name="Scale", default=0.01, min=1e-5, max=100.0,
                         description="Blender units per game centimetre (0.01 = metres)")

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == "CURVE"

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        src = context.active_object
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        k = self.scale
        pts = curve_points(src, self.spacing)
        if len(pts) < 2:
            self.report({"ERROR"}, "The curve is too short for a road")
            return {"CANCELLED"}
        surface = bsp.SURFACES[int(self.surface)]
        road = roads.Road([to_game_point(p, k) for p in pts], self.width / k, self.texture.strip() or "M01_ROAD01",
                          self.tile / k, surface, self.lift, self.across)
        pos, _nrm, uv, tris = road.strip()
        name = "ROAD_" + re.sub(r"[^A-Za-z0-9_]", "_", base_name(src.name)).upper()
        verts = [conv_point(p, k) for p in pos]
        faces = [(a, c, b) for a, b, c in tris]             # the axis mirror flips the winding (see build_mesh)
        me = bpy.data.meshes.new(name)
        me.from_pydata(verts, [], faces)
        loops = [0] * len(me.loops)
        me.loops.foreach_get("vertex_index", loops)
        layer = me.uv_layers.new(name="UVMap")
        layer.data.foreach_set("uv", [c for vi in loops for c in (uv[vi][0], 1.0 - uv[vi][1])])
        tex = road.texture
        finder = evo.AssetFinder(None, [os.path.join(DEFAULT_EXTRACT_DIR, d) for d in ("mission1", "frontend")])
        me.materials.append(get_material(tex, finder.find(tex), True))
        me.validate()
        me.update()
        coll = src.users_collection[0] if src.users_collection else context.scene.collection
        old = bpy.data.objects.get(name)
        if old is not None and old.get("ds_road_of") == src.name:      # regenerate in place
            for child in list(old.children):
                bpy.data.objects.remove(child)
            bpy.data.objects.remove(old)
        ob = bpy.data.objects.new(name, me)
        ob["ds_road_of"] = src.name
        coll.objects.link(ob)
        # collision: the same strip, one walkable surface
        cme = bpy.data.meshes.new("#BSP_" + name)
        cme.from_pydata(verts, [], faces)
        cme.update()
        col = bpy.data.objects.new("#BSP_" + name, cme)
        _helper_target(context, ob).objects.link(col)
        col.parent = ob
        col["evo_role"] = "collision"
        _set_surface(col, int(self.surface))
        col.display_type = "SOLID" if props(context.scene).show_surfaces else "WIRE"
        for o in context.selected_objects:
            o.select_set(False)
        ob.select_set(True)
        col.select_set(True)
        context.view_layer.objects.active = ob
        props(context.scene).last_exported_model = name
        props(context.scene).generic_prop = True
        length = sum((b - a).length for a, b in zip(pts, pts[1:]))
        self.report({"INFO"}, "%s: %d triangles over %.1f, collision %s - export with Based On: Auto / Generic Prop "
                              "(the model's origin is the world origin)" % (name, len(faces), length, surface))
        return {"FINISHED"}


# ------------------------------------------------------------------------------------------- level scenes --

def game_dir_of(scene):
    """The profile's game folder: its Game Folder, else the registry's / Steam's. "" when neither has that game's exe.
    The profile's game is the current one (games.current())."""
    game = profile()
    own = props(scene).game_dir
    for d in (bpy.path.abspath(own) if own else "", archive.find_game_dir(game) or ""):
        if d and games.detect(d) is game:
            return d
    return ""


def archive_dirs():
    """The per-archive folders of every known extraction (mission1, chardata ...)."""
    out = []
    for root in extracted_roots():
        try:
            out += [os.path.join(root, d) for d in sorted(os.listdir(root))
                    if not d.startswith("_") and os.path.isdir(os.path.join(root, d))]
        except OSError:
            pass
    return out


def _exclude(view_layer, coll, on=True):
    def walk(lc):
        if lc.collection == coll:
            lc.exclude = on
            return True
        return any(walk(c) for c in lc.children)
    walk(view_layer.layer_collection)


def _clamp01(c):
    return tuple(max(0.0, min(1.0, float(x))) for x in c)


class SceneImporter:
    """A level scene (.ENV, or an editor mod's .envpatch) as Blender objects: each model once in a library collection
    (excluded from the view layer), every prop / object a collection instance of it at its record's transform;
    lights, regions and ambient sounds as lights and empties. Records keep their scene line as ds_line."""

    def __init__(self, context, path, opts):
        self.context, self.path, self.o = context, path, opts
        self.k = opts["scale"]
        self.models, self.notes, self.missing_models, self.missing_textures = {}, [], set(), set()

    def run(self):
        folder = os.path.dirname(os.path.abspath(self.path))
        extra = [bpy.path.abspath(self.o["texture_dir"])] if self.o["texture_dir"] else []
        self.finder = evo.AssetFinder(self.path, extra + archive_dirs())
        self.rfx = evo.read_rfx(self.finder) if self.o["normal_maps"] else None
        env = levelscene.read_scene(self.path, self.finder)
        items = levelscene.scene_items(env)
        self.uses = levelscene.folder_uses(folder)
        level = os.path.splitext(os.path.basename(self.path))[0]
        root = bpy.data.collections.new(level)
        self.context.collection.children.link(root)
        root["ds_env_path"] = os.path.abspath(self.path)
        root["ds_level"] = level.upper()
        root["evo_scale"] = self.k
        ext = levelscene.extent(items)
        if ext:
            root["ds_extent"] = [ext[0][0], ext[0][1], ext[1][0], ext[1][1]]
        self.lib = bpy.data.collections.new(level + " models")
        root.children.link(self.lib)
        try:
            groups = [("props", "World", items["props"])]
            if self.o["objects"]:
                groups.append(("objects", "Objects", items["objects"]))
            todo = [it for _k, _n, lst in groups for it in lst
                    if self.o["zones"] or it.get("type") not in ZONE_TYPES]
            wm = self.context.window_manager
            wm.progress_begin(0, max(1, len(todo)))
            done = 0
            for _key, cname, lst in groups:
                coll = bpy.data.collections.new(cname)
                root.children.link(coll)
                for it in lst:
                    if it.get("type") in ZONE_TYPES and not self.o["zones"]:
                        continue
                    self.place(coll, it)
                    done += 1
                    wm.progress_update(done)
            wm.progress_end()
            if self.o["lights"] and items["lights"]:
                coll = bpy.data.collections.new("Lights")
                root.children.link(coll)
                for it in items["lights"]:
                    self.light(coll, it)
            if self.o["regions"] and items["regions"]:
                coll = bpy.data.collections.new("Regions")
                root.children.link(coll)
                coll.hide_render = True
                for it in items["regions"]:
                    self.region(coll, it)
            if self.o["sounds"] and items["sounds"]:
                coll = bpy.data.collections.new("Sounds")
                root.children.link(coll)
                coll.hide_render = True
                for it in items["sounds"]:
                    self.sound(coll, it)
        finally:
            _exclude(self.context.view_layer, self.lib)        # the originals sit at the origin: instances only
        root["ds_lines"] = sorted({int(ob["ds_line"]) for ob in root.all_objects if "ds_line" in ob})
        n = {k: len(v) for k, v in items.items()}
        self.notes.insert(0, "%d props, %d objects, %d lights, %d regions, %d sounds; %d models"
                          % (n["props"], n["objects"], n["lights"], n["regions"], n["sounds"],
                             sum(1 for c in self.models.values() if c)))
        return root

    # -- models --
    def model_path(self, name):
        """Path of a model: its file, a .uses alias, or a kit piece cut out of its source (cached in the temp folder;
        its textures sit in the source's archive folder, which the scene's finder searches anyway)."""
        key = name.upper()
        path = self.finder.find(name, (".evo",))
        if path:
            return path
        src = self.uses.get(key)
        if src and src[1] is None:
            return self.finder.find(src[0], (".evo",))
        kit = src or levelscene.kit_from_name(name)
        if not kit:
            return None
        spath = self.finder.find(kit[0], (".evo",))
        if not spath:
            return None
        cache = os.path.join(tempfile.gettempdir(), "conflict_ds_tools", "kits", key + ".EVO")
        if not os.path.isfile(cache):
            with open(spath, "rb") as f:
                data = kits.extract(f.read(), kit[1], key)
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            with open(cache, "wb") as f:
                f.write(data)
        return cache

    def model(self, name):
        key = name.upper()
        if key in self.models:
            return self.models[key]
        self.models[key] = None
        try:
            path = self.model_path(name)
        except (evo.EvoError, OSError) as e:
            self.notes.append("%s: %s" % (name, e))
            path = None
        if not path:
            self.missing_models.add(name)
            return None
        opts = dict(EDITABLE_OPTS, scale=self.k, helpers=False, normal_maps=self.o["normal_maps"], finder=self.finder,
                    rfx=self.rfx)
        try:
            with self.context.temp_override(collection=self.lib):
                imp = EvoImporter(bpy.context, path, opts)
                coll = imp.run()
        except (evo.EvoError, OSError, ValueError) as e:
            self.notes.append("%s: %s" % (name, e))
            return None
        self.missing_textures |= imp.missing
        coll.name = key
        self.models[key] = coll
        return coll

    def place(self, coll, it):
        model = self.model(it["model"])
        ob = bpy.data.objects.new(it["name"], None)
        coll.objects.link(ob)
        if model is not None:
            ob.instance_type = "COLLECTION"
            ob.instance_collection = model
            ob.empty_display_size = 10 * self.k
        else:
            ob.empty_display_type = "CUBE"              # a missing model: a marker at its place
            ob.empty_display_size = 50 * self.k
        ob.matrix_world = conv_matrix(levelscene.record_matrix(it["pos"], it["rot"], it["scale"]), self.k)
        ob["ds_line"] = it["line"]
        ob["ds_kind"] = it["kind"]
        ob["ds_model"] = it["model"]
        ob["ds_zone"] = it["zone"]
        if it["kind"] == "HLOI":
            ob["ds_script_id"] = it.get("script_id") if isinstance(it.get("script_id"), int) else -1
            ob["ds_unit"] = it.get("unit", "")
        else:
            ob["ds_type"] = it["type"]

    # -- lights, regions, sounds --
    def light(self, coll, it):
        k = self.k
        ld = bpy.data.lights.new(it["name"], it["kind"])
        ld.color = _clamp01(it["colour"])
        if it["kind"] == "SUN":
            ld.energy = self.o["sun_strength"]
        elif it["kind"] == "POINT":
            r = it["radius"] * k                     # reach in Blender units: energy grows with its square
            ld.energy = 50.0 * max(r, 0.01) ** 2 * (0.01 / k) ** 2
            ld.shadow_soft_size = 0.1 * r
            if hasattr(ld, "use_custom_distance"):
                ld.use_custom_distance = True
                ld.cutoff_distance = max(r, 1e-4)
        else:
            outer = max(1.0, min(180.0, it["outer"] if it["outer"] > 0 else 45.0))
            ld.spot_size = math.radians(outer)
            ld.spot_blend = max(0.0, min(1.0, 1.0 - it["inner"] / outer)) if it["inner"] > 0 else 0.15
            r = it["range"] * k
            ld.energy = 50.0 * max(r, 0.01) ** 2 * (0.01 / k) ** 2
            if hasattr(ld, "use_custom_distance"):
                ld.use_custom_distance = True
                ld.cutoff_distance = max(r, 1e-4)
        ob = bpy.data.objects.new(it["name"], ld)
        coll.objects.link(ob)
        ob.location = conv_point(it["pos"], k)
        if "tilt" in it:
            d = conv_dir(levelscene.direction(it["tilt"], it["heading"]))
            ob.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
            ob["ds_tilt"], ob["ds_heading"] = it["tilt"], it["heading"]
        if "radius" in it:
            ob["ds_radius"] = it["radius"]
        ob["ds_line"] = it["line"]
        ob["ds_kind"] = {"SUN": "DLGT", "POINT": "PLGT", "SPOT": "SLGT"}[it["kind"]]
        ob["ds_ambient"] = list(it["ambient"])

    def region(self, coll, it):
        k = self.k
        ob = bpy.data.objects.new(it["name"], None)
        coll.objects.link(ob)
        if it["shape"] == "SPHERE":
            ob.empty_display_type = "SPHERE"
            ob.empty_display_size = it["radius"] * k
            ob.location = conv_point(it["pos"], k)
        else:
            ob.empty_display_type = "CUBE"
            ob.empty_display_size = 1.0
            sx, sy, sz = (s / 2 * k for s in it["size"])
            m = conv_matrix(levelscene.record_matrix(it["pos"], it["rot"]), k) @ Matrix.Translation(conv_point(it["centre"], k))
            ob.matrix_world = m @ Matrix.Diagonal((max(sx, 1e-4), max(sz, 1e-4), max(sy, 1e-4), 1.0))
        ob.show_name = True
        ob["ds_region_name"] = it["name"]
        ob["ds_region_shape"] = it["shape"]
        ob["ds_line"] = it["line"]
        ob["ds_kind"] = "REGN"

    def sound(self, coll, it):
        k = self.k
        ob = bpy.data.objects.new(it["name"] or it["sound"], None)
        coll.objects.link(ob)
        ob.empty_display_type = "SPHERE"
        ob.empty_display_size = max(it["max_distance"], 1.0) * k     # silent past this
        ob.location = conv_point(it["pos"], k)
        for key in ("sound", "min_distance", "max_distance", "volume", "loop", "line"):
            ob["ds_" + key] = it[key]
        ob["ds_kind"] = "ENVSND"
        if it["min_distance"] > 0:                                    # full volume inside this
            inner = bpy.data.objects.new((it["name"] or it["sound"]) + " (full volume)", None)
            coll.objects.link(inner)
            inner.empty_display_type = "SPHERE"
            inner.empty_display_size = it["min_distance"] * k
            inner.parent = ob


class IMPORT_SCENE_OT_ds_env(bpy.types.Operator, ImportHelper):
    """Import a Conflict: Desert Storm level scene (.ENV, or an editor mod's .envpatch): world pieces, props, kit
    pieces, objects, lights, trigger regions and ambient sounds at their places"""
    bl_idname = "import_scene.ds_env"
    bl_label = "Import Scene"
    bl_options = {"REGISTER", "UNDO"}

    game: game_prop()

    filename_ext = ".env"
    filter_glob: StringProperty(default="*.env;*.ENV;*.envpatch;*.ENVPATCH", options={"HIDDEN"})
    scale: FloatProperty(name="Scale", default=0.01, min=1e-5, max=100.0,
                         description="Game units are centimetres; 0.01 imports in metres")
    zones: BoolProperty(name="World Pieces", default=True,
                        description="The level's ground and buildings (zone models): large - off for a quick look "
                                    "at the props")
    objects: BoolProperty(name="Objects", default=True,
                          description="Soldiers, vehicles, crates, doors (the scene's HLOI records)")
    lights: BoolProperty(name="Lights", default=True, description="Sun, point and spot lights")
    regions: BoolProperty(name="Trigger Regions", default=True,
                          description="The mission script's regions as wireframe boxes / spheres (named like the region)")
    sounds: BoolProperty(name="Ambient Sounds", default=True,
                         description="Positional sounds as empties: outer sphere = silent past it, inner = full volume")
    normal_maps: BoolProperty(name="Normal Maps", default=False,
                              description="Add the .RFX normal maps (slower; the shape of the level shows without)")
    sun_strength: FloatProperty(name="Sun Strength", default=3.0, min=0.0, max=100.0)
    texture_dir: StringProperty(name="Extra Folder", default="", subtype="DIR_PATH",
                                description="Extra folder to search for models and textures (the scene's folder, its "
                                            "sibling folders and the extracted archives are always searched)")

    def execute(self, context):
        apply_game(self)
        if context.object and context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        opts = {n: getattr(self, n) for n in ("scale", "zones", "objects", "lights", "regions", "sounds",
                                              "normal_maps", "sun_strength", "texture_dir")}
        imp = SceneImporter(context, self.filepath, opts)
        try:
            imp.run()
        except (OSError, ValueError, evo.EvoError) as e:
            self.report({"ERROR"}, "%s: %s" % (os.path.basename(self.filepath), e))
            return {"CANCELLED"}
        msg = list(imp.notes)
        if imp.missing_models:
            msg.append("models not found (extract the game archives, or the mod's own files are missing): "
                       + ", ".join(sorted(imp.missing_models)[:12]) + (" ..." if len(imp.missing_models) > 12 else ""))
        if imp.missing_textures:
            msg.append("%d textures not found" % len(imp.missing_textures))
        self.report({"WARNING"} if imp.missing_models else {"INFO"}, "; ".join(msg))
        return {"FINISHED"}


# ---------------------------------------------------------------------------------------------- scene export --

def to_game_matrix(m, k):
    """Game-space 4x4 (rows, cm) of a Blender world matrix - the inverse of conv_matrix."""
    out = [[0.0] * 4 for _ in range(3)] + [[0.0, 0.0, 0.0, 1.0]]
    for i, (a, s) in enumerate(AXES):
        for j, (b, t) in enumerate(AXES):
            out[a][b] = m[i][j] * s * t
        out[a][3] = m[i][3] * s / k
    return out


def _model_name(name):
    return re.sub(r"[^A-Z0-9_]", "_", base_name(name).upper())[:31] or "NEW_PROP"


class EXPORT_SCENE_OT_ds_env(bpy.types.Operator, ExportHelper):
    """Write the imported level scene back with your changes: props and objects moved, turned, scaled, deleted or
duplicated; new props for new meshes (written as models with their textures next to the scene) and for added model
instances; lights, sounds and regions at their new places. Everything else in the scene stays as it was"""
    bl_idname = "export_scene.ds_env"
    bl_label = "Export Scene"
    bl_options = {"REGISTER"}

    game: game_prop()
    filename_ext = ".env"
    filter_glob: StringProperty(default="*.env;*.ENV", options={"HIDDEN"})
    new_models: BoolProperty(name="Export New Meshes as Models", default=True,
                             description="Meshes that aren't from the scene become models (.evo, their textures as "
                                         ".dds) next to the scene file, each placed as a new prop")
    collision: EnumProperty(name="New Models Collide", default="MESH",
                            items=(("MESH", "With Their Own Mesh", "Soldiers, bullets and vehicles hit them"),
                                   ("NONE", "Not at All", "Decoration only")),
                            description="Collision of the models made from new meshes")

    @classmethod
    def poll(cls, context):
        return level_root(context) is not None

    def invoke(self, context, event):
        root = level_root(context)
        if root is not None and not self.filepath:
            self.filepath = os.path.join(os.path.dirname(root.get("ds_env_path", "")), root["ds_level"] + ".ENV")
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        apply_game(self)
        root = level_root(context)
        if root is None or not os.path.isfile(root.get("ds_env_path", "")):
            self.report({"ERROR"}, "Import the level scene first (File > Import > ... Scene (.env)): the export "
                                   "writes your changes into it")
            return {"CANCELLED"}
        if context.object and context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        k = float(root.get("evo_scale", 0.01))
        env = levelscene.read_scene(root["ds_env_path"], evo.AssetFinder(root["ds_env_path"], archive_dirs()))
        lib = next((c for c in root.children if c.name.endswith(" models")), None)
        lib_objs = set(lib.all_objects) if lib else set()
        lib_names = {c.name for c in lib.children} if lib else set()
        by_line, news, insts = {}, [], []
        for ob in root.all_objects:
            if ob in lib_objs:
                continue
            if "ds_line" in ob:
                by_line.setdefault(int(ob["ds_line"]), []).append(ob)
            elif ob.type == "MESH" and ob.parent is None:
                news.append(ob)
            elif ob.type == "EMPTY" and ob.instance_type == "COLLECTION" and ob.instance_collection is not None \
                    and ob.instance_collection.name in lib_names:
                insts.append(ob)
        moves, copies, aims = {}, [], {}
        for line, obs in by_line.items():
            # the object without a .001 suffix is the record itself, the others are duplicates (Shift+D)
            obs.sort(key=lambda o: (re.search(r"\.\d{3}$", o.name) is not None, o.name))
            first, rest = obs[0], obs[1:]
            kind = first.get("ds_kind", "")
            t = to_game_matrix(first.matrix_world, k)
            if kind in ("ENVI", "HLOI"):
                moves[line] = t
                copies += [(line, to_game_matrix(o.matrix_world, k)) for o in rest]
            elif kind == "REGN" and first.get("ds_region_shape") != "SPHERE":
                # a box is drawn at record position + rotated centre: take the centre back off
                rec = env.record_at(line)
                rot = tuple(float(c) for c in rec["rot"]) if "rot.x" in rec.fields else (0.0, 0.0, 0.0)
                item = levelscene.region(rec)
                r = levelscene.rotation(rot)
                c = item["centre"] if item else (0.0, 0.0, 0.0)
                pos = [t[i][3] - sum(r[i][j] * c[j] for j in range(3)) for i in range(3)]
                moves[line] = [[1, 0, 0, pos[0]], [0, 1, 0, pos[1]], [0, 0, 1, pos[2]], [0, 0, 0, 1]]
            elif kind in ("REGN", "ENVSND", "DLGT", "PLGT", "SLGT"):
                moves[line] = t
                if kind in ("DLGT", "SLGT"):
                    d = first.matrix_world.to_3x3() @ Vector((0.0, 0.0, -1.0))     # a Blender light shines along -Z
                    g = [0.0, 0.0, 0.0]
                    for i, (a, s) in enumerate(AXES):
                        g[a] = d[i] * s
                    aims[line] = tuple(g)
        imported = {int(x) for x in root.get("ds_lines", [])}
        deletes = imported - set(by_line)
        props = [(ob.instance_collection.name, to_game_matrix(ob.matrix_world, k)) for ob in insts]
        out_dir = os.path.dirname(bpy.path.abspath(self.filepath))
        made = []
        if self.new_models and news:
            sel = list(context.selected_objects)
            active = context.view_layer.objects.active
            for ob in news:
                model = _model_name(ob.name)
                world = ob.matrix_world.copy()
                for o in context.selected_objects:
                    o.select_set(False)
                ob.select_set(True)
                context.view_layer.objects.active = ob
                ob.matrix_world = Matrix.Identity(4)        # the model at its own origin, the prop record places it
                try:
                    r = run_op(EXPORT_SCENE_OT_evo, filepath=os.path.join(out_dir, model + ".evo"), game=self.game,
                                                 base="GENERIC", selected_only=True, scale=k,
                                                 collision=self.collision, save_textures=True)
                except RuntimeError as e:
                    self.report({"WARNING"}, "%s not exported: %s" % (ob.name, e))
                    r = {"CANCELLED"}
                finally:
                    ob.matrix_world = world
                if "FINISHED" in r:
                    props.append((model, to_game_matrix(world, k)))
                    made.append(model)
            for o in context.selected_objects:
                o.select_set(False)
            for o in sel:
                o.select_set(True)
            context.view_layer.objects.active = active
        elif news:
            self.report({"WARNING"}, "%d new mesh(es) left out (Export New Meshes as Models is off)" % len(news))
        try:
            notes = sceneexport.apply(env, moves, copies, deletes, props, aims)
            written = sceneexport.write(env, bpy.path.abspath(self.filepath))
        except (ValueError, KeyError, OSError) as e:
            self.report({"ERROR"}, "Scene not written: %s" % e)
            return {"CANCELLED"}
        msg = notes[0] + " -> " + os.path.basename(written[0])
        if len(written) > 1:
            msg += " (+%d part files)" % (len(written) - 1)
        if made:
            msg += "; new models: " + ", ".join(made)
        if not imported:
            msg += "; deletions not written (imported by an older add-on - import the scene again)"
        if notes[1:]:
            msg += "; " + "; ".join(notes[1:4])
        self.report({"WARNING"} if notes[1:] or not imported else {"INFO"}, msg)
        return {"FINISHED"}


# ------------------------------------------------------------------------------------------------- minimap --

def level_root(context):
    """The imported level collection (ds_level) the active object belongs to, else the only one in the scene."""
    roots = [c for c in context.scene.collection.children_recursive if c.get("ds_level")]
    ob = context.active_object
    if ob is not None:
        for c in roots:
            if ob.name in c.all_objects:
                return c
    return roots[0] if len(roots) == 1 else None


def scene_bounds_game(context, k):
    """((min x, min z), (max x, max z)) in game cm of the visible geometry (bounding boxes), or None."""
    xs, ys = [], []
    for ob in context.visible_objects:
        if ob.type not in ("MESH", "EMPTY") or (ob.type == "EMPTY" and ob.instance_type != "COLLECTION"):
            continue
        if ob.type == "MESH":
            pts = [ob.matrix_world @ Vector(c) for c in ob.bound_box]
        else:
            pts = [ob.matrix_world.translation]
        xs += [p.x for p in pts]
        ys += [p.y for p in pts]
    if not xs:
        return None
    return (min(xs) / k, min(ys) / k), (max(xs) / k, max(ys) / k)


class OBJECT_OT_ds_bake_minimap(bpy.types.Operator):
    """Render the level from straight above as the mission's tactical map (in-game MISSION tab / pause screen):
    GW<Name>0.png + its GW<Name>.img sheet in the active mod, covering the level bounds"""
    bl_idname = "object.ds_bake_minimap"
    bl_label = "Bake Minimap"
    bl_options = {"REGISTER"}

    map_name: StringProperty(name="Map Name", default="",
                             description="Writes GW<Name>.img + GW<Name>0.png; a mission script loads it with "
                                         "game.load_map_image(\"GW<Name>.img\")")
    size: EnumProperty(name="Size", default="256",
                       items=(("256", "256 x 256", "The size of every shipped map (checked in game)"),
                              ("512", "512 x 512", "Sharper; the sheet names its size (not checked in game yet)")))
    min_x: FloatProperty(name="Min X", description="Left edge (game cm)")
    min_z: FloatProperty(name="Min Z", description="Bottom edge (game cm)")
    max_x: FloatProperty(name="Max X", description="Right edge (game cm)")
    max_z: FloatProperty(name="Max Z", description="Top edge (game cm)")
    engine: EnumProperty(name="Look", default="WORKBENCH",
                         items=(("WORKBENCH", "Textured (fast)", "Workbench: the textures with studio lighting"),
                                ("SCENE", "Scene's Render Engine", "Whatever the scene renders with (lights, EEVEE / "
                                                                   "Cycles)")))
    background: FloatVectorProperty(name="Background", subtype="COLOR", size=3, min=0.0, max=1.0,
                                    default=(0.55, 0.48, 0.36),
                                    description="Colour outside the level's ground (Textured look)")
    scale: FloatProperty(name="Scale", default=0.01, min=1e-5, max=100.0,
                         description="Blender units per game centimetre (the import's Scale)")

    def invoke(self, context, event):
        root = level_root(context)
        if root is not None:
            self.scale = float(root.get("evo_scale", self.scale))
        level = str(root["ds_level"]) if root is not None else ""
        if not self.map_name:
            self.map_name = props(context.scene).active_mod.strip() or level.title() or "Map"
        bounds = minimap.LEVEL_BOUNDS.get(level)
        if bounds is None and root is not None and "ds_extent" in root:
            e = list(root["ds_extent"])
            bounds = ((e[0], e[1]), (e[2], e[3]))
        if bounds is None:
            b = scene_bounds_game(context, self.scale)
            bounds = ((b[0][0] - 3000, b[0][1] - 3000), (b[1][0] + 3000, b[1][1] + 3000)) if b else ((-5000, -5000), (5000, 5000))
        (self.min_x, self.min_z), (self.max_x, self.max_z) = bounds
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        lay = self.layout
        lay.use_property_split = True
        lay.prop(self, "map_name")
        lay.prop(self, "size")
        col = lay.column(align=True)
        for p in ("min_x", "max_x", "min_z", "max_z"):
            col.prop(self, p)
        lay.prop(self, "engine")
        if self.engine == "WORKBENCH":
            lay.prop(self, "background")
        mod = props(context.scene).active_mod.strip()
        lay.label(text="Into Mods\\%s" % mod if mod else "Set the Active Mod first (Export to Mod box)",
                  icon="FILE_FOLDER" if mod else "ERROR")

    def execute(self, context):
        scn = context.scene
        name = re.sub(r"[^A-Za-z0-9_]", "", self.map_name)
        if not name:
            self.report({"ERROR"}, "Map Name: letters, digits and _ only")
            return {"CANCELLED"}
        mod = props(scn).active_mod.strip().strip("\\/")
        gdir = game_dir_of(scn)
        if not mod or not gdir:
            self.report({"ERROR"}, "Needs the Active Mod and the game folder (Desert Storm sidebar > Game)")
            return {"CANCELLED"}
        w, d = self.max_x - self.min_x, self.max_z - self.min_z
        if w <= 0 or d <= 0:
            self.report({"ERROR"}, "Max X / Z must be larger than Min X / Z")
            return {"CANCELLED"}
        folder = os.path.join(gdir, "Mods", mod)
        os.makedirs(folder, exist_ok=True)
        size = int(self.size)
        img, page = minimap.map_names(name)
        k = self.scale
        top = max((ob.matrix_world.translation.z for ob in scn.objects), default=0.0)
        top = max(top, 0.0) + 100000 * k
        cam_data = bpy.data.cameras.new("DS Minimap")
        cam_data.type = "ORTHO"
        cam_data.ortho_scale = max(w, d) * k
        cam_data.clip_start = 1.0 * k
        cam_data.clip_end = top + 200000 * k
        cam = bpy.data.objects.new("DS Minimap", cam_data)
        scn.collection.objects.link(cam)
        cam.location = Vector(((self.min_x + w / 2) * k, (self.min_z + d / 2) * k, top))   # looks down, +Y = up
        r = scn.render
        keep = dict(camera=scn.camera, rx=r.resolution_x, ry=r.resolution_y, pct=r.resolution_percentage,
                    ax=r.pixel_aspect_x, ay=r.pixel_aspect_y, path=r.filepath, fmt=r.image_settings.file_format,
                    mode=r.image_settings.color_mode, ext=r.use_file_extension, transp=r.film_transparent,
                    engine=r.engine, light=scn.display.shading.light, ctype=scn.display.shading.color_type)
        target = mod_path(folder, page)
        world = scn.world
        tmp_world = None
        try:
            if self.engine == "WORKBENCH":
                tmp_world = bpy.data.worlds.new("DS Minimap")
                tmp_world.color = self.background
                scn.world = tmp_world
            scn.camera = cam
            r.resolution_x = r.resolution_y = size
            r.resolution_percentage = 100
            r.pixel_aspect_x, r.pixel_aspect_y = minimap.pixel_aspect(w, d)
            r.image_settings.file_format = "PNG"
            r.image_settings.color_mode = "RGB"
            r.use_file_extension = False
            r.film_transparent = False
            r.filepath = target
            if self.engine == "WORKBENCH":
                r.engine = "BLENDER_WORKBENCH"
                scn.display.shading.light = "STUDIO"
                scn.display.shading.color_type = "TEXTURE"
            bpy.ops.render.render(write_still=True)
        finally:
            scn.camera = keep["camera"]
            r.resolution_x, r.resolution_y, r.resolution_percentage = keep["rx"], keep["ry"], keep["pct"]
            r.pixel_aspect_x, r.pixel_aspect_y = keep["ax"], keep["ay"]
            r.filepath, r.use_file_extension, r.film_transparent = keep["path"], keep["ext"], keep["transp"]
            r.image_settings.file_format, r.image_settings.color_mode = keep["fmt"], keep["mode"]
            r.engine = keep["engine"]
            scn.display.shading.light, scn.display.shading.color_type = keep["light"], keep["ctype"]
            bpy.data.objects.remove(cam)
            bpy.data.cameras.remove(cam_data)
            if tmp_world is not None:
                scn.world = world
                bpy.data.worlds.remove(tmp_world)
        if not os.path.isfile(target):
            self.report({"ERROR"}, "The render wasn't written (%s)" % target)
            return {"CANCELLED"}
        with open(mod_path(folder, img), "wb") as f:
            f.write(minimap.sheet(page, size))
        msg = "Wrote %s + %s into Mods\\%s (x %.0f..%.0f, z %.0f..%.0f)" % (img, page, mod, self.min_x, self.max_x,
                                                                           self.min_z, self.max_z)
        script = modlayout.find(folder, name + ".lua")
        if script:
            with open(script, encoding="utf-8") as f:
                text = f.read()
            new, how = minimap.script_with_map(text, img)
            if new != text:
                with open(script, "w", encoding="utf-8", newline="\n") as f:
                    f.write(new)
            msg += "; %s.lua loads it (%s)" % (name, how)
        else:
            msg += "; load it from your mission script: game.load_map_image(\"%s\") and game.set_level_bounds(" \
                   "%.1f, 0, %.1f, %.1f, 0, %.1f)" % (img, self.max_x, self.max_z, self.min_x, self.min_z)
        self.report({"INFO"}, msg)
        return {"FINISHED"}


# ------------------------------------------------------------------------------------------- asset library --

_lib_job = {}


class OBJECT_OT_ds_generate_asset_library(bpy.types.Operator):
    """Save every extracted model as an editable .blend marked as an Asset Browser asset, sorted into catalogs
    (Vehicles, Weapons, Characters, Structures, Kit Pieces, Props), and add the folder as an asset library"""
    bl_idname = "object.ds_generate_asset_library"
    bl_label = "Generate Asset Library"

    extracted_dir: StringProperty(name="Extracted Folder", subtype="DIR_PATH",
                                  description="The folder File > Import > Conflict: Desert Storm Archives (extract) wrote")
    level_pieces: BoolProperty(name="Include Level Pieces", default=False,
                               description="Also the world-piece models (Kit Pieces catalog): thousands of large models")
    skip_existing: BoolProperty(name="Skip Models Already Saved", default=False,
                                description="Keep .blend files from an earlier run (they get no asset mark if they "
                                            "were made before)")
    register_library: BoolProperty(name="Add as Asset Library", default=True,
                                   description="Add <folder>\\_blend to Preferences > File Paths > Asset Libraries")

    @classmethod
    def poll(cls, context):
        busy = [j for j in (_job, _lib_job) if j.get("running")] + \
               ([_job] if _job.get("thread") is not None and _job["thread"].is_alive() else [])
        return not busy

    def invoke(self, context, event):
        if not self.extracted_dir:
            self.extracted_dir = extract_dir()
        return context.window_manager.invoke_props_dialog(self, width=520)

    def execute(self, context):
        out = bpy.path.abspath(self.extracted_dir)
        if not os.path.isdir(out):
            self.report({"ERROR"}, "%s doesn't exist - extract the game archives first (File > Import > Conflict: "
                                   "Desert Storm Archives)" % out)
            return {"CANCELLED"}
        models = editable_models(out, self.level_pieces)
        if not models:
            self.report({"ERROR"}, "No .evo models in %s - extract the game archives first" % out)
            return {"CANCELLED"}
        _lib_job.clear()
        _lib_job.update(out=out, models=models, done=0, saved=0, failed=[], phase="models", fraction=0.0, text="",
                        cancel=False, running=True, start=time.time(), skip_existing=self.skip_existing,
                        register=self.register_library, classify=Classifier(out))
        _status_job[0] = _lib_job
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.1, window=context.window)
        wm.modal_handler_add(self)
        context.workspace.status_text_set(_draw_status)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type == "ESC" and event.value == "PRESS":
            _lib_job["cancel"] = True
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        context.workspace.status_text_set(_draw_status)
        if not _lib_job["cancel"] and _lib_job["done"] < len(_lib_job["models"]):
            save_editable_batch(context, _lib_job, budget=0.25)
            return {"PASS_THROUGH"}
        context.window_manager.event_timer_remove(self._timer)
        context.workspace.status_text_set(None)
        _lib_job["running"] = False
        _status_job[0] = None
        bpy.data.orphans_purge(do_recursive=True)
        note = finish_library(_lib_job)
        msg = "%d models saved as assets in %s in %.0f s" % (_lib_job["saved"], os.path.join(_lib_job["out"], "_blend"),
                                                            time.time() - _lib_job["start"])
        if _lib_job["cancel"]:
            msg += " (stopped after %d of %d)" % (_lib_job["done"], len(_lib_job["models"]))
        if _lib_job["failed"]:
            msg += "; %d could not be converted: %s" % (len(_lib_job["failed"]), ", ".join(_lib_job["failed"][:8]))
        if note:
            msg += "; " + note
        msg += "; previews: right-click in the Asset Browser > Generate Preview"
        self.report({"WARNING"} if _lib_job["failed"] or _lib_job["cancel"] else {"INFO"}, msg)
        return {"FINISHED"}


# ------------------------------------------------------------------------------------- weapons: sights, hand --

AIM_CAMERA = "DS Aim Camera"
HAND_SNAP = "DS Hand Snap"


def model_root(ob):
    while ob.parent is not None:
        ob = ob.parent
    return ob


def find_point(ob, role):
    """The #PT_<role> helper of the model the object belongs to (its hierarchy, then its collections)."""
    want = "#PT_" + role
    root = model_root(ob)
    cands = [root] + list(root.children_recursive)
    for coll in ob.users_collection:
        cands += list(coll.all_objects)
        for c in bpy.data.collections:                  # the model's collection holds a 'helpers' child
            if coll.name in c.children:
                cands += list(c.all_objects)
    for o in cands:
        if o.get("evo_point_role") == role or base_name(o.name).upper() == want:
            return o
    return None


class OBJECT_OT_ds_align_aim_camera(bpy.types.Operator):
    """Look down the active weapon's sights: a camera locked on #PT_REAR_SIGHT looking at #PT_FRONT_SIGHT (or the
    muzzle point). Add the sight points with Add Point; they stay in Blender"""
    bl_idname = "object.ds_align_aim_camera"
    bl_label = "Aim Camera"
    bl_options = {"REGISTER", "UNDO"}

    lens: FloatProperty(name="Focal Length", default=50.0, min=5.0, max=500.0, description="Camera lens (mm)")

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        ob = context.active_object
        rear = find_point(ob, "REAR_SIGHT")
        front = find_point(ob, "FRONT_SIGHT") or find_point(ob, "MUZZLE")
        if rear is None or front is None:
            self.report({"ERROR"}, "Needs #PT_REAR_SIGHT and #PT_FRONT_SIGHT (or #PT_MUZZLE): put the 3D cursor on "
                                   "each sight and use Add Point")
            return {"CANCELLED"}
        cam = bpy.data.objects.get(AIM_CAMERA)
        if cam is None or cam.type != "CAMERA":
            cam = bpy.data.objects.new(AIM_CAMERA, bpy.data.cameras.new(AIM_CAMERA))
            context.scene.collection.objects.link(cam)
        cam.data.lens = self.lens
        dist = (front.matrix_world.translation - rear.matrix_world.translation).length
        cam.data.clip_start = max(1e-4, dist * 0.01)
        cam.parent = rear
        cam.matrix_parent_inverse = Matrix.Identity(4)
        cam.matrix_basis = Matrix.Identity(4)
        for c in list(cam.constraints):
            cam.constraints.remove(c)
        t = cam.constraints.new("TRACK_TO")
        t.target = front
        t.track_axis = "TRACK_NEGATIVE_Z"
        t.up_axis = "UP_Y"
        cam.lock_location = (True, True, True)
        cam.lock_rotation = (True, True, True)
        context.scene.camera = cam
        screen = context.screen
        for area in (screen.areas if screen else []):
            if area.type == "VIEW_3D":
                area.spaces.active.region_3d.view_perspective = "CAMERA"
        self.report({"INFO"}, "Aim camera on %s looking at %s (%.1f apart) - Numpad 0 toggles it"
                    % (rear.name, front.name, dist))
        return {"FINISHED"}


def hand_target(arm):
    """(bone, inverse matrix) to hold a weapon: the gun connector (BIP01 GUNR CONNECTOR) or else the right hand,
    placed by the game's own node frame when the armature's model is known."""
    bones = {b.name.upper(): b.name for b in arm.data.bones}
    wanted = (r"GUN\s*R\s*CONNECTOR", r"\bR\s*HAND\b")
    k = arm.get("evo_scale", 0.01)
    path = arm.get("evo_path", "")
    if path and os.path.isfile(path):
        try:
            scene = evo.load(path)
        except (evo.EvoError, OSError):
            scene = None
        if scene is not None:
            for pat in wanted:
                node = next((so for so in scene.objects if re.search(pat, so.name.upper())), None)
                if node is None:
                    continue
                j = node.index                          # the bone it moves with: itself or an ancestor
                while j is not None and not (j in scene.bone_ids and scene.objects[j].name.upper() in bones):
                    j = scene.objects[j].parent
                if j is None:
                    continue
                bone = bones[scene.objects[j].name.upper()]
                rest = arm.data.bones[bone].matrix_local
                return bone, rest.inverted() @ conv_matrix(node.world, k)
    for pat in wanted:
        hit = next((name for up, name in bones.items() if re.search(pat, up)), None)
        if hit:
            return hit, Matrix.Identity(4)
    return None, None


class OBJECT_OT_ds_snap_to_hand(bpy.types.Operator):
    """Put the selected weapon in a soldier's right hand (gun connector) to check the grip in the current pose;
    press again to put it back where it was"""
    bl_idname = "object.ds_snap_to_hand"
    bl_label = "Snap to Hand"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type != "ARMATURE"

    def execute(self, context):
        weapon = model_root(context.active_object)
        c = weapon.constraints.get(HAND_SNAP)
        if c is not None:
            weapon.constraints.remove(c)
            saved = weapon.get("ds_hand_snap_matrix")
            if saved is not None and len(saved) == 16:
                weapon.matrix_basis = Matrix([saved[i * 4:i * 4 + 4] for i in range(4)])
            self.report({"INFO"}, "%s back in place" % weapon.name)
            return {"FINISHED"}
        arms = [o for o in context.selected_objects if o.type == "ARMATURE"] or \
               [o for o in context.scene.objects if o.type == "ARMATURE"]
        arm, bone, inv = None, None, None
        for a in arms:
            bone, inv = hand_target(a)
            if bone:
                arm = a
                break
        if arm is None:
            self.report({"ERROR"}, "No soldier armature with a right hand bone - import a character (HERO01_ARMSTRONG "
                                   "...) with Armature on")
            return {"CANCELLED"}
        weapon["ds_hand_snap_matrix"] = [x for row in weapon.matrix_basis for x in row]
        weapon.matrix_basis = Matrix.Identity(4)
        c = weapon.constraints.new("CHILD_OF")
        c.name = HAND_SNAP
        c.target = arm
        c.subtarget = bone
        c.inverse_matrix = inv
        if hasattr(c, "set_inverse_pending"):
            c.set_inverse_pending = False       # new Child Of constraints would set their own inverse
        self.report({"INFO"}, "%s in %s's %s - press Snap to Hand again to put it back" % (weapon.name, arm.name, bone))
        return {"FINISHED"}


# ------------------------------------------------------------------------------------------- vehicle rig --

TURRET_PIVOT, GUN_PIVOT = "DS_TURRET_PIVOT", "DS_GUN_PIVOT"


def model_collection(ob):
    """The imported model's collection (evo_source) an object belongs to."""
    for c in bpy.data.collections:
        if c.get("evo_source") and ob.name in c.all_objects:
            return c
    return None


def _pivot_node(scene, node):
    p = scene.objects[node].parent
    return p if p is not None and scene.objects[p].name.upper().startswith("CONNECTOR") else node


def _descends(scene, node, anc):
    while node is not None:
        if node == anc:
            return True
        node = scene.objects[node].parent
    return False


def rig_nodes(scene):
    """(turret node, gun node) of a vehicle model: TURRET / GUNMOUNT / BARREL / CANNON objects (no helpers)."""
    def real(so):
        n = so.name.upper()
        return not n.startswith(("#", "CONNECTOR"))
    turret = next((so.index for so in scene.objects if real(so) and "TURRET" in so.name.upper()), None)
    guns = [so.index for so in scene.objects if real(so) and re.search(r"GUN|BARREL|CANNON", so.name.upper())]
    if turret is not None:
        tp = _pivot_node(scene, turret)
        guns.sort(key=lambda g: not _descends(scene, g, tp))
    return turret, guns[0] if guns else None


def posed_pivots(obs):
    """Names of rig pivots above the objects that are turned away from their rest rotation."""
    out = set()
    for ob in obs:
        p = ob.parent
        while p is not None:
            rest = p.get("ds_rest_euler")
            if rest is not None and any(abs(a - b) > 1e-4 for a, b in zip(p.rotation_euler, rest)):
                out.add(p.name)
            p = p.parent
    return sorted(out)


class OBJECT_OT_ds_setup_vehicle_rig(bpy.types.Operator):
    """Make the active vehicle's turret and gun turnable for a preview: pivots at the game's mount points (the gun
    rides on the turret), turret free on Z, gun limited on X (elevation: positive = up)"""
    bl_idname = "object.ds_setup_vehicle_rig"
    bl_label = "Rig Turret / Gun"
    bl_options = {"REGISTER", "UNDO"}

    traverse: FloatProperty(name="Traverse (each way)", default=180.0, min=0.0, max=180.0,
                            description="How far the turret turns each way (180 = all round)")
    elevation: FloatProperty(name="Max Elevation", default=20.0, min=0.0, max=90.0,
                             description="Degrees the gun lifts (the game's tables give no limits: tank guns ~16-20)")
    depression: FloatProperty(name="Max Depression", default=8.0, min=0.0, max=45.0,
                              description="Degrees the gun dips")

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        coll = model_collection(context.active_object)
        if coll is None:
            self.report({"ERROR"}, "Select an imported vehicle (File > Import > Conflict: Desert Storm .evo)")
            return {"CANCELLED"}
        try:
            scene = evo.load(bpy.path.abspath(coll["evo_source"]))
        except (evo.EvoError, OSError) as e:
            self.report({"ERROR"}, "Can't read %s: %s" % (coll["evo_source"], e))
            return {"CANCELLED"}
        turret, gun = rig_nodes(scene)
        if turret is None and gun is None:
            self.report({"ERROR"}, "%s has no TURRET or GUN object" % scene.name)
            return {"CANCELLED"}
        k = float(coll.get("evo_scale", 0.01))
        obs = list(coll.all_objects)
        for o in obs:                                   # rig again: drop the old pivots (children keep their place)
            if o.name.startswith((TURRET_PIVOT, GUN_PIVOT)) and o.type == "EMPTY":
                for ch in list(o.children):
                    mw = ch.matrix_world.copy()
                    ch.parent = o.parent
                    ch.matrix_world = mw
                bpy.data.objects.remove(o)
        obs = list(coll.all_objects)
        by_node = {}
        for o in obs:
            n = o.get("evo_node")
            if isinstance(n, int) and 0 <= n < len(scene.objects):
                by_node.setdefault(n, o)
        for so in scene.objects:                        # older imports: by name
            if so.index not in by_node:
                same = [o for o in obs if base_name(o.name).upper() == so.name.upper() and "evo_part_conn" not in o]
                if len(same) == 1:
                    by_node[so.index] = same[0]
        roots = [so.index for so in scene.objects if so.parent is None and so.index in by_node]
        if not roots:
            self.report({"ERROR"}, "Can't find %s's objects in the scene" % scene.name)
            return {"CANCELLED"}
        root_so = scene.objects[roots[0]]
        place = by_node[root_so.index].matrix_world @ conv_matrix(root_so.world, k).inverted()   # moved since import

        def holder(node):
            j = scene.objects[node].parent
            while j is not None and j not in by_node:
                j = scene.objects[j].parent
            return by_node.get(j, by_node[root_so.index])

        made = []

        def node_of(o):
            n = o.get("evo_part_conn")
            if not isinstance(n, int):
                n = next((j for j, ob in by_node.items() if ob == o), None)
            return n if n is not None and n < len(scene.objects) else None

        # Pivot axes = the vehicle's own frame as placed (Z up, X across), not the holder object's: imported nodes
        # carry rotations of their own (the T-62's HULL is turned 90 degrees about two axes)
        frame = place.to_quaternion().to_matrix().to_4x4()

        def pivot(name, node, parent_ob, sub=None):
            pos = (place @ conv_matrix(scene.objects[node].world, k)).translation
            ob = bpy.data.objects.new(name, None)
            coll.objects.link(ob)
            ob.empty_display_type = "ARROWS"
            ob.empty_display_size = 100 * k
            ob.parent = parent_ob
            ob.matrix_parent_inverse = Matrix.Identity(4)
            rot = frame
            if sub is not None:                         # gun: local +Y along the barrel, so a positive X lifts it
                cs = [o.matrix_world @ (sum((Vector(c) for c in o.bound_box), Vector()) / 8) for o in obs
                      if o.type == "MESH" and node_of(o) is not None and _descends(scene, node_of(o), sub)]
                if cs:
                    ahead = sum(cs, Vector()) / len(cs) - pos
                    if ahead.dot(frame.to_3x3() @ Vector((0, 1, 0))) < 0:
                        rot = frame @ Matrix.Rotation(math.pi, 4, "Z")
            ob.matrix_world = Matrix.Translation(pos) @ rot   # (not via rotation_euler: matrix_world would stay
                                                               # stale and the parts parented next would turn round)
            ob["ds_rig_pivot"] = name
            ob["ds_rest_euler"] = list(ob.rotation_euler)
            made.append(ob)
            return ob

        tp = _pivot_node(scene, turret) if turret is not None else None
        gp = _pivot_node(scene, gun) if gun is not None else None
        t_ob = pivot(TURRET_PIVOT, tp, holder(tp)) if tp is not None else None
        g_ob = None
        if gp is not None:
            g_parent = t_ob if t_ob is not None and _descends(scene, gp, tp) else holder(gp)
            g_ob = pivot(GUN_PIVOT, gp, g_parent, gp)

        def target(node):
            if g_ob is not None and _descends(scene, node, gp):
                return g_ob
            if t_ob is not None and _descends(scene, node, tp):
                return t_ob
            return None

        moved = 0
        for o in obs:
            node = node_of(o)
            if node is None:
                continue
            t = target(node)
            if t is None or o in made:
                continue
            p = o.parent                                # only the top-most: children follow their parent
            pn = node_of(p) if p is not None else None
            if pn is not None and target(pn) == t:
                continue
            mw = o.matrix_world.copy()
            o.parent = t
            o.matrix_world = mw
            moved += 1
        rad = math.radians
        if t_ob is not None:
            t_ob.lock_rotation = (True, True, False)
            if self.traverse < 180.0:
                c = t_ob.constraints.new("LIMIT_ROTATION")
                c.owner_space = "LOCAL"
                c.use_limit_z = True
                c.min_z, c.max_z = -rad(self.traverse), rad(self.traverse)
                c.use_transform_limit = True
        if g_ob is not None:
            g_ob.lock_rotation = (False, True, True)
            c = g_ob.constraints.new("LIMIT_ROTATION")
            c.owner_space = "LOCAL"
            c.use_limit_x = True
            c.min_x, c.max_x = -rad(self.depression), rad(self.elevation)
            c.use_transform_limit = True
        self.report({"INFO"}, "%s: %s%s, %d objects moved onto them - turn them in the sidebar; Reset Vehicle Pose "
                              "before exporting" % (scene.name, "turret" if t_ob else "",
                                                    (" + gun" if t_ob else "gun") if g_ob else "", moved))
        return {"FINISHED"}


class OBJECT_OT_ds_reset_vehicle_rig(bpy.types.Operator):
    """Turn the active vehicle's turret and gun back to where the game model has them"""
    bl_idname = "object.ds_reset_vehicle_rig"
    bl_label = "Reset Vehicle Pose"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        coll = model_collection(context.active_object) if context.active_object else None
        n = 0
        for o in (coll.all_objects if coll else context.scene.objects):
            rest = o.get("ds_rest_euler")
            if rest is not None:
                o.rotation_euler = rest
                n += 1
        self.report({"INFO"}, "%d pivots reset" % n)
        return {"FINISHED"}


def vehicle_pivots(ob):
    coll = model_collection(ob) if ob else None
    if coll is None:
        return None, None
    found = {o.get("ds_rig_pivot"): o for o in coll.all_objects if o.get("ds_rig_pivot")}
    return found.get(TURRET_PIVOT), found.get(GUN_PIVOT)


# ----------------------------------------------------------------------------------------- test in game --

def game_running():
    """True when the current game's exe is already running (Windows), None when that can't be told."""
    if os.name != "nt":
        return None
    import subprocess
    try:
        out = subprocess.run(["tasklist", "/NH"], capture_output=True,
                             text=True, timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return any(e.lower() in out.lower() for e in games.current().exes)


class OBJECT_OT_ds_play_in_game(bpy.types.Operator):
    """Export the selected model into the active mod (as Export to Mod does) and start the game: straight into the
    Test Level, or the launcher when it's empty (the other games: always the launcher / front end - only
    DesertStormFix has a +level start)"""
    bl_idname = "object.ds_play_in_game"
    bl_label = "Test in Game"
    bl_options = {"REGISTER"}

    def execute(self, context):
        import subprocess
        scn = context.scene
        gdir = game_dir_of(scn)
        if not gdir:
            self.report({"ERROR"}, "The game wasn't found - set Game Folder (the folder with the game's exe)")
            return {"CANCELLED"}
        game = games.current()
        if game.id not in TEST_GAMES:
            self.report({"ERROR"}, "%s: its plugin (%s) doesn't load models from a Mods folder yet" % (
                game.short, game.plugin))
            return {"CANCELLED"}
        if game_running():
            self.report({"ERROR"}, "The game is running - close it first (it reads the mods when it starts)")
            return {"CANCELLED"}
        exported = ""
        if props(scn).play_export and any(o.type == "MESH" for o in context.selected_objects):
            if not props(scn).active_mod.strip():
                self.report({"ERROR"}, "Set the Active Mod to export into (or untick Export First)")
                return {"CANCELLED"}
            if not props(scn).game_dir:
                props(scn).game_dir = gdir
            res = run_op(OBJECT_OT_ds_quick_export)
            if "FINISHED" not in res:
                self.report({"ERROR"}, "Export failed (see the message before this one) - the game wasn't started")
                return {"CANCELLED"}
            exported = "exported into Mods\\%s, " % props(scn).active_mod.strip()
        args = [os.path.join(gdir, game.exes[0])]
        level = props(scn).test_level.strip()
        skipped = ""
        if level and game.script_host:
            args += ["+level", level]
        elif level:
            skipped = " (Test Level ignored: %s has no +level start - pick the mission in the game's menus)" % game.short
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            subprocess.Popen(args, cwd=gdir, close_fds=True, creationflags=flags)
        except OSError as e:
            self.report({"ERROR"}, "Couldn't start the game: %s" % e)
            return {"CANCELLED"}
        self.report({"WARNING"} if skipped else {"INFO"}, "%sstarted %s%s" % (
            exported, " ".join(os.path.basename(a) if i == 0 else a for i, a in enumerate(args)), skipped))
        return {"FINISHED"}


# --------------------------------------------------------------------------------------- soldier uniforms --

_uniforms = {"items": [], "paths": {}, "key": None, "textures": None}


def soldier_armature(ob):
    if ob is None:
        return None
    if ob.type == "ARMATURE":
        return ob
    return armature_of(ob) if ob.type == "MESH" else None


def _texture_index():
    """{STEM: path} of the textures in the extracted folders (built once; Refresh clears it)."""
    if _uniforms["textures"] is None:
        idx = {}
        for d in archive_dirs():
            try:
                names = os.listdir(d)
            except OSError:
                continue
            for n in names:
                stem, ext = os.path.splitext(n)
                if ext.lower() in (".dds", ".tga", ".png") and stem.upper().startswith("HERO0"):
                    idx.setdefault(stem.upper(), os.path.join(d, n))
        _uniforms["textures"] = idx
    return _uniforms["textures"]


def uniform_items(self, context):
    arm = soldier_armature(context.active_object)
    hero = assetlib.hero_number(os.path.splitext(os.path.basename(arm.get("evo_path", "")))[0]) if arm else None
    key = (hero, props(context.scene).game_dir)
    if key == _uniforms["key"] and _uniforms["items"]:
        return _uniforms["items"]
    items, paths = [], {}
    if not hero:
        items = [("NONE", "Select a squad soldier", "Import HERO01_ARMSTRONG / 02_RAMIREZ / 03_JONES / 04_FOLEY with "
                                                    "Armature on and select it")]
    else:
        tex = _texture_index()
        for stem in assetlib.game_skins(tex.keys(), hero):
            items.append((stem, assetlib.skin_label(stem), "Game skin %s (%s)" % (stem, tex[stem])))
            paths[stem] = tex[stem]
        gdir = game_dir_of(context.scene)
        for name, path, label in assetlib.mod_skins(os.path.join(gdir, "Mods"), hero) if gdir else []:
            ident = "MOD:" + name
            if ident not in paths:
                items.append((ident, label, "Mod skin %s (%s)" % (name, path)))
                paths[ident] = path
        if not items:
            items = [("NONE", "No skins found", "Extract the game archives (chardata holds the uniforms)")]
    _uniforms.update(items=items, paths=paths, key=key)     # Blender needs the strings kept alive
    return items


def apply_uniform(context, ident):
    """Show the soldier (meshes bound to the active armature) in a uniform texture. Returns a problem or None."""
    arm = soldier_armature(context.active_object)
    path = _uniforms["paths"].get(ident)
    if arm is None or not path:
        return None
    img = load_image(path)
    if img is None:
        return "can't load %s" % path
    n = 0
    for ob in context.scene.objects:
        if ob.type != "MESH" or armature_of(ob) != arm:
            continue
        for slot in ob.material_slots:
            mat = slot.material
            if image_of(mat) is None:
                continue
            if mat.users > 1 and not mat.get("ds_uniform_copy"):
                mat = mat.copy()                        # only this soldier changes
                mat["ds_uniform_copy"] = True
                slot.material = mat
            for node in mat.node_tree.nodes:
                if node.type == "TEX_IMAGE" and node.image and node.outputs[0].is_linked and \
                        not node.image.colorspace_settings.name.lower().startswith("non"):
                    node.image = img
                    n += 1
                    break
        ob["ds_uniform"] = ident
    return None if n else "the soldier's materials have no texture to swap"


def _uniform_update(self, context):
    if props(self).soldier_uniform != "NONE":
        apply_uniform(context, props(self).soldier_uniform)


class OBJECT_OT_ds_refresh_uniforms(bpy.types.Operator):
    """Look for uniform textures again (after extracting or installing a skin mod)"""
    bl_idname = "object.ds_refresh_uniforms"
    bl_label = "Refresh Uniforms"

    def execute(self, context):
        _uniforms.update(items=[], paths={}, key=None, textures=None)
        return {"FINISHED"}


_stats = {}


def model_stats(ob):
    """Numbers for the sidebar's Model Check, cached per mesh until its counts or materials change."""
    me = ob.data
    key = (me.as_pointer(), len(me.vertices), len(me.loops), len(me.polygons),
           tuple(s.material.name if s.material else "" for s in ob.material_slots))
    hit = _stats.get(ob.name)
    if hit and hit[0] == key:
        return hit[1]
    totals = [0] * len(me.polygons)
    me.polygons.foreach_get("loop_total", totals)
    mats = [0] * len(me.polygons)
    me.polygons.foreach_get("material_index", mats)
    loops = [0] * len(me.loops)
    me.loops.foreach_get("vertex_index", loops)
    uv = [0.0] * (2 * len(me.loops))
    if me.uv_layers.active:
        me.uv_layers.active.data.foreach_get("uv", uv)
    per_tex = {}
    li = 0
    for total, mi in zip(totals, mats):
        mat = ob.material_slots[mi].material if mi < len(ob.material_slots) else None
        seen = per_tex.setdefault(texture_of(mat), set())
        for j in range(li, li + total):         # an export vertex per position + UV (hard edges add a few more)
            seen.add((loops[j], round(uv[2 * j], 5), round(uv[2 * j + 1], 5)))
        li += total
    images = {}
    for s in ob.material_slots:
        img = image_of(s.material)
        if img is not None:
            images[img.name] = tuple(img.size)
    st = {"verts": len(me.vertices), "tris": sum(t - 2 for t in totals),
          "per_texture": {t: len(v) for t, v in per_tex.items()}, "images": images}
    _stats[ob.name] = (key, st)
    return st


def collision_of(ob):
    """Collision helper objects that belong to a model object (its #BSP_ children, or itself)."""
    if helper_kind(ob) == "collision":
        return [ob]
    return [c for c in ob.children if c.type == "MESH" and helper_kind(c) == "collision"]


class OBJECT_OT_ds_refresh_stats(bpy.types.Operator):
    """Count the active model again (after UV or material edits)"""
    bl_idname = "object.ds_refresh_stats"
    bl_label = "Refresh"

    def execute(self, context):
        _stats.clear()
        return {"FINISHED"}


POT = {1 << i for i in range(16)}


class VIEW3D_PT_ds_tools(bpy.types.Panel):
    """3D View sidebar (N) > Desert Storm: export, model check, collision surfaces, helpers, points, skins, roads"""
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Desert Storm"
    bl_label = "Desert Storm"

    def draw(self, context):
        lay = self.layout
        scn = context.scene
        ob = context.active_object
        # the game of the last import / export: Export to Mod and Test in Game use its folder and mod
        game = profile()
        if not GAME:
            lay.label(text="Game: %s (last import / export)" % game.short, icon="WORLD")
        if game.id not in WRITE_GAMES:              # Denied Ops: import only - none of the export tools apply
            box = lay.box()
            box.label(text="Level", icon="WORLD")
            box.operator(IMPORT_SCENE_OT_ds_env.bl_idname, text="Import Scene (.env)", icon="IMPORT")
            box = lay.box()
            box.label(text="%s models import only:" % game.short, icon="INFO")
            box.label(text="exporting them isn't built yet")
            return
        # quick export into the active mod
        box = lay.box()
        box.label(text="Export to mod", icon="EXPORT")
        box.prop(scn, sp("active_mod"), text="Mod")
        box.prop(scn, sp("last_exported_model"), text="Model")
        box.prop(scn, sp("generic_prop"), text="Based On: Auto / Generic Prop")
        row = box.row()
        row.scale_y = 1.4
        row.operator(OBJECT_OT_ds_quick_export.bl_idname, icon="FILE_TICK")
        if not props(scn).active_mod.strip():
            box.label(text="Export once Into a Mod Folder, or type a mod name", icon="INFO")
        # test in game
        box = lay.box()
        box.label(text="Game", icon="PLAY")
        box.prop(scn, sp("game_dir"), text="Folder")
        if not game_dir_of(scn):
            box.label(text="No supported game here - set the folder", icon="ERROR")
        if not GAME or game.id in TEST_GAMES:           # the games whose plugin loads the exported model
            box.prop(scn, sp("test_level"), text="Level")
            box.prop(scn, sp("play_export"), text="Export Selection First")
            row = box.row()
            row.scale_y = 1.4
            row.operator(OBJECT_OT_ds_play_in_game.bl_idname, icon="PLAY")
        if GAME in (None, "ds1"):
            box.operator(IMPORT_SCENE_OT_ds_gc_audio.bl_idname, text="GameCube Audio...", icon="SPEAKER")
        # level tools
        box = lay.box()
        box.label(text="Level", icon="WORLD")
        box.operator(IMPORT_SCENE_OT_ds_env.bl_idname, text="Import Scene (.env)", icon="IMPORT")
        box.operator(OBJECT_OT_ds_bake_minimap.bl_idname, icon="RENDER_STILL")
        box.operator(OBJECT_OT_ds_generate_asset_library.bl_idname, icon="ASSET_MANAGER")
        self.draw_model_tools(lay, context, ob)
        if ob is not None and ob.type == "MESH" and context.mode in ("OBJECT", "EDIT_MESH"):
            self.draw_stats(lay, ob, context)
        box = lay.box()
        box.label(text="Collision surface")
        # what blocks what (tested in game): normal surfaces stop soldiers and bullets; Blocking Invisible stops
        # soldiers, bullets / grenades fly through; Player Barrier stops only your squad
        row = box.row(align=True)
        row.prop_enum(scn, sp("surface"), "4", text="Blocks All")
        row.prop_enum(scn, sp("surface"), "35", text="Soldiers Only")
        row.prop_enum(scn, sp("surface"), "46", text="Your Squad")
        row = box.row(align=True)
        row.prop(scn, sp("surface"), text="")
        if context.mode == "EDIT_MESH":
            row.operator(OBJECT_OT_ds_pick_surface.bl_idname, text="", icon="EYEDROPPER")
        box.operator(OBJECT_OT_ds_assign_surface.bl_idname,
                     text="Assign to Selected Faces" if context.mode == "EDIT_MESH" else "Assign to Object")
        box.prop(scn, sp("show_surfaces"), text="Show Collision Surfaces", toggle=True, icon="SHADING_SOLID")
        if props(scn).show_surfaces and ob is not None and ob.type == "MESH" and helper_kind(ob) == "collision":
            col = box.column(align=True)
            for s in ob.material_slots:
                if s.material is not None:
                    r = col.row(align=True)
                    r.prop(s.material, "diffuse_color", text="")
                    r.label(text=base_name(s.material.name).replace("Surface: ", ""))
        box = lay.box()
        box.label(text="New helper from the active mesh")
        row = box.row(align=True)
        for shape, label in (("BOX", "Box"), ("HULL", "Hull"), ("COPY", "Copy")):
            op = row.operator(OBJECT_OT_ds_make_collision.bl_idname, text=label)
            op.shape, op.kind = shape, "COLLISION"
        op = box.operator(OBJECT_OT_ds_make_collision.bl_idname, text="Shadow Mesh (Hull)")
        op.shape, op.kind = "HULL", "SHADOW"
        box = lay.box()
        box.label(text="Point at the 3D cursor")
        box.operator_menu_enum(OBJECT_OT_ds_add_point.bl_idname, "role", text="Add Point")
        if ob is not None and ob.type == "MESH" and armature_of(ob) is not None:
            box = lay.box()
            box.label(text="Character skin", icon="ARMATURE_DATA")
            box.operator(OBJECT_OT_ds_validate_skin_weights.bl_idname, icon="CHECKMARK")
        box = lay.box()
        box.label(text="Road from the active curve", icon="CURVE_PATH")
        if ob is not None and ob.type == "CURVE":
            box.operator(OBJECT_OT_ds_generate_road.bl_idname, icon="MOD_CURVE")
        else:
            box.label(text="Select a curve (Add > Curve) to make a road")
        if ob is not None:
            kind = helper_kind(ob) if ob.type == "MESH" else None
            role = ob.get("evo_point_role")
            if kind or role:
                lay.label(text="Active: %s" % (("point " + role.lower()) if role else kind), icon="INFO")

    def draw_model_tools(self, lay, context, ob):
        arm = soldier_armature(ob)
        if arm is not None:
            box = lay.box()
            box.label(text="Soldier: " + arm.name, icon="ARMATURE_DATA")
            row = box.row(align=True)
            row.prop(context.scene, sp("soldier_uniform"), text="Uniform")
            row.operator(OBJECT_OT_ds_refresh_uniforms.bl_idname, text="", icon="FILE_REFRESH")
        if ob is None or ob.type == "ARMATURE":
            return
        box = lay.box()
        box.label(text="Weapon", icon="LIGHT_SPOT")
        row = box.row(align=True)
        row.operator(OBJECT_OT_ds_align_aim_camera.bl_idname, icon="CAMERA_DATA")
        snapped = model_root(ob).constraints.get(HAND_SNAP) is not None
        row.operator(OBJECT_OT_ds_snap_to_hand.bl_idname, text="Release from Hand" if snapped else "Snap to Hand",
                     icon="VIEW_PAN")
        coll = model_collection(ob)
        if coll is None:
            return
        box = lay.box()
        box.label(text="Vehicle", icon="AUTO")
        turret, gun = vehicle_pivots(ob)
        box.operator(OBJECT_OT_ds_setup_vehicle_rig.bl_idname, icon="CON_ROTLIMIT")
        if turret is not None or gun is not None:
            col = box.column(align=True)
            if turret is not None:
                col.prop(turret, "rotation_euler", index=2, text="Turret")
            if gun is not None:
                col.prop(gun, "rotation_euler", index=0, text="Gun Elevation")
            box.operator(OBJECT_OT_ds_reset_vehicle_rig.bl_idname, icon="LOOP_BACK")

    def draw_stats(self, lay, ob, context):
        box = lay.box()
        row = box.row()
        row.label(text="Model check: " + ob.name, icon="VIEWZOOM")
        row.operator(OBJECT_OT_ds_refresh_stats.bl_idname, text="", icon="FILE_REFRESH")
        st = model_stats(ob)
        col = box.column(align=True)
        col.label(text="%d vertices, %d triangles" % (st["verts"], st["tris"]))
        helper = helper_kind(ob) is not None          # collision / shadow: no textures, the counts below matter
        for tex, n in [] if helper else sorted(st["per_texture"].items()):
            over = n > 65535
            col.label(text="%s: ~%d export vertices%s" % (tex, n, " - over 65535!" if over else ""),
                      icon="ERROR" if over else "TEXTURE")
        for name, (w, h) in [] if helper else sorted(st["images"].items()):
            if w > 2048 or h > 2048:
                col.label(text="%s is %d x %d - over 2048" % (name, w, h), icon="ERROR")
            elif w > 1024 or h > 1024:
                col.label(text="%s is %d x %d - needs DesertStormFix 3.2+" % (name, w, h), icon="INFO")
            elif w and h and (w not in POT or h not in POT):
                col.label(text="%s is %d x %d - not a power of two" % (name, w, h), icon="ERROR")
        for c in collision_of(ob):
            ntri = sum(len(p.vertices) - 2 for p in c.data.polygons)
            col.label(text="%s: %d collision triangles%s" % (c.name, ntri, " - keep it under 3000" if ntri > 3000 else ""),
                      icon="ERROR" if ntri > 3000 else "MOD_PHYSICS")
        if context.mode == "EDIT_MESH":
            col.label(text="(counts update in Object Mode)", icon="INFO")


CLASSES = (IMPORT_SCENE_OT_evo, IMPORT_ANIM_OT_prb, IMPORT_SCENE_OT_ds_extract, IMPORT_SCENE_OT_ds_gc_audio,
           IMPORT_SCENE_OT_ds_port, EXPORT_SCENE_OT_ds_env,
           EXPORT_SCENE_OT_evo,
           EXPORT_ANIM_OT_prb, OBJECT_OT_ds_assign_surface, OBJECT_OT_ds_make_collision, OBJECT_OT_ds_add_point,
           OBJECT_OT_ds_pick_surface, OBJECT_OT_ds_validate_skin_weights, OBJECT_OT_ds_quick_export,
           OBJECT_OT_ds_generate_road, OBJECT_OT_ds_refresh_stats, IMPORT_SCENE_OT_ds_env, OBJECT_OT_ds_bake_minimap,
           OBJECT_OT_ds_generate_asset_library, OBJECT_OT_ds_align_aim_camera, OBJECT_OT_ds_snap_to_hand,
           OBJECT_OT_ds_setup_vehicle_rig, OBJECT_OT_ds_reset_vehicle_rig, OBJECT_OT_ds_play_in_game,
           OBJECT_OT_ds_refresh_uniforms, VIEW3D_PT_ds_tools)

SCENE_PROPS = {
    "ds_surface": lambda: EnumProperty(name="Surface", items=SURFACE_ITEMS, default="4",
                                       description="Collision surface: footstep sounds and bullet effects; "
                                                   "Blocking ... = an obstacle on the AI's route map"),
    "ds_active_mod": lambda: StringProperty(name="Active Mod", get=_profile_get("active_mod"),
                                            set=_profile_set("active_mod"),
                                            description="Mod folder under <game>\\Mods that Export to Mod writes into "
                                                        "(set by the last export Into a Mod Folder; one per Game)"),
    "ds_last_exported_model": lambda: StringProperty(name="Model Name", default="",
                                                     description="File name Export to Mod writes (<name>.evo; empty = "
                                                                 "the active object's name)"),
    "ds_generic_prop": lambda: BoolProperty(name="Generic Prop", default=False,
                                            description="Export brand-new props on the generic template ("
                                                        + GENERIC_TEMPLATE + ") instead of a game model"),
    "ds_game_dir": lambda: StringProperty(name="Game Folder", subtype="DIR_PATH", get=_profile_get("game_dir"),
                                          set=_profile_set("game_dir"),
                                          description="Folder with the Game's exe (empty = found from the registry; "
                                                      "set it if you have the game twice, e.g. Steam and GOG; one "
                                                      "per Game)"),
    "ds_test_level": lambda: StringProperty(name="Test Level", default="",
                                            description="Level Test in Game starts straight into: Mission1.dll ... or "
                                                        "a mod mission such as Ambush.lua (empty = the launcher; "
                                                        "Desert Storm II always starts at the launcher)"),
    "ds_play_export": lambda: BoolProperty(name="Export First", default=True,
                                           description="Test in Game first exports the selected model into the active "
                                                       "mod (like Export to Mod)"),
    "ds_soldier_uniform": lambda: EnumProperty(name="Uniform", items=uniform_items, update=_uniform_update,
                                               description="Show the selected squad soldier in another uniform "
                                                           "(game skins from the extracted chardata, and mods' .skin "
                                                           "uniforms) - a viewport preview, nothing is exported"),
    "ds_show_surfaces": lambda: BoolProperty(name="Show Collision Surfaces", default=False, update=_surfaces_update,
                                             description="Draw #BSP_ collision objects solid in one colour per "
                                                         "surface instead of as wireframes"),
}


def _scene_prop(name):
    """SCENE_PROPS key -> this add-on's Scene property name (ds_surface -> sp("surface"))."""
    return sp(name[3:])


if PREFIX:                                  # a game add-on: its own ids, panel and sidebar tab
    for _c in CLASSES:
        if issubclass(_c, bpy.types.Operator):
            _c.bl_idname = op_id(_c.bl_idname)
    VIEW3D_PT_ds_tools.bl_idname = "VIEW3D_PT_%s_tools" % PREFIX
    VIEW3D_PT_ds_tools.bl_category = VIEW3D_PT_ds_tools.bl_label = games.GAMES[GAME].short
    if GAME == "cg":                        # Global Storm's animations are .ani
        IMPORT_ANIM_OT_prb.bl_label = "Import ANI Animation"
        EXPORT_ANIM_OT_prb.bl_label = "Export ANI Animation"
        EXPORT_ANIM_OT_prb.filename_ext = ".ani"


def register():
    bpy.utils.register_class(DS_PT_preferences)
    for c in CLASSES:
        bpy.utils.register_class(c)
    for name, make in SCENE_PROPS.items():
        setattr(bpy.types.Scene, _scene_prop(name), make())
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    for name in SCENE_PROPS:
        delattr(bpy.types.Scene, _scene_prop(name))
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
    bpy.utils.unregister_class(DS_PT_preferences)
