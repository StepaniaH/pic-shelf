"""Browser regressions. Run with PICSHELF_BROWSER_TESTS=1 and the browser extra."""
import os
import re
import secrets
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from picshelf import auth, db as dbm, indexer, libsvc, ops
from picshelf.api import create_app
from picshelf.config import Config
from tests.conftest import make_image

pytestmark = pytest.mark.skipif(
    os.environ.get("PICSHELF_BROWSER_TESTS") != "1", reason="opt-in browser suite",
)


@dataclass
class WebServer:
    url: str
    password: str = field(repr=False)
    import_source: Path
    import_target: Path


@pytest.fixture(scope="module")
def web_server(tmp_path_factory):
    import uvicorn
    root = tmp_path_factory.mktemp("web")
    cfg = Config(root / "data").ensure()
    conn = dbm.open_db(cfg.db_path)
    auth.ensure_default_groups(conn)
    password = secrets.token_urlsafe(24)
    uid = auth.create_user(conn, "browser-test", password, is_admin=True)
    library = root / "images"
    for n in range(125):
        make_image(library / f"image-{n:03}.png", (n * 2, 90, 130), size=(160, 100))
    library_id = libsvc.create_library(conn, "Gallery", str(library))
    indexer.scan_library(conn, cfg, library_id)
    iid = conn.execute("SELECT id FROM images ORDER BY id DESC LIMIT 1").fetchone()[0]
    ops.add_tags(conn, iid, ["色彩:蓝色"], uid)
    ops.add_comment(conn, iid, uid, "A sample comment")
    conn.close()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(create_app(cfg), log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(.02)
    assert server.started
    import_source = root / "incoming"
    import_target = root / "managed"
    import_target.mkdir()
    make_image(import_source / "first.png", (20, 100, 230), size=(50, 30))
    yield WebServer(f"http://127.0.0.1:{sock.getsockname()[1]}", password, import_source, import_target)
    server.should_exit = True
    thread.join(timeout=10)
    sock.close()


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        options = {"headless": True}
        if os.environ.get("PICSHELF_TEST_BROWSER"):
            options["executable_path"] = os.environ["PICSHELF_TEST_BROWSER"]
        browser = playwright.chromium.launch(**options)
        yield browser
        browser.close()


@pytest.fixture()
def page(browser):
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    yield page
    page.close()
    assert errors == []


def sign_in(page, web_server, language="en"):
    page.add_init_script(f"localStorage.setItem('picshelf.lang', '{language}')")
    page.goto(web_server.url)
    page.locator("#login-username").wait_for(state="visible")
    page.locator("#login-username").fill("browser-test")
    page.locator("#login-password").fill(web_server.password)
    page.locator("#login-form button").click()
    page.locator(".lib-card").first.wait_for()


def test_group_editor_changes_only_the_selected_membership(page, web_server):
    from playwright.sync_api import expect
    sign_in(page, web_server)
    base = web_server.url + '/api/v1'
    headers = {'Origin': web_server.url}
    admin_id = page.request.get(base + '/auth/me').json()['id']
    created = page.request.post(base + '/users', headers=headers,
                                data={'username': 'group-member', 'password': secrets.token_urlsafe(24)}).json()
    uid = created['id']
    original = {group['id'] for group in created['groups']}
    group = page.request.post(base + '/groups', headers=headers,
                              data={'name': 'Review group', 'capabilities': ['view']}).json()
    gid = group['id']
    assert page.request.put(f'{base}/groups/{gid}/members/{admin_id}', headers=headers).status == 200
    page.goto(web_server.url + '/#/admin?tab=groups')
    card = page.locator(f'[data-gid="{gid}"]')
    card.locator('.add-user').fill('group-member')
    card.locator('[data-add-user]').click()
    expect(card.locator(f'[data-rm-user="{uid}"]')).to_be_visible()
    current = next(user for user in page.request.get(base + '/users').json() if user['id'] == uid)
    assert {group['id'] for group in current['groups']} == original | {gid}
    assert 'admins' not in {group['name'] for group in current['groups']}
    card.locator(f'[data-rm-user="{uid}"]').click()
    expect(card.locator(f'[data-rm-user="{uid}"]')).to_have_count(0)
    current = next(user for user in page.request.get(base + '/users').json() if user['id'] == uid)
    assert {group['id'] for group in current['groups']} == original


def test_condition_editor_preserves_or_and_literal_commas(page, web_server):
    from playwright.sync_api import expect
    sign_in(page, web_server)
    page.goto(web_server.url + '/#/search')
    page.locator('.query-editor summary').click()
    page.locator('[name="field"]').select_option('ext')
    page.locator('[name="value"]').fill('png,jpg')
    page.locator('[data-add]').click()
    expect(page.locator('[name="expression"]')).to_have_value('ext:"png","jpg"')
    page.locator('[name="field"]').select_option('tag')
    page.locator('[name="value"]').fill('"comma,name",night sky')
    page.locator('[data-add]').click()
    expect(page.locator('[name="expression"]')).to_have_value('ext:"png","jpg" tag:"comma,name","night sky"')
    expect(page.locator('[data-rule-error]')).to_be_empty()


def test_random_viewer_stops_at_sample_boundary(page, web_server):
    from urllib.parse import parse_qs, urlsplit
    from playwright.sync_api import expect
    sign_in(page, web_server)
    page.goto(web_server.url + '/#/search?sort=random')
    cards = page.locator('#grid .card')
    expect(cards).to_have_count(120)
    expect(page.locator('#more-btn')).to_be_hidden()
    requests = []
    page.on('request', lambda request: requests.append(request.url))
    name = cards.last.locator('.fname').text_content()
    cards.last.click()
    expect(page.locator('.viewer-stage img')).to_have_attribute('alt', name)
    expect(page.locator('.nav-btn.next')).to_be_disabled()
    expect(page.locator('.meta-count')).to_contain_text('Sample 120')
    page.keyboard.press('ArrowRight')
    page.keyboard.press('ArrowRight')
    expect(page.locator('.viewer-stage img')).to_have_attribute('alt', name)
    page.locator('#v-close').click()
    assert not any(int(parse_qs(urlsplit(url).query).get('offset', ['0'])[0]) > 0
                   for url in requests if urlsplit(url).path == '/api/v1/images')


def test_import_executes_saved_preview_and_requires_new_preview_after_option_change(page, web_server):
    from playwright.sync_api import expect
    sign_in(page, web_server)
    page.goto(web_server.url + "/#/admin?tab=libraries")
    page.locator("#nl-name").fill("Managed import test")
    page.locator("#nl-path").fill(str(web_server.import_target))
    page.locator("#nl-mode").select_option("managed")
    page.locator("#nl-default").select_option("copy")
    page.locator("#nl-create").click()
    expect(page.locator(".grant-item").filter(has_text="Managed import test")).to_be_visible()
    page.locator('[data-tab="import"]').click()
    page.locator("#imp-src").fill(str(web_server.import_source))
    page.locator("#imp-lib").select_option(label="Managed import test (0 images)")
    expect(page.locator("#imp-mode")).to_have_value("copy")
    page.locator("#imp-plan").click()
    expect(page.locator("#imp-exec")).to_be_enabled()
    page.locator("#imp-mode").select_option("move")
    expect(page.locator("#imp-exec")).to_be_disabled()
    page.locator("#imp-mode").select_option("copy")
    page.locator("#imp-plan").click()
    expect(page.locator("#imp-exec")).to_be_enabled()
    make_image(web_server.import_source / "later.png", (80, 160, 240), size=(50, 30))
    page.locator("#imp-exec").click()
    page.locator(".modal-body .btn.danger").click()
    expect(page.locator("#imp-out")).to_contain_text("Scanned: added 1 image(s).")
    assert (web_server.import_target / "first.png").read_bytes() == (web_server.import_source / "first.png").read_bytes()
    assert not (web_server.import_target / "later.png").exists()
    page.locator('[data-tab="jobs"]').click()
    row = page.locator("tr[data-job]").filter(has_text="Import")
    expect(row).to_contain_text("Succeeded")
    row.locator("[data-view]").click()
    expect(page.locator(".job-progress")).to_contain_text("Processed 1/1")


def test_empty_catalog_guides_server_path_registration_scan_and_tasks(page, tmp_path):
    from playwright.sync_api import expect
    from tests.scenario_support import http_server
    cfg = Config(tmp_path / "data").ensure()
    with dbm.open_db(cfg.db_path) as conn:
        auth.ensure_default_groups(conn)
        password = secrets.token_urlsafe(24)
        auth.create_user(conn, "browser-test", password, is_admin=True)
    root = tmp_path / "originals"
    make_image(root / "only.png", (5, 90, 130), size=(80, 60))
    original = (root / "only.png").read_bytes()
    with http_server(cfg) as client:
        sign_in(page, WebServer(str(client.base_url), password, root, root))
        expect(page.get_by_role("heading", name="Get started")).to_be_visible()
        page.locator('a[href="#/admin?tab=libraries"]').click()
        expect(page.get_by_text("Local browser files are not uploaded.", exact=False)).to_be_visible()
        page.locator("#nl-name").fill("Read in place")
        page.locator("#nl-path").fill(str(root))
        expect(page.locator("#nl-mode")).to_have_value("indexed")
        page.locator("#nl-create").click()
        library = page.locator(".grant-item").filter(has_text="Read in place")
        library.locator("[data-scan]").click()
        expect(library.locator("[data-scan]")).to_be_enabled(timeout=10000)
        library.get_by_role("link", name="Read in place", exact=True).click()
        expect(page.locator("#grid .card")).to_have_count(1)
        assert "-d2" in page.locator("#grid .card img").get_attribute("src")
        page.goto(str(client.base_url) + "/#/admin?tab=jobs")
        expect(page.locator("tr[data-job]").filter(has_text="Scan")).to_contain_text("Succeeded")
    assert (root / "only.png").read_bytes() == original


def test_viewer_organization_and_provenance_keep_literal_text(page, web_server):
    from playwright.sync_api import expect
    sign_in(page, web_server)
    page.locator('.lib-card a[href="#/library/1"]').click()
    page.locator("#grid .card").first.click()
    expect(page.locator("[data-workflow]")).to_have_attribute("data-workflow", "done")
    page.locator("[data-workflow]").click()
    expect(page.locator("[data-workflow]")).to_have_attribute("data-workflow", "inbox")
    note = '<img src=x onerror="window.unexpectedMetadataScript=true">'
    form = page.locator("[data-source-form]")
    form.locator('[name="note"]').fill(note)
    form.locator('[name="collected_at"]').fill("2026-10-10T21:35:00+08:00")
    form.locator('button[type="submit"]').click()
    expect(page.get_by_text(note, exact=True)).to_be_visible()
    expect(page.get_by_text("Collected 2026-10-10 13:35:00 UTC", exact=False)).to_be_visible()
    assert page.evaluate("window.unexpectedMetadataScript === undefined")
    page.locator("#v-close").click()


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("width", [390, 1280])
def test_gallery_viewer_and_nested_dialogs(page, web_server, language, width):
    from playwright.sync_api import expect
    page.set_viewport_size({"width": width, "height": 900})
    sign_in(page, web_server, language)
    assert page.locator("#nav").is_visible()
    page.locator('.lib-card a[href="#/library/1"]').click()
    expect(page.locator("#grid .card")).to_have_count(120)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    if destination := os.environ.get("PICSHELF_TEST_SCREENSHOTS"):
        output = Path(destination)
        output.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(output / f"gallery-{language}-{width}.png"))
    page.locator("#more-btn").click()
    expect(page.locator("#grid .card")).to_have_count(125)
    page.locator("#grid .card").first.click()
    if width < 900:
        page.locator("#v-panel").click()
    expect(page.locator("[data-untag]")).to_have_count(1)
    page.locator("#tag-input").fill("色彩")
    expect(page.locator('#tag-dl option[value="色彩:蓝色"]')).to_have_count(1)
    page.locator("#tag-input").fill("Browser test")
    page.locator("#tag-add").click()
    expect(page.locator('[data-untag="Browser test"]')).to_be_visible()
    page.locator('[data-untag="Browser test"]').click()
    expect(page.locator('[data-untag="Browser test"]')).to_have_count(0)
    if destination:
        page.screenshot(path=str(output / f"viewer-{language}-{width}.png"))
    # Zero is a rating, distinct from clearing a rating.
    page.locator('[data-rate="0"]').click()
    expect(page.locator('[data-rate="0"]')).to_have_class("rate-btn on")
    page.locator('[data-rate="clear"]').click()
    expect(page.locator(".rate-btn.on")).to_have_count(0)
    page.locator("#v-zoom").click()
    page.locator("#v-zoom").click()
    expect(page.locator(".viewer-stage img")).to_have_attribute("src", re.compile("/media/original/"))
    page.locator("[data-del-comment]").click()
    expect(page.locator(".modal-backdrop")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator(".modal-backdrop")).to_have_count(0)
    expect(page.locator(".viewer-backdrop")).to_be_visible()
    expect(page.locator("[data-del-comment]")).to_have_count(1)
    page.locator("#v-slideshow").click()
    page.locator(".modal-body [data-close]").click()
    expect(page.locator(".modal-backdrop")).to_have_count(0)
    page.locator("#v-slideshow").click()
    page.locator("#ss-size").select_option("original")
    page.locator("#ss-start").click()
    expect(page.locator("#ss-img")).to_have_attribute("src", re.compile("/media/original/"))
    page.keyboard.press("Escape")
    expect(page.locator(".slideshow-backdrop")).to_have_count(0)
    expect(page.locator(".viewer-backdrop")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator(".viewer-backdrop")).to_have_count(0)
    expect(page.locator("#grid .card").first).to_be_focused()


