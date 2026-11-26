import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import Mock

from p2p_forca.message_router import MessageRouter


class TestMessageRouter(unittest.TestCase):
    def setUp(self):
        self.error = Mock()
        self.router = MessageRouter("alice@CIC", self.error)
        self.connection = SimpleNamespace(peer_id="bob@CIC", established=True, features=["hangman"])
        self.handler = Mock()
        self.router.register_handler("GAME_LIST", self.handler)
        self.message = {
            "type": "GAME_LIST", "msg_id": str(uuid.uuid4()), "ttl": 1,
            "src": "bob@CIC", "dst": "alice@CIC",
        }

    def test_routes_valid_message(self):
        self.assertTrue(self.router.route(self.connection, self.message))
        self.handler.assert_called_once_with(self.connection, self.message)

    def test_unknown_type(self):
        message = dict(self.message, type="UNKNOWN")
        self.assertFalse(self.router.route(self.connection, message))
        self.assertEqual(self.error.call_args.args[1], "unknown_type")

    def test_ttl_other_than_one_is_discarded(self):
        self.assertFalse(self.router.route(self.connection, dict(self.message, ttl=0)))
        self.error.assert_not_called()
        self.handler.assert_not_called()

    def test_wrong_destination(self):
        self.assertFalse(self.router.route(self.connection, dict(self.message, dst="carol@CIC")))
        self.assertEqual(self.error.call_args.args[1], "not_for_me")

    def test_invalid_field_type(self):
        self.assertFalse(self.router.route(self.connection, dict(self.message, ttl=True)))
        self.assertEqual(self.error.call_args.args[1], "bad_request")

    def test_game_messages_require_hangman(self):
        self.connection.features.clear()
        self.assertFalse(self.router.route(self.connection, self.message))
        self.assertEqual(self.error.call_args.args[1], "invalid_state")

    def test_cannot_route_game_message_before_handshake(self):
        self.connection.established = False
        self.assertFalse(self.router.route(self.connection, self.message))
        self.assertEqual(self.error.call_args.args[1], "invalid_state")

    def test_unregister_handler(self):
        self.assertIs(self.router.unregister_handler("GAME_LIST"), self.handler)
        self.assertNotIn("GAME_LIST", self.router.handlers)