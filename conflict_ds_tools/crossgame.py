"""Converting characters between the Conflict games (pure Python - used by the Blender add-on's cross-game export and
by the porter, conflict_ds_tools/porting.py): skin parts per texture, split to the 16-bit limits, and moved from one
game's skeleton rest pose onto another's."""
import collections
import math
import re

from . import evo, evo_write


def split_skin(part, limit=65535):
    """A SkinPart cut into pieces of at most `limit` vertices and triangles (a skin object's index buffer and its
    triangle-neighbour table are 16-bit). Pieces grow over shared vertices (connected patches, so each stays a short
    triangle strip); shared vertices are copied into each piece that uses them."""
    if len(part.positions) <= limit and len(part.triangles) <= limit:
        return [part]
    key = [tuple(round(c, 2) for c in p) for p in part.positions]    # UV seams split vertices, not the surface
    by_vertex = {}
    for ti, t in enumerate(part.triangles):
        for i in t:
            by_vertex.setdefault(key[i], []).append(ti)
    done = [False] * len(part.triangles)
    pieces = []
    seed = 0
    while True:
        while seed < len(done) and done[seed]:
            seed += 1
        if seed >= len(done):
            break
        remap, tris, queue = {}, [], collections.deque([seed])
        done[seed] = True
        while queue:
            ti = queue.popleft()
            t = part.triangles[ti]
            if len(tris) >= limit or len(remap) + sum(1 for i in t if i not in remap) > limit:
                for rest in [ti, *queue]:           # full: these start the next pieces
                    done[rest] = False
                    seed = min(seed, rest)
                break
            for i in t:
                if i not in remap:
                    remap[i] = len(remap)
            tris.append(tuple(remap[i] for i in t))
            for i in t:
                for nb in by_vertex[key[i]]:
                    if not done[nb]:
                        done[nb] = True
                        queue.append(nb)
        if tris:
            pieces.append((remap, tris))
    out = []
    for remap, tris in pieces:
        order = sorted(remap, key=remap.get)
        out.append(evo_write.SkinPart([part.positions[i] for i in order], [part.normals[i] for i in order],
                                      [part.uvs[i] for i in order], tris, [part.weights[i] for i in order],
                                      texture=part.texture))
    return out


def character_skin_parts(groups, own_texture, limit=65535):
    """The SkinParts of a character export from [(texture, SkinPart)] (one per material of each mesh): the same
    texture joined, then split into pieces of at most `limit` vertices / triangles (16-bit in a skin object),
    biggest first (it replaces the model's skin object; the others become skin objects of their own). Each part names
    its texture, except one that is the template skin's own texture (or "Default"): that keeps the template's - so a
    plain one-material character exports exactly as before. Returns the list (one item = the old single skin)."""
    joined = {}
    for tex, part in groups:
        if not part.triangles:
            continue
        key = tex.lower()
        if key in joined:
            m = joined[key][1]
            base = len(m.positions)
            m.positions += part.positions
            m.normals += part.normals
            m.uvs += part.uvs
            m.weights += part.weights
            m.triangles += [(a + base, b + base, c + base) for a, b, c in part.triangles]
        else:
            joined[key] = (tex, evo_write.SkinPart(list(part.positions), list(part.normals), list(part.uvs),
                                                   list(part.triangles), list(part.weights)))
    parts = []
    for key, (tex, part) in joined.items():
        texture = None if key in ("default", "untextured", (own_texture or "").lower()) else tex
        for piece in split_skin(part, limit):
            piece.texture = texture
            parts.append(piece)
    parts.sort(key=lambda p: -len(p.positions))
    return parts


TRUNK_PARTS = ("PELVIS", "SPINE", "NECK", "HEAD")

# Biped twist bones a skeleton may lack, by name -> the bone whose motion they follow. A ForeTwist hangs off the
# UPPERARM in Vietnam's Biped (off the forearm in Global Storm's), so falling back to the nearest ancestor left the
# forearm skin on the upper arm: flattened forearms, hands bent back as soon as an elbow bends.
TWIST_BONES = ((re.compile(r"FORETWIST\d*$"), "FOREARM"), (re.compile(r"UPARMTWIST\d*$"), "UPPERARM"),
               (re.compile(r"THIGHTWIST\d*$"), "THIGH"), (re.compile(r"CALFTWIST\d*$"), "CALF"))


def in_trunk(bone):
    """Bones that keep the source's proportions: pelvis, spine, neck and head. Arms and legs follow the target's
    joints (hands stay on its weapon grips, feet on its ground)."""
    b = bone.upper()
    return b.startswith("BIP01 ") and any(part in b for part in TRUNK_PARTS)


def twist_target(key, target_rest):
    """The target bone a twist bone (TWIST_BONES) stands in for, if the target has it, else None."""
    for pat, bone in TWIST_BONES:
        cand = pat.sub(bone, key)
        if cand != key and cand in target_rest:
            return cand
    return None


def trunk_rest(source_rest, target_rest, parents):
    """target_rest with the trunk bones (in_trunk, in both skeletons) moved: each keeps the target's rotation, its joint
    = the parent trunk joint + the source's parent-to-joint offset turned by the parent's rotation difference (the
    source's lengths, the target's pose); the first trunk bone keeps the target's joint. Bones off the trunk unchanged."""
    if not parents:
        return target_rest
    out = dict(target_rest)
    placed = {}

    def joint(m):
        return evo.mat_apply(m, (0.0, 0.0, 0.0))

    def place(b):
        if b in placed:
            return placed[b]
        p = parents.get(b)
        if p and in_trunk(p) and p in source_rest and p in target_rest:
            turn = evo.mat_mul(target_rest[p], evo.mat_invert_affine(source_rest[p]))
            off = [a - c for a, c in zip(joint(source_rest[b]), joint(source_rest[p]))]
            pos = [a + c for a, c in zip(place(p), evo.mat_apply(turn, off, 0.0))]
        else:
            pos = list(joint(target_rest[b]))
        placed[b] = pos
        return pos

    for b in source_rest:
        if in_trunk(b) and b in target_rest:
            m = [list(r) for r in target_rest[b]]
            for i in range(3):
                m[i][3] = place(b)[i]
            out[b] = m
    return out


