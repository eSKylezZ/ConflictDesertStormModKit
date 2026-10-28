"""Editor commands for working safely and in bulk - the workflow layer of the GECK MCP (fnv-geck-mcp), translated to
this editor's model (records addressed by scene line, a mission = changes against a base level):

  status          what is open, unsaved, running - answers even while another command runs
  batch           several commands as ONE undo step, all or nothing; "$0.line" = a value of step 0's result
  preview         any command (or batch) run on the scene and rolled back: its result + what it would change
  set_fields      several fields of one record at once (the shape get_record returns)
  search          text / kind / distance / layer search with paging (total, returned, truncated)
  references      "Use Info": what a record uses and what uses it (crews, doors, layers, rules, objectives, the base
                  mission's DLL, the Lua script) - read it before deleting or renaming
  check_mission   one lint list: crashers, broken logic, route errors, duplicate ids / names, budget - each issue
                  with a severity and the command that fixes it
  mission_report  the mission against its base level: records added / removed / changed per kind, routes, logic
  revert_record   put a changed record back the way the base level has it
  recovery_*      autosaved copies of the open level / mission (every 5 minutes of edits) and restoring one
  screenshot      the editor's 3D view as an image (MCP image block) - the browser window renders it
  focus           select and frame a record in the editor window (the human sees what the assistant means)

Every change made through Session.call is also appended to <data>/_editor_cache/edits.log (one JSON line: time,
command, arguments, title, outcome), and save_mission / save_mod back up the files they overwrite to
<data>/_editor_cache/backups/<mod>/<NAME>-<time>/ first.
"""
from __future__ import annotations

import difflib
import glob
import hashlib
import json
import math
import os
import re
import shutil
import threading
import time

from commands import (COMMANDS, CommandError, Session, command, EnvFile, RouteFile, _CHANGES_VIEW, _action_title,  # noqa: F401
                      _base_ids, _crew, _find_file, _subject, _vec, get_record, pretty_name, set_field)
import commands as _commands

# Commands that write files or start things (not undoable) - read-only MCP mode hides them, batch / preview refuse them.
WRITES_FILES = {"save_mod", "save_mission", "save_script", "play", "create_weapon", "create_skin", "upload_file",
                "open_level", "open_mission", "undo", "redo", "history_jump", "copy", "new_level", "kit_preview",
                "record_set", "record_new", "record_revert", "record_delete", "string_set", "string_revert",
                "save_minimap", "save_prefab", "delete_prefab", "campaign_save", "campaign_reorder",
                "briefing_burn_map", "export_mod_zip", "recovery_save", "recovery_restore", "focus"}
# Answered without waiting for the editor's command lock (server.py): status while something slow runs, and the
# browser link (a screenshot waits for the browser, which must still be able to poll and reply).
UNLOCKED = {"status", "screenshot", "focus", "ui_poll", "ui_reply"}
# The browser's side of that link: not offered to MCP clients.
INTERNAL = {"ui_poll", "ui_reply"}
_NOT_IN_BATCH = WRITES_FILES | _CHANGES_VIEW | UNLOCKED | {"batch", "preview", "help", "history", "scene_problems",
                                                           "game_console", "recovery_list"}

_RECOVERY_EVERY = 300      # seconds of editing between automatic recovery copies
_RECOVERY_KEEP = 10
_BACKUPS_KEEP = 10


def _need_level(s: Session):
    if s.env is None:
        raise CommandError("no level open - open_level or open_mission first")
    return s.env


def _cache_dir(s: Session, *parts):
    path = os.path.join(s.data_dir, "_editor_cache", *parts)     # "_" folders aren't levels (list_levels)
    os.makedirs(path, exist_ok=True)
    return path


# ------------------------------------------------------------------------------------------------- status --
@command("What the editor has open and whether anything is unsaved or running: level, mission, unsaved changes, undo "
         "steps, record counts, the game process, whether an editor window is connected (screenshot / focus need "
         "one), the last recovery copy. Answers even while another command is running (busy names it).")
def status(s: Session):
    env = s.env
    game = getattr(s, "game", None)
    busy = getattr(s, "busy", None)
    return {"level": s.level, "mission": list(s.mission) if s.mission else None, "dirty": s.dirty,
            "revision": getattr(s, "revision", 0), "undo_steps": len(s.undo_stack), "redo_steps": len(s.redo_stack),
            "records": env.summary()["records"] if env is not None else {},
            "route_markers": len(s.routes.markers) if getattr(s, "routes", None) else 0,
            "data_dir": s.data_dir, "game_dir": s.game_dir,
            "game_running": bool(game is not None and game.poll() is None),
            "editor_window": _ui_connected(s),
            "busy": {"command": busy[0], "seconds": round(time.time() - busy[1], 1)} if busy else None,
            "last_recovery": getattr(s, "recovery_last_file", None)}


# -------------------------------------------------------------------------------------- batch / preview --
_REF = re.compile(r"^\$(\d+|last)((?:\.[A-Za-z0-9_]+)*)$")


def _resolve_refs(v, results):
    """"$2.line" / "$0.lines.1" / "$last.script_id" -> that value of an earlier step's result."""
    if isinstance(v, str):
        m = _REF.match(v)
        if not m:
            return v
        if not results:
            raise CommandError(f"{v}: no earlier step")
        i = len(results) - 1 if m.group(1) == "last" else int(m.group(1))
        if i >= len(results):
            raise CommandError(f"{v}: step {i} hasn't run yet")
        cur = results[i]
        for key in [k for k in m.group(2).split(".") if k]:
            try:
                cur = cur[int(key)] if isinstance(cur, list) else cur[key]
            except (KeyError, IndexError, ValueError, TypeError):
                raise CommandError(f"{v}: step {i}'s result has no {key!r}") from None
        return cur
    if isinstance(v, list):
        return [_resolve_refs(x, results) for x in v]
    if isinstance(v, dict):
        return {k: _resolve_refs(x, results) for k, x in v.items()}
    return v


