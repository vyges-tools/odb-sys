// SPDX-License-Identifier: Apache-2.0
// Builds a self-contained libodb and links it into the cxx bridge.
//
//   PREBUILT (light): VYGES_ODB_PREBUILT_DIR=<dir> with lib/*.a (libodb + fmt/spdlog/abseil,
//     static) and include/ — links the published bundle; no cmake/fetch/compile.
//   FROM SOURCE (default): CMake builds libodb from the pinned sparse OpenROAD subtree AND
//     fmt/spdlog/abseil from source at pinned versions (static, self-contained). Deps are
//     never taken from the (often older) system, so the binary is portable — the floor is
//     glibc, not the distro's C++ libs.
//
// libodb is unix-only; on non-unix targets the crate is an empty stub (see lib.rs).
use std::path::{Path, PathBuf};

fn main() {
    if std::env::var_os("CARGO_CFG_UNIX").is_none() {
        println!("cargo:warning=vyges-opendb-lib: non-unix target, building empty stub (libodb unavailable)");
        return;
    }

    // (static archives to link, include dirs the shim compiles against)
    let (archives, mut includes): (Vec<PathBuf>, Vec<PathBuf>) =
        if let Ok(prebuilt) = std::env::var("VYGES_ODB_PREBUILT_DIR") {
            let p = PathBuf::from(&prebuilt);
            if !p.join("lib/libodb.a").exists() {
                panic!("VYGES_ODB_PREBUILT_DIR set but {}/lib/libodb.a not found", p.display());
            }
            println!("cargo:warning=vyges-opendb-lib: linking prebuilt libodb from {prebuilt}");
            (ordered_archives(collect_archives(&p.join("lib"))), vec![p.join("include")])
        } else {
            let src = source_tree();
            let dst = cmake::Config::new(".")
                .define("VYGES_ODB_SMOKE", "OFF")
                .define("OPENROAD_SRC", &src)
                .build_target("odb")
                .build();
            let build = dst.join("build");
            let mut libs = collect_archives(&build.join("_deps")); // fmt/spdlog/abseil (static)
            libs.insert(0, build.join("libodb.a"));
            let deps = build.join("_deps");
            (
                ordered_archives(libs),
                vec![
                    src.join("src/odb/include"),
                    src.join("src/utl/include"),
                    deps.join("fmt-src/include"),
                    deps.join("spdlog-src/include"),
                    deps.join("absl-src"),
                ],
            )
        };

    // Boost headers are header-only + system (mac: brew prefix; linux: default /usr/include).
    if cfg!(target_os = "macos") {
        let brew = if Path::new("/opt/homebrew").exists() { "/opt/homebrew" } else { "/usr/local" };
        includes.push(PathBuf::from(format!("{brew}/include")));
    }

    // Compile the cxx bridge + shim against odb/utl + the pinned deps' headers.
    let mut b = cxx_build::bridge("src/lib.rs");
    b.file("src/shim.cc").std("c++20").include("src");
    for inc in &includes {
        b.include(inc);
    }
    b.compile("vyges_opendb_shim");

    // Link libodb + the static deps. Use rustc-link-lib (NOT rustc-link-arg): a dependency
    // build script's link-args do not propagate to the final binary, but link-lib/link-search
    // do. abseil's archives are mutually circular, so `+whole-archive` includes them fully —
    // this avoids a linker `--start-group` (which would need the non-propagating link-arg).
    // libstdc++ compatibility comes from building on an old glibc base, not `-static-libstdc++`
    // (also a non-propagating link-arg).
    for a in &archives {
        let dir = a.parent().unwrap();
        println!("cargo:rustc-link-search=native={}", dir.display());
        let name = a
            .file_stem()
            .and_then(|s| s.to_str())
            .and_then(|s| s.strip_prefix("lib"))
            .unwrap_or("");
        if name == "odb" {
            println!("cargo:rustc-link-lib=static=odb");
        } else {
            // fmt / spdlog / absl_* — whole-archive so forward + circular refs all resolve.
            println!("cargo:rustc-link-lib=static:+whole-archive={name}");
        }
    }
    println!("cargo:rustc-link-lib=dylib=z");
    // libodb v1 (LEF/DEF I/O) pulls boost::iostreams (gzipped LEF/DEF). STATIC-link it so the
    // binary stays self-contained (glibc/libstdc++/zlib only) — no libboost runtime dep. On macOS
    // Homebrew's lib dir isn't on the default search path, so add it.
    if cfg!(target_os = "macos") {
        let brew = if Path::new("/opt/homebrew").exists() { "/opt/homebrew" } else { "/usr/local" };
        println!("cargo:rustc-link-search=native={brew}/lib");
    } else if let Ok(arch) = std::env::var("CARGO_CFG_TARGET_ARCH") {
        // Debian/Ubuntu multiarch dir where libboost_iostreams.a lives — not a default rustc
        // *static* search path (e.g. /usr/lib/x86_64-linux-gnu, /usr/lib/aarch64-linux-gnu).
        println!("cargo:rustc-link-search=native=/usr/lib/{arch}-linux-gnu");
    }
    println!("cargo:rustc-link-lib=static=boost_iostreams");
    if cfg!(target_os = "macos") {
        println!("cargo:rustc-link-lib=dylib=c++");
    } else {
        // Linux boost_iostreams is built with the bzip2 filter → it references libbz2. (macOS
        // Homebrew's is not, so it needs no bz2.) bz2 stays dynamic — a universal system lib.
        println!("cargo:rustc-link-lib=dylib=bz2");
        println!("cargo:rustc-link-lib=dylib=stdc++");
    }

    println!("cargo:rerun-if-env-changed=VYGES_ODB_PREBUILT_DIR");
    println!("cargo:rerun-if-changed=src/lib.rs");
    println!("cargo:rerun-if-changed=src/shim.cc");
    println!("cargo:rerun-if-changed=src/shim.h");
    println!("cargo:rerun-if-changed=CMakeLists.txt");
}

