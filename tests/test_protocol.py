import unittest
from unittest.mock import Mock

from p2p_forca.message_router import MessageRouter
from p2p_forca.peer_connection import utc_timestamp


class TestEnvelope(unittest.TestCase):

    def setUp(self):
        self.router = MessageRouter(local_peer_id="alice@CIC")
        self.handler = Mock()
        self.router.register_handler("GAME_LIST", self.handler)

        self.connection = Mock()
        self.connection.peer_id = "bob@CIC"

    def base(self, **extra):
        message = {
            "type": "GAME_LIST", "msg_id": "1", "ttl": 1,
            "src": "bob@CIC", "dst": "alice@CIC",
        }
        message.update(extra)
        return message

    def error_code(self):
        return self.connection.send_message.call_args[0][0]["code"]

    def test_valid_message_is_routed(self):
        self.assertTrue(self.router.route(self.connection, self.base()))
        self.handler.assert_called_once()

    def test_ttl_other_than_one_is_discarded(self):
        self.assertFalse(
            self.router.route(self.connection, self.base(ttl=2))
        )
        self.handler.assert_not_called()

    def test_not_for_me(self):
        self.router.route(self.connection, self.base(dst="carol@CIC"))

        self.assertEqual(self.error_code(), "not_for_me")
        self.handler.assert_not_called()

    def test_missing_src_dst_is_bad_request(self):
        message = self.base()
        del message["src"]
        self.router.route(self.connection, message)

        self.assertEqual(self.error_code(), "bad_request")

    def test_unknown_type_replies_error(self):
        self.router.route(self.connection, self.base(type="WHATEVER"))

        reply = self.connection.send_message.call_args[0][0]
        self.assertEqual(reply["code"], "unknown_type")
        self.assertEqual(reply["ref_msg_id"], "1")

    def test_error_is_never_answered(self):
        self.router.route(
            self.connection, self.base(type="ERROR", dst="carol@CIC")
        )

        self.connection.send_message.assert_not_called()

    def test_connection_messages_need_no_src_dst(self):
        self.router.register_handler("PING", self.handler)

        self.assertTrue(self.router.route(
            self.connection, {"type": "PING", "msg_id": "p1", "ttl": 1}
        ))


class TestTimestamp(unittest.TestCase):

    def test_utc_iso_format(self):
        stamp = utc_timestamp()

        self.assertTrue(stamp.endswith("Z"))
        self.assertEqual(stamp[10], "T")


if __name__ == "__main__":
    unittest.main()