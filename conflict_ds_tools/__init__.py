"""Conflict: Desert Storm tools - Blender add-on and library.

Modules: evo (meshes, skeletons, animations, vehicle data), archive (.dat extraction), images (DDS/TGA/PNG).
The Blender UI lives in blender.py and is only loaded inside Blender, so the command-line tools
(evo_convert.py, ds_extract.py) can import this package with plain Python.
"""
bl_info = {
    "name": "Conflict: Desert Storm tools",
    "author": "eSKylezZ",
    "version": (1, 1, 0),
    "blender": (3, 6, 0),
    "location": "File > Import > Conflict: Desert Storm",
    "description": "Import .EVO meshes (props, vehicles, characters) and .prb animations, extract the game archives",
    "category": "Import-Export",
}

try:
    import bpy  # noqa: F401
except ImportError:
    bpy = None

if bpy is not None:
    from .blender import register, unregister  # noqa: F401