def _run_steps(s: Session, steps):
    if not isinstance(steps, list) or not steps:
        raise CommandError("steps: a list of {command, args}")
    if len(steps) > 500:
        raise CommandError("steps: at most 500")
    results, titles = [], []
    for i, step in enumerate(steps):
        if not isinstance(step, dict) or "command" not in step:
            raise CommandError(f"step {i}: {{command, args}}")
        name = step["command"]
        if name not in COMMANDS:
            raise CommandError(f"step {i}: unknown command {name!r}")
        if name in _NOT_IN_BATCH:
            raise CommandError(f"step {i}: {name} can't run inside a batch (it writes files, changes the open level "
                               "or the history) - run it on its own")
        fn, _d, _a, mutates = COMMANDS[name]
        args = _resolve_refs(step.get("args") or {}, results)
        subject = _subject(s, args) if mutates else ""
        try:
            result = fn(s, **args)
        except CommandError as e:
            raise CommandError(f"step {i} ({name}): {e} - nothing was changed") from None
        except TypeError as e:
            raise CommandError(f"step {i} ({name}): bad arguments: {e} - nothing was changed") from None
        results.append(result)
        if mutates:
            titles.append(_action_title(name, subject, result))
    return results, titles


def _snapshot_changes(s: Session, before, after, limit=40):
    """What changed between two Session snapshots: scene line hunks, route markers, layers, logic, terrains."""
    out = {}
    a, b = before[0], after[0]
    if a != b:
        hunks = []
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
            if op == "equal":
                continue
            hunks.append({"op": op, **({"lines": [j1 + 1, j2]} if j2 > j1 else {"at": j1 + 1}),
                          "before": a[i1:i2][:5], "after": b[j1:j2][:5],
                          **({"more": max(i2 - i1, j2 - j1) - 5} if max(i2 - i1, j2 - j1) > 5 else {})})
        out["scene"] = {"hunks": hunks[:limit], "total_hunks": len(hunks), "lines_before": len(a), "lines_after": len(b)}
    ma, mb = before[1] or [], after[1] or []
    if ma != mb:
        changed = [i for i in range(min(len(ma), len(mb))) if ma[i] != mb[i]]
        out["routes"] = {"markers_before": len(ma), "markers_after": len(mb), "changed": changed[:limit]}
    if before[2] != after[2] or before[3] != after[3]:
        out["layers"] = {"before": sorted(set(before[2].values())), "after": sorted(set(after[2].values())),
                         "objects_moved": sorted({k for k in set(before[2]) | set(after[2])
                                                  if before[2].get(k) != after[2].get(k)})[:limit]}
    for idx, key in ((4, "terrains"), (5, "roads")):
        if len(before) > idx and before[idx] != after[idx]:
            out[key] = sorted(k for k in set(before[idx]) | set(after[idx]) if before[idx].get(k) != after[idx].get(k))
    if len(before) > 6 and before[6] != after[6]:
        xa, xb = json.loads(before[6]), json.loads(after[6])
        out["logic"] = sorted(k for k in set(xa) | set(xb) if xa.get(k) != xb.get(k))
    return out


def _dry(s: Session, steps):
    _need_level(s)
    before = s._snapshot()
    try:
        results, titles = _run_steps(s, steps)
        after = s._snapshot()
    finally:
        s._restore(before)
    return {"dry_run": True, "results": results, "would_do": titles, "changes": _snapshot_changes(s, before, after)}


@command("Run several commands as ONE undo step, all or nothing: if a step fails, every earlier step is rolled back "
         "and the error names the step. steps = [{command, args}]; an argument may be \"$<n>.<key>\" (or \"$last.<key>\") "
         "= that value of step n's result, e.g. duplicate then move \"$0.line\". Lines shift after inserts / deletes "
         "inside the batch too - use the lines earlier steps return. Queries (find, get_record, references ...) may be "
         "steps. dry_run = only report what would change (like preview).", mutates=True,
         steps="[{command, args}] (max 500)", dry_run="true = roll back and report the changes")
def batch(s: Session, steps, dry_run=False):
    _need_level(s)
    if dry_run:
        return _dry(s, steps)
    results, titles = _run_steps(s, steps)
    if not titles:
        title = "Batch (no changes)"
    elif len(titles) == 1:
        title = titles[0]
    else:
        title = f"Batch: {', '.join(dict.fromkeys(titles[:3]))}" + (f" +{len(titles) - 3} more" if len(titles) > 3 else "")
    return {"action": title, "results": results, "steps": len(results)}


@command("Try a scene-changing command without keeping it: runs it, reports its result and exactly what it would "
         "change (scene lines before / after, route markers, layers, logic, terrains), then rolls everything back. "
         "Nothing enters the undo history.", command="command name, e.g. move", args="its arguments {name: value}")
def preview(s: Session, command, args=None):   # noqa: A002 - the MCP argument name
    out = _dry(s, [{"command": command, "args": args or {}}])
    return {"dry_run": True, "result": out["results"][0], "would_do": out["would_do"][0] if out["would_do"] else None,
            "changes": out["changes"]}


