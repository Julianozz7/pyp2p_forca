import hashlib
import json
import os
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

from p2p_forca.p2p_client import P2PClient
from p2p_forca.rendezvous_connection import RendezvousConnection

PROJECT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.create_server(("127.0.0.1", 0)) as sock:
        return sock.getsockname()[1]


class WirePeer:
    """Interlocutor TCP de teste, com mensagens JSON escritas a partir da especificação."""

    def __init__(self, sock, peer_id, remote):
        self.sock = sock
        self.sock.settimeout(5)
        self.stream = self.sock.makefile("rb")
        self.peer_id = peer_id
        self.remote = remote

    def emit(self, kind, **fields):
        msg = {"type": kind, "msg_id": str(uuid.uuid4()), "ttl": 1, **fields}
        if kind not in ("HELLO", "HELLO_OK", "PING", "PONG"):
            msg = {"src": self.peer_id, "dst": self.remote, **msg}
        self.sock.sendall((json.dumps(msg) + "\n").encode("utf-8"))
        return msg["msg_id"]

    def read(self, expected):
        while line := self.stream.readline():
            msg = json.loads(line)
            if msg["type"] == "PING" and expected != "PING":
                self.emit("PONG", msg_id=msg["msg_id"], timestamp=msg["timestamp"])
                continue
            if msg["type"] != expected:
                raise AssertionError(f"Expected {expected}, received {msg}")
            return msg
        raise AssertionError(f"EOF waiting for {expected}")

    def hello(self, features=None):
        request = self.emit("HELLO", peer_id=self.peer_id, version="1.0",
                            features=["hangman"] if features is None else features)
        reply = self.read("HELLO_OK")
        assert reply["msg_id"] == request and reply["peer_id"] == self.remote
        assert "hangman" in reply["features"] and "src" not in reply

    def close(self):
        self.stream.close()
        self.sock.close()


