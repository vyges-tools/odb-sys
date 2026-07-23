// SPDX-License-Identifier: Apache-2.0
// Builds the standalone libodb via CMake when the pinned OpenROAD subtree is present
// (vendor/OpenROAD, populated by scripts/fetch-odb-src.sh). Until the cxx bridge lands,
// this just produces + links libodb.a; there are no FFI calls yet.
use std::path::Path;

fn main() {
    let vendor = Path::new("vendor/OpenROAD/src/odb/include/odb/db.h");
    if !vendor.exists() {
        println!(
            "cargo:warning=vendor/OpenROAD not found — run scripts/fetch-odb-src.sh to build libodb. Skipping native build."
        );
        return;
    }

    let dst = cmake::Config::new(".")
        .define("VYGES_ODB_SMOKE", "OFF")
        .build_target("odb")
        .build();

    // libodb.a lands under the CMake build dir.
    println!("cargo:rustc-link-search=native={}/build", dst.display());
    println!("cargo:rustc-link-lib=static=odb");
    // External deps (spdlog/fmt/absl/z) + the cxx bridge glue are wired with the bindings.

    println!("cargo:rerun-if-changed=CMakeLists.txt");
    println!("cargo:rerun-if-changed=openroad-pin.yaml");
}