# ------------------------------------------------------------------------------------------------ fields --
@command("Change several fields of one record in one undo step - the same {field: value} shape get_record returns "
         "(vectors pos / rot / scale as [x, y, z]). Fields are applied in the given order; a bad one undoes them all.",
         mutates=True, line="record line", fields="{field: value}")
def set_fields(s: Session, line, fields):
    if not isinstance(fields, dict) or not fields:
        raise CommandError("fields: {field: value} (get_record shows the names)")
    r = s.record(line)
    for k, v in fields.items():
        if k in ("line", "kind"):
            continue
        if k not in r.fields and f"{k}.x" not in r.fields:
            raise CommandError(f"{r.kind} has no field {k!r} - it has: {', '.join(sorted(r.as_dict())[:40])}")
        set_field(s, line, k, v)
    return get_record(s, line)


# ------------------------------------------------------------------------------------------------ search --
def _pos(r):
    try:
        p = r["pos"]
        return p if isinstance(p, tuple) and len(p) == 3 else None
    except KeyError:
        return None


@command("Find records: text (any part of the line or its readable name, any case), kind (HLOI, ENVI, REGN, "
         "NEWENVWAYPT, SWITCH, EMITTER ...), near [x, y, z] within radius cm (sorted nearest first), layer. Pages with "
         "offset / limit; the result says total / returned / truncated.",
         text="optional text to look for", kind="optional record keyword", near="optional [x, y, z]",
         radius="cm around near (default 5000)", layer="optional layer name (objects in it)",
         limit="results per page (default 50, max 1000)", offset="skip this many (paging)")
def search(s: Session, text="", kind=None, near=None, radius=5000, layer=None, limit=50, offset=0):
    env = _need_level(s)
    limit, offset = max(1, min(int(limit), 1000)), max(0, int(offset))
    needle = (text or "").lower()
    centre = _vec(near) if near is not None else None
    in_layer = {sid for sid, name in s.layers.items() if name == layer} if layer else None
    hits = []
    for r in env.records:
        if not r.kind or (kind and r.kind.upper() != str(kind).upper()):
            continue
        if in_layer is not None and (r.kind != "HLOI" or r.get("script_id") not in in_layer):
            continue
        name = pretty_name(r) if r.fields else r.kind
        if needle and needle not in r.text.lower() and needle not in name.lower():
            continue
        p, d = _pos(r), None
        if centre is not None:
            if p is None:
                continue
            d = math.dist(p, centre)
            if d > float(radius):
                continue
        hits.append({"line": r.index + 1, "kind": r.kind, "name": name, **({"pos": list(p)} if p else {}),
                     **({"script_id": r["script_id"]} if r.kind == "HLOI" and "script_id" in r.fields else {}),
                     **({"distance": round(d, 1)} if d is not None else {})})
    if centre is not None:
        hits.sort(key=lambda h: h["distance"])
    page = hits[offset:offset + limit]
    return {"total": len(hits), "returned": len(page), "offset": offset,
            "truncated": offset + len(page) < len(hits), "results": page}


# -------------------------------------------------------------------------------------------- references --
def _brief(r):
    return {"line": r.index + 1, "kind": r.kind, "name": pretty_name(r) if r.fields else r.kind}


def _script_mentions(s: Session, words, ids=()):
    """Lines of the open mission's Lua that name one of these words (as a whole word) or pass one of these script ids
    to a function (game.activate(12), {12, 14})."""
    if not s.mission or not (words or ids):
        return []
    try:
        path = _commands._script_path(s)
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read().splitlines()
    except (CommandError, OSError):
        return []
    pats = [re.compile(r"(?<![A-Za-z0-9_])" + re.escape(str(w)) + r"(?![A-Za-z0-9_])") for w in words if str(w)]
    pats += [re.compile(r"[({,]\s*" + str(int(i)) + r"\s*[,)}]") for i in ids]
    return [{"script_line": i + 1, "text": t.strip()[:160]} for i, t in enumerate(text)
            if any(p.search(t) for p in pats)][:30]


@command("\"Use Info\" for one record: what it uses (its zone, a vehicle's crew, a switch's doors and region, an "
         "emitter's region) and what uses it (vehicles it crews, switches that open it, its layer, objectives, event "
         "rules, patrols, records hanging from it, the base mission's DLL, lines of the mission's Lua that name it). "
         "Read it before deleting or renaming; safe_to_delete sums it up.", line="record line")
