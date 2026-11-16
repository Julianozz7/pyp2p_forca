import unittest
from unittest.mock import Mock

from p2p_forca.message_router import MessageRouter


class TestMessageRouter(unittest.TestCase):

    def setUp(self):
        self.router = MessageRouter()
        self.connection = Mock()
        self.handler = Mock()

    def test_register_handler(self):
        self.router.register_handler("PING", self.handler)

        self.assertIn("PING", self.router.handlers)

    def test_route_message(self):
        self.router.register_handler("PING", self.handler)

        message = {
            "type": "PING",
            "msg_id": "123"
        }

        result = self.router.route(self.connection, message)

        self.assertTrue(result)
        self.handler.assert_called_once_with(
            self.connection,
            message
        )

    def test_unknown_message(self):
        message = {
            "type": "UNKNOWN",
            "msg_id": "456"
        }

        result = self.router.route(self.connection, message)

        self.assertFalse(result)

    def test_duplicate_message(self):
        self.router.register_handler("PING", self.handler)

        message = {
            "type": "PING",
            "msg_id": "789"
        }

        self.router.route(self.connection, message)
        result = self.router.route(self.connection, message)

        self.assertFalse(result)
        self.handler.assert_called_once()

    def test_invalid_message(self):
        result = self.router.route(self.connection, "mensagem inválida")

        self.assertFalse(result)

    def test_unregister_handler(self):
        self.router.register_handler("PING", self.handler)

        removed = self.router.unregister_handler("PING")

        self.assertIsNotNone(removed)
        self.assertNotIn("PING", self.router.handlers)


if __name__ == "__main__":
    unittest.main()