def cross_repose(part, source_rest, target_rest, parents=None):
    """Moves a SkinPart (model space, weights by bone name) from one skeleton's rest pose onto another's: each vertex
    by the weighted blend of target_bone x source_bone^-1 over its bones (linear blend skinning), normals likewise.
    A weight on a bone the target lacks moves to the bone it twists (TWIST_BONES), else to its nearest ancestor
    (`parents`: source bone -> parent, upper-case) that the target has - also in part.weights, so the skin binds to it; without one it is dropped (the rest
    renormalised; none left = unmoved). Returns {missing bone: the ancestor used, or "-"}.
    The trunk (pelvis, spine, neck, head) keeps the source's own proportions: the games' Biped spines are split at
    different heights (Vietnam's SPINE3 joint 12 cm above Desert Storm's, neck and head level), and moving each
    vertex with its own trunk bone pulled the chest down while the head stayed - a long neck. Trunk bones take the
    target's rotation at a joint placed by the source's bone lengths (trunk_rest); limbs follow the target exactly."""
    target_rest = trunk_rest(source_rest, target_rest, parents)
    deltas, moved = {}, {}

    def present(key):
        twin = twist_target(key, target_rest)
        if twin:
            return twin
        seen = 0
        while key and key not in target_rest and seen < 64:
            key = (parents or {}).get(key)
            seen += 1
        return key if key in target_rest else None

    for i, (p, n, ws) in enumerate(zip(part.positions, part.normals, part.weights)):
        acc_p, acc_n, total = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], 0.0
        new_ws = []
        for name, w in ws:
            key = name.upper()
            if key not in target_rest:
                anc = present(key)
                moved[key] = anc or "-"
                if anc is None:
                    continue
                m = deltas.get(key)
                if m is None and key in source_rest:
                    # the part keeps its place on the bone it hung on: moved by the stand-in bone's delta
                    m = deltas[key] = evo.mat_mul(target_rest[anc], evo.mat_invert_affine(source_rest[anc]))                         if anc in source_rest else None
                key = anc
            else:
                m = deltas.get(key)
                if m is None and key in source_rest:
                    m = deltas[key] = evo.mat_mul(target_rest[key], evo.mat_invert_affine(source_rest[key]))
            new_ws.append((key, w))
            if m is None:
                continue
            q = evo.mat_apply(m, p)
            r = evo.mat_apply(m, n, 0.0)
            for j in range(3):
                acc_p[j] += w * q[j]
                acc_n[j] += w * r[j]
            total += w
        part.weights[i] = new_ws
        if total > 0:
            part.positions[i] = tuple(c / total for c in acc_p)
            ln = math.sqrt(sum(c * c for c in acc_n)) or 1.0
            part.normals[i] = tuple(c / ln for c in acc_n)
    return moved


HEAD_BONE = "BIP01 HEAD"


def _head_extent(points):
    """(top y, front z) of head vertices: highest point, and the frontmost one (models face -Z)."""
    return max(p[1] for p in points), min(p[2] for p in points)


def head_connector_offsets(target, parts, min_weight=0.5):
    """{object index: (dx, dy, dz) in its parent's space} that moves a target body's head connectors (HAT / HEAD on
    top, FACE in front - where Desert Storm hangs hats, helmets, glasses, cigars) by how much the converted head's top
    and front differ from the body's own head, so gear sits on the new head instead of floating over it or sinking
    into it. target = the body's evo.Scene; parts = the converted SkinParts (in its rest space, weights by bone name).
    Empty when either head can't be measured or nothing moves by 0.5 cm or more."""
    own = []
    for o in target.objects:
        if o.role != "skin":
            continue
        for m in o.node.meshes:
            for p, vw in zip(m.positions, target.vertex_weights(o, m)):
                if any(target.objects[b].name.upper() == HEAD_BONE and w >= min_weight for b, w in vw):
                    own.append(p)
    new = [p for part in parts for p, ws in zip(part.positions, part.weights)
           if any(n.upper() == HEAD_BONE and w >= min_weight for n, w in ws)]
    if len(own) < 20 or len(new) < 20:
        return {}
    (top0, front0), (top1, front1) = _head_extent(own), _head_extent(new)
    out = {}
    for o in target.objects:
        name = (o.name or "").upper()
        if "CONNECTOR" not in name or o.parent is None or target.objects[o.parent].name.upper() != HEAD_BONE:
            continue
        # (up / down only: moving a hat forward / back by the heads' crowns put The Great Escape guard's beret further
        # back than the base body's - checked in side profiles, it already sat like the Iraqi conscript's)
        world = (0.0, top1 - top0, 0.0) if ("HAT" in name or "HEAD CONNECTOR" in name) else \
            (0.0, 0.0, front1 - front0) if "FACE" in name else None
        if world is None or max(abs(c) for c in world) < 0.5:
            continue
        parent = target.objects[o.parent].world
        out[o.index] = tuple(evo.mat_apply(evo.mat_invert_affine(parent), world, 0.0))
    return out