def references(s: Session, line):
    import interactive
    import logic
    env = _need_level(s)
    r = s.record(line)
    uses, used_by = [], []
    names = [str(r.get("name"))] if r.get("name") not in (None, "") else []
    parent = r.get("zone") if r.kind in ("HLOI", "SWITCH") else (r.get("parent") if r.kind == "ENVI" else None)
    if parent not in (None, "", "Root", "-NULL_OBJECT-"):
        z = env.find("ENVI", name=str(parent))
        uses.append({"as": "zone", "name": parent, **({"line": z[0].index + 1} if z else {"missing": True})})
    ids = {h["script_id"]: h for h in env.find("HLOI") if "script_id" in h.fields}
    regions = {str(g["name"]).upper(): g for g in env.find("REGN") if "name" in g.fields}

    if r.kind == "HLOI":
        sid = r["script_id"]
        for unit, seat, cid in _crew(r):
            c = ids.get(cid)
            uses.append({"as": f"crew seat {seat}", "script_id": cid, **({"line": c.index + 1} if c else {"missing": True})})
        for v in env.find("HLOI"):
            if any(cid == sid for _u, _s, cid in _crew(v)):
                used_by.append({"as": "crew of", **_brief(v)})
        for sw in env.find("SWITCH"):
            if sid in (sw.get("target_a"), sw.get("target_b")):
                used_by.append({"as": "door of switch", **_brief(sw)})
        if sid in s.layers:
            used_by.append({"as": "layer", "layer": s.layers[sid],
                            "start": s.layer_start.get(s.layers[sid], "hidden")})
        for o in getattr(s, "objectives", []):
            if o.get("type") == "kill" and sid in logic._ids(o.get("target")):
                used_by.append({"as": "kill objective", "objective": o["id"], "title": o.get("title")})
        for i, rule in enumerate(getattr(s, "rules", [])):
            if rule["trigger"]["type"] == "killed" and rule["trigger"]["target"] == sid:
                used_by.append({"as": "event rule trigger", "rule": i})
        if str(sid) in getattr(s, "patrols", {}):
            uses.append({"as": "patrol", **s.patrols[str(sid)]})
        slots = logic.base_targets(s).get(sid)
        if slots:
            used_by.append({"as": "base mission DLL", "dll": _commands._base_dll(s, s.level), "api_slots": slots,
                            "more": "object_logic(script_id) shows each step"})
        mentions = _script_mentions(s, names, ids=[sid])
    else:
        mentions = _script_mentions(s, names)

    if r.kind == "ENVI" and names:
        for c in env.records:
            if c is r or not c.fields:
                continue
            if (c.kind == "ENVI" and str(c.get("parent")) == names[0]) or \
               (c.kind in ("HLOI", "SWITCH") and str(c.get("zone")) == names[0]):
                used_by.append({"as": "hangs from it", **_brief(c)})
        used_by = used_by[:200]
    if r.kind == "SWITCH":
        for k in ("target_a", "target_b"):
            d = r.get(k)
            if d not in (None, -1):
                uses.append({"as": "door", "script_id": d, **({"line": ids[d].index + 1} if d in ids else {"missing": True})})
        reg = regions.get(str(r.get("region")).upper())
        if r.get("region"):
            uses.append({"as": "use region", "name": r["region"], **({"line": reg.index + 1} if reg else {"missing": True})})
        for i, rule in enumerate(getattr(s, "rules", [])):
            if rule["trigger"]["type"] == "switch" and str(rule["trigger"]["target"]).upper() == names[0].upper():
                used_by.append({"as": "event rule trigger", "rule": i})
    if r.kind == "EMITTER" and r.get("region"):
        h = r["region"] & 0xFFFFFFFF
        reg = next((g for g in regions.values() if interactive.name_hash(str(g["name"])) == h), None)
        uses.append({"as": "spawn region", **({"line": reg.index + 1, "name": reg["name"]} if reg else {"hash": h})})
    if r.kind == "REGN" and names:
        h = interactive.name_hash(names[0])
        for sw in env.find("SWITCH"):
            if str(sw.get("region")).upper() == names[0].upper():
                used_by.append({"as": "switch use region", **_brief(sw)})
        for e in env.find("EMITTER"):
            if e.get("region") and e["region"] & 0xFFFFFFFF == h:
                used_by.append({"as": "emitter spawn region", **_brief(e)})
        for o in getattr(s, "objectives", []):
            if o.get("type") == "region" and str(o.get("target")).upper() == names[0].upper():
                used_by.append({"as": "region objective", "objective": o["id"], "title": o.get("title")})
        for i, rule in enumerate(getattr(s, "rules", [])):
            if rule["trigger"]["type"].startswith("region") and str(rule["trigger"]["target"]).upper() == names[0].upper():
                used_by.append({"as": "event rule trigger", "rule": i})
    if r.kind == "NEWENVWAYPT" and names:
        for sid, p in getattr(s, "patrols", {}).items():
            if names[0] in p.get("waypoints", []):
                used_by.append({"as": "patrol waypoint", "script_id": int(sid)})
    blocking = [u for u in used_by if u["as"] in ("base mission DLL", "crew of", "door of switch", "kill objective",
                                                  "event rule trigger", "region objective", "hangs from it",
                                                  "switch use region", "patrol waypoint")]
    return {**_brief(r), "uses": uses, "used_by": used_by, "script": mentions,
            "safe_to_delete": not blocking and not mentions,
            **({"advice": "hide it in a layer instead of deleting it (set_layer) - something depends on it"}
               if blocking else {})}


# ------------------------------------------------------------------------------------------------ lint --
def _issue(severity, source, message, line=None, fix=None):
    return {"severity": severity, "source": source, "message": message,
            **({"line": line} if line is not None else {}), **({"fix": fix} if fix else {})}


_FIXERS = {"zones": "fix_zones", "hidden": "fix_hidden", "crews": "fix_crews", "rinfo": "fix_rinfo"}


