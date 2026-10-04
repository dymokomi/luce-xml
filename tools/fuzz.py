#!/usr/bin/env python3
"""Mutate the W3C suite's documents and parse them: the parser must never trap, hang
or grow without bound, whatever the bytes.

Each case is one of the suite's documents (with the entities and DTDs it names beside
it) changed by a few random mutations: bytes flipped, inserted, deleted or repeated,
chunks spliced in from another document, and XML's own tokens (`<`, `&`, `]]>`,
`<!ENTITY`, `%p;`, quotes, character references, deep nesting, long names) dropped in
at random places. Cases run in batches through build/xmlconf (tools/xmlconf.lucb, with
`q`: well-formedness only), each batch under a time limit; a batch that dies or times
out is narrowed to the case responsible, which is saved under build/fuzz-failures.
The peak memory of the parsing processes is reported.

First, a stress phase parses generated documents that are large or adversarial (20 MB
of markup, 100,000 attributes on one tag, 20,000 namespace declarations, deep nesting,
100,000 entities, a quadratic entity blow-up, a million references in one attribute,
long names and runs of white space): each must finish in under two seconds.

  python3 tools/fuzz.py [--cases 20000] [--seed 1] [--suite DIR]
"""
import argparse, random, re, resource, shutil, subprocess, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT.parent / ".donors/xmlconf"
TOOL = ROOT / "build/xmlconf"
FAILURES = ROOT / "build/fuzz-failures"

TOKENS = [b"<", b">", b"&", b";", b"]]>", b"<![CDATA[", b"<!--", b"-->", b"<?", b"?>", b"<!ENTITY e '&e;'>",
          b"<!ENTITY % p 'x'>", b"%p;", b"&e;", b"&#x10FFFF;", b"&#0;", b"&#99999999999;", b"'", b'"', b"=",
          b"xmlns:a='urn:a'", b"a:b", b":", b"<![INCLUDE[", b"<![IGNORE[", b"<!ATTLIST a b CDATA 'c'>",
          b"<!DOCTYPE a [", b"]>", b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff", b"\r\n", b"\r", b"\x00",
          b"\xc3", b"\xed\xa0\x80", b"<?xml version='1.0' encoding='UTF-16'?>", b"<a" * 64, b"</a>" * 64,
          b"x" * 3000, b"&amp;" * 200]
SYSTEM = re.compile(rb"""SYSTEM\s+["']([^"'#]+)["']""")


def stress_documents():
    """Large and adversarial documents: (name, bytes)."""
    items = "".join(f'<item id="i{i}" x:k="v{i}">text &amp; more {i}<![CDATA[c]]><!--c--></item>\n' for i in range(200000))
    yield "big", f'<root xmlns="urn:r" xmlns:x="urn:x">{items}</root>'.encode()
    yield "attributes", ("<a " + " ".join(f'a{i}="{i}"' for i in range(100000)) + "/>").encode()
    yield "prefixed", ('<a xmlns:p="urn:p" xmlns:q="urn:p" ' + " ".join(f'p:a{i}="{i}"' for i in range(100000)) + "/>").encode()
    yield "declarations", ("<a " + " ".join(f'xmlns:p{i}="urn:{i}"' for i in range(20000)) + " "
                           + " ".join(f'p{i}:z="{i}"' for i in range(20000)) + "/>").encode()
    yield "deep", ("<a>" * 4999 + "</a>" * 4999).encode()
    yield "deeper", ("<a>" * 200000).encode()
    yield "entities", ("<!DOCTYPE d [" + "".join(f'<!ENTITY e{i} "v{i}">' for i in range(100000)) + "]><d>"
                       + "".join(f"&e{i};" for i in range(0, 100000, 7)) + "</d>").encode()
    yield "notations", ("<!DOCTYPE d [" + "".join(f"<!NOTATION n{i} SYSTEM 'n'>" for i in range(50000)) + "]><d/>").encode()
    yield "attribute lists", ("<!DOCTYPE d [" + "".join(f'<!ATTLIST d a{i} CDATA "{i}">' for i in range(20000)) + "]>"
                              + "<d/>" * 1).encode()
    yield "quadratic", ('<!DOCTYPE d [<!ENTITY e "' + "x" * 1000000 + '">]><d>' + "&e;" * 100 + "</d>").encode()
    yield "laughs", ("<!DOCTYPE d [<!ENTITY a0 'lol'>" + "".join(f"<!ENTITY a{i} '" + f"&a{i - 1};" * 10 + "'>" for i in range(1, 10))
                     + "]><d>&a9;</d>").encode()
    yield "references", ("<d>" + "&#x41;&lt;" * 500000 + "</d>").encode()
    yield "attribute references", ('<!DOCTYPE d [<!ENTITY e "0123456789">]><d a="' + "&e;" * 1000000 + '"/>').encode()
    yield "long name", ("<" + "n" * 1000000 + "/>").encode()
    yield "white space", ("<d" + " " * 5000000 + "/>").encode()
    yield "parameter entities", ("<!DOCTYPE d [<!ENTITY % p \"<!ENTITY a 'b'>\">" + "%p;" * 200000 + "]><d/>").encode()
    yield "comment", ("<d><!--" + "-x" * 1000000 + "--></d>").encode()


