// SPDX-License-Identifier: Apache-2.0
//! `vyges-odb-sys` — low-level FFI to OpenROAD's OpenDB (`libodb`).
//!
//! Status: **scaffold**. The standalone `libodb` build (CMake + pinned sparse OpenROAD
//! subtree) is in place and proven on linux/x86_64, linux/arm64, and macOS/Apple Silicon.
//! The `cxx` bridge lands next — a thin C++ shim over `dbDatabase` / `dbBlock` / `dbInst` /
//! `dbNet` / `dbITerm` (open, read_db, write_db, iterate, place, connect). The safe API then
//! lives in the sibling crate `vyges-odb`.
//!
//! Planned bridge surface (read + single-buffer-ECO write paths):
//! - `dbDatabase::create/setLogger/read/write`, `dbChip::getBlock`
//! - `dbBlock::getInsts/getNets/getBTerms/findInst/findNet`
//! - `dbInst::create/getITerms/getLocation/setLocation/findITerm`
//! - `dbNet::create`, `dbITerm::connect/getNet/isInputSignal/isOutputSignal`, `dbMaster`
