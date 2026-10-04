#!/usr/bin/env python3
"""Run the W3C XML Conformance Test Suite against luce-xml and print pass rates.

The suite (xmlts20130923, https://www.w3.org/XML/Test/) is unpacked into
../.donors/xmlconf (never committed here). Every XML 1.0 and Namespaces 1.0 test of a
non-validating processor runs, Fifth Edition rules where an edition is named (XML 1.1
and Namespaces 1.1 tests are left out):

- valid and invalid documents must be accepted (invalid ones are well-formed: only a
  validating parser rejects them); where the suite gives the canonical output, ours
  must match it byte for byte;
- not-wf documents must be rejected;
- error documents may go either way, and are only counted.

Tests marked NAMESPACE="no" run with namespaces off. External entities are read from
the suite's files (the parser asks the tool, which reads them). A document in an
encoding luce-xml does not decode is transcoded here with Python's codecs and parsed
again as UTF-8, as a browser would hand it over.

  python3 tools/xmlconf.py [--suite DIR] [--list-failing] [--only xmltest]

LUCE_BASE names the compiler (default: luce-base on PATH).
"""
import argparse, os, re, subprocess, sys, tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT.parent / ".donors/xmlconf"
TOOL = ROOT / "build/xmlconf"
COLLECTIONS = ["xmltest", "sun", "ibm", "oasis", "japanese", "eduni"]
INDEXES = [
    "xmltest/xmltest.xml", "japanese/japanese.xml", "sun/sun-valid.xml", "sun/sun-invalid.xml",
    "sun/sun-not-wf.xml", "sun/sun-error.xml", "oasis/oasis.xml", "ibm/ibm_oasis_invalid.xml",
    "ibm/ibm_oasis_not-wf.xml", "ibm/ibm_oasis_valid.xml", "eduni/errata-2e/errata2e.xml",
    "eduni/namespaces/1.0/rmt-ns10.xml", "eduni/errata-3e/errata3e.xml",
    "eduni/namespaces/errata-1e/errata1e.xml", "eduni/errata-4e/errata4e.xml", "eduni/misc/ht-bh.xml",
]


def build():
    base = os.environ.get("LUCE_BASE", "luce-base")
    TOOL.parent.mkdir(exist_ok=True)
    subprocess.run([base, "build", str(ROOT / "tools/xmlconf.lucb"), "-o", str(TOOL), "--release"],
                   cwd=ROOT, check=True)


def tests(suite):
    """Every test the run covers: (collection, attributes, path, output path or None)."""
    found = []
    for index in INDEXES:
        path = suite / index
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in re.finditer(r"<TEST\b([^>]*)>", text):
            attributes = {key: double or single for key, double, single in
                          re.findall(r"""([A-Z]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""", match.group(1))}
            if attributes.get("VERSION", "1.0") not in ("1.0", ""):
                continue
            if attributes.get("RECOMMENDATION", "XML1.0") in ("XML1.1", "NS1.1"):
                continue
            if "EDITION" in attributes and "5" not in attributes["EDITION"].split():
                continue
            document = path.parent / attributes["URI"]
            output = path.parent / attributes["OUTPUT"] if "OUTPUT" in attributes else None
            found.append((index.split("/")[0], attributes, document, output))
    return found


def run_batch(jobs, timeout=600):
    """Run the tool over `jobs` [(flags, path)]: per job, ("ok", output bytes) or
    (status, message). Restarts past a job that crashes the tool."""
    results = [None] * len(jobs)
    with tempfile.TemporaryDirectory() as directory:
        start = 0
        while start < len(jobs):
            listing = Path(directory) / "list.txt"
            listing.write_text("".join(f"{flags or '-'} {path}\n" for flags, path in jobs[start:]))
            out = Path(directory) / f"out-{start}"
            out.mkdir()
            try:
                run = subprocess.run([str(TOOL), str(listing), str(out)], capture_output=True, timeout=timeout)
                lines = run.stdout.decode("utf-8", "replace").splitlines()
                crashed = run.returncode != 0
                trailer = run.stderr.decode("utf-8", "replace").strip()
            except subprocess.TimeoutExpired as expired:
                lines = (expired.stdout or b"").decode("utf-8", "replace").splitlines()
                crashed, trailer = True, "timeout"
            done = start
            for line in lines:
                number, _, rest = line.partition(" ")
                if not number.isdigit():
                    continue
                index = start + int(number)
                status, _, message = rest.partition(" ")
                if status == "ok":
                    results[index] = ("ok", (out / f"{number}.xml").read_bytes())
                else:
                    results[index] = (status, message)
                done = index + 1
            if crashed and done < len(jobs):
                results[done] = ("crash", trailer[-300:])
                done += 1
            start = done
    return results


