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

This is a **mechanism, not yet a generator**. It sets up the input a future generator
would use to emit cxx shims for the core classes the way OpenROAD generates the rest — and
in the meantime it doubles as a **coverage map**: each method is tagged `bridged` if our
cxx shim already exposes it.

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
