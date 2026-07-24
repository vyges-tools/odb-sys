# Derived core schema

OpenDB has two kinds of db classes:

- **Generated** (~75): tech-layer rules, GDS, scan, module hierarchy, power domains,
  chip/3dblox, groups, guides, access-points. OpenROAD's `codeGenerator` ships a JSON
  **schema** (`src/odb/src/codeGenerator/schema/**/dbFoo.json`) for each and generates
  its C++ from that schema.
- **Hand-written** (the core we instrument): `dbInst`, `dbNet`, `dbBlock`, `dbITerm`,
  `dbBTerm`, `dbMaster`, `dbBox`, `dbWire`, `dbTech`, `dbLib`, … — declared directly in
  `db.h`, with **no schema**.

`scripts/derive-schema.py` closes that gap: it parses the public API of the hand-written
classes out of `db.h` and emits `derived-core-schema.json` — a schema in the same spirit
as the upstream one, but **method-based** (we bind public *methods*, not private fields,
so the accessor surface is exactly what a binding generator consumes).

It also doubles as a **coverage map**: each method is tagged `bridged` if our cxx layer
(hand-written shim *or* the generated bindings below) already exposes it.

## Generating from the schema

`scripts/generate-bindings.py` consumes this schema and emits the **read** surface —
getters, predicates, relations (`dbFoo*` → the target's name), and iterators
(`dbSet<dbFoo>` → count + nth-name) — for the name-addressable core classes (`dbBlock`,
`dbInst`, `dbNet`, `dbBTerm`), so the long tail of accessors is bound *mechanically*
instead of by hand:

```sh
scripts/generate-bindings.py     # then: cargo build
```

It writes four generated files (all marked `@generated … DO NOT EDIT`):

| File | Role |
| --- | --- |
| `src/generated.h` / `src/generated.cc` | C++ shim decls + name-addressed bodies (total: null → default) |
| `src/generated_bridge.rs` | a second `#[cxx::bridge]` sharing the `OdbDb` handle with the hand-written `ffi` bridge |
| `../vyges-tools-opendb/src/generated_api.rs` | safe `impl Db` wrappers (`include!`'d into `lib.rs`) |

Scope guards, by design:

- **Read-only.** Only `getter`/`predicate`/`relation`/`iterator` are generated. Edits
  (setters) stay hand-written and audited — the L2/write governance boundary.
- **Marshallable returns only.** Scalars (`int`/`uint`/`bool`/`float`), strings, the six
  `getString()` enums, nameable relations/iterators. Geometry structs (`Point`/`Rect`/
  `Polygon`), vectors, and `optional`s are skipped — those get purpose-built hand bindings.
- **No collisions.** Any generated name that clashes with a hand-written export/`Db` method
  is skipped, so the hand-written surface always wins.

Regenerating after an OpenROAD SHA bump re-derives the schema and re-emits the bindings, so
new upstream accessors are picked up automatically (subject to the guards above).

## Regenerate

```sh
scripts/fetch-odb-src.sh          # ensure vendor/OpenROAD is present (pinned SHA)
scripts/derive-schema.py          # -> docs/derived-core-schema.json
scripts/derive-schema.py --all    # include the already-generated classes too
```

## Format

```jsonc
{
  "schema_version": "vyges-derived-core-schema-v1",
  "classes": [
    {
      "name": "dbInst",
      "parent": "dbObject",
      "hand_written": true,
      "methods": [
        { "name": "getName",   "return": "std::string",    "params": [], "const": true,
          "kind": "getter",   "bridged": true },
        { "name": "getMaster", "return": "dbMaster*",       "params": [], "kind": "relation",
          "target": "dbMaster", "bridged": false },
        { "name": "getITerms", "return": "dbSet<dbITerm>",  "params": [], "kind": "iterator",
          "element": "dbITerm", "bridged": false },
        { "name": "setOrigin", "return": "void",
          "params": [{ "type": "int", "name": "x" }, { "type": "int", "name": "y" }],
          "kind": "setter",   "bridged": false }
      ]
    }
  ]
}
```

`kind` is one of `getter` · `setter` · `relation` (returns a `dbFoo*`, see `target`) ·
`iterator` (returns a `dbSet<dbFoo>`, see `element`) · `predicate` (`is*`/`has*` bool) ·
`other`. Relations + iterators are the traversal edges of the connectivity graph; getters
+ predicates are read instrumentation; setters are the edit surface.

## Known limitations (v1)

The classifier is regex-over-headers, not a clang AST, so a few signatures are
approximated:

- **Out-param getters** (`void getLocation(int& x, int& y)`) classify as `setter` because
  they return `void`. They're read accessors that hand results back through references.
- Overloaded methods appear once per declared signature.
- Only single (first) inheritance base is recorded.

These don't affect the schema's use as a coverage map or a generation seed; a later
clang-based pass can refine `kind` if/when the generator needs it.
