"""i18n：语言解析、翻译回退、CLI / API 双语。"""

from click.testing import CliRunner
from fastapi.testclient import TestClient

from picshelf import i18n
from picshelf.api import create_app
from picshelf.cli import main


def test_resolve_priority(monkeypatch):
    monkeypatch.delenv("PICSHELF_LANG", raising=False)
    monkeypatch.setenv("LC_ALL", "zh_CN.UTF-8")
    assert i18n.resolve_lang() == "zh"
    monkeypatch.setenv("PICSHELF_LANG", "en")
    assert i18n.resolve_lang() == "en"
    assert i18n.resolve_lang("zh") == "zh"


def test_t_fallback_and_format():
    i18n.set_lang("zh")
    assert i18n.t("未登录") == "未登录"
    assert i18n.t("测试专用中文串") == "测试专用中文串"  # 目录里没有 → 原样回退
    i18n.set_lang("en")
    assert i18n.t("未登录") == "Not signed in"
    assert i18n.t("共 {v0} 张（显示前 {v1}）", v0=70, v1=50) == "70 images (showing first 50)"
    i18n.set_lang("zh")


def test_cli_lang_flag():
    try:
        r = CliRunner().invoke(main, ["--lang", "en", "--help"])
        assert r.exit_code == 0
        assert "self-hosted image library" in r.output
        assert "UI language" in r.output
        r = CliRunner().invoke(main, ["--lang", "zh", "--help"])
        assert r.exit_code == 0
        assert "自托管图片库" in r.output
    finally:
        i18n.set_lang("zh")  # --lang 是 eager 全局切换，测试后必须还原


def test_api_accept_language(cfg):
    client = TestClient(create_app(cfg))
    r = client.get("/api/v1/libraries", headers={"Accept-Language": "en"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Not signed in"
    r = client.get("/api/v1/libraries", headers={"Accept-Language": "zh-CN,zh;q=0.9"})
    assert r.status_code == 401
    assert r.json()["detail"] == "未登录"
