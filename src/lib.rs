// SPDX-License-Identifier: Apache-2.0
//! `vyges-odb-sys` — low-level FFI to OpenROAD's OpenDB (`libodb`).
//!
//! v0 read/round-trip surface over a standalone libodb (no tcl/swig/engines), proven on
//! linux/x86_64, linux/arm64, and macOS/Apple Silicon. The next increment adds the write
//! path (create inst/net, connect iterms, place) for the ECO applier; the safe, ergonomic
//! wrappers live in the sibling crate `vyges-odb`.

#[cxx::bridge]
mod ffi {
    unsafe extern "C++" {
        include!("shim.h");

        /// Opaque handle owning a `dbDatabase` + its `utl::Logger`.
        type OdbDb;

        /// Read a `.odb` file into a fresh database.
        fn open_db(path: &str) -> Result<UniquePtr<OdbDb>>;
        /// Name of the top block (empty if none).
        fn block_name(db: &OdbDb) -> String;
        fn num_insts(db: &OdbDb) -> usize;
        fn num_nets(db: &OdbDb) -> usize;
        fn num_bterms(db: &OdbDb) -> usize;
        /// Serialize the database back to a `.odb` file.
        fn write_db(db: &OdbDb, path: &str) -> Result<()>;
    }
}

pub use ffi::{block_name, num_bterms, num_insts, num_nets, open_db, write_db, OdbDb};
