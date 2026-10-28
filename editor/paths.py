"""AI paths, waypoints and regions: the editor commands that make the places mission logic points at.

Waypoints (NEWENVWAYPT, conflict_ds_tools/env.py) are the game's patrol points: the waypoint table [0x63C9B0] holds
600 entries indexed by the record's id, each with a wait on arrival (ms) and the id of the next waypoint (-1 = the
path ends). A path is a chain of them; the shipped levels' guard routes are such chains (Mission 1: CELL_BLOCK_GUARD_0
-> _1 -> ...). A soldier walks one when the mission starts it on the chain's first waypoint (set_path, API +0x110) -
the editor does that for patrols (logic.py set_patrol) and for the rules' "Walk a path" action (missionrules.py).

Regions (REGN) are boxes (type 0: centre, rotation, min / max corner offsets) or spheres (type 1: centre, radius);
the game reads exactly the count before the REGN lines (env.py keeps it).
"""
from __future__ import annotations

import re

from commands import CommandError, Session, command, _unique, _vec  # noqa: F401

MAX_WAYPOINTS = 600


def _env(s: Session):
    if s.env is None:
        raise CommandError("no level open")
    return s.env


def _wps(s):
    return {r["id"]: r for r in _env(s).find("NEWENVWAYPT") if r.fields}


def _free_ids(s, n):
    used = set(_wps(s))
    out = [i for i in range(MAX_WAYPOINTS) if i not in used][:n]
    if len(out) < n:
        raise CommandError(f"the game holds {MAX_WAYPOINTS} waypoints per level - delete some first")
    return out


def _clean_name(name, what="name"):
    name = re.sub(r"[^A-Za-z0-9_]", "_", str(name or "").strip()).upper()[:28]
    if not name:
        raise CommandError(f"{what}: letters, digits and _")
    return name


def _insert_waypoint(s, wid, name, pos, wait_ms=0, nxt=-1):
    env = _env(s)
    end = next((r.index for r in env.records if r.kind == "ENDWAYPTS"), None)
    if end is None:
        raise CommandError("this scene has no waypoint section")
    x, y, z = pos
    v = env.version
    if v >= 190:
        raise CommandError("adding waypoints to Global Storm scenes is not supported yet (their layout is not mapped)")
    # version gates as env._waypoint; the extra values are the shipped Vietnam defaults (u1 32, -1 -1 0 -65332)
    line = f'NEWENVWAYPT {wid} {32 if v >= 107 else 0} "{name}" {x:.6f} {y:.6f} {z:.6f} 0.000000 0.000000 0.000000 '
    line += f'{int(wait_ms)} ' + ("0.00 " if v >= 107 else "") + f'{int(nxt)} '
    if v >= 79: line += "-1 -1 "
    if v >= 82: line += "0 "
    if v >= 88: line += "-65332 "
    env.lines.insert(end, line)
    env._parse()
    return env.find("NEWENVWAYPT", id=wid)[0]


def chains(s) -> list[dict]:
    """Paths in the scene: chains of waypoints by their next links. A chain starts at a waypoint nobody points at
    (or, for a loop, at its lowest id)."""
    wps = _wps(s)
    nxt = {i: r["next"] for i, r in wps.items()}
    pointed = {n for n in nxt.values() if n in wps}
    out, seen = [], set()

    def walk(start):
        ids, i = [], start
        while i in wps and i not in ids:
            ids.append(i)
            i = nxt[i]
        return ids, i == start and len(ids) > 1

    for start in sorted(i for i in wps if i not in pointed and nxt[i] in wps):
        ids, loop = walk(start)
        seen.update(ids)
        out.append({"start": start, "ids": ids, "loop": loop})
    for start in sorted(wps):                       # loops: no waypoint without a predecessor
        if start in seen or nxt[start] not in wps:
            continue
        ids, loop = walk(start)
        if loop:
            seen.update(ids)
            out.append({"start": start, "ids": ids, "loop": True})
    for c in out:
        r = wps[c["start"]]
        c["name"] = re.sub(r"_?\d+$", "", str(r["name"])) or str(r["name"])
        c["names"] = [str(wps[i]["name"]) for i in c["ids"]]
        c["points"] = [list(wps[i]["pos"]) for i in c["ids"]]
        c["waits"] = [wps[i]["wait_ms"] / 1000 for i in c["ids"]]
    return out


def _walk_fix(s, pts, snap):
    """Walk-map check of path points: (points, blocked indices). snap = blocked points move to the nearest walkable
    cell (the soldiers can't plan a route into a blocked one - the game's AI quadtree, studio.py)."""
    import studio
    out, blocked = [], []
    for k, p in enumerate(pts):
        ok = studio.walkable_at(s, p[0], p[2])
        if ok is False:
            n = studio.nearest_walkable(s, p[0], p[2]) if snap else None
            if n:
                p = [n[0], p[1] if n[1] is None else n[1], n[2]]
            else:
                blocked.append(k)
        out.append(p)
    return out, blocked


