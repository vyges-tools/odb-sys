// SPDX-License-Identifier: Apache-2.0
// Thin cxx-friendly shim over OpenDB. Opaque handle owns a dbDatabase + its Logger.
// Objects are addressed by name (cxx-friendly): no raw odb pointers cross the boundary.
#pragma once
#include <cstddef>
#include <cstdint>
#include <memory>

#include "rust/cxx.h"
#include "odb/db.h"
#include "utl/Logger.h"

// Complete definition here (not a forward decl) so the generated cxx bridge —
// which instantiates std::unique_ptr<OdbDb> — sees a complete type.
struct OdbDb {
  utl::Logger logger;
  odb::dbDatabase* db;
  OdbDb() : db(odb::dbDatabase::create()) { db->setLogger(&logger); }
};

// ---- open / read / write -----------------------------------------------------
std::unique_ptr<OdbDb> open_db(rust::Str path);   // throws -> Rust Result
void write_db(const OdbDb& db, rust::Str path);   // throws -> Rust Result

// ---- read / inspect ----------------------------------------------------------
rust::String block_name(const OdbDb& db);
std::size_t num_insts(const OdbDb& db);
std::size_t num_nets(const OdbDb& db);
std::size_t num_bterms(const OdbDb& db);
rust::String nth_inst_name(const OdbDb& db, std::size_t i);      // "" if out of range
rust::String first_master_name(const OdbDb& db);                 // any master, "" if none
rust::String find_master(const OdbDb& db, rust::Str substr);     // first master whose name contains substr
rust::String input_pin(const OdbDb& db, rust::Str inst);         // first input-signal pin name
rust::String output_pin(const OdbDb& db, rust::Str inst);        // first output-signal pin name
rust::String inst_master(const OdbDb& db, rust::Str inst);       // instance's master cell name ("" if none)
std::size_t num_iterms(const OdbDb& db, rust::Str inst);         // number of instance pins (iterms)
rust::String nth_iterm_name(const OdbDb& db, rust::Str inst, std::size_t i);  // i-th pin name ("" out of range)
rust::String net_of(const OdbDb& db, rust::Str inst, rust::Str pin);  // net on a pin, "" if none
int32_t inst_x(const OdbDb& db, rust::Str inst);   // instance origin x in DBU (0 if not found)
int32_t inst_y(const OdbDb& db, rust::Str inst);   // instance origin y in DBU (0 if not found)
rust::String nth_bterm_name(const OdbDb& db, std::size_t i);         // block port (bterm), "" if out of range
rust::String bterm_net(const OdbDb& db, rust::Str bterm);            // net on a port, "" if none
int32_t bterm_x(const OdbDb& db, rust::Str bterm);                   // port first-pin x in DBU (0 if none)
int32_t bterm_y(const OdbDb& db, rust::Str bterm);                   // port first-pin y in DBU (0 if none)

// ---- write / ECO primitives (the InsertECOBuffers building blocks) -----------
void create_net(const OdbDb& db, rust::Str name);                       // throws on dup/failure
void create_inst(const OdbDb& db, rust::Str master, rust::Str name);    // throws if master missing
void set_inst_location(const OdbDb& db, rust::Str inst, int32_t x, int32_t y);  // + PLACED
void set_inst_orient(const OdbDb& db, rust::Str inst, rust::Str orient);        // R0/R90/MX/…
void connect(const OdbDb& db, rust::Str inst, rust::Str pin, rust::Str net);    // iterm -> net
void disconnect(const OdbDb& db, rust::Str inst, rust::Str pin);               // iterm -> (none)
