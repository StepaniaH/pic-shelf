"""运行配置：数据目录解析与各子路径。

优先级：显式参数 > 环境变量 PICSHELF_DATA > 可识别源码目录内 ./data。
安装包要求显式指定数据目录，不写 site-packages 或静默新建另一套数据。
"""
from __future__ import annotations

import os
import tempfile
import tomllib
import ipaddress
from urllib.parse import urlsplit
from dataclasses import dataclass
from pathlib import Path

from .errors import ServiceError
from .i18n import t

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 39431
ENV_DATA = "PICSHELF_DATA"

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Config:
    data_dir: Path
    public_origin: str | None = None
    trusted_proxies: tuple[str, ...] = ()

    def __post_init__(self):
        try:
            if self.public_origin:
                parsed = urlsplit(self.public_origin)
                if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username
                        or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
                    raise ValueError
                _ = parsed.port
            for proxy in self.trusted_proxies:
                ipaddress.ip_network(proxy, strict=False)
        except ValueError as exc:
            raise ServiceError("server_config_invalid", t("站点来源或可信代理配置无效"),
                               next_step=t("请使用完整的 HTTP(S) 站点来源；可信代理必须为明确的 IP 或网段，不能使用星号")) from exc

    @property
    def db_path(self) -> Path:
        return self.data_dir / "picshelf.db"

    @property
    def secret_path(self) -> Path:
        return self.data_dir / "secret.key"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def thumbs_dir(self) -> Path:
        return self.media_dir / "thumbs"

    @property
    def previews_dir(self) -> Path:
        return self.media_dir / "previews"

    @property
    def playlists_dir(self) -> Path:
        return self.data_dir / "playlists"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    def ensure(self) -> "Config":
        try:
            for d in (
                self.data_dir,
                self.media_dir,
                self.thumbs_dir,
                self.previews_dir,
                self.playlists_dir,
                self.exports_dir,
            ):
                d.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=self.data_dir):
                pass
        except OSError as exc:
            raise ServiceError("data_directory_unwritable", t("指定的数据目录不可写"),
                               next_step=t("请修复目录权限，或明确指定可写目录：picshelf --data <数据目录> init")) from exc
        return self

    @classmethod
    def resolve(cls, data_dir: str | Path | None = None) -> "Config":
        web = {"public_origin": os.environ.get("PICSHELF_PUBLIC_ORIGIN") or None,
               "trusted_proxies": tuple(p.strip() for p in os.environ.get("PICSHELF_TRUSTED_PROXIES", "").split(",") if p.strip())}
        if data_dir is not None:
            return cls(Path(data_dir).expanduser().resolve(), **web)
        env = os.environ.get(ENV_DATA)
        if env:
            return cls(Path(env).expanduser().resolve(), **web)
        try:
            project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
            source_checkout = project.get("project", {}).get("name") == "pic-shelf" and (REPO_ROOT / "picshelf").is_dir()
        except (OSError, ValueError):
            source_checkout = False
        if not source_checkout:
            raise ServiceError("data_directory_required", t("安装包需要明确的数据目录"),
                               next_step=t("请先运行 picshelf --data <数据目录> init，并在后续命令中使用同一路径或设置 PICSHELF_DATA"))
        return cls(REPO_ROOT / "data", **web)
