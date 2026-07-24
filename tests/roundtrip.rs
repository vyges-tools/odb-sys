// SPDX-License-Identifier: Apache-2.0
// Rust <-> libodb FFI: open a real .odb, read the model, write it back.

#[test]
fn open_read_roundtrip() {
    let db = vyges_opendb_lib::open_db("test/fixtures/counter.odb").expect("read .odb");
    assert_eq!(vyges_opendb_lib::block_name(&db), "counter");
    assert_eq!(vyges_opendb_lib::num_insts(&db), 229);
    assert_eq!(vyges_opendb_lib::num_nets(&db), 52);
    assert_eq!(vyges_opendb_lib::num_bterms(&db), 13);

    let out = std::env::temp_dir().join("vyges_opendb_rt.odb");
    vyges_opendb_lib::write_db(&db, out.to_str().unwrap()).expect("write .odb");
    assert!(out.exists());
}

#[test]
fn missing_file_is_err() {
    assert!(vyges_opendb_lib::open_db("/nonexistent/x.odb").is_err());
}
