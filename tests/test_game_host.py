import hashlib
import unittest

from p2p_forca.game_host import GameHost


class Net:
    """Captura o que o host enviaria pela rede."""

    def __init__(self):
        self.sent = []

    def __call__(self, dst, message):
        self.sent.append((dst, message))
        return True

    def of(self, type_, dst=None):
        return [
            m for d, m in self.sent
            if m["type"] == type_ and (dst is None or d == dst)
        ]

    def clear(self):
        self.sent.clear()


def msg(type_, src, n=[0], **fields):
    n[0] += 1
    return {"type": type_, "msg_id": f"m{n[0]}", "src": src,
            "dst": "alice@CIC", "ttl": 1, **fields}


class TestGameHost(unittest.TestCase):

    def setUp(self):
        self.net = Net()
        self.host = GameHost(
            "alice@CIC", lives=3, max_players=2, turn_timeout=30,
            send=self.net, notify=lambda text: None
        )
        self.gid = self.host.create_game("casa")

    def tearDown(self):
        self.host.cancel_game()

    def join(self, peer):
        self.host.handle_join(None, msg("JOIN", peer, game_id=self.gid))

    def guess(self, peer, **fields):
        self.net.clear()
        self.host.handle_guess(
            None, msg("GUESS", peer, game_id=self.gid, **fields)
        )

    def start_two(self):
        self.join("bob@CIC")
        self.join("carol@CIC")
        self.host.start_game()
        self.net.clear()

    # ---- criação / lobby -------------------------------------------------
    def test_game_id_format(self):
        prefix, suffix = self.gid.split("/")
        self.assertEqual(prefix, "alice@CIC")
        self.assertEqual(len(suffix), 8)
        int(suffix, 16)

    def test_game_list_ok_repeats_msg_id(self):
        request = msg("GAME_LIST", "bob@CIC")
        self.host.handle_game_list(None, request)

        reply = self.net.of("GAME_LIST_OK", "bob@CIC")[0]
        game = reply["games"][0]

        self.assertEqual(reply["msg_id"], request["msg_id"])
        self.assertEqual(game["status"], "OPEN")
        self.assertEqual(game["word_length"], 4)
        self.assertEqual(game["max_players"], 2)

    def test_game_list_without_game(self):
        host = GameHost("x@CIC", send=self.net, notify=lambda t: None)
        host.handle_game_list(None, msg("GAME_LIST", "bob@CIC"))

        self.assertEqual(self.net.of("GAME_LIST_OK")[-1]["games"], [])

    def test_join_errors(self):
        self.join("bob@CIC")
        self.join("bob@CIC")
        self.assertEqual(self.net.of("JOIN_ERR")[-1]["reason"],
                         "already_joined")

        self.join("carol@CIC")
        self.join("dave@CIC")
        self.assertEqual(self.net.of("JOIN_ERR")[-1]["reason"], "game_full")

        self.host.handle_join(None, msg("JOIN", "bob@CIC", game_id="x/0"))
        self.assertEqual(self.net.of("JOIN_ERR")[-1]["reason"],
                         "unknown_game")

    def test_join_running_new_player_refused(self):
        self.join("bob@CIC")
        self.host.start_game()
        self.join("zed@CIC")

        self.assertEqual(self.net.of("JOIN_ERR")[-1]["reason"],
                         "game_running")

    def test_start_requires_player(self):
        ok, _ = self.host.start_game()
        self.assertFalse(ok)

    # ---- partida -----------------------------------------------------------
    def test_game_start_and_first_state(self):
        self.join("bob@CIC")
        self.join("carol@CIC")
        self.net.clear()
        self.host.start_game()

        start = self.net.of("GAME_START", "bob@CIC")[0]
        self.assertEqual(start["players"], ["bob@CIC", "carol@CIC"])
        self.assertEqual(
            start["word_hash"], hashlib.sha256(b"casa").hexdigest()
        )

        state = self.net.of("STATE", "carol@CIC")[0]
        self.assertEqual(state["seq"], 1)
        self.assertEqual(state["masked"], "____")
        self.assertEqual(state["turn"], "bob@CIC")
        self.assertIsNone(state["last_guess"])

    def test_not_your_turn_has_no_state(self):
        self.start_two()
        self.guess("carol@CIC", letter="a")

        result = self.net.of("GUESS_RESULT", "carol@CIC")[0]
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "not_your_turn")
        self.assertEqual(self.net.of("STATE"), [])

    def test_correct_guess_passes_turn_and_increments_seq(self):
        self.start_two()
        self.guess("bob@CIC", letter="a")

        result = self.net.of("GUESS_RESULT", "bob@CIC")[0]
        self.assertTrue(result["accepted"])
        self.assertTrue(result["correct"])

        state = self.net.of("STATE", "carol@CIC")[0]
        self.assertEqual(state["seq"], 2)
        self.assertEqual(state["masked"], "_a_a")
        self.assertEqual(state["turn"], "carol@CIC")
        self.assertEqual(state["last_guess"],
                         {"player": "bob@CIC", "letter": "a",
                          "correct": True})

    def test_rejected_guess_keeps_turn(self):
        self.start_two()
        self.guess("bob@CIC", letter="a")
        self.guess("carol@CIC", letter="a")

        self.assertEqual(
            self.net.of("GUESS_RESULT")[0]["reason"], "letter_already_used"
        )
        self.assertEqual(self.host.get_current_player(), "carol@CIC")

        self.guess("carol@CIC", letter="1")
        self.assertEqual(
            self.net.of("GUESS_RESULT")[0]["reason"], "invalid_letter"
        )

    def test_bad_request_when_letter_and_word_together(self):
        self.start_two()
        self.guess("bob@CIC", letter="a", word="casa")

        self.assertEqual(self.net.of("ERROR")[0]["code"], "bad_request")

    def test_guess_without_game_is_invalid_state(self):
        host = GameHost("x@CIC", send=self.net, notify=lambda t: None)
        host.handle_guess(None, msg("GUESS", "bob@CIC",
                                    game_id="x", letter="a"))

        self.assertEqual(self.net.of("ERROR")[-1]["code"], "invalid_state")

    def test_win_by_word_gives_hidden_letters_to_winner(self):
        self.start_two()
        self.guess("bob@CIC", letter="a")          # 2 pontos
        self.guess("carol@CIC", word="casa")       # c, s ocultas = 2

        over = self.net.of("GAME_OVER", "bob@CIC")[0]
        self.assertEqual(over["result"], "WON")
        self.assertEqual(over["winner"], "carol@CIC")
        self.assertEqual(over["word"], "casa")
        self.assertEqual(over["scores"], {"bob@CIC": 2, "carol@CIC": 2})
        self.assertEqual(self.host.status, "FINISHED")

    def test_winner_is_who_completes_the_word(self):
        self.join("bob@CIC")
        self.host.start_game()

        for letter in "cas":
            self.guess("bob@CIC", letter=letter)

        over = self.net.of("GAME_OVER")[0]
        self.assertEqual(over["winner"], "bob@CIC")

    def test_loss(self):
        self.join("bob@CIC")
        self.host.start_game()

        for letter in "xyz":
            self.guess("bob@CIC", letter=letter)

        over = self.net.of("GAME_OVER")[0]
        self.assertEqual(over["result"], "LOST")
        self.assertIsNone(over["winner"])
        self.assertEqual(over["word"], "casa")

    def test_wrong_word_costs_life_without_revealing(self):
        self.join("bob@CIC")
        self.host.start_game()
        self.guess("bob@CIC", word="rede")

        state = self.net.of("STATE")[0]
        self.assertEqual(state["lives"], 2)
        self.assertEqual(state["masked"], "____")

    # ---- falhas --------------------------------------------------------------
    def test_turn_timeout_skips_without_losing_life(self):
        self.start_two()
        token = self.host.turn_token
        self.host._on_turn_timeout(token)

        state = self.net.of("STATE", "carol@CIC")[0]
        self.assertEqual(state["turn"], "carol@CIC")
        self.assertEqual(state["lives"], 3)
        self.assertEqual(state["last_guess"],
                         {"player": "bob@CIC", "timeout": True})

    def test_stale_timer_token_is_ignored(self):
        self.start_two()
        self.host._on_turn_timeout(self.host.turn_token - 1)

        self.assertEqual(self.net.of("STATE"), [])

    def test_player_lost_on_their_turn_passes_turn(self):
        self.start_two()
        self.host.player_lost("bob@CIC")

        state = self.net.of("STATE", "carol@CIC")[0]
        self.assertEqual(state["players"], ["carol@CIC"])
        self.assertEqual(state["turn"], "carol@CIC")

    def test_player_lost_not_on_turn_keeps_turn(self):
        self.start_two()
        self.host.player_lost("carol@CIC")

        self.assertEqual(self.host.get_current_player(), "bob@CIC")

    def test_last_player_lost_aborts(self):
        self.join("bob@CIC")
        self.host.start_game()
        self.host.player_lost("bob@CIC")

        self.assertEqual(self.host.status, "FINISHED")

    def test_rejoin_returns_to_end_of_rotation(self):
        self.start_two()
        self.host.player_lost("bob@CIC")
        self.net.clear()
        self.join("bob@CIC")

        ok = self.net.of("JOIN_OK", "bob@CIC")[0]
        self.assertTrue(ok["rejoin"])
        self.assertEqual(ok["players"], ["carol@CIC", "bob@CIC"])
        self.assertEqual(len(self.net.of("STATE", "bob@CIC")), 1)

    def test_leave_in_game(self):
        self.start_two()
        self.net.clear()
        self.host.handle_leave(
            None, msg("LEAVE", "carol@CIC", game_id=self.gid)
        )

        self.assertEqual(len(self.net.of("LEAVE_OK", "carol@CIC")), 1)
        self.assertEqual(self.host.players, ["bob@CIC"])

    def test_cancel_sends_aborted(self):
        self.start_two()
        self.host.cancel_game()

        over = self.net.of("GAME_OVER", "bob@CIC")[0]
        self.assertEqual(over["result"], "ABORTED")
        self.assertEqual(over["reason"], "host_shutdown")


if __name__ == "__main__":
    unittest.main()