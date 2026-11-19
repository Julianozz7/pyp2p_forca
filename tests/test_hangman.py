import unittest

from p2p_forca.game_host import GameHost
from p2p_forca.hangman import Hangman


class TestHangman(unittest.TestCase):
    def test_normalization_and_invalid_words(self):
        self.assertEqual(Hangman("AVIÃO").word, "aviao")
        for word in ("a", "duas palavras", "bem-te-vi", "casa2"):
            with self.subTest(word=word), self.assertRaises(ValueError):
                Hangman(word)

    def test_wrong_letter_order_and_repeat(self):
        game = Hangman("banana", 3)
        for letter in ("z", "e"):
            self.assertFalse(game.guess_letter(letter)["correct"])
        self.assertEqual(game.wrong_letters, ["z", "e"])
        self.assertEqual(game.guess_letter("z")["reason"], "letter_already_used")
        self.assertEqual(game.lives, 1)

    def test_words(self):
        game = Hangman("banana", 3)
        self.assertEqual(game.guess_word("3")["reason"], "invalid_word")
        self.assertFalse(game.guess_word("laranja")["correct"])
        self.assertEqual(game.get_hidden_word(), "______")
        self.assertEqual(game.lives, 2)
        self.assertTrue(game.guess_word("banana")["correct"])
        self.assertTrue(game.is_won())

    def test_winner_completes_word_even_with_lower_score(self):
        host = GameHost("alice@CIC")
        host.create_game("abacaxi")
        for name in ("bob@CIC", "carol@CIC"):
            host.join(name, host.game_id)
        host.start_game()
        host.guess("bob@CIC", "letter", "a")
        host.guess("carol@CIC", "letter", "b")
        host.guess("bob@CIC", "letter", "c")
        host.guess("carol@CIC", "letter", "x")
        host.guess("bob@CIC", "letter", "z")
        host.guess("carol@CIC", "word", "abacaxi")
        end = host.finish_game(winner="carol@CIC")
        self.assertEqual(end["scores"], {"bob@CIC": 4, "carol@CIC": 3})
        self.assertEqual(end["winner"], "carol@CIC")