def test_modal_close_settles_prompt_and_restores_focus(page, web_server):
    from playwright.sync_api import expect
    sign_in(page, web_server)
    page.evaluate("""async () => {
      const { uiPrompt } = await import('/js/app.js');
      window.promptResult = 'pending';
      uiPrompt('Name').then(value => window.promptResult = value);
    }""")
    expect(page.locator(".prompt-form input")).to_be_focused()
    page.locator(".modal-head [data-close]").click()
    page.wait_for_function("window.promptResult === null")


def test_unsafe_artist_link_is_not_rendered(page, web_server):
    from playwright.sync_api import expect
    sign_in(page, web_server)
    page.route("**/api/v1/artists/999", lambda route: route.fulfill(json={
        "id": 999, "name": "Link test", "links": ["javascript:alert(1)", "https://example.org"],
    }))
    page.goto(web_server.url + "/#/artist/999")
    expect(page.locator('a[href="https://example.org/"]')).to_be_visible()
    expect(page.locator('a[href^="javascript:"]')).to_have_count(0)


def test_viewer_ignores_late_response_for_previous_image(page, web_server):
    from playwright.sync_api import expect
    sign_in(page, web_server)
    page.locator('.lib-card a[href="#/library/1"]').click()
    expect(page.locator("#grid .card")).to_have_count(120)
    expected = page.locator("#grid .card .fname").nth(1).text_content()
    first_id = page.locator("#grid .card").first.get_attribute("data-id")
    page.evaluate("""id => {
      const original = window.fetch;
      window.fetch = async (...args) => {
        const response = await original(...args);
        if (String(args[0]).endsWith('/images/' + id)) {
          const body = await response.text();
          window.slowResponseReady = true;
          await new Promise(resolve => { window.releaseSlowResponse = resolve; });
          window.slowResponseFinished = true;
          return new Response(body, {status: response.status, headers: response.headers});
        }
        return response;
      };
    }""", first_id)
    page.locator("#grid .card").first.click()
    page.wait_for_function("window.slowResponseReady === true")
    page.keyboard.press("ArrowRight")
    expect(page.locator(".viewer-stage img")).to_have_attribute("alt", expected)
    page.evaluate("window.releaseSlowResponse()")
    page.wait_for_function("window.slowResponseFinished === true")
    expect(page.locator(".viewer-stage img")).to_have_attribute("alt", expected)
    expect(page.locator(".viewer-panel .kv")).to_contain_text(expected)
    expect(page.locator("[data-untag]")).to_have_count(0)


