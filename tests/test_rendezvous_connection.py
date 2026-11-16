import unittest
from unittest.mock import patch

from p2p_forca.rendezvous_connection import RendezvousConnection


class TestRendezvousConnection(unittest.TestCase):

    def setUp(self):
        self.rendezvous = RendezvousConnection(
            host="localhost",
            port=8080,
            namespace="CIC",
            peer_id="alice"
        )

    @patch.object(RendezvousConnection, "_send_request")
    def test_register(self, mock_send):
        self.rendezvous.register(8081)

        request = mock_send.call_args[0][0]

        self.assertEqual(request["type"], "REGISTER")
        self.assertEqual(request["namespace"], "CIC")
        self.assertEqual(request["name"], "alice")
        self.assertEqual(request["port"], 8081)

    @patch.object(RendezvousConnection, "_send_request")
    def test_discover(self, mock_send):
        self.rendezvous.discover()

        request = mock_send.call_args[0][0]

        self.assertEqual(request["type"], "DISCOVER")
        self.assertEqual(request["namespace"], "CIC")

    @patch.object(RendezvousConnection, "_send_request")
    def test_unregister(self, mock_send):
        self.rendezvous.unregister(8081)

        request = mock_send.call_args[0][0]

        self.assertEqual(request["type"], "UNREGISTER")
        self.assertEqual(request["namespace"], "CIC")
        self.assertEqual(request["name"], "alice")
        self.assertEqual(request["port"], 8081)


if __name__ == "__main__":
    unittest.main()
