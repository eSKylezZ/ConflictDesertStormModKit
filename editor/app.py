"""The editor as a desktop app: a native window (pywebview, Edge WebView2) with a game picker, first-run extraction
of the picked game's archives, then the editor itself (server.py in a background thread) in the same window.

  python editor/app.py [--data ds1=<extracted folder> ...] [--game ds1=<game folder> ...]

--data reuses an existing extraction (ds_extract.py output) for a game instead of the app's own cache
(%LOCALAPPDATA%\\ConflictEditor\\<game>\\extracted). Packaged into one exe by scripts/build_exe.py.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import webview  # noqa: E402

from conflict_ds_tools import archive, games  # noqa: E402

CACHE = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "ConflictEditor")
DONE = ".extracted"          # written into a cache folder once its extraction finished


def cache_dir(gid):
    return os.path.join(CACHE, gid, "extracted")


class Api:
    """Called from the picker page (window.pywebview.api.*)."""

    def __init__(self, data_overrides, game_overrides=None):
        self._window = None
        self._data = data_overrides         # game id -> extracted folder given with --data
        self._games = game_overrides or {}  # game id -> game folder given with --game
        self._found = {}                    # game id -> game folder
        self._busy = False
        self._server = None

    def home(self):
        """Back to the game picker (File > Switch game): the open editor's server stops."""
        srv, self._server = self._server, None
        if srv:
            threading.Thread(target=srv.shutdown, daemon=True).start()
        def back():               # after this call has returned to the page it replaces
            import time
            time.sleep(0.2)
            self._window.set_title("Conflict Editor")
            self._window.load_html(PICKER)
        threading.Thread(target=back, daemon=True).start()

    def _data_dir(self, gid):
        return self._data.get(gid) or cache_dir(gid)

    def _ready(self, gid):
        d = self._data_dir(gid)
        return os.path.isdir(d) and (gid in self._data or os.path.exists(os.path.join(d, DONE)))

    def installs(self):
        self._found = {**games.find_installs(), **self._games}
        out = []
        for g in games.GAMES.values():
            out.append({"id": g.id, "title": g.title, "folder": self._found.get(g.id), "ready": self._ready(g.id)})
        return out

    def browse(self):
        res = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        if not res:
            return None
        folder = res[0] if isinstance(res, (list, tuple)) else res
        g = games.detect(folder)
        if not g:
            return {"error": f"No supported game in {folder} (looked for " +
                    ", ".join(e for x in games.GAMES.values() for e in x.exes[:1]) + ")"}
        self._found[g.id] = folder
        return {"id": g.id, "folder": folder}

    def open(self, gid):
        if self._busy:
            return False
        folder = self._found.get(gid)
        if not folder:
            return False
        self._busy = True
        threading.Thread(target=self._open, args=(gid, folder), daemon=True).start()
        return True

    def _js(self, fn, *args):
        self._window.evaluate_js(f"{fn}({', '.join(json.dumps(a) for a in args)})")

    def _open(self, gid, folder):
        try:
            data = self._data_dir(gid)
            if not self._ready(gid):
                os.makedirs(data, exist_ok=True)

                def progress(frac, text):
                    self._js("progress", frac, text)
                self._js("progress", 0.0, "Extracting the game's archives (first time only)")
                archive.extract(folder, data, None, False, progress, log=lambda *a: None)
                open(os.path.join(data, DONE), "w").close()
            self._js("progress", 1.0, "Starting the editor")
            import server
            # always the game's own port (8765 Desert Storm, 8780 Vietnam, 8790 Global Storm, 8800 The Great Escape): the
            # window's address stays the same between runs, so what it remembers (panels, layers, snap, keys) survives a
            # restart, and the MCP connection finds it. Taken = this game's editor is already open.
            try:
                httpd, url, g, _game = server.start(data, folder, mod_layout=getattr(self, "_mod_layout", None))
            except OSError:
                raise RuntimeError("the Conflict Editor for this game is already open (in another window) - "
                                   "close it first, or switch to it") from None
            self._server = httpd
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            self._window.set_title(f"{g.short} Editor")
            self._window.load_url(url)
        except Exception as e:  # noqa: BLE001 - shown in the window
            traceback.print_exc()
            self._js("failed", f"{type(e).__name__}: {e}")
        finally:
            self._busy = False