def test_concurrent_load_more_does_not_duplicate_images(page, web_server):
    sign_in(page, web_server)
    result = page.evaluate("""async () => {
      const { Source } = await import('/js/views.js');
      const source = new Source('Test', {library_id: 1}, { sort: 'path', filters: {library_id: 1} });
      await source.load();
      await Promise.all([source.loadMore(), source.loadMore(), source.loadMore()]);
      return { total: source.total, loaded: source.items.length,
        unique: new Set(source.items.map(item => item.id)).size };
    }""")
    assert result == {"total": 125, "loaded": 125, "unique": 125}


def test_reader_view_is_read_only_and_logout_removes_private_media_access(page,tmp_path):
    from playwright.sync_api import expect
    from tests.scenario_support import build_catalog,http_server
    catalog = build_catalog(tmp_path)
    with http_server(catalog.cfg) as client:
        page.add_init_script("localStorage.setItem('picshelf.lang','en')")
        page.goto(str(client.base_url))
        page.locator("#login-username").fill("reader")
        page.locator("#login-password").fill(catalog.password)
        page.locator("#login-form button").click()
        expect(page.locator(".lib-card")).to_have_count(1)
        page.locator('.lib-card a[href^="#/library/"]').click()
        expect(page.locator("#grid .card")).to_have_count(12)
        page.locator("#grid .card").first.click()
        expect(page.locator(".viewer-stage img")).to_be_visible()
        expect(page.locator("#tag-input")).to_have_count(0)
        expect(page.locator("#artist-input")).to_have_count(0)
        media_url = page.locator(".viewer-stage img").get_attribute("src")
        page.keyboard.press("Escape")
        page.locator("#logout-btn").click()
        expect(page.locator("#login-form")).to_be_visible()
        # A new request in the same browser context cannot reuse a cached private response.
        result = page.evaluate("async url => {const response = await fetch(url); return response.status;}",media_url)
        assert result == 401


