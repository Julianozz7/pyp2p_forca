import unittest
from p2p_forca.hangman import Hangman
from p2p_forca.game_host import GameHost
from p2p_forca.game_player import GamePlayer


class TestHangman(unittest.TestCase):
    def test_normalization_and_mask(self):
        game=Hangman("Árvore",6)
        self.assertEqual(game.word,"arvore")
        self.assertEqual(game.get_hidden_word(),"______")
    def test_letter_and_repeat(self):
        game=Hangman("casa",6)
        self.assertTrue(game.guess_letter("a")["correct"])
        self.assertEqual(game.get_hidden_word(),"_a_a")
        self.assertEqual(game.guess_letter("a")["reason"],"letter_already_used")
    def test_wrong_letter_loses_life(self):
        game=Hangman("casa",2)
        self.assertFalse(game.guess_letter("z")["correct"])
        self.assertEqual(game.lives,1)
    def test_word_guess(self):
        game=Hangman("casa",2)
        self.assertTrue(game.guess_word("casa")["correct"])
        self.assertTrue(game.is_won())
    def test_validation(self):
        for word in ("ab","palavra muito longa", "c4sa"):
            with self.assertRaises(ValueError):Hangman(word)
    def test_accented_letter_is_not_protocol_letter(self):
        game=Hangman("arvore")
        self.assertEqual(game.guess_letter("á")["reason"],"invalid_letter")


class TestGameHost(unittest.TestCase):
    def setUp(self):
        self.host=GameHost("alice@CIC",6,4,30)
        self.host.create_game("casa")
        self.host.add_player("bob@CIC")
        self.host.add_player("carol@CIC")
        self.host.start_game()
    def test_turn_and_state(self):
        result=self.host.guess_letter("bob@CIC","a")
        self.assertTrue(result["accepted"])
        self.assertEqual(result["state"]["masked"],"_a_a")
        self.assertEqual(result["state"]["turn"],"carol@CIC")
    def test_wrong_turn_rejected_without_state(self):
        result=self.host.guess_letter("carol@CIC","a")
        self.assertEqual(result["reason"],"not_your_turn")
        self.assertEqual(self.host.seq,0)
    def test_word_guess_scores_remaining_letters(self):
        self.host.guess_letter("bob@CIC","a")
        result=self.host.guess_word("carol@CIC","casa")
        self.assertEqual(result["game_over"]["winner"],"carol@CIC")
        self.assertEqual(self.host.scores["carol@CIC"],2)
    def test_timeout_advances_turn_without_losing_life(self):
        self.host.last_activity -= 31
        state=self.host.check_timeout()
        self.assertTrue(state["last_guess"]["timeout"])
        self.assertEqual(state["turn"],"carol@CIC")
        self.assertEqual(state["lives"],6)
    def test_rejoin_snapshot_does_not_increment_sequence(self):
        seq=self.host.get_state()["seq"]
        snapshot=self.host.snapshot_state()
        self.assertEqual(snapshot["seq"],seq)
        self.assertEqual(self.host.seq,seq)
    def test_player_discards_old_state(self):
        p=GamePlayer("bob@CIC");p.join_game("alice@CIC",self.host.game_id)
        p.start_game({"game_id":self.host.game_id,"players":["bob@CIC"],"lives":6,"word_hash":self.host.word_hash})
        self.assertTrue(p.apply_state({"game_id":self.host.game_id,"seq":2,"masked":"__","lives":6,"turn":"bob@CIC"}))
        self.assertFalse(p.apply_state({"game_id":self.host.game_id,"seq":1,"masked":"_a","lives":6,"turn":"bob@CIC"}))


if __name__=="__main__":unittest.main()
