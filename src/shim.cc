// SPDX-License-Identifier: Apache-2.0
#include "shim.h"

#include "odb/defin.h"   // LEF/DEF I/O (libodb v1)
#include "odb/defout.h"

#include <cstdint>
#include <fstream>
#include <stdexcept>
#include <string>
#include <vector>

using odb::dbBlock;
using odb::dbBox;
using odb::dbBPin;
using odb::dbBTerm;
using odb::dbInst;
using odb::dbITerm;
using odb::dbMaster;
using odb::dbNet;
using odb::dbObstruction;
using odb::dbTech;
using odb::dbTechLayer;

static std::string s(rust::Str v) { return std::string(v.data(), v.size()); }

static dbBlock* block_of(const OdbDb& h) {
  odb::dbChip* chip = h.db->getChip();
  return chip ? chip->getBlock() : nullptr;
}
static dbBlock* require_block(const OdbDb& h) {
  dbBlock* b = block_of(h);
  if (!b) throw std::runtime_error("vyges-opendb: no top block");
  return b;
}
static dbInst* require_inst(const OdbDb& h, rust::Str inst) {
  dbInst* i = require_block(h)->findInst(s(inst).c_str());
  if (!i) throw std::runtime_error("vyges-opendb: inst not found: " + s(inst));
  return i;
}
static dbITerm* require_iterm(const OdbDb& h, rust::Str inst, rust::Str pin) {
  dbITerm* t = require_inst(h, inst)->findITerm(s(pin).c_str());
  if (!t) throw std::runtime_error("vyges-opendb: pin not found: " + s(inst) + "/" + s(pin));
  return t;
}

