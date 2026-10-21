"""Catalog query results and bounded work on larger collections."""
from picshelf import catalog, db, indexer, libsvc, names, ops, play, search
from picshelf.access import AccessContext
from tests.datasets import metadata_rows


def test_image_summaries_identify_libraries_without_expanding_access(tmp_path):
    from tests.scenario_support import build_catalog
    sample = build_catalog(tmp_path)
    with db.open_db(sample.cfg.db_path) as conn:
        for username, expected in [('curator', {'north', 'south'}), ('reader', {'north'}), ('outsider', set())]:
            result = catalog.query_images(conn, AccessContext(sample.users[username]), catalog.ImageQuery(sort='path'))
            assert {item['library_name'] for item in result['items']} == expected
            for item in result['items']:
                assert item['library_id'] == sample.libraries[item['library_name']]
        conn.execute('UPDATE libraries SET name=? WHERE id=?', ('Renamed north', sample.libraries['north']))
        conn.commit()
        result = catalog.query_images(conn, AccessContext(sample.users['reader']), catalog.ImageQuery(sort='path'))
        assert {item['library_name'] for item in result['items']} == {'Renamed north'}


def test_album_pages_scope_members_covers_and_literal_search(conn, library, tmp_path):
    other_root = tmp_path / "private"
    other_root.mkdir()
    other = libsvc.create_library(conn, "Private", str(other_root))
    records = list(metadata_rows(3, library["id"])) + list(metadata_rows(1, other))
    fields = list(records[0])
    conn.executemany(f"INSERT INTO images({','.join(fields)}) VALUES({','.join('?' for _ in fields)})",
                     [list(record.values()) for record in records])
    conn.commit()
    albums = [ops.create_album(conn, f"Album {n:03}", library["id"])["id"] for n in range(110)]
    for album in albums: ops.album_add_images(conn, album, [1, 2])
    mixed = ops.create_album(conn, "100%_mixed", library["id"])["id"]
    shared = ops.create_album(conn, "Shared")["id"]
    for album in [mixed, shared]:
        ops.album_add_images(conn, album, [1, 3, 4])
        ops.update_album(conn, album, cover_image_id=4)
    conn.execute("UPDATE images SET missing=1 WHERE id=3")
    conn.commit()
    allowed = [library["id"]]
    first = ops.list_albums(conn, allowed_library_ids=allowed, limit=100)
    second = ops.list_albums(conn, allowed_library_ids=allowed, limit=100, offset=100)
    assert len(first) == 100 and len(second) == 12
    assert {a["id"] for a in first}.isdisjoint(a["id"] for a in second)
    assert {a["id"] for a in first + second} == {*albums, mixed, shared}
    for album in first + second:
        assert album["image_count"] == (1 if album["id"] in (mixed, shared) else 2)
        assert album["cover_image_id"] is None
        assert album["preview_image_id"] == 1
    assert [a["id"] for a in ops.list_albums(conn, allowed_library_ids=allowed, q="%_")] == [mixed]
    assert {a["id"] for a in ops.list_albums(conn, allowed_library_ids=allowed, for_edit=True)} == set(albums)
    assert ops.list_albums(conn, allowed_library_ids=[], limit=100) == []


def test_tag_rename_cannot_take_another_tags_alias(conn):
    import pytest
    first = ops.ensure_tag(conn, "Night sky")
    second = ops.ensure_tag(conn, "Daylight")
    conn.execute("INSERT INTO tag_aliases(entity_id,name,match_key,created_at) VALUES(?,?,?,?)",
                 (first, "Stars", "stars", db.utcnow()))
    conn.commit()
    for conflicting in (" stars ", "NIGHT  SKY"):
        with pytest.raises(ValueError): ops.rename_tag(conn, second, conflicting)
        assert conn.execute("SELECT name FROM tags WHERE id=?", (second,)).fetchone()[0] == "Daylight"
    assert ops.rename_tag(conn, first, "Stars")["name"] == "Stars"


