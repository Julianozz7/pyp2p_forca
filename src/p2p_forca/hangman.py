"""Pure game rules for the cooperative Hangman game."""
import unicodedata
import re


class Hangman:
    def __init__(self, word, max_lives=6):
        self.word = self.normalize(word)
        if not isinstance(max_lives, int) or isinstance(max_lives, bool) or max_lives < 1:
            raise ValueError("Vidas deve ser um inteiro positivo.")
        if not 3 <= len(self.word) <= 20 or not re.fullmatch(r"[a-z]{3,20}", self.word):
            raise ValueError("A palavra deve conter de 3 a 20 letras de A a Z.")
        self.max_lives = max_lives
        self.lives = max_lives
        self.guessed_letters = set()
        self.wrong_letters = set()

    @staticmethod
    def normalize(text):
        if not isinstance(text, str):
            raise ValueError("A palavra deve ser texto.")
        text = unicodedata.normalize("NFD", text.strip().lower())
        return "".join(c for c in text if unicodedata.category(c) != "Mn")

    def used_letters(self):
        return self.guessed_letters | self.wrong_letters

    def guess_letter(self, letter):
        letter = letter.strip().lower() if isinstance(letter, str) else ""
        if not re.fullmatch(r"[a-z]", letter):
            return {"valid": False, "reason": "invalid_letter", "message": "Digite uma letra de a a z."}
        if self.is_finished():
            return {"valid": False, "reason": "game_not_running", "message": "A partida terminou."}
        if letter in self.used_letters():
            return {"valid": False, "reason": "letter_already_used", "message": "Essa letra já foi utilizada."}
        correct = letter in self.word
        if correct:
            self.guessed_letters.add(letter)
        else:
            self.wrong_letters.add(letter)
            self.lives -= 1
        return {"valid": True, "correct": correct}

    def guess_word(self, word):
        word = self.normalize(word) if isinstance(word, str) else ""
        if self.is_finished():
            return {"valid": False, "reason": "game_not_running"}
        if not re.fullmatch(r"[a-z]{3,20}", word):
            return {"valid": False, "reason": "invalid_word"}
        correct = word == self.word
        if correct:
            self.guessed_letters.update(self.word)
        else:
            self.lives -= 1
        return {"valid": True, "correct": correct}

    def get_hidden_word(self):
        return "".join(c if c in self.guessed_letters else "_" for c in self.word)

    def is_won(self):
        return all(c in self.guessed_letters for c in self.word)

    def is_lost(self):
        return self.lives <= 0

    def is_finished(self):
        return self.is_won() or self.is_lost()

    def get_state(self):
        return {"masked": self.get_hidden_word(), "word": self.get_hidden_word(),
                "lives": self.lives, "max_lives": self.max_lives,
                "guessed_letters": sorted(self.guessed_letters),
                "wrong_letters": sorted(self.wrong_letters),
                "won": self.is_won(), "lost": self.is_lost(),
                "finished": self.is_finished()}
