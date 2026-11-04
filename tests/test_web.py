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

pytestmark = [pytest.mark.browser, pytest.mark.skipif(
    os.environ.get("PICSHELF_BROWSER_TESTS") != "1", reason="opt-in browser suite",
)]


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
    page.locator("#view .page-head h1").wait_for()


def navigate_from_sidebar(page, href):
    if page.locator("#nav-toggle").is_visible():
        page.locator("#nav-toggle").click()
    page.locator(f'#nav a[href="{href}"]').click()


def show_viewer_info(page):
    viewer = page.locator(".viewer-backdrop").last
    if viewer.locator("#viewer-details").is_hidden():
        viewer.locator("#v-panel").click()
    return viewer


def viewer_editor(page, name):
    viewer = show_viewer_info(page)
    editor = viewer.locator(f'[data-section="{name}"]')
    if editor.get_attribute("open") is None:
        editor.locator("summary").click()
    return viewer


def viewer_more(page):
    viewer = page.locator(".viewer-backdrop").last
    if viewer.locator(".viewer-more").get_attribute("open") is None:
        viewer.locator(".viewer-more > summary").click()
    return viewer


def rate_viewer(page, value):
    from playwright.sync_api import expect
    slider = show_viewer_info(page).locator("#v-rating")
    box = slider.bounding_box()
    slider.click(position={"x":8 + (box["width"] - 16) * int(value) / 10, "y":box["height"] / 2})
    expect(slider).to_have_value(str(value))
    expect(page.locator("#v-rating-clear")).to_be_enabled()


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
    page.locator('.query-editor > summary').click()
    page.locator('[name="field"]').select_option('fav')
    expect(page.locator('[data-field-hint]')).to_be_hidden()
    page.locator('[name="field"]').select_option('ext')
    expect(page.locator('[data-field-hint]')).to_be_visible()
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
    expect(page.locator('#v-next')).to_be_disabled()
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
    page.locator("[data-new-library]").click()
    page.locator("#nl-name").fill("Managed import test")
    page.locator('[data-library-options] > summary').click()
    page.locator("#nl-path").fill(str(web_server.import_target))
    page.locator("#nl-mode").select_option("managed")
    original_url = page.url
    page.locator("#imp-src").fill(str(web_server.import_source))
    expect(page.locator("#imp-mode")).to_have_value("hardlink")
    page.locator("#imp-mode").select_option("copy")
    page.locator("#nl-create").click()
    expect(page.locator("#imp-exec")).to_be_enabled()
    page.locator("#imp-change").click()
    page.locator("#imp-mode").select_option("move")
    expect(page.locator("#imp-exec")).to_be_disabled()
    page.locator("#imp-mode").select_option("copy")
    page.locator("#nl-create").click()
    expect(page.locator("#imp-exec")).to_be_enabled()
    make_image(web_server.import_source / "later.png", (80, 160, 240), size=(50, 30))
    page.locator("#imp-exec").click()
    expect(page.locator("#imp-out")).to_contain_text("Files imported: 1")
    expect(page.locator("#imp-out")).not_to_contain_text("failed 0")
    expect(page.locator("#imp-exec")).to_be_hidden()
    expect(page.locator("#imp-browse")).to_be_visible()
    assert (web_server.import_target / "first.png").read_bytes() == (web_server.import_source / "first.png").read_bytes()
    assert not (web_server.import_target / "later.png").exists()
    assert page.url == original_url
    page.locator('#imp-browse').click()
    page.locator('[data-tab="jobs"]').click()
    row = page.locator("tr[data-job]").filter(has_text="Import")
    expect(row).to_contain_text("Succeeded")
    job_id = row.get_attribute("data-job")
    row.locator("[data-view]").click()
    expect(page.locator(".job-progress")).to_contain_text("Processed 1/1")
    expect(page.locator(".job-progress [role=status]")).not_to_contain_text("failed 0")
    expect(page.locator(".job-progress details p")).to_be_hidden()
    page.locator(".job-progress summary").click()
    expect(page.locator(".job-progress details p")).to_contain_text(job_id)


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
        expect(page.get_by_role("heading", name="No libraries yet")).to_be_visible()
        page.locator('[data-new-library]').first.click()
        page.locator('[data-library-options] > summary').click()
        page.locator('#nl-mode').select_option('indexed')
        expect(page.locator('#library-path-hint')).to_contain_text('For Docker, use the path inside the container.')
        page.locator("#nl-name").fill("Read in place")
        page.locator("#nl-path").fill(str(root))
        expect(page.locator("#nl-mode")).to_have_value("indexed")
        page.locator("#nl-create").click()
        expect(page.locator("#grid .card")).to_have_count(1, timeout=10000)
        assert page.url.endswith('#/home')
        page.locator('.library-title').click()
        expect(page.locator("#lib-scan")).to_be_enabled()
        assert "-d3" in page.locator("#grid .card img").get_attribute("src")
        page.goto(str(client.base_url) + "/#/admin?tab=jobs")
        expect(page.locator("tr[data-job]").filter(has_text="Scan")).to_contain_text("Succeeded")
    assert (root / "only.png").read_bytes() == original


def test_viewer_organization_and_provenance_keep_literal_text(page, web_server):
    from playwright.sync_api import expect
    sign_in(page, web_server)
    page.locator('.lib-card a[href="#/library/1"]').click()
    page.locator("#grid .card").first.click()
    expect(page.locator("[data-workflow]")).to_have_attribute("data-workflow", "done")
    show_viewer_info(page).locator("[data-workflow]").click()
    expect(page.locator("[data-workflow]")).to_have_attribute("data-workflow", "inbox")
    note = '<img src=x onerror="window.unexpectedMetadataScript=true">'
    show_viewer_info(page).locator('[data-section="sources"] > summary').click()
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
    if width < 961:
        expect(page.locator("#nav")).to_be_hidden()
        page.locator("#nav-toggle").click()
        expect(page.locator("#nav")).to_be_visible()
        page.keyboard.press("Escape")
    else:
        expect(page.locator("#nav")).to_be_visible()
    page.locator('.lib-card a[href="#/library/1"]').click()
    expect(page.locator("#grid .card")).to_have_count(120)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    if destination := os.environ.get("PICSHELF_TEST_SCREENSHOTS"):
        output = Path(destination)
        output.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(output / f"gallery-{language}-{width}.png"))
    page.locator("#more-btn").scroll_into_view_if_needed()
    expect(page.locator("#grid .card")).to_have_count(125)
    page.locator("#grid .card").first.click()
    show_viewer_info(page)
    expect(page.locator("[data-untag]")).to_have_count(1)
    viewer_editor(page, "tag-editor").locator("#tag-input").fill("色彩")
    expect(page.locator('#tag-dl option[value="色彩:蓝色"]')).to_have_count(1)
    viewer_editor(page, "tag-editor").locator("#tag-input").fill("Browser test")
    page.locator("#tag-add").click()
    expect(page.locator('[data-untag="Browser test"]')).to_be_visible()
    page.locator('[data-untag="Browser test"]').click()
    expect(page.locator('[data-untag="Browser test"]')).to_have_count(0)
    if destination:
        page.screenshot(path=str(output / f"viewer-{language}-{width}.png"))
    # Zero is a rating, distinct from clearing a rating.
    rate_viewer(page, 0)
    expect(page.locator("#v-rating")).to_have_value("0")
    page.locator("#v-rating-clear").click()
    expect(page.locator("#v-rating-clear")).to_be_disabled()
    viewer_more(page).locator("#v-zoom").click()
    viewer_more(page).locator("#v-zoom").click()
    expect(page.locator(".viewer-stage img")).to_have_attribute("src", re.compile("/media/original/"))
    show_viewer_info(page).locator('[data-section="comments"] > summary').click()
    page.locator("[data-del-comment]").click()
    expect(page.locator(".modal-backdrop")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator(".modal-backdrop")).to_have_count(0)
    expect(page.locator(".viewer-backdrop")).to_be_visible()
    expect(page.locator("[data-del-comment]")).to_have_count(1)
    viewer_more(page).locator("#v-slideshow").click()
    page.locator(".modal-body [data-close]").click()
    expect(page.locator(".modal-backdrop")).to_have_count(0)
    viewer_more(page).locator("#v-slideshow").click()
    page.locator(".play-advanced > summary").click()
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
    expect(page.locator(".viewer-panel [data-field=path] .viewer-field-value")).to_contain_text(expected)
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
        page.locator(".account-menu > summary").click()
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
        page.locator("[data-selection-toggle]").click()
        page.locator("[data-select-image]").first.check()
        page.locator("#more-btn").scroll_into_view_if_needed()
        expect(page.locator("#grid .card")).to_have_count(133)
        last=int(page.locator("[data-select-image]").last.get_attribute("data-select-image"))
        page.locator("[data-select-image]").last.check()
        page.locator("[data-batch-names]").fill("Web selected only")
        page.locator("[data-batch-preview]").click()
        expect(page.locator("[data-batch-review]")).to_contain_text("Update 2 images")
        page.locator("[data-batch-apply]").click()
        expect(page.locator("[data-batch-status]")).to_contain_text("changed 2")
        expect(page.locator("[data-batch-status]")).not_to_contain_text("conflicts 0")
        with dbm.open_db(catalog.cfg.db_path) as conn:
            observed={r[0] for r in conn.execute("SELECT it.image_id FROM image_tags it JOIN tags t ON t.id=it.tag_id WHERE t.name='Web selected only'")}
            assert observed=={first,last}
            ops.add_tags(conn,first,["Later manual annotation"],catalog.users["curator"])
        page.locator("[data-batch-undo]").click();page.locator("[data-yes]").click()
        expect(page.locator("[data-batch-status]")).to_contain_text("Undo has conflicts")
        with dbm.open_db(catalog.cfg.db_path) as conn:
            assert {r[0] for r in conn.execute("SELECT it.image_id FROM image_tags it JOIN tags t ON t.id=it.tag_id WHERE t.name='Web selected only'")}=={first}
            assert conn.execute("SELECT 1 FROM image_tags it JOIN tags t ON t.id=it.tag_id WHERE it.image_id=? AND t.name='Later manual annotation'",(first,)).fetchone()


