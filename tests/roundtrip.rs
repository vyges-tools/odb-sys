// SPDX-License-Identifier: Apache-2.0
// Rust <-> libodb FFI: open a real .odb, read the model, write it back.

#[test]
fn open_read_roundtrip() {
    let db = vyges_odb_sys::open_db("test/fixtures/counter.odb").expect("read .odb");
    assert_eq!(vyges_odb_sys::block_name(&db), "counter");
    assert_eq!(vyges_odb_sys::num_insts(&db), 229);
    assert_eq!(vyges_odb_sys::num_nets(&db), 52);
    assert_eq!(vyges_odb_sys::num_bterms(&db), 13);

    let out = std::env::temp_dir().join("vyges_odb_rt.odb");
    vyges_odb_sys::write_db(&db, out.to_str().unwrap()).expect("write .odb");
    assert!(out.exists());
}

#[test]
fn missing_file_is_err() {
    assert!(vyges_odb_sys::open_db("/nonexistent/x.odb").is_err());
}