def test_album_edit_picker_filters_current_permissions_and_validates_names(tmp_path):
    from fastapi.testclient import TestClient
    from picshelf.api import create_app
    from tests.scenario_support import build_catalog
    sample = build_catalog(tmp_path)
    with db.open_db(sample.cfg.db_path) as conn:
        conn.execute("UPDATE library_grants SET access='edit' WHERE library_id=?", (sample.libraries["north"],))
        conn.commit()
        own = ops.create_album(conn, "Editable album",sample.libraries["north"])["id"]
        mixed = ops.create_album(conn, "Mixed album",sample.libraries["north"])["id"]
        global_album = ops.create_album(conn, "Global album")["id"]
        ops.album_add_images(conn,mixed,[sample.images["s01"]])
        ops.album_add_images(conn,global_album,[sample.images["n01"]])
    with TestClient(create_app(sample.cfg),headers={"Origin":"http://testserver"}) as client:
        client.post("/api/v1/auth/login",json={"username":"reader","password":sample.password}).raise_for_status()
        visible = client.get("/api/v1/albums",params={"q":"album","limit":100}).json()
        assert {own,mixed,global_album} <= {row["id"] for row in visible}
        editable = client.get("/api/v1/albums",params={"q":"album","access":"edit","limit":100}).json()
        assert {row["id"] for row in editable} == {own}
        assert client.get(f"/api/v1/albums/{own}").json()["can_edit"] is True
        assert client.get(f"/api/v1/albums/{mixed}").json()["can_edit"] is False
        assert client.patch(f"/api/v1/albums/{mixed}",json={"name":"Denied"}).status_code == 403
        assert client.patch(f"/api/v1/albums/{own}",json={"name":"   "}).status_code == 400
        assert client.get(f"/api/v1/albums/{own}").json()["name"] == "Editable album"


def test_tag_matching_work_depends_on_names_not_image_count(conn, library, admin):
    rows = list(metadata_rows(2000, library["id"]))
    fields = list(rows[0])
    conn.executemany(f"INSERT INTO images({','.join(fields)}) VALUES({','.join('?' for _ in fields)})",
                     [list(row.values()) for row in rows])
    tag = ops.ensure_tag(conn, "Night sky")
    conn.executemany("INSERT INTO image_tags VALUES(?,?,?,?)",
                     [(image, tag, admin["id"], db.utcnow()) for image in range(1, 2001)])
    conn.execute("INSERT INTO tag_aliases(entity_id,name,match_key,created_at) VALUES(?,?,?,?)",
                 (tag, "Stars", "stars", db.utcnow()))
    conn.commit()
    calls = []
    conn.create_function("picshelf_name_key", 1, lambda value: calls.append(value) or names.match_key(value), deterministic=True)
    for spec in (catalog.ImageQuery(query='tag:STARS', sort='path', offset=1500, limit=20),
                 catalog.ImageQuery(tag=' Night   sky ', sort='path', offset=1500, limit=20)):
        calls.clear()
        result = catalog.query_images(conn, AccessContext(admin["id"]), spec)
        assert result["total"] == 2000
        assert [item["id"] for item in result["items"]] == list(range(1501, 1521))
        assert all(item["tag_count"] == 1 for item in result["items"])
        assert len(calls) < 20
    excluded = search.search_images(conn, admin["id"], [], query='tag:stars')
    assert excluded["total"] == 0 and excluded["items"] == []


def test_name_lists_page_without_losing_aliases_or_interpreting_wildcards(conn, library, admin):
    tags = [ops.ensure_tag(conn, f"name-{n:03}") for n in range(120)]
    for tag in tags:
        conn.execute("INSERT INTO tag_aliases(entity_id,name,match_key,created_at) VALUES(?,?,?,?)",
                     (tag, f"alias-{tag}", f"alias-{tag}", db.utcnow()))
    literal = ops.ensure_tag(conn, "100%_ready")
    conn.commit()
    statements = []
    conn.set_trace_callback(statements.append)
    first = ops.list_tags(conn, limit=100)
    conn.set_trace_callback(None)
    second = ops.list_tags(conn, limit=100, offset=100)
    assert len(first) == 100 and len(second) == 21
    assert {item["id"] for item in first}.isdisjoint(item["id"] for item in second)
    assert set(item["id"] for item in first + second) == {*tags, literal}
    assert [item for item in first + second if item["id"] == tags[-1]][0]["name_aliases"] == [f"alias-{tags[-1]}"]
    assert len(statements) <= 3
    assert [item["id"] for item in ops.list_tags(conn, q="%_")] == [literal]
    assert ops.list_tags(conn, allowed_library_ids=[]) == []
    for name in ["ordinary", "100%_artist", "another"]:
        ops.create_artist(conn, name)
    assert [item["name"] for item in ops.list_artists(conn, q="%_")] == ["100%_artist"]
    assert len(ops.list_artists(conn, limit=2, offset=2)) == 1
    assert ops.list_artists(conn, allowed_library_ids=[]) == []


