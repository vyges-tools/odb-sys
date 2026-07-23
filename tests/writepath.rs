// SPDX-License-Identifier: Apache-2.0
// Write path: create a buffer inst + net, wire it, persist, and verify it survives a
// round-trip. These are the primitives InsertECOBuffers composes.
use vyges_odb_sys as odb;

#[test]
fn create_wire_and_persist() {
    let db = odb::open_db("test/fixtures/counter.odb").expect("read");
    let (n0, m0) = (odb::num_insts(&db), odb::num_nets(&db));

    // a real buffer master from the design's library
    let master = odb::find_master(&db, "buf");
    assert!(!master.is_empty(), "no buffer master in fixture libs");

    odb::create_net(&db, "vyges_eco_net").unwrap();
    odb::create_inst(&db, &master, "vyges_eco_buf").unwrap();
    odb::set_inst_location(&db, "vyges_eco_buf", 1000, 1000).unwrap();

    let a = odb::input_pin(&db, "vyges_eco_buf");
    assert!(!a.is_empty(), "buffer has no input pin");
    odb::connect(&db, "vyges_eco_buf", &a, "vyges_eco_net").unwrap();
    assert_eq!(odb::net_of(&db, "vyges_eco_buf", &a), "vyges_eco_net");

    assert_eq!(odb::num_insts(&db), n0 + 1);
    assert_eq!(odb::num_nets(&db), m0 + 1);

    // persist -> reread -> the edit survived serialization
    let out = std::env::temp_dir().join("vyges_odb_wp.odb");
    odb::write_db(&db, out.to_str().unwrap()).unwrap();
    let db2 = odb::open_db(out.to_str().unwrap()).unwrap();
    assert_eq!(odb::num_insts(&db2), n0 + 1);
    assert_eq!(odb::num_nets(&db2), m0 + 1);
    assert_eq!(odb::net_of(&db2, "vyges_eco_buf", &a), "vyges_eco_net");
}

#[test]
fn errors_are_results() {
    let db = odb::open_db("test/fixtures/counter.odb").unwrap();
    assert!(odb::create_inst(&db, "no_such_master", "x").is_err());
    assert!(odb::connect(&db, "no_such_inst", "A", "no_such_net").is_err());
}
