# SPDX-License-Identifier: Apache-2.0
# vyges-odb-sys — standalone libodb (OpenDB core) built from a pinned, sparse
# OpenROAD subtree. No SWIG, no Tcl, no OpenROAD engines. C++20.
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
find_package(Boost REQUIRED)   # header-only for the db core
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
# v0 scope: the db core (in-memory model + .odb read/write) + utl (logger).
# The db core carries dbDatabase read/write, the ECO journal, wire codec, RC, etc.
# LEF/DEF/GDS I/O sub-libs (defin/lefin/gdsin/...) are a follow-up (ODB_IO below).
file(GLOB DB_SRC  CONFIGURE_DEPENDS "${OPENROAD_SRC}/src/odb/src/db/*.cpp")
file(GLOB UTL_SRC CONFIGURE_DEPENDS
     "${OPENROAD_SRC}/src/utl/src/*.cpp"
     "${OPENROAD_SRC}/src/utl/src/prometheus/*.cpp")  # Logger.cpp references it unconditionally
# Drop the Tcl-glue files (need tcl.h; unused in a tcl-free libodb). They only compiled on
# macOS by accident (system tcl.h present) and were dropped as dead code at link time.
list(FILTER UTL_SRC EXCLUDE REGEX "/(MakeLogger|decode)\\.cpp$")

add_library(odb STATIC ${DB_SRC} ${UTL_SRC})
target_include_directories(odb PUBLIC
  "${OPENROAD_SRC}/src/odb/include"
  "${OPENROAD_SRC}/src/utl/include"
  ${Boost_INCLUDE_DIRS})
target_link_libraries(odb PUBLIC
  ZLIB::ZLIB spdlog::spdlog fmt::fmt
  absl::synchronization absl::strings absl::hash absl::flat_hash_map absl::base)
# Deliberately NOT linked: Tcl (proven droppable), SWIG bindings, or-tools, engines.

# ---- smoke test: round-trip a real .odb --------------------------------------
option(VYGES_ODB_SMOKE "Build the .odb round-trip smoke test" OFF)
if(VYGES_ODB_SMOKE)
  add_executable(odb_smoke test/probe.cpp)
  target_link_libraries(odb_smoke PRIVATE odb)
endif()
