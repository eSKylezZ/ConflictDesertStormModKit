"""Conflict: Desert Storm records - the game's data as PSE data files (PSE docs/sdk/data-files.md).

  python cli/ds_records.py [-g <game>] [-o <out>] [--schema <schema.lua>] [--check] [--levels] [--merged]

  -o          writes <out>/forms/<type>.lua (weapons, units, vehicles ... as records with ds1: IDs) and, with --levels,
              <out>/levels/<level>.lua (each level's placed objects / waypoints / regions with editor IDs)
  --check     the records turn back into the game's tables byte for byte (all tables)
  --merged    also <out>/merged/<TABLE>.TXT + report.txt: the tables with every switched-on mod's records\\*.lua edits

The schema comes from the PSE install (<game>\\PSE\\Records\\ds1\\schema.lua) or --schema. Everything written is made
from your own game files: keep it local, don't distribute it.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from conflict_ds_tools import archive, forms, games, placed, pse_data  # noqa: E402

ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
ap.add_argument("-g", "--game")
ap.add_argument("-o", "--out")
ap.add_argument("--schema")
ap.add_argument("--check", action="store_true")
ap.add_argument("--levels", action="store_true")
ap.add_argument("--merged", action="store_true")
a = ap.parse_args()

game = a.game or games.find_game_dir(games.GAMES["ds1"])
if not game:
    sys.exit("game folder not found - pass it with -g")
schema_path = a.schema or os.path.join(game, "PSE", "Records", "ds1", "schema.lua")
if not os.path.exists(schema_path):
    sys.exit("no schema at %s (install DesertStormFix 3.3+ or pass --schema)" % schema_path)
schema = forms.load_schema(schema_path)
gf = archive.GameFiles(game)

if a.check:
    problems = forms.check(game, schema)
    for t, p in problems:
        print("[fail] %s: %s" % (t, p))
    print("round trip: %d table(s), %d problem(s)" % (len(schema["tables"].items), len(problems)))
    if problems:
        sys.exit(1)

if a.out:
    docs = forms.generate(game, schema, gf)
    os.makedirs(os.path.join(a.out, "forms"), exist_ok=True)
    for rtype, doc in docs.items():
        pse_data.save(os.path.join(a.out, "forms", rtype + ".lua"), doc)
    print("%d record files -> %s" % (len(docs), os.path.join(a.out, "forms")))
    if a.levels:
        os.makedirs(os.path.join(a.out, "levels"), exist_ok=True)
        n = 0
        for lv in placed.LEVELS:
            doc = placed.level_refs(game, lv, docs, gf)
            if doc is not None:
                pse_data.save(os.path.join(a.out, "levels", lv + ".lua"), doc)
                n += len(doc.root["refs"].items)
        print("%d placed references in %d levels -> %s" % (n, len(placed.LEVELS), os.path.join(a.out, "levels")))
    if a.merged:
        files, report = forms.merged_tables(game, schema)
        os.makedirs(os.path.join(a.out, "merged"), exist_ok=True)
        for name, data in files.items():
            with open(os.path.join(a.out, "merged", name), "wb") as f:
                f.write(data)
        with open(os.path.join(a.out, "merged", "report.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(report) + ("\n" if report else ""))
        print("merged tables -> %s (%d report line(s))" % (os.path.join(a.out, "merged"), len(report)))
        for line in report:
            print("  ", line)
elif not a.check:
    ap.print_help()
