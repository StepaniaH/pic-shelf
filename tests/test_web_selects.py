"""Shared select presentation and native interactions in the real application."""
import os

import pytest

from tests.test_web import browser, page, web_server, sign_in

pytestmark = [pytest.mark.browser, pytest.mark.skipif(
    os.environ.get('PICSHELF_BROWSER_TESTS') != '1', reason='opt-in browser suite')]


@pytest.mark.parametrize('language,width', [('en',1280), ('zh',390)])
def test_sort_picker_mouse_keyboard_and_cancel(page, web_server, language, width):
    from playwright.sync_api import expect
    page.set_viewport_size({'width':width, 'height':850})
    sign_in(page, web_server, language)
    page.goto(web_server.url + '/#/search')
    picker = page.locator('#sort-select')
    expect(picker).to_have_value('newest')
    assert page.evaluate("CSS.supports('appearance', 'base-select')")
    picker.click()
    assert picker.evaluate("element => element.matches(':open')")
    page.locator('#sort-select option[value=oldest]').click()
    expect(page.locator('#sort-select')).to_have_value('oldest')
    picker = page.locator('#sort-select')
    width_before = picker.bounding_box()['width']
    picker.click()
    page.keyboard.press('ArrowDown')
    page.keyboard.press('Enter')
    expect(page.locator('#sort-select')).to_have_value('path')
    picker = page.locator('#sort-select')
    assert picker.bounding_box()['width'] == width_before
    picker.click()
    page.keyboard.press('ArrowDown')
    page.keyboard.press('Escape')
    expect(picker).to_have_value('path')
    assert not picker.evaluate("element => element.matches(':open')")
    expect(picker).to_be_focused()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


@pytest.mark.parametrize('width', [1280,390])
def test_form_picker_escape_does_not_close_dialog_and_commit_keeps_form(page, web_server, width):
    from playwright.sync_api import expect
    page.set_viewport_size({'width':width, 'height':850})
    sign_in(page, web_server)
    page.locator('[data-new-library]').first.click()
    page.locator('#nl-name').fill('Dropdown form')
    page.locator('[data-library-options] > summary').click()
    picker = page.locator('#nl-mode')
    picker.click()
    assert picker.evaluate("element => element.matches(':open')")
    page.keyboard.press('Escape')
    expect(page.locator('.modal')).to_have_count(1)
    expect(picker).to_have_value('managed')
    expect(picker).to_be_focused()
    picker.click()
    page.locator('#nl-mode option[value=indexed]').click()
    expect(picker).to_have_value('indexed')
    expect(page.locator('#imp-drop')).to_be_hidden()
    expect(page.locator('#nl-path')).to_have_attribute('required', '')
    expect(page.locator('#nl-name')).to_have_value('Dropdown form')
    page.keyboard.press('Escape')
    expect(page.locator('.modal')).to_have_count(0)


@pytest.mark.parametrize('width', [1280,390])
def test_album_picker_handles_long_names_scrolling_and_dynamic_options(page, web_server, width):
    from playwright.sync_api import expect
    sign_in(page, web_server)
    page.set_viewport_size({'width':width, 'height':750})
    long_name = 'A library with a long name / 很长的图库名称 / ' + str(width)
    for index in range(14):
        response = page.request.post(web_server.url + '/api/v1/libraries',
            headers={'Origin':web_server.url}, data={'name':f'{long_name} {index:02}', 'mode':'managed'})
        assert response.ok, response.text()
    last_id = str(response.json()['id'])
    page.goto(web_server.url + '/#/albums')
    page.locator('[data-new-album]').click()
    picker = page.locator('#album-form [name=library_id]')
    picker.click()
    last = picker.locator(f'option[value="{last_id}"]')
    last.scroll_into_view_if_needed()
    box = last.bounding_box()
    assert box['x'] >= 0 and box['x'] + box['width'] <= width
    assert box['y'] >= 0 and box['y'] + box['height'] <= 750
    assert box['height'] < 150
    last.click()
    expect(picker).to_have_value(last_id)
    expect(page.locator('#imp-drop')).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.keyboard.press('Escape')
    expect(page.locator('.modal')).to_have_count(0)


def test_touch_picker_has_readable_text_and_large_rows(browser, web_server):
    from playwright.sync_api import expect
    page = browser.new_page(viewport={'width':390, 'height':844}, is_mobile=True, has_touch=True)
    try:
        sign_in(page, web_server, 'zh')
        page.goto(web_server.url + '/#/search')
        picker = page.locator('#sort-select')
        assert page.evaluate("matchMedia('(pointer: coarse)').matches")
        assert picker.evaluate('element => getComputedStyle(element).fontSize') == '16px'
        assert picker.evaluate('element => getComputedStyle(element).webkitTapHighlightColor') == 'rgba(0, 0, 0, 0)'
        picker.tap()
        option = picker.locator('option[value=path]')
        assert option.bounding_box()['height'] >= 44
        option.tap()
        expect(page.locator('#sort-select')).to_have_value('path')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.emulate_media(forced_colors='active')
        assert page.locator('#sort-select').evaluate('element => getComputedStyle(element).appearance') == 'auto'
    finally:
        page.close()
