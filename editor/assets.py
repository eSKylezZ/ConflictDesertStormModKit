"""Models for the viewport: an .EVO from the extracted game folder -> .glb (cli/evo_convert.py's writer: textures
embedded, metres, right-handed - x, y, -z of the game's centimetres), cached on disk per level."""
from __future__ import annotations

import importlib.util
import os
import re
import threading
import types

from conflict_ds_tools import evo

_here = os.path.dirname(os.path.abspath(__file__))
_cli = os.path.join(os.path.dirname(_here), "cli", "evo_convert.py")
if os.path.exists(_cli):
    _spec = importlib.util.spec_from_file_location("evo_convert", _cli)
    evo_convert = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(evo_convert)
else:                                      # the packaged editor (editor/app.py): bundled as a module
    import evo_convert

OPTS = types.SimpleNamespace(skin="", no_normal_maps=True, no_parts=False, helpers=False, scale=0.01, no_embed=False,
                             rfx=None, textures=[], skip_invisible=True)   # Vietnam blockers (evo: Mesh.invisible)


class ModelCache:
    def __init__(self, data_dir, cache_dir):
        self.data_dir, self.cache_dir = data_dir, cache_dir
        self.finders = {}       # level folder -> AssetFinder (lists every extracted folder once)
        self.locks = {}
        self.lock = threading.Lock()

    def _finder(self, folder):
        with self.lock:
            if folder not in self.finders:
                any_file = next((os.path.join(folder, f) for f in os.listdir(folder)), folder)
                self.finders[folder] = evo.AssetFinder(any_file)
            return self.finders[folder]

    def glb(self, folder, model, skin=""):
        """Path of the cached .glb of `model` (level folder's archive first), converting it when needed; None if the
        model isn't in the extracted files."""
        name = os.path.basename(model).upper()
        if name.endswith((".GLB", ".EVO")):
            name = name[:-4]
        out_dir = os.path.join(self.cache_dir, os.path.basename(folder).upper())
        skin = (skin or "").upper()
        out = os.path.join(out_dir, name + ("@" + skin if skin else "") + ".glb")
        with self.lock:
            lock = self.locks.setdefault(out, threading.Lock())
        with lock:
            if os.path.exists(out):
                return out
            finder = self._finder(folder)
            src = finder.find(name, (".evo",))
            only = None
            if not src:
                # a zone chunk placed on its own: "<MODEL>NNN" = object NNN of <MODEL>.EVO (Vietnam scenes do this)
                m = re.fullmatch(r"(.+?)(\d{3})", name)
                src = finder.find(m.group(1), (".evo",)) if m else None
                if not src:
                    return None
                only = int(m.group(2))
                try:
                    if only >= len(evo.read(src)):
                        return None
                except evo.EvoError:
                    return None
            os.makedirs(out_dir, exist_ok=True)
            opts = types.SimpleNamespace(**vars(OPTS))
            opts.rfx = None
            if skin and finder.find(skin):
                opts.skin = skin          # skinned meshes (soldiers)
                opts.override = skin      # the main texture of static meshes (props, vehicles)
            m = evo_convert.Model(src, finder, opts, only=only)
            tmp = out + ".tmp"
            evo_convert.GltfWriter(m, [], opts, tmp).write(tmp, True)
            os.replace(tmp, out)
            return out
