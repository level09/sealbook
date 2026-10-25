import unittest
from pathlib import Path

import stk

from sealbook import assets
from sealbook.app import create_app
from sealbook.settings import Config


class AssetConfig(stk.TestingConfig, Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite+aiosqlite:///:memory:"
    SESSION_TYPE = None
    QUART_RATE_LIMITER_ENABLED = False


class AssetTests(unittest.IsolatedAsyncioTestCase):
    async def test_templates_version_assets_and_map_modules(self):
        client = create_app(AssetConfig).test_client()
        html = await (await client.get("/login")).get_data(as_text=True)
        self.assertRegex(html, r'href="/static/css/app\.css\?v=[0-9a-f]{12}"')
        self.assertRegex(html, r'src="/static/js/config\.js\?v=[0-9a-f]{12}"')
        self.assertNotRegex(html, r'(src|href)="/static/[^"?]*"')

    async def test_import_map_versions_nested_modules(self):
        app = create_app(AssetConfig)
        async with app.test_request_context("/"):
            tag = app.jinja_env.globals["import_map"]()
        self.assertRegex(
            tag,
            r'"/static/js/components/media\.js": "/static/js/components/media\.js\?v=[0-9a-f]{12}"',
        )

    async def test_changing_a_file_changes_its_version(self):
        app = create_app(AssetConfig)
        path = Path(assets.__file__).parent / "static" / "js" / "_version_probe.js"
        try:
            path.write_text("a")
            async with app.test_request_context("/"):
                first = assets.asset("js/_version_probe.js")
            path.write_text("b")
            async with app.test_request_context("/"):
                self.assertNotEqual(first, assets.asset("js/_version_probe.js"))
        finally:
            path.unlink(missing_ok=True)

    async def test_core_assets_resolve_from_stk(self):
        app = create_app(AssetConfig)
        async with app.test_request_context("/"):
            self.assertRegex(assets.asset("js/vue.min.js"), r"\?v=[0-9a-f]{12}$")

    async def test_cache_headers(self):
        client = create_app(AssetConfig).test_client()
        versioned = await client.get("/static/css/app.css?v=abc")
        self.assertEqual(
            versioned.headers["Cache-Control"], "public, max-age=31536000, immutable"
        )
        plain = await client.get("/static/css/app.css")
        self.assertEqual(plain.headers["Cache-Control"], "no-cache")
