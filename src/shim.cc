// SPDX-License-Identifier: Apache-2.0
#include "shim.h"

#include <fstream>
#include <stdexcept>
#include <string>

using odb::dbBlock;
using odb::dbInst;
using odb::dbITerm;
using odb::dbMaster;
using odb::dbNet;

static std::string s(rust::Str v) { return std::string(v.data(), v.size()); }

static dbBlock* block_of(const OdbDb& h) {
  odb::dbChip* chip = h.db->getChip();
  return chip ? chip->getBlock() : nullptr;
}
static dbBlock* require_block(const OdbDb& h) {
  dbBlock* b = block_of(h);
  if (!b) throw std::runtime_error("vyges-odb: no top block");
  return b;
}
static dbInst* require_inst(const OdbDb& h, rust::Str inst) {
  dbInst* i = require_block(h)->findInst(s(inst).c_str());
  if (!i) throw std::runtime_error("vyges-odb: inst not found: " + s(inst));
  return i;
}
static dbITerm* require_iterm(const OdbDb& h, rust::Str inst, rust::Str pin) {
  dbITerm* t = require_inst(h, inst)->findITerm(s(pin).c_str());
  if (!t) throw std::runtime_error("vyges-odb: pin not found: " + s(inst) + "/" + s(pin));
  return t;
}

// ---- open / write ------------------------------------------------------------
std::unique_ptr<OdbDb> open_db(rust::Str path) {
  auto h = std::make_unique<OdbDb>();
  std::string p = s(path);
  std::ifstream in(p, std::ios::binary);
  if (!in) throw std::runtime_error("vyges-odb: cannot open " + p);
  h->db->read(in);
  return h;
}
void write_db(const OdbDb& h, rust::Str path) {
  std::string p = s(path);
  std::ofstream out(p, std::ios::binary);
  if (!out) throw std::runtime_error("vyges-odb: cannot write " + p);
  h.db->write(out);
}

// ---- read / inspect ----------------------------------------------------------
rust::String block_name(const OdbDb& h) {
  dbBlock* b = block_of(h);
  return rust::String(b ? b->getName() : std::string());
}
std::size_t num_insts(const OdbDb& h)  { dbBlock* b = block_of(h); return b ? b->getInsts().size()  : 0; }
std::size_t num_nets(const OdbDb& h)   { dbBlock* b = block_of(h); return b ? b->getNets().size()   : 0; }
std::size_t num_bterms(const OdbDb& h) { dbBlock* b = block_of(h); return b ? b->getBTerms().size() : 0; }

rust::String nth_inst_name(const OdbDb& h, std::size_t i) {
  dbBlock* b = block_of(h);
  if (!b) return rust::String();
  std::size_t k = 0;
  for (dbInst* inst : b->getInsts()) {
    if (k++ == i) return rust::String(inst->getName());
  }
  return rust::String();
}
rust::String first_master_name(const OdbDb& h) {
  for (odb::dbLib* lib : h.db->getLibs())
    for (dbMaster* m : lib->getMasters())
      return rust::String(m->getName());
  return rust::String();
}
rust::String find_master(const OdbDb& h, rust::Str substr) {
  std::string want = s(substr);
  for (odb::dbLib* lib : h.db->getLibs())
    for (dbMaster* m : lib->getMasters()) {
      std::string n = m->getName();
      if (n.find(want) != std::string::npos) return rust::String(n);
    }
  return rust::String();
}
// Inspect functions are total (never throw): return "" on a missing block/inst/pin.
rust::String input_pin(const OdbDb& h, rust::Str inst) {
  dbBlock* b = block_of(h);
  dbInst* i = b ? b->findInst(s(inst).c_str()) : nullptr;
  if (!i) return rust::String();
  for (dbITerm* t : i->getITerms())
    if (t->isInputSignal()) return rust::String(t->getMTerm()->getName());
  return rust::String();
}
rust::String output_pin(const OdbDb& h, rust::Str inst) {
  dbBlock* b = block_of(h);
  dbInst* i = b ? b->findInst(s(inst).c_str()) : nullptr;
  if (!i) return rust::String();
  for (dbITerm* t : i->getITerms())
    if (t->isOutputSignal()) return rust::String(t->getMTerm()->getName());
  return rust::String();
}
rust::String net_of(const OdbDb& h, rust::Str inst, rust::Str pin) {
  dbBlock* b = block_of(h);
  dbInst* i = b ? b->findInst(s(inst).c_str()) : nullptr;
  dbITerm* t = i ? i->findITerm(s(pin).c_str()) : nullptr;
  dbNet* n = t ? t->getNet() : nullptr;
  return rust::String(n ? n->getName() : std::string());
}
int32_t inst_x(const OdbDb& h, rust::Str inst) {
  dbBlock* b = block_of(h);
  dbInst* i = b ? b->findInst(s(inst).c_str()) : nullptr;
  if (!i) return 0;
  int x = 0, y = 0;
  i->getLocation(x, y);
  return x;
}
int32_t inst_y(const OdbDb& h, rust::Str inst) {
  dbBlock* b = block_of(h);
  dbInst* i = b ? b->findInst(s(inst).c_str()) : nullptr;
  if (!i) return 0;
  int x = 0, y = 0;
  i->getLocation(x, y);
  return y;
}

// ---- write / ECO primitives --------------------------------------------------
void create_net(const OdbDb& h, rust::Str name) {
  dbBlock* b = require_block(h);
  if (b->findNet(s(name).c_str())) throw std::runtime_error("vyges-odb: net exists: " + s(name));
  if (!dbNet::create(b, s(name).c_str())) throw std::runtime_error("vyges-odb: create_net failed: " + s(name));
}
void create_inst(const OdbDb& h, rust::Str master, rust::Str name) {
  dbBlock* b = require_block(h);
  dbMaster* m = h.db->findMaster(s(master).c_str());
  if (!m) throw std::runtime_error("vyges-odb: master not found: " + s(master));
  if (!dbInst::create(b, m, s(name).c_str())) throw std::runtime_error("vyges-odb: create_inst failed: " + s(name));
}
void set_inst_location(const OdbDb& h, rust::Str inst, int32_t x, int32_t y) {
  dbInst* i = require_inst(h, inst);
  i->setLocation(x, y);
  i->setPlacementStatus(odb::dbPlacementStatus::PLACED);
}
void connect(const OdbDb& h, rust::Str inst, rust::Str pin, rust::Str net) {
  dbNet* n = require_block(h)->findNet(s(net).c_str());
  if (!n) throw std::runtime_error("vyges-odb: net not found: " + s(net));
  require_iterm(h, inst, pin)->connect(n);
}
void disconnect(const OdbDb& h, rust::Str inst, rust::Str pin) {
  require_iterm(h, inst, pin)->disconnect();
}
