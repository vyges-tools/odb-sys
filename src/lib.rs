// SPDX-License-Identifier: Apache-2.0
//! `vyges-opendb-lib` — low-level FFI to OpenROAD's OpenDB (`libodb`).
//!
//! Read + write-path surface over a standalone libodb (no tcl/swig/engines), proven on
//! linux/x86_64, linux/arm64, and macOS/Apple Silicon. Objects are addressed by name so
//! no raw odb pointers cross the FFI boundary. The write primitives are the building
//! blocks for the ECO applier (`InsertECOBuffers`). The safe, ergonomic wrappers live in
//! the sibling crate `vyges-opendb`.

// Unix-only: libodb is not built on non-unix targets (see build.rs). On Windows this crate
// compiles to an empty stub so a `--features odb` build still succeeds across the dist matrix.
#[cfg(unix)]
#[cxx::bridge]
mod ffi {
    unsafe extern "C++" {
        include!("shim.h");

        /// Opaque handle owning a `dbDatabase` + its `utl::Logger`.
        type OdbDb;

        // open / read / write
        fn open_db(path: &str) -> Result<UniquePtr<OdbDb>>;
        fn write_db(db: &OdbDb, path: &str) -> Result<()>;
        fn write_def(db: &OdbDb, path: &str) -> Result<()>;
        fn read_def(db: &OdbDb, def_path: &str, mode: &str) -> Result<()>;

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
        fn inst_master(db: &OdbDb, inst: &str) -> String;
        fn num_iterms(db: &OdbDb, inst: &str) -> usize;
        fn nth_iterm_name(db: &OdbDb, inst: &str, i: usize) -> String;
        fn net_of(db: &OdbDb, inst: &str, pin: &str) -> String;
        fn inst_x(db: &OdbDb, inst: &str) -> i32;
        fn inst_y(db: &OdbDb, inst: &str) -> i32;
        fn nth_bterm_name(db: &OdbDb, i: usize) -> String;
        fn bterm_net(db: &OdbDb, bterm: &str) -> String;
        fn bterm_x(db: &OdbDb, bterm: &str) -> i32;
        fn bterm_y(db: &OdbDb, bterm: &str) -> i32;

        // write / ECO primitives
        fn create_net(db: &OdbDb, name: &str) -> Result<()>;
        fn create_inst(db: &OdbDb, master: &str, name: &str) -> Result<()>;
        fn set_inst_location(db: &OdbDb, inst: &str, x: i32, y: i32) -> Result<()>;
        fn set_inst_orient(db: &OdbDb, inst: &str, orient: &str) -> Result<()>;
        fn add_obstruction(db: &OdbDb, layer: &str, x1: i32, y1: i32, x2: i32, y2: i32) -> Result<()>;
        fn num_obstructions(db: &OdbDb) -> usize;
        fn clear_obstructions(db: &OdbDb) -> usize;
        fn bterm_direction(db: &OdbDb, bterm: &str) -> String;
        fn total_wire_length(db: &OdbDb) -> u64;
        fn place_bterm(db: &OdbDb, bterm: &str, layer: &str, x1: i32, y1: i32, x2: i32, y2: i32) -> Result<()>;
        fn connect(db: &OdbDb, inst: &str, pin: &str, net: &str) -> Result<()>;
        fn disconnect(db: &OdbDb, inst: &str, pin: &str) -> Result<()>;
    }
}

#[cfg(unix)]
pub use ffi::{
    add_obstruction, block_name, bterm_direction, bterm_net, bterm_x, bterm_y, clear_obstructions,
    connect, create_inst, create_net, disconnect, find_master, first_master_name, input_pin,
    inst_master, inst_x, inst_y, net_of, nth_bterm_name, nth_inst_name, nth_iterm_name, num_bterms,
    num_insts, num_iterms, num_nets, num_obstructions, open_db, output_pin, place_bterm,
    read_def, set_inst_location, set_inst_orient, total_wire_length, write_db, write_def, OdbDb,
};
