import unittest
from unittest.mock import Mock

from p2p_forca.keep_alive import KeepAlive


class TestKeepAlive(unittest.TestCase):

    def setUp(self):
        self.peer_table = Mock()
        self.keep_alive = KeepAlive(
            peer_table=self.peer_table,
            ping_interval=30,
            miss_limit=3
        )

        self.connection = Mock()
        self.connection.peer_id = "peer_123"

    def test_send_ping(self):
        self.keep_alive.send_ping(self.connection)

        self.connection.send_message.assert_called_once()

        message = self.connection.send_message.call_args[0][0]

        self.assertEqual(message["type"], "PING")
        self.assertIn("msg_id", message)
        self.assertIn("timestamp", message)

    def test_receive_pong(self):
        self.keep_alive.pending_pings["peer_123"] = ["ping_123"]

        result = self.keep_alive.receive_pong(
            "peer_123",
            {"msg_id": "ping_123"}
        )

        self.assertTrue(result)
        self.assertNotIn("peer_123", self.keep_alive.pending_pings)

    def test_invalid_pong(self):
        result = self.keep_alive.receive_pong(
            "peer_123",
            {"msg_id": "invalid"}
        )

        self.assertFalse(result)

    def test_peer_becomes_stale(self):
        self.keep_alive.pending_pings["peer_123"] = [
            "ping_1",
            "ping_2",
            "ping_3"
        ]

        self.keep_alive.check_peers()

        self.peer_table.update_status.assert_called_once_with(
            "peer_123",
            "STALE"
        )


if __name__ == "__main__":
    unittest.main()