def test_cross_page_selection_preview_undo_and_later_edit_conflict(page,tmp_path):
    from playwright.sync_api import expect
    from tests.scenario_support import build_catalog,http_server
    catalog=build_catalog(tmp_path)
    with dbm.open_db(catalog.cfg.db_path) as conn:
        for n in range(121):make_image(catalog.roots/"north"/f"batch-{n:03}.png",(n,70,190))
        indexer.scan_library(conn,catalog.cfg,catalog.libraries["north"])
        conn.execute("UPDATE library_grants SET access='edit' WHERE group_id IN (SELECT group_id FROM user_groups WHERE user_id=?)",(catalog.users["reader"],));conn.commit()
    with http_server(catalog.cfg) as client:
        page.add_init_script("localStorage.setItem('picshelf.lang','en')")
        page.goto(str(client.base_url))
        page.locator("#login-username").fill("reader");page.locator("#login-password").fill(catalog.password)
        page.locator("#login-form button").click()
        page.locator('.lib-card a[href^="#/library/"]').click()
        expect(page.locator("#grid .card")).to_have_count(120)
        first=int(page.locator("[data-select-image]").first.get_attribute("data-select-image"))
        page.locator("[data-select-image]").first.check()
        page.locator("#more-btn").click()
        expect(page.locator("#grid .card")).to_have_count(133)
        last=int(page.locator("[data-select-image]").last.get_attribute("data-select-image"))
        page.locator("[data-select-image]").last.check()
        page.locator("[data-batch-names]").fill("Web selected only")
        page.locator("[data-batch-preview]").click()
        expect(page.locator("[data-batch-review]")).to_contain_text("Preview fixed 2 images")
        page.locator("[data-batch-apply]").click()
        expect(page.locator("[data-batch-status]")).to_contain_text("changed 2")
        with dbm.open_db(catalog.cfg.db_path) as conn:
            observed={r[0] for r in conn.execute("SELECT it.image_id FROM image_tags it JOIN tags t ON t.id=it.tag_id WHERE t.name='Web selected only'")}
            assert observed=={first,last}
            ops.add_tags(conn,first,["Later manual annotation"],catalog.users["curator"])
        page.locator("[data-batch-undo]").click();page.locator("[data-yes]").click()
        expect(page.locator("[data-batch-status]")).to_contain_text("Undo has conflicts")
        with dbm.open_db(catalog.cfg.db_path) as conn:
            assert {r[0] for r in conn.execute("SELECT it.image_id FROM image_tags it JOIN tags t ON t.id=it.tag_id WHERE t.name='Web selected only'")}=={first}
            assert conn.execute("SELECT 1 FROM image_tags it JOIN tags t ON t.id=it.tag_id WHERE it.image_id=? AND t.name='Later manual annotation'",(first,)).fetchone()


