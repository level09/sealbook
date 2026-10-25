"""Unhandled errors render as 500 even when the exception has its own .code."""

import unittest

import stk
from sqlalchemy.exc import OperationalError

from sealbook.app import create_app
from sealbook.settings import Config


class ErrorConfig(stk.TestingConfig, Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite+aiosqlite:///:memory:"
    SESSION_TYPE = None
    QUART_RATE_LIMITER_ENABLED = False


class ErrorHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_database_error_is_500_and_logged(self):
        app = create_app(ErrorConfig)

        @app.get("/boom")
        async def boom():
            raise OperationalError("SELECT 1", {}, Exception("no such table"))

        with self.assertLogs("stk.app", level="ERROR"):
            response = await app.test_client().get("/boom")
        self.assertEqual(response.status_code, 500)
