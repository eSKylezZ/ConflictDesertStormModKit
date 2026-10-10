r"""MCP server for the Desert Storm Editor: every editor command as a tool for AI assistants.

The tools are the editor's own command layer (commands.py) - the same commands the browser UI uses, with the same
undo history. By default this server forwards each call to the running editor (server.py), so what an assistant does
appears in the editor window and can be undone there; the browser redraws when the scene changes.

  python editor/mcp_server.py [--url http://127.0.0.1:8765]
  python editor/mcp_server.py --standalone --data <extracted game folder> [--game <game folder>]

Register it with an MCP client over stdio, e.g. Claude Code:
  claude mcp add desert-storm-editor -- python C:\path\to\ConflictDesertStormModKit\editor\mcp_server.py

Protocol: MCP over stdio (JSON-RPC 2.0, one message per line) - initialize, tools/list, tools/call, ping.
No third-party packages.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

PROTOCOL = "2025-06-18"
INSTRUCTIONS = """Desert Storm Editor: edits Conflict: Desert Storm levels and missions (the same commands as the editor's
UI, with undo). Typical flow: list_levels / list_missions -> open_level or open_mission -> scene (everything in it)
or find (records by kind and field values) -> get_record for details -> set_field / move / place (library templates)
/ duplicate / delete / route_* / set_layer -> scene_problems -> save_mission (a playable mission in Mods) ->
get_script / save_script (the mission's Lua) -> play (starts the game in it) -> game_console (log, crash report).
Mission logic without Lua: mission_logic (objectives, rules, names, the generated Lua) -> set_objectives / add_rule /
set_rules / delete_rule / set_patrol; soldiers' behaviour: ai_profiles -> set_ai_profile; scene look: get_atmosphere /
set_atmosphere; cameras: set_camera. These are compiled into the mission script when it is saved.
Production: briefing_get / briefing_set (dossier + map pins; briefing_burn_map draws them into the tactical map),
music_options / music_set (looping ambient / combat .wav tracks), multiplayer_info / multiplayer_setup (co-op starts,
versus areas), scene_budget (triangles / objects / texture memory vs the largest shipped level), campaign_list /
campaign_save / campaign_reorder (campaign.txt), export_mod_zip (checked, clean release .zip with README).
Level dressing: walkability_grid (the AI walk map), minimap_info / save_minimap (the in-game map picture), save_prefab /
list_prefabs / paste_prefab (stamp saved groups), vehicle_crew / assign_crew (who sits in which seat), sound_info /
voice_banks (sounds to audition).
Tactics: soldier_detection_profiles (enemy sight ranges / facing for stealth paths), minefield_paint / minefield_delete
(anti-tank mines), emplacement_presets / place_emplacement (sandbag nest + tripod gun + gunner), set_air_support
(laser-designated strikes compiled into the script), tactics_info. History: history (timeline of named changes) /
history_jump (straight to any step).
Working safely: status (what is open / unsaved / running; answers while busy) -> search (text, kind, near a point,
paged) -> references (what uses a record: crews, doors, layers, rules, objectives, the base DLL, the Lua - read it
before deleting) -> preview (any command, rolled back: shows exactly what it would change) -> batch (several commands
as ONE undo step, all or nothing; "$0.line" passes step 0's result on) or set_fields (several fields of one record) ->
check_mission (one issue list: errors / warnings with the fixing command) -> mission_report (changes against the base
level; revert_record undoes one record's changes) -> save_mission (backs up the files it overwrites).
Seeing: screenshot (the editor's 3D view as an image - aim it at a line, route marker, point or camera position; needs
the editor page open) and focus (selects and frames a record for the user). Lost work: recovery_list /
recovery_restore (copies written every 5 minutes of unsaved edits).
Positions are game units: centimetres, y up. Records are addressed by their line in the scene file - lines shift after
inserts and deletes, so re-read them (search / find) after changing the scene. Every scene change can be undone (undo);
an edit that changes nothing is reported as unchanged and leaves no undo step."""


def editor_token(url, timeout=10):
    """The running editor's per-launch API token: server.py puts it into index.html (<meta name="editor-token">)."""
    with urllib.request.urlopen(url.rstrip("/") + "/", timeout=timeout) as r:
        page = r.read().decode("utf-8", "replace")
    m = re.search(r'<meta name="editor-token" content="([^"]+)"', page)
    if not m:
        raise RuntimeError(f"no editor token at {url} - an older editor? restart it")
    return m.group(1)


class Editor:
    """Forwards commands to the running editor server."""

    def __init__(self, url):
        self.url = url.rstrip("/")
        self.token = None   # read on first use; again if the editor was restarted (new token)

    def call(self, name, args, retry=True):
        try:
            if self.token is None:
                self.token = editor_token(self.url)
            req = urllib.request.Request(f"{self.url}/api/{name}", data=json.dumps(args or {}).encode(),
                                         headers={"Content-Type": "application/json", "X-Editor-Token": self.token},
                                         method="POST")
            with urllib.request.urlopen(req, timeout=600) as r:
                reply = json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 403 and retry:   # editor restarted since the token was read
                self.token = None
                return self.call(name, args, retry=False)
            try:
                reply = json.loads(e.read())
            except ValueError:
                raise RuntimeError(f"editor answered HTTP {e.code}") from None
        except urllib.error.URLError as e:
            raise RuntimeError(f"the editor isn't running at {self.url} ({e.reason}) - start it with "
                               f"python editor/server.py --data <extracted game folder>") from None
        if not reply.get("ok"):
            raise ValueError(reply.get("error", "failed"))
        return reply.get("result")


class Standalone:
    """Its own session (no editor window)."""

    def __init__(self, data, game):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import commands
        self.commands = commands
        self.session = commands.Session(data, game)

    def call(self, name, args):
        try:
            return self.session.call(name, args or {})
        except self.commands.CommandError as e:
            raise ValueError(str(e)) from None


# Older editors' help has no writes_files flag: the commands that write files / start things
_WRITES_FALLBACK = {"save_mod", "save_mission", "save_script", "play", "create_weapon", "create_skin", "upload_file",
                    "open_level", "open_mission", "undo", "redo", "copy", "new_level", "kit_preview", "record_set",
                    "record_new", "record_revert", "record_delete", "string_set", "string_revert", "save_minimap",
                    "save_prefab", "delete_prefab", "campaign_save", "campaign_reorder", "briefing_burn_map",
                    "export_mod_zip"}


def _read_only(name, info):
    return not info["changes_level"] and not info.get("writes_files", name in _WRITES_FALLBACK)


def tool_list(backend, read_only=False):
    tools = []
    for name, info in sorted(backend.call("help", {}).items()):
        if info.get("internal"):
            continue
        ro = _read_only(name, info)
        if read_only and not ro:
            continue
        types = info.get("types") or {}
        props = {arg: {**types.get(arg, {}), "description": desc} for arg, desc in info["args"].items()}
        tools.append({
            "name": name,
            "description": info["description"] + (" (changes the scene - undo reverts it)" if info["changes_level"] else ""),
            "inputSchema": {"type": "object", "properties": props, "required": info.get("required", [])},
            # scene changes are undoable (not destructive); file writes aren't
            "annotations": {"readOnlyHint": ro, "destructiveHint": not ro and not info["changes_level"],
                            "openWorldHint": False},
        })
    return tools


def call_result(value):
    """A command's result as MCP content: JSON text (+ structuredContent for objects); an {"image": {data, mimeType}}
    in it becomes an image block."""
    content = []
    if isinstance(value, dict) and isinstance(value.get("image"), dict) and value["image"].get("data"):
        img = value["image"]
        content.append({"type": "image", "data": img["data"], "mimeType": img.get("mimeType", "image/jpeg")})
        value = {k: v for k, v in value.items() if k != "image"}
    content.insert(0, {"type": "text", "text": json.dumps(value, indent=1, default=str)})
    out = {"content": content, "isError": False}
    if isinstance(value, dict):
        out["structuredContent"] = json.loads(json.dumps(value, default=str))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8765", help="the running editor (server.py)")
    ap.add_argument("--standalone", action="store_true", help="own session instead of the running editor")
    ap.add_argument("--data", help="extracted game folder (standalone)")
    ap.add_argument("--game", help="game folder (standalone)")
    ap.add_argument("--read-only", action="store_true",
                    help="offer only tools that change nothing (no scene edits, no saving, no starting the game)")
    a = ap.parse_args()
    backend = Standalone(a.data, a.game) if a.standalone else Editor(a.url)

    # JSON-RPC is UTF-8; Windows consoles / pipes default to the ANSI or OEM code page
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    out = sys.stdout
    sys.stdout = sys.stderr            # stray prints must not corrupt the protocol stream

    def send(msg):
        out.write(json.dumps(msg) + "\n")
        out.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            continue
        mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
        if mid is None:                # notifications (initialized, cancelled ...) need no answer
            continue
        try:
            if method == "initialize":
                result = {"protocolVersion": params.get("protocolVersion", PROTOCOL),
                          "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": {"name": "desert-storm-editor", "version": "1.0"},
                          "instructions": INSTRUCTIONS}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": tool_list(backend, a.read_only)}
            elif method == "tools/call":
                try:
                    name = params.get("name")
                    if a.read_only and name not in {t["name"] for t in tool_list(backend, True)}:
                        raise ValueError(f"{name} changes things - this MCP server was started with --read-only")
                    result = call_result(backend.call(name, params.get("arguments") or {}))
                except (ValueError, RuntimeError) as e:
                    result = {"content": [{"type": "text", "text": str(e)}], "isError": True}
            else:
                send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {method}"}})
                continue
            send({"jsonrpc": "2.0", "id": mid, "result": result})
        except Exception as e:  # noqa: BLE001 - reported to the client
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": f"{type(e).__name__}: {e}"}})


if __name__ == "__main__":
    main()
