import unittest

from src.p2p_forca.peer_table import PeerTable


class TestPeerTable(unittest.TestCase):

    def setUp(self):
        self.table = PeerTable()

    def test_add_peer(self):
        peer = self.table.add_peer(
            "alice",
            ("127.0.0.1", 8081)
        )

        self.assertEqual(peer["peer_id"], "alice")
        self.assertEqual(peer["status"], "DISCOVERED")

    def test_get_peer(self):
        self.table.add_peer("alice", ("127.0.0.1", 8081))

        peer = self.table.get_peer("alice")

        self.assertIsNotNone(peer)
        self.assertEqual(peer["peer_id"], "alice")

    def test_remove_peer(self):
        self.table.add_peer("alice", ("127.0.0.1", 8081))

        removed = self.table.remove_peer("alice")

        self.assertIsNotNone(removed)
        self.assertIsNone(self.table.get_peer("alice"))

    def test_connected_peers(self):
        self.table.add_peer(
            "alice",
            ("127.0.0.1", 8081),
            connection=object()
        )

        self.table.add_peer(
            "bob",
            ("127.0.0.1", 8082)
        )

        connected = self.table.get_connected_peers()

        self.assertEqual(len(connected), 1)
        self.assertEqual(connected[0]["peer_id"], "alice")

    def test_update_status(self):
        self.table.add_peer("alice", ("127.0.0.1", 8081))

        result = self.table.update_status("alice", "STALE")

        self.assertTrue(result)
        self.assertEqual(
            self.table.get_peer("alice")["status"],
            "STALE"
        )

    def test_count(self):
        self.table.add_peer("alice", ("127.0.0.1", 8081))
        self.table.add_peer("bob", ("127.0.0.1", 8082))

        self.assertEqual(self.table.count(), 2)


if __name__ == "__main__":
    unittest.main()