CODECS = {"shift_jis": "shift_jis", "euc-jp": "euc_jp", "iso-2022-jp": "iso2022_jp", "big5": "big5",
          "euc-kr": "euc_kr", "gb2312": "gb2312", "iso-8859-2": "iso8859_2", "windows-1252": "cp1252",
          "koi8-r": "koi8_r", "iso-10646-ucs-2": "utf_16", "ucs-2": "utf_16", "utf-16": "utf_16"}


def transcode(path, label):
    """The document at `path`, decoded as `label` and written as UTF-8 beside it (so its
    relative entities still resolve); None if Python has no codec for it."""
    codec = CODECS.get(label.lower(), label.lower())
    try:
        text = Path(path).read_bytes().decode(codec)
    except (LookupError, UnicodeDecodeError):
        return None
    target = Path(path).with_name(f".utf8-{Path(path).name}")
    target.write_bytes(text.encode("utf-8"))
    return target


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, default=SUITE)
    parser.add_argument("--list-failing", action="store_true")
    parser.add_argument("--only", default=None)
    parser.add_argument("--no-build", action="store_true")
    options = parser.parse_args()
    if not options.no_build:
        build()
    cases = [case for case in tests(options.suite) if options.only in (None, case[0])]
    jobs = [("n" if case[1].get("NAMESPACE") == "no" else "", str(case[2])) for case in cases]
    results = run_batch(jobs)
    # Documents in encodings luce-xml leaves to its caller: transcode and parse again.
    retry = [i for i, result in enumerate(results) if result and result[0] == "encoding"]
    made = []
    if retry:
        again = []
        for i in retry:
            target = transcode(cases[i][2], results[i][1])
            if target is None:
                continue
            made.append(target)
            again.append((i, (jobs[i][0] + "u", str(target))))
        for (i, _), result in zip(again, run_batch([job for _, job in again])):
            results[i] = result
    for target in made:
        target.unlink()
    table = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    failing = []
    for (collection, attributes, document, output), result in zip(cases, results):
        kind = attributes["TYPE"]
        status = result[0] if result else "crash"
        name = f"{collection} {attributes['ID']} ({document.relative_to(options.suite)})"
        if status == "crash":
            failing.append(f"CRASH {name}: {result[1] if result else ''}")
        if kind in ("valid", "invalid"):
            row = table[collection][kind]
            row[1] += 1
            if status == "ok":
                row[0] += 1
                if output is not None:
                    compared = table[collection]["output"]
                    compared[1] += 1
                    if output.read_bytes() == result[1]:
                        compared[0] += 1
                    else:
                        failing.append(f"OUTPUT {name}\n    expected {output.read_bytes()[:300]!r}\n    actual   {result[1][:300]!r}")
            else:
                failing.append(f"REJECTED {kind} {name}: {result[1] if result else ''}")
        elif kind == "not-wf":
            row = table[collection]["not-wf"]
            row[1] += 1
            if status in ("error", "encoding") and not result[1].startswith("listener"):
                row[0] += 1
            else:
                failing.append(f"ACCEPTED not-wf {name}")
        else:
            row = table[collection]["error"]
            row[1] += 1
            if status == "error":
                row[0] += 1
    print(f"{'collection':<10} {'valid':>12} {'invalid':>12} {'not-wf':>12} {'output':>12} {'error*':>10}")
    totals = defaultdict(lambda: [0, 0])
    for collection in COLLECTIONS:
        if collection not in table:
            continue
        cells = []
        for kind in ("valid", "invalid", "not-wf", "output", "error"):
            passed, total = table[collection][kind]
            totals[kind][0] += passed
            totals[kind][1] += total
            cells.append(f"{passed}/{total}" if total else "-")
        print(f"{collection:<10} {cells[0]:>12} {cells[1]:>12} {cells[2]:>12} {cells[3]:>12} {cells[4]:>10}")
    cells = [f"{totals[k][0]}/{totals[k][1]}" for k in ("valid", "invalid", "not-wf", "output", "error")]
    print(f"{'all':<10} {cells[0]:>12} {cells[1]:>12} {cells[2]:>12} {cells[3]:>12} {cells[4]:>10}")
    print("valid, invalid: accepted; not-wf: rejected; output: canonical output matches;")
    print("error*: optional errors the parser reports (either answer conforms)")
    if options.list_failing:
        for line in failing:
            print(line)
    return 0 if not failing else 1


if __name__ == "__main__":
    sys.exit(main())
