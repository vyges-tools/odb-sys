#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Derive a machine-readable schema from OpenDB's *hand-written* core classes.

OpenROAD's codeGenerator ships JSON schemas for ~75 newer/peripheral db classes
(tech-layer rules, GDS, scan, module hierarchy, power domains, chip/3dblox, …) and
generates their C++ from those schemas. The **core** classes we actually instrument
-- dbInst, dbNet, dbBlock, dbITerm, dbBTerm, dbMaster, dbBox, dbWire, dbTech, dbLib,
… -- are hand-written in db.h and have *no* schema.

This tool parses the public API of those hand-written classes out of db.h and emits a
schema in the same spirit as the upstream one, but **method-based** (we bind public
methods, not private fields -- so the accessor surface is exactly what a binding
generator needs). It also cross-references which methods our cxx shim already exposes,
so the schema doubles as a coverage map.

Not wired into the build -- this sets up the *mechanism* (headers -> schema) so a future
generator can emit cxx shims for the core classes the way OpenROAD generates the rest.

Usage:
  scripts/derive-schema.py [--odb <path-to-odb>] [--out <file.json>] [--all]

  --odb   path to src/odb (default: vendor/OpenROAD/src/odb)
  --out   output JSON path (default: docs/derived-core-schema.json)
  --all   include classes that already have an upstream schema (default: core only)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# A public method declaration inside a class body, e.g.
#   dbSet<dbITerm> getITerms() const;
#   void setOrigin(int x, int y);
#   dbBox* getBBox();
METHOD_RE = re.compile(
    r"""^\s*
        (?P<ret>[A-Za-z_][\w:*&<>,\s]*?)      # return type (greedy-ish, may hold templates)
        \s+
        (?P<name>[A-Za-z_]\w*)                 # method name
        \s*\((?P<params>[^;{]*)\)              # ( params )
        \s*(?P<const>const)?
        \s*(?:=\s*0)?                          # pure-virtual tail
        \s*(?:;|\{)                            # decl ; or inline {
    """,
    re.VERBOSE,
)

# Lines that look like methods but aren't accessors we care about.
SKIP_NAME = {"operator", "dbObject"}

SETTER_PREFIXES = ("set", "add", "create", "destroy", "remove", "clear",
                   "rename", "swap", "connect", "disconnect", "assign", "apply")
GETTER_PREFIXES = ("get", "is", "has", "find", "first")


def upstream_schema_classes(odb: Path) -> set[str]:
    """Class names that already have an upstream codeGenerator schema."""
    schema_dir = odb / "src" / "codeGenerator" / "schema"
    names = set()
    if schema_dir.is_dir():
        for p in schema_dir.rglob("*.json"):
            names.add(p.stem)
    return names


def bridged_methods(odb_lib: Path) -> set[str]:
    """odb method names our cxx shim already calls (best-effort, for a coverage cross-ref)."""
    shim = odb_lib / "src" / "shim.cc"
    called = set()
    if shim.is_file():
        # match `->methodName(` and `.methodName(` on odb objects
        for m in re.finditer(r"[.>]([a-z][A-Za-z0-9]*)\s*\(", shim.read_text()):
            called.add(m.group(1))
    return called


