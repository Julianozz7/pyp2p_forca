"""Lógica pura do Jogo da Forca (sem nenhuma dependência de rede)."""

import unicodedata

WORD_MIN = 3
WORD_MAX = 20


class Hangman:
    """Regras da forca: palavra secreta, máscara, vidas e letras usadas."""

    def __init__(self, word, max_lives=6):
        self.word = self.normalize(word)
        self.max_lives = max_lives
        self.lives = max_lives
        self.guessed_letters = set()   # letras certas já reveladas
        self.wrong_letters = set()     # letras erradas (para consulta rápida)
        self.wrong_order = []          # letras erradas, na ordem das tentativas

        if not self.is_valid_word(self.word):
            raise ValueError(
                f"A palavra deve ter de {WORD_MIN} a {WORD_MAX} letras "
                "(somente a-z, sem espaços, acentos ou hífens)."
            )

    # ------------------------------------------------------------------
    # utilitários
    # ------------------------------------------------------------------
    @staticmethod
    def normalize(text):
        """Minúsculas e remoção de acentos."""
        text = str(text).strip().lower()
        text = unicodedata.normalize("NFD", text)
        return "".join(
            char for char in text
            if unicodedata.category(char) != "Mn"
        )

    @staticmethod
    def is_valid_word(word):
        """Palavra já normalizada: somente a-z, de 3 a 20 caracteres."""
        return (
            WORD_MIN <= len(word) <= WORD_MAX
            and word.isascii()
            and word.isalpha()
        )

    # ------------------------------------------------------------------
    # jogadas
    # ------------------------------------------------------------------
    def guess_letter(self, letter):
        """Processa um palpite de letra.

        Retorna um dicionário com:
          valid   -> False se a jogada foi rejeitada (não consome vida)
          reason  -> código da rejeição (invalid_letter, letter_already_used,
                     game_not_running)
          correct -> True/False quando valid == True
          count   -> quantas posições foram reveladas (letra correta)
        """
        letter = self.normalize(letter)

        if len(letter) != 1 or not letter.isalpha() or not letter.isascii():
            return {
                "valid": False,
                "reason": "invalid_letter",
                "message": "Digite apenas uma letra de A a Z.",
            }

        if self.is_finished():
            return {
                "valid": False,
                "reason": "game_not_running",
                "message": "A partida já terminou.",
            }

        if letter in self.guessed_letters or letter in self.wrong_letters:
            return {
                "valid": False,
                "reason": "letter_already_used",
                "message": "Essa letra já foi utilizada.",
            }

        if letter in self.word:
            self.guessed_letters.add(letter)
            return {
                "valid": True,
                "correct": True,
                "count": self.word.count(letter),
                "message": "Letra correta!",
            }

        self.wrong_letters.add(letter)
        self.wrong_order.append(letter)
        self.lives -= 1

        return {
            "valid": True,
            "correct": False,
            "count": 0,
            "message": "Letra incorreta!",
        }

    def guess_word(self, word):
        """Processa um palpite da palavra inteira."""
        word = self.normalize(word)

        if not self.is_valid_word(word):
            return {
                "valid": False,
                "reason": "invalid_word",
                "message": "Palavra inválida (use de 3 a 20 letras, a-z).",
            }

        if self.is_finished():
            return {
                "valid": False,
                "reason": "game_not_running",
                "message": "A partida já terminou.",
            }

        if word == self.word:
            hidden = self.hidden_count()
            self.guessed_letters.update(self.word)
            return {
                "valid": True,
                "correct": True,
                "count": hidden,
                "message": "Você acertou a palavra!",
            }

        self.lives -= 1
        return {
            "valid": True,
            "correct": False,
            "count": 0,
            "message": "Você errou a palavra!",
        }

    # ------------------------------------------------------------------
    # consultas
    # ------------------------------------------------------------------
    def hidden_count(self):
        """Quantidade de posições ainda ocultas."""
        return sum(
            1 for letter in self.word
            if letter not in self.guessed_letters
        )

    def get_masked(self):
        """Máscara do protocolo: 'b_n_n_' (sem espaços)."""
        return "".join(
            letter if letter in self.guessed_letters else "_"
            for letter in self.word
        )

    def get_hidden_word(self):
        """Máscara para exibição: 'b _ n _ n _' (com espaços)."""
        return " ".join(self.get_masked())

    def is_won(self):
        return all(letter in self.guessed_letters for letter in self.word)

    def is_lost(self):
        return self.lives <= 0

    def is_finished(self):
        return self.is_won() or self.is_lost()

    def get_state(self):
        return {
            "word": self.get_hidden_word(),
            "masked": self.get_masked(),
            "lives": self.lives,
            "max_lives": self.max_lives,
            "guessed_letters": sorted(self.guessed_letters),
            "wrong_letters": list(self.wrong_order),
            "won": self.is_won(),
            "lost": self.is_lost(),
            "finished": self.is_finished(),
        }