class Tester:
    """--test SCRIPT: run(app) drives the app without the mouse - app.open(game) / app.home() / app.title /
    app.js(code) / app.wait(cond) / app.shot(png) (PrintWindow: works off-screen) / app.log(...); the app quits after."""

    def __init__(self, api):
        self.api = api

    @property
    def title(self):
        return self.api._window.title

    def open(self, gid, timeout=600):
        self.api.installs()
        if not self.api.open(gid):
            raise RuntimeError(f"can't open {gid}")
        self.wait(lambda: self.title.endswith(" Editor") and self.title != "Conflict Editor", timeout)

    def home(self):
        self.api.home()
        self.wait(lambda: self.title == "Conflict Editor")

    def js(self, code):
        return self.api._window.evaluate_js(code)

    def wait(self, cond, timeout=120):
        import time
        end = time.time() + timeout
        while time.time() < end:
            try:
                if cond():
                    return True
            except Exception:  # noqa: BLE001 - page not ready yet
                pass
            time.sleep(0.5)
        raise TimeoutError("waited too long")

    def shot(self, path):
        import ctypes
        from ctypes import wintypes
        u, g = ctypes.windll.user32, ctypes.windll.gdi32
        h = int(str(self.api._window.native.Handle.ToInt64()))
        r = wintypes.RECT()
        u.GetWindowRect(h, ctypes.byref(r))
        w, ht = r.right - r.left, r.bottom - r.top
        dc = u.GetWindowDC(h)
        mdc = g.CreateCompatibleDC(dc)
        bmp = g.CreateCompatibleBitmap(dc, w, ht)
        g.SelectObject(mdc, bmp)
        u.PrintWindow(h, mdc, 2)                         # PW_RENDERFULLCONTENT: the WebView2 contents too
        from PIL import Image
        buf = ctypes.create_string_buffer(w * ht * 4)
        bmi = (ctypes.c_uint32 * 10)(40, w, -ht, 1 | (32 << 16), 0, 0, 0, 0, 0, 0)
        g.GetDIBits(mdc, bmp, 0, ht, buf, bmi, 0)
        Image.frombuffer("RGBA", (w, ht), buf, "raw", "BGRA", 0, 1).convert("RGB").save(path)
        g.DeleteObject(bmp); g.DeleteDC(mdc); u.ReleaseDC(h, dc)

    def log(self, *a):
        print("[test]", *a, flush=True)

    def run(self, fn):
        try:
            fn(self)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        finally:
            self.api._window.destroy()


