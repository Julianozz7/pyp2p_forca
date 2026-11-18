import hashlib, time, uuid
from p2p_forca.hangman import Hangman


class GameHost:
    def __init__(self, peer_id, lives=6, max_players=4, turn_timeout=30):
        self.peer_id,self.lives,self.max_players,self.turn_timeout=peer_id,lives,max_players,turn_timeout
        self.game_id=None; self.players=[]; self.scores={}; self.game=None
        self.status="IDLE"; self.turn=0; self.seq=0; self.last_activity=time.monotonic()
        self.word_hash=None; self.last_guess=None; self.finished_at=None

    def create_game(self, word):
        if self.status=="FINISHED" and self.finished_at and time.monotonic()-self.finished_at>=60:
            self.status="IDLE"
        if self.status!="IDLE":return False
        self.game=Hangman(word,self.lives); self.game_id=f"{self.peer_id}/{uuid.uuid4().hex[:8]}"
        self.word_hash=hashlib.sha256(self.game.word.encode()).hexdigest()
        self.players=[];self.scores={};self.turn=0;self.seq=0;self.last_guess=None
        self.status="OPEN";self.last_activity=time.monotonic();return True

    def add_player(self, peer_id):
        if self.status!="OPEN" or peer_id==self.peer_id:return False
        if peer_id in self.players:return True
        if len(self.players)>=self.max_players:return False
        self.players.append(peer_id);self.scores[peer_id]=0;return True

    def rejoin_player(self, peer_id):
        if self.status!="RUNNING" or peer_id not in self.scores:return False
        if peer_id not in self.players:self.players.append(peer_id)
        return True

    def remove_player(self, peer_id):
        if peer_id not in self.players:return False
        idx=self.players.index(peer_id); self.players.remove(peer_id)
        if self.players:
            if idx<self.turn:self.turn-=1
            self.turn%=len(self.players)
        else:self.turn=0
        self.last_activity=time.monotonic()
        if self.status=="RUNNING" and not self.players:
            return self.finish_game("ABORTED","no_players")
        return True

    def start_game(self):
        if self.status!="OPEN" or not self.players:return None
        self.status="RUNNING";self.turn=0;self.last_activity=time.monotonic()
        return {"type":"GAME_START","game_id":self.game_id,"players":self.players.copy(),
                "lives":self.game.lives,"word_length":len(self.game.word),
                "word_hash":self.word_hash,"turn_timeout":self.turn_timeout}

    def get_current_player(self):
        return self.players[self.turn] if self.players else None
    def _next_turn(self):
        if self.players:self.turn=(self.turn+1)%len(self.players)
        self.last_activity=time.monotonic()

    def _guess(self, peer_id, kind, value):
        if self.status!="RUNNING":return {"accepted":False,"reason":"game_not_running"}
        if peer_id not in self.players:return {"accepted":False,"reason":"game_not_running"}
        if peer_id!=self.get_current_player():return {"accepted":False,"reason":"not_your_turn"}
        hidden_before = sum(1 for ch in self.game.word if ch not in self.game.guessed_letters)
        if kind=="letter":result=self.game.guess_letter(value)
        else:result=self.game.guess_word(value)
        if not result["valid"]:return {"accepted":False,"reason":result.get("reason","invalid_word")}
        correct=result["correct"]
        if kind=="letter" and correct:self.scores[peer_id]+=self.game.word.count(self.game.normalize(value))
        if kind=="word" and correct:
            self.scores[peer_id] += hidden_before
        self.last_guess={"player":peer_id,kind:value,"correct":correct}
        self._next_turn()
        if self.game.is_won():return {"accepted":True,"correct":True,"game_over":self.finish_game("WON",winner=peer_id)}
        if self.game.is_lost():return {"accepted":True,"correct":False,"game_over":self.finish_game("LOST")}
        return {"accepted":True,"correct":correct,"state":self.get_state()}

    def guess_letter(self,peer_id,letter):return self._guess(peer_id,"letter",letter)
    def guess_word(self,peer_id,word):return self._guess(peer_id,"word",word)

    def get_state(self, last_guess=None):
        self.seq+=1
        if last_guess is not None:self.last_guess=last_guess
        return {"type":"STATE","game_id":self.game_id,"seq":self.seq,
                "masked":self.game.get_hidden_word(),"wrong_letters":sorted(self.game.wrong_letters),
                "lives":self.game.lives,"turn":self.get_current_player(),"players":self.players.copy(),
                "last_guess":self.last_guess,"scores":self.scores.copy()}

    def snapshot_state(self):
        return {"type":"STATE","game_id":self.game_id,"seq":self.seq,
                "masked":self.game.get_hidden_word(),"wrong_letters":sorted(self.game.wrong_letters),
                "lives":self.game.lives,"turn":self.get_current_player(),"players":self.players.copy(),
                "last_guess":self.last_guess,"scores":self.scores.copy()}

    def check_timeout(self):
        if self.status!="RUNNING" or time.monotonic()-self.last_activity<self.turn_timeout:return None
        player=self.get_current_player()
        self._next_turn()
        return self.get_state({"player":player,"timeout":True})

    def finish_game(self,result=None,reason=None,winner=None):
        if self.status=="FINISHED":return None
        if result is None:result="WON" if self.game and self.game.is_won() else "LOST"
        self.status="FINISHED";self.finished_at=time.monotonic()
        msg={"type":"GAME_OVER","game_id":self.game_id,"result":result,
             "word":self.game.word if self.game else "","winner":winner,
             "scores":self.scores.copy()}
        if reason:msg["reason"]=reason
        return msg

    def cancel_game(self):
        if self.status not in ("OPEN","RUNNING"):return False
        return self.finish_game("ABORTED","host_shutdown")