def _walk_issues(s, env):
    """Patrols whose soldier or waypoints are on blocked cells of the AI walk map (soldiers can't plan a route there;
    indoors the level's hand-placed markers may still route them, so these are warnings)."""
    import studio
    if studio._walk_nodes(s) is None:
        return []
    wps = {str(w["name"]).upper(): w for w in env.find("NEWENVWAYPT") if w.fields}
    soldiers = {h["script_id"]: h for h in env.find("HLOI") if "script_id" in h.fields}
    out = []
    for sid, p in sorted(getattr(s, "patrols", {}).items(), key=lambda kv: int(kv[0])):
        h = soldiers.get(int(sid))
        if h is not None and studio.walkable_at(s, h["pos"][0], h["pos"][2]) is False:
            out.append(_issue("warning", "walk map", f"patrol soldier {h.get('stat') or sid} stands on a blocked walk-map "
                              "cell - he may not find a way onto his route; move him onto open ground",
                              h.index + 1))
        bad = [n for n in p.get("waypoints", []) if n.upper() in wps
               and studio.walkable_at(s, wps[n.upper()]["pos"][0], wps[n.upper()]["pos"][2]) is False]
        if bad:
            out.append(_issue("warning", "walk map", f"{h.get('stat') if h is not None else sid}'s route has "
                              f"{len(bad)} waypoint(s) on blocked walk-map cells ({', '.join(bad[:4])}"
                              f"{' ...' if len(bad) > 4 else ''}) - soldiers can't plan a route there",
                              wps[bad[0].upper()].index + 1))
    return out


@command("Check the open level / mission for everything known to break it, as one list of issues {severity error / "
         "warning / info, source, message, line, fix = the command that repairs it}: game crashers (scene_problems), "
         "scene parse problems, route errors, duplicate script ids, duplicate region / switch names, broken objectives / "
         "rules / patrols / switch doors, the game's script errors from its last run, unsaved changes; budget = also "
         "compare triangles / objects / texture memory with the largest shipped level (slower).",
         budget="true = include the scene budget", limit="max issues (default 200)")
def check_mission(s: Session, budget=False, limit=200):
    import logic
    env = _need_level(s)
    issues = []
    for p in env.problems:
        issues.append(_issue("error", "scene", p))
    for p in _commands.scene_problems(s):
        issues.append(_issue("error", "crash", p["problem"], p.get("line"), _FIXERS.get(p.get("fix"))))
    if s.routes:
        for p in s.routes.problems():
            issues.append(_issue("error", "routes", p, fix="route_unlink / route_delete"))
    import missionlogic
    try:
        for p in missionlogic.dll_item_problems(s):
            issues.append(_issue("warning", "logic", p["problem"], p["line"], "set_kit"))
    except Exception:  # noqa: BLE001 - a DLL the analyser can't read: no check
        pass
    seen = {}
    for h in env.find("HLOI"):
        if "script_id" in h.fields:
            sid = h["script_id"]
            if sid in seen:
                issues.append(_issue("error", "scene", f"script id {sid} is used by line {seen[sid]} and line "
                                     f"{h.index + 1} - the game keeps only one; set_field one of them to a free id",
                                     h.index + 1, "set_field"))
            else:
                seen[sid] = h.index + 1
    for kind in ("REGN", "SWITCH"):
        names = {}
        for g in env.find(kind):
            n = str(g.get("name", "")).upper()
            if not n:
                continue
            if n in names:
                issues.append(_issue("warning", "scene", f"{kind} name {g['name']} also on line {names[n]} - rules, "
                                     "objectives and the game's name hash can't tell them apart", g.index + 1, "set_field"))
            else:
                names[n] = g.index + 1
    ids = set(seen)
    regions = {str(g["name"]).upper() for g in env.find("REGN") if "name" in g.fields}
    for sw in env.find("SWITCH"):
        if not sw.fields:
            continue
        for k in ("target_a", "target_b"):
            d = sw.get(k)
            if d not in (None, -1) and d not in ids:
                issues.append(_issue("warning", "switch", f"switch {sw['name']} opens object {d}, which isn't in the "
                                     "scene", sw.index + 1, "switch_set"))
        if sw.get("region") and str(sw["region"]).upper() not in regions:
            issues.append(_issue("warning", "switch", f"switch {sw['name']}'s use region {sw['region']} is gone - it "
                                 "can't be used", sw.index + 1, "switch_set"))
    for w in logic._warnings(s):
        issues.append(_issue("warning", "logic", w, fix="set_objectives / set_rules / set_patrol"))
    issues += _walk_issues(s, env)
    if s.mission:
        try:
            for l in _commands.script_report(s)["lines"]:
                if "[fail]" in l.lower() or "error" in l.lower():
                    issues.append(_issue("warning", "script", l, fix="get_script / save_script"))
        except CommandError:
            pass
    if budget:
        import production
        b = production.scene_budget(s)
        for k, v in (b.get("ratio") or {}).items():
            if v > 1:
                issues.append(_issue("warning", "budget", f"{k}: {round(v * 100)} % of the largest shipped level "
                                     f"({b.get('base_level')}) - may run out of memory / frame time"))
    if s.dirty:
        issues.append(_issue("info", "session", "unsaved changes", fix="save_mission"))
    limit = max(1, int(limit))
    count = {sev: sum(1 for i in issues if i["severity"] == sev) for sev in ("error", "warning", "info")}
    return {**{f"{k}s": v for k, v in count.items()}, "ok": count["error"] == 0, "issues": issues[:limit],
            "truncated": len(issues) > limit}


# ------------------------------------------------------------------------------------- report / revert --
def _base_env(s: Session):
    path = _find_file(s.folder, s.level + ".ENV") if s.folder else None
    if not path:
        raise CommandError(f"the base scene of {s.level} isn't in the data folder")
    return EnvFile.load(path)


def _kinds(env):
    out = [""] * len(env.lines)
    for r in env.records:
        if 0 <= r.index < len(out):
            out[r.index] = r.kind
            for c in r.children:
                if 0 <= c.index < len(out):
                    out[c.index] = c.kind
    return out


def _line_map(base_lines, lines):
    """Opcodes between the base scene and the current one."""
    return difflib.SequenceMatcher(None, base_lines, lines, autojunk=False).get_opcodes()