def test_library_covers_skip_missing_and_failed_images(conn, cfg, library):
    indexer.scan_library(conn, cfg, library["id"])
    ids = [row[0] for row in conn.execute("SELECT id FROM images ORDER BY id DESC")]
    conn.execute("UPDATE images SET missing=1 WHERE id=?", (ids[0],))
    conn.execute("UPDATE images SET scan_error_code='decode_error' WHERE id=?", (ids[1],))
    conn.commit()
    assert libsvc.list_libraries(conn)[0]["cover_image_id"] == ids[2]
    assert libsvc.list_libraries(conn, []) == []


def test_ranked_pages_and_id_selection_preserve_ties_zero_and_unrated(conn, library, alice, admin, tmp_path):
    other_root = tmp_path / "other"
    other_root.mkdir()
    other = libsvc.create_library(conn, "Other", str(other_root))
    records = list(metadata_rows(180, library["id"])) + list(metadata_rows(20, other))
    fields = list(records[0])
    conn.executemany(f"INSERT INTO images({','.join(fields)}) VALUES({','.join('?' for _ in fields)})",
                     [list(record.values()) for record in records])
    expected = []
    for image_id, record in enumerate(records, 1):
        mine = image_id % 11 if image_id % 3 else None
        theirs = (image_id * 7) % 11 if image_id % 4 else None
        for user, rating in [(alice["id"], mine), (admin["id"], theirs)]:
            if rating is not None:
                conn.execute("INSERT INTO ratings(image_id,user_id,value,updated_at) VALUES(?,?,?,?)",
                             (image_id, user, rating, db.utcnow()))
        ratings = [value for value in (mine, theirs) if value is not None]
        avg = round(sum(ratings) / len(ratings), 2) if ratings else None
        if image_id % 13:
            expected.append({"id": image_id, "library_id": record["library_id"], "path": record["rel_path"],
                             "my_rating": mine if mine is not None else -1, "avg_rating": avg})
        else:
            conn.execute("UPDATE images SET missing=1 WHERE id=?", (image_id,))
    conn.commit()
    keys = {"rating": lambda row: (-row["my_rating"], -row["id"]),
            "avg": lambda row: (row["avg_rating"] is None, -(row["avg_rating"] or 0), -row["id"]),
            "path": lambda row: (row["path"], row["id"]), "newest": lambda row: -row["id"], "oldest": lambda row: row["id"]}
    for allowed in [None, [library["id"]], []]:
        visible = [row for row in expected if allowed is None or row["library_id"] in allowed]
        for sort, key in keys.items():
            ordered = sorted(visible, key=key)
            for offset in [0, 97, 300]:
                page = ordered[offset:offset + 30]
                result = search.search_images(conn, alice["id"], allowed, sort=sort, limit=30, offset=offset)
                ids = search.search_images(conn, alice["id"], allowed, sort=sort, limit=30, offset=offset, ids_only=True)
                assert result["total"] == ids["total"] == len(visible)
                assert [item["id"] for item in result["items"]] == [item["id"] for item in page]
                assert ids["items"] == [{"id": item["id"]} for item in page]
                assert [(item["my_rating"], item["avg_rating"]) for item in result["items"]] == [
                    (item["my_rating"], item["avg_rating"]) for item in page]
            assert play.build_queue(conn, alice["id"], allowed, {"sort":sort, "limit":80}) == [row["id"] for row in ordered[:80]]


def test_playback_and_browsing_share_source_filters(conn, cfg, library, admin):
    indexer.scan_library(conn, cfg, library["id"])
    ids = [row[0] for row in conn.execute("SELECT id FROM images ORDER BY id")]
    ops.add_tags(conn, ids[0], ["Night sky"], admin["id"])
    tag = conn.execute("SELECT id FROM tags WHERE name='Night sky'").fetchone()[0]
    conn.execute("INSERT INTO tag_aliases(entity_id,name,match_key,created_at) VALUES(?,?,?,?)",
                 (tag, "Stars", "stars", db.utcnow()))
    conn.commit()
    ops.set_rating(conn, ids[0], admin["id"], 8)
    ops.set_favorite(conn, ids[0], admin["id"], True)
    sources = [{"tag":" STARS "}, {"library_id":library["id"], "favorites":True},
               {"rating_min":0, "workflow":"inbox"}, {"rating_min":8, "tag":"night sky"}]
    for source in sources:
        rows = catalog.query_images(conn, AccessContext(admin["id"]), catalog.ImageQuery(sort="path", **source))["items"]
        assert play.build_queue(conn, admin["id"], None, {"sort":"path", "source":source}) == [row["id"] for row in rows]


