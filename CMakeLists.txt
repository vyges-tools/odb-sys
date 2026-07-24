# SPDX-License-Identifier: Apache-2.0
# vyges-opendb-lib — standalone libodb (OpenDB core + LEF/DEF I/O) built from a pinned, sparse
# OpenROAD subtree. No SWIG, no Tcl, no OpenROAD engines. C++20. BISON at build time (LEF/DEF).
#
# The source layout is pinned (see openroad-pin.yaml); scripts/fetch-odb-src.sh
# sparse-checks-out src/odb + src/utl + cmake into vendor/OpenROAD.
cmake_minimum_required(VERSION 3.20)
project(vyges_odb LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)              # odb headers use operator<=> and <numbers>
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON) # so the .a can go into a Rust cdylib later
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

set(OPENROAD_SRC "${CMAKE_SOURCE_DIR}/vendor/OpenROAD"
    CACHE PATH "Root of the sparse OpenROAD subtree (src/odb, src/utl, cmake)")
if(NOT EXISTS "${OPENROAD_SRC}/src/odb/include/odb/db.h")
  message(FATAL_ERROR
    "OpenROAD odb source not found at ${OPENROAD_SRC}.\n"
    "Run: scripts/fetch-odb-src.sh   (sparse-checkout of the pinned SHA)")
endif()

# ---- external deps -----------------------------------------------------------
# Boost (headers) + zlib come from the system: stable, ABI-safe, always present.
# fmt / spdlog / abseil are built FROM SOURCE at PINNED versions and static-linked, so the
# binary does not depend on the host's (often older, incompatible) system versions. This is
# what makes libodb portable — the version floor is glibc, not the distro's C++ libs.
# (Versions mirrored in openroad-pin.yaml for the provenance manifest.)
find_package(Boost REQUIRED COMPONENTS iostreams)  # iostreams (compiled) for gzipped LEF/DEF
find_package(ZLIB  REQUIRED)

include(FetchContent)
set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)      # build the fetched deps static
set(ABSL_PROPAGATE_CXX_STD ON  CACHE BOOL "" FORCE)
set(ABSL_ENABLE_INSTALL    OFF CACHE BOOL "" FORCE)
set(FMT_INSTALL            OFF CACHE BOOL "" FORCE)
set(SPDLOG_INSTALL         OFF CACHE BOOL "" FORCE)
set(SPDLOG_FMT_EXTERNAL    ON  CACHE BOOL "" FORCE)  # use our pinned fmt, not spdlog's bundled copy

