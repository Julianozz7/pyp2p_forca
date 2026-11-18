import hashlib, time


class GamePlayer:
    def __init__(self,peer_id):
        self.peer_id=peer_id;self.host_id=None;self.game_id=None;self.status="IDLE"
        self.players=[];self.lives=0;self.max_lives=0;self.masked="";self.turn=None;self.seq=0
        self.word_hash=None;self.last_state=None;self.turn_timeout=30;self.state_received_at=time.monotonic()

    def join_game(self,host_id,game_id):
        if self.status!="IDLE":return False
        self.host_id,self.game_id=host_id,game_id;self.status="JOINED";return True
    def leave_game(self):
        self.host_id=None;self.game_id=None;self.status="IDLE";self.players=[]
        self.lives=0;self.max_lives=0;self.masked="";self.turn=None;self.seq=0;self.word_hash=None;self.last_state=None
    def start_game(self,message):
        if message.get("game_id")!=self.game_id:return False
        self.players=list(message.get("players",[]));self.lives=message.get("lives",6);self.max_lives=self.lives
        self.word_hash=message.get("word_hash");self.turn_timeout=message.get("turn_timeout",30)
        self.status="PLAYING";return True
    def apply_state(self,message):
        if message.get("game_id")!=self.game_id or not isinstance(message.get("seq"),int):return False
        if message["seq"]<=self.seq:return False
        self.seq=message["seq"];self.masked=message.get("masked",message.get("word",""))
        self.lives=message.get("lives",self.lives)
        if self.max_lives<=0:self.max_lives=self.lives
        self.turn=message.get("turn")
        self.players=list(message.get("players",self.players));self.last_state=message
        self.state_received_at=time.monotonic()
        return True
    def is_my_turn(self):return self.status=="PLAYING" and self.turn==self.peer_id
    def verify_game_over(self,message):
        if message.get("game_id")!=self.game_id:return False
        word=message.get("word")
        if not isinstance(word,str):return False
        if not self.word_hash:
            self.status="FINISHED"
            return None
        valid=hashlib.sha256(word.encode()).hexdigest()==self.word_hash
        self.status="FINISHED"
        return valid
    def get_status(self):
        return {"peer_id":self.peer_id,"host_id":self.host_id,"game_id":self.game_id,
                "status":self.status,"masked":self.masked,"lives":self.lives,
                "turn":self.turn,"seq":self.seq}
