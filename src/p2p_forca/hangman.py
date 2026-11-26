import re
import unicodedata

class Hangman:
    def __init__(self, word, max_lives=6):
        self.word = self.normalize(word)
        if not self.valid_word(self.word):
            raise ValueError("A palavra deve conter de 3 a 20 letras de A a Z.")
        if type(max_lives) is not int or max_lives < 1:
            raise ValueError("O número de vidas deve ser um inteiro positivo.")

        self.max_lives = max_lives
        self.lives = max_lives
        self.guessed_letters = set()
        self.wrong_letters = []

    @staticmethod
    def normalize(text):
        decomposed = unicodedata.normalize("NFD", text.lower())
        return "".join(char for char in decomposed if unicodedata.category(char) != "Mn")

    @staticmethod
    def valid_word(word):
        return isinstance(word, str) and re.fullmatch("[a-z]{3,20}", word) is not None

    def guess_letter(self, letter):
        if not isinstance(letter, str) or re.fullmatch("[a-z]", letter) is None:
            return {"valid": False, "reason": "invalid_letter"}
        if self.is_finished():
            return {"valid": False, "reason": "game_not_running"}
        if letter in self.guessed_letters or letter in self.wrong_letters:
            return {"valid": False, "reason": "letter_already_used"}

        correct = letter in self.word
        if correct:
            self.guessed_letters.add(letter)
        else:
            self.wrong_letters.append(letter)
            self.lives -= 1
        return {"valid": True, "correct": correct}

    def guess_word(self, word):
        if not self.valid_word(word):
            return {"valid": False, "reason": "invalid_word"}
        if self.is_finished():
            return {"valid": False, "reason": "game_not_running"}

        correct = word == self.word
        if correct:
            self.guessed_letters.update(self.word)
        else:
            self.lives -= 1
        return {"valid": True, "correct": correct}

    def get_hidden_word(self):
        return "".join(letter if letter in self.guessed_letters else "_" for letter in self.word)

    def is_won(self):
        return "_" not in self.get_hidden_word()

    def is_lost(self):
        return self.lives == 0

    def is_finished(self):
        return self.is_won() or self.is_lost()

    def get_state(self):
        return {
            "word": self.get_hidden_word(),
            "lives": self.lives,
            "max_lives": self.max_lives,
            "guessed_letters": sorted(self.guessed_letters),
            "wrong_letters": self.wrong_letters.copy(),
            "won": self.is_won(),
            "lost": self.is_lost(),
            "finished": self.is_finished(),
        }