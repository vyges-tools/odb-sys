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
(`dbSet<dbFoo>` → count + nth-name) — for the **name-addressable** core classes, so the
long tail of accessors is bound *mechanically* instead of by hand:

| Class | Addressed by | Resolver |
| --- | --- | --- |
| `dbBlock` | (singleton) | `chip->getBlock()` |
| `dbInst` · `dbNet` · `dbBTerm` | object name | `block->find{Inst,Net,BTerm}` |
| `dbITerm` | `inst` + `pin` | `inst->findITerm` |
| `dbMaster` · `dbSite` | name (scan libs) | `lib->find{Master,Site}` |
| `dbMTerm` | `master` + `term` | `master->findMTerm` |
| `dbTechLayer` · `dbTechVia` | name | `tech->find{Layer,Via}` |
| `dbVia` | name | `block->findVia` |
| `dbTechNonDefaultRule` | name | `block`/`tech` `->findNonDefaultRule` |
| `dbRow` | name (scan rows) | `block->getRows()` match |
| `dbModule` · `dbGroup` · `dbRegion` | name | `block->find{Module,Group,Region}` |
| `dbMarkerCategory` | name | `block->findMarkerCategory` |
| `dbModInst` · `dbModNet` | hierarchical name | `block->find{ModInst,ModNet}` |
| `dbPowerDomain` · `dbPowerSwitch` · `dbIsolation` · `dbLevelShifter` | name | `block->find*` (UPF power intent) |

Classes with **no name** are addressed by **position** instead (the index-addressing mode —
an arg typed `idx` → `usize`/`std::size_t`):

| Class | Addressed by | Resolver |
| --- | --- | --- |
| `dbObstruction` · `dbFill` | `idx` | i-th of `block->get{Obstructions,Fills}()` |
| `dbSWire` | `net` + `idx` | i-th of `net->getSWires()` |
| `dbWire` | `net` | `net->getWire()` (1:1 with the net) |
| `dbBox` | `idx` | i-th obstruction's `getBBox()` — surfaces box geometry (`xMin`/`getDX`/…) |
| `dbBlockage` · `dbTrackGrid` | `idx` | i-th of `block->get{Blockages,TrackGrids}()` |
| `dbMarker` | `category` + `idx` | i-th of `markerCategory->getMarkers()` — DRC violation (`get_b_box_*`, `get_tech_layer`, `is_waived`, `get_comment`) |
| `dbModBTerm` · `dbModITerm` | `module`/`modinst` + `idx` | i-th of the parent's `getMod{BTerms,ITerms}()` |

**Still uncovered:** `dbRSeg`/`dbCapNode` (parasitics — addressable by id, low instrumentation
value) and the polymorphic ownership of `dbBox` beyond obstructions (inst/master/pin bboxes)
— both fit the same index/owner mechanism when needed.

Run:

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

- **Marshallable types only.** Scalars (`int`/`uint`/`bool`/`float`), strings, the six
  `getString()` enums, nameable relations/iterators, geometry structs `Point`/`Rect` returned by
  value (**expanded into scalar sub-fields** — `Rect getBBox()` → `get_b_box_{x_min,y_min,x_max,
  y_max,dx,dy}`, `Point getOrigin()` → `get_origin_{x,y}`), `std::vector<dbFoo*>` getters (marshal
  like an iterator → count + nth-name), and **out-param getters** `void get*(int& a, int& b)`
  (→ one scalar sub-field per out-ref, e.g. `getLocation` → `get_location_{x,y}`, `getWireCount`
  → `get_wire_count_{wire_cnt,via_cnt}`). `Polygon`, vectors of values/structs, and `optional`s
  are still skipped — those get purpose-built hand bindings.
- **No collisions.** Any generated name that clashes with a hand-written export/`Db` method
  is skipped, so the hand-written surface always wins.

## The write (setter) surface — governance-gated

The generator also emits `set*`/`clear*` setters with fully-marshallable params (scalars,
strings, and the enums via their `dbFoo(const char*)` constructors) → each a `&mut self`
`Db` method returning `Result<()>` that **throws → `Err`** when the addressed object is
missing. These write into a **separate third `#[cxx::bridge]`** and are **gated behind the
`gen-write` Cargo feature — OFF by default**:

```sh
cargo build                      # read-only surface only (no setters linked)
cargo build --features gen-write # + the generated setter surface
```

This is the L2/write governance boundary in code: the broad auto-generated edit surface is
opt-in, never in the default read-only build. Curated, side-effectful edits
(`create_inst`, `connect`, `set_inst_location` = `setLocation` **+ PLACED`) stay
hand-written in the shim; the generator deliberately emits only `set*`/`clear*` (never
`create`/`destroy`/`add`/`remove`/`connect`), so it can't manufacture structural edits.

Files (all `@generated`): `generated_write.{h,cc}`, `generated_write_bridge.rs`,
`../vyges-tools-opendb/src/generated_write_api.rs`. Resolvers are shared with the read
surface via `generated_resolvers.h`.

## Runtime surface — reachable by name (CLI / `vyges mcp`)

Hundreds of typed `Db` methods aren't much use to an agent that can only shell out. The
generator also emits a **runtime registry** (`../vyges-tools-opendb/src/generated_registry.rs`,
exposed as `vyges_opendb::registry`) with `FIELDS`/`WRITE_FIELDS` discovery tables and
`get`/`set` dispatch keyed by `(class, field)` with **string-encoded** keys + values. Three
generic `vyges-opendb` subcommands sit on top, so the whole surface is reachable — and
self-describing to `vyges mcp` via `--describe` — without a subcommand per accessor:

```sh
vyges-opendb fields [--class dbInst] [--writable]        # discover fields (JSON)
vyges-opendb get --input d.odb --class dbNet --field get_sig_type --key VPWR   # -> "POWER"
vyges-opendb get --input d.odb --class dbInst --field get_orient --key _19_    # -> "R0"
vyges-opendb set --input d.odb --output o.odb --class dbInst --field set_orient --key _19_ --value MX
```

Addressing follows the tables above: `--key` is repeatable and positional (`str:` keys and
`idx:` keys in declared order), `--value` likewise for setters. `set` (and `fields
--writable`) require a `--features gen-write` build — the same L2/write gate — and error
clearly otherwise. Unknown fields, bad indices, bad values, and missing objects all surface
as typed errors, never panics.

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
