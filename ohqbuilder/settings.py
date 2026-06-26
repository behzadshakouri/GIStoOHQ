from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ProjectPaths:
    root: Path
    site: str
    outputs_dir: str = "outputs"
    topology: str = "topology.gpkg"
    subbasins: str = "subwatershed_params.gpkg"
    reaches: str = "reaches.gpkg"
    junctions: str = "junctions.gpkg"

    @property
    def site_path(self) -> Path:
        return self.root / self.site

    @property
    def outputs_path(self) -> Path:
        return self.site_path / self.outputs_dir

    def output_file(self, name: str) -> Path:
        return self.outputs_path / name


@dataclass
class OHQSettings:
    template: str | None = None
    include_comments: bool = True
    default_subbasin_type: str = "cn_catchment"
    default_reach_type: str = "trapezoidal_channel"
    default_junction_type: str = "mixer"
    default_outlet_type: str = "outlet"


@dataclass
class BuilderSettings:
    project_name: str
    paths: ProjectPaths
    ohq: OHQSettings = field(default_factory=OHQSettings)
    defaults: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_file(cls, config_path: Path, root: Path | None = None, site: str | None = None) -> "BuilderSettings":
        data = yaml.safe_load(config_path.read_text()) or {}
        project = data.get("project", {})
        paths = data.get("paths", {})
        root_path = Path(root or data.get("root") or ".").expanduser().resolve()
        site_name = site or data.get("site") or project.get("site") or "WS3_GIS/AZ12-100"
        pp = ProjectPaths(
            root=root_path,
            site=site_name,
            outputs_dir=paths.get("outputs_dir", "outputs"),
            topology=paths.get("topology", "topology.gpkg"),
            subbasins=paths.get("subbasins", "subwatershed_params.gpkg"),
            reaches=paths.get("reaches", "reaches.gpkg"),
            junctions=paths.get("junctions", "junctions.gpkg"),
        )
        site_base = Path(site_name).name.replace("-", "_")
        name = project.get("name") or site_base
        return cls(
            project_name=name,
            paths=pp,
            ohq=OHQSettings(**(data.get("ohq") or {})),
            defaults=data.get("defaults") or {},
        )

    @classmethod
    def from_args(cls, root: str, site: str, config: str | None = None, project_name: str | None = None) -> "BuilderSettings":
        if config:
            obj = cls.from_file(Path(config), Path(root), site)
        else:
            pp = ProjectPaths(root=Path(root).expanduser().resolve(), site=site)
            obj = cls(project_name=Path(site).name.replace("-", "_"), paths=pp)
        if project_name:
            obj.project_name = project_name
        return obj