class TestPeerProtocol(unittest.TestCase):
    def setUp(self):
        self.namespace = "teste-" + uuid.uuid4().hex[:8]
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.rdv_port = free_port()
        server = subprocess.Popen(
            [sys.executable, "-B", str(PROJECT / "src/rendezvous/main.py"),
             "--host", "127.0.0.1", "--port", str(self.rdv_port)],
            cwd=directory.name, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.addCleanup(self.stop_process, server)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.rdv_port), timeout=0.2) as connection:
                    connection.sendall(b'{"type":"DISCOVER"}\n')
                    connection.recv(1024)
                    return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("Servidor Rendezvous local indisponível.")

    @staticmethod
    def stop_process(process):
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)

    def client(self, name, **changes):
        overrides = {
            "name": name, "namespace": self.namespace, "listen_port": free_port(),
            "rdv_host": "127.0.0.1", "rdv_port": self.rdv_port, "discover_interval": 4,
            "ping_interval": 1, "base_backoff": 0.4, "max_backoff": 2,
            **changes,
        }
        client = P2PClient(PROJECT / "config.json", overrides)
        self.addCleanup(client.stop)
        self.assertTrue(client.start())
        return client

    def wire(self, host, name="carol", *, features=None):
        wire = WirePeer(socket.create_connection(("127.0.0.1", host.listen_port), timeout=2),
                        f"{name}@{self.namespace}", host.peer_id)
        self.addCleanup(wire.close)
        wire.hello(features)
        return wire

    def wait_for(self, condition, description, timeout=6):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.03)
        self.assertTrue(condition(), description)

    @staticmethod
    def request(client, host, kind, **fields):
        with client.lock:
            connection = client._connection_to(host.peer_id)
            pending = client._begin_request(connection, kind, **fields)
        return client._wait_reply(pending)

    def test_mesh_refresh_lobby_victory_and_ordered_shutdown(self):
        alice = self.client("alice", register_ttl=4, ping_interval=0.3)
        bob = self.client("bob", ping_interval=0.3)
        carol = self.client("carol", ping_interval=0.3)
        dave = self.client("dave", ping_interval=0.3)
        for peer in (alice, bob, carol, dave):
            peer.discover_peers(self.namespace, force=True)
        self.wait_for(lambda: all(len(peer.connection_status()) == 3 for peer in (alice, bob, carol, dave)),
                      "cada cliente deve manter três conexões únicas")
        self.wait_for(lambda: all(row[3] is not None for row in bob.connection_status()), "RTT")
        scheduled_refresh = alice.refresh_at
        self.wait_for(lambda: alice.refresh_at > scheduled_refresh, "refresh de REGISTER")

        game_id = alice.create_game("banana", 2)
        second_game = dave.create_game("--random")
        self.assertEqual({game["game_id"] for game in bob.list_games()}, {game_id, second_game})
        with self.assertRaises(ValueError):
            alice.start_game()
        bob.join_game(alice.peer_id)
        carol.join_game(alice.peer_id)
        error = self.request(dave, alice, "JOIN", game_id=game_id)
        self.assertEqual(error["reason"], "game_full")
        error = self.request(bob, alice, "JOIN", game_id=game_id)
        self.assertEqual(error["reason"], "already_joined")

        alice.start_game()
        self.wait_for(lambda: bob.player.seq == carol.player.seq == 1, "estado inicial")
        wrong_turn = self.request(carol, alice, "GUESS", game_id=game_id, letter="x")
        self.assertEqual(wrong_turn["reason"], "not_your_turn")
        self.assertEqual(alice.host.seq, 1)
        error = self.request(dave, alice, "JOIN", game_id=game_id)
        self.assertEqual(error["reason"], "game_running")

        bob.send_guess("letter", "a")
        self.wait_for(lambda: carol.player.seq == 2, "turno de Carol")
        self.assertEqual(carol.player.masked, "_a_a_a")
        carol.send_guess("letter", "n")
        self.wait_for(lambda: bob.player.seq == 3, "turno de Bob")
        repeated = self.request(bob, alice, "GUESS", game_id=game_id, letter="a")
        self.assertEqual(repeated["reason"], "letter_already_used")
        self.assertEqual(alice.host.seq, 3)
        bob.send_guess("word", "laranja")
        self.wait_for(lambda: carol.player.seq == 4, "palpite errado de palavra")
        self.assertEqual(carol.player.lives, 5)
        with self.assertLogs("Player", "INFO") as captured:
            carol.send_guess("word", "banana")
            self.wait_for(lambda: bob.player.status == carol.player.status == "IDLE", "fim da partida")
        self.assertTrue(any("GAME_OVER WON" in entry and "hash OK" in entry for entry in captured.output))
        self.assertEqual(alice.host.scores, {bob.peer_id: 3, carol.peer_id: 3})
        error = self.request(bob, alice, "JOIN", game_id=game_id)
        self.assertEqual(error["reason"], "game_finished")
        error = self.request(bob, alice, "JOIN", game_id=alice.peer_id + "/ffffffff")
        self.assertEqual(error["reason"], "unknown_game")

        dave.cancel_game()
        bob.stop()
        self.assertNotIn(bob.peer_id, alice.discover_peers(self.namespace))

    def test_wire_player_timeout_loss_rejoin_and_shutdown(self):
        host = self.client("alice", lives=1, turn_timeout=2, discover_interval=35)
        bob = self.client("bob", discover_interval=35)
        self.wait_for(lambda: bob.connection_status(), "ligação do primeiro jogador")
        carol = self.wire(host)
        game_id = host.create_game("casa")
        bob.join_game(host.peer_id)
        join_id = carol.emit("JOIN", game_id=game_id)
        self.assertEqual(carol.read("JOIN_OK")["msg_id"], join_id)
        host.start_game()
        start = carol.read("GAME_START")
        self.assertEqual(start["word_hash"], hashlib.sha256(b"casa").hexdigest())
        state = carol.read("STATE")
        self.assertEqual((state["seq"], state["turn"]), (1, bob.peer_id))
        state = carol.read("STATE")
        self.assertEqual(state["last_guess"], {"player": bob.peer_id, "timeout": True})
        carol.close()
        self.wait_for(lambda: len(host.host.players) == 1, "jogador removido")
        self.assertEqual(host.host.get_current_player(), bob.peer_id)

        returning = self.wire(host)
        returning.emit("JOIN", game_id=game_id)
        self.assertTrue(returning.read("JOIN_OK")["rejoin"])
        state = returning.read("STATE")
        self.assertEqual(state["players"], [bob.peer_id, returning.peer_id])

        bob.send_guess("letter", "a")
        self.assertEqual(returning.read("STATE")["turn"], returning.peer_id)
        returning.emit("GUESS", game_id=game_id, letter="z")
        self.assertFalse(returning.read("GUESS_RESULT")["correct"])
        self.assertEqual(returning.read("STATE")["lives"], 0)
        game_over = returning.read("GAME_OVER")
        self.assertEqual((game_over["result"], game_over["winner"], game_over["word"]), ("LOST", None, "casa"))

        # A partida anterior permanece anunciada, e outro peer pode hospedar.
        game_id = bob.create_game("limao")
        player = self.wire(bob, name="dave")
        player.emit("JOIN", game_id=game_id)
        player.read("JOIN_OK")
        bob.start_game()
        player.read("GAME_START")
        player.read("STATE")
        closer = threading.Thread(target=bob.stop)
        closer.start()
        try:
            self.assertEqual(player.read("GAME_OVER")["reason"], "host_shutdown")
            bye = player.read("BYE")
            self.assertEqual(bye["reason"], "shutdown")
            player.emit("BYE_OK", msg_id=bye["msg_id"])
        finally:
            closer.join(5)
        self.assertFalse(closer.is_alive())
        self.assertNotIn(bob.peer_id, host.discover_peers(self.namespace))

    def test_wire_host_and_sequence_and_invalid_messages(self):
        rdv = RendezvousConnection("127.0.0.1", self.rdv_port, self.namespace, "alice")
        listener = socket.create_server(("127.0.0.1", 0))
        self.addCleanup(listener.close)
        listener.settimeout(5)
        port = listener.getsockname()[1]
        self.assertEqual(rdv.register(port)["status"], "OK")
        self.addCleanup(rdv.unregister, port)

        bob = self.client("bob", ping_interval=2)
        sock, _ = listener.accept()
        host = WirePeer(sock, f"alice@{self.namespace}", bob.peer_id)
        self.addCleanup(host.close)
        hello = host.read("HELLO")
        self.assertEqual(hello["features"], ["hangman"])
        host.emit("HELLO_OK", msg_id=hello["msg_id"], peer_id=host.peer_id, version="1.0",
                  features=["hangman"])
        self.wait_for(lambda: bob.connection_status(), "handshake de saída")
        wrong = host.emit("UNRECOGNIZED")
        error = host.read("ERROR")
        self.assertEqual((error["code"], error["ref_msg_id"]), ("unknown_type", wrong))

        errors = queue.Queue()
        def join():
            try:
                bob.join_game(host.peer_id)
            except Exception as error:
                errors.put(error)

        task = threading.Thread(target=join, daemon=True)
        task.start()
        game_id = host.peer_id + "/01234567"
        listing = host.read("GAME_LIST")
        host.emit("GAME_LIST_OK", msg_id=listing["msg_id"], games=[{
            "host": host.peer_id, "game_id": game_id, "status": "OPEN", "players": [],
            "max_players": 4, "lives": 6, "word_length": 4,
        }])
        join = host.read("JOIN")
        host.emit("JOIN_OK", msg_id=join["msg_id"], game_id=game_id, rejoin=False, players=[bob.peer_id])
        task.join(5)
        self.assertFalse(task.is_alive())
        self.assertTrue(errors.empty())

        host.emit("GAME_START", game_id=game_id, players=[bob.peer_id], lives=6, word_length=4,
                  word_hash=hashlib.sha256(b"casa").hexdigest(), turn_timeout=30)
        state = {"game_id": game_id, "wrong_letters": [], "lives": 6, "turn": bob.peer_id,
                 "players": [bob.peer_id], "last_guess": None}
        host.emit("STATE", **state, seq=1, masked="____")
        host.emit("STATE", **state, seq=2, masked="_a_a")
        host.emit("STATE", **state, seq=1, masked="____")
        self.wait_for(lambda: bob.player.seq == 2, "STATE seq=2")
        self.assertEqual(bob.player.masked, "_a_a")
        bob.send_guess("word", "casa")
        guess = host.read("GUESS")
        self.assertEqual(guess["word"], "casa")
        host.emit("GUESS_RESULT", msg_id=guess["msg_id"], game_id=game_id, accepted=True, correct=True)

        with self.assertLogs("Player", "WARNING") as captured:
            host.emit("GAME_OVER", game_id=game_id, result="WON", word="rato",
                      winner=bob.peer_id, scores={bob.peer_id: 4})
            self.wait_for(lambda: bob.player.status == "IDLE", "fim mesmo quando o hash diverge")
        self.assertIn("host cheated: hash mismatch", captured.output[0])

        host.sock.sendall(b"invalid json\n")
        self.assertEqual(host.read("ERROR")["code"], "bad_json")
        self.assertEqual(host.stream.readline(), b"")

    def test_unexpected_host_loss_returns_player_to_idle(self):
        host = self.client("alice")
        player = self.client("bob")
        self.wait_for(lambda: len(player.connection_status()) == 1, "malha")
        host.create_game("banana")
        player.join_game(host.peer_id)
        host.start_game()
        self.wait_for(lambda: player.player.status == "PLAYING", "início")
        with player.lock:
            player._connection_to(host.peer_id).close()
        self.wait_for(lambda: player.player.status == "IDLE", "queda do host limpa partida local")
        self.assertIsNotNone(player.interrupted_game)

    def test_automatic_rejoin_retains_commitment_and_leave_ends_empty_game(self):
        host = self.client("alice")
        bob = self.client("bob")
        carol = self.client("carol")
        for client in (bob, carol):
            self.wait_for(lambda: any(row[0] == host.peer_id for row in client.connection_status()),
                          "conexão com o host")
        game_id = host.create_game("banana")
        bob.join_game(host.peer_id)
        carol.join_game(host.peer_id)
        host.start_game()
        self.wait_for(lambda: carol.player.seq == 1, "primeiro estado")
        word_hash = carol.player.word_hash
        with carol.lock:
            carol._connection_to(host.peer_id).close()
        self.wait_for(lambda: carol.player.status == "IDLE", "partida local interrompida")
        self.wait_for(lambda: carol.player.status == "PLAYING" and carol.player.seq >= 3,
                      "reentrada automática")
        self.assertEqual(carol.player.game_id, game_id)
        self.assertEqual(carol.player.word_hash, word_hash)
        carol.leave_game()
        self.assertEqual(carol.player.status, "IDLE")
        bob.leave_game()
        self.wait_for(lambda: host.host.status == "IDLE", "host sem jogadores volta a IDLE")

    def test_duplicate_prefers_lower_id_and_closes_connection_after_missing_pongs(self):
        rdv = RendezvousConnection("127.0.0.1", self.rdv_port, self.namespace, "alice")
        listener = socket.create_server(("127.0.0.1", 0))
        self.addCleanup(listener.close)
        listener.settimeout(5)
        port = listener.getsockname()[1]
        self.assertEqual(rdv.register(port)["status"], "OK")
        self.addCleanup(rdv.unregister, port)
        bob = self.client("bob", ping_interval=0.25, base_backoff=2)

        sock, _ = listener.accept()
        outbound = WirePeer(sock, f"alice@{self.namespace}", bob.peer_id)
        self.addCleanup(outbound.close)
        hello = outbound.read("HELLO")
        outbound.emit("HELLO_OK", msg_id=hello["msg_id"], peer_id=outbound.peer_id,
                      version="1.0", features=["hangman"])
        self.wait_for(lambda: any(row[1] == "outbound" for row in bob.connection_status()),
                      "a conexão inicial é de saída")
        preferred = self.wire(bob, name="alice")
        bye = outbound.read("BYE")
        self.assertEqual(bye["reason"], "duplicate")
        outbound.emit("BYE_OK", msg_id=bye["msg_id"])
        self.assertEqual(outbound.stream.readline(), b"")
        self.assertEqual([row[1] for row in bob.connection_status()], ["inbound"])

        with self.assertLogs("KeepAlive", "INFO") as captured:
            for _ in range(3):
                preferred.read("PING")
            self.assertEqual(preferred.stream.readline(), b"")
        self.assertIn("missed 3 PINGs", " ".join(captured.output))
        self.wait_for(lambda: bob.peer_table.get_peer(preferred.peer_id)["status"] == "STALE",
                      "três perdas levam a STALE")

    def test_cli_entrypoint_and_debug_file(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "alice.log"
            result = subprocess.run(
                [sys.executable, "-B", "-X", "utf8", "-m", "p2p_forca.main",
                 "--rdv-host", "127.0.0.1", "--rdv-port", str(self.rdv_port),
                 "--namespace", self.namespace, "--listen-port", str(free_port()),
                 "--log-file", str(log_path)],
                cwd=PROJECT, env=os.environ | {"PYTHONPATH": str(PROJECT / "src")},
                input="/peers *\n/reconnect\n/conn\n/rtt\n/create --random 2\n/status\n/cancel\n/log DEBUG\n/quit\n",
                capture_output=True, encoding="utf-8", timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("status: OPEN", result.stdout)
            self.assertIn("Log no terminal: DEBUG", result.stdout)
            raw_log = log_path.read_text(encoding="utf-8")
            self.assertIn('[DEBUG] [RDV] TX {"type":"REGISTER"', raw_log)
            self.assertIn('[DEBUG] [RDV] TX {"type":"UNREGISTER"', raw_log)