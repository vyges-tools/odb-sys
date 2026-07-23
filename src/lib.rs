// SPDX-License-Identifier: Apache-2.0
//! `vyges-odb-sys` — low-level FFI to OpenROAD's OpenDB (`libodb`).
//!
//! Read + write-path surface over a standalone libodb (no tcl/swig/engines), proven on
//! linux/x86_64, linux/arm64, and macOS/Apple Silicon. Objects are addressed by name so
//! no raw odb pointers cross the FFI boundary. The write primitives are the building
//! blocks for the ECO applier (`InsertECOBuffers`). The safe, ergonomic wrappers live in
//! the sibling crate `vyges-odb`.

#[cxx::bridge]
mod ffi {
    unsafe extern "C++" {
        include!("shim.h");

        /// Opaque handle owning a `dbDatabase` + its `utl::Logger`.
        type OdbDb;

        // open / read / write
        fn open_db(path: &str) -> Result<UniquePtr<OdbDb>>;
        fn write_db(db: &OdbDb, path: &str) -> Result<()>;

        // read / inspect
        fn block_name(db: &OdbDb) -> String;
        fn num_insts(db: &OdbDb) -> usize;
        fn num_nets(db: &OdbDb) -> usize;
        fn num_bterms(db: &OdbDb) -> usize;
        fn nth_inst_name(db: &OdbDb, i: usize) -> String;
        fn first_master_name(db: &OdbDb) -> String;
        fn find_master(db: &OdbDb, substr: &str) -> String;
        fn input_pin(db: &OdbDb, inst: &str) -> String;
        fn output_pin(db: &OdbDb, inst: &str) -> String;
        fn net_of(db: &OdbDb, inst: &str, pin: &str) -> String;

        // write / ECO primitives
        fn create_net(db: &OdbDb, name: &str) -> Result<()>;
        fn create_inst(db: &OdbDb, master: &str, name: &str) -> Result<()>;
        fn set_inst_location(db: &OdbDb, inst: &str, x: i32, y: i32) -> Result<()>;
        fn connect(db: &OdbDb, inst: &str, pin: &str, net: &str) -> Result<()>;
        fn disconnect(db: &OdbDb, inst: &str, pin: &str) -> Result<()>;
    }
}

pub use ffi::{
    block_name, connect, create_inst, create_net, disconnect, find_master, first_master_name,
    input_pin, net_of, nth_inst_name, num_bterms, num_insts, num_nets, open_db, output_pin,
    set_inst_location, write_db, OdbDb,
};
