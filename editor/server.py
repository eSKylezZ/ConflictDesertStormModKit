"""Conflict world editor (Desert Storm, Desert Storm II, Vietnam, Global Storm) - the editor's local backend.

The editor is ConflictEditor.exe (app.py): its window shows the UI this server serves on 127.0.0.1, inside the exe.
There is no browser version. Run on its own only for development, tests and a headless MCP session:

  python editor/server.py --data <extracted game folder> [--game <game folder>] [--port <port>]

The game comes from the game folder's exe, else the extracted folder (its .game marker); --port defaults to that game's
port (Desert Storm / DS2 8765, Vietnam 8780, Global Storm 8790), so editors for two games can run side by side.

Serves the editor UI (editor/web) and a JSON API: POST /api/<command> with the arguments as a JSON object ->
{"ok": true, "result": ...} or {"ok": false, "error": "..."} (the commands in editor/commands.py; GET /api/help lists
them). GET /model/<LEVEL>/<MODEL>.glb converts and serves a model; GET /audio/<LEVEL>/<sample>.wav and
/audio/<LEVEL>/<BANK>/<line>.wav decode a sound of the level's sound cache (studio.py). Only listens on 127.0.0.1.

Cross-site protection: every request must carry Host 127.0.0.1:<port> / localhost:<port> (DNS rebinding) and no
foreign Origin; /api/* calls also need Content-Type application/json (POST) and the per-launch token in the
X-Editor-Token header. The token is put into index.html as <meta name="editor-token">; other local clients (the MCP
server, test scripts) read it from there - see mcp_server.editor_token().
"""
from __future__ import annotations

import argparse
import hmac
import importlib
import json
import mimetypes
import os
import secrets
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from assets import ModelCache  # noqa: E402
import commands  # noqa: E402
from commands import CommandError, Session  # noqa: E402
from conflict_ds_tools import games  # noqa: E402
from conflict_ds_tools.archive import find_game_dir  # noqa: E402

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("model/gltf-binary", ".glb")
TOKEN_HEADER = "X-Editor-Token"



def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0