def test_album_organizing_create_edit_cover_remove_and_undo(page,tmp_path):
    from playwright.sync_api import expect
    from tests.scenario_support import build_catalog,http_server
    catalog = build_catalog(tmp_path)
    with http_server(catalog.cfg) as client:
        base = str(client.base_url).rstrip("/")
        page.add_init_script("localStorage.setItem('picshelf.lang','en')")
        page.goto(base)
        page.locator("#login-username").fill("curator"); page.locator("#login-password").fill(catalog.password)
        page.locator("#login-form button").click()
        page.locator(f'.lib-card a[href="#/library/{catalog.libraries["north"]}"]').click()
        page.locator("[data-selection-toggle]").click()
        page.locator("[data-select-image]").nth(0).check(); page.locator("[data-select-image]").nth(1).check()
        selected = [int(page.locator("[data-select-image]").nth(n).get_attribute("data-select-image")) for n in range(2)]
        page.locator("[data-batch-operation]").select_option("album_add")
        page.locator("[data-batch-target]").click()
        page.get_by_role("button",name="New album",exact=True).click()
        form = page.locator(".catalog-form").last
        form.locator('[name="name"]').fill("Weekend favorites")
        page.locator("[data-album-submit]").click()
        expect(page.locator(".modal-backdrop")).to_have_count(0)
        expect(page.locator("[data-selection-count]")).to_contain_text("2 selected")
        expect(page.locator("[data-batch-target]")).to_have_text("Weekend favorites")
        page.locator("[data-batch-preview]").click(); page.locator("[data-batch-apply]").click()
        expect(page.locator("[data-batch-status]")).to_contain_text("changed 2")
        page.locator("[data-selection-toggle]").click()
        expect(page.locator("[data-batch-status]")).to_be_visible()
        page.goto(base + "/#/albums")
        card = page.locator(".album-grid .card",has_text="Weekend favorites")
        expect(card.locator("img")).to_have_count(1)
        card.click()
        expect(page.locator("#grid .card")).to_have_count(2)
        album_id = int(page.url.split("/album/")[1].split("?")[0])
        page.locator("[data-edit-album]").click()
        page.locator('.catalog-form [name="name"]').fill("Weekend picks")
        page.locator('.catalog-form [name="category"]').fill("Trips")
        page.locator('.catalog-form [name="description"]').fill("Two favorites from the trip.")
        page.locator('[data-album-submit]').click()
        expect(page.locator("#view h1")).to_contain_text("Weekend picks")
        expect(page.locator(".album-description")).to_have_text("Two favorites from the trip.")
        page.locator("#grid .card").first.click()
        show_viewer_info(page).locator(f'[data-album="{album_id}"]').click()
        page.locator("[data-cover]").click()
        expect(page.locator(".modal-backdrop")).to_have_count(0)
        show_viewer_info(page).locator(f'[data-album="{album_id}"]').click()
        page.locator("[data-remove]").click()
        expect(page.locator(".modal-backdrop")).to_have_count(1)
        page.locator("[data-yes]").click()
        expect(page.locator(f'[data-album="{album_id}"]')).to_have_count(0)
        page.locator("#v-close").click()
        expect(page.locator("#grid .card")).to_have_count(1)
        page.locator("[data-selection-toggle]").click(); page.locator("[data-select-loaded]").click()
        page.locator("[data-batch-operation]").select_option("album_remove")
        page.locator("[data-batch-target]").click()
        page.locator(".modal .entity-search input").fill("Weekend picks")
        page.locator(".picker-results button",has_text="Weekend picks").click()
        page.locator("[data-batch-preview]").click(); page.locator("[data-batch-apply]").click()
        expect(page.locator("#grid .card")).to_have_count(0)
        page.locator("[data-batch-undo]").click(); page.locator("[data-yes]").click()
        expect(page.locator("#grid .card")).to_have_count(1)
        expect(page.locator("[data-batch-status]")).to_contain_text("Undone")
        with dbm.open_db(catalog.cfg.db_path) as conn:
            album = ops.get_album(conn,album_id)
            assert album["image_count"] == 1 and album["name"] == "Weekend picks"
            assert set(r[0] for r in conn.execute("SELECT image_id FROM album_images WHERE album_id=?",(album_id,))) < set(selected)


def test_tag_manager_renames_and_adds_searchable_alias(page,tmp_path):
    from playwright.sync_api import expect
    from tests.scenario_support import build_catalog,http_server
    catalog = build_catalog(tmp_path)
    with dbm.open_db(catalog.cfg.db_path) as conn:
        ops.add_tags(conn,catalog.images["n01"],["Evening"],catalog.users["curator"])
    with http_server(catalog.cfg) as client:
        base = str(client.base_url).rstrip("/")
        page.add_init_script("localStorage.setItem('picshelf.lang','en')")
        page.goto(base)
        page.locator("#login-username").fill("curator"); page.locator("#login-password").fill(catalog.password)
        page.locator("#login-form button").click()
        page.locator('#nav a[href="#/tags"]').click()
        page.locator(".entity-search input").fill("Evening")
        page.get_by_role("button",name="Manage tag: Evening",exact=True).click()
        page.locator('[data-rename] [name="name"]').fill("Twilight views")
        with page.expect_response("**/api/v1/tags/*") as saved:
            page.locator("[data-rename] button").click()
        assert saved.value.status == 200
        page.locator('[data-alias] [name="alias"]').fill("Twilight alias")
        page.locator("[data-alias] button").click()
        expect(page.locator("[data-review]")).to_contain_text("Twilight views")
        page.locator("[data-review] button").click()
        expect(page.locator("[data-aliases]")).to_contain_text("Twilight alias")
        page.locator('[data-alias] [name="alias"]').fill("TWILIGHT ALIAS")
        page.locator("[data-alias] button").click(); page.locator("[data-review] button").click()
        expect(page.locator('[data-alias] [name="alias"]')).to_have_value("")
        expect(page.locator("[data-aliases] .pill")).to_have_count(1)
        expect(page.locator("[data-aliases]")).to_have_text("Twilight alias")
        page.locator(".modal [data-close]").click()
        page.locator(".entity-search input").fill("Twilight alias")
        expect(page.locator(".entity-name")).to_have_text("Twilight views")
        page.locator(".entity-link").click()
        expect(page.locator("#grid .card")).to_have_count(1)

        page.locator("[data-selection-toggle]").click(); page.locator("[data-select-loaded]").click()
        page.locator("[data-batch-operation]").select_option("tags_remove")
        page.locator("[data-batch-names]").fill("Twilight alias")
        page.locator("[data-batch-preview]").click(); page.locator("[data-batch-apply]").click()
        expect(page.locator("#grid .card")).to_have_count(0)
        page.locator("[data-batch-undo]").click(); page.locator("[data-yes]").click()
        expect(page.locator("#grid .card")).to_have_count(1)


def test_album_and_artist_pickers_find_targets_beyond_first_500(page,tmp_path):
    from playwright.sync_api import expect
    from tests.scenario_support import build_catalog,http_server
    catalog = build_catalog(tmp_path)
    with dbm.open_db(catalog.cfg.db_path) as conn:
        stamp = dbm.utcnow()
        conn.executemany("INSERT INTO albums(library_id,name,kind,created_at) VALUES(?,?,'manual',?)",
                         [(catalog.libraries["north"],f"Paged album {n:03}",stamp) for n in range(601)])
        conn.executemany("INSERT INTO artists(name,created_at) VALUES(?,?)",[(f"Paged artist {n:03}",stamp) for n in range(601)])
        conn.commit()
    with http_server(catalog.cfg) as client:
        base = str(client.base_url).rstrip("/")
        page.add_init_script("localStorage.setItem('picshelf.lang','en')")
        page.goto(base)
        page.locator("#login-username").fill("curator"); page.locator("#login-password").fill(catalog.password)
        page.locator("#login-form button").click()
        page.locator('#nav a[href="#/albums"]').click()
        expect(page.locator(".album-grid .card")).to_have_count(100)
        page.locator("[data-more]").click()
        expect(page.locator(".album-grid .card")).to_have_count(200)
        page.locator(".collection-toolbar input").fill("Paged album 600")
        expect(page.locator(".album-grid .card")).to_have_count(1)
        page.goto(base + f'/#/library/{catalog.libraries["north"]}')
        page.locator("[data-selection-toggle]").click(); page.locator("[data-select-image]").first.check()
        for action,label in [("artist_add","Paged artist 600"),("album_add","Paged album 600")]:
            page.locator("[data-batch-operation]").select_option(action)
            page.locator("[data-batch-target]").click()
            expect(page.locator(".picker-results button")).to_have_count(100)
            page.locator(".modal [data-more]").click()
            expect(page.locator(".picker-results button")).to_have_count(200)
            page.locator(".modal .entity-search input").fill(label)
            expect(page.locator(".picker-results button")).to_have_count(1)
            page.locator(".picker-results button").click()
            expect(page.locator("[data-batch-target]")).to_have_text(label)
            page.locator("[data-batch-preview]").click(); page.locator("[data-batch-apply]").click()
            expect(page.locator("[data-batch-status]")).to_contain_text("changed 1")
            page.locator("[data-batch-undo]").click(); page.locator("[data-yes]").click()
            expect(page.locator("[data-batch-status]")).to_contain_text("Undone")