/// Recursively collect `*.a` under `dir`.
fn collect_archives(dir: &Path) -> Vec<PathBuf> {
    fn walk(d: &Path, out: &mut Vec<PathBuf>) {
        if let Ok(rd) = std::fs::read_dir(d) {
            for e in rd.flatten() {
                let p = e.path();
                if p.is_dir() {
                    walk(&p, out);
                } else if p.extension().is_some_and(|x| x == "a") {
                    out.push(p);
                }
            }
        }
    }
    let mut out = Vec::new();
    walk(dir, &mut out);
    out
}

/// libodb first (it references the deps), then the rest sorted — a stable order for the group.
fn ordered_archives(mut a: Vec<PathBuf>) -> Vec<PathBuf> {
    a.sort_by_key(|p| {
        let is_odb = p.file_name().map_or(false, |n| n == "libodb.a");
        (!is_odb, p.clone())
    });
    a
}

/// Local `vendor/OpenROAD` if present (dev); otherwise auto-fetch the pinned sparse subtree
/// into `OUT_DIR/OpenROAD` (self-contained dist build-from-source).
fn source_tree() -> PathBuf {
    let vendor = PathBuf::from("vendor/OpenROAD");
    if vendor.join("src/odb/include/odb/db.h").exists() {
        return std::fs::canonicalize(&vendor).expect("canonicalize vendor/OpenROAD");
    }
    let out = PathBuf::from(std::env::var("OUT_DIR").unwrap());
    let dest = out.join("OpenROAD");
    if !dest.join("src/odb/include/odb/db.h").exists() {
        fetch_openroad(&dest);
    }
    dest
}

/// Blobless, cone-sparse checkout of only src/odb + src/utl + cmake at the pinned commit.
fn fetch_openroad(dest: &Path) {
    let pin = std::fs::read_to_string("openroad-pin.yaml").expect("read openroad-pin.yaml");
    let sha = pin
        .lines()
        .find_map(|l| l.trim().strip_prefix("commit:").map(|s| s.split('#').next().unwrap().trim().to_string()))
        .expect("commit: in openroad-pin.yaml");
    let src = "https://github.com/The-OpenROAD-Project/OpenROAD.git";
    println!("cargo:warning=vyges-opendb-lib: fetching pinned OpenROAD subtree @ {sha}");
    let run = |args: &[&str], cwd: Option<&Path>| {
        let mut c = std::process::Command::new("git");
        c.args(args);
        if let Some(d) = cwd {
            c.current_dir(d);
        }
        if !c.status().expect("git not found").success() {
            panic!("git {args:?} failed");
        }
    };
    if !dest.join(".git").exists() {
        std::fs::create_dir_all(dest).unwrap();
        run(&["clone", "--quiet", "--filter=blob:none", "--no-checkout", src, dest.to_str().unwrap()], None);
    }
    run(&["sparse-checkout", "set", "--cone", "src/odb", "src/utl", "cmake"], Some(dest));
    run(&["checkout", "--quiet", &sha], Some(dest));
}