def test_saved_smart_search_updates_members_and_repairs_invalid_rule(page,tmp_path):
    from playwright.sync_api import expect
    from tests.scenario_support import build_catalog,http_server
    catalog=build_catalog(tmp_path)
    with http_server(catalog.cfg) as client:
        page.add_init_script("localStorage.setItem('picshelf.lang','en')")
        page.goto(str(client.base_url))
        page.locator('#login-username').fill('reader');page.locator('#login-password').fill(catalog.password)
        page.locator('#login-form button').click()
        expect(page.locator('.lib-card')).to_have_count(1)
        page.locator('#search-input').fill('rated:false')
        page.locator('#search-input').press('Enter')
        expect(page.locator('#grid .card')).to_have_count(1)
        expect(page.locator('#grid .fname')).to_have_text('02 unrated.png')
        page.locator('.query-editor summary').click()
        page.locator('.query-editor [name=name]').fill('Unrated collection')
        page.locator('.query-editor [name=kind]').select_option('smart')
        page.locator('[data-save]').click()
        expect(page.locator('h1')).to_have_text('Unrated collection')
        with dbm.open_db(catalog.cfg.db_path) as conn:
            ops.set_rating(conn,catalog.images['n03'],catalog.users['reader'],None)
        page.locator('[data-reload-rule]').click()
        expect(page.locator('#grid .card')).to_have_count(2)
        page.locator('.query-editor summary').click()
        page.locator('.query-editor [name=expression]').fill('tag:')
        page.locator('[data-save]').click()
        expect(page.locator('[data-rule-error]')).to_contain_text('Invalid search condition')
        page.locator('.query-editor [name=expression]').fill('rated:true rating:0')
        page.locator('[data-save]').click()
        expect(page.locator('#grid .card')).to_have_count(1)
        expect(page.locator('#grid .fname')).to_have_text('01 zero.png')
        with dbm.open_db(catalog.cfg.db_path) as conn:
            conn.execute('DELETE FROM library_grants');conn.commit()
        page.locator('[data-reload-rule]').click()
        expect(page.locator('#grid .card')).to_have_count(0)


