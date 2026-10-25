import unittest

from stk.cli.reports import build_routes_report
from stk.cli.smoke import discover_smoke_paths, machine_paths

from sealbook.app import create_app


class SmokePageDiscoveryTests(unittest.TestCase):
    def test_smoke_skips_the_mcp_authorization_endpoint(self):
        app = create_app()
        skip = machine_paths(app) | set(app.config["STK_SMOKE_SKIP"])
        paths = discover_smoke_paths(build_routes_report(app), skip)

        self.assertIn("/desk", paths)
        # GET starts an OAuth authorization flow for an MCP client, not a page.
        self.assertNotIn("/oauth/authorize", paths)
        self.assertNotIn("/ws", paths)


if __name__ == "__main__":
    unittest.main()
