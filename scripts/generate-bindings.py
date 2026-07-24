#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Generate read-only cxx bindings for core OpenDB classes from the derived schema.

Consumes `docs/derived-core-schema.json` (produced by `derive-schema.py`) and emits, for a
set of name-addressable core classes, the *read* surface -- getters, predicates, relations
(`dbFoo*` -> the target's name), and iterators (`dbSet<dbFoo>` -> count + nth-name) -- as:

  opendb-lib/src/generated.h          C++ shim declarations
  opendb-lib/src/generated.cc         C++ shim bodies (name-addressed, total: null -> default)
  opendb-lib/src/generated_bridge.rs  a second #[cxx::bridge] + re-exports
  opendb/src/generated_api.rs         `impl Db` safe wrappers

This closes the parity gap *mechanically* for the long tail of accessors, instead of
hand-writing each. Only READ methods are generated -- edits stay hand-written/audited (the
L2/write governance boundary). Methods already exposed by the hand-written shim are skipped
(name-collision check), as are non-marshallable return types (geometry structs, vectors,
optionals -- those get purpose-built hand bindings).

Regenerate:  scripts/generate-bindings.py   (then `cargo build`)
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT                                   # vyges-opendb-lib
API = ROOT.parent / "vyges-tools-opendb"     # sibling vyges-opendb crate
DB_H = LIB / "vendor/OpenROAD/src/odb/include/odb/db.h"

# Name-addressable core classes: (db.h class name) -> resolver.
#   key:     short prefix for the generated function/method names
#   args:    FFI args that identify the object (Rust &str / C++ rust::Str)
#   resolve: C++ expression yielding a `dbFoo*` (or nullptr) from (h, args...)
# The resolver *functions* (gen_*) are defined in the RESOLVERS block below.
TARGETS = {
    "dbBlock":     {"key": "block",  "args": [],                  "resolve": "gen_block(h)"},
    "dbInst":      {"key": "inst",   "args": ["inst"],            "resolve": "gen_inst(h, inst)"},
    "dbNet":       {"key": "net",    "args": ["net"],             "resolve": "gen_net(h, net)"},
    "dbBTerm":     {"key": "bterm",  "args": ["bterm"],           "resolve": "gen_bterm(h, bterm)"},
    "dbMaster":    {"key": "master", "args": ["master"],          "resolve": "gen_master(h, master)"},
    "dbITerm":     {"key": "iterm",  "args": ["inst", "pin"],     "resolve": "gen_iterm(h, inst, pin)"},
    "dbMTerm":     {"key": "mterm",  "args": ["master", "term"],  "resolve": "gen_mterm(h, master, term)"},
    "dbTechLayer": {"key": "layer",  "args": ["layer"],           "resolve": "gen_techlayer(h, layer)"},
}


def load_derive():
    """Import the sibling derive-schema.py (hyphenated name) for its db.h parser."""
    spec = importlib.util.spec_from_file_location("derive_schema", LIB / "scripts" / "derive-schema.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

# C++ scalar return type -> (rust type, cxx type, C++ default when the object is null)
SCALAR = {
    "int": ("i32", "int32_t", "0"),
    "int32_t": ("i32", "int32_t", "0"),
    "uint": ("u32", "uint32_t", "0"),
    "uint32_t": ("u32", "uint32_t", "0"),
    "unsigned": ("u32", "uint32_t", "0"),
    "bool": ("bool", "bool", "false"),
    "float": ("f32", "float", "0.0f"),
    "double": ("f64", "double", "0.0"),
}
# enum types that expose `const char* getString() const` (odb/dbTypes.h)
ENUMS = {"dbSigType", "dbIoType", "dbPlacementStatus", "dbOrientType", "dbSourceType", "dbWireType"}


def norm(t: str) -> str:
    return t.replace(" ", "")


def snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def nameable_classes(db_h: str) -> dict[str, str]:
    """class name -> a C++ expression template '{}->...' yielding its name (const char*/std::string).

    A class is nameable if it declares getConstName() or getName() with no args. dbITerm has
    neither but is addressable as inst/mterm."""
    out: dict[str, str] = {}
    for m in re.finditer(r"\bclass\s+(db[A-Za-z0-9_]*)\b[^{;]*\{", db_h):
        name = m.group(1)
        i, depth, n = m.end(), 1, len(db_h)
        while i < n and depth:
            depth += (db_h[i] == "{") - (db_h[i] == "}")
            i += 1
        body = db_h[m.end():i - 1]
        if re.search(r"\bgetConstName\s*\(\s*\)", body):
            out[name] = "{}->getConstName()"
        elif re.search(r"\bstd::string\s+getName\s*\(\s*\)", body):
            out[name] = "{}->getName()"
    out["dbITerm"] = '({0}->getInst()->getName() + "/" + {0}->getMTerm()->getName())'
    return out


def reserved_ffi() -> set[str]:
    """Function names already exported by the hand-written bridge (avoid collisions)."""
    text = (LIB / "src" / "lib.rs").read_text()
    names: set[str] = set()
    for block in re.findall(r"pub use ffi::\{([^}]*)\}", text):
        for tok in block.split(","):
            tok = tok.strip()
            if re.fullmatch(r"[a-z_][a-z0-9_]*", tok):
                names.add(tok)
    return names


def reserved_db_methods() -> set[str]:
    """`Db` methods already defined by hand (avoid collisions in generated_api.rs)."""
    text = (API / "src" / "lib.rs").read_text()
    return set(re.findall(r"pub fn (\w+)\s*\(\s*&", text))


class Emit:
    def __init__(self):
        self.h: list[str] = []
        self.cc: list[str] = []
        self.bridge: list[str] = []
        self.reexport: list[str] = []
        self.api: list[str] = []
        self.per_class: dict[str, int] = {}
        self.skipped = 0

    def add(self, cls, spec, m, nameable, reserved_fn, reserved_db, seen):
        key, resolve = spec["key"], spec["resolve"]
        args = spec["args"]
        kind, ret, name = m["kind"], m["return"], m["name"]
        fn = f"{key}_{snake(name)}"
        if fn in seen or fn in reserved_fn or fn in reserved_db:
            return False

        # C++ / Rust argument fragments
        c_params = "".join(f", rust::Str {a}" for a in args)
        r_params = "".join(f", {a}: &str" for a in args)
        c_call_args = "".join(f", {a}" for a in args)  # forwarding in Db wrappers is Rust-side
        rust_args_sig = "".join(f", {a}: &str" for a in args)
        rust_fwd = "".join(f", {a}" for a in args)

        nret = norm(ret)
        target = ret.rstrip("*").strip() if kind == "relation" else None
        elem = ret[len("dbSet<"):].rstrip(">").strip().rstrip("*").strip() if kind == "iterator" else None

        # ---- decide marshalling ------------------------------------------------
        if kind in ("getter", "predicate"):
            if nret in SCALAR:
                rty, cty, default = SCALAR[nret]
                self.h.append(f"{cty} {fn}(const OdbDb& db{c_params});")
                self.cc.append(
                    f"{cty} {fn}(const OdbDb& h{c_params}) {{ auto* p = {resolve}; "
                    f"return p ? p->{name}() : {default}; }}")
                self.bridge.append(f"        fn {fn}(db: &OdbDb{r_params}) -> {rty};")
                self.api.append(
                    f"    pub fn {fn}(&self{rust_args_sig}) -> {rty} "
                    f"{{ sys::{fn}(self.r(){rust_fwd}) }}")
            elif nret in ("std::string",):
                self._string(fn, name, resolve, c_params, r_params, rust_args_sig, rust_fwd,
                             f"rust::String(p->{name}())")
            elif nret in ("constchar*", "char*"):
                self.h.append(f"rust::String {fn}(const OdbDb& db{c_params});")
                self.cc.append(
                    f"rust::String {fn}(const OdbDb& h{c_params}) {{ auto* p = {resolve}; "
                    f"if (!p) return rust::String(); const char* v = p->{name}(); "
                    f'return rust::String(v ? v : ""); }}')
                self.bridge.append(f"        fn {fn}(db: &OdbDb{r_params}) -> String;")
                self.api.append(
                    f"    pub fn {fn}(&self{rust_args_sig}) -> String "
                    f"{{ sys::{fn}(self.r(){rust_fwd}) }}")
            elif ret in ENUMS:
                self._string(fn, name, resolve, c_params, r_params, rust_args_sig, rust_fwd,
                             f"rust::String(p->{name}().getString())")
            else:
                self.skipped += 1
                return False
        elif kind == "relation":
            if target not in nameable:
                self.skipped += 1
                return False
            nexpr = nameable[target].format("t")
            self.h.append(f"rust::String {fn}(const OdbDb& db{c_params});")
            self.cc.append(
                f"rust::String {fn}(const OdbDb& h{c_params}) {{ auto* p = {resolve}; "
                f"if (!p) return rust::String(); auto* t = p->{name}(); "
                f"return t ? rust::String({nexpr}) : rust::String(); }}")
            self.bridge.append(f"        fn {fn}(db: &OdbDb{r_params}) -> String;")
            self.api.append(
                f"    pub fn {fn}(&self{rust_args_sig}) -> String "
                f"{{ sys::{fn}(self.r(){rust_fwd}) }}")
        elif kind == "iterator":
            if elem not in nameable:
                self.skipped += 1
                return False
            nexpr = nameable[elem].format("e")
            num, nth = f"num_{fn}", f"nth_{fn}"
            if num in seen or nth in seen:
                return False
            self.h.append(f"std::size_t {num}(const OdbDb& db{c_params});")
            self.h.append(f"rust::String {nth}(const OdbDb& db{c_params}, std::size_t i);")
            self.cc.append(
                f"std::size_t {num}(const OdbDb& h{c_params}) {{ auto* p = {resolve}; "
                f"return p ? p->{name}().size() : 0; }}")
            self.cc.append(
                f"rust::String {nth}(const OdbDb& h{c_params}, std::size_t i) {{ auto* p = {resolve}; "
                f"if (!p) return rust::String(); std::size_t k = 0; "
                f"for (auto* e : p->{name}()) {{ if (k++ == i) return rust::String({nexpr}); }} "
                f"return rust::String(); }}")
            self.bridge.append(f"        fn {num}(db: &OdbDb{r_params}) -> usize;")
            self.bridge.append(f"        fn {nth}(db: &OdbDb{r_params}, i: usize) -> String;")
            self.reexport.append(num)
            self.reexport.append(nth)
            self.api.append(
                f"    pub fn {fn}(&self{rust_args_sig}) -> Vec<String> {{ "
                f"(0..sys::{num}(self.r(){rust_fwd})).map(|i| sys::{nth}(self.r(){rust_fwd}, i)).collect() }}")
            seen.add(num)
            seen.add(nth)
            seen.add(fn)
            self.per_class[cls] = self.per_class.get(cls, 0) + 1
            return True
        else:
            self.skipped += 1
            return False

        self.reexport.append(fn)
        seen.add(fn)
        self.per_class[cls] = self.per_class.get(cls, 0) + 1
        return True

    def _string(self, fn, name, resolve, c_params, r_params, rust_args_sig, rust_fwd, expr):
        self.h.append(f"rust::String {fn}(const OdbDb& db{c_params});")
        self.cc.append(
            f"rust::String {fn}(const OdbDb& h{c_params}) {{ auto* p = {resolve}; "
            f"return p ? {expr} : rust::String(); }}")
        self.bridge.append(f"        fn {fn}(db: &OdbDb{r_params}) -> String;")
        self.api.append(
            f"    pub fn {fn}(&self{rust_args_sig}) -> String {{ sys::{fn}(self.r(){rust_fwd}) }}")


BANNER = "// @generated by scripts/generate-bindings.py from docs/derived-core-schema.json -- DO NOT EDIT.\n"


def main() -> int:
    # Parse target-class methods straight from db.h (via derive-schema's parser), so classes that
    # have an upstream schema and are thus absent from derived-core-schema.json (e.g. dbTechLayer)
    # are still targetable.
    ds = load_derive()
    db_h = DB_H.read_text()
    by_name = {}
    for cname, base, body in ds.iter_class_bodies(db_h):
        if cname in TARGETS:
            by_name[cname] = ds.parse_class(cname, base, body, set())
    missing = [c for c in TARGETS if c not in by_name]
    if missing:
        print(f"error: target class(es) not found in db.h: {missing}")
        return 1

    nameable = nameable_classes(db_h)
    reserved_fn = reserved_ffi()
    reserved_db = reserved_db_methods()

    e = Emit()
    for cls, spec in TARGETS.items():
        seen: set[str] = set()
        for m in by_name[cls]["methods"]:
            if m["kind"] in ("getter", "predicate", "relation", "iterator") and not m["params"]:
                e.add(cls, spec, m, nameable, reserved_fn, reserved_db, seen)

    # ---- generated.h -----------------------------------------------------------
    (LIB / "src/generated.h").write_text(
        "// SPDX-License-Identifier: Apache-2.0\n" + BANNER +
        "#pragma once\n#include \"shim.h\"\n\n" + "\n".join(e.h) + "\n")

    # ---- generated.cc ----------------------------------------------------------
    resolvers = (
        "namespace {\n"
        "static std::string gs(rust::Str v) { return std::string(v.data(), v.size()); }\n"
        "static odb::dbBlock* gen_block(const OdbDb& h) {\n"
        "  odb::dbChip* c = h.db->getChip(); return c ? c->getBlock() : nullptr; }\n"
        "static odb::dbInst* gen_inst(const OdbDb& h, rust::Str n) {\n"
        "  odb::dbBlock* b = gen_block(h); return b ? b->findInst(gs(n).c_str()) : nullptr; }\n"
        "static odb::dbNet* gen_net(const OdbDb& h, rust::Str n) {\n"
        "  odb::dbBlock* b = gen_block(h); return b ? b->findNet(gs(n).c_str()) : nullptr; }\n"
        "static odb::dbBTerm* gen_bterm(const OdbDb& h, rust::Str n) {\n"
        "  odb::dbBlock* b = gen_block(h); return b ? b->findBTerm(gs(n).c_str()) : nullptr; }\n"
        "static odb::dbMaster* gen_master(const OdbDb& h, rust::Str n) {\n"
        "  std::string name = gs(n);\n"
        "  for (odb::dbLib* lib : h.db->getLibs()) { if (auto* m = lib->findMaster(name.c_str())) return m; }\n"
        "  return nullptr; }\n"
        "static odb::dbITerm* gen_iterm(const OdbDb& h, rust::Str inst, rust::Str pin) {\n"
        "  odb::dbInst* i = gen_inst(h, inst); return i ? i->findITerm(gs(pin).c_str()) : nullptr; }\n"
        "static odb::dbMTerm* gen_mterm(const OdbDb& h, rust::Str master, rust::Str term) {\n"
        "  odb::dbMaster* m = gen_master(h, master); return m ? m->findMTerm(gs(term).c_str()) : nullptr; }\n"
        "static odb::dbTechLayer* gen_techlayer(const OdbDb& h, rust::Str n) {\n"
        "  odb::dbTech* t = h.db->getTech(); return t ? t->findLayer(gs(n).c_str()) : nullptr; }\n"
        "}  // namespace\n")
    (LIB / "src/generated.cc").write_text(
        "// SPDX-License-Identifier: Apache-2.0\n" + BANNER +
        '#include "generated.h"\n\nusing namespace odb;\n\n' + resolvers + "\n" +
        "\n".join(e.cc) + "\n")

    # ---- generated_bridge.rs ---------------------------------------------------
    reexport = ",\n    ".join(sorted(e.reexport))
    (LIB / "src/generated_bridge.rs").write_text(
        "// SPDX-License-Identifier: Apache-2.0\n" + BANNER +
        "//! Second cxx bridge: machine-generated read accessors for core odb classes.\n"
        "//! Shares the opaque `OdbDb` handle with the hand-written `ffi` bridge in lib.rs.\n\n"
        "#[cxx::bridge]\nmod ffi_gen {\n"
        "    unsafe extern \"C++\" {\n"
        "        include!(\"generated.h\");\n"
        "        type OdbDb = crate::ffi::OdbDb;\n" +
        "\n".join(e.bridge) + "\n"
        "    }\n}\n\n"
        f"pub use ffi_gen::{{\n    {reexport},\n}};\n")

    # ---- generated_api.rs (opendb crate) ---------------------------------------
    (API / "src/generated_api.rs").write_text(
        "// SPDX-License-Identifier: Apache-2.0\n" + BANNER +
        "// Machine-generated safe `Db` read accessors. include!()'d into lib.rs (unix only),\n"
        "// so this file uses line comments (an inner //! doc is illegal mid-file).\n\n"
        "impl Db {\n" + "\n".join(e.api) + "\n}\n")

    total = sum(e.per_class.values())
    print(f"generated {total} read accessors across {len(e.per_class)} classes "
          f"({e.skipped} methods skipped: non-marshallable / unnameable / reserved)")
    for c in sorted(e.per_class, key=lambda c: -e.per_class[c]):
        print(f"  {c:<10} {e.per_class[c]:>3}")
    print("wrote: src/generated.{h,cc}, src/generated_bridge.rs, ../vyges-tools-opendb/src/generated_api.rs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