FetchContent_Declare(fmt    GIT_REPOSITORY https://github.com/fmtlib/fmt.git        GIT_TAG 11.0.2     GIT_SHALLOW TRUE)
FetchContent_Declare(absl   GIT_REPOSITORY https://github.com/abseil/abseil-cpp.git GIT_TAG 20250127.0 GIT_SHALLOW TRUE)
FetchContent_Declare(spdlog GIT_REPOSITORY https://github.com/gabime/spdlog.git     GIT_TAG v1.15.3    GIT_SHALLOW TRUE)
FetchContent_MakeAvailable(fmt absl)   # fmt before spdlog (SPDLOG_FMT_EXTERNAL)
FetchContent_MakeAvailable(spdlog)

# ---- sources (pinned layout) -------------------------------------------------
# v1 scope: the db core + utl (logger) + LEF/DEF I/O (lefin/lefout/defin/defout and the si2
# LEF/DEF parser libs). We do NOT use OpenROAD's per-sub-lib CMakeLists via add_subdirectory —
# they call OpenROAD-only macros (swig_lib, messages) and assume the OpenROAD project layout — so
# we glob the sources into one static lib ourselves, as we already do for db+utl.
# Still deliberately excluded: Tcl, SWIG, or-tools, engines, GDS/CDL/3dblox (add later if needed).
set(ODB "${OPENROAD_SRC}/src/odb/src")

# The si2 LEF/DEF parsers are BISON-generated (their lexers are hand-written — no flex).
find_package(BISON REQUIRED)
bison_target(lef_parser "${ODB}/lef/lef/lef.y" "${CMAKE_CURRENT_BINARY_DIR}/lef_parser.cpp"
             COMPILE_FLAGS "--name-prefix=lefyy")
bison_target(def_parser "${ODB}/def/def/def.y" "${CMAKE_CURRENT_BINARY_DIR}/def_parser.cpp"
             COMPILE_FLAGS "--name-prefix=defyy")

file(GLOB DB_SRC  CONFIGURE_DEPENDS "${ODB}/db/*.cpp")
file(GLOB UTL_SRC CONFIGURE_DEPENDS
     "${OPENROAD_SRC}/src/utl/src/*.cpp"
     "${OPENROAD_SRC}/src/utl/src/prometheus/*.cpp")  # Logger.cpp references it unconditionally
# Drop the Tcl-glue files (need tcl.h; unused in a tcl-free libodb).
list(FILTER UTL_SRC EXCLUDE REGEX "/(MakeLogger|decode)\\.cpp$")
# LEF/DEF readers/writers + the parser libs. For lef/def, ONLY the lef/ + lefzlib/ (resp. def/ +
# defzlib/) subdirs are part of the library; the sibling subdirs (clef, lefrw, TEST, doc, …) are
# standalone tools we don't build.
file(GLOB IO_SRC CONFIGURE_DEPENDS
     "${ODB}/defin/*.cpp" "${ODB}/defout/*.cpp"
     "${ODB}/lefin/*.cpp" "${ODB}/lefout/*.cpp"
     "${ODB}/lef/lef/*.cpp" "${ODB}/lef/lefzlib/*.cpp"
     "${ODB}/def/def/*.cpp" "${ODB}/def/defzlib/*.cpp")

add_library(odb STATIC ${DB_SRC} ${UTL_SRC} ${IO_SRC}
            ${BISON_lef_parser_OUTPUT_SOURCE} ${BISON_def_parser_OUTPUT_SOURCE})
target_include_directories(odb PUBLIC
  "${OPENROAD_SRC}/src/odb/include"
  "${OPENROAD_SRC}/src/utl/include"
  "${ODB}/lef/lef" "${ODB}/lef/lefzlib"
  "${ODB}/def/def" "${ODB}/def/defzlib"
  "${CMAKE_CURRENT_BINARY_DIR}")            # generated lef_parser.hpp / def_parser.hpp
# Link fmt/spdlog/abseil (our pinned, source-built) BEFORE Boost so their headers win: a system
# Boost dir (e.g. Homebrew's /opt/homebrew/include) also bundles a *different* fmt/abseil, and if
# it came first the odb objects would reference those symbols and fail to link our pins.
target_link_libraries(odb PUBLIC
  fmt::fmt spdlog::spdlog
  absl::synchronization absl::strings absl::hash absl::flat_hash_map absl::base
  Boost::headers Boost::iostreams ZLIB::ZLIB)
# The si2 parser C code trips -Wclass-memaccess on GCC (not an error — no -Werror); silence it.
target_compile_options(odb PRIVATE $<$<CXX_COMPILER_ID:GNU>:-Wno-class-memaccess>)

# ---- smoke test: round-trip a real .odb --------------------------------------
option(VYGES_ODB_SMOKE "Build the .odb round-trip smoke test" OFF)
if(VYGES_ODB_SMOKE)
  add_executable(odb_smoke test/probe.cpp)
  target_link_libraries(odb_smoke PRIVATE odb)
endif()

# ---- conformance gate: OpenROAD's OWN odb C++ GTests, run against OUR libodb --
# Proves our standalone/no-Tcl build is behaviorally OpenROAD, and catches drift on a pin bump.
# Only the tests needing nothing beyond libodb build here: OpenROAD's tst/ framework pulls STA +
# Tcl + Bazel-runfiles, so the tst/helper-based, gdsin, 3dblox, and STA tests are out of scope.
# Coverage note (2026-07-24): only TestGeom builds standalone. The other 25 odb gtests pull
# OpenROAD's `tst::Fixture` framework (tst/fixture.h, {sky130,nangate45}_fixture.h,
# IntegratedFixture.h), which #includes db_sta/dbSta + sta/Liberty + tcl.h + Bazel runfiles — none
# of which exist in a Tcl/SWIG/engine-free libodb. Expanding this gate toward parity = writing a
# minimal standalone `tst` shim (db + Logger + LEF load via our lefin, no STA/Tcl); see
# docs (opendb-conformance-gate.md) in vyges-tools-internal.
option(VYGES_ODB_GTEST "Build OpenROAD's standalone odb C++ GTests against our libodb" OFF)
if(VYGES_ODB_GTEST)
  FetchContent_Declare(googletest GIT_REPOSITORY https://github.com/google/googletest.git
                       GIT_TAG v1.15.2 GIT_SHALLOW TRUE)
  set(gtest_force_shared_crt ON CACHE BOOL "" FORCE)
  FetchContent_MakeAvailable(googletest)
  set(ODB_TEST "${OPENROAD_SRC}/src/odb/test/cpp")
  add_executable(odb_gtest "${ODB_TEST}/TestGeom.cpp")   # Point/Rect/Oct/Polygon geometry
  target_link_libraries(odb_gtest PRIVATE odb GTest::gtest GTest::gtest_main GTest::gmock)
endif()
