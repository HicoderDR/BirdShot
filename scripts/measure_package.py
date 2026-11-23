# SPDX-License-Identifier: AGPL-3.0-only
"""Complete timed ZIP baselines; explicit isolated output, never overwrite artifacts."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import time
import zipfile


def main(args):
    source, archive = args.source.resolve(), args.archive.resolve()
    if os.name == "nt":
        source = Path("\\\\?\\" + str(source))
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    results = {"method": "Python3.12 zipfile on one Windows machine, same local volume",
               "cacheState": "OS disk caches not cleared; single run, not a statistical benchmark"}
    files = sorted(path for path in source.rglob("*")
                   if path.is_file() and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"})
    results.update(files=len(files), expandedBytes=sum(path.stat().st_size for path in files))
    stamp = time.perf_counter()
    repack = output / "baseline-generated.zip"
    with zipfile.ZipFile(repack, "w", zipfile.ZIP_DEFLATED, compresslevel=args.level) as target:
        for path in files:
            target.write(path, source.name + "/" + path.relative_to(source).as_posix())
    results.update(compressionLevel=args.level, generationSeconds=round(time.perf_counter() - stamp, 3),
                   generatedBytes=repack.stat().st_size)
    (output / "measurements.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps({"phase": "generation-completed", **results}), flush=True)
    destination = output / "fresh-extraction"
    stamp = time.perf_counter()
    with zipfile.ZipFile(archive) as original:
        original.extractall("\\\\?\\" + str(destination))
        results.update(inputArchiveBytes=archive.stat().st_size, extractedFiles=len(original.namelist()))
    results["extractionSeconds"] = round(time.perf_counter() - stamp, 3)
    (output / "measurements.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps({"phase": "complete", **results}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--level", type=int, default=6, choices=range(0, 10))
    main(parser.parse_args())