def test_play_rule_pause_bad_image_skip_and_resume_cursor(page,tmp_path):
    from playwright.sync_api import expect
    from tests.scenario_support import build_catalog,http_server
    catalog=build_catalog(tmp_path)
    with http_server(catalog.cfg) as client:
        page.add_init_script("localStorage.setItem('picshelf.lang','en')")
        page.goto(str(client.base_url))
        page.locator('#login-username').fill('reader');page.locator('#login-password').fill(catalog.password)
        page.locator('#login-form button').click()
        page.locator('.lib-card a[href^="#/library/"]').click()
        page.locator('#slideshow-btn').click()
        page.locator('#ss-interval').fill('1');page.locator('#ss-count').fill('3')
        page.locator('#ss-sort').select_option('path');page.locator('#ss-loop').uncheck()
        page.locator('#ss-name').fill('Reader slides');page.locator('#ss-save').click()
        expect(page.locator('.toast').last).to_contain_text('Rule saved')
        page.locator('#ss-start').click()
        expect(page.locator('#ss-img')).to_have_attribute('alt','01 zero.png')
        page.wait_for_function("document.querySelector('#ss-img').naturalWidth > 0")
        page.locator('#ss-pause').click()
        expect(page.locator('#ss-pause')).to_contain_text('Resume')
        page.wait_for_timeout(1400)
        expect(page.locator('#ss-img')).to_have_attribute('alt','01 zero.png')
        page.route(f"**/api/v1/media/preview/{catalog.images['n02']}?*",lambda route:route.abort())
        page.locator('#ss-next').click()
        expect(page.locator('#ss-status')).to_contain_text('next image to skip')
        page.locator('#ss-next').click()
        expect(page.locator('#ss-img')).to_have_attribute('alt','03 one.png')
        page.locator('#ss-exit').click()
        page.locator('a[href="#/play-rules"]').click()
        page.locator('[data-resume]').click()
        expect(page.locator('#ss-img')).to_have_attribute('alt','03 one.png')
        expect(page.locator('#ss-pause')).to_contain_text('Resume')
        page.wait_for_timeout(1400)
        expect(page.locator('#ss-img')).to_have_attribute('alt','03 one.png')
        with dbm.open_db(catalog.cfg.db_path) as conn:
            conn.execute('DELETE FROM library_grants');conn.commit()
        page.locator('#ss-next').click()
        expect(page.locator('#ss-img')).to_be_hidden()
        expect(page.locator('#ss-caption')).to_have_text('')
        page.locator('#ss-exit').click()


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("width", [360, 390, 768, 1280, 1920])
@pytest.mark.parametrize("text_scale", [1, 2])
def test_browse_layout_touch_targets_and_text_scaling(page, web_server, language, width, text_scale):
    """Measure actual layout; root text scaling is not a hardware/browser zoom check."""
    from playwright.sync_api import expect
    page.set_viewport_size({"width": width, "height": 900})
    sign_in(page, web_server, language)
    page.evaluate("scale => document.documentElement.style.fontSize = `${16 * scale}px`", text_scale)
    page.locator('#nav a[href="#/search"]').click()
    expect(page.locator("#grid .card")).to_have_count(120)
    expect(page.locator("details.batch-controls")).not_to_have_attribute("open", "")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert page.locator("#search-input").evaluate("el => parseFloat(getComputedStyle(el).fontSize) >= 16")
    for selector in ["#lang-btn", "#slideshow-btn", "#density-select", ".card-selection"]:
        box = page.locator(selector).first.bounding_box()
        assert box["width"] >= 44 and box["height"] >= 44, (selector, box)
    assert page.locator("#grid img").first.evaluate("el => getComputedStyle(el).objectFit") == "contain"
    page.locator("#density-select").select_option("compact")
    page.locator("[data-select-image]").first.check()
    expect(page.locator("[data-batch-preview]")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    if destination := os.environ.get("PICSHELF_TEST_SCREENSHOTS"):
        output = Path(destination)
        output.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(output / f"browse-{language}-{width}-text{text_scale}.png"))
    page.reload()
    expect(page.locator("#density-select")).to_have_value("compact")