PICKER = r"""<!doctype html><html><head><meta charset="utf-8"><title>Conflict Editor</title><style>
:root { color-scheme: dark; }
body { margin: 0; background: #1b1d1a; color: #e6e2d6; font: 14px/1.45 "Segoe UI", sans-serif; }
main { max-width: 720px; margin: 48px auto; padding: 0 24px; }
h1 { font-size: 22px; margin: 0 0 4px; color: #f2c14e; } p.sub { margin: 0 0 24px; color: #a9a596; }
.game { display: flex; align-items: center; gap: 16px; padding: 14px 16px; margin-bottom: 10px; border: 1px solid #34372f;
        border-radius: 8px; background: #23261f; }
.game .t { flex: 1; } .game .t b { display: block; font-size: 15px; } .game .t small { color: #8f8b7e; word-break: break-all; }
button { font: inherit; padding: 7px 16px; border-radius: 6px; border: 1px solid #5a5c4f; background: #2e3128; color: #eee; cursor: pointer; }
button.go { background: #f2c14e; color: #1b1d1a; border-color: #f2c14e; font-weight: 600; }
button:disabled { opacity: .45; cursor: default; }
#bar { display: none; margin-top: 24px; } #bar div.track { height: 8px; background: #34372f; border-radius: 4px; overflow: hidden; }
#bar div.fill { height: 100%; width: 0; background: #f2c14e; transition: width .3s; } #msg { margin-top: 8px; color: #a9a596; }
.err { color: #ff8a7a; margin-top: 16px; white-space: pre-wrap; }
</style></head><body><main>
<h1>Conflict Editor</h1><p class="sub">Pick a game. The first time, its archives are extracted to your user folder
(a few minutes, about 1.5 GB) - nothing is changed in the game folder.</p>
<div id="list">Looking for installed games…</div>
<button id="browse">Browse for a game folder…</button>
<div id="bar"><div class="track"><div class="fill" id="fill"></div></div><div id="msg"></div></div>
<div class="err" id="err"></div>
<script>
const $ = (id) => document.getElementById(id);
function progress(f, text) { $('bar').style.display = 'block'; $('fill').style.width = (f * 100).toFixed(1) + '%'; $('msg').textContent = text; }
function failed(e) { $('err').textContent = e; for (const b of document.querySelectorAll('button')) b.disabled = false; }
async function draw() {
  const games = await pywebview.api.installs();
  $('list').innerHTML = '';
  for (const g of games) {
    const row = document.createElement('div'); row.className = 'game';
    const t = document.createElement('div'); t.className = 't';
    t.innerHTML = `<b></b><small></small>`; t.querySelector('b').textContent = g.title;
    t.querySelector('small').textContent = g.folder ? g.folder + (g.ready ? '' : ' - not extracted yet') : 'not found - use Browse';
    const b = document.createElement('button'); b.className = 'go';
    b.textContent = g.ready ? 'Open' : 'Extract & open'; b.disabled = !g.folder;
    b.onclick = async () => { for (const x of document.querySelectorAll('button')) x.disabled = true; $('err').textContent = '';
                              progress(0, 'Starting…'); await pywebview.api.open(g.id); };
    row.append(t, b); $('list').append(row);
  }
}
$('browse').onclick = async () => {
  const r = await pywebview.api.browse();
  if (r && r.error) $('err').textContent = r.error; else if (r) draw();
};
window.addEventListener('pywebviewready', draw);
</script></main></body></html>"""


def _allow_panel_windows():
    """Panels popped out of the editor (web/panels.js) are real windows: window.open('about:blank', ...) from the page.
    pywebview sends every new-window request to the system browser or the main view - for those blank windows WebView2
    is left to open its own popup window instead (same page, so the panel moves into it live). Other links unchanged."""
    try:
        from webview.platforms import edgechromium
    except ImportError:            # not on Windows / no WebView2 backend
        return
    cls = edgechromium.EdgeChrome
    if getattr(cls, "_panel_windows", False):
        return
    original = cls.on_new_window_request

    def on_new_window_request(self, sender, args):
        try:
            blank = str(args.get_Uri()) == "about:blank"
        except Exception:  # noqa: BLE001
            blank = False
        if not blank:
            return original(self, sender, args)
        try:
            _panel_window(self, sender, args, edgechromium)
        except Exception:  # noqa: BLE001 - unhandled, WebView2 opens its own (plainer) popup instead
            traceback.print_exc()
    cls.on_new_window_request = on_new_window_request
    cls._panel_windows = True


