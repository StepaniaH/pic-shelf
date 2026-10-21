"""Album paging and large metadata batches, with deterministic membership checks."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import platform
import secrets
import sqlite3
import time

from picshelf import auth, batches, db, libsvc, ops
from picshelf.access import AccessContext
from picshelf.config import Config
from tests.benchmarks.catalog import measure
from tests.datasets import metadata_rows, SEED


def run(root, count=50000, rounds=30):
    cfg = Config(root / "data").ensure()
    originals = root / "originals"
    originals.mkdir()
    with closing(db.open_db(cfg.db_path)) as conn:
        auth.ensure_default_groups(conn)
        user = auth.create_user(conn,"benchmark",secrets.token_urlsafe(24),is_admin=True)
        access = AccessContext(user)
        library = libsvc.create_library(conn,"Generated metadata",str(originals))
        records = list(metadata_rows(count,library)); fields = list(records[0])
        conn.executemany(f"INSERT INTO images({','.join(fields)}) VALUES({','.join('?' for _ in fields)})",
                         [list(row.values()) for row in records])
        album_count = (count + 19) // 20; stamp = db.utcnow()
        conn.executemany("INSERT INTO albums(library_id,name,kind,created_at) VALUES(?,?,'manual',?)",
                         [(library,f"Album {n:05}",stamp) for n in range(album_count)])
        conn.executemany("INSERT INTO album_images(album_id,image_id,position,added_at) VALUES(?,?,0,?)",
                         [(n//20+1,n+1,stamp) for n in range(count)])
        conn.commit()
        result = {}
        for label, options in [("album_all",{}),("album_page",{"limit":101})]:
            rows = ops.list_albums(conn,**options)
            assert len(rows) == min(album_count,options.get("limit",album_count))
            assert [(row["id"],row["image_count"],row["preview_image_id"]) for row in rows] == [
                (n+1,min(20,count-n*20),n*20+1) for n in range(len(rows))]
            result[label] = {**measure(lambda:ops.list_albums(conn,**options),rounds),"returned":len(rows)}
        selected = min(10000,count)
        previewed = []
        def preview():
            value = batches.preview(conn,access,{"kind":"tags_add","names":["Batch review"]},
                                    query={"library_id":library,"query":"file:image-00","sort":"path"})
            assert value["total"] == selected
            previewed.append(value["id"])
        result["preview"] = measure(preview,rounds)
        statements = []; conn.set_trace_callback(statements.append)
        preview(); conn.set_trace_callback(None)
        result["preview"]["read_statements"] = sum(sql.lstrip().upper().startswith(("SELECT","WITH")) for sql in statements)
        result["preview"]["total_statements"] = len(statements)
        batch_id = previewed[-1]
        result["execute"] = measure(lambda:batches.execute(conn,cfg,access,batch_id),1)
        assert [r[0] for r in conn.execute("SELECT image_id FROM image_tags ORDER BY image_id")] == list(range(1,selected+1))
        for label, include in [("status_with_items",True),("status_summary",False)]:
            value = batches.get(conn,access,batch_id,include_items=include)
            assert value["counts"]["succeeded"] == selected
            result[label] = {**measure(lambda:batches.get(conn,access,batch_id,include_items=include),rounds),
                             "response_bytes":len(json.dumps(value,separators=(',',':')).encode())}
        result["undo"] = measure(lambda:batches.undo(conn,cfg,access,batch_id),1)
        assert conn.execute("SELECT COUNT(*) FROM image_tags").fetchone()[0] == 0
        assert batches.get(conn,access,batch_id,include_items=False)["counts"]["undone"] == selected
    return {"dataset":{"images":count,"albums":album_count,"selected":selected,"seed":SEED},
            "environment":{"python":platform.python_version(),"sqlite":sqlite3.sqlite_version,"system":platform.system(),"architecture":platform.machine()},
            "measurements":result,
            "limitations":["Generated metadata; no file decoding, HTTP or browser rendering", "OS caches were not flushed", "Execute and undo have one sample each"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--count",type=int,default=50000)
    parser.add_argument("--rounds",type=int,default=30)
    args = parser.parse_args()
    if not 1000 <= args.count <= 100000 or args.rounds < 5:
        parser.error("use 1,000–100,000 images and at least 5 rounds")
    report = run(args.output.parent / f"organization-{time.time_ns()}",args.count,args.rounds)
    args.output.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report["measurements"],indent=2))