// ---- open / write ------------------------------------------------------------
std::unique_ptr<OdbDb> open_db(rust::Str path) {
  auto h = std::make_unique<OdbDb>();
  std::string p = s(path);
  std::ifstream in(p, std::ios::binary);
  if (!in) throw std::runtime_error("vyges-opendb: cannot open " + p);
  h->db->read(in);
  return h;
}
void write_db(const OdbDb& h, rust::Str path) {
  std::string p = s(path);
  std::ofstream out(p, std::ios::binary);
  if (!out) throw std::runtime_error("vyges-opendb: cannot write " + p);
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
rust::String inst_master(const OdbDb& h, rust::Str inst) {
  dbBlock* b = block_of(h);
  dbInst* i = b ? b->findInst(s(inst).c_str()) : nullptr;
  dbMaster* m = i ? i->getMaster() : nullptr;
  return rust::String(m ? m->getName() : std::string());
}
std::size_t num_iterms(const OdbDb& h, rust::Str inst) {
  dbBlock* b = block_of(h);
  dbInst* i = b ? b->findInst(s(inst).c_str()) : nullptr;
  return i ? i->getITerms().size() : 0;
}
rust::String nth_iterm_name(const OdbDb& h, rust::Str inst, std::size_t idx) {
  dbBlock* b = block_of(h);
  dbInst* i = b ? b->findInst(s(inst).c_str()) : nullptr;
  if (!i) return rust::String();
  std::size_t k = 0;
  for (dbITerm* t : i->getITerms())
    if (k++ == idx) return rust::String(t->getMTerm()->getName());
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
rust::String nth_bterm_name(const OdbDb& h, std::size_t i) {
  dbBlock* b = block_of(h);
  if (!b) return rust::String();
  std::size_t k = 0;
  for (dbBTerm* bt : b->getBTerms()) {
    if (k++ == i) return rust::String(bt->getName());
  }
  return rust::String();
}
rust::String bterm_net(const OdbDb& h, rust::Str bterm) {
  dbBlock* b = block_of(h);
  dbBTerm* bt = b ? b->findBTerm(s(bterm).c_str()) : nullptr;
  dbNet* n = bt ? bt->getNet() : nullptr;
  return rust::String(n ? n->getName() : std::string());
}
int32_t bterm_x(const OdbDb& h, rust::Str bterm) {
  dbBlock* b = block_of(h);
  dbBTerm* bt = b ? b->findBTerm(s(bterm).c_str()) : nullptr;
  int x = 0, y = 0;
  if (bt) bt->getFirstPinLocation(x, y);
  return x;
}
int32_t bterm_y(const OdbDb& h, rust::Str bterm) {
  dbBlock* b = block_of(h);
  dbBTerm* bt = b ? b->findBTerm(s(bterm).c_str()) : nullptr;
  int x = 0, y = 0;
  if (bt) bt->getFirstPinLocation(x, y);
  return y;
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
  if (b->findNet(s(name).c_str())) throw std::runtime_error("vyges-opendb: net exists: " + s(name));
  if (!dbNet::create(b, s(name).c_str())) throw std::runtime_error("vyges-opendb: create_net failed: " + s(name));
}
void create_inst(const OdbDb& h, rust::Str master, rust::Str name) {
  dbBlock* b = require_block(h);
  dbMaster* m = h.db->findMaster(s(master).c_str());
  if (!m) throw std::runtime_error("vyges-opendb: master not found: " + s(master));
  if (!dbInst::create(b, m, s(name).c_str())) throw std::runtime_error("vyges-opendb: create_inst failed: " + s(name));
}
void set_inst_location(const OdbDb& h, rust::Str inst, int32_t x, int32_t y) {
  dbInst* i = require_inst(h, inst);
  i->setLocation(x, y);
  i->setPlacementStatus(odb::dbPlacementStatus::PLACED);
}
void set_inst_orient(const OdbDb& h, rust::Str inst, rust::Str orient) {
  // dbOrientType parses "R0"/"R90"/"R180"/"R270"/"MX"/"MY"/"MXR90"/"MYR90".
  require_inst(h, inst)->setOrient(odb::dbOrientType(s(orient).c_str()));
}
void add_obstruction(const OdbDb& h, rust::Str layer, int32_t x1, int32_t y1, int32_t x2,
                     int32_t y2) {
  dbBlock* b = require_block(h);
  dbTech* tech = b->getTech();
  dbTechLayer* l = tech ? tech->findLayer(s(layer).c_str()) : nullptr;
  if (!l) throw std::runtime_error("vyges-opendb: tech layer not found: " + s(layer));
  dbObstruction::create(b, l, x1, y1, x2, y2);
}
std::size_t num_obstructions(const OdbDb& h) {
  dbBlock* b = block_of(h);
  return b ? b->getObstructions().size() : 0;
}
std::size_t clear_obstructions(const OdbDb& h) {
  dbBlock* b = block_of(h);
  if (!b) return 0;
  std::vector<dbObstruction*> obs(b->getObstructions().begin(), b->getObstructions().end());
  for (dbObstruction* o : obs) dbObstruction::destroy(o);
  return obs.size();
}
rust::String bterm_direction(const OdbDb& h, rust::Str bterm) {
  dbBlock* b = block_of(h);
  dbBTerm* bt = b ? b->findBTerm(s(bterm).c_str()) : nullptr;
  if (!bt) return rust::String();
  return rust::String(std::string(bt->getIoType().getString()));  // INPUT/OUTPUT/INOUT/…
}
std::uint64_t total_wire_length(const OdbDb& h) {
  dbBlock* b = block_of(h);
  if (!b) return 0;
  std::uint64_t total = 0;
  for (dbNet* n : b->getNets()) {
    odb::dbWire* w = n->getWire();
    if (w) total += w->getLength();
  }
  return total;
}
void write_def(const OdbDb& h, rust::Str path) {
  dbBlock* b = require_block(h);
  // the OdbDb owns its logger; DefOut wants a non-const Logger* (logically mutable, like h.db).
  odb::DefOut writer(const_cast<utl::Logger*>(&h.logger));
  if (!writer.writeBlock(b, s(path).c_str())) {
    throw std::runtime_error("vyges-opendb: DEF write failed: " + s(path));
  }
}
// Read a DEF into the db. mode: "default" (design from scratch), "floorplan" (update existing
// COMPONENTS/PINS/DIEAREA/TRACKS/ROWS/NETS — this is Odb.ApplyDEFTemplate), "incremental"
// (update COMPONENTS/PINS). Non-default modes require an existing design (chip + libs).
void read_def(const OdbDb& h, rust::Str def_path, rust::Str mode) {
  odb::dbDatabase* db = h.db;
  std::string ms = s(mode);
  odb::defin::MODE m = odb::defin::DEFAULT;
  if (ms == "floorplan") {
    m = odb::defin::FLOORPLAN;
  } else if (ms == "incremental") {
    m = odb::defin::INCREMENTAL;
  }
  odb::dbChip* chip = db->getChip();
  if (!chip) {
    if (m != odb::defin::DEFAULT) {
      throw std::runtime_error("vyges-opendb: no existing design for a floorplan/incremental DEF update");
    }
    chip = odb::dbChip::create(db, db->getTech());  // fresh chip (DEFAULT import into a tech-only db)
  }
  std::vector<odb::dbLib*> libs;
  for (odb::dbLib* lib : db->getLibs()) {
    libs.push_back(lib);
  }
  odb::defin reader(db, const_cast<utl::Logger*>(&h.logger), m);
  reader.readChip(libs, s(def_path).c_str(), chip);
}
void place_bterm(const OdbDb& h, rust::Str bterm, rust::Str layer, int32_t x1, int32_t y1,
                 int32_t x2, int32_t y2) {
  dbBlock* b = require_block(h);
  dbBTerm* bt = b->findBTerm(s(bterm).c_str());
  if (!bt) throw std::runtime_error("vyges-opendb: bterm not found: " + s(bterm));
  dbTech* tech = b->getTech();
  dbTechLayer* l = tech ? tech->findLayer(s(layer).c_str()) : nullptr;
  if (!l) throw std::runtime_error("vyges-opendb: tech layer not found: " + s(layer));
  dbBPin* bpin = dbBPin::create(bt);
  dbBox::create(bpin, l, x1, y1, x2, y2);
  bpin->setPlacementStatus(odb::dbPlacementStatus::PLACED);
}
void connect(const OdbDb& h, rust::Str inst, rust::Str pin, rust::Str net) {
  dbNet* n = require_block(h)->findNet(s(net).c_str());
  if (!n) throw std::runtime_error("vyges-opendb: net not found: " + s(net));
  require_iterm(h, inst, pin)->connect(n);
}
void disconnect(const OdbDb& h, rust::Str inst, rust::Str pin) {
  require_iterm(h, inst, pin)->disconnect();
}
