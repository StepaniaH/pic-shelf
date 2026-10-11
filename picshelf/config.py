"""运行配置：数据目录解析与各子路径。

优先级：显式参数 > 环境变量 PICSHELF_DATA > 仓库内 ./data
（默认值锚定在仓库目录，保证从任意 cwd 运行都落在项目文件夹内。）
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 39431
ENV_DATA = "PICSHELF_DATA"

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Config:
    data_dir: Path

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
        for d in (
            self.data_dir,
            self.media_dir,
            self.thumbs_dir,
            self.previews_dir,
            self.playlists_dir,
            self.exports_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)
        return self

    @classmethod
    def resolve(cls, data_dir: str | Path | None = None) -> "Config":
        if data_dir is not None:
            return cls(Path(data_dir).expanduser().resolve())
        env = os.environ.get(ENV_DATA)
        if env:
            return cls(Path(env).expanduser().resolve())
        return cls(REPO_ROOT / "data")