def _blocked_note(blocked, total):
    if not blocked:
        return {}
    which = "all its points" if len(blocked) == total else "point(s) " + ", ".join(str(k + 1) for k in blocked)
    return {"blocked": blocked, "warning": f"{which} on blocked cells of the AI walk map - soldiers can't plan a route "
                                           f"there (fine indoors, where the level's hand-placed markers route them); "
                                           f"snap = true moves them to the nearest walkable cell"}


@command("Paths in the scene (chains of waypoints by their next links): start id, ids, names, points, waits (s), "
         "loop - the shipped levels' guard routes and the ones drawn here.")
def path_list(s: Session):
    return {"paths": chains(s), "waypoints_used": len(_wps(s)), "max": MAX_WAYPOINTS}


@command("Add one waypoint (a point soldiers can walk to / be teleported to, or a path point).", mutates=True,
         pos="[x, y, z]", name="name (default WAYPOINT_n)", wait="seconds a unit waits here", next="next waypoint id (-1 none)",
         snap="on a blocked walk-map cell: move it to the nearest walkable one")
def waypoint_add(s: Session, pos, name=None, wait=0, next=-1, snap=False):
    wid = _free_ids(s, 1)[0]
    nm = _unique(s, "NEWENVWAYPT", "name", _clean_name(name or f"WAYPOINT_{wid}"))
    if int(next) != -1 and int(next) not in _wps(s):
        raise CommandError(f"no waypoint {next}")
    (p,), blocked = _walk_fix(s, [_vec(pos)], snap)
    r = _insert_waypoint(s, wid, nm, p, float(wait) * 1000, int(next))
    return {"line": r.index + 1, "id": wid, "name": nm, "pos": list(p), **_blocked_note(blocked, 1)}


@command("Draw a path: a waypoint at each point (in order), each linked to the next; loop = the last leads back to "
         "the first. Soldiers walk it when given it (Assign path / a rule's 'Walk a path').", mutates=True,
         points="[[x, y, z], ...] (2 or more)", name="path name (waypoints NAME_0, NAME_1 ...)", loop="a closed loop",
         wait="seconds a unit waits at each point",
         snap="points on blocked walk-map cells move to the nearest walkable one")
def path_create(s: Session, points, name="PATH", loop=True, wait=0, snap=False):
    pts = [_vec(p) for p in points or []]
    if len(pts) < 2:
        raise CommandError("a path needs at least two points")
    pts, blocked = _walk_fix(s, pts, snap)
    base = _clean_name(name, "path name")
    taken = {str(r["name"]).upper() for r in _wps(s).values()}
    stem, n = base, 2
    while any(f"{stem}_{k}" in taken for k in range(len(pts))):
        stem, n = f"{base}{n}", n + 1
    ids = _free_ids(s, len(pts))
    wait_ms = max(0.0, float(wait)) * 1000
    for k, (wid, p) in enumerate(zip(ids, pts)):
        nxt = ids[k + 1] if k + 1 < len(ids) else (ids[0] if loop else -1)
        _insert_waypoint(s, wid, f"{stem}_{k}", p, wait_ms, nxt)
    return {"start": ids[0], "ids": ids, "name": stem, "loop": bool(loop), "points": [list(p) for p in pts],
            **_blocked_note(blocked, len(pts))}


def _chain_of(s, start):
    for c in chains(s):
        if c["start"] == int(start) or int(start) in c["ids"]:
            return c
    raise CommandError(f"no path through waypoint {start}")


@command("Change a path: loop on / off, the wait at every point (s), or rename it.", mutates=True,
         start="a waypoint id on the path", loop="closed loop", wait="seconds at each point", name="new path name")
def path_set(s: Session, start, loop=None, wait=None, name=None):
    c = _chain_of(s, start)
    wps = _wps(s)
    if loop is not None:
        wps[c["ids"][-1]]["next"] = c["ids"][0] if loop else -1
    if wait is not None:
        for i in c["ids"]:
            wps[i]["wait_ms"] = int(max(0.0, float(wait)) * 1000)
    if name:
        stem = _clean_name(name, "path name")
        for k, i in enumerate(c["ids"]):
            wps[i]["name"] = _unique(s, "NEWENVWAYPT", "name", f"{stem}_{k}")
    return _chain_of(s, c["ids"][0])


@command("Link one waypoint to the next (or -1 to end the path there).", mutates=True, waypoint="waypoint id",
         next="next waypoint id or -1")
