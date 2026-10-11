"""数据层回归测试：跨线程使用（FastAPI 依赖/端点线程池拆分场景）。"""
import threading

from picshelf import db as dbm


def test_connection_cross_thread_sequential_use(tmp_path):
    """依赖在 A 线程建连接、端点在 B 线程使用——必须允许（check_same_thread=False）。"""
    conn = dbm.open_db(tmp_path / "x.db")
    result = {}

    def use_in_other_thread():
        result["value"] = conn.execute("SELECT 1 AS v").fetchone()["v"]

    t = threading.Thread(target=use_in_other_thread)
    t.start()
    t.join()
    assert result["value"] == 1
    conn.close()


def test_migration_idempotent(tmp_path):
    from picshelf.config import Config

    cfg = Config(tmp_path / "data").ensure()
    conn = dbm.open_db(cfg.db_path)
    assert dbm.schema_version(conn) == dbm.SCHEMA_VERSION
    conn.close()
    conn2 = dbm.open_db(cfg.db_path)  # 二次打开不应重复建表或报错
    assert dbm.schema_version(conn2) == dbm.SCHEMA_VERSION
    tables = {r["name"] for r in conn2.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert {"users", "images", "play_sessions", "tags", "artists"} <= tables
    conn2.close()
