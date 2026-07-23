// SPDX-License-Identifier: Apache-2.0
#include "shim.h"

#include <fstream>
#include <stdexcept>
#include <string>

static odb::dbBlock* block_of(const OdbDb& h) {
  odb::dbChip* chip = h.db->getChip();
  return chip ? chip->getBlock() : nullptr;
}

std::unique_ptr<OdbDb> open_db(rust::Str path) {
  auto h = std::make_unique<OdbDb>();
  std::string p(path.data(), path.size());
  std::ifstream in(p, std::ios::binary);
  if (!in) {
    throw std::runtime_error("vyges-odb: cannot open " + p);
  }
  h->db->read(in);
  return h;
}

rust::String block_name(const OdbDb& h) {
  odb::dbBlock* b = block_of(h);
  return rust::String(b ? b->getName() : std::string());
}

std::size_t num_insts(const OdbDb& h) {
  odb::dbBlock* b = block_of(h);
  return b ? b->getInsts().size() : 0;
}

std::size_t num_nets(const OdbDb& h) {
  odb::dbBlock* b = block_of(h);
  return b ? b->getNets().size() : 0;
}

std::size_t num_bterms(const OdbDb& h) {
  odb::dbBlock* b = block_of(h);
  return b ? b->getBTerms().size() : 0;
}

void write_db(const OdbDb& h, rust::Str path) {
  std::string p(path.data(), path.size());
  std::ofstream out(p, std::ios::binary);
  if (!out) {
    throw std::runtime_error("vyges-odb: cannot write " + p);
  }
  h.db->write(out);
}