class Editor:
    def __init__(self, data_dir, game_dir, cache_dir):
        self.session = Session(data_dir, game_dir)
        self.models = ModelCache(data_dir, cache_dir)
        self.lock = threading.Lock()  # one command at a time
        self.token = secrets.token_urlsafe(32)  # per launch: /api/* calls must send it (X-Editor-Token)
        self.commands_time = _mtime(commands.__file__)

    def reload_commands(self):
        """commands.py changed on disk: reload it in place (the open session and its undo history stay; the module's
        globals - COMMANDS, the session's command functions - are replaced)."""
        t = _mtime(commands.__file__)
        if t != self.commands_time and not getattr(sys, "frozen", False):   # (the exe has no commands.py to reload)
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

        def deny(self, code, error):
            self.close_connection = True
            return self.send(code, {"ok": False, "error": error})

        def allowed_hosts(self):
            port = self.server.server_address[1]
            return {f"127.0.0.1:{port}", f"localhost:{port}"}

        def check_origin(self):
            """Host must be this server (DNS rebinding) and any Origin one of its own. Returns False after denying."""
            hosts = self.allowed_hosts()
            if (self.headers.get("Host") or "").lower() not in hosts:
                self.deny(403, "bad Host header")
                return False
            origin = self.headers.get("Origin")
            if origin is not None:
                o = urlsplit(origin)
                if o.scheme != "http" or (o.netloc or "").lower() not in hosts:
                    self.deny(403, "cross-origin request refused")
                    return False
            return True

        def check_api(self, post):
            """/api/* only: JSON body (POST) and the per-launch token. Returns False after denying."""
            if post:
                ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if ctype != "application/json":
                    self.deny(415, "Content-Type must be application/json")
                    return False
            if not hmac.compare_digest((self.headers.get(TOKEN_HEADER) or "").encode(), editor.token.encode()):
                self.deny(403, "missing or wrong editor token")
                return False
            return True

        def do_OPTIONS(self):  # no CORS: preflights fail
            if self.check_origin():
                self.deny(405, "method not allowed")

        def api(self, name, args):
            try:
                # status and the browser link (screenshot waits for the page's ui_reply) don't wait for the lock
                if name in getattr(commands.workflow, "UNLOCKED", ()):
                    return {"ok": True, "result": editor.session.call(name, args)}
                with editor.lock:
                    editor.reload_commands()
                    editor.session.busy = (name, time.time())
                    try:
                        return {"ok": True, "result": editor.session.call(name, args)}
                    finally:
                        editor.session.busy = None
            except commands.CommandError as e:
                return {"ok": False, "error": str(e)}
            except TypeError as e:
                return {"ok": False, "error": f"bad arguments for {name}: {e}"}
            except Exception as e:  # noqa: BLE001 - reported to the UI
                traceback.print_exc()
                return {"ok": False, "error": f"{type(e).__name__}: {e}"}

        def do_POST(self):
            if not self.check_origin():
                return
            if not self.path.startswith("/api/"):
                return self.send(404, {"ok": False, "error": "not found"})
            if not self.check_api(post=True):
                return
            length = int(self.headers.get("Content-Length") or 0)
            try:
                args = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self.send(400, {"ok": False, "error": "body is not JSON"})
            self.send(200, self.api(self.path[5:], args))

        def do_GET(self):
            if not self.check_origin():
                return
            path = unquote(self.path.split("?")[0])
            if path.startswith("/api/"):
                if not self.check_api(post=False):
                    return
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
                    from conflict_ds_tools import modlayout   # the track at any depth (organised mods: sounds\)
                    full = modlayout.find(os.path.join(editor.session.game_dir, "Mods", parts[0]), parts[1]) or ""
                    ctype = "audio/wav"
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
                if level.startswith("@"):        # a library source: a converted library or a mod
                    folder = commands.source_folder(s, level, model[:-4] if model.lower().endswith(".glb") else model)
                    if not folder:
                        return self.send(404, b"not found", "text/plain")
                else:
                    folder = s.folder if s.level == level.upper() else commands.level_folder(s, level)
                skin = ""
                for kv in urlsplit(self.path).query.split("&"):
                    if kv.startswith("skin="):
                        skin = os.path.basename(unquote(kv[5:]).replace("\\", "/"))
                if ":" in skin or skin in (".", ".."):
                    skin = ""
                try:
                    glb = editor.models.glb(folder, model, skin)
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
                body = f.read()
            if rel == "index.html":   # the page gets this launch's API token
                body = body.replace(b"<head>", b'<head>\n<meta name="editor-token" content="%s">'
                                    % editor.token.encode(), 1)
            self.send(200, body, mimetypes.guess_type(full)[0] or "application/octet-stream")

    return Handler


def start(data, game=None, port=None, cache=None, mod_layout=None):
    """The editor server for one game (not yet serving - call serve_forever). port None = the game's default, 0 = any
    free port; mod_layout = the layout new mods get (conflict_ds_tools.modlayout: organised (default) / flat). Returns
    (server, url, Game, game folder)."""
    g = (games.detect(game) if game else None) or games.detect_extracted(data) or games.current()
    games.set_current(g)
    game = game or find_game_dir(g)
    editor = Editor(data, game, cache or os.path.join(data, "_editor_cache"))
    if mod_layout:
        editor.session.mod_layout = mod_layout
    ThreadingHTTPServer.allow_reuse_address = False   # Windows: otherwise a second editor binds the same port silently
    server = ThreadingHTTPServer(("127.0.0.1", g.port if port is None else port), make_handler(editor))
    return server, f"http://127.0.0.1:{server.server_address[1]}/", g, game


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True, help="extracted game folder (ds_extract.py output)")
    ap.add_argument("--game", help="game folder (Mods are saved there; default: found in the registry)")
    ap.add_argument("--cache", help="model cache folder (default: <data>/_editor_cache)")
    ap.add_argument("--port", type=int, help="default: the game's (8765 Desert Storm / DS2, 8780 Vietnam, 8790 Global Storm)")
    ap.add_argument("--no-browser", action="store_true", help=argparse.SUPPRESS)   # old scripts: no browser either way
    ap.add_argument("--mod-layout", choices=("organised", "flat"), default="organised",
                    help="new mods: files in folders by type (organised, default) or all in the mod's top folder (flat); "
                         "mods that already have files keep their own layout")
    a = ap.parse_args()
    server, url, g, game = start(a.data, a.game, a.port, a.cache, a.mod_layout)
    print(f"{g.short} Editor backend: {url}  (data {a.data}, game {game or '-'}) - the editor itself is ConflictEditor.exe")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
