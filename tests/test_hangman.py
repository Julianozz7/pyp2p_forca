import unittest

from p2p_forca.hangman import Hangman


class TestHangman(unittest.TestCase):

    def test_normalizes_word(self):
        self.assertEqual(Hangman("  Maçã ").word, "maca")

    def test_invalid_words(self):
        for word in ("ab", "a" * 21, "casa branca", "ca-sa", "123", "cas4"):
            with self.assertRaises(ValueError):
                Hangman(word)

    def test_masked_has_no_spaces(self):
        game = Hangman("banana")
        game.guess_letter("a")

        self.assertEqual(game.get_masked(), "_a_a_a")
        self.assertEqual(game.get_hidden_word(), "_ a _ a _ a")

    def test_correct_letter_reveals_all_occurrences(self):
        game = Hangman("banana")
        result = game.guess_letter("A")

        self.assertTrue(result["valid"])
        self.assertTrue(result["correct"])
        self.assertEqual(result["count"], 3)
        self.assertEqual(game.lives, 6)

    def test_wrong_letter_costs_life_and_keeps_order(self):
        game = Hangman("banana", 6)
        game.guess_letter("z")
        game.guess_letter("e")

        self.assertEqual(game.lives, 4)
        self.assertEqual(game.get_state()["wrong_letters"], ["z", "e"])

    def test_repeated_letter_is_rejected_without_penalty(self):
        game = Hangman("banana")
        game.guess_letter("a")
        game.guess_letter("z")
        lives = game.lives

        for letter in ("a", "z"):
            result = game.guess_letter(letter)
            self.assertFalse(result["valid"])
            self.assertEqual(result["reason"], "letter_already_used")

        self.assertEqual(game.lives, lives)

    def test_invalid_letter(self):
        game = Hangman("banana")

        for value in ("", "ab", "1", "-"):
            result = game.guess_letter(value)
            self.assertFalse(result["valid"])
            self.assertEqual(result["reason"], "invalid_letter")

    def test_correct_word_wins_and_counts_hidden_letters(self):
        game = Hangman("banana")
        game.guess_letter("a")
        result = game.guess_word("banana")

        self.assertTrue(result["correct"])
        self.assertEqual(result["count"], 3)       # b, n, n
        self.assertTrue(game.is_won())

    def test_wrong_word_costs_life(self):
        game = Hangman("banana")
        result = game.guess_word("bananas")

        self.assertTrue(result["valid"])
        self.assertFalse(result["correct"])
        self.assertEqual(game.lives, 5)
        self.assertEqual(game.get_masked(), "______")

    def test_invalid_word(self):
        game = Hangman("banana")

        result = game.guess_word("ba")
        self.assertFalse(result["valid"])
        self.assertEqual(result["reason"], "invalid_word")

    def test_lost_when_lives_exhausted(self):
        game = Hangman("abc", 2)
        game.guess_letter("x")
        game.guess_letter("y")

        self.assertTrue(game.is_lost())
        self.assertTrue(game.is_finished())
        self.assertFalse(game.guess_letter("z")["valid"])

    def test_won_when_all_letters_found(self):
        game = Hangman("casa")

        for letter in "cas":
            game.guess_letter(letter)

        self.assertTrue(game.is_won())


if __name__ == "__main__":
    unittest.main()