@command("The open mission compared with its base level (like a plugin report): scene records added / removed / "
         "changed per kind, route markers, layers, terrains, roads, kit pieces, objectives, rules, patrols. details = "
         "also each changed record (base line -> line).", details="true = list the records", limit="max listed (default 200)")
def mission_report(s: Session, details=False, limit=200):
    env = _need_level(s)
    base = _base_env(s)
    bk, ck = _kinds(base), _kinds(env)
    per, listed = {}, []
    for op, i1, i2, j1, j2 in _line_map(base.lines, env.lines):
        if op == "equal":
            continue
        pairs = min(i2 - i1, j2 - j1) if op == "replace" else 0
        for k in range(pairs):
            kind = ck[j1 + k] or bk[i1 + k] or "(other)"
            per.setdefault(kind, {"added": 0, "removed": 0, "changed": 0})["changed"] += 1
            listed.append({"change": "changed", "kind": kind, "line": j1 + k + 1, "base_line": i1 + k + 1})
        for j in range(j1 + pairs, j2):
            kind = ck[j] or "(other)"
            per.setdefault(kind, {"added": 0, "removed": 0, "changed": 0})["added"] += 1
            listed.append({"change": "added", "kind": kind, "line": j + 1})
        for i in range(i1 + pairs, i2):
            kind = bk[i] or "(other)"
            per.setdefault(kind, {"added": 0, "removed": 0, "changed": 0})["removed"] += 1
            listed.append({"change": "removed", "kind": kind, "base_line": i + 1, "text": base.lines[i][:120]})
    out = {"level": s.level, "mission": list(s.mission) if s.mission else None, "scene": per,
           "unchanged": not per}
    rmd = (_find_file(s.folder, s.level + ".RMD") or _find_file(s.folder, s.level + ".RDA")) if s.folder else None
    if s.routes and rmd:
        b = RouteFile.load(rmd).markers
        m = s.routes.markers
        out["routes"] = {"base": len(b), "now": len(m),
                         "moved_or_relinked": sum(1 for i in range(min(len(b), len(m))) if b[i] != m[i])}
    out["layers"] = {name: sum(1 for v in s.layers.values() if v == name) for name in sorted(set(s.layers.values()))}
    out["terrains"], out["roads"] = sorted(getattr(s, "terrains", {})), sorted(getattr(s, "roads", {}))
    out["kit_pieces"] = len(getattr(s, "kits", {}))
    out["logic"] = {"objectives": len(getattr(s, "objectives", [])), "rules": len(getattr(s, "rules", [])),
                    "patrols": len(getattr(s, "patrols", {})),
                    "base_objective_changes": len(getattr(s, "base_overrides", {}) or {})}
    if details:
        limit = max(1, int(limit))
        out["records"] = listed[:limit]
        out["truncated"] = len(listed) > limit
    return out


@command("Put a changed record back the way its base level has it (the line's original text). Records this mission "
         "added have no original - delete them instead.", mutates=True, line="record line")
def revert_record(s: Session, line):
    env = _need_level(s)
    r = s.record(line)
    base = _base_env(s)
    idx = r.index
    for op, i1, i2, j1, j2 in _line_map(base.lines, env.lines):
        if not j1 <= idx < j2:
            continue
        if op == "equal":
            return {"line": idx + 1, "action": f"Revert {pretty_name(r)}", "note": "already as in the base level"}
        if op == "replace" and idx - j1 < i2 - i1:
            original = base.lines[i1 + idx - j1]
            title = f"Revert {pretty_name(r)}"
            env.lines[idx] = original
            env._parse()
            if env.problems:
                raise CommandError("; ".join(env.problems[:3]))
            return {"action": title, **get_record(s, idx + 1)}
        break
    raise CommandError(f"line {line} was added by this mission (the base level has no such record) - delete it instead")


# ------------------------------------------------------------------------------------------ edit log --
def _short(v, depth=0):
    if isinstance(v, str):
        return v if len(v) <= 400 else v[:200] + f"... ({len(v)} chars)"
    if isinstance(v, list):
        return [_short(x, depth + 1) for x in v[:50]] + ([f"... ({len(v)} items)"] if len(v) > 50 else [])
    if isinstance(v, dict) and depth < 6:
        return {k: _short(x, depth + 1) for k, x in v.items()}
    return v