def test_route_offline_error_keeps_login_and_can_retry(page, web_server):
    from playwright.sync_api import expect
    sign_in(page, web_server)
    page.route("**/api/v1/images?*", lambda route: route.abort("internetdisconnected"))
    page.locator('#nav a[href="#/search"]').click()
    expect(page.get_by_role("heading", name="Unable to load this page")).to_be_visible()
    expect(page.locator("#logout-btn")).to_be_visible()
    page.unroute("**/api/v1/images?*")
    page.locator("[data-route-retry]").click()
    expect(page.locator("#grid .card")).to_have_count(120)


def test_viewer_failed_pixels_and_saves_keep_context_and_inputs(page, web_server):
    from playwright.sync_api import expect
    sign_in(page, web_server)
    page.locator('#nav a[href="#/search"]').click()
    cards = page.locator("#grid .card")
    expect(cards).to_have_count(120)
    first = cards.first.get_attribute("data-id")
    second_name = cards.nth(1).locator(".fname").text_content()
    page.route(f"**/media/preview/{first}?*", lambda route: route.abort("failed"))
    cards.first.click()
    expect(page.locator(".viewer-status")).to_contain_text("could not be displayed")
    expect(page.locator(".viewer-stage img")).to_have_count(0)
    expect(page.locator(".viewer-panel .kv")).to_have_count(0)
    page.locator(".nav-btn.next").click()
    expect(page.locator(".viewer-stage img")).to_have_attribute("alt", second_name)
    expect(page.locator(".viewer-panel .kv")).to_contain_text(second_name)
    page.unroute(f"**/media/preview/{first}?*")
    draft = "A comment to keep through a failed save"
    page.locator("#comment-input").fill(draft)
    page.route("**/images/*/comments", lambda route: route.abort("internetdisconnected"))
    page.locator("#comment-add").click()
    expect(page.locator(".viewer-status")).to_contain_text("unconfirmed")
    expect(page.locator("#comment-input")).to_have_value(draft)
    page.unroute("**/images/*/comments")
    page.locator("[data-rate='0']").click()
    expect(page.locator("#comment-input")).to_have_value(draft)
    page.locator("#v-close").click()
    expect(page.locator(".modal-body [data-no]")).to_be_focused()
    page.keyboard.press("Escape")
    expect(page.locator("#comment-input")).to_have_value(draft)
    page.locator("#comment-add").click()
    expect(page.locator("#comment-list")).to_contain_text(draft)
    expect(page.locator("#comment-input")).to_have_value("")
    page.locator("#v-close").click()
    expect(cards.first).to_be_focused()


