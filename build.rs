// SPDX-License-Identifier: Apache-2.0
// Gets libodb one of two ways, then compiles the cxx bridge + shim against it:
//
//   1. PREBUILT (light): set VYGES_ODB_PREBUILT_DIR=<dir> containing `lib/libodb.a` and
//      `include/{odb,utl}/...` (the layout the build-libodb workflow publishes). No cmake,
//      no OpenROAD fetch, no C++ compile of libodb — just links the published archive.
//   2. FROM SOURCE (default): builds a standalone libodb via CMake from the pinned sparse
//      OpenROAD subtree (run scripts/fetch-odb-src.sh first).
use std::path::{Path, PathBuf};

fn main() {
    let dep = DepPaths::detect();

    // Where libodb.a lives + the include dirs the shim compiles against.
    let (lib_dir, mut includes): (PathBuf, Vec<PathBuf>) =
        if let Ok(prebuilt) = std::env::var("VYGES_ODB_PREBUILT_DIR") {
            let p = PathBuf::from(&prebuilt);
            let lib = p.join("lib");
            if !lib.join("libodb.a").exists() {
                panic!("VYGES_ODB_PREBUILT_DIR set but {}/libodb.a not found", lib.display());
            }
            println!("cargo:warning=vyges-odb-sys: linking prebuilt libodb from {prebuilt}");
            (lib, vec![p.join("include")])
        } else {
            // Build from source. Use the local sparse checkout if present (dev), else auto-fetch
            // the pinned subtree into OUT_DIR (self-contained dist build-from-source).
            let src = source_tree();
            let dst = cmake::Config::new(".")
                .define("VYGES_ODB_SMOKE", "OFF")
                .define("OPENROAD_SRC", &src)
                .build_target("odb")
                .build();
            (
                dst.join("build"),
                vec![
                    src.join("src/odb/include"),
                    src.join("src/utl/include"),
                ],
            )
        };

    // 1. link libodb.
    println!("cargo:rustc-link-search=native={}", lib_dir.display());
    println!("cargo:rustc-link-lib=static=odb");

    // 2. compile the cxx bridge + shim against odb/utl + dep headers.
    includes.extend(dep.includes.iter().cloned());
    let mut b = cxx_build::bridge("src/lib.rs");
    b.file("src/shim.cc").std("c++20").include("src");
    for inc in &includes {
        b.include(inc);
    }
    b.compile("vyges_odb_shim");

    // 3. external link flags.
    for dir in &dep.lib_dirs {
        println!("cargo:rustc-link-search=native={}", dir.display());
    }
    for lib in ["spdlog", "fmt", "z"] {
        println!("cargo:rustc-link-lib=dylib={lib}");
    }
    for absl in dep.abseil_libs() {
        println!("cargo:rustc-link-lib=dylib={absl}");
    }
    if cfg!(target_os = "macos") {
        println!("cargo:rustc-link-lib=dylib=c++");
    } else {
        println!("cargo:rustc-link-lib=dylib=stdc++");
    }

    println!("cargo:rerun-if-env-changed=VYGES_ODB_PREBUILT_DIR");
    println!("cargo:rerun-if-changed=src/lib.rs");
    println!("cargo:rerun-if-changed=src/shim.cc");
    println!("cargo:rerun-if-changed=src/shim.h");
    println!("cargo:rerun-if-changed=CMakeLists.txt");
}

/// Local `vendor/OpenROAD` if present (dev); otherwise auto-fetch the pinned sparse subtree
/// into `OUT_DIR/OpenROAD` (self-contained dist build-from-source).
fn source_tree() -> PathBuf {
    let vendor = PathBuf::from("vendor/OpenROAD");
    if vendor.join("src/odb/include/odb/db.h").exists() {
        return vendor;
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
    println!("cargo:warning=vyges-odb-sys: fetching pinned OpenROAD subtree @ {sha}");
    let run = |args: &[&str], cwd: Option<&Path>| {
        let mut c = std::process::Command::new("git");
        c.args(args);
        if let Some(d) = cwd {
            c.current_dir(d);
        }
        let st = c.status().expect("git not found");
        if !st.success() {
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

struct DepPaths {
    includes: Vec<PathBuf>,
    lib_dirs: Vec<PathBuf>,
    abseil_lib_dir: PathBuf,
    dylib_ext: &'static str,
}

impl DepPaths {
    fn detect() -> Self {
        if cfg!(target_os = "macos") {
            let brew = if Path::new("/opt/homebrew").exists() {
                PathBuf::from("/opt/homebrew")
            } else {
                PathBuf::from("/usr/local")
            };
            DepPaths {
                includes: vec![brew.join("include")],
                lib_dirs: vec![brew.join("lib")],
                abseil_lib_dir: brew.join("opt/abseil/lib"),
                dylib_ext: ".dylib",
            }
        } else {
            let mut lib_dirs = vec![PathBuf::from("/usr/lib")];
            for d in ["/usr/lib/x86_64-linux-gnu", "/usr/lib/aarch64-linux-gnu"] {
                if Path::new(d).exists() {
                    lib_dirs.push(PathBuf::from(d));
                }
            }
            let abseil_lib_dir = lib_dirs
                .iter()
                .find(|d| arch_glob(d))
                .cloned()
                .unwrap_or_else(|| PathBuf::from("/usr/lib"));
            DepPaths {
                includes: vec![PathBuf::from("/usr/include")],
                lib_dirs,
                abseil_lib_dir,
                dylib_ext: ".so",
            }
        }
    }

    fn abseil_libs(&self) -> Vec<String> {
        let mut out = Vec::new();
        if let Ok(rd) = std::fs::read_dir(&self.abseil_lib_dir) {
            for e in rd.flatten() {
                let n = e.file_name().to_string_lossy().into_owned();
                if let Some(rest) = n.strip_prefix("libabsl_") {
                    if let Some(stem) = rest.strip_suffix(self.dylib_ext) {
                        if !stem.chars().any(|c| c.is_ascii_digit()) {
                            out.push(format!("absl_{stem}"));
                        }
                    }
                }
            }
        }
        out.sort();
        out.dedup();
        out
    }
}

fn arch_glob(dir: &Path) -> bool {
    std::fs::read_dir(dir)
        .map(|rd| {
            rd.flatten()
                .any(|e| e.file_name().to_string_lossy().starts_with("libabsl_"))
        })
        .unwrap_or(false)
}