def stress():
    """Parse the stress documents; answer the failures."""
    failures = []
    with tempfile.TemporaryDirectory() as directory:
        for name, data in stress_documents():
            path = Path(directory) / "stress.xml"
            path.write_bytes(data)
            started = time.monotonic()
            done, crashed, trailer = run([("q", str(path))], timeout=30)
            spent = time.monotonic() - started
            verdict = "trap" if crashed else ("slow" if spent > 2 else "ok")
            print(f"stress {name:<22} {len(data) / 1e6:6.1f} MB {spent:6.2f} s  {verdict}", flush=True)
            if verdict != "ok":
                failures.append(f"stress {name}: {verdict} {trailer.strip()[-200:]}")
    return failures


def mutate(data, rng, donors):
    data = bytearray(data)
    for _ in range(rng.randint(1, 6)):
        choice = rng.random()
        at = rng.randint(0, len(data))
        if choice < 0.2 and data:
            data[min(at, len(data) - 1)] ^= 1 << rng.randint(0, 7)
        elif choice < 0.4:
            data[at:at] = rng.choice(TOKENS)
        elif choice < 0.55 and data:
            del data[at:at + rng.randint(1, 16)]
        elif choice < 0.7 and data:
            end = min(len(data), at + rng.randint(1, 64))
            data[at:at] = data[at:end] * rng.randint(1, 8)
        elif choice < 0.85:
            donor = rng.choice(donors)
            start = rng.randint(0, max(0, len(donor) - 1))
            data[at:at] = donor[start:start + rng.randint(1, 200)]
        else:
            data[at:at] = bytes([rng.randint(0, 255)])
    return bytes(data)


def write_case(directory, seed, rng, donors):
    directory.mkdir(parents=True)
    source = seed.read_bytes()
    for name in set(SYSTEM.findall(source)):
        sibling = seed.parent / name.decode("latin-1")
        target = directory / name.decode("latin-1")
        if sibling.is_file() and target.parent == directory:
            data = sibling.read_bytes()
            target.write_bytes(mutate(data, rng, donors) if rng.random() < 0.5 else data)
    document = directory / "doc.xml"
    document.write_bytes(mutate(source, rng, donors))
    return document


def run(jobs, timeout):
    """Run `jobs` [(flags, path)] in one tool process: (finished count, crashed?, stderr)."""
    with tempfile.TemporaryDirectory() as directory:
        listing = Path(directory) / "list.txt"
        listing.write_text("".join(f"{flags} {path}\n" for flags, path in jobs))
        try:
            result = subprocess.run([str(TOOL), str(listing), directory], capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as expired:
            done = len((expired.stdout or b"").splitlines())
            return done, True, "timeout"
        done = len(result.stdout.splitlines())
        return done, result.returncode != 0, result.stderr.decode("utf-8", "replace")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=int, default=20000)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--suite", type=Path, default=SUITE)
    parser.add_argument("--batch", type=int, default=500)
    parser.add_argument("--no-stress", action="store_true")
    options = parser.parse_args()
    stressed = [] if options.no_stress else stress()
    rng = random.Random(options.seed)
    seeds = sorted(p for p in options.suite.rglob("*.xml") if "/out/" not in str(p) and p.stat().st_size < 100000)
    donors = [p.read_bytes() for p in rng.sample(seeds, min(200, len(seeds)))]
    failures = []
    with tempfile.TemporaryDirectory() as work:
        made = 0
        while made < options.cases:
            count = min(options.batch, options.cases - made)
            jobs = []
            for index in range(count):
                document = write_case(Path(work) / f"{made + index}", rng.choice(seeds), rng, donors)
                flags = "q" + ("n" if rng.random() < 0.2 else "") + ("x" if rng.random() < 0.2 else "")
                jobs.append((flags, str(document)))
            start = 0
            while start < len(jobs):
                done, crashed, trailer = run(jobs[start:], timeout=60)
                if not crashed:
                    break
                bad = start + done
                case = Path(jobs[bad][1]).parent
                FAILURES.mkdir(parents=True, exist_ok=True)
                saved = FAILURES / f"case-{made + bad}"
                shutil.rmtree(saved, ignore_errors=True)
                shutil.copytree(case, saved)
                failures.append(f"{saved} [{jobs[bad][0]}]: {trailer.strip()[-300:]}")
                print(f"FAIL {failures[-1]}", flush=True)
                start = bad + 1
            made += count
            shutil.rmtree(work, ignore_errors=True)
            Path(work).mkdir(exist_ok=True)
            print(f"{made} cases, {len(failures)} failing", flush=True)
    peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    peak_mb = peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024
    print(f"{options.cases} cases: {len(failures)} traps or hangs; peak memory of a parsing process {peak_mb:.1f} MB")
    for line in stressed:
        print(f"FAIL {line}")
    return 1 if failures or stressed else 0


if __name__ == "__main__":
    sys.exit(main())
