"""Conflict: Desert Storm world editor - local web server.

  python editor/server.py --data <extracted game folder> [--game <game folder>] [--port 8765] [--no-browser]

Serves the editor UI (editor/web) and a JSON API: POST /api/<command> with the arguments as a JSON object ->
{"ok": true, "result": ...} or {"ok": false, "error": "..."} (the commands in editor/commands.py; GET /api/help lists
them). GET /model/<LEVEL>/<MODEL>.glb converts and serves a model; GET /audio/<LEVEL>/<sample>.wav and
/audio/<LEVEL>/<BANK>/<line>.wav decode a sound of the level's sound cache (studio.py). Only listens on 127.0.0.1.
"""
from __future__ import annotations

import argparse
import importlib
import json
import mimetypes
import os
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from assets import ModelCache  # noqa: E402
import commands  # noqa: E402
from commands import CommandError, Session  # noqa: E402
from conflict_ds_tools.archive import find_game_dir  # noqa: E402

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("model/gltf-binary", ".glb")



class Editor:
    def __init__(self, data_dir, game_dir, cache_dir):
        self.session = Session(data_dir, game_dir)
        self.models = ModelCache(data_dir, cache_dir)
        self.lock = threading.Lock()  # one command at a time
        self.commands_time = os.path.getmtime(commands.__file__)

    def reload_commands(self):
        """commands.py changed on disk: reload it in place (the open session and its undo history stay; the module's
        globals - COMMANDS, the session's command functions - are replaced)."""
        t = os.path.getmtime(commands.__file__)
        if t != self.commands_time:
            self.commands_time = t
            importlib.reload(commands)   # (commands.py reloads conflict_ds_tools.env itself)
            # the open session onto the new class (its methods) with any attributes the new __init__ adds
            fresh = commands.Session(self.session.data_dir, self.session.game_dir)
            for k, v in vars(fresh).items():
                if not hasattr(self.session, k):
                    setattr(self.session, k, v)
            self.session.__class__ = commands.Session
            self.session.undo_stack.clear(), self.session.redo_stack.clear()   # snapshot layout may have changed
            print("commands.py reloaded")