def iter_class_bodies(text: str):
    """Yield (class_name, base, body_text) for each `class dbFoo ... { ... };`."""
    # find class headers; then brace-match to the closing }.
    for m in re.finditer(r"\bclass\s+(db[A-Za-z0-9_]*)\b([^{;]*)\{", text):
        name = m.group(1)
        base = m.group(2).strip()
        i = m.end()  # just past the opening {
        depth = 1
        n = len(text)
        while i < n and depth:
            c = text[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            i += 1
        yield name, base, text[m.end():i - 1]


def parse_params(raw: str) -> list[dict]:
    raw = raw.strip()
    if not raw or raw == "void":
        return []
    params = []
    # split on top-level commas (params rarely nest templates here; be defensive anyway)
    depth, cur = 0, ""
    for ch in raw:
        if ch in "<([":
            depth += 1
        elif ch in ">)]":
            depth -= 1
        if ch == "," and depth == 0:
            params.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        params.append(cur)
    out = []
    for p in params:
        p = p.strip()
        # last identifier is the param name (if present)
        mm = re.match(r"^(?P<type>.+?)\s*(?P<name>[A-Za-z_]\w*)?\s*(?:=.*)?$", p)
        typ = (mm.group("type") or p).strip() if mm else p
        nm = mm.group("name") if mm else None
        out.append({"type": typ.strip(), "name": nm})
    return out


def classify(name: str, ret: str, params: list[dict]) -> tuple[str, dict]:
    """Return (kind, extra) where kind in relation|iterator|getter|setter|predicate|other."""
    ret_c = ret.strip()
    extra: dict = {}
    if ret_c.startswith("dbSet<"):
        inner = ret_c[len("dbSet<"):].rstrip(">").strip().rstrip("*").strip()
        extra["element"] = inner
        return "iterator", extra
    if re.match(r"^db[A-Za-z0-9_]+\s*\*$", ret_c):
        extra["target"] = ret_c.rstrip("*").strip()
        return "relation", extra
    lname = name[0].lower() + name[1:]
    if any(lname.startswith(pre) for pre in SETTER_PREFIXES) or ret_c == "void":
        return "setter", extra
    if ret_c == "bool" and any(lname.startswith(pre) for pre in ("is", "has")):
        return "predicate", extra
    if any(lname.startswith(pre) for pre in GETTER_PREFIXES) and not params:
        return "getter", extra
    if not params and ret_c != "void":
        return "getter", extra
    return "other", extra


def parse_class(name: str, base: str, body: str, bridged: set[str]) -> dict:
    # walk lines, tracking the current access section (default: private for a class)
    access = "private"
    methods = []
    for line in body.splitlines():
        s = line.strip()
        if s in ("public:", "protected:", "private:"):
            access = s[:-1]
            continue
        if access != "public":
            continue
        if s.startswith(("//", "friend", "typedef", "using", "enum", "static ", "struct ")):
            continue
        m = METHOD_RE.match(line)
        if not m:
            continue
        mname = m.group("name")
        ret = " ".join(m.group("ret").split())
        if mname in SKIP_NAME or ret in ("class", "struct") or mname.startswith("~"):
            continue
        if mname == name:  # constructor
            continue
        params = parse_params(m.group("params"))
        kind, extra = classify(mname, ret, params)
        entry = {
            "name": mname,
            "return": ret,
            "params": params,
            "const": bool(m.group("const")),
            "kind": kind,
            "bridged": mname in bridged,
            **extra,
        }
        methods.append(entry)
    base_clean = re.sub(r"^\s*:\s*public\s+", "", base).split(",")[0].strip() or None
    return {"name": name, "parent": base_clean, "methods": methods}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--odb", default="vendor/OpenROAD/src/odb")
    ap.add_argument("--out", default="docs/derived-core-schema.json")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    odb = (root / args.odb).resolve()
    db_h = odb / "include" / "odb" / "db.h"
    if not db_h.is_file():
        print(f"error: db.h not found at {db_h} (run scripts/fetch-odb-src.sh first)", file=sys.stderr)
        return 1

    upstream = upstream_schema_classes(odb)
    bridged = bridged_methods(root)
    text = db_h.read_text()

    classes = []
    for cname, base, body in iter_class_bodies(text):
        hand_written = cname not in upstream
        if not args.all and not hand_written:
            continue
        cls = parse_class(cname, base, body, bridged)
        if not cls["methods"]:
            continue
        cls["hand_written"] = hand_written
        classes.append(cls)

    classes.sort(key=lambda c: c["name"])
    total_methods = sum(len(c["methods"]) for c in classes)
    total_bridged = sum(1 for c in classes for m in c["methods"] if m["bridged"])
    out = {
        "schema_version": "vyges-derived-core-schema-v1",
        "source": "OpenDB public headers (src/odb/include/odb/db.h)",
        "note": "Method-based schema derived from the hand-written core classes; parallels "
                "OpenROAD's field-based codeGenerator schema for the classes it does not cover.",
        "scope": "all" if args.all else "hand-written core only",
        "class_count": len(classes),
        "method_count": total_methods,
        "bridged_count": total_bridged,
        "classes": classes,
    }
    out_path = (root / args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2) + "\n")

    print(f"derived schema: {len(classes)} classes, {total_methods} public methods "
          f"({total_bridged} already bridged) -> {out_path.relative_to(root)}")
    # top classes by surface size, for a quick sense of the work
    for c in sorted(classes, key=lambda c: len(c["methods"]), reverse=True)[:8]:
        kinds = {}
        for m in c["methods"]:
            kinds[m["kind"]] = kinds.get(m["kind"], 0) + 1
        br = sum(1 for m in c["methods"] if m["bridged"])
        print(f"  {c['name']:<16} {len(c['methods']):>3} methods  "
              f"{br:>2} bridged  {dict(sorted(kinds.items()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
