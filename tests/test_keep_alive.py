import unittest
from unittest.mock import Mock

from p2p_forca.keep_alive import KeepAlive


class TestKeepAlive(unittest.TestCase):
    def setUp(self):
        self.keep_alive = KeepAlive(ping_interval=30, miss_limit=3)
        self.connection = Mock(peer_id="bob@CIC", bye_id=None)
        self.connection.closed.is_set.return_value = False

    def ping(self):
        self.keep_alive.send_ping(self.connection)
        return self.connection.send_message.call_args.args[0]

    def test_send_ping(self):
        message = self.ping()
        self.assertEqual(message["type"], "PING")
        self.assertIn("msg_id", message)
        self.assertTrue(message["timestamp"].endswith("Z"))

    def test_pong_must_match_id_and_timestamp(self):
        ping = self.ping()
        wrong = dict(ping, timestamp="incorreto")
        self.assertFalse(self.keep_alive.receive_pong(self.connection, wrong))
        self.assertTrue(self.keep_alive.receive_pong(self.connection, ping))
        self.assertFalse(self.keep_alive.receive_pong(self.connection, ping))
        self.assertIsNotNone(self.keep_alive.average_rtt(self.connection))

    def test_valid_pong_breaks_consecutive_misses(self):
        self.ping()
        newest = self.ping()
        self.assertTrue(self.keep_alive.receive_pong(self.connection, newest))
        self.keep_alive.check_peers([self.connection])
        self.connection.close.assert_not_called()

    def test_three_missed_pings_close_socket_on_the_next_check(self):
        for _ in range(3):
            self.keep_alive.check_peers([self.connection])
        self.connection.close.assert_not_called()
        self.keep_alive.check_peers([self.connection])
        self.connection.close.assert_called_once()