def make_handler(editor: Editor):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quiet: errors only
            pass

        def send(self, code, body, ctype="application/json", cache=False):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "max-age=3600" if cache else "no-store")
            self.end_headers()
            self.wfile.write(data)

        def api(self, name, args):
            try:
                with editor.lock:
                    editor.reload_commands()
                    return {"ok": True, "result": editor.session.call(name, args)}
            except commands.CommandError as e:
                return {"ok": False, "error": str(e)}
            except TypeError as e:
                return {"ok": False, "error": f"bad arguments for {name}: {e}"}
            except Exception as e:  # noqa: BLE001 - reported to the UI
                traceback.print_exc()
                return {"ok": False, "error": f"{type(e).__name__}: {e}"}

        def do_POST(self):
            if not self.path.startswith("/api/"):
                return self.send(404, {"ok": False, "error": "not found"})
            length = int(self.headers.get("Content-Length") or 0)
            try:
                args = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self.send(400, {"ok": False, "error": "body is not JSON"})
            self.send(200, self.api(self.path[5:], args))

        def do_GET(self):
            path = unquote(self.path.split("?")[0])
            if path.startswith("/api/"):
                return self.send(200, self.api(path[5:], {}))
            if path.startswith("/audio/"):
                # /audio/<LEVEL>/<sample>.wav (an ambient sound's file name) or /audio/<LEVEL>/<BANK>/<line>.wav
                parts = [os.path.basename(p.replace("\\", "/")) for p in path.split("/")[2:]]
                if len(parts) not in (2, 3) or not parts[-1].lower().endswith(".wav") or \
                        any(p in ("", ".", "..") or ":" in p for p in parts):
                    return self.send(404, b"not found", "text/plain")
                with editor.lock:
                    editor.reload_commands()
                studio = sys.modules["studio"]
                stem = parts[-1][:-4]
                try:
                    if len(parts) == 3:
                        wav = studio.sample_wav(editor.session, parts[0], bank=parts[1], line=int(stem))
                    else:
                        wav = studio.sample_wav(editor.session, parts[0], name=stem)
                except (commands.CommandError, ValueError) as e:
                    return self.send(404, str(e).encode(), "text/plain")
                except Exception as e:  # noqa: BLE001
                    traceback.print_exc()
                    return self.send(500, str(e).encode(), "text/plain")
                return self.send(200, wav, "audio/wav", cache=True)
            if path.startswith("/download/") or path.startswith("/modfile/"):
                # /download/<release>.zip (production.export_mod_zip) or /modfile/<mod>/<track>.wav (music audition)
                parts = [os.path.basename(p.replace("\\", "/")) for p in path.split("/")[2:]]
                if any(p in ("", ".", "..") or ":" in p for p in parts):
                    return self.send(404, b"not found", "text/plain")
                if path.startswith("/download/") and len(parts) == 1 and parts[0].lower().endswith(".zip"):
                    full, ctype = os.path.join(sys.modules["production"].RELEASE_DIR, parts[0]), "application/zip"
                elif path.startswith("/modfile/") and len(parts) == 2 and parts[1].lower().endswith(".wav") \
                        and editor.session.game_dir:
                    full, ctype = os.path.join(editor.session.game_dir, "Mods", parts[0], parts[1]), "audio/wav"
                else:
                    return self.send(404, b"not found", "text/plain")
                if not os.path.isfile(full):
                    return self.send(404, b"not found", "text/plain")
                with open(full, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                if ctype == "application/zip":
                    self.send_header("Content-Disposition", f'attachment; filename="{parts[0]}"')
                self.end_headers()
                self.wfile.write(body)
                return
            if path.startswith("/model/"):
                parts = path.split("/")
                if len(parts) != 4:
                    return self.send(404, b"", "text/plain")
                # plain names only: no "..", drive letters or separators (backslashes included) in either part
                level, model = (os.path.basename(p.replace("\\", "/")) for p in parts[2:4])
                if level in ("", ".", "..") or model in ("", ".", "..") or ":" in level + model:
                    return self.send(404, b"not found", "text/plain")
                s = editor.session
                folder = s.folder if s.level == level.upper() else os.path.join(s.data_dir, level.lower())
                try:
                    glb = editor.models.glb(folder, model)
                except Exception as e:  # noqa: BLE001
                    traceback.print_exc()
                    return self.send(500, str(e).encode(), "text/plain")
                if not glb:
                    return self.send(404, b"model not found", "text/plain")
                try:
                    with open(glb, "rb") as f:
                        return self.send(200, f.read(), "model/gltf-binary", cache=True)
                except OSError:
                    return self.send(404, b"model not found", "text/plain")
            rel = "index.html" if path == "/" else path.lstrip("/")
            full = os.path.abspath(os.path.join(WEB, rel))
            try:
                inside = os.path.commonpath([WEB, full]) == WEB
            except ValueError:  # another drive
                inside = False
            if not inside or not os.path.isfile(full):
                return self.send(404, b"not found", "text/plain")
            with open(full, "rb") as f:
                self.send(200, f.read(), mimetypes.guess_type(full)[0] or "application/octet-stream")

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True, help="extracted game folder (ds_extract.py output)")
    ap.add_argument("--game", help="game folder (Mods are saved there; default: found in the registry)")
    ap.add_argument("--cache", help="model cache folder (default: <data>/_editor_cache)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    game = a.game or find_game_dir()
    editor = Editor(a.data, game, a.cache or os.path.join(a.data, "_editor_cache"))
    server = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(editor))
    url = f"http://127.0.0.1:{a.port}/"
    print(f"Editor: {url}  (data {a.data}, game {game or '-'})")
    if not a.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
