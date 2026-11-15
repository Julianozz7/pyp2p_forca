import unicodedata


class Hangman:
    def __init__(self, word, max_lives=6):
        self.word = self.normalize(word)
        self.max_lives = max_lives
        self.lives = max_lives
        self.guessed_letters = set()
        self.wrong_letters = set()

        if not 3 <= len(self.word) <= 20:
            raise ValueError("A palavra deve ter entre 3 e 20 letras.")

        if not self.word.isalpha() or not self.word.isascii():
            raise ValueError("A palavra deve conter somente letras de A a Z.")

    @staticmethod
    def normalize(text):
        text = text.strip().lower()
        text = unicodedata.normalize("NFD", text)
        return "".join(
            char for char in text
            if unicodedata.category(char) != "Mn"
        )

    def guess_letter(self, letter):
        letter = self.normalize(letter)

        if len(letter) != 1 or not letter.isalpha() or not letter.isascii():
            return {"valid": False, "message": "Digite apenas uma letra de A a Z."}

        if self.is_finished():
            return {"valid": False, "message": "A partida já terminou."}

        if letter in self.guessed_letters or letter in self.wrong_letters:
            return {"valid": False, "message": "Essa letra já foi utilizada."}

        if letter in self.word:
            self.guessed_letters.add(letter)
            return {
                "valid": True,
                "correct": True,
                "message": "Letra correta!"
            }

        self.wrong_letters.add(letter)
        self.lives -= 1

        return {
            "valid": True,
            "correct": False,
            "message": "Letra incorreta!"
        }

    def guess_word(self, word):
        word = self.normalize(word)

        if self.is_finished():
            return {"valid": False, "message": "A partida já terminou."}

        if word == self.word:
            self.guessed_letters.update(self.word)
            return {"valid": True, "correct": True, "message": "Você acertou a palavra!"}

        self.lives -= 1
        return {"valid": True, "correct": False, "message": "Você errou a palavra!"}

    def get_hidden_word(self):
        return " ".join(
            letter if letter in self.guessed_letters else "_"
            for letter in self.word
        )

    def is_won(self):
        return all(letter in self.guessed_letters for letter in self.word)

    def is_lost(self):
        return self.lives <= 0

    def is_finished(self):
        return self.is_won() or self.is_lost()

    def get_state(self):
        return {
            "word": self.get_hidden_word(),
            "lives": self.lives,
            "max_lives": self.max_lives,
            "guessed_letters": sorted(self.guessed_letters),
            "wrong_letters": sorted(self.wrong_letters),
            "won": self.is_won(),
            "lost": self.is_lost(),
            "finished": self.is_finished()
        }
