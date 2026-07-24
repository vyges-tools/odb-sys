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
    "dbRow":       {"key": "row",    "args": ["row"],             "resolve": "gen_row(h, row)"},
    "dbVia":       {"key": "via",    "args": ["via"],             "resolve": "gen_via(h, via)"},
    "dbTechVia":   {"key": "techvia","args": ["via"],             "resolve": "gen_techvia(h, via)"},
    "dbTechNonDefaultRule": {"key": "ndr", "args": ["rule"],      "resolve": "gen_ndr(h, rule)"},
    "dbSite":      {"key": "site",   "args": ["site"],            "resolve": "gen_site(h, site)"},
    # index-addressed collections (no names) — addressed by position, and dbBox/dbWire by owner.
    "dbObstruction": {"key": "obs",  "args": [{"name": "idx", "type": "idx"}], "resolve": "gen_obstruction(h, idx)"},
    "dbSWire":     {"key": "swire",  "args": ["net", {"name": "idx", "type": "idx"}], "resolve": "gen_swire(h, net, idx)"},
    "dbWire":      {"key": "wire",   "args": ["net"],             "resolve": "gen_wire(h, net)"},
    "dbFill":      {"key": "fill",   "args": [{"name": "idx", "type": "idx"}], "resolve": "gen_fill(h, idx)"},
    "dbBox":       {"key": "box",    "args": [{"name": "idx", "type": "idx"}], "resolve": "gen_box(h, idx)"},
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
# enum types that expose `const char* getString() const` (odb/dbTypes.h) AND a `dbFoo(const char*)`
# constructor — so they marshal both ways (getter → string, setter param ← string).
ENUMS = {"dbSigType", "dbIoType", "dbPlacementStatus", "dbOrientType", "dbSourceType", "dbWireType"}

# geometry structs returned by value — expanded into scalar (int) sub-fields (suffix, accessor),
# so `Rect getBBox()` becomes get_b_box_{x_min,y_min,x_max,y_max,dx,dy}. Reuses the scalar path.
STRUCT_FIELDS = {
    "Point": [("x", "getX"), ("y", "getY")],
    "Rect": [("x_min", "xMin"), ("y_min", "yMin"), ("x_max", "xMax"), ("y_max", "yMax"),
             ("dx", "dx"), ("dy", "dy")],
}

# setter param scalar -> (cxx type, rust type)
SCALAR_IN = {
    "int": ("int32_t", "i32"), "int32_t": ("int32_t", "i32"),
    "uint": ("uint32_t", "u32"), "uint32_t": ("uint32_t", "u32"), "unsigned": ("uint32_t", "u32"),
    "int64_t": ("int64_t", "i64"), "uint64_t": ("uint64_t", "u64"),
    "bool": ("bool", "bool"), "float": ("float", "f32"), "double": ("double", "f64"),
}
RUST_KW = {"type", "match", "move", "ref", "box", "fn", "let", "mut", "self", "use", "mod", "impl",
           "as", "in", "loop", "if", "else", "for", "while", "const", "static", "trait", "where",
           "enum", "struct", "crate", "pub", "return", "break", "continue", "dyn", "async", "await",
           "gen", "become", "yield", "macro", "super", "true", "false", "unsafe", "extern"}


def marshal_param(ptype: str, arg: str):
    """A setter value param -> (cxx_param_type, rust_param_type, cpp_arg_expr). None if unmarshallable."""
    n = norm(ptype)
    if n in SCALAR_IN:
        cxx, rty = SCALAR_IN[n]
        return cxx, rty, arg
    if n == "std::string":
        return "rust::Str", "&str", f"gs({arg})"
    if n in ("constchar*", "char*"):
        return "rust::Str", "&str", f"gs({arg}).c_str()"
    if ptype in ENUMS:
        return "rust::Str", "&str", f"odb::{ptype}(gs({arg}).c_str())"
    return None


def norm(t: str) -> str:
    return t.replace(" ", "")


def snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def normalize_args(args):
    """Args may be a bare str (a name-string key, C++ rust::Str / Rust &str) or a dict
    {name, type:'idx'} for an integer index (C++ std::size_t / Rust usize)."""
    out = []
    for a in args:
        if isinstance(a, str):
            out.append((a, "str"))
        else:
            out.append((a["name"], a.get("type", "str")))
    return out


def _cty(kind: str) -> str:
    return "std::size_t" if kind == "idx" else "rust::Str"


def _rty(kind: str) -> str:
    return "usize" if kind == "idx" else "&str"


def key_exprs(argspecs):
    """(dispatch call fragment, discovery descriptors) for a class's addressing keys."""
    calls = ", ".join(
        (f"k_idx(keys, {i})?" if k == "idx" else f"k_str(keys, {i})?")
        for i, (n, k) in enumerate(argspecs))
    desc = [("idx:" + n) if k == "idx" else ("str:" + n) for n, k in argspecs]
    return calls, desc


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
        # separate buffers for the governance-gated write (setter) surface
        self.wh: list[str] = []
        self.wcc: list[str] = []
        self.wbridge: list[str] = []
        self.wreexport: list[str] = []
        self.wapi: list[str] = []
        self.wper_class: dict[str, int] = {}
        # runtime registry: (class, field, value_kind, keys_desc, dispatch_arm) for reads,
        # and (class, field, value_types_desc, keys_desc, dispatch_arm) for writes.
        self.reg: list[tuple] = []
        self.wreg: list[tuple] = []

    def add_setter(self, cls, spec, m, reserved_fn, reserved_db, seen):
        """Emit a `set*`/`clear*` setter with fully-marshallable params -> Result<()> (throws on
        missing object). Written to the gated write buffers, not the read surface."""
        key, resolve = spec["key"], spec["resolve"]
        name = m["name"]
        if not (name.startswith("set") or name.startswith("clear")):
            return False
        fn = f"{key}_{snake(name)}"
        if fn in seen or fn in reserved_fn or fn in reserved_db:
            return False

        argspecs = normalize_args(spec["args"])
        used = {n for n, _ in argspecs}
        # marshal each value param; bail if any is a pointer/struct/unknown type
        cxx_vals, rust_vals, cpp_args = [], [], []
        for i, p in enumerate(m["params"]):
            mp = marshal_param(p["type"], f"a{i}")
            if mp is None:
                return False
            cxx_t, rust_t, expr = mp
            pn = p.get("name")
            if pn and re.fullmatch(r"[A-Za-z_]\w*", pn):
                pn = snake(pn)  # header names are camelCase; snake for idiomatic Rust params
            if not pn or pn in RUST_KW or pn in used:
                pn = f"a{i}"
            used.add(pn)
            expr = expr.replace(f"a{i}", pn)
            cxx_vals.append(f"{cxx_t} {pn}")
            rust_vals.append(f"{pn}: {rust_t}")
            cpp_args.append(expr)

        c_ids = "".join(f", {_cty(k)} {n}" for n, k in argspecs)
        r_ids = "".join(f", {n}: {_rty(k)}" for n, k in argspecs)
        fwd = "".join(f", {n}" for n, k in argspecs) + "".join(
            f", {v.split(':')[0].strip()}" for v in rust_vals)
        c_vals = "".join(f", {v}" for v in cxx_vals)
        r_vals = "".join(f", {v}" for v in rust_vals)
        call = ", ".join(cpp_args)

        self.wh.append(f"void {fn}(const OdbDb& db{c_ids}{c_vals});")
        self.wcc.append(
            f"void {fn}(const OdbDb& h{c_ids}{c_vals}) {{ auto* p = {resolve}; "
            f'if (!p) throw std::runtime_error("vyges-opendb: {cls} not found"); '
            f"p->{name}({call}); }}")
        self.wbridge.append(f"        fn {fn}(db: &OdbDb{r_ids}{r_vals}) -> Result<()>;")
        self.wreexport.append(fn)
        self.wapi.append(
            f"    pub fn {fn}(&mut self{r_ids}{r_vals}) -> crate::Result<()> "
            f"{{ Ok(sys::{fn}(self.r(){fwd})?) }}")
        seen.add(fn)
        self.wper_class[cls] = self.wper_class.get(cls, 0) + 1

        # runtime write registry: convert each value from the CLI's `values` slice
        rust_types = [v.split(":", 1)[1].strip() for v in rust_vals]

        def conv(j, rt):
            if rt == "&str":
                return f"val(values, {j})?"
            return (f'val(values, {j})?.parse().map_err(|_| '
                    f'crate::Error::Odb(format!("value #{j} must be a {rt}")))?')

        field = snake(name)
        key_call, keys_desc = key_exprs(argspecs)
        value_types = ["str" if rt == "&str" else rt for rt in rust_types]
        # Db setter signature is (keys..., values...) — join both, skipping an empty key list.
        parts = ([key_call] if key_call else []) + [conv(j, rt) for j, rt in enumerate(rust_types)]
        arm = f'        ("{cls}", "{field}") => {{ db.{fn}({", ".join(parts)})?; Ok(()) }},'
        self.wreg.append((cls, field, value_types, keys_desc, arm))
        return True

    def add(self, cls, spec, m, nameable, reserved_fn, reserved_db, seen):
        key, resolve = spec["key"], spec["resolve"]
        argspecs = normalize_args(spec["args"])
        kind, ret, name = m["kind"], m["return"], m["name"]
        fn = f"{key}_{snake(name)}"
        if fn in seen or fn in reserved_fn or fn in reserved_db:
            return False

        # C++ / Rust argument fragments (name-string or integer-index args)
        c_params = "".join(f", {_cty(k)} {n}" for n, k in argspecs)
        r_params = "".join(f", {n}: {_rty(k)}" for n, k in argspecs)
        rust_args_sig = r_params
        rust_fwd = "".join(f", {n}" for n, k in argspecs)

        nret = norm(ret)
        target = ret.rstrip("*").strip() if kind == "relation" else None
        elem = ret[len("dbSet<"):].rstrip(">").strip().rstrip("*").strip() if kind == "iterator" else None

        field = snake(name)
        key_call, keys_desc = key_exprs(argspecs)
        arm = f'        ("{cls}", "{field}") => Ok(serde_json::json!(db.{fn}({key_call}))),'
        reg_kind = None

        # ---- decide marshalling ------------------------------------------------
        if kind in ("getter", "predicate"):
            if nret in SCALAR:
                rty, cty, default = SCALAR[nret]
                reg_kind = rty
                self.h.append(f"{cty} {fn}(const OdbDb& db{c_params});")
                self.cc.append(
                    f"{cty} {fn}(const OdbDb& h{c_params}) {{ auto* p = {resolve}; "
                    f"return p ? p->{name}() : {default}; }}")
                self.bridge.append(f"        fn {fn}(db: &OdbDb{r_params}) -> {rty};")
                self.api.append(
                    f"    pub fn {fn}(&self{rust_args_sig}) -> {rty} "
                    f"{{ sys::{fn}(self.r(){rust_fwd}) }}")
            elif nret in ("std::string",):
                reg_kind = "string"
                self._string(fn, name, resolve, c_params, r_params, rust_args_sig, rust_fwd,
                             f"rust::String(p->{name}())")
            elif nret in ("constchar*", "char*"):
                reg_kind = "string"
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
                reg_kind = "string"
                self._string(fn, name, resolve, c_params, r_params, rust_args_sig, rust_fwd,
                             f"rust::String(p->{name}().getString())")
            elif ret.replace("odb::", "").strip() in STRUCT_FIELDS \
                    and not any(c in ret for c in "<&*"):
                # a geometry struct (Point/Rect) returned by value -> N scalar (int) sub-fields.
                base = ret.replace("odb::", "").strip()
                emitted = 0
                for suffix, accessor in STRUCT_FIELDS[base]:
                    sub = f"{fn}_{suffix}"
                    if sub in seen or sub in reserved_fn or sub in reserved_db:
                        continue
                    self.h.append(f"int32_t {sub}(const OdbDb& db{c_params});")
                    self.cc.append(
                        f"int32_t {sub}(const OdbDb& h{c_params}) {{ auto* p = {resolve}; "
                        f"return p ? p->{name}().{accessor}() : 0; }}")
                    self.bridge.append(f"        fn {sub}(db: &OdbDb{r_params}) -> i32;")
                    self.api.append(
                        f"    pub fn {sub}(&self{rust_args_sig}) -> i32 "
                        f"{{ sys::{sub}(self.r(){rust_fwd}) }}")
                    self.reexport.append(sub)
                    seen.add(sub)
                    subarm = (f'        ("{cls}", "{field}_{suffix}") => '
                              f"Ok(serde_json::json!(db.{sub}({key_call}))),")
                    self.reg.append((cls, f"{field}_{suffix}", "i32", keys_desc, subarm))
                    emitted += 1
                if emitted:
                    self.per_class[cls] = self.per_class.get(cls, 0) + 1
                    return True
                self.skipped += 1
                return False
            else:
                self.skipped += 1
                return False
        elif kind == "relation":
            if target not in nameable:
                self.skipped += 1
                return False
            reg_kind = "string"
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
            self.reg.append((cls, field, "list", keys_desc, arm))
            return True
        else:
            self.skipped += 1
            return False

        self.reexport.append(fn)
        seen.add(fn)
        self.per_class[cls] = self.per_class.get(cls, 0) + 1
        self.reg.append((cls, field, reg_kind, keys_desc, arm))
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
        seen_w: set[str] = set()
        for m in by_name[cls]["methods"]:
            if m["kind"] == "setter":
                e.add_setter(cls, spec, m, reserved_fn, reserved_db, seen_w)

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
        "static odb::dbRow* gen_row(const OdbDb& h, rust::Str n) {\n"
        "  odb::dbBlock* b = gen_block(h); if (!b) return nullptr; std::string name = gs(n);\n"
        "  for (odb::dbRow* r : b->getRows()) { if (r->getName() == name) return r; } return nullptr; }\n"
        "static odb::dbVia* gen_via(const OdbDb& h, rust::Str n) {\n"
        "  odb::dbBlock* b = gen_block(h); return b ? b->findVia(gs(n).c_str()) : nullptr; }\n"
        "static odb::dbTechVia* gen_techvia(const OdbDb& h, rust::Str n) {\n"
        "  odb::dbTech* t = h.db->getTech(); return t ? t->findVia(gs(n).c_str()) : nullptr; }\n"
        "static odb::dbTechNonDefaultRule* gen_ndr(const OdbDb& h, rust::Str n) {\n"
        "  std::string name = gs(n); odb::dbBlock* b = gen_block(h);\n"
        "  if (b) { if (auto* r = b->findNonDefaultRule(name.c_str())) return r; }\n"
        "  odb::dbTech* t = h.db->getTech(); return t ? t->findNonDefaultRule(name.c_str()) : nullptr; }\n"
        "static odb::dbSite* gen_site(const OdbDb& h, rust::Str n) {\n"
        "  std::string name = gs(n);\n"
        "  for (odb::dbLib* lib : h.db->getLibs()) { if (auto* s = lib->findSite(name.c_str())) return s; }\n"
        "  return nullptr; }\n"
        "static odb::dbObstruction* gen_obstruction(const OdbDb& h, std::size_t i) {\n"
        "  odb::dbBlock* b = gen_block(h); if (!b) return nullptr;\n"
        "  std::size_t k = 0; for (odb::dbObstruction* o : b->getObstructions()) { if (k++ == i) return o; }\n"
        "  return nullptr; }\n"
        "static odb::dbSWire* gen_swire(const OdbDb& h, rust::Str net, std::size_t i) {\n"
        "  odb::dbNet* n = gen_net(h, net); if (!n) return nullptr;\n"
        "  std::size_t k = 0; for (odb::dbSWire* w : n->getSWires()) { if (k++ == i) return w; } return nullptr; }\n"
        "static odb::dbWire* gen_wire(const OdbDb& h, rust::Str net) {\n"
        "  odb::dbNet* n = gen_net(h, net); return n ? n->getWire() : nullptr; }\n"
        "static odb::dbFill* gen_fill(const OdbDb& h, std::size_t i) {\n"
        "  odb::dbBlock* b = gen_block(h); if (!b) return nullptr;\n"
        "  std::size_t k = 0; for (odb::dbFill* f : b->getFills()) { if (k++ == i) return f; } return nullptr; }\n"
        "static odb::dbBox* gen_box(const OdbDb& h, std::size_t i) {\n"
        "  odb::dbObstruction* o = gen_obstruction(h, i); return o ? o->getBBox() : nullptr; }\n"
        "}  // namespace\n")

    # ---- generated_resolvers.h (shared by the read + write .cc) -----------------
    # inline (not static-in-anon-namespace) so a resolver unused in one TU doesn't warn.
    resolver_body = (resolvers.replace("namespace {\n", "").replace("}  // namespace\n", "")
                     .replace("static ", "inline "))
    (LIB / "src/generated_resolvers.h").write_text(
        "// SPDX-License-Identifier: Apache-2.0\n" + BANNER +
        '#pragma once\n#include "shim.h"\n\n' + resolver_body)

    # ---- generated.cc ----------------------------------------------------------
    (LIB / "src/generated.cc").write_text(
        "// SPDX-License-Identifier: Apache-2.0\n" + BANNER +
        '#include "generated.h"\n#include "generated_resolvers.h"\n\nusing namespace odb;\n\n' +
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

    # ---- write surface (gated behind the `gen-write` feature) -------------------
    (LIB / "src/generated_write.h").write_text(
        "// SPDX-License-Identifier: Apache-2.0\n" + BANNER +
        "#pragma once\n#include \"shim.h\"\n\n" + "\n".join(e.wh) + "\n")
    (LIB / "src/generated_write.cc").write_text(
        "// SPDX-License-Identifier: Apache-2.0\n" + BANNER +
        '#include "generated_write.h"\n#include "generated_resolvers.h"\n\n'
        "#include <stdexcept>\n\nusing namespace odb;\n\n" + "\n".join(e.wcc) + "\n")
    wreexport = ",\n    ".join(sorted(e.wreexport))
    (LIB / "src/generated_write_bridge.rs").write_text(
        "// SPDX-License-Identifier: Apache-2.0\n" + BANNER +
        "//! Third cxx bridge: machine-generated SETTERS (the L2/write governance surface).\n"
        "//! Gated behind the `gen-write` feature — absent from the default read-only build.\n\n"
        "#[cxx::bridge]\nmod ffi_gen_write {\n"
        "    unsafe extern \"C++\" {\n"
        "        include!(\"generated_write.h\");\n"
        "        type OdbDb = crate::ffi::OdbDb;\n" +
        "\n".join(e.wbridge) + "\n"
        "    }\n}\n\n"
        f"pub use ffi_gen_write::{{\n    {wreexport},\n}};\n")
    (API / "src/generated_write_api.rs").write_text(
        "// SPDX-License-Identifier: Apache-2.0\n" + BANNER +
        "// Machine-generated `Db` SETTERS (L2/write). include!()'d into lib.rs behind `gen-write`.\n"
        "// &mut self + Result<()>; throws (-> Err) when the addressed object does not exist.\n\n"
        "impl Db {\n" + "\n".join(e.wapi) + "\n}\n")

    # ---- generated_registry.rs (runtime discovery + get/set dispatch) ----------
    def keys_lit(desc):
        return "&[" + ", ".join(f'"{d}"' for d in desc) + "]"

    reg = sorted(e.reg)
    wreg = sorted(e.wreg)
    read_fields = "\n".join(
        f'    Field {{ class: "{c}", field: "{f}", value: "{k}", keys: {keys_lit(kd)} }},'
        for c, f, k, kd, _ in reg)
    read_arms = "\n".join(t[4] for t in reg)
    write_fields = "\n".join(
        f'    WriteField {{ class: "{c}", field: "{f}", values: &[{", ".join(chr(34)+v+chr(34) for v in vt)}], keys: {keys_lit(kd)} }},'
        for c, f, vt, kd, _ in wreg)
    write_arms = "\n".join(t[4] for t in wreg)

    (API / "src/generated_registry.rs").write_text(
        "// SPDX-License-Identifier: Apache-2.0\n" + BANNER +
        "// Runtime registry over the generated accessors: field discovery + string-keyed\n"
        "// get/set dispatch, so the whole surface is reachable from the CLI / `vyges mcp`\n"
        "// without a bespoke subcommand per accessor. include!()'d into `mod registry`.\n\n"
        "use crate::Db;\n\n"
        "/// A readable field: its class, name, JSON value kind, and addressing keys\n"
        "/// (`\"str:inst\"` / `\"idx:idx\"` — order matters).\n"
        "pub struct Field {\n"
        "    pub class: &'static str,\n    pub field: &'static str,\n"
        "    pub value: &'static str,\n    pub keys: &'static [&'static str],\n}\n\n"
        "/// Every readable field the generated surface exposes.\n"
        "pub const FIELDS: &[Field] = &[\n" + read_fields + "\n];\n\n"
        "fn k_str(keys: &[String], i: usize) -> crate::Result<&str> {\n"
        "    keys.get(i).map(String::as_str)\n"
        "        .ok_or_else(|| crate::Error::Odb(format!(\"missing key #{i}\")))\n}\n"
        "fn k_idx(keys: &[String], i: usize) -> crate::Result<usize> {\n"
        "    k_str(keys, i)?.parse()\n"
        "        .map_err(|_| crate::Error::Odb(format!(\"key #{i} must be an integer index\")))\n}\n\n"
        "/// Read a field by (class, field) with string-encoded addressing keys -> JSON value.\n"
        "pub fn get(db: &Db, class: &str, field: &str, keys: &[String]) "
        "-> crate::Result<serde_json::Value> {\n"
        "    match (class, field) {\n" + read_arms + "\n"
        "        _ => Err(crate::Error::Odb(format!(\"unknown read field: {class}.{field}\"))),\n"
        "    }\n}\n\n"
        "/// A writable field: its class, name, value types to supply, and addressing keys.\n"
        "#[cfg(feature = \"gen-write\")]\n"
        "pub struct WriteField {\n"
        "    pub class: &'static str,\n    pub field: &'static str,\n"
        "    pub values: &'static [&'static str],\n    pub keys: &'static [&'static str],\n}\n\n"
        "/// Every writable field (gated behind `gen-write`).\n"
        "#[cfg(feature = \"gen-write\")]\n"
        "pub const WRITE_FIELDS: &[WriteField] = &[\n" + write_fields + "\n];\n\n"
        "#[cfg(feature = \"gen-write\")]\n"
        "fn val(values: &[String], j: usize) -> crate::Result<&str> {\n"
        "    values.get(j).map(String::as_str)\n"
        "        .ok_or_else(|| crate::Error::Odb(format!(\"missing value #{j}\")))\n}\n\n"
        "/// Apply a setter by (class, field) with string keys + string-encoded values.\n"
        "#[cfg(feature = \"gen-write\")]\n"
        "pub fn set(db: &mut Db, class: &str, field: &str, keys: &[String], values: &[String]) "
        "-> crate::Result<()> {\n"
        "    match (class, field) {\n" + write_arms + "\n"
        "        _ => Err(crate::Error::Odb(format!(\"unknown write field: {class}.{field}\"))),\n"
        "    }\n}\n")

    total = sum(e.per_class.values())
    wtotal = sum(e.wper_class.values())
    print(f"generated {total} read accessors across {len(e.per_class)} classes "
          f"({e.skipped} methods skipped: non-marshallable / unnameable / reserved)")
    for c in sorted(e.per_class, key=lambda c: -e.per_class[c]):
        w = e.wper_class.get(c, 0)
        print(f"  {c:<10} {e.per_class[c]:>3} read  {w:>3} write")
    print(f"generated {wtotal} setters (gated behind `gen-write`) across {len(e.wper_class)} classes")
    print(f"registry: {len(e.reg)} read fields + {len(e.wreg)} write fields (get/set dispatch)")
    print("wrote: src/generated{,_write}.{h,cc}, src/generated{,_write}_bridge.rs, "
          "src/generated_resolvers.h, ../vyges-tools-opendb/src/generated{,_write}_api.rs, "
          "../vyges-tools-opendb/src/generated_registry.rs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