def test_viewer_range_scroll_focus_and_ime_preserve_browsing(page, web_server):
    from playwright.sync_api import expect
    sign_in(page, web_server)
    page.locator('#nav a[href="#/search"]').click()
    page.locator("[data-select-image]").nth(2).click()
    page.locator("[data-select-image]").nth(5).click(modifiers=["Shift"])
    expect(page.locator("[data-select-image]:checked")).to_have_count(4)
    card = page.locator("#grid .card").nth(60)
    card.scroll_into_view_if_needed()
    scroll = page.evaluate("scrollY")
    handle = card.element_handle()
    requests = []
    page.on("request", lambda request: requests.append(request.url))
    card.locator("img").click()
    page.locator("#tag-input").fill("中文输入")
    page.locator("#tag-input").evaluate("el => el.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter',isComposing:true,bubbles:true}))")
    expect(page.locator("#tag-input")).to_have_value("中文输入")
    expect(page.locator("[data-untag='中文输入']")).to_have_count(0)
    page.locator("#tag-input").fill("")
    page.keyboard.press("Tab")
    assert page.evaluate("document.querySelector('.viewer-backdrop').contains(document.activeElement)")
    page.keyboard.press("Escape")
    expect(card).to_be_focused()
    assert handle.evaluate("el => el.isConnected")
    assert abs(page.evaluate("scrollY") - scroll) < 2
    expect(page.locator("[data-select-image]:checked")).to_have_count(4)
    assert not any("/media/original/" in url for url in requests)
