// SPDX-License-Identifier: Apache-2.0
// Fixture generator: read a flat .odb and bolt on a small, well-known MODULE HIERARCHY plus a DRC
// MARKER, then write it out. Produces the hierarchical fixture the Rust tests read
// (hier.odb) so the mod-inst/mod-net/mod-bterm/mod-iterm + marker accessors can be validated
// against *populated* data, not just discovery / graceful-empty.
//
// Our safe Rust API deliberately does not expose module/marker *creation* (structural edits stay
// audited), and there's no OpenROAD on the build host — so this tiny libodb program is the
// reproducible way to synthesize the fixture. Build with -DVYGES_ODB_MKFIXTURE=ON, then:
//     odb_mkfixture <in-flat.odb> <out-hier.odb>
//
// What it creates (names the tests assert on):
//   top module (existing)  ── u_leaf : dbModInst (master = "leaf")
//   leaf module (new)      ── ports A (INPUT), Y (OUTPUT) : dbModBTerm
//   u_leaf                 ── A, Y : dbModITerm
//   top module             ── hier_net : dbModNet
//   marker category "drc_test" ── one dbMarker: rect (1000,2000)-(5000,8000), waived, "test drc"
#include <fstream>
#include <iostream>

#include "odb/db.h"
#include "odb/geom.h"
#include "utl/Logger.h"

using namespace odb;

int main(int argc, char** argv) {
  if (argc < 3) {
    std::cerr << "usage: odb_mkfixture <in-flat.odb> <out-hier.odb>\n";
    return 2;
  }
  utl::Logger logger;
  dbDatabase* db = dbDatabase::create();
  db->setLogger(&logger);

  std::ifstream in(argv[1], std::ios::binary);
  if (!in) { std::cerr << "cannot open " << argv[1] << "\n"; return 1; }
  db->read(in);

  dbChip* chip = db->getChip();
  dbBlock* block = chip ? chip->getBlock() : nullptr;
  if (!block) { std::cerr << "no top block in " << argv[1] << "\n"; return 1; }
  dbModule* top = block->getTopModule();
  if (!top) { std::cerr << "no top module\n"; return 1; }

  // --- module hierarchy ---
  dbModule* leaf = dbModule::create(block, "leaf");
  dbModBTerm* a = dbModBTerm::create(leaf, "A");
  a->setIoType(dbIoType::INPUT);
  dbModBTerm* y = dbModBTerm::create(leaf, "Y");
  y->setIoType(dbIoType::OUTPUT);

  dbModInst* u_leaf = dbModInst::create(top, leaf, "u_leaf");
  dbModITerm::create(u_leaf, "A", a);
  dbModITerm::create(u_leaf, "Y", y);

  dbModNet::create(top, "hier_net");

  // --- one DRC marker in a category ---
  dbMarkerCategory* cat = dbMarkerCategory::create(block, "drc_test");
  dbMarker* m = dbMarker::create(cat);
  m->addShape(Rect(1000, 2000, 5000, 8000));
  m->setComment("test drc");
  m->setWaived(true);
  m->setLineNumber(42);
  if (dbTech* tech = db->getTech()) {
    if (dbTechLayer* met1 = tech->findLayer("met1")) m->setTechLayer(met1);
  }

  std::ofstream out(argv[2], std::ios::binary);
  if (!out) { std::cerr << "cannot write " << argv[2] << "\n"; return 1; }
  db->write(out);
  std::cout << "wrote " << argv[2] << ": +1 modinst (u_leaf/leaf), +1 modnet (hier_net), "
            << "+2 modbterms/moditerms, +1 marker (cat drc_test)\n";
  return 0;
}
