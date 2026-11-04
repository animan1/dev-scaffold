"""Gate changed executable application lines using reports from one test run."""

import argparse
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path


def changed_lines(diff):
    changed = {}
    filename = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            filename = line[6:]
            changed.setdefault(filename, set())
        match = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
        if match and filename:
            start, count = int(match[1]), int(match[2] if match[2] is not None else 1)
            changed[filename].update(range(start, start + count))
    return changed


def coverage_lines(xml, lcov=None):
    result = {}
    tree = ET.parse(xml)
    app_root = any(
        (item.text or "").rstrip("/").endswith("src/app") for item in tree.iter("source")
    )
    for cls in tree.iter("class"):
        filename = cls.attrib["filename"]
        if filename.startswith("src/"):
            filename = filename[4:]
        elif app_root and not filename.startswith("app/"):
            filename = "app/" + filename
        filename = "backend/src/" + filename
        result[filename] = {
            int(line.attrib["number"]): int(line.attrib["hits"]) > 0 for line in cls.iter("line")
        }
    if lcov and Path(lcov).exists():
        filename = None
        for line in Path(lcov).read_text().splitlines():
            if line.startswith("SF:"):
                filename = "frontend/" + line[3:].removeprefix("/workspace/frontend/")
                result.setdefault(filename, {})
            elif filename and line.startswith("DA:"):
                number, count, *_ = line[3:].split(",")
                result[filename][int(number)] = int(count) > 0
    return result


def measure(changed, measured):
    hits = total = 0
    missing = []
    for filename, lines in changed.items():
        for number in lines & measured.get(filename, {}).keys():
            total += 1
            if measured[filename][number]:
                hits += 1
            else:
                missing.append(f"{filename}:{number}")
    return hits, total, missing


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--xml", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=90)
    args = parser.parse_args()
    diff = subprocess.check_output(
        [
            "git",
            "diff",
            "--no-ext-diff",
            "--unified=0",
            args.base,
            "--",
            "backend/src",
            "frontend/src",
        ],
        cwd=args.root,
        text=True,
    )
    hits, total, missing = measure(
        changed_lines(diff),
        coverage_lines(args.xml, args.root / "frontend/coverage/lcov.info"),
    )
    percentage = 100 * hits / total if total else 100
    print(
        f"Changed application lines: {hits}/{total}, {percentage:.2f}% (required {args.threshold:g}%)"
    )
    if percentage < args.threshold:
        raise SystemExit("Uncovered changed lines: " + ", ".join(missing))


if __name__ == "__main__":
    main()
