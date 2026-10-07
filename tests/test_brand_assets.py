"""The iPhone app's service logos are generated from the web client's icon set
(client-swift/tools/gen-brands.py). Both clients must show the same mark for
the same service, so the generated files may not drift from web/icons.js."""
import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).parent.parent
GEN = ROOT / "client-swift" / "tools" / "gen-brands.py"


def _gen():
    spec = importlib.util.spec_from_file_location("gen_brands", GEN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_brand_mark_is_in_the_asset_catalog():
    gen = _gen()
    brands, _ = gen.parse_icons((ROOT / "web" / "icons.js").read_text(encoding="utf-8"))
    assert len(brands) >= 50
    for slug, (_, path) in brands.items():
        folder = gen.CATALOG / f"brand-{slug}.imageset"
        svg = (folder / f"brand-{slug}.svg").read_text(encoding="utf-8")
        assert f'd="{path}"' in svg, slug
        props = json.loads((folder / "Contents.json").read_text())["properties"]
        assert props["template-rendering-intent"] == "template", slug
    assert (gen.CATALOG / "pocketadm-mark.imageset" / "pocketadm-mark.png").exists()


def test_the_swift_tables_match_the_web_client():
    gen = _gen()
    brands, aliases = gen.parse_icons((ROOT / "web" / "icons.js").read_text(encoding="utf-8"))
    assert gen.SWIFT.read_text(encoding="utf-8") == gen.swift_source(brands, aliases), \
        "run client-swift/tools/gen-brands.py"
