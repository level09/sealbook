import unittest

from stk import assets
from stk.app import create_app
from stk.settings import Config


class AssetConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite+aiosqlite:///:memory:"
    SESSION_TYPE = None
    QUART_RATE_LIMITER_ENABLED = False


class AssetTests(unittest.IsolatedAsyncioTestCase):
    async def test_templates_version_assets_and_map_modules(self):
        client = create_app(AssetConfig).test_client()
        html = await (await client.get("/login")).get_data(as_text=True)
        self.assertRegex(html, r'href="/static/css/app\.css\?v=[0-9a-f]{10}"')
        self.assertRegex(html, r'src="/static/js/config\.js\?v=[0-9a-f]{10}"')
        self.assertNotRegex(html, r'(src|href)="/static/[^"?]*"')

    async def test_import_map_versions_nested_modules(self):
        app = create_app(AssetConfig)
        async with app.test_request_context("/"):
            tag = app.jinja_env.globals["import_map"]()
        self.assertRegex(
            tag,
            r'"/static/js/components/media\.js": "/static/js/components/media\.js\?v=[0-9a-f]{10}"',
        )
        self.assertNotIn("vue.min.js", tag)

    def test_changing_a_file_changes_its_version(self):
        path = assets.STATIC / "js" / "_version_probe.js"
        try:
            path.write_text("a")
            first = assets.version("js/_version_probe.js", True)
            path.write_text("b")
            self.assertNotEqual(first, assets.version("js/_version_probe.js", True))
        finally:
            path.unlink(missing_ok=True)
            assets._seen.pop("js/_version_probe.js", None)

    async def test_cache_headers(self):
        client = create_app(AssetConfig).test_client()
        versioned = await client.get("/static/css/app.css?v=abc")
        self.assertEqual(versioned.headers["Cache-Control"], assets.LONG)
        plain = await client.get("/static/css/app.css")
        self.assertEqual(plain.headers["Cache-Control"], "no-cache")
