"""Blender side of the Conflict: Desert Storm tools (File > Import > Conflict: Desert Storm ...).

- .evo import: props, vehicles (node hierarchy + the tracks, wheels and guns the game attaches), world pieces,
  cloth (frames as shape keys), characters (armature, skin weights, optionally all their animations).
- .prb import: animations onto a character armature made by the .evo import; .prb export: the armature's action
  back to the game (footstep events kept from the game animation it replaces).
- Archive extraction: the game's .dat files -> folders of meshes, textures (+ PNG), animations and tables.
- .evo export: new geometry for a game model's parts (the model it is based on keeps attachment points,
  materials and layout): static models (weapons, props, vehicles) and characters (the skin, weighted to the
  game's skeleton).
Materials: base texture, the .RFX normal map (DirectX green flipped), backface culling like the game.
"""
import os
import threading
import time

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatProperty, StringProperty
from bpy_extras.io_utils import ExportHelper, ImportHelper
from mathutils import Matrix, Vector

from . import archive, evo, evo_write, images

HELPER_COLLECTION = "helpers"


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
            ca.data.foreach_set("color", [x for c in cols for x in c])
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
        self.finder = evo.AssetFinder(self.path, [self.o["texture_dir"]] if self.o["texture_dir"] else [])
        self.rfx = evo.read_rfx(self.finder) if self.o["normal_maps"] else None
        coll = bpy.data.collections.new(os.path.splitext(os.path.basename(self.path))[0])
        self.context.collection.children.link(coll)
        coll["evo_source"] = self.path
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
            if role in ("collision", "shadow", "connector", "hitbox") and not self.o["helpers"]:
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
            if role in ("collision", "shadow", "bone"):
                ob.display_type = "WIRE"
            objs[so.index] = ob
        self.context.view_layer.update()
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