def waypoint_link(s: Session, waypoint, next):
    wps = _wps(s)
    if int(waypoint) not in wps or (int(next) != -1 and int(next) not in wps):
        raise CommandError("unknown waypoint")
    wps[int(waypoint)]["next"] = int(next)
    return {"id": int(waypoint), "next": int(next)}


@command("Delete a whole path (all its waypoints); links into it from other waypoints end there instead.",
         mutates=True, start="a waypoint id on the path")
def path_delete(s: Session, start):
    c = _chain_of(s, start)
    gone = set(c["ids"])
    env = _env(s)
    for r in list(env.find("NEWENVWAYPT")):
        if r.fields and r["id"] not in gone and r["next"] in gone:
            r["next"] = -1
    for i in c["ids"]:
        rec = env.find("NEWENVWAYPT", id=i)
        if rec:
            env.delete(rec[0])
    names = set(c["names"])
    for sid, p in list(getattr(s, "patrols", {}).items()):
        if names & set(p.get("waypoints", [])):
            s.patrols.pop(sid)
    return {"deleted": c["ids"]}


@command("Add a trigger region: a box (size [x, y, z] in metres, turned by yaw degrees) or a sphere (radius m).",
         mutates=True, pos="[x, y, z] centre", name="name (letters, digits, _)", shape="box or sphere",
         size="box size in m [x, y, z] (default 10, 4, 10)", radius="sphere radius in m (default 5)", yaw="box turn (degrees)")
def region_add(s: Session, pos, name="REGION", shape="box", size=None, radius=5, yaw=0):
    env = _env(s)
    end = next((r.index for r in env.records if r.kind == "ENDREGIONS"), None)
    if end is None:
        raise CommandError("this scene has no regions section")
    nm = _unique(s, "REGN", "name", _clean_name(name, "region name"))
    x, y, z = _vec(pos)
    if shape == "sphere":
        rr = max(0.5, float(radius)) * 100
        line = f'REGN "{nm}" 1 {x:.6f} {y:.6f} {z:.6f} {rr:.6f} '
    elif shape == "box":
        sx, sy, sz = (float(v) * 50 for v in (size or (10, 4, 10)))
        line = (f'REGN "{nm}" 0 {x:.6f} {y:.6f} {z:.6f} 0.000000 {float(yaw) % 360:.6f} 0.000000 '
                f'{-sx:.6f} {-sy:.6f} {-sz:.6f} {sx:.6f} {sy:.6f} {sz:.6f} ')
    else:
        raise CommandError("shape: box or sphere")
    env.lines.insert(end, line)
    env._parse()                                   # the region count is kept by env._sync_counts
    r = env.find("REGN", name=nm)[0]
    return {"line": r.index + 1, "name": nm}


@command("Add a free camera (CAMFREE) - a cutscene shot: at pos, looking along rot [pitch (down +), yaw, roll] in "
         "degrees, with a vertical field of view. Rules show it with the 'Camera shot' action (camera n = the scene's "
         "n-th camera record; the first, PLAYERVIEW, is the player's own view).", mutates=True,
         pos="[x, y, z] cm", rot="[pitch, yaw, roll] degrees", name="name (letters, digits, _)", fov="degrees (default 60)")
def camera_add(s: Session, pos, rot, name="SHOT", fov=60):
    env = _env(s)
    end = next((r.index for r in env.records if r.kind == "ENDC"), None)
    if end is None:
        raise CommandError("this scene has no camera section")
    from commands import _nearest_zone
    if sum(1 for q in env.records if q.kind.startswith("CAM") and q.fields) >= 128:
        raise CommandError("the game holds 128 cameras per level - delete some first")
    nm = _unique(s, "CAMFREE", "name", _clean_name(name, "camera name"))
    x, y, z = _vec(pos)
    p, yw, rl = (float(v) for v in (list(rot) + [0, 0, 0])[:3])
    f = max(10.0, min(120.0, float(fov)))
    zone = _nearest_zone(s, "HLOI", (x, y, z)) or "Root"
    zeros = " ".join(["0.000000"] * 11)
    line = (f'CAMFREE "{nm}" 32897 128 "{zone}" {x:.6f} {y:.6f} {z:.6f} {p % 360:.6f} {yw % 360:.6f} {rl:.6f} 0 0 '
            f'{zeros} {f:.6f} {" ".join(["0.000000"] * 7)} 0 0.000000 ')
    env.lines.insert(end, line)
    env._parse()
    r = env.find("CAMFREE", name=nm)[0]
    if env.problems:
        raise CommandError("; ".join(env.problems[:2]))
    index = [q.index for q in env.records if q.kind.startswith("CAM") and q.fields].index(r.index)
    return {"line": r.index + 1, "name": nm, "index": index}