def test_name_filters_ranking_and_queue_match_independent_membership_sets(conn, library, alice, tmp_path):
    other_root = tmp_path / "other"
    other_root.mkdir()
    other = libsvc.create_library(conn, "Other", str(other_root))
    records = list(metadata_rows(60, library['id'])) + list(metadata_rows(30, other))
    fields = list(records[0])
    conn.executemany(f"INSERT INTO images({','.join(fields)}) VALUES({','.join('?' for _ in fields)})",
                     [list(row.values()) for row in records])
    conn.commit()
    sky = ops.ensure_tag(conn, 'Night sky')
    literal = ops.ensure_tag(conn, '100%_ready')
    artist = ops.create_artist(conn, 'Aster', aliases='Star author')['id']
    conn.execute('INSERT INTO tag_aliases(entity_id,name,match_key,created_at) VALUES(?,?,?,?)',
                 (sky, 'Stars', 'stars', db.utcnow()))
    conn.execute('INSERT INTO artist_aliases(entity_id,name,match_key,created_at) VALUES(?,?,?,?)',
                 (artist, 'Nova', 'nova', db.utcnow()))
    members = {'sky': set(), 'literal': set(), 'artist': set()}
    observations = []
    for iid, record in enumerate(records, 1):
        tags = []
        if iid % 2:
            members['sky'].add(iid); tags.append(sky)
        if iid % 3 == 0:
            members['literal'].add(iid); tags.append(literal)
        for tag in tags:
            conn.execute('INSERT INTO image_tags VALUES(?,?,?,?)', (iid, tag, alice['id'], db.utcnow()))
        if iid % 5:
            members['artist'].add(iid)
            conn.execute('INSERT INTO image_artists(image_id,artist_id) VALUES(?,?)', (iid, artist))
        rating = iid % 11 if iid % 4 else None
        if rating is not None:
            conn.execute('INSERT INTO ratings VALUES(?,?,?,?)', (iid, alice['id'], rating, db.utcnow()))
        missing = iid % 13 == 0
        if missing:
            conn.execute('UPDATE images SET missing=1 WHERE id=?', (iid,))
        observations.append({'id':iid, 'library_id':record['library_id'], 'path':record['rel_path'],
                             'missing':missing, 'rating':rating, 'tag_count':len(tags)})
    conn.commit()
    all_ids = set(range(1, len(records) + 1))
    cases = [('', all_ids), ('tag:" night   SKY "', members['sky']), ('tag:STARS', members['sky']),
             ('-tag:stars', all_ids - members['sky']), ('tag:stars,"100%_ready"', members['sky'] | members['literal']),
             ('tag:stars -tag:"100%_ready"', members['sky'] - members['literal']),
             ('artist:nova', members['artist']), ('artist:"STAR AUTHOR"', members['artist']),
             ('-artist:nova tag:stars', members['sky'] - members['artist']),
             ('artist:nova tag:"100%_ready"', members['artist'] & members['literal'])]
    keys = {'newest':lambda row:-row['id'], 'oldest':lambda row:row['id'],
            'path':lambda row:(row['path'], row['id']),
            'rating':lambda row:(-(row['rating'] if row['rating'] is not None else -1), -row['id']),
            'avg':lambda row:(row['rating'] is None, -(row['rating'] or 0), -row['id'])}
    for allowed in (None, [library['id']], []):
        visible = [row for row in observations if not row['missing'] and
                   (allowed is None or row['library_id'] in allowed)]
        for query, members in cases:
            for sort, key in keys.items():
                expected = sorted((row for row in visible if row['id'] in members), key=key)
                for offset in (0, 7, 200):
                    page = expected[offset:offset + 7]
                    result = search.search_images(conn, alice['id'], allowed, query=query, sort=sort, offset=offset, limit=7)
                    ids = search.search_images(conn, alice['id'], allowed, query=query, sort=sort, offset=offset, limit=7, ids_only=True)
                    assert result['errors'] == ids['errors'] == []
                    assert result['total'] == ids['total'] == len(expected)
                    assert [row['id'] for row in result['items']] == [row['id'] for row in page]
                    assert ids['items'] == [{'id':row['id']} for row in page]
                    assert [row['tag_count'] for row in result['items']] == [row['tag_count'] for row in page]
                assert play.build_queue(conn, alice['id'], allowed, {'query':query, 'sort':sort}) == [row['id'] for row in expected]
