"""A deployment bump moves the reader pin, regenerates the image constraints from that commit's
uv.lock (the command is in pyproject.toml) and updates the configs' endpoints. These guards catch
what can be checked offline; that the constraints were regenerated is not."""

import re
import tomllib
from pathlib import Path

from eopf_accept.cli import CONFIG_DIR

ROOT = Path(__file__).parents[1]
PYPROJECT = (ROOT / "pyproject.toml").read_text()
LOCKED = {p["name"]: p.get("version") for p in tomllib.loads((ROOT / "uv.lock").read_text())["package"]}
ENDPOINTS = {p.name: tomllib.loads(p.read_text()).get("endpoints") or {} for p in sorted(CONFIG_DIR.glob("*.toml"))}


def test_image_constraints_name_the_reader_pin():
    pin = re.search(r'titiler-eopf @ git\+\S+@([0-9a-f]{40})"', PYPROJECT)
    assert pin, "the reader extra must pin titiler-eopf to a full commit SHA"
    assert f"uv.lock of titiler-eopf {pin.group(1)}" in PYPROJECT, "regenerate constraint-dependencies at the reader pin"


def test_rstaging_expects_the_locked_reader_version():
    """TR01 and the real-app tests run the locked titiler-eopf: the configs must expect /rstaging to report it."""
    for name, eps in ENDPOINTS.items():
        if "rstaging" in eps:
            assert eps["rstaging"]["expect_version"] == LOCKED["titiler-eopf"], name


def test_each_endpoint_is_the_same_in_every_config():
    seen = {}
    for name, eps in ENDPOINTS.items():
        for ep, table in eps.items():
            assert seen.setdefault(ep, table) == table, (name, ep)
    assert {"rstaging", "raster"} <= seen.keys()