def _panel_window(chrome, sender, args, ec):
    """A plain tool window for a popped-out panel: a WinForms form with a WebView2 in the editor's environment (no
    address bar), the editor's icon, positioned / sized as window.open asked, titled from the page, closed by
    window.close(). The page then moves the live panel into it."""
    from System.Drawing import Point, Size
    WinForms, WebView2 = ec.WinForms, ec.WebView2
    feats = args.WindowFeatures

    def signed(v):                     # WindowFeatures are UInt32: a window left of / above the main screen wraps round
        v = int(v)
        return v - (1 << 32) if v >= (1 << 31) else v
    form = WinForms.Form()
    form.Text = "Conflict Editor"
    form.StartPosition = WinForms.FormStartPosition.Manual
    form.Location = Point(signed(feats.Left) if feats.HasPosition else 200, signed(feats.Top) if feats.HasPosition else 200)
    form.ClientSize = Size(max(200, signed(feats.Width)) if feats.HasSize else 320,
                           max(120, signed(feats.Height)) if feats.HasSize else 480)
    owner = getattr(chrome, "form", None)
    if owner is not None:
        try:
            form.Icon = owner.Icon
        except Exception:  # noqa: BLE001
            pass
    wv = WebView2()
    wv.Dock = WinForms.DockStyle.Fill
    form.Controls.Add(wv)

    def ready(_s, e):
        try:
            if e.IsSuccess:
                core = wv.CoreWebView2

                def title(_s2, _e2):
                    form.Text = core.DocumentTitle or "Conflict Editor"

                def close(_s2, _e2):
                    form.Close()
                core.DocumentTitleChanged += title
                core.WindowCloseRequested += close
                args.NewWindow = core
            args.Handled = True
        finally:
            deferral.Complete()
    # the deferral last: from here window.open waits for this window, so nothing may fail before ready() completes it
    deferral = args.GetDeferral()
    wv.CoreWebView2InitializationCompleted += ready
    try:
        form.Show()
        wv.EnsureCoreWebView2Async(sender.Environment)
    except Exception:
        deferral.Complete()            # unhandled: WebView2's own popup instead of a hung window.open
        form.Close()
        raise


def main():
    if getattr(sys, "frozen", False):          # the exe has no console: messages and errors go to a log file
        os.makedirs(CACHE, exist_ok=True)
        sys.stdout = sys.stderr = open(os.path.join(CACHE, "editor.log"), "w", encoding="utf-8", buffering=1)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", action="append", default=[], metavar="GAME=FOLDER",
                    help="use an existing extraction for a game (ds1, ds2, cv, cg)")
    ap.add_argument("--game", action="append", default=[], metavar="GAME=FOLDER",
                    help="use this install of a game (when there are several, e.g. Steam and GOG)")
    ap.add_argument("--debug", action="store_true", help="WebView2 developer tools")
    ap.add_argument("--background", action="store_true", help="off-screen window that never takes the focus (tests)")
    ap.add_argument("--test", metavar="SCRIPT", help="a Python file with run(app): drives the app (tests; see Tester)")
    ap.add_argument("--mod-layout", choices=("organised", "flat"), default="organised",
                    help="new mods: files in folders by type (organised, default) or all in the mod's top folder (flat)")
    a = ap.parse_args()
    over = {}
    for d in a.data:
        gid, _, path = d.partition("=")
        if gid.lower() not in games.GAMES or not os.path.isdir(path):
            ap.error(f"--data {d}: expected GAME=FOLDER with GAME one of {', '.join(games.GAMES)}")
        over[gid.lower()] = os.path.abspath(path)
    games_over = {}
    for d in a.game:
        gid, _, path = d.partition("=")
        if gid.lower() not in games.GAMES or not os.path.isdir(path):
            ap.error(f"--game {d}: expected GAME=FOLDER with GAME one of {', '.join(games.GAMES)}")
        games_over[gid.lower()] = os.path.abspath(path)
    webview.settings["ALLOW_DOWNLOADS"] = True     # Export Mod Package's zip (/download/...)
    _allow_panel_windows()
    api = Api(over, games_over)
    api._mod_layout = a.mod_layout
    where = dict(x=-4000, y=0, focus=False) if a.background else {}
    api._window = webview.create_window("Conflict Editor", html=PICKER, js_api=api, width=1500, height=920,
                                        min_size=(1000, 650), **where)
    func = None
    if a.test:
        ns = {"__file__": os.path.abspath(a.test), "__name__": "editor_test"}
        exec(compile(open(a.test, encoding="utf-8").read(), a.test, "exec"), ns)
        func = lambda: Tester(api).run(ns["run"])
    icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")   # the exe carries its own
    webview.start(func, gui="edgechromium", debug=a.debug, private_mode=False,
                  storage_path=os.path.join(CACHE, "webview"),
                  icon=icon if not getattr(sys, "frozen", False) and os.path.isfile(icon) else None)


if __name__ == "__main__":
    main()