class IMPORT_SCENE_OT_evo(bpy.types.Operator, ImportHelper):
    """Import Conflict: Desert Storm .EVO meshes"""
    bl_idname = "import_scene.evo"
    bl_label = "Import EVO"
    bl_options = {"REGISTER", "UNDO", "PRESET"}

    filename_ext = ".evo"
    filter_glob: StringProperty(default="*.evo;*.EVO", options={"HIDDEN"})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement, options={"HIDDEN", "SKIP_SAVE"})
    directory: StringProperty(subtype="DIR_PATH", options={"HIDDEN", "SKIP_SAVE"})

    scale: FloatProperty(name="Scale", default=0.01, min=1e-5, max=100.0,
                         description="Game units are centimetres; 0.01 imports in metres")
    armature: BoolProperty(name="Armature", default=True,
                           description="Characters: build the biped skeleton and bind the skin to it")
    animations: EnumProperty(name="Animations", default="NONE",
                             items=(("NONE", "None", "Bind pose only (import animations later with "
                                                     "File > Import > Conflict: Desert Storm Animation)"),
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
                          description="Also import connectors (attachment points), collision / shadow meshes, "
                                      "hit regions and bone proxy boxes into a hidden 'helpers' collection")
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

    def execute(self, context):
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


class IMPORT_ANIM_OT_prb(bpy.types.Operator, ImportHelper):
    """Import Conflict: Desert Storm .prb animations onto the active character armature"""
    bl_idname = "import_anim.evo_prb"
    bl_label = "Import PRB Animation"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".prb"
    filter_glob: StringProperty(default="*.prb;*.PRB;_*.BIN;_*.bin", options={"HIDDEN"})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement, options={"HIDDEN", "SKIP_SAVE"})
    directory: StringProperty(subtype="DIR_PATH", options={"HIDDEN", "SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob is not None and ob.type == "ARMATURE" and "evo_path" in ob

    def execute(self, context):
        arm = context.active_object
        try:
            scene = evo.load(arm["evo_path"])
        except (evo.EvoError, OSError) as e:
            self.report({"ERROR"}, "can't read the armature's model %s: %s" % (arm["evo_path"], e))
            return {"CANCELLED"}
        k = arm.get("evo_scale", 0.01)
        paths = [os.path.join(self.directory, f.name) for f in self.files if f.name] or [self.filepath]
        done, skipped = 0, []
        for path in paths:
            try:
                anim = evo.read_prb(path)
            except (evo.EvoError, OSError) as e:
                skipped.append("%s (%s)" % (os.path.basename(path), e))
                continue
            if not evo.compatible(scene, anim):
                skipped.append("%s (%d tracks, the model has %d objects)" % (anim.name, len(anim.tracks),
                                                                              len(scene.objects)))
                continue
            apply_animation(arm, scene, anim, k, assign=True)
            done += 1
        if skipped:
            self.report({"WARNING"}, "skipped: " + ", ".join(skipped))
        return {"FINISHED"} if done else {"CANCELLED"}


class IMPORT_SCENE_OT_ds_extract(bpy.types.Operator):
    """Extract the Conflict: Desert Storm .dat archives (meshes, textures, animations, tables) to a folder"""
    bl_idname = "import_scene.ds_extract"
    bl_label = "Extract Game Archives"

    game_dir: StringProperty(name="Game Folder", subtype="DIR_PATH",
                             description="Folder with DesertStorm.exe and the .dat archives")
    out_dir: StringProperty(name="Output Folder", subtype="DIR_PATH",
                            description="Where to write one folder per archive")
    png: BoolProperty(name="PNG Copies", default=False,
                      description="Also write PNG copies of the DDS/TGA textures (Blender reads DDS/TGA itself; "
                                  "PNG is handy for other programs)")

    def invoke(self, context, event):
        if not self.game_dir:
            self.game_dir = archive.find_game_dir() or ""
        if not self.out_dir:
            self.out_dir = os.path.join(os.path.expanduser("~"), "Documents", "Conflict Desert Storm extracted")
        return context.window_manager.invoke_props_dialog(self, width=520)

    @classmethod
    def poll(cls, context):
        return _job.get("thread") is None or not _job["thread"].is_alive()

    def execute(self, context):
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
        self.report({"INFO"}, "Extracted %d files (%d named) in %.0f s to %s" % (total, named, secs, _job["out"]))
        return {"FINISHED"}


_job = {}


def _draw_status(header, context):
    """Status bar while extracting: progress bar (Blender 4.0+) or text, and the cancel hint."""
    layout = header.layout
    frac = _job.get("fraction", 0.0)
    text = _job.get("text", "")
    layout.label(text="Extracting game archives", icon="FILE_ARCHIVE")
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
                return os.path.splitext(os.path.basename(bpy.path.abspath(path)) or n.image.name)[0]
    name = mat.name
    if name.startswith("EVO "):
        name = name[4:]
    if name.endswith(" +N"):
        name = name[:-3]
    return name.split(".")[0] if name.count(".") == 1 and name.rsplit(".", 1)[1].isdigit() else name


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
        mw = ob.matrix_world
        rot = mw.to_3x3()
        conv = Matrix([list(r) for r in to_local])
        conv3 = conv.to_3x3()
        parts = {}
        for tri in me.loop_triangles:
            mat = ob.material_slots[tri.material_index].material if tri.material_index < len(ob.material_slots) else None
            tex = texture_of(mat)
            part = parts.setdefault(tex, {"verts": {}, "p": [], "n": [], "uv": [], "t": []})
            idx = []
            for li in tri.loops:
                vi = me.loops[li].vertex_index
                n = corner_normals[li].vector if corner_normals is not None else me.loops[li].normal
                u = tuple(uv[li].uv) if uv else (0.0, 0.0)
                key = (vi, round(u[0], 5), round(u[1], 5), round(n[0], 3), round(n[1], 3), round(n[2], 3))
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
                idx.append(part["verts"][key])
            part["t"].append((idx[0], idx[2], idx[1]))      # the mirror flips the winding back
        return [evo_write.Part(tex, d["p"], d["n"], d["uv"], d["t"]) for tex, d in parts.items() if d["t"]]
    finally:
        eob.to_mesh_clear()


def skin_part(context, ob, arm, k):
    """evo_write.SkinPart for a character mesh bound to an armature: rest pose, model space, weights from the vertex
    groups (named after the bones, as the importer makes them)."""
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
        groups = {g.index: g.name for g in ob.vertex_groups}
        src = ob.data                               # vertex groups live on the original mesh
        verts, pos, nrm, uvs, weights, tris = {}, [], [], [], [], []
        for tri in me.loop_triangles:
            idx = []
            for li in tri.loops:
                vi = me.loops[li].vertex_index
                n = corner_normals[li].vector if corner_normals is not None else me.loops[li].normal
                u = tuple(uv[li].uv) if uv else (0.0, 0.0)
                key = (vi, round(u[0], 5), round(u[1], 5), round(n[0], 3), round(n[1], 3), round(n[2], 3))
                if key not in verts:
                    verts[key] = len(pos)
                    pos.append(to_game_point(to_model @ me.vertices[vi].co, k))
                    gn = Vector(to_game_dir(rot @ n))
                    if gn.length > 0:
                        gn.normalize()
                    nrm.append(tuple(gn))
                    uvs.append((u[0], 1.0 - u[1]))
                    vw = src.vertices[vi].groups if vi < len(src.vertices) else []
                    weights.append([(groups[g.group], g.weight) for g in vw if g.group in groups and g.weight > 0])
                idx.append(verts[key])
            tris.append((idx[0], idx[2], idx[1]))
        return evo_write.SkinPart(pos, nrm, uvs, tris, weights)
    finally:
        eob.to_mesh_clear()
        if arm:
            arm.data.pose_position = rest
            context.view_layer.update()


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


def base_name(name):
    head, _, tail = name.rpartition(".")
    return head if head and tail.isdigit() else name


class EXPORT_SCENE_OT_evo(bpy.types.Operator, ExportHelper):
    """Write a Conflict: Desert Storm model (.evo) based on a game model: new geometry for its parts"""
    bl_idname = "export_scene.evo"
    bl_label = "Export EVO"
    bl_options = {"REGISTER"}

    filename_ext = ".evo"
    filter_glob: StringProperty(default="*.evo;*.EVO", options={"HIDDEN"})
    template: StringProperty(name="Based On", subtype="FILE_PATH",
                             description="The game model this one replaces (.evo from the extracted folders): its "
                                         "attachment points, materials and part layout are kept. Empty = the model "
                                         "the selected objects were imported from")
    scale: FloatProperty(name="Scale", default=0.01, min=1e-5, max=100.0,
                         description="Blender units per game centimetre (0.01 = the model is in metres)")
    selected_only: BoolProperty(name="Selected Only", default=True)
    save_textures: BoolProperty(name="Save Textures", default=True,
                                description="Save new textures (materials whose image isn't one of the game model's own) "
                                            "as .dds next to the model, named after the image")

    def source_of(self, obs):
        for ob in obs:
            for coll in ob.users_collection:
                if coll.get("evo_source"):
                    return coll["evo_source"]
        return ""

    def invoke(self, context, event):
        obs = context.selected_objects or context.view_layer.objects
        if not self.template:
            self.template = self.source_of(obs)
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        obs = [o for o in (context.selected_objects if self.selected_only else context.view_layer.objects)
               if o.type == "MESH"]
        template = bpy.path.abspath(self.template) if self.template else self.source_of(obs)
        if not template or not os.path.isfile(template):
            self.report({"ERROR"}, "Pick the game model this one is based on (Based On)")
            return {"CANCELLED"}
        if not obs:
            self.report({"ERROR"}, "No mesh objects to export")
            return {"CANCELLED"}
        k = self.scale
        data = open(template, "rb").read()
        scene = evo.Scene(evo.read(data))
        targets = [so for so in scene.objects if so.node.kind in (0, 3) and so.node.meshes and so.role == "mesh"]
        skins = [so for so in scene.objects if so.role == "skin"]
        if skins:
            targets = []                                    # characters: the skin is what gets replaced
        by_name = {}
        for ob in obs:
            by_name.setdefault(base_name(ob.name).upper(), []).append(ob)
        parts = {}
        used = set()
        for so in targets:
            match = by_name.get(so.name.upper(), [])
            if len(targets) == 1:
                match = obs                                 # one-part model: every selected mesh is that part
            if not match:
                continue
            to_local = game_matrix_inverse(so.world)
            new = []
            for ob in match:
                new += mesh_parts(context, ob, to_local, k)
                used.add(ob.name)
            if new:
                parts[so.node.name] = new
        for so in skins:
            match = by_name.get(so.name.upper(), [])
            if not match and len(skins) == 1:
                match = [o for o in obs if armature_of(o)] or obs
            if not match:
                continue
            if len(match) > 1:
                self.report({"ERROR"}, "Join the character's meshes into one object (%s)" % ", ".join(o.name for o in match))
                return {"CANCELLED"}
            parts[so.node.name] = skin_part(context, match[0], armature_of(match[0]), k)
            used.add(match[0].name)
        if not parts:
            self.report({"ERROR"}, "No object matches a part of %s (%s)" % (
                os.path.basename(template), ", ".join(so.name for so in targets)))
            return {"CANCELLED"}
        name = os.path.splitext(os.path.basename(self.filepath))[0]
        try:
            notes = []
            out = evo_write.rebuild(data, parts, model_name=name, warnings=notes)
        except evo.EvoError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        with open(self.filepath, "wb") as f:
            f.write(out)
        if self.save_textures:
            game = {name.lower() for n in scene.nodes for _h, name in n.textures}
            saved = {}                                      # lower-case name -> file name
            folder = os.path.dirname(self.filepath)
            for ob in obs:
                if ob.name not in used:
                    continue
                for slot in ob.material_slots:
                    img = image_of(slot.material)
                    tex = texture_of(slot.material)
                    if img is None or tex.lower() in game or tex.lower() == "default" or tex.lower() in saved:
                        continue
                    saved[tex.lower()] = tex + ".dds"
                    problem = save_dds(img, os.path.join(folder, tex + ".dds"))
                    if problem:
                        notes.append(problem)
            if saved:
                notes.append("saved " + ", ".join(sorted(saved.values())))
        skipped = [o.name for o in obs if o.name not in used]
        textures = sorted({p.texture for ps in parts.values() if isinstance(ps, list) for p in ps}) or ["(character skin)"]
        msg = "Wrote %s: %d part(s), textures %s" % (os.path.basename(self.filepath), len(parts), ", ".join(textures))
        if skipped:
            msg += "; not exported (no matching part): " + ", ".join(skipped)
        if notes:
            msg += "; " + "; ".join(notes)
        warn = skipped or any(not n.startswith("saved ") for n in notes)
        self.report({"WARNING"} if warn else {"INFO"}, msg)
        return {"FINISHED"}


def action_of(arm):
    ad = arm.animation_data
    return ad.action if ad else None


class EXPORT_ANIM_OT_prb(bpy.types.Operator, ExportHelper):
    """Write the armature's current action as a Conflict: Desert Storm animation (.prb)"""
    bl_idname = "export_anim.evo_prb"
    bl_label = "Export PRB Animation"
    bl_options = {"REGISTER"}

    filename_ext = ".prb"
    filter_glob: StringProperty(default="*.prb;*.PRB", options={"HIDDEN"})
    fps: FloatProperty(name="Keys per Second", default=15.0, min=1.0, max=60.0,
                       description="How often the animation is sampled (the game's own use 6-15)")
    template: StringProperty(name="Footsteps From", subtype="FILE_PATH",
                             description="A game animation whose footstep events (walk / run cycles) the new one "
                                         "keeps. Empty = the game animation with the same name, if there is one")

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob is not None and ob.type == "ARMATURE" and ob.get("evo_path") and action_of(ob) is not None

    def invoke(self, context, event):
        act = action_of(context.active_object)
        if act is not None:
            if act.get("evo_fps"):
                self.fps = float(act["evo_fps"])
            if not self.filepath or os.path.basename(self.filepath) in ("", "untitled.prb"):
                self.filepath = act.name + ".prb"
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        arm = context.active_object
        act = action_of(arm)
        k = arm.get("evo_scale", 0.01)
        try:
            scene = evo.load(arm["evo_path"])
        except (evo.EvoError, OSError) as e:
            self.report({"ERROR"}, "Can't read the character's model %s: %s" % (arm["evo_path"], e))
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
                template = (evo.read_prb(tdata), evo_write.prb_events(tdata))
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
                        if template and evo.compatible(scene, template[0]):
                            local = Matrix([list(r) for r in template[0].local(i, min(f, template[0].frames - 1))])
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
        if template:
            events, nevents, flag, tframes = template[1]
            out = evo_write.write_prb(tracks, self.fps, points, events, nevents, flag, tframes)
        else:
            out = evo_write.write_prb(tracks, self.fps, points)
        with open(self.filepath, "wb") as f:
            f.write(out)
        msg = "Wrote %s: %d frames at %g per second" % (os.path.basename(self.filepath), frames, self.fps)
        if template:
            msg += ", footsteps from %s" % os.path.basename(tpath)
        self.report({"INFO"}, msg)
        return {"FINISHED"}


def menu_func_export(self, context):
    self.layout.operator(EXPORT_SCENE_OT_evo.bl_idname, text="Conflict: Desert Storm (.evo)")
    self.layout.operator(EXPORT_ANIM_OT_prb.bl_idname, text="Conflict: Desert Storm Animation (.prb)")


def menu_func_import(self, context):
    self.layout.operator(IMPORT_SCENE_OT_evo.bl_idname, text="Conflict: Desert Storm (.evo)")
    self.layout.operator(IMPORT_ANIM_OT_prb.bl_idname, text="Conflict: Desert Storm Animation (.prb)")
    self.layout.operator(IMPORT_SCENE_OT_ds_extract.bl_idname, text="Conflict: Desert Storm Archives (extract)")


CLASSES = (IMPORT_SCENE_OT_evo, IMPORT_ANIM_OT_prb, IMPORT_SCENE_OT_ds_extract, EXPORT_SCENE_OT_evo,
           EXPORT_ANIM_OT_prb)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
