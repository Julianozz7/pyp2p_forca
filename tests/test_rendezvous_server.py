import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "rendezvous"))
from protocol_parser import ProtocolParser
from request_handler import RequestHandler
from peer_db import PeerDatabase


class TestRendezvousHandler(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=PeerDatabase(str(Path(self.temp.name)/"peers.json"))
        self.handler=RequestHandler(self.db)
        self.parser=ProtocolParser()

    def tearDown(self):
        self.temp.cleanup()

    def request(self,payload,ip="127.0.0.1"):
        raw=payload if isinstance(payload,str) else json.dumps(payload)
        return json.loads(self.handler.handle(self.parser.parse(raw),ip))

    def test_register_discover_unregister(self):
        registered=self.request({"type":"REGISTER","namespace":"CIC","name":"alice","port":4100,"ttl":60})
        self.assertEqual(registered["status"],"OK")
        self.assertEqual(registered["ip"],"127.0.0.1")
        self.assertEqual(registered["port"],4100)
        discovered=self.request({"type":"DISCOVER","namespace":"CIC"})
        self.assertEqual(discovered["peers"][0]["name"],"alice")
        self.assertIn("expires_in",discovered["peers"][0])
        removed=self.request({"type":"UNREGISTER","namespace":"CIC","name":"alice","port":4100})
        self.assertEqual(removed,{"status":"OK"})

    def test_ttl_clamping_and_bad_type(self):
        low=self.request({"type":"REGISTER","namespace":"CIC","name":"low","port":4101,"ttl":0})
        high=self.request({"type":"REGISTER","namespace":"CIC","name":"high","port":4102,"ttl":999999})
        bad=self.request({"type":"REGISTER","namespace":"CIC","name":"bad","port":4103,"ttl":"10"})
        self.assertEqual(low["ttl"],1)
        self.assertEqual(high["ttl"],86400)
        self.assertEqual(bad["message"],"bad_ttl")

    def test_discover_requires_registration_and_bad_namespace(self):
        self.assertEqual(self.request({"type":"DISCOVER","namespace":"CIC"}),{"status":"ERROR","message":"peer_not_registered"})
        self.request({"type":"REGISTER","namespace":"CIC","name":"alice","port":4100})
        self.assertEqual(self.request({"type":"DISCOVER","namespace":""})["message"],"bad_namespace")

    def test_unregister_requires_namespace_and_checks_port_type(self):
        self.request({"type":"REGISTER","namespace":"CIC","name":"alice","port":4100})
        self.assertEqual(self.request({"type":"UNREGISTER"})["message"],"namespace_required")
        self.assertEqual(self.request({"type":"UNREGISTER","namespace":"CIC","port":"abc"})["message"],"bad_port (abc)")


if __name__=="__main__":unittest.main()
