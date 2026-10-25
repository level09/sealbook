import unittest
from unittest import mock

import httpx

from sealbook import netguard


class NetguardTest(unittest.IsolatedAsyncioTestCase):
    async def fetch(self, url, addrs, handler):
        with mock.patch.object(netguard, "resolve", side_effect=addrs):
            async with netguard.stream(url, transport=httpx.MockTransport(handler)) as (
                resp,
                final,
            ):
                return resp.status_code, final

    async def test_refuses_non_public_addresses(self):
        for addr in ("127.0.0.1", "10.0.0.5", "169.254.169.254", "::1", "fd00::1"):
            with self.subTest(addr=addr), self.assertRaises(netguard.Blocked):
                await self.fetch(
                    "https://evil.example/", [[addr]], lambda r: httpx.Response(200)
                )

    async def test_refuses_when_any_address_is_private(self):
        with self.assertRaises(netguard.Blocked):
            await self.fetch(
                "https://mixed.example/",
                [["93.184.215.14", "10.0.0.1"]],
                lambda r: httpx.Response(200),
            )

    async def test_connects_to_the_checked_ip_with_hostname_for_tls(self):
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(200)

        await self.fetch("https://example.org:8443/a?b=1", [["93.184.215.14"]], handler)
        (req,) = seen
        self.assertEqual(str(req.url), "https://93.184.215.14:8443/a?b=1")
        self.assertEqual(req.headers["host"], "example.org:8443")
        self.assertEqual(req.extensions["sni_hostname"], "example.org")

    async def test_redirect_to_internal_host_is_rechecked(self):
        def handler(request):
            return httpx.Response(
                302, headers={"location": "http://internal.example/admin"}
            )

        with self.assertRaises(netguard.Blocked):
            await self.fetch(
                "https://example.org/", [["93.184.215.14"], ["10.1.2.3"]], handler
            )

    async def test_cookies_never_cross_hops(self):
        sent = []

        def handler(request):
            sent.append(request.headers.get("cookie"))
            if request.headers["host"] == "victim.example":
                return httpx.Response(
                    302,
                    headers={
                        "set-cookie": "session=secret",
                        "location": "https://attacker.example/",
                    },
                )
            return httpx.Response(200)

        await self.fetch("https://victim.example/", [["93.184.215.14"]] * 2, handler)
        self.assertEqual(sent, [None, None])

    async def test_follows_public_redirect(self):
        def handler(request):
            if request.url.path == "/old":
                return httpx.Response(301, headers={"location": "/new"})
            return httpx.Response(200)

        status, final = await self.fetch(
            "https://example.org/old", [["93.184.215.14"]] * 2, handler
        )
        self.assertEqual((status, final), (200, "https://example.org/new"))


if __name__ == "__main__":
    unittest.main()
