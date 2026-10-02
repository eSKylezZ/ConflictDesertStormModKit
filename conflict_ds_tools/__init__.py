"""Conflict: Desert Storm tools - Blender add-on and library.

Modules: evo (meshes, skeletons, animations, vehicle data), archive (.dat extraction), images (DDS/TGA/PNG),
sound (level sound caches, banks, ADPCM samples <-> WAV).
The Blender UI lives in blender.py and is only loaded inside Blender, so the command-line tools
(ds_extract.py, evo_convert.py, ds_texture.py, ds_sound.py) can import this package with plain Python.
"""
bl_info = {
    "name": "Conflict: Desert Storm tools",
    "author": "eSKylezZ",
    "version": (1, 2, 0),
    "blender": (3, 6, 0),
    "location": "File > Import / Export > Conflict: Desert Storm",
    "description": "Import and export .EVO meshes (props, vehicles, characters) and .prb animations, extract the game archives",
    "category": "Import-Export",
}

try:
    import bpy  # noqa: F401
except ImportError:
    bpy = None

if bpy is not None:
    from .blender import register, unregister  # noqa: F401
