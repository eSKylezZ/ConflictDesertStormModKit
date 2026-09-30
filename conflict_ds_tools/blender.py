"""Blender side of the Conflict: Desert Storm tools (File > Import > Conflict: Desert Storm ...).

- .evo import: props, vehicles (node hierarchy + the tracks, wheels and guns the game attaches), world pieces,
  cloth (frames as shape keys), characters (armature, skin weights, optionally all their animations).
- .prb import: animations onto a character armature made by the .evo import.
- Archive extraction: the game's .dat files -> folders of meshes, textures (+ PNG), animations and tables.
Materials: base texture, the .RFX normal map (DirectX green flipped), backface culling like the game.
"""
import os
import time

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatProperty, StringProperty
from bpy_extras.io_utils import ImportHelper
from mathutils import Matrix, Vector

from . import archive, evo

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

    def execute(self, context):
        game = bpy.path.abspath(self.game_dir)
        out = bpy.path.abspath(self.out_dir)
        if not os.path.isdir(game) or not archive.list_archives(game):
            self.report({"ERROR"}, "no .dat archives in " + game)
            return {"CANCELLED"}
        wm = context.window_manager
        wm.progress_begin(0, 1000)
        t = time.time()

        def progress(i, n):
            wm.progress_update(int(1000 * i / max(1, n)))

        summary = archive.extract(game, out, None, self.png, progress, log=lambda s: None)
        wm.progress_end()
        named = sum(s[1] for s in summary)
        total = named + sum(s[2] for s in summary)
        self.report({"INFO"}, "Extracted %d files (%d named) in %.0f s to %s" % (total, named, time.time() - t, out))
        return {"FINISHED"}


def menu_func_import(self, context):
    self.layout.operator(IMPORT_SCENE_OT_evo.bl_idname, text="Conflict: Desert Storm (.evo)")
    self.layout.operator(IMPORT_ANIM_OT_prb.bl_idname, text="Conflict: Desert Storm Animation (.prb)")
    self.layout.operator(IMPORT_SCENE_OT_ds_extract.bl_idname, text="Conflict: Desert Storm Archives (extract)")


CLASSES = (IMPORT_SCENE_OT_evo, IMPORT_ANIM_OT_prb, IMPORT_SCENE_OT_ds_extract)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
