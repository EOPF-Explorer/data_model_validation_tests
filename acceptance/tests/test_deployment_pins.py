"""A deployment bump touches the reader pin, the image constraints and four configs: each guard
fails when one of them is left behind."""

import re
import tomllib
from pathlib import Path

from eopf_accept.cli import CONFIG_DIR

PYPROJECT = (Path(__file__).parents[1] / "pyproject.toml").read_text()


def test_image_constraints_come_from_the_reader_pin():
    pin = re.search(r"titiler-eopf@([0-9a-f]{40})", PYPROJECT).group(1)
    assert f"uv.lock of titiler-eopf {pin}" in PYPROJECT


def test_every_config_targets_the_same_deployments():
    endpoints = {p.name: tomllib.loads(p.read_text()).get("endpoints") for p in sorted(CONFIG_DIR.glob("*.toml"))}
    endpoints = {name: ep for name, ep in endpoints.items() if ep}
    assert len(endpoints) >= 4 and all(ep == next(iter(endpoints.values())) for ep in endpoints.values()), endpoints
