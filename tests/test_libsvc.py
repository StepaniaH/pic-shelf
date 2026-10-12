import pytest

from picshelf import libsvc


def test_similarity_uses_first_representative_and_visible_images(conn, library):
    # 0000 ↔ 0011 ↔ 1111 forms a chain. At radius 2 the last item must
    # not join the first group: every member must match its representative.
    for n, value in enumerate([0, 3, 15, 0, 15]):
        conn.execute(
            "INSERT INTO images(library_id, rel_path, filename, ext, phash, imported_at, size_bytes, mtime) VALUES(?,?,?,?,?,?,0,0)",
            (library["id"], f"{n}.png", f"{n}.png", "png", f"{value:016x}", "2026-01-01"),
        )
    conn.commit()
    groups = libsvc.similar_dupes(conn, threshold=2)
    assert [[x["filename"] for x in group["images"]] for group in groups] == [
        ["0.png", "1.png", "3.png"], ["2.png", "4.png"],
    ]
    assert [x["distance"] for x in groups[0]["images"]] == [0, 2, 0]
    assert libsvc.similar_dupes(conn, allowed=[], threshold=2) == []
    assert [g["count"] for g in libsvc.similar_dupes(conn, threshold=0)] == [2, 2]
    with pytest.raises(ValueError):
        libsvc.similar_dupes(conn, threshold=65)
