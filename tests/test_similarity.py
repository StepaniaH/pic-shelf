"""Candidate acceleration matches an independent first-representative algorithm."""
import random

import pytest

from picshelf import libsvc


def reference(values, threshold):
    groups = []
    for image_id, value, version in values:
        for group in groups:
            if group[0][2] == version and bin(group[0][1] ^ value).count('1') <= threshold:
                group.append((image_id, value, version))
                break
        else:
            groups.append([(image_id, value, version)])
    return [[(row[0], bin(group[0][1] ^ row[1]).count('1')) for row in group] for group in groups if len(group) > 1]


@pytest.mark.parametrize('threshold', [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 32, 64])
@pytest.mark.parametrize('distribution', ['uniform', 'same', 'duplicates', 'shared-segment', 'chain'])
def test_candidates_preserve_earliest_representative_and_distances(conn, library, threshold, distribution):
    rng = random.Random(20261013)
    values = []
    for number in range(320):
        value = (rng.getrandbits(64) if distribution == 'uniform' else
                 0 if distribution == 'same' else
                 rng.randrange(8) if distribution == 'duplicates' else
                 rng.getrandbits(48) << 16 if distribution == 'shared-segment' else
                 (0, 3, 15, 255, 0)[number % 5])
        version = 1 + number % 2
        image_id = conn.execute('INSERT INTO images(library_id,rel_path,filename,ext,size_bytes,mtime,imported_at,phash,phash_version) VALUES(?,?,?,?,0,0,?,?,?)',
                                (library['id'], f'{number}.png', f'{number}.png', 'png', '2026-01-01T00:00:00+00:00', f'{value:016x}', version)).lastrowid
        values.append((image_id, value, version))
    conn.commit()
    observed = libsvc.similar_dupes(conn, threshold=threshold)
    assert [[(item['id'], item['distance']) for item in group['images']] for group in observed] == reference(values, threshold)
    assert libsvc.similar_dupes(conn, allowed=[], threshold=threshold) == []


@pytest.mark.parametrize('threshold', range(1, 9))
def test_indexed_candidates_keep_boundary_matches_and_earliest_groups(conn, library, threshold):
    rng = random.Random(20261017)
    hashes = [(rng.getrandbits(64), 1 + n % 2) for n in range(400)]
    # Exercise exact-distance and just-outside candidates after the index is in
    # use, including changes in distinct bands and several changes in one band.
    for value, version in hashes[:40]:
        for distance in (threshold, threshold + 1):
            bits = rng.sample(range(64), distance)
            hashes.append((value ^ sum(1 << bit for bit in bits), version))
        hashes.append((value ^ ((1 << threshold) - 1), version))
    for version in (1, 2):
        hashes.extend((value, version) for value in (0, (1 << threshold) - 1, (1 << (2 * threshold)) - 1, 0))
    values = []
    for number, (value, version) in enumerate(hashes):
        iid = conn.execute('INSERT INTO images(library_id,rel_path,filename,ext,size_bytes,mtime,imported_at,phash,phash_version) '
                           'VALUES(?,?,?,?,0,0,?,?,?)',
                           (library['id'], f'{number}.png', f'{number}.png', 'png', '2026-01-01T00:00:00+00:00',
                            f'{value:016x}', version)).lastrowid
        values.append((iid, value, version))
    conn.commit()
    result = libsvc.similar_dupes(conn, allowed=[library['id']], threshold=threshold)
    assert [[(item['id'], item['distance']) for item in group['images']] for group in result] == reference(values, threshold)
