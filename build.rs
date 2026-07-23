// SPDX-License-Identifier: Apache-2.0
// Builds the standalone libodb (CMake, from the pinned sparse OpenROAD subtree),
// compiles the cxx bridge + C++ shim against it, and emits the link flags.
use std::path::{Path, PathBuf};

fn main() {
    let vendor = PathBuf::from("vendor/OpenROAD");
    if !vendor.join("src/odb/include/odb/db.h").exists() {
        panic!("vendor/OpenROAD missing — run scripts/fetch-odb-src.sh first");
    }

    // 1. standalone libodb.a via CMake.
    let dst = cmake::Config::new(".")
        .define("VYGES_ODB_SMOKE", "OFF")
        .build_target("odb")
        .build();
    let odb_build = dst.join("build");
    println!("cargo:rustc-link-search=native={}", odb_build.display());
    println!("cargo:rustc-link-lib=static=odb");

    // 2. external dependency prefixes (cross-platform).
    let dep = DepPaths::detect();

    // 3. compile the cxx bridge + shim against odb + utl + dep headers.
    let mut b = cxx_build::bridge("src/lib.rs");
    b.file("src/shim.cc")
        .std("c++20")
        .include("src")
        .include(vendor.join("src/odb/include"))
        .include(vendor.join("src/utl/include"));
    for inc in &dep.includes {
        b.include(inc);
    }
    b.compile("vyges_odb_shim");

    // 4. external link flags.
    for dir in &dep.lib_dirs {
        println!("cargo:rustc-link-search=native={}", dir.display());
    }
    for lib in ["spdlog", "fmt", "z"] {
        println!("cargo:rustc-link-lib=dylib={lib}");
    }
    // abseil: link the unversioned libs the db + logger pull in.
    for absl in dep.abseil_libs() {
        println!("cargo:rustc-link-lib=dylib={absl}");
    }
    // C++ runtime.
    if cfg!(target_os = "macos") {
        println!("cargo:rustc-link-lib=dylib=c++");
    } else {
        println!("cargo:rustc-link-lib=dylib=stdc++");
    }

    println!("cargo:rerun-if-changed=src/lib.rs");
    println!("cargo:rerun-if-changed=src/shim.cc");
    println!("cargo:rerun-if-changed=src/shim.h");
    println!("cargo:rerun-if-changed=CMakeLists.txt");
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
            // Homebrew (Apple Silicon default /opt/homebrew, Intel /usr/local).
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
            // Linux: system paths (apt lib*-dev). Cover both multiarch dirs.
            let mut lib_dirs = vec![PathBuf::from("/usr/lib")];
            for d in ["/usr/lib/x86_64-linux-gnu", "/usr/lib/aarch64-linux-gnu"] {
                if Path::new(d).exists() {
                    lib_dirs.push(PathBuf::from(d));
                }
            }
            let abseil_lib_dir = lib_dirs
                .iter()
                .find(|d| d.join("libabsl_base.so").exists() || arch_glob(d))
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

    // Enumerate unversioned libabsl_*.{dylib,so} -> "absl_<name>" link names.
    fn abseil_libs(&self) -> Vec<String> {
        let mut out = Vec::new();
        if let Ok(rd) = std::fs::read_dir(&self.abseil_lib_dir) {
            for e in rd.flatten() {
                let n = e.file_name().to_string_lossy().into_owned();
                if let Some(rest) = n.strip_prefix("libabsl_") {
                    if let Some(stem) = rest.strip_suffix(self.dylib_ext) {
                        // skip versioned (libabsl_base.2601.0.0.dylib)
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