def log_edit(s: Session, name, args, title=None, error=None, unchanged=False):
    """One JSON line per scene-changing command in <data>/_editor_cache/edits.log (never raises)."""
    try:
        entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "command": name, "args": _short(args or {}),
                 "level": s.level, "mission": list(s.mission) if s.mission else None}
        if error is not None:
            entry["error"] = str(error)[:500]
        else:
            entry["title"] = title
            if unchanged:
                entry["unchanged"] = True
        with open(os.path.join(_cache_dir(s), "edits.log"), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:  # noqa: BLE001 - logging must never break an edit
        pass


# ------------------------------------------------------------------------------------------ recovery --
def _recovery_state(s: Session):
    snap = s._snapshot()
    return {"version": 1, "time": time.strftime("%Y-%m-%d %H:%M:%S"), "level": s.level,
            "mission": list(s.mission) if s.mission else None, "blank": bool(getattr(s, "blank", False)),
            "lines": snap[0], "routes": [m.as_dict(i) for i, m in enumerate(snap[1])] if snap[1] is not None else None,
            "layers": {str(k): v for k, v in snap[2].items()}, "layer_start": snap[3], "terrains": snap[4],
            "roads": snap[5], "extras": snap[6], "kits": getattr(s, "kits", {}),
            "undo_titles": [e[0] for e in s.undo_stack]}


def write_recovery(s: Session, force=False):
    """A recovery copy of the open level / mission; skipped when nothing is unsaved or it equals the last copy."""
    if s.env is None or (not s.dirty and not force):
        return None
    state = _recovery_state(s)
    body = json.dumps(state, default=str)
    digest = hashlib.sha1(json.dumps({k: v for k, v in state.items() if k not in ("time", "undo_titles")},
                                     default=str).encode()).hexdigest()
    if digest == getattr(s, "recovery_digest", None):
        return getattr(s, "recovery_last_file", None)
    folder = _cache_dir(s, "recovery")
    name = f"{(s.mission[1] if s.mission else s.level)}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    s.recovery_digest, s.recovery_last_file, s.recovery_time = digest, name, time.time()
    for old in sorted(glob.glob(os.path.join(folder, "*.json")), key=os.path.getmtime)[:-_RECOVERY_KEEP]:
        try:
            os.remove(old)
        except OSError:
            pass
    return name


def after_change(s: Session):
    """Called by Session.call after each scene change: a recovery copy every few minutes of editing."""
    try:
        if time.time() - getattr(s, "recovery_time", 0) >= _RECOVERY_EVERY:
            if getattr(s, "recovery_time", 0) == 0:
                s.recovery_time = time.time()      # the first change of a session starts the clock
            else:
                write_recovery(s)
    except Exception:  # noqa: BLE001
        pass


@command("Write a recovery copy of the open level / mission now (also written by itself every 5 minutes while there "
         "are unsaved changes; the newest 10 are kept in <data>/_editor_cache/recovery).")
def recovery_save(s: Session):
    _need_level(s)
    name = write_recovery(s, force=True)
    return {"file": name, "folder": _cache_dir(s, "recovery")}


@command("Recovery copies, newest first: file, time, level, mission, undo steps they held.")
def recovery_list(s: Session):
    folder = _cache_dir(s, "recovery")
    out = []
    for path in sorted(glob.glob(os.path.join(folder, "*.json")), key=os.path.getmtime, reverse=True):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            out.append({"file": os.path.basename(path), "time": d.get("time"), "level": d.get("level"),
                        "mission": d.get("mission"), "changes": len(d.get("undo_titles") or []),
                        "last_change": (d.get("undo_titles") or [None])[-1]})
        except (OSError, ValueError):
            continue
    return {"copies": out, "folder": folder, "unsaved_now": s.dirty}


@command("Open a recovery copy (recovery_list names them): its level with every change it held, as unsaved changes "
         "(save_mission keeps them). Refused while the open level has unsaved changes unless discard_changes.",
         file="recovery file name", discard_changes="true = drop the open level's unsaved changes")
def recovery_restore(s: Session, file, discard_changes=False):
    if s.dirty and not discard_changes:
        raise CommandError("the open level has unsaved changes - save_mission first, or pass discard_changes: true "
                           "(recovery_save keeps a copy of them)")
    path = os.path.join(_cache_dir(s, "recovery"), os.path.basename(file))
    if not os.path.exists(path):
        raise CommandError(f"no recovery copy {file!r} - recovery_list names them")
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    _commands.open_level(s, d["level"])
    from conflict_ds_tools.routes import Marker
    routes = None
    if d.get("routes") is not None:
        routes = [Marker(pos=tuple(m["pos"]), links=list(m["links"]), flags=m["flags"], weight=m["weight"])
                  for m in d["routes"]]
        if s.routes is not None:   # keep the fields the JSON doesn't carry (unknown / filler) where markers remain
            for i, m in enumerate(routes):
                if i < len(s.routes.markers):
                    m.unknown, m.filler = s.routes.markers[i].unknown, s.routes.markers[i].filler
    s._restore((d["lines"], routes, {int(k): v for k, v in d["layers"].items()}, d["layer_start"],
                d.get("terrains") or {}, d.get("roads") or {}, d.get("extras") or "{}", d.get("kits") or {}))
    s.mission = tuple(d["mission"]) if d.get("mission") else None
    s.blank = bool(d.get("blank"))
    s.dirty = True
    s.revision = getattr(s, "revision", 0) + 1
    return {"level": s.level, "mission": d.get("mission"), "restored": file, "changes": len(d.get("undo_titles") or []),
            "note": "the changes are unsaved - save_mission keeps them (the undo history starts here)"}


# ------------------------------------------------------------------------------------------ backups --
def backup_files(s: Session, folder, names, mod, stem):
    """Copy the files of `names` that exist in `folder` to <data>/_editor_cache/backups/<mod>/<stem>-<time>/ (before a
    save overwrites them); the newest 10 per stem are kept. Returns the backup folder or None. A bare name is the mod's
    file of that name at any depth (organised or flat mod, conflict_ds_tools.modlayout); a path (records\\x.lua) as
    given. Backed up under its path in the mod."""
    from conflict_ds_tools import modlayout
    present = []
    for n in names:   # a bare name: every copy (a flat leftover and an organised one can both be there)
        hits = modlayout.find_all(folder, n) if os.sep not in n and "/" not in n else [os.path.join(folder, n)]
        for p in hits:
            rel = os.path.relpath(p, folder)
            if os.path.isfile(p) and rel not in present:
                present.append(rel)
    if not present:
        return None
    root = _cache_dir(s, "backups", mod)
    dest = os.path.join(root, f"{stem}-{time.strftime('%Y%m%d-%H%M%S')}")
    os.makedirs(dest, exist_ok=True)
    for n in present:
        os.makedirs(os.path.dirname(os.path.join(dest, n)), exist_ok=True)    # names may be in sub-folders
        shutil.copy2(os.path.join(folder, n), os.path.join(dest, n))
    old = sorted((d for d in glob.glob(os.path.join(root, stem + "-*")) if os.path.isdir(d)), key=os.path.getmtime)
    for d in old[:-_BACKUPS_KEEP]:
        shutil.rmtree(d, ignore_errors=True)
    return dest


def mission_files(folder, up):
    """A saved mission's own files in its mod folder (what save_mission may overwrite): relative paths, sub-folders
    included (organised mods)."""
    from conflict_ds_tools import modlayout
    if not os.path.isdir(folder):
        return []
    stems = {up.upper()}
    out = []
    for rel in modlayout.files(folder):
        n = os.path.basename(rel)
        if n.split(".")[0].upper() in stems or n.upper().startswith(up.upper() + ".") or rel.lower() == "campaign.txt":
            out.append(rel)
    return out


# ------------------------------------------------------------------------------- the editor window link --
# The browser page polls ui_poll; screenshot / focus queue a request and (screenshot) wait for its ui_reply. These
# run outside the server's command lock (UNLOCKED), so the page can answer while a screenshot waits.
_UI_LOCK = threading.Lock()


def _ui(s: Session):
    with _UI_LOCK:
        if not hasattr(s, "ui_queue"):
            s.ui_queue, s.ui_replies, s.ui_seen, s.ui_next = [], {}, 0.0, 1
            s.ui_event = threading.Condition()
    return s


def _ui_connected(s: Session):
    return time.time() - getattr(s, "ui_seen", 0.0) < 5


def _ui_request(s: Session, kind, args, wait):
    _ui(s)
    if not _ui_connected(s):
        raise CommandError("no editor window is open - start the editor (python editor/server.py) and keep its page "
                           "open in a browser; screenshot and focus are drawn there")
    with s.ui_event:
        rid = s.ui_next
        s.ui_next += 1
        s.ui_queue.append({"id": rid, "type": kind, "args": args})
    if not wait:
        return None
    deadline = time.time() + wait
    with s.ui_event:
        while rid not in s.ui_replies:
            left = deadline - time.time()
            if left <= 0:
                s.ui_queue = [q for q in s.ui_queue if q["id"] != rid]
                raise CommandError("the editor window didn't answer in time (is its tab asleep? bring it to the front)")
            s.ui_event.wait(left)
        reply = s.ui_replies.pop(rid)
    if reply.get("error"):
        raise CommandError(reply["error"])
    return reply.get("result")


@command("(the editor page) Requests waiting for the browser.")
def ui_poll(s: Session):
    _ui(s)
    s.ui_seen = time.time()
    with s.ui_event:
        out, s.ui_queue = s.ui_queue, []
    return out


@command("(the editor page) The answer to a request.", id="request id", result="its result", error="or an error")
def ui_reply(s: Session, id, result=None, error=None):  # noqa: A002
    _ui(s)
    with s.ui_event:
        s.ui_replies[int(id)] = {"result": result, "error": error}
        s.ui_event.notify_all()
    return {"ok": True}


@command("A picture of the editor's 3D view (needs the editor page open in a browser), returned as an image. Aim it "
         "at a record (line), a route marker (route), a point (target [x, y, z]) or from a camera position (position, "
         "looking at target); yaw = compass direction the camera looks (degrees, game convention: 0 = +z), pitch = "
         "degrees down, distance = cm from the target (default: fits the record); top = straight down. Without any of "
         "them: the view as the user sees it. The camera goes back afterwards unless keep_camera.",
         line="optional record line", route="optional route marker index", target="optional [x, y, z]",
         position="optional camera [x, y, z]", yaw="degrees (default 45)", pitch="degrees down (default 35)",
         distance="cm from the target", top="true = straight down", width="pixels (64-2048, default 1024)",
         format="jpeg (default) or png", keep_camera="true = leave the view there")
def screenshot(s: Session, line=None, route=None, target=None, position=None, yaw=None, pitch=None, distance=None,
               top=False, width=1024, format="jpeg", keep_camera=False):   # noqa: A002
    args = {"line": int(line) if line is not None else None, "route": int(route) if route is not None else None,
            "target": list(_vec(target)) if target is not None else None,
            "position": list(_vec(position)) if position is not None else None,
            "yaw": yaw, "pitch": pitch, "distance": distance, "top": bool(top),
            "width": max(64, min(int(width), 2048)), "format": "png" if str(format).lower() == "png" else "jpeg",
            "keep_camera": bool(keep_camera)}
    res = _ui_request(s, "screenshot", args, wait=30) or {}
    if not res.get("image"):
        raise CommandError("the editor window sent no picture")
    return {"image": {"data": res["image"], "mimeType": res.get("mime", "image/jpeg")},
            "camera": res.get("camera"), "size": res.get("size")}


@command("Show the user a record or route marker in the editor window: selects it and frames the view on it (like "
         "opening a form in the GECK).", line="record line", route="or a route marker index")
def focus(s: Session, line=None, route=None):
    if line is None and route is None:
        raise CommandError("give line or route")
    if line is not None:
        s.record(line)
    _ui_request(s, "focus", {"line": int(line) if line is not None else None,
                             "route": int(route) if route is not None else None}, wait=0)
    return {"focused": {"line": line} if line is not None else {"route": route}}
