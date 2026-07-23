// SPDX-License-Identifier: Apache-2.0
// Thin cxx-friendly shim over OpenDB. The opaque handle owns a dbDatabase + its Logger.
#pragma once
#include <cstddef>
#include <memory>

#include "rust/cxx.h"
#include "odb/db.h"
#include "utl/Logger.h"

// Complete definition here (not just a forward decl) so the generated cxx bridge —
// which instantiates std::unique_ptr<OdbDb> — sees a complete type.
struct OdbDb {
  utl::Logger logger;
  odb::dbDatabase* db;
  OdbDb() : db(odb::dbDatabase::create()) { db->setLogger(&logger); }
};

std::unique_ptr<OdbDb> open_db(rust::Str path);   // read a .odb (throws -> Rust Result)
rust::String block_name(const OdbDb& db);
std::size_t num_insts(const OdbDb& db);
std::size_t num_nets(const OdbDb& db);
std::size_t num_bterms(const OdbDb& db);
void write_db(const OdbDb& db, rust::Str path);   // throws -> Rust Result
