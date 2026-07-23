// SPDX-License-Identifier: Apache-2.0
// Smoke test: read a .odb, walk the model, write it back.
// Proves the db + I/O core links WITHOUT swig/tcl bindings or any OpenROAD engine.
#include <fstream>
#include <iostream>
#include "odb/db.h"
#include "utl/Logger.h"
using namespace odb;
int main(int argc, char** argv) {
  if (argc < 3) { std::cerr << "usage: odb_smoke in.odb out.odb\n"; return 2; }
  utl::Logger logger;
  dbDatabase* db = dbDatabase::create();
  db->setLogger(&logger);
  std::ifstream in(argv[1], std::ios::binary);
  if (!in) { std::cerr << "cannot open " << argv[1] << "\n"; return 1; }
  db->read(in);
  dbChip* chip = db->getChip();
  if (chip && chip->getBlock()) {
    dbBlock* b = chip->getBlock();
    std::cout << "block=" << b->getName()
              << " insts=" << b->getInsts().size()
              << " nets=" << b->getNets().size()
              << " bterms=" << b->getBTerms().size() << "\n";
  } else {
    std::cout << "no chip/block (still read OK)\n";
  }
  std::ofstream out(argv[2], std::ios::binary);
  db->write(out);
  std::cout << "round-trip OK\n";
  return 0;
}
