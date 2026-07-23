# vyges-odb-sys

Low-level build + (future) FFI bindings for **OpenROAD's OpenDB (`libodb`)** — built
**standalone** from a pinned, sparse OpenROAD subtree, with **no Tcl, no SWIG, and no
OpenROAD engines**. The safe Rust API lives in the sibling crate `vyges-odb`.

> Part of Vyges Loom. `libodb` is the in-memory design database every OpenROAD engine reads
> and writes; binding it lets Loom do ECO/audit/extraction natively over `.odb` — and it
> carries the LEF/DEF/GDS/CDL I/O with it.

## What this repo produces

A single static `libodb.a` (the OpenDB core + the `utl` logger), buildable on
**linux/x86_64, linux/arm64, and macOS/Apple Silicon**. Verified: it reads a real placed +
routed `.odb`, walks the model, and writes it back — linking none of the engines.

## How it works — pinned + sparse, no full mirror

- **`openroad-pin.yaml`** pins the OpenROAD commit (matches the `vyges-openroad` distribution).
- **`scripts/fetch-odb-src.sh`** does a blobless, cone-sparse checkout of only `src/odb` +
  `src/utl` + `cmake` at that SHA — **~24 MB**, not the ~1.8 GB full tree.
- **`CMakeLists.txt`** compiles the db core + utl into `libodb.a` (C++20), linking Boost
  (headers), zlib, spdlog, fmt, abseil. Tcl/SWIG/or-tools/engines are deliberately excluded.
- **`.github/workflows/build-libodb.yml`** builds + smoke-tests + publishes `libodb.a`
  per-arch, on demand (`workflow_dispatch`).

## Build locally

```sh
scripts/fetch-odb-src.sh                 # sparse-checkout the pinned subtree -> vendor/OpenROAD
cmake -S . -B build -DVYGES_ODB_SMOKE=ON
cmake --build build -j
./build/odb_smoke test/fixtures/counter.odb /tmp/rt.odb   # -> block=counter insts=229 ...
```

Deps: a C++20 compiler + `cmake boost zlib abseil spdlog fmt` (apt `lib*-dev`, or
`brew install`).

## Scope

- **v0 (now):** the db core — the in-memory model + `.odb` read/write (dbDatabase, the ECO
  journal, wire codec, RC, connectivity). Enough for the odb applier + audit steps.
- **v1 (next):** add the LEF/DEF/GDS/CDL I/O sub-libs (`defin/lefin/gdsin/...`).
- **bindings (next):** the `cxx` bridge (this crate's `src/`) + the safe `vyges-odb` API.

## Notes

- **C++20 required** — odb headers use `operator<=>` and `<numbers>`.
- `utl` bundles a self-contained Prometheus metrics server (Boost.Asio + std only) that
  `Logger.cpp` constructs unconditionally, so it is compiled in; it pulls no external
  prometheus-cpp.
- OpenROAD is BSD-3-Clause; this repo (our CMake, scripts, workflow, bindings) is Apache-2.0.
