"""Service/transport parity and permission changes with independent catalog data."""
import json

import pytest
from fastapi.testclient import TestClient

from picshelf import catalog, db, i18n
from picshelf.access import AccessContext
from picshelf.api import create_app
from picshelf.errors import ServiceError
from tests.datasets import CATALOG
from tests.scenario_support import build_catalog, cli


@pytest.fixture
def collection(tmp_path):
    return build_catalog(tmp_path)


def test_service_and_http_queries_follow_known_sets(collection):
    access = AccessContext(collection.users["reader"])
    with db.open_db(collection.cfg.db_path) as conn, TestClient(create_app(collection.cfg)) as client:
        assert client.post("/api/v1/auth/login", json={"username": "reader", "password": collection.password}).status_code == 200
        for case in CATALOG["queries"]:
            expected = {collection.images[key] for key in case["expected"]}
            direct = catalog.query_images(conn, access, catalog.ImageQuery(query=case["query"]))
            response = client.get("/api/v1/images", params={"q": case["query"]})
            assert response.status_code == 200
            for result in (direct, response.json()):
                assert {item["id"] for item in result["items"]} == expected
                assert result["error_codes"] == (["invalid_query"] if case.get("error") else [])


@pytest.mark.parametrize("language", ["zh", "en"])
def test_rating_service_http_and_cli_share_result_and_error_codes(collection, language):
    iid = collection.images["n01"]
    access = AccessContext(collection.users["reader"])
    token = i18n.push_lang(language)
    try:
        with db.open_db(collection.cfg.db_path) as conn, TestClient(create_app(collection.cfg)) as client:
            client.headers["Accept-Language"] = language
            assert client.post("/api/v1/auth/login", json={"username": "reader", "password": collection.password}).status_code == 200
            expected = {"my_rating": 0, "avg_rating": 0.0, "rating_count": 1}
            assert catalog.rate_image(conn, access, iid, 0) == expected
            response = client.post(f"/api/v1/images/{iid}/rating", json={"value": 0})
            assert response.status_code == 200 and response.json() == expected
            process = cli(collection.cfg, "rate", str(iid), "0", "--user", "reader", "--json", lang=language)
            assert process.returncode == 0
            assert json.loads(process.stdout) == expected
            with pytest.raises(ServiceError) as caught:
                catalog.rate_image(conn, access, iid, 11)
            response = client.post(f"/api/v1/images/{iid}/rating", json={"value": 11})
            assert response.status_code == 400
            process = cli(collection.cfg, "rate", str(iid), "11", "--json", lang=language)
            assert process.returncode == 1
            for failure in (caught.value.as_dict(), response.json(), json.loads(process.stdout)):
                assert failure["error"]["code"] == "invalid_rating"
                assert failure["error"]["retryable"] is False
                assert failure["error"]["objects"] == {"image_id": iid}
                assert failure["detail"] and failure["error"]["next_step"]
                if language == "en":
                    assert "Enter" in failure["error"]["next_step"]
    finally:
        i18n.pop_lang(token)


def test_reused_access_context_observes_revocation_and_disabled_account(collection):
    access = AccessContext(collection.users["reader"])
    with db.open_db(collection.cfg.db_path) as conn:
        assert catalog.query_images(conn, access, catalog.ImageQuery())["total"] == 12
        # Simulate an administrator revoking access after the request was prepared.
        conn.execute("DELETE FROM library_grants WHERE group_id IN "
                     "(SELECT group_id FROM user_groups WHERE user_id=?)", (access.user_id,))
        conn.commit()
        assert catalog.query_images(conn, access, catalog.ImageQuery())["total"] == 0
        with pytest.raises(ServiceError) as caught:
            catalog.rate_image(conn, access, collection.images["n01"], 9)
        assert caught.value.code == "permission_denied"
        assert conn.execute("SELECT value FROM ratings WHERE image_id=? AND user_id=?",
                            (collection.images["n01"], access.user_id)).fetchone()[0] == 0
        conn.execute("UPDATE users SET disabled=1 WHERE id=?", (access.user_id,))
        conn.commit()
        with pytest.raises(ServiceError) as caught:
            catalog.query_images(conn, access, catalog.ImageQuery())
        assert caught.value.code == "authentication_required"


def test_unauthorized_rating_never_writes_but_local_admin_keeps_compatibility(collection):
    iid = collection.images["s01"]
    with TestClient(create_app(collection.cfg)) as client:
        assert client.post("/api/v1/auth/login", json={"username": "reader", "password": collection.password}).status_code == 200
        denied = client.post(f"/api/v1/images/{iid}/rating", json={"value": 9})
        assert denied.status_code == 403 and denied.json()["error"]["code"] == "permission_denied"
    with db.open_db(collection.cfg.db_path) as conn:
        assert conn.execute("SELECT value FROM ratings WHERE image_id=?", (iid,)).fetchone()[0] == 0
    local = cli(collection.cfg, "rate", str(iid), "9", "--user", "reader", "--json")
    assert local.returncode == 0 and json.loads(local.stdout)["my_rating"] == 9


def test_missing_image_error_is_actionable_without_traceback(collection):
    result = cli(collection.cfg, "rate", "99999", "4", "--json")
    assert result.returncode == 1
    assert json.loads(result.stdout)["error"]["code"] == "image_not_found"
    assert "Traceback" not in result.stderr