def test_batch_recovers_lost_preview_and_status_then_cancels_resumes_undoes(page,tmp_path,monkeypatch):
    from playwright.sync_api import expect
    from picshelf import batches
    from picshelf.access import AccessContext
    from picshelf.worker import WorkerManager
    from tests.scenario_support import build_catalog,http_server
    catalog = build_catalog(tmp_path)
    # Keep a real queued batch pending until the browser has exercised recovery.
    monkeypatch.setattr(WorkerManager,"start",lambda self:None)
    with dbm.open_db(catalog.cfg.db_path) as conn:
        for n in range(101): make_image(catalog.roots/"north"/f"recovery-{n:03}.png",(n,80,150))
        indexer.scan_library(conn,catalog.cfg,catalog.libraries["north"])
    previews, polls = [], []
    def preview_route(route):
        previews.append(route.request.post_data_json)
        if len(previews) == 1:
            route.fetch().dispose(); route.abort("failed")
        else: route.continue_()
    def status_route(route):
        polls.append(route.request.url)
        if len(polls) == 1: route.abort("failed")
        else: route.continue_()
    page.route("**/api/v1/batches/preview",preview_route)
    page.route("**/api/v1/batches/*?include_items=false",status_route)
    with http_server(catalog.cfg) as client:
        base = str(client.base_url).rstrip("/")
        page.add_init_script("localStorage.setItem('picshelf.lang','en')")
        page.goto(base)
        page.locator("#login-username").fill("curator"); page.locator("#login-password").fill(catalog.password)
        page.locator("#login-form button").click()
        page.locator(f'.lib-card a[href="#/library/{catalog.libraries["north"]}"]').click()
        page.locator("[data-selection-toggle]").click(); page.locator("[data-select-all]").click()
        page.locator("[data-batch-names]").fill("Recovered batch")
        page.locator("[data-batch-preview]").click()
        expect(page.locator(".toast.error")).to_be_visible()
        page.locator("[data-batch-preview]").click()
        expect(page.locator("[data-batch-apply]")).to_be_visible()
        assert len(previews) == 2 and previews[0]["request_key"] == previews[1]["request_key"]
        with dbm.open_db(catalog.cfg.db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM metadata_batches").fetchone()[0] == 1
            batch_id = conn.execute("SELECT id FROM metadata_batches").fetchone()[0]
        page.locator("[data-batch-apply]").click()
        expect(page.locator("[data-batch-status]")).to_contain_text("Queued")
        expect(page.locator("[data-batch-undo]")).to_be_hidden()
        page.locator("[data-batch-cancel]").click()
        expect(page.locator("[data-batch-status]")).to_contain_text("Cancelled")
        page.locator("[data-batch-resume]").click()
        expect(page.locator("[data-status-retry]")).to_be_visible()
        with dbm.open_db(catalog.cfg.db_path) as conn:
            result = batches.execute(conn,catalog.cfg,AccessContext(catalog.users["curator"]),batch_id)
            assert result["counts"]["succeeded"] == 113
        page.locator("[data-status-retry]").click()
        expect(page.locator("[data-batch-status]")).to_contain_text("changed 113")
        expect(page.locator("[data-status-error]")).to_be_hidden()
        page.locator("[data-batch-undo]").click(); page.locator("[data-yes]").click()
        expect(page.locator("[data-batch-status]")).to_contain_text("Undone")
        page.locator("[data-batch-status] a").click()
        expect(page.locator("tbody tr")).to_have_count(113)
        expect(page.locator("tbody tr").first).to_contain_text("Undone")


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
        page.locator('[data-save-query]').click()
        page.locator('[data-save-form] [name=name]').fill('Unrated collection')
        page.locator('[data-save-form] [name=kind]').select_option('smart')
        page.locator('[data-save]').click()
        expect(page.locator('h1')).to_have_text('Unrated collection')
        with dbm.open_db(catalog.cfg.db_path) as conn:
            ops.set_rating(conn,catalog.images['n03'],catalog.users['reader'],None)
        page.locator('[data-reload-rule]').click()
        expect(page.locator('#grid .card')).to_have_count(2)
        page.locator('.query-editor > summary').click()
        page.locator('.query-syntax > summary').click()
        page.locator('.query-editor [name=expression]').fill('tag:')
        page.locator('[data-save-query]').click()
        page.locator('[data-save]').click()
        expect(page.locator('[data-save-error]')).to_contain_text('Invalid search condition')
        page.locator('[data-save-form] [data-close]').click()
        page.locator('.query-editor [name=expression]').fill('rated:true rating:0')
        page.locator('[data-save-query]').click()
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
        page.locator('.play-advanced > summary').click()
        page.locator('#ss-interval').fill('1');page.locator('#ss-count').fill('3')
        page.locator('#ss-sort').select_option('path');page.locator('#ss-loop').uncheck()
        page.locator('#ss-name').fill('Reader slides');page.locator('#ss-save').click()
        expect(page.locator('.toast').last).to_contain_text('Rule saved')
        page.route(f"**/api/v1/media/preview/{catalog.images['n02']}?*",lambda route:route.abort())
        page.locator('#ss-start').click()
        expect(page.locator('#ss-img')).to_have_attribute('alt','01 zero.png')
        page.wait_for_function("document.querySelector('#ss-img').naturalWidth > 0")
        page.locator('#ss-pause').click()
        expect(page.locator('#ss-pause')).to_contain_text('Resume')
        page.wait_for_timeout(1400)
        expect(page.locator('#ss-img')).to_have_attribute('alt','01 zero.png')
        page.locator('#ss-next').click()
        expect(page.locator('#ss-status')).to_contain_text('skip to the next image')
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
    navigate_from_sidebar(page, "#/search")
    expect(page.locator("#grid .card")).to_have_count(120)
    expect(page.locator("details.batch-controls")).not_to_have_attribute("open", "")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert page.locator("#search-input").evaluate("el => parseFloat(getComputedStyle(el).fontSize) >= 16")
    for selector in ["#lang-btn", "#slideshow-btn", "#density-select", ".card-selection"]:
        box = page.locator(selector).first.bounding_box()
        assert box["width"] >= 44 and box["height"] >= 44, (selector, box)
    assert page.locator("#grid img").first.evaluate("el => getComputedStyle(el).objectFit") == "contain"
    page.locator("#density-select").select_option("compact")
    page.locator("[data-selection-toggle]").click()
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
    navigate_from_sidebar(page, "#/search")
    expect(page.get_by_role("heading", name="Unable to load this page")).to_be_visible()
    expect(page.locator(".account-menu > summary")).to_be_visible()
    page.unroute("**/api/v1/images?*")
    page.locator("[data-route-retry]").click()
    expect(page.locator("#grid .card")).to_have_count(120)


def test_viewer_failed_pixels_and_saves_keep_context_and_inputs(page, web_server):
    from playwright.sync_api import expect
    sign_in(page, web_server)
    navigate_from_sidebar(page, "#/search")
    cards = page.locator("#grid .card")
    expect(cards).to_have_count(120)
    first = cards.first.get_attribute("data-id")
    second_name = cards.nth(1).locator(".fname").text_content()
    page.route(f"**/media/preview/{first}?*", lambda route: route.abort("failed"))
    cards.first.click()
    expect(page.locator(".viewer-status")).to_contain_text("could not be displayed")
    expect(page.locator(".viewer-stage img")).to_have_count(0)
    expect(page.locator(".viewer-panel [data-field=path] .viewer-field-value")).to_have_count(0)
    page.locator("#v-next").click()
    expect(page.locator(".viewer-stage img")).to_have_attribute("alt", second_name)
    expect(page.locator(".viewer-panel [data-field=path] .viewer-field-value")).to_contain_text(second_name)
    page.unroute(f"**/media/preview/{first}?*")
    draft = "A comment to keep through a failed save"
    show_viewer_info(page).locator('[data-section="comments"] > summary').click()
    page.locator("#comment-input").fill(draft)
    page.route("**/images/*/comments", lambda route: route.abort("internetdisconnected"))
    page.locator("#comment-add").click()
    expect(page.locator(".viewer-status")).to_contain_text("unconfirmed")
    expect(page.locator("#comment-input")).to_have_value(draft)
    page.unroute("**/images/*/comments")
    rate_viewer(page, 0)
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
    navigate_from_sidebar(page, "#/search")
    page.locator("[data-selection-toggle]").click()
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
    viewer_editor(page, "tag-editor").locator("#tag-input").fill("中文输入")
    page.locator("#tag-input").evaluate("el => el.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter',isComposing:true,bubbles:true}))")
    expect(page.locator("#tag-input")).to_have_value("中文输入")
    expect(page.locator("[data-untag='中文输入']")).to_have_count(0)
    viewer_editor(page, "tag-editor").locator("#tag-input").fill("")
    page.keyboard.press("Tab")
    assert page.evaluate("document.querySelector('.viewer-backdrop').contains(document.activeElement)")
    page.keyboard.press("Escape")
    expect(card).to_be_focused()
    assert handle.evaluate("el => el.isConnected")
    assert abs(page.evaluate("scrollY") - scroll) < 2
    expect(page.locator("[data-select-image]:checked")).to_have_count(4)
    assert not any("/media/original/" in url for url in requests)


def test_phone_navigation_closes_with_escape_and_restores_search_access(page, web_server):
    from playwright.sync_api import expect
    page.set_viewport_size({"width": 390, "height": 844})
    sign_in(page, web_server)
    page.locator("#nav-toggle").click()
    expect(page.locator("#sidebar")).to_have_attribute("aria-modal", "true")
    page.keyboard.press("Escape")
    expect(page.locator("#nav-toggle")).to_be_focused()
    page.locator("#nav-toggle").click()
    page.locator('#nav a[href="#/favorites"]').click()
    expect(page.locator("#nav-toggle")).to_have_attribute("aria-expanded", "false")
    expect(page.locator("#sidebar")).to_be_hidden()
    expect(page.locator("#view .page-head h1")).to_have_text("My favorites")
    page.keyboard.press("/")
    expect(page.locator("#search-input")).to_be_focused()
    page.locator("#search-input").fill("image-000")
    page.locator("#search-input").press("Enter")
    expect(page.locator("#grid .card")).to_have_count(1)


@pytest.mark.parametrize("width", [320, 390, 1280])
def test_viewer_actions_stay_reachable_and_escape_closes_the_menu_first(page, web_server, width):
    from playwright.sync_api import expect
    page.set_viewport_size({"width":width,"height":844})
    sign_in(page, web_server)
    page.locator("#grid .card").first.click()
    expect(page.locator(".viewer-stage img")).to_be_visible()
    more = page.locator(".viewer-more > summary")
    if width <= 390:
        assert page.locator("#v-close").bounding_box()["x"] < more.bounding_box()["x"]
    more.click()
    page.locator("#v-bg").click()
    expect(page.locator(".viewer-stage")).to_have_class(re.compile("checkerboard"))
    more.click()
    menu = page.locator(".viewer-more .viewer-menu").bounding_box()
    assert 0 <= menu["x"] and menu["x"] + menu["width"] <= width
    page.keyboard.press("Escape")
    expect(page.locator(".viewer-backdrop")).to_be_visible()
    expect(page.locator(".viewer-more")).not_to_have_attribute("open", "")
    expect(more).to_be_focused()
    page.locator("#v-close").click()
    expect(page.locator(".viewer-backdrop")).to_have_count(0)


def test_direct_link_opens_an_image_beyond_the_first_page(page, web_server):
    from playwright.sync_api import expect
    sign_in(page, web_server)
    requests = []
    page.on("request", lambda request: requests.append(request.url))
    page.goto(web_server.url + "/#/view/1")
    expect(page.locator(".viewer-stage img")).to_have_attribute("alt", "image-000.png")
    assert len([url for url in requests if "/api/v1/images?" in url]) == 1
    expect(page.locator("#v-next")).to_be_disabled()
    expect(page.locator("#v-slideshow")).to_have_count(0)
    page.locator("#v-close").click()
    expect(page.locator("#view h1")).to_have_text("All images")
    expect(page.locator("#grid .card")).to_have_count(120)


def test_old_admin_response_cannot_replace_the_selected_tab(page, web_server):
    from playwright.sync_api import expect
    sign_in(page, web_server)
    page.evaluate("""() => {
      const fetchOriginal = window.fetch;
      window.fetch = async (...args) => {
        const response = await fetchOriginal(...args);
        if (String(args[0]).endsWith('/users')) {
          await new Promise(resolve => {window.releaseUsers = resolve});
        }
        return response;
      };
    }""")
    page.goto(web_server.url + "/#/admin?tab=users")
    page.wait_for_function("typeof window.releaseUsers === 'function'")
    page.locator('[data-tab="libraries"]').click()
    page.locator('[data-import]').first.click()
    expect(page.locator("#imp-pick-files")).to_be_visible()
    page.evaluate("window.releaseUsers()")
    expect(page.locator('[data-tab="libraries"]')).to_have_attribute("aria-current", "page")
    expect(page.locator("#imp-pick-files")).to_be_visible()
    expect(page.locator("#nu-name")).to_have_count(0)


def test_entity_search_pages_and_treats_wildcards_literally(page, web_server):
    from playwright.sync_api import expect
    sign_in(page, web_server)
    response = page.request.post(web_server.url + "/api/v1/tags", headers={"Origin":web_server.url},
                                 data={"name":"100%_literal"})
    assert response.status == 200
    for n in range(101):
        response = page.request.post(web_server.url + "/api/v1/tags", headers={"Origin":web_server.url},
                                     data={"name":f"catalog-name-{n:03}"})
        assert response.status == 200
    page.goto(web_server.url + "/#/tags")
    expect(page.locator(".entity-row")).to_have_count(100)
    expect(page.locator(".entity-name", has_text="catalog-name-100")).to_have_count(0)
    page.locator("[data-list-more]").click()
    expect(page.locator(".entity-name", has_text="catalog-name-100")).to_be_visible()
    page.locator(".entity-search input").fill("catalog-name-100")
    expect(page.locator(".entity-row")).to_have_count(1)
    expect(page.locator(".entity-name")).to_have_text("catalog-name-100")
    page.locator(".entity-search input").fill("%_")
    expect(page.locator(".entity-row")).to_have_count(1)
    expect(page.locator(".entity-name")).to_have_text("100%_literal")
    page.locator(".entity-row").click()
    expect(page.locator("#view h1")).to_contain_text("100%_literal")


def test_name_search_network_failure_retains_results_and_retries(page, web_server):
    from playwright.sync_api import expect
    sign_in(page, web_server)
    response = page.request.post(web_server.url + "/api/v1/tags", headers={"Origin":web_server.url},
                                 data={"name":"retry-search"})
    assert response.status == 200
    page.goto(web_server.url + "/#/tags")
    expect(page.locator(".entity-row").first).to_be_visible()
    count = page.locator(".entity-row").count()
    page.route("**/api/v1/tags?*", lambda route: route.abort("internetdisconnected"))
    page.locator(".entity-search input").fill("retry-search")
    expect(page.locator("[data-list-error]")).to_contain_text("Connection interrupted")
    expect(page.locator(".entity-row")).to_have_count(count)
    page.unroute("**/api/v1/tags?*")
    page.locator("[data-list-retry]").click()
    expect(page.locator(".entity-row")).to_have_count(1)
    expect(page.locator(".entity-name")).to_have_text("retry-search")


def test_partial_import_keeps_failure_details_and_retry_available(page, web_server, tmp_path):
    from playwright.sync_api import expect
    source, target = tmp_path / 'source', tmp_path / 'target'
    target.mkdir()
    make_image(source / 'good.png', (20, 60, 180))
    make_image(source / 'changed.png', (70, 150, 220))
    sign_in(page, web_server)
    page.goto(web_server.url + '/#/admin?tab=libraries')
    page.locator('[data-new-library]').click()
    page.locator('#nl-name').fill('Partial import')
    page.locator('[data-library-options] > summary').click()
    page.locator('#nl-path').fill(str(target))
    page.locator('#nl-mode').select_option('managed')
    page.locator('#imp-src').fill(str(source))
    page.locator('#imp-mode').select_option('copy')
    page.locator('#nl-create').click()
    expect(page.locator('#imp-exec')).to_be_enabled()
    # The saved preview must report this conflict without losing the successful file.
    make_image(source / 'changed.png', (180, 10, 30), size=(90, 45))
    page.locator('#imp-exec').click()
    expect(page.locator('#imp-out')).to_contain_text('Import partially failed')
    expect(page.locator('#imp-out')).to_contain_text('Files imported: 1 · failed 1')
    expect(page.locator('#imp-out details pre')).to_be_visible()
    expect(page.locator('#imp-out details pre')).not_to_be_empty()
    expect(page.locator('#imp-exec')).to_be_enabled()
    expect(page.locator('#imp-exec')).to_contain_text('Retry')
    assert (target / 'good.png').read_bytes() == (source / 'good.png').read_bytes()
    assert not (target / 'changed.png').exists()
    page.locator('.modal-head [data-close]').click()
    page.locator('[data-tab="jobs"]').click()
    page.locator('tr[data-job]').filter(has_text='Partial').first.locator('[data-view]').click()
    expect(page.locator('.job-progress [role="status"]')).to_contain_text('failed 1')


def test_filters_apply_to_results_and_keep_results_after_network_failure(page, web_server):
    from playwright.sync_api import expect
    sign_in(page, web_server)
    page.goto(web_server.url + '/#/search')
    expect(page.locator('#grid .card')).to_have_count(120)
    expect(page.locator('[data-save-form]')).to_have_count(0)
    page.locator('.query-editor > summary').click()
    page.locator('[name=field]').select_option('file')
    page.locator('[name=value]').fill('image-000')
    page.route('**/api/v1/images?*', lambda route: route.abort(), times=1)
    page.locator('[name=value]').press('Enter')
    expect(page.locator('[data-rule-error]')).not_to_be_empty()
    expect(page.locator('#grid .card')).to_have_count(120)
    page.locator('[data-add]').click()
    expect(page.locator('#grid .card')).to_have_count(1)
    expect(page.locator('#grid .fname')).to_have_text('image-000.png')
    expect(page.locator('#search-input')).to_have_value('file:"image-000"')
    page.reload()
    expect(page.locator('#grid .card')).to_have_count(1)
    page.locator('.query-editor > summary').click()
    page.locator('.query-syntax > summary').click()
    page.locator('[data-clear]').click()
    expect(page.locator('#grid .card')).to_have_count(120)


def test_library_form_retries_scan_without_registering_twice(page, web_server, tmp_path):
    from playwright.sync_api import expect
    root = tmp_path / 'library'
    make_image(root / 'ready.png')
    sign_in(page, web_server)
    page.goto(web_server.url + '/#/admin?tab=libraries')
    page.locator('[data-new-library]').click()
    page.locator('[data-library-options] > summary').click()
    page.locator('#nl-mode').select_option('indexed')
    page.locator('#nl-name').fill('Scan retry')
    page.locator('#nl-path').fill(str(root))
    page.route('**/api/v1/libraries/*/scan-jobs', lambda route: route.abort(), times=1)
    page.locator('#nl-path').press('Enter')
    expect(page.locator('[data-library-error]')).to_be_visible()
    expect(page.locator('#nl-create')).to_have_text('Retry scan')
    expect(page.locator('#nl-name')).to_be_disabled()
    page.locator('#nl-create').click()
    expect(page.locator('.modal-backdrop')).to_have_count(0, timeout=10000)
    libs = page.request.get(web_server.url + '/api/v1/libraries').json()
    assert len([lib for lib in libs if lib['name'] == 'Scan retry']) == 1
    library = next(lib for lib in libs if lib['name'] == 'Scan retry')
    assert library['image_count'] == 1


def test_library_manager_can_import_without_account_administration(page, tmp_path):
    from playwright.sync_api import expect
    from tests.scenario_support import build_catalog, http_server
    from picshelf import source_access
    from picshelf.access import AccessContext
    catalog = build_catalog(tmp_path)
    source = tmp_path / 'incoming'
    make_image(source / 'new.png')
    with dbm.open_db(catalog.cfg.db_path) as conn:
        source_access.register(conn, AccessContext(catalog.users['curator']), str(source), allow_move=True)
        conn.execute("UPDATE library_grants SET access='manage' WHERE library_id=? AND group_id IN "
                     "(SELECT group_id FROM user_groups WHERE user_id=?)",
                     (catalog.libraries['north'], catalog.users['reader']))
        conn.execute("UPDATE libraries SET mode='managed',default_import_mode='move' WHERE id=?", (catalog.libraries['north'],))
        conn.commit()
    with http_server(catalog.cfg) as client:
        page.add_init_script("localStorage.setItem('picshelf.lang','en')")
        page.goto(str(client.base_url))
        page.locator('#login-username').fill('reader')
        page.locator('#login-password').fill(catalog.password)
        page.locator('#login-form button').click()
        expect(page.locator('.lib-card')).to_have_count(1)
        page.goto(f'{client.base_url}/#/library/{catalog.libraries["north"]}')
        page.get_by_role('button', name='Import images', exact=True).click()
        expect(page.locator('[data-tab="users"]')).to_have_count(0)
        expect(page.locator('[data-tab="groups"]')).to_have_count(0)
        expect(page.locator('#imp-lib option')).to_have_count(1)
        page.locator('.import-options > summary').click()
        page.locator('#imp-src').fill(str(source))
        page.locator('#imp-mode').select_option('move')
        page.locator('#imp-plan').click()
        expect(page.locator('#imp-exec')).to_be_enabled()
        page.locator('#imp-exec').click()
        expect(page.locator('.modal-body').last).to_contain_text('Files will be moved')
        page.locator('[data-no]').click()
        assert (source / 'new.png').exists()
        assert not (catalog.roots / 'north' / 'new.png').exists()
        page.locator('#imp-exec').click()
        page.locator('[data-yes]').click()
        expect(page.locator('#imp-browse')).to_be_visible(timeout=10000)
        assert not (source / 'new.png').exists()
        page.locator('#imp-browse').click()
        expect(page.locator('#grid .card')).to_have_count(13)
        with dbm.open_db(catalog.cfg.db_path) as conn:
            conn.execute("UPDATE library_grants SET access='view' WHERE library_id=?", (catalog.libraries['north'],))
            conn.commit()
        page.reload()
        expect(page.get_by_role('button', name='Import images', exact=True)).to_have_count(0)
        page.goto(str(client.base_url) + '/#/admin?tab=import')
        expect(page.locator('#imp-plan')).to_be_disabled()
        expect(page.locator('#imp-lib option')).to_have_count(0)


@pytest.mark.parametrize('language', ['zh', 'en'])
@pytest.mark.parametrize('width', [390, 1280])
def test_viewer_playback_starts_here_and_browse_sort_survives_reload(page, web_server, language, width):
    from playwright.sync_api import expect
    page.set_viewport_size({'width': width, 'height': 900})
    sign_in(page, web_server, language)
    page.goto(web_server.url + '/#/library/1')
    page.locator('#sort-select').select_option('path')
    expect(page.locator('#grid .fname').first).to_have_text('image-000.png')
    expect(page).to_have_url(re.compile(r'sort=path'))
    page.reload()
    expect(page.locator('#sort-select')).to_have_value('path')
    card = page.locator('#grid .card').nth(7)
    card.click()
    expect(page.locator('.viewer-stage img')).to_have_attribute('alt', 'image-007.png')
    viewer_more(page).locator("#v-slideshow").click()
    expect(page.locator('#ss-from-current')).to_be_checked()
    expect(page.locator('#ss-sort')).to_have_value('path')
    page.locator('#ss-interval').fill('0')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.locator('#ss-interval').press('Enter')
    expect(page.locator('#ss-img')).to_have_attribute('alt', 'image-007.png')
    expect(page.locator('#ss-img')).to_be_visible()
    expect(page.locator('#ss-retry')).to_be_hidden()
    expect(page.locator('#ss-caption')).to_contain_text('8 / 125')
    page.locator('#ss-next').click()
    expect(page.locator('#ss-img')).to_have_attribute('alt', 'image-008.png')
    page.locator('#ss-exit').click()
    expect(page.locator('.viewer-stage img')).to_have_attribute('alt', 'image-007.png')
    page.locator('#v-close').click()
    expect(card).to_be_focused()


def test_rule_creation_saves_without_starting_and_names_the_selected_library(page, web_server):
    from playwright.sync_api import expect
    sign_in(page, web_server)
    requests = []
    page.on('request', lambda request: requests.append((request.method, request.url)))
    page.goto(web_server.url + '/#/play-rules')
    page.locator('[data-new]').click()
    expect(page.locator('#ss-name')).to_be_visible()
    expect(page.locator('.modal-actions .primary')).to_have_id('ss-save')
    expect(page.locator('#ss-start')).to_have_count(0)
    page.locator('#ss-name').fill('Morning gallery')
    page.locator('#ss-library').select_option(label='Gallery')
    page.locator('#ss-interval').fill('0')
    page.locator('#ss-name').press('Enter')
    expect(page.locator('[data-play-form]')).to_have_count(0)
    rule = page.locator('[data-rules] .lib-card').filter(has_text='Morning gallery')
    expect(rule.locator('.query-expression')).to_have_text('Gallery')
    assert not any(method == 'POST' and url.endswith('/play/sessions') for method, url in requests)
    rule.locator('[data-edit]').click()
    expect(page.locator('#ss-library')).to_have_value('1')
    page.locator('#ss-name').fill('Morning gallery revised')
    page.locator('#ss-save').click()
    rule = page.locator('[data-rules] .lib-card').filter(has_text='Morning gallery revised')
    expect(rule).to_have_count(1)
    rule.locator('[data-start]').click()
    expect(page.locator('#ss-img')).to_have_attribute('alt', 'image-000.png')
    page.locator('#ss-exit').click()


def test_filter_history_returns_to_previous_query_and_retains_sort(page, web_server):
    from playwright.sync_api import expect
    sign_in(page, web_server)
    page.goto(web_server.url + '/#/search?sort=path')
    page.locator('.query-editor > summary').click()
    page.locator('[name=field]').select_option('file')
    page.locator('[name=value]').fill('image-00')
    page.locator('[data-add]').click()
    expect(page.locator('#grid .card')).to_have_count(10)
    first_query = page.locator('#search-input').input_value()
    page.locator('[name=field]').select_option('file')
    page.locator('[name=value]').fill('image-000')
    page.locator('[data-add]').click()
    expect(page.locator('#grid .card')).to_have_count(1)
    page.go_back()
    expect(page.locator('#grid .card')).to_have_count(10)
    expect(page.locator('#search-input')).to_have_value(first_query)
    expect(page.locator('#sort-select')).to_have_value('path')
    page.go_back()
    expect(page.locator('#grid .card')).to_have_count(120)
    page.go_forward()
    expect(page.locator('#grid .card')).to_have_count(10)
    page.locator('#sort-select').select_option('oldest')
    expect(page).to_have_url(re.compile(r'sort=oldest'))
    page.reload()
    expect(page.locator('#grid .card')).to_have_count(10)
    expect(page.locator('#sort-select')).to_have_value('oldest')


def test_job_details_resume_after_disconnect_and_fetch_items_only_when_opened(page, web_server):
    from playwright.sync_api import expect
    from urllib.parse import parse_qs, urlsplit
    sign_in(page, web_server)
    response = page.request.post(web_server.url + '/api/v1/libraries/1/rebuild-jobs',
                                 headers={'Origin': web_server.url}, data={'request_key': secrets.token_hex(16)})
    assert response.status == 202
    job_id = response.json()['id']
    path = f'/api/v1/jobs/{job_id}'
    page.route(f'**{path}?include_items=false', lambda route: route.abort('internetdisconnected'), times=1)
    requests = []
    page.on('request', lambda request: requests.append(request.url))
    page.goto(web_server.url + f'/#/admin?tab=jobs&job={job_id}')
    expect(page.locator('[data-connection]')).to_be_visible()
    page.locator('[data-reconnect]').click()
    expect(page.locator('[data-title]')).to_contain_text('Gallery')
    expect(page.locator('.job-progress [role=status]')).to_contain_text('Succeeded', timeout=15000)
    expect(page.locator('[data-retry]')).to_be_hidden()
    assert not any(urlsplit(url).path == path and parse_qs(urlsplit(url).query).get('include_items') != ['false']
                   for url in requests)
    page.reload()
    expect(page.locator('.job-progress [role=status]')).to_contain_text('Processed 125/125')
    page.locator('.job-progress summary').click()
    expect(page.locator('[data-job-items] li')).to_have_count(100)
    page.locator('[data-job-items]').get_by_role('button', name='Next page').click()
    expect(page.locator('[data-job-items] li')).to_have_count(25)
    page.locator('[data-library]').click()
    expect(page.locator('#grid .card')).to_have_count(120)
    page.go_back()
    expect(page.locator('.job-progress [role=status]')).to_contain_text('Processed 125/125')


def test_excluded_playback_start_keeps_settings_and_can_start_from_queue_head(page, web_server):
    from playwright.sync_api import expect
    sign_in(page, web_server)
    page.goto(web_server.url + '/#/library/1?sort=path')
    page.locator('#grid .card').nth(7).click()
    viewer_more(page).locator("#v-slideshow").click()
    page.locator('#ss-interval').fill('0')
    page.locator('.play-advanced > summary').click()
    page.locator('#ss-count').fill('2')
    page.locator('#ss-start').click()
    expect(page.locator('#ss-error')).to_contain_text('This image is not in the playback queue')
    expect(page.locator('#ss-error')).to_contain_text('turn off Start from this image')
    expect(page.locator('#ss-count')).to_have_value('2')
    expect(page.locator('.slideshow-backdrop')).to_have_count(0)
    page.locator('#ss-from-current').uncheck()
    page.locator('#ss-start').click()
    expect(page.locator('#ss-img')).to_have_attribute('alt', 'image-000.png')
    expect(page.locator('#ss-caption')).to_contain_text('1 / 2')
    page.locator('#ss-exit').click()
    page.locator('#v-close').click()


def test_cancelled_job_retries_in_its_detail_page_and_updates_history(page, tmp_path):
    from playwright.sync_api import expect
    from tests.scenario_support import build_catalog, http_server
    from tests.test_jobs import held_process
    catalog = build_catalog(tmp_path)
    with http_server(catalog.cfg) as client:
        page.add_init_script("localStorage.setItem('picshelf.lang','en')")
        page.goto(str(client.base_url))
        page.locator('#login-username').fill('curator')
        page.locator('#login-password').fill(catalog.password)
        page.locator('#login-form button').click()
        page.locator('#view .page-head h1').wait_for()
        with held_process(catalog.cfg, 'slot'), held_process(catalog.cfg, 'slot'):
            response = page.request.post(str(client.base_url) + f"/api/v1/libraries/{catalog.libraries['north']}/scan-jobs",
                                         headers={'Origin': str(client.base_url)}, data={'request_key': 'cancel-and-retry'})
            assert response.status == 202
            job_id = response.json()['id']
            page.goto(str(client.base_url) + f'/#/admin?tab=jobs&job={job_id}')
            expect(page.locator('.job-progress [role=status]')).to_contain_text('Queued')
            page.locator('.job-progress [data-cancel]').click()
            expect(page.locator('.job-progress [role=status]')).to_contain_text('Cancelled')
            expect(page.locator('[data-retry]')).to_be_visible()
        page.locator('[data-retry]').click()
        page.locator('[data-yes]').click()
        expect(page.locator('.job-progress [role=status]')).to_contain_text('Succeeded', timeout=15000)
        expect(page.locator('[data-retry]')).to_be_hidden()
        page.get_by_role('link', name='All tasks').click()
        expect(page.locator(f'tr[data-job="{job_id}"]')).to_contain_text('Succeeded')
        expect(page.locator(f'tr[data-job="{job_id}"]')).to_contain_text('north')


@pytest.mark.parametrize('width', [390, 1280])
def test_large_gallery_scroll_retry_selection_focus_and_resize(page, tmp_path, width):
    from playwright.sync_api import expect
    from urllib.parse import urlparse, parse_qs
    from tests.datasets import metadata_rows
    from tests.scenario_support import build_catalog, http_server
    catalog = build_catalog(tmp_path)
    with dbm.open_db(catalog.cfg.db_path) as conn:
        sample = conn.execute('SELECT thumb_rel,preview_rel FROM images WHERE id=?', (catalog.images['n01'],)).fetchone()
        rows = list(metadata_rows(1200, catalog.libraries['north']))
        for row in rows: row.update(dict(sample))
        fields = list(rows[0])
        conn.executemany(f"INSERT INTO images({','.join(fields)}) VALUES({','.join('?' for _ in fields)})", [list(row.values()) for row in rows])
        conn.commit()
    attempts = []
    def pages(route):
        offset = int(parse_qs(urlparse(route.request.url).query).get('offset', ['0'])[0])
        attempts.append(offset)
        if offset == 240 and attempts.count(240) == 1: route.abort()
        else: route.continue_()
    page.route('**/api/v1/images?*', pages)
    page.set_viewport_size({'width':width, 'height':800})
    with http_server(catalog.cfg) as client:
        page.add_init_script("localStorage.setItem('picshelf.lang','en')")
        page.goto(str(client.base_url))
        page.locator('#login-username').fill('reader'); page.locator('#login-password').fill(catalog.password)
        page.locator('#login-form button').click(); page.locator('.lib-card').wait_for()
        page.goto(str(client.base_url).rstrip('/') + '/#/search?sort=path')
        expect(page.locator('#grid .card')).to_have_count(120)
        page.locator('[data-selection-toggle]').click()
        first_id = page.locator('#grid .card').first.get_attribute('data-id')
        page.locator(f'[data-select-image="{first_id}"]').check()
        page.locator('[data-selection-toggle]').focus()
        page.locator('#more-btn').scroll_into_view_if_needed()
        expect(page.locator('#grid .card')).to_have_count(240)
        page.locator('#more-btn').scroll_into_view_if_needed()
        expect(page.locator('#more-btn')).to_have_text('Retry')
        page.wait_for_timeout(250)
        assert attempts.count(240) == 1
        page.locator('#more-btn').click()
        expect(page.locator('#grid')).to_have_class(re.compile(r'windowed'))
        assert page.locator('#grid .card').count() < 160
        expect(page.locator('[data-selection-count]')).to_have_text('1 selected')
        card_id = page.evaluate('''() => [...document.querySelectorAll('#grid .card')].find(card => {
          const rect=card.getBoundingClientRect();return rect.top>=80&&rect.bottom<innerHeight;
        }).dataset.id''')
        card = page.locator(f'#grid .card[data-id="{card_id}"]')
        scroll = page.evaluate('scrollY')
        card.locator('img').click()
        expect(page.locator('.viewer-stage img')).to_be_visible()
        page.keyboard.press('Escape')
        expect(card).to_be_focused()
        assert abs(page.evaluate('scrollY') - scroll) < 2
        position = int(card.get_attribute('aria-posinset'))
        card.locator('input').click(modifiers=['Shift'])
        expect(page.locator('[data-selection-count]')).to_have_text(f'{position} selected')
        # Keyboard navigation can reach an unmounted row, then return to the start.
        card.press('End')
        expect(page.locator('#grid .card[aria-posinset="360"]')).to_be_focused()
        page.keyboard.press('Home')
        expect(page.locator('#grid .card[aria-posinset="1"]')).to_be_focused()
        expect(page.locator(f'[data-select-image="{first_id}"]')).to_be_checked()
        page.keyboard.press('End')
        page.set_viewport_size({'width':1280 if width == 390 else 390, 'height':800})
        page.wait_for_timeout(150)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        focused = page.locator('#grid .card:focus')
        assert focused.evaluate('el => el.getBoundingClientRect().bottom > 0 && el.getBoundingClientRect().top < innerHeight')
        page.locator('#density-select').evaluate("el => {el.value='compact';el.dispatchEvent(new Event('change'));}")
        page.wait_for_timeout(100)
        assert page.locator('#grid .card').count() < 160
        page.screenshot(path=str(tmp_path / f'gallery-{width}.png'))


def test_thumbnail_retry_does_not_open_the_viewer(page, web_server):
    from playwright.sync_api import expect
    failures = []
    def thumbnails(route):
        if not failures:
            failures.append(route.request.url); route.abort()
        else: route.continue_()
    page.route('**/api/v1/media/thumb/**', thumbnails)
    sign_in(page, web_server)
    # The home page has separate compact cards; exercise the collection renderer.
    failures.clear()
    navigate_from_sidebar(page, '#/search')
    retry = page.locator('[data-thumb-retry]')
    expect(retry).to_have_count(1)
    image = retry.locator('..').locator('img').element_handle()
    retry.click()
    expect(retry).to_have_count(0)
    page.wait_for_function('el => el.complete && el.naturalWidth > 0', arg=image)
    expect(page.locator('.viewer-backdrop')).to_have_count(0)


@pytest.mark.parametrize('entry', ['search', 'filter', 'filter_sort'])
def test_search_navigation_cancels_the_abandoned_query(page, web_server, entry):
    from playwright.sync_api import expect
    sign_in(page, web_server)
    if entry != 'search':
        navigate_from_sidebar(page, '#/search')
        expect(page.locator('#grid .card')).to_have_count(120)
    page.evaluate('''() => {
      const original=window.fetch;
      window.fetch=(url,options={})=>{
        if(String(url).includes('/images?') && new URL(url,location.origin).searchParams.get('q')==='file:abandoned') {
          window.abandonedStarted=true;
          return new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>{
            window.abandonedCancelled=true;reject(new DOMException('Aborted','AbortError'));
          }));
        }
        return original(url,options);
      };
    }''')
    if entry == 'search':
        page.locator('#search-input').fill('file:abandoned'); page.locator('#search-input').press('Enter')
    else:
        page.locator('.query-editor > summary').click()
        page.locator('.query-syntax > summary').click()
        page.locator('#query-expression').fill('file:abandoned')
        page.locator('[data-expression-form] button[type=submit]').click()
    page.wait_for_function('window.abandonedStarted')
    if entry == 'filter_sort':
        page.locator('#sort-select').select_option('oldest')
        expect(page).to_have_url(re.compile(r'sort=oldest'))
        expect(page.locator('#grid .card').first).to_have_attribute('aria-label', 'image-000.png')
    else:
        page.locator('#search-input').fill('file:124'); page.locator('#search-input').press('Enter')
        expect(page.locator('#grid .card')).to_have_count(1)
        expect(page.locator('#grid .card')).to_have_attribute('aria-label','image-124.png')
    assert page.evaluate('window.abandonedCancelled')


def test_slideshow_reuses_prefetched_pixels_and_checks_access_before_showing_them(page, tmp_path):
    from playwright.sync_api import expect
    from tests.scenario_support import build_catalog, http_server
    catalog = build_catalog(tmp_path)
    requests = []
    page.on('request', lambda request: requests.append(request.url))
    with http_server(catalog.cfg) as client:
        page.add_init_script("localStorage.setItem('picshelf.lang','en')")
        page.goto(str(client.base_url))
        page.locator('#login-username').fill('reader'); page.locator('#login-password').fill(catalog.password)
        page.locator('#login-form button').click(); page.locator('.lib-card a[href^="#/library/"]').click()
        page.locator('#slideshow-btn').click(); page.locator('#ss-interval').fill('0')
        page.locator('.play-advanced > summary').click(); page.locator('#ss-sort').select_option('path')
        page.locator('#ss-start').click()
        expect(page.locator('#ss-img')).to_have_attribute('alt', '01 zero.png')
        second = f"/media/preview/{catalog.images['n02']}?"
        page.wait_for_function("part => performance.getEntriesByType('resource').some(entry=>entry.name.includes(part))", arg=second)
        page.locator('#ss-next').click()
        expect(page.locator('#ss-img')).to_have_attribute('alt', '02 unrated.png')
        assert sum(second in url for url in requests) == 1
        third = f"/media/preview/{catalog.images['n03']}?"
        page.wait_for_function("part => performance.getEntriesByType('resource').some(entry=>entry.name.includes(part))", arg=third)
        with dbm.open_db(catalog.cfg.db_path) as conn:
            conn.execute('DELETE FROM library_grants'); conn.commit()
        page.locator('#ss-next').click()
        expect(page.locator('#ss-img')).to_be_hidden()
        expect(page.locator('#ss-caption')).to_have_text('')
        assert sum(third in url for url in requests) == 1
        page.locator('#ss-exit').click()


def test_gallery_keeps_focus_and_thumbnail_after_rating_and_moves_focus_after_removal(page, tmp_path):
    from playwright.sync_api import expect
    from tests.scenario_support import build_catalog, http_server
    catalog = build_catalog(tmp_path)
    with http_server(catalog.cfg) as client:
        page.add_init_script("localStorage.setItem('picshelf.lang','en')")
        page.goto(str(client.base_url))
        page.locator('#login-username').fill('reader'); page.locator('#login-password').fill(catalog.password)
        page.locator('#login-form button').click(); page.locator('.lib-card').wait_for()
        navigate_from_sidebar(page, '#/favorites')
        expect(page.locator('#view .page-head h1')).to_have_text('My favorites')
        cards = page.locator('#grid .card')
        first_id, second_id = cards.nth(0).get_attribute('data-id'), cards.nth(1).get_attribute('data-id')
        first = page.locator(f'#grid .card[data-id="{first_id}"]')
        rating = '7' if first.locator('.rated').inner_text() != '7' else '6'
        thumbnail = first.locator('img').element_handle()
        page.wait_for_function('image => image.complete && image.naturalWidth > 0', arg=thumbnail)
        requests = []
        page.on('request', lambda request: requests.append(request.url))
        first.click(); rate_viewer(page, rating)
        with page.expect_response(re.compile(r'/api/v1/images\?')):
            page.locator('#v-close').click()
        expect(first.locator('.rated')).to_have_text(rating)
        expect(first).to_be_focused()
        assert thumbnail.evaluate('image => image.isConnected')
        assert not any(f'/media/thumb/{first_id}?' in url for url in requests)
        first.click(); page.locator('#v-fav').click()
        expect(page.locator('#v-fav')).to_have_attribute('aria-pressed', 'false')
        page.locator('#v-close').click()
        expect(first).to_have_count(0)
        expect(page.locator(f'#grid .card[data-id="{second_id}"]')).to_be_focused()


@pytest.mark.parametrize('action', ['viewer', 'save', 'nested_playback', 'slideshow'])
def test_expired_session_releases_dialogs_and_allows_sign_in(page, web_server, action):
    from playwright.sync_api import expect
    sign_in(page, web_server)
    page.locator('#grid .card').first.click()
    expect(page.locator('.viewer-stage img')).to_be_visible()
    if action != 'slideshow':
        viewer_editor(page, "tag-editor").locator("#tag-input").fill('Unsubmitted draft')
    if action in ('nested_playback', 'slideshow'):
        viewer_more(page).locator("#v-slideshow").click()
        page.locator('#ss-interval').fill('0')
    if action == 'slideshow':
        page.locator('#ss-start').click()
        expect(page.locator('#ss-img')).to_be_visible()
    page.context.clear_cookies()
    page.locator({'viewer':'.viewer-backdrop #v-next', 'save':'#tag-add',
                  'nested_playback':'#ss-start', 'slideshow':'#ss-next'}[action]).click()
    expect(page.locator('#login-form')).to_be_visible()
    expect(page.get_by_role('dialog')).to_have_count(0)
    assert page.evaluate("!document.body.classList.contains('dialog-open')")
    page.locator('#login-username').fill('browser-test')
    page.locator('#login-password').fill(web_server.password)
    page.locator('#login-form button').click()
    expect(page.get_by_role('link', name='Gallery', exact=True)).to_be_visible()
    page.locator('#grid .card').first.click()
    expect(page.locator('.viewer-stage img')).to_be_visible()
    page.keyboard.press('Escape')
    expect(page.get_by_role('dialog')).to_have_count(0)


def test_cancelled_playback_start_does_not_reopen_over_another_page(page, web_server):
    from playwright.sync_api import expect
    sign_in(page, web_server)
    navigate_from_sidebar(page, '#/search')
    page.locator('#slideshow-btn').click()
    page.locator('#ss-interval').fill('0')
    page.evaluate('''() => {
      const original = window.fetch;
      window.fetch = async (url, options) => {
        const response = await original(url, options);
        if (url === '/api/v1/play/sessions' && options?.method === 'POST')
          return new Promise(resolve => { window.releasePlayback = () => resolve(response); });
        return response;
      };
    }''')
    page.locator('#ss-start').click()
    page.wait_for_function("typeof window.releasePlayback === 'function'")
    page.keyboard.press('Escape')
    navigate_from_sidebar(page, '#/albums')
    expect(page.locator('#view h1')).to_have_text('Albums')
    page.evaluate('window.releasePlayback()')
    page.wait_for_timeout(150)
    expect(page.locator('.slideshow-backdrop')).to_have_count(0)
    expect(page.get_by_role('dialog')).to_have_count(0)


@pytest.mark.parametrize('late_result', ['response', 'failure'])
def test_batch_cancel_supersedes_an_inflight_status_read(page, tmp_path, monkeypatch, late_result):
    from playwright.sync_api import expect
    from picshelf.worker import WorkerManager
    from tests.scenario_support import build_catalog, http_server
    catalog = build_catalog(tmp_path)
    monkeypatch.setattr(WorkerManager, 'start', lambda self: None)
    with http_server(catalog.cfg) as client:
        base = str(client.base_url)
        page.add_init_script("localStorage.setItem('picshelf.lang','en')")
        page.goto(base)
        page.locator('#login-username').fill('curator')
        page.locator('#login-password').fill(catalog.password)
        page.locator('#login-form button').click()
        expect(page.locator('.lib-card')).to_have_count(2)
        headers = {'Origin': base}
        response = page.request.post(base + '/api/v1/batches/preview', headers=headers,
                                     data={'action': {'kind': 'workflow', 'value': 'done'},
                                           'image_ids': list(catalog.images.values())})
        assert response.status == 200
        batch_id = response.json()['id']
        assert page.request.post(base + f'/api/v1/batches/{batch_id}/execute', headers=headers,
                                 data={'confirm': True, 'background': True}).status == 200
        page.evaluate('''({batchId, fail}) => {
          const original = window.fetch;
          let reads = 0;
          window.fetch = async (url, options) => {
            const response = await original(url, options);
            if (url === `/api/v1/batches/${batchId}?include_items=false` && ++reads === 2) {
              await new Promise(resolve => { window.releaseStatus = resolve; });
              if (fail) throw new TypeError('Delayed connection failure');
            }
            return response;
          };
        }''', {'batchId': batch_id, 'fail': late_result == 'failure'})
        page.goto(base + f'/#/batch/{batch_id}')
        page.wait_for_function("typeof window.releaseStatus === 'function'")
        page.locator('[data-batch-cancel]').click()
        expect(page.locator('[data-batch-status]')).to_contain_text('Cancelled')
        page.evaluate('''async () => {
          window.releaseStatus();
          await new Promise(resolve => setTimeout(resolve, 50));
        }''')
        # Observe before a later poll could hide the stale response again.
        assert 'Cancelled' in page.locator('[data-batch-status]').text_content()
        assert not page.locator('[data-status-error]').is_visible()
        expect(page.locator('[data-batch-resume]')).to_be_visible()


@pytest.mark.parametrize('outcome', ['cancelled', 'failed'])
def test_library_scan_restores_controls_after_a_job_without_results(page, tmp_path, monkeypatch, outcome):
    from playwright.sync_api import expect
    from picshelf import jobs
    from picshelf.worker import WorkerManager
    from tests.scenario_support import build_catalog, http_server
    catalog = build_catalog(tmp_path)
    monkeypatch.setattr(WorkerManager, 'start', lambda self: None)
    with http_server(catalog.cfg) as client:
        base = str(client.base_url)
        page.add_init_script("localStorage.setItem('picshelf.lang','en')")
        page.goto(base)
        page.locator('#login-username').fill('curator')
        page.locator('#login-password').fill(catalog.password)
        page.locator('#login-form button').click()
        page.get_by_role('link', name='north', exact=True).click()
        page.locator('#lib-scan').click()
        expect(page.locator('.job-progress [role=status]')).to_contain_text('Queued')
        if outcome == 'cancelled':
            page.locator('.job-progress [data-cancel]').click()
        else:
            original = catalog.roots / 'north'
            offline = catalog.roots / 'north-offline'
            original.rename(offline)
            try:
                with dbm.open_db(catalog.cfg.db_path) as conn:
                    job_id = conn.execute('SELECT id FROM jobs').fetchone()[0]
                jobs.run_job(catalog.cfg, job_id)
            finally:
                offline.rename(original)
        expect(page.locator('.job-progress [role=status]')).to_contain_text(outcome.title())
        expect(page.locator('#lib-scan')).to_be_enabled()
        expect(page.locator('#lib-scan')).to_have_text('Rescan')
        page.locator('#lib-scan').click()
        expect(page.locator('.job-progress [role=status]')).to_contain_text('Queued')
        with dbm.open_db(catalog.cfg.db_path) as conn:
            assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 2
            assert {row[0] for row in conn.execute('SELECT library_id FROM jobs')} == {catalog.libraries['north']}


def test_scan_submission_reuses_lost_response_and_survives_page_reload(page, tmp_path, monkeypatch):
    from playwright.sync_api import expect
    from picshelf import jobs
    from picshelf.worker import WorkerManager
    from tests.scenario_support import build_catalog, http_server
    catalog = build_catalog(tmp_path)
    monkeypatch.setattr(WorkerManager, 'start', lambda self: None)
    requests = []
    def submit(route):
        requests.append(route.request.post_data_json)
        if len(requests) == 1:
            route.fetch().dispose()
            route.abort('failed')
        else:
            route.continue_()
    page.route('**/api/v1/libraries/*/scan-jobs', submit)
    with http_server(catalog.cfg) as client:
        base = str(client.base_url)
        page.add_init_script("localStorage.setItem('picshelf.lang','en')")
        page.goto(base)
        page.locator('#login-username').fill('curator')
        page.locator('#login-password').fill(catalog.password)
        page.locator('#login-form button').click()
        page.get_by_role('link', name='north', exact=True).click()
        page.locator('#lib-scan').click()
        expect(page.locator('.toast.error')).to_be_visible()
        page.locator('#lib-scan').click()
        expect(page.locator('.job-progress [role=status]')).to_contain_text('Queued')
        assert requests[0]['request_key'] == requests[1]['request_key']
        with dbm.open_db(catalog.cfg.db_path) as conn:
            assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 1
            job_id = conn.execute('SELECT id FROM jobs').fetchone()[0]
        expect(page).to_have_url(re.compile('job=' + job_id))
        page.reload()
        expect(page.locator('.job-progress [role=status]')).to_contain_text('Queued')
        assert len(requests) == 2
        assert jobs.run_job(catalog.cfg, job_id)
        expect(page.locator('.toast.ok')).to_contain_text('Scan complete', timeout=10000)
        expect(page.locator('#lib-scan')).to_be_enabled()
        expect(page.locator('#grid .card')).to_have_count(12)
        assert 'job=' not in page.url


@pytest.mark.parametrize('width,language', [(390, 'zh'), (1280, 'en')])
def test_multiple_libraries_batch_album_recovery_permissions_and_undo(page, tmp_path, width, language):
    from playwright.sync_api import expect
    from tests.scenario_support import build_catalog, http_server
    catalog = build_catalog(tmp_path)
    page.set_viewport_size({'width': width, 'height': 900})
    page.add_init_script(f"localStorage.setItem('picshelf.lang','{language}')")
    with http_server(catalog.cfg) as client:
        base = str(client.base_url)
        def login(username):
            page.goto(base)
            page.locator('#login-username').fill(username)
            page.locator('#login-password').fill(catalog.password)
            page.locator('#login-form button').click()
            expect(page.locator('.lib-card')).to_have_count(2 if username == 'curator' else 1)

        login('curator')
        selected = set()
        for name in ('north', 'south'):
            page.get_by_role('link', name=name, exact=True).click()
            expect(page.locator('#grid .card')).to_have_count(12)
            expect(page.locator('[data-selection-toggle]')).to_have_attribute('aria-expanded', 'false')
            assert page.locator('[data-select-image]:checked').count() == 0
            page.locator('[data-selection-toggle]').click()
            for index in (0, 1):
                checkbox = page.locator('[data-select-image]').nth(index)
                selected.add(int(checkbox.get_attribute('data-select-image')))
                checkbox.check()
            page.locator('[data-batch-names]').fill('multi-review')
            page.locator('[data-batch-preview]').click()
            expect(page.locator('.batch-targets li')).to_have_count(2)
            assert all(text.startswith(name + ' / ') for text in page.locator('.batch-targets li').all_text_contents())
            page.locator('[data-batch-apply]').click()
            expect(page.locator('[data-batch-status]')).to_contain_text('已完成' if language == 'zh' else 'Completed')
            navigate_from_sidebar(page, '#/home')
            expect(page.locator('.lib-card')).to_have_count(2)

        page.locator('#search-input').fill('tag:multi-review')
        page.locator('#search-input').press('Enter')
        expect(page.locator('#grid .card')).to_have_count(4)
        assert {int(value) for value in page.locator('#grid .card').evaluate_all('(cards) => cards.map(card => card.dataset.id)')} == selected
        assert sorted(page.locator('#grid .library-name').all_text_contents()) == ['north', 'north', 'south', 'south']
        page.locator('[data-selection-toggle]').click()
        page.locator('[data-select-all]').click()
        page.locator('[data-batch-operation]').select_option('album_add')
        page.locator('[data-batch-target]').click()
        page.locator('.modal [data-new-album]').click()
        page.locator('.catalog-form [name=name]').fill('Multi-library picks')
        page.locator('.catalog-form [name=library_id]').select_option(str(catalog.libraries['north']))
        page.locator('[data-album-submit]').click()
        expect(page.get_by_role('dialog')).to_have_count(0)
        expect(page.locator('[data-batch-target]')).to_have_text('Multi-library picks')
        page.locator('[data-batch-preview]').click()
        expect(page.locator('.batch-targets li')).to_have_count(4)
        def lose_reply(route):
            route.fetch().dispose()
            route.abort('failed')
        page.route('**/api/v1/batches/*/execute', lose_reply, times=1)
        page.locator('[data-batch-apply]').click()
        expect(page.locator('.toast.error')).to_be_visible()
        with dbm.open_db(catalog.cfg.db_path) as conn:
            album_id = conn.execute("SELECT id FROM albums WHERE name='Multi-library picks'").fetchone()[0]
            batch_id = conn.execute("SELECT id FROM metadata_batches WHERE json_extract(action,'$.album_id')=?", (album_id,)).fetchone()[0]
            before_retry = [tuple(row) for row in conn.execute('SELECT * FROM album_images WHERE album_id=? ORDER BY image_id', (album_id,))]
            assert {row[1] for row in before_retry} == selected
        page.locator('[data-batch-apply]').click()
        expect(page.locator('[data-batch-status]')).to_contain_text('已完成' if language == 'zh' else 'Completed')
        with dbm.open_db(catalog.cfg.db_path) as conn:
            assert [tuple(row) for row in conn.execute('SELECT * FROM album_images WHERE album_id=? ORDER BY image_id', (album_id,))] == before_retry
            assert conn.execute("SELECT COUNT(*) FROM metadata_batches WHERE json_extract(action,'$.album_id')=?", (album_id,)).fetchone()[0] == 1
        navigate_from_sidebar(page, '#/albums')
        page.locator('.collection-toolbar input').fill('Multi-library picks')
        page.locator('.album-grid .card', has_text='Multi-library picks').click()
        expect(page.locator('#grid .card')).to_have_count(4)
        assert sorted(page.locator('#grid .library-name').all_text_contents()) == ['north', 'north', 'south', 'south']
        page.wait_for_function("[...document.querySelectorAll('#grid .card img')].every(img => img.complete && img.naturalWidth > 0)")
        expect(page.locator('.toast.error')).to_have_count(0)
        page.screenshot(path=str(tmp_path / f'multi-library-{language}.png'), full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')

        page.context.clear_cookies()
        login('reader')
        page.locator('#search-input').fill('tag:multi-review')
        page.locator('#search-input').press('Enter')
        expect(page.locator('#grid .card')).to_have_count(2)
        north = {iid for key, iid in catalog.images.items() if key.startswith('n')}
        assert {int(value) for value in page.locator('#grid .card').evaluate_all('(cards) => cards.map(card => card.dataset.id)')} == selected & north
        page.goto(base + f'/#/album/{album_id}')
        expect(page.locator('#grid .card')).to_have_count(2)
        expect(page.locator('[data-edit-album]')).to_have_count(0)
        expect(page.locator('#grid .library-name')).to_have_count(0)
        assert page.request.get(base + f'/api/v1/batches/{batch_id}').status == 404

        page.context.clear_cookies()
        login('curator')
        navigate_from_sidebar(page, '#/batches')
        page.locator(f'a[href="#/batch/{batch_id}"]').click()
        expect(page.locator('tbody tr')).to_have_count(4)
        page.locator('[data-batch-undo]').click()
        page.locator('[data-yes]').click()
        expect(page.locator('[data-batch-status]')).to_contain_text('已撤销' if language == 'zh' else 'Undone')
        page.reload()
        expect(page.locator('[data-batch-status]')).to_contain_text('已撤销' if language == 'zh' else 'Undone')
        with dbm.open_db(catalog.cfg.db_path) as conn:
            assert conn.execute('SELECT COUNT(*) FROM album_images WHERE album_id=?', (album_id,)).fetchone()[0] == 0
            assert {row[0] for row in conn.execute("SELECT image_id FROM image_tags JOIN tags ON tags.id=image_tags.tag_id WHERE name='multi-review'")} == selected


def test_batch_undo_discards_a_grid_refresh_started_before_the_undo(page, tmp_path):
    from playwright.sync_api import expect
    from tests.scenario_support import build_catalog, http_server
    catalog = build_catalog(tmp_path)
    with http_server(catalog.cfg) as client:
        page.add_init_script("localStorage.setItem('picshelf.lang','en')")
        page.goto(str(client.base_url))
        page.locator('#login-username').fill('curator')
        page.locator('#login-password').fill(catalog.password)
        page.locator('#login-form button').click()
        page.get_by_role('link', name='north', exact=True).click()
        page.locator('[data-selection-toggle]').click()
        page.locator('[data-select-loaded]').click()
        page.locator('[data-batch-operation]').select_option('favorite_on')
        page.locator('[data-batch-preview]').click()
        page.locator('[data-batch-apply]').click()
        expect(page.locator('#grid .badge.fav')).to_have_count(12)
        page.evaluate('''() => {
          const original = window.fetch;
          let held = false;
          window.fetch = async (url, options) => {
            const response = await original(url, options);
            if (!held && url.startsWith('/api/v1/images?')) {
              held = true;
              const body = await response.text();
              await new Promise(resolve => {window.releaseGrid = resolve;});
              return new Response(body, {headers:response.headers, status:response.status});
            }
            return response;
          };
        }''')
        page.locator('[data-refresh]').click()
        page.wait_for_function("typeof window.releaseGrid === 'function'")
        page.locator('[data-batch-undo]').click()
        page.locator('[data-yes]').click()
        expect(page.locator('[data-batch-status]')).to_contain_text('Undone')
        page.evaluate('window.releaseGrid()')
        expect(page.locator('[data-refresh]')).to_be_enabled()
        expect(page.locator('#grid .badge.fav')).to_have_count(0)
        page.reload()
        expect(page.locator('#grid .card')).to_have_count(12)
        expect(page.locator('#grid .badge.fav')).to_have_count(0)


@pytest.mark.parametrize('width', [390, 1440])
def test_viewer_pilot_defaults_to_image_and_all_expanded_controls_follow_rules(page, web_server, width):
    from playwright.sync_api import expect
    page.set_viewport_size({'width': width, 'height': 1000})
    page.add_init_script("localStorage.setItem('picshelf.panel', '1')")
    sign_in(page, web_server)
    card = page.locator('#grid .card').first
    card.click()
    viewer = page.locator('.viewer-backdrop')
    expect(viewer.locator('.viewer-stage img')).to_be_visible()
    expect(viewer.locator('#viewer-details')).to_be_hidden()
    expect(viewer.locator('#v-panel')).to_have_attribute('aria-expanded', 'false')
    expect(viewer.locator('#v-close')).to_contain_text('Back')
    expect(viewer.locator('#v-close')).to_contain_text('Esc')
    show_viewer_info(page)
    expect(viewer.locator('#v-panel')).to_have_attribute('aria-label', 'Hide info')
    expect(viewer.locator('[data-field="rating"]')).to_have_accessible_name('My rating 0–10')
    rating_label_gap = viewer.locator('label[for="v-rating"]').evaluate('''label => {
      const text = document.createRange();
      text.selectNodeContents(label.firstChild);
      return label.querySelector('.viewer-caption').getBoundingClientRect().left - text.getBoundingClientRect().right;
    }''')
    assert rating_label_gap == pytest.approx(4, abs=0.5)
    # Closed details can retain layout rectangles; assert actual visibility instead.
    for field in viewer.locator('#viewer-details input:not([type=range]), #viewer-details textarea, #viewer-details select').all():
        expect(field).to_be_hidden()
    expect(viewer.locator('#viewer-identity')).to_have_count(0)
    expect(viewer.locator('#viewer-summary')).not_to_contain_text(card.locator('.fname').text_content())
    expect(viewer.locator('#viewer-summary')).to_have_css('font-size', '12px')
    expect(viewer.locator('.viewer-summary')).to_contain_text('×')
    # Count the actual panel, including every editor and menu, rather than a quiet collapsed shell.
    viewer.locator('details').evaluate_all('elements => elements.forEach(el => el.open = true)')
    measured = viewer.evaluate('''root => {
      const panel = root.querySelector('#viewer-details');
      const measure = (scope, excludePanel = false) => {
        const elements = [scope, ...scope.querySelectorAll('*')].filter(el => el.getClientRects().length &&
          getComputedStyle(el).visibility !== 'hidden' && (!excludePanel || !panel.contains(el)));
        const values = props => [...new Set(elements.flatMap(el => props.map(prop => parseFloat(getComputedStyle(el)[prop]))).filter(n => Number.isFinite(n) && n > 0))];
        const buttons = elements.filter(el => el.tagName === 'BUTTON');
        return {fonts: values(['fontSize']), radii: values(['borderTopLeftRadius','borderTopRightRadius','borderBottomLeftRadius','borderBottomRightRadius']),
          padding: values(['paddingTop','paddingRight','paddingBottom','paddingLeft']), gaps: values(['rowGap','columnGap']),
          buttonPadding: [...new Set(buttons.map(el => getComputedStyle(el).padding))], buttonFonts: [...new Set(buttons.map(el => getComputedStyle(el).fontSize))],
          roles: [...new Set(buttons.map(el => el.className))],
          maxFont: Math.max(...values(['fontSize'])),
          maxTargets: elements.filter(el=>parseFloat(getComputedStyle(el).fontSize)===Math.max(...values(['fontSize']))).map(el=>el.id),
          headings: elements.filter(el=>el.matches('h1,h2,h3,h4,h5,h6')).length,
          pills: elements.filter(el=>el.matches('.pill,.chip,[class*="chip"]')).length,
          boxes: elements.filter(el=>['Top','Right','Bottom','Left'].filter(side=>parseFloat(getComputedStyle(el)['border'+side+'Width'])>0 && getComputedStyle(el)['border'+side+'Style']!=='none').length>=3).length,
          primaryActions: buttons.filter(el=>el.className==='viewer-primary').map(el=>el.getAttribute('data-workflow')),
          tiny: elements.filter(el => el.matches('button,input,select,textarea,summary,a[href]'))
            .filter(el => {const r = el.getBoundingClientRect(); return r.height < 43.9 || r.width < 43.9;}).map(el => el.outerHTML)};
      };
      const fields=[...panel.querySelectorAll('.viewer-field')];
      // Labels have a compact inline gap; property rows and groups retain 8/24 spacing.
      const spacing=[...new Set([panel,...panel.querySelectorAll('*')].filter(el=>el.getClientRects().length&&!el.matches('.viewer-field-label')).flatMap(el=>['marginTop','marginRight','marginBottom','marginLeft','paddingTop','paddingRight','paddingBottom','paddingLeft','rowGap','columnGap'].map(prop=>parseFloat(getComputedStyle(el)[prop]))).filter(n=>Number.isFinite(n)&&n>0))];
      const chrome=measure(root,true), max=chrome.maxFont;
      const title=root.querySelector('.viewer-top .viewer-heading h1.title');
      const allMax=[root,...root.querySelectorAll('*')].filter(el=>el.getClientRects().length&&parseFloat(getComputedStyle(el).fontSize)===max);
      return {chrome, panel: measure(panel), overflow: root.scrollWidth > root.clientWidth,spacing,
        maxIsTitle:allMax.length===1&&allMax[0]===title,
        unlabeled:fields.filter(el=>el.querySelectorAll(':scope > .viewer-field-label').length!==1||el.querySelectorAll(':scope > .viewer-field-value').length!==1).length};
    }''')
    assert not measured['overflow']
    for name in ('chrome', 'panel'):
        data = measured[name]
        assert set(data['fonts']) <= {12, 14, 16, 20}, data
        assert set(data['radii']) <= {4, 8, 12}, data
        assert set(data['padding']) <= {4, 8, 12, 16, 24, 32}, data
        assert len(data['gaps']) <= 5, data
        assert len(data['buttonPadding']) <= 2, data
        assert data['buttonFonts'] == ['14px'], data
        assert set(data['roles']) <= {'viewer-action', 'viewer-primary', 'viewer-icon'}, data
        assert not data['tiny'], data
    content = measured['panel']
    assert set(content['fonts']) <= {12, 14}, content
    assert measured['maxIsTitle'], measured
    assert measured['unlabeled'] == 0, measured
    assert set(measured['spacing']) == {8,24}, measured
    assert content['headings'] == 0 and content['pills'] == 0, content
    assert content['boxes'] <= 6, content
    assert len(content['primaryActions']) <= 1 and set(content['primaryActions']) <= {'done'}, content
    # A panel on a phone shares space with the image; it must not cover the stage.
    stage = viewer.locator('.viewer-stage').bounding_box()
    panel = viewer.locator('#viewer-details').bounding_box()
    if width == 390:
        assert panel['y'] >= stage['y'] + stage['height'] - 1
    viewer.locator('.viewer-more > summary').click()
    viewer.locator('#v-close').click()
    expect(viewer).to_have_count(0)
    expect(card).to_be_focused()


def test_viewer_pilot_rating_keeps_all_values_zero_clear_and_keyboard_focus(page, web_server):
    from playwright.sync_api import expect
    sign_in(page, web_server)
    card = page.locator('#grid .card').first
    image_id = card.get_attribute('data-id')
    card.click()
    show_viewer_info(page)
    expect(page.locator('label[for="v-rating"]')).to_have_text('My rating 0–10')
    clear = page.locator('#v-rating-clear')
    if clear.is_enabled():
        clear.click()
        expect(clear).to_be_disabled()
    # Each value remains a single pointer action, including zero from an unrated state.
    for value in range(11):
        rate_viewer(page, value)
        assert page.request.get(web_server.url + f'/api/v1/images/{image_id}').json()['my_rating'] == value
    # A single click at 70% selects 7; each arrow press then persists its exact next value.
    expect(page.locator('.viewer-rating-scale')).to_have_count(0)
    rate_viewer(page, 7)
    slider = page.locator('#v-rating')
    assert page.request.get(web_server.url + f'/api/v1/images/{image_id}').json()['my_rating'] == 7
    for value in (8, 9):
        slider.press('ArrowRight')
        expect(slider).to_have_value(str(value))
        expect(slider).to_be_enabled()
        expect(slider).to_be_focused()
        assert page.request.get(web_server.url + f'/api/v1/images/{image_id}').json()['my_rating'] == value
    clear.click()
    expect(clear).to_be_disabled()
    assert page.request.get(web_server.url + f'/api/v1/images/{image_id}').json()['my_rating'] == -1
    slider = page.locator('#v-rating')
    expect(slider).to_be_enabled()
    expect(slider).to_have_attribute('aria-valuetext', 'Not rated')
    slider.press('Home')
    expect(clear).to_be_enabled()
    expect(slider).to_have_value('0')
    expect(slider).to_be_focused()
    page.locator('#v-close').click()
    expect(page.locator('.modal-backdrop')).to_have_count(0)
    expect(card).to_be_focused()


@pytest.mark.parametrize('width', [390, 1440])
def test_viewer_empty_fields_keep_labels_and_add_actions_without_placeholders(page, web_server, width):
    from playwright.sync_api import expect
    page.set_viewport_size({'width':width,'height':1000})
    sign_in(page,web_server)
    card=page.locator('#grid .card').first
    image_id=card.get_attribute('data-id')
    # A read-only response fixture exercises absent values without changing stored metadata.
    def empty_metadata(route):
        response=route.fetch()
        data=response.json()
        data.update(tags=[],artists=[],albums=[])
        data['metadata']['tag_claims']=[]
        route.fulfill(response=response,json=data)
    page.route(f'**/api/v1/images/{image_id}',empty_metadata)
    card.click()
    viewer=show_viewer_info(page)
    for field,label,editor in [('tags','Tags','tag-editor'),('artists','Artists','artist-editor'),('albums','Albums',None)]:
        row=viewer.locator(f'.viewer-field[data-field={field}]')
        expect(row.locator(':scope > .viewer-field-label')).to_have_text(label)
        value=row.locator(':scope > .viewer-field-value')
        assert value.inner_text().strip()
        assert '(none)' not in value.inner_text().lower()
        assert '—' not in value.inner_text()
        left=row.locator('.viewer-field-label').bounding_box()
        right=value.bounding_box()
        assert left['x']+left['width'] <= right['x']
        if editor:
            value.locator(f'[data-section={editor}] > summary').click()
            expect(value.locator('input')).to_be_visible()
            value.locator(f'[data-section={editor}] > summary').click()
    expect(viewer.locator('#viewer-summary')).to_contain_text('UTC')
    expect(viewer.locator('#viewer-identity')).to_have_count(0)
    page.locator('#v-close').click()
