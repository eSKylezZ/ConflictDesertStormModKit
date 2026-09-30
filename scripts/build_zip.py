"""Build the installable Blender extension zip: dist/conflict_ds_tools-<version>.zip."""
import os, re, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "conflict_ds_tools")


def main():
    with open(os.path.join(PKG, "blender_manifest.toml"), encoding="utf-8") as f:
        version = re.search(r'^version\s*=\s*"([^"]+)"', f.read(), re.M).group(1)
    out_dir = os.path.join(ROOT, "dist")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "conflict_ds_tools-%s.zip" % version)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(os.listdir(PKG)):
            if name.endswith((".py", ".toml")):
                z.write(os.path.join(PKG, name), "conflict_ds_tools/" + name)
    print(out)


if __name__ == "__main__":
    main()
