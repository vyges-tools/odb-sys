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

# ---- external deps (all cross-platform: linux x86_64/arm64 + Apple Silicon) --
find_package(Boost  REQUIRED)   # header-only for the db core
find_package(ZLIB   REQUIRED)
find_package(absl   REQUIRED)   # abseil
find_package(spdlog REQUIRED)
find_package(fmt    REQUIRED)

# ---- sources (pinned layout) -------------------------------------------------
# v0 scope: the db core (in-memory model + .odb read/write) + utl (logger).
# The db core carries dbDatabase read/write, the ECO journal, wire codec, RC, etc.
# LEF/DEF/GDS I/O sub-libs (defin/lefin/gdsin/...) are a follow-up (ODB_IO below).
file(GLOB DB_SRC  CONFIGURE_DEPENDS "${OPENROAD_SRC}/src/odb/src/db/*.cpp")
file(GLOB UTL_SRC CONFIGURE_DEPENDS
     "${OPENROAD_SRC}/src/utl/src/*.cpp"
     "${OPENROAD_SRC}/src/utl/src/prometheus/*.cpp")  # Logger.cpp references it unconditionally

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
