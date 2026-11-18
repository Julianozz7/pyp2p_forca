import json, logging, socket, threading, time, uuid
from pathlib import Path
from p2p_forca.peer_connection import PeerConnection
from p2p_forca.peer_table import PeerTable
from p2p_forca.rendezvous_connection import RendezvousConnection
from p2p_forca.keep_alive import KeepAlive
from p2p_forca.message_router import MessageRouter
from p2p_forca.game_host import GameHost
from p2p_forca.game_player import GamePlayer

log=logging.getLogger(__name__)


class P2PClient:
    def __init__(self,config_path="config.json"):
        self.config_path=Path(config_path);self.config=self._load_config()
        self.name=self.config["name"];self.namespace=self.config["namespace"]
        self.peer_id=f"{self.name}@{self.namespace}";self.listen_port=int(self.config["listen_port"])
        self.peer_table=PeerTable();self.router=MessageRouter(self.peer_id)
        self.rendezvous=RendezvousConnection(self.config["rdv_host"],self.config["rdv_port"],self.namespace,self.peer_id)
        self.keep_alive=KeepAlive(self.peer_table,self.config.get("ping_interval",30),
                                  self.config.get("ping_miss_limit",3),self._peer_stale)
        self.server_socket=None;self.connections={};self.running=False;self.lock=threading.RLock()
        self.pending={};self.games_cache=[];self.public_ip=None;self.registered=False
        self.effective_register_ttl=int(self.config.get("register_ttl",7200))
        self.host=GameHost(self.peer_id,self.config.get("lives",6),self.config.get("max_players",4),self.config.get("turn_timeout",30))
        self.player=GamePlayer(self.peer_id);self._threads=[]
        self._register_handlers()

    def _load_config(self):
        try:
            cfg=json.loads(self.config_path.read_text(encoding="utf8"))
        except (OSError,json.JSONDecodeError) as e:raise RuntimeError(f"Não foi possível carregar {self.config_path}: {e}")
        defaults={"rdv_host":"pyp2p.mfcaetano.cc","rdv_port":8080,"namespace":"CIC","name":"alice",
                  "listen_port":8081,"register_ttl":7200,"discover_interval":20,"ping_interval":30,
                  "ping_miss_limit":3,"max_reconnect_attempts":5,"base_backoff":2,"max_backoff":60,
                  "lives":6,"max_players":4,"turn_timeout":30,"words_file":"words.txt","log_file":None}
        defaults.update(cfg)
        for key in ("rdv_host","rdv_port","namespace","name","listen_port"):
            if key not in defaults:raise ValueError(f"Configuração ausente: {key}")
        return defaults

    def _register_handlers(self):
        handlers={"HELLO":self._handle_hello,"HELLO_OK":self._handle_hello_ok,
          "PING":self._handle_ping,"PONG":self._handle_pong,"BYE":self._handle_bye,
          "BYE_OK":self._handle_bye_ok,"ERROR":self._handle_error,
          "GAME_LIST":self._handle_game_list,"GAME_LIST_OK":self._handle_game_list_ok,
          "JOIN":self._handle_join,"JOIN_OK":self._handle_join_ok,"JOIN_ERR":self._handle_join_err,
          "LEAVE":self._handle_leave,"LEAVE_OK":self._handle_leave_ok,
          "GAME_START":self._handle_game_start,"STATE":self._handle_state,
          "GUESS":self._handle_guess,"GUESS_RESULT":self._handle_guess_result,
          "GAME_OVER":self._handle_game_over}
        for typ,fn in handlers.items():self.router.register_handler(typ,fn)

    def _msg(self,typ,peer_id=None,**fields):
        msg={"type":typ,"ttl":1,"msg_id":str(uuid.uuid4()),**fields}
        if peer_id:
            msg.update({"src":self.peer_id,"dst":peer_id})
        return msg

    def start_server(self):
        if not 1 <= int(self.listen_port) <= 65535:
            raise ValueError("listen_port deve estar entre 1 e 65535.")
        self.server_socket=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        self.server_socket.bind(("0.0.0.0",self.listen_port));self.server_socket.listen(50)
        self.server_socket.settimeout(1)
        t=threading.Thread(target=self._accept_connections,daemon=True);t.start();self._threads.append(t)

    def _accept_connections(self):
        while self.running:
            try:
                sock,addr=self.server_socket.accept()
                conn=PeerConnection(sock,addr,on_message=self._on_message,on_disconnect=self._on_disconnect,direction="inbound")
                conn.local_peer_id=self.peer_id;conn.handshake_event=threading.Event();conn.start()
                threading.Thread(target=self._handshake_watch, args=(conn,), daemon=True).start()
            except socket.timeout:continue
            except OSError:break

    def _handshake_watch(self,conn):
        if not conn.handshake_event.wait(5) and conn.is_connected():
            log.warning("[PeerServer] Handshake timeout from %s",conn.address)
            conn.close()

    def _on_message(self,conn,msg):
        typ=msg.get("type") if isinstance(msg,dict) else None
        if conn.peer_id is None:
            expected="HELLO" if conn.direction=="inbound" else "HELLO_OK"
            if typ not in (expected,"BYE","BYE_OK"):
                conn._send_error(msg.get("msg_id") if isinstance(msg,dict) else None,
                                 "bad_request","Handshake required before application messages")
                conn.close()
                return
        elif typ in ("HELLO","HELLO_OK"):
            conn._send_error(msg.get("msg_id"),"bad_request","Handshake already completed")
            conn.close()
            return
        self.router.route(conn,msg)

    def _on_disconnect(self,conn):
        pid=conn.peer_id
        if not pid:return
        with self.lock:
            was_current=self.connections.get(pid) is conn
            if was_current:self.connections.pop(pid,None)
        peer=self.peer_table.get_peer(pid)
        if was_current and peer and peer.get("connection") is conn:
            self.peer_table.update_status(pid,"STALE")
            self.keep_alive.reset_peer(pid)
            self._schedule_reconnect(pid)
        if was_current and self.player.host_id==pid and self.player.status in ("JOINED","PLAYING"):
            log.warning("[Game] Host %s unreachable — game aborted",pid)
            self.player.leave_game()
        if was_current and self.host.status in ("OPEN","RUNNING") and pid in self.host.players:
            log.warning("[Host] Player %s disconnected — removing from game",pid)
            removed=self.host.remove_player(pid)
            if self.host.status=="FINISHED":
                over=removed if isinstance(removed,dict) else {"type":"GAME_OVER","game_id":self.host.game_id,
                    "result":"ABORTED","word":self.host.game.word,"winner":None,
                    "scores":self.host.scores.copy(),"reason":"no_players"}
                self._broadcast_game_over(over)
            elif self.host.status=="RUNNING":self._broadcast_state(self.host.get_state())

    def _schedule_reconnect(self,pid):
        info=self.peer_table.get_peer(pid)
        if not info:return
        # The higher peer waits for the lower peer to redial; only the
        # deterministic dialer performs exponential reconnect attempts.
        if self.peer_id > pid:
            info["next_attempt"]=0
            self.peer_table.update_status(pid,"DISCOVERED")
            return
        failures=info.get("failures",0)+1
        info["failures"]=failures
        if failures>=self.config["max_reconnect_attempts"]:
            self.peer_table.update_status(pid,"GONE")
            if self.player.host_id==pid and self.player.status in ("JOINED","PLAYING"):
                log.warning("[Game] Host %s unreachable — game aborted",pid)
                self.player.leave_game()
            return
        delay=min(self.config["max_backoff"],self.config["base_backoff"]*(2**(failures-1)))
        info["next_attempt"]=time.monotonic()+delay
        self.peer_table.update_status(pid,"STALE")

    def _retry_due_peers(self):
        now=time.monotonic()
        for info in self.peer_table.get_all_peers():
            if info.get("status")!="STALE" or not info.get("rdv_present",True):
                continue
            if now < info.get("next_attempt",0):continue
            address=info.get("address")
            if not isinstance(address,(tuple,list)) or len(address)!=2:continue
            try:name,namespace=info["peer_id"].split("@",1)
            except ValueError:continue
            self._connect_peer({"name":name,"namespace":namespace,"ip":address[0],"port":address[1]})

    def _peer_stale(self,pid):
        log.warning("[KeepAlive] %s missed %s PINGs — marking STALE",pid,self.keep_alive.miss_limit)
        self._schedule_reconnect(pid)
        if self.player.host_id==pid and self.player.status in ("JOINED","PLAYING"):
            log.warning("[Game] Host %s unreachable — game aborted",pid)
            self.player.leave_game()
        if self.host.status in ("OPEN","RUNNING") and pid in self.host.players:
            log.warning("[Host] Player %s STALE — removed from game",pid)
            removed=self.host.remove_player(pid)
            if self.host.status=="FINISHED":
                over=removed if isinstance(removed,dict) else {"type":"GAME_OVER","game_id":self.host.game_id,
                    "result":"ABORTED","word":self.host.game.word,"winner":None,"scores":self.host.scores.copy(),"reason":"no_players"}
                self._broadcast_game_over(over)
            elif self.host.status=="RUNNING":self._broadcast_state(self.host.get_state())

    def _attach(self,conn,pid):
        conn.peer_id=pid;conn.local_peer_id=self.peer_id
        self.keep_alive.reset_peer(pid)
        if hasattr(conn,"handshake_event"):conn.handshake_event.set()
        with self.lock:
            old=self.connections.get(pid)
            preferred="outbound" if self.peer_id < pid else "inbound"
            if conn.direction != preferred:
                log.info("[PeerServer] Duplicate connection with %s — keeping %s",pid,preferred)
                try:conn.send_message(self._msg("BYE",pid,reason="duplicate"))
                except OSError:pass
                conn.close();return False
            if old and old is not conn:
                if old.direction==preferred:
                    log.info("[PeerServer] Duplicate connection with %s — keeping existing %s",pid,preferred)
                    conn.close();return False
                try:old.send_message(self._msg("BYE",pid,reason="duplicate"))
                except OSError:pass
                old.close()
            self.connections[pid]=conn
            self.peer_table.add_peer(pid,conn.address,conn)
            info=self.peer_table.get_peer(pid)
            if info:info["rdv_present"]=True;info["failures"]=0
        if self.player.host_id==pid and self.player.status in ("JOINED","PLAYING"):
            try:conn.send_message(self._msg("JOIN",pid,game_id=self.player.game_id))
            except OSError:pass
        return True

    def _handle_hello(self,c,m):
        if c.direction!="inbound":
            c.close();return
        pid=m.get("peer_id")
        if not isinstance(pid,str) or not pid or pid==self.peer_id or m.get("version")!="1.0":
            c.close();return
        features=m.get("features",[])
        c.features=[x for x in features if isinstance(x,str)] if isinstance(features,list) else []
        attached=self._attach(c,pid)
        if attached:
            log.info("[PeerServer] Inbound connected: %s (features=%s)",pid,c.features)
        if not c.is_connected():return
        c.send_message({"type":"HELLO_OK","msg_id":m.get("msg_id"),"peer_id":self.peer_id,
                        "version":"1.0","features":["hangman"],"ttl":1})

    def _handle_hello_ok(self,c,m):
        if c.direction!="outbound" or m.get("msg_id")!=getattr(c,"hello_msg_id",None):
            c.close();return
        pid=m.get("peer_id")
        if not isinstance(pid,str) or m.get("version")!="1.0":c.close();return
        features=m.get("features",[])
        c.features=[x for x in features if isinstance(x,str)] if isinstance(features,list) else []
        attached=self._attach(c,pid)
        if attached:log.info("[PeerServer] Connected outbound to %s (features=%s)",pid,c.features)

    def _connect_peer(self,p):
        pid=f'{p["name"]}@{p["namespace"]}'
        if pid==self.peer_id:return
        # The lexicographically smaller peer is the designated dialer. This
        # avoids unnecessary simultaneous dials; _attach still arbitrates
        # duplicates if a peer implementation opens both directions.
        if self.peer_id > pid:return
        current=self.connections.get(pid)
        if current and current.is_connected():return
        old=self.peer_table.get_peer(pid)
        if old and old.get("status")=="CONNECTED":return
        try:
            sock=socket.create_connection((p["ip"],int(p["port"])),timeout=5);sock.settimeout(None)
            c=PeerConnection(sock,(p["ip"],int(p["port"])),on_message=self._on_message,on_disconnect=self._on_disconnect,direction="outbound")
            c.local_peer_id=self.peer_id;c.handshake_event=threading.Event();c.hello_msg_id=str(uuid.uuid4());c.start()
            with self.lock:
                self.peer_table.add_peer(pid,(p["ip"],int(p["port"])))
                self.peer_table.update_status(pid,"CONNECTING")
            c.send_message({"type":"HELLO","msg_id":c.hello_msg_id,"peer_id":self.peer_id,
                            "version":"1.0","features":["hangman"],"ttl":1})
            def handshake_watch():
                if not c.handshake_event.wait(5):
                    c.close()
                    info=self.peer_table.get_peer(pid)
                    if info:
                        failures=info.get("failures",0)+1;info["failures"]=failures
                        if failures>=self.config["max_reconnect_attempts"]:
                            self.peer_table.update_status(pid,"GONE")
                            if self.player.host_id==pid and self.player.status in ("JOINED","PLAYING"):
                                log.warning("[Game] Host %s unreachable — game aborted",pid)
                                self.player.leave_game()
                        else:
                            info["next_attempt"]=time.monotonic()+min(self.config["max_backoff"],self.config["base_backoff"]*2**(failures-1))
                            self.peer_table.update_status(pid,"STALE")
            threading.Thread(target=handshake_watch,daemon=True).start()
        except OSError as e:
            log.debug("Conexão com %s falhou: %s",pid,e)
            if old:
                failures=old.get("failures",0)+1;old["failures"]=failures
                if failures>=self.config["max_reconnect_attempts"]:
                    self.peer_table.update_status(pid,"GONE")
                    if self.player.host_id==pid and self.player.status in ("JOINED","PLAYING"):
                        log.warning("[Game] Host %s unreachable — game aborted",pid)
                        self.player.leave_game()
                else:
                    old["next_attempt"]=time.monotonic()+min(self.config["max_backoff"],self.config["base_backoff"]*2**(failures-1))
                    self.peer_table.update_status(pid,"STALE")

    def register(self):
        response=self.rendezvous.register(self.listen_port,self.config["register_ttl"])
        if not response or response.get("status")!="OK":
            log.error("Falha no REGISTER Rendezvous: %s",response);return False
        self.registered=True;self.public_ip=response.get("ip")
        self.effective_register_ttl=int(response.get("ttl",self.config.get("register_ttl",7200)))
        if self.effective_register_ttl < 6:
            log.warning("register_ttl abaixo de 6s pode exceder o limite de 50 requisições/minuto do Rendezvous.")
        log.info("[RDV] registered %s as %s:%s (ttl=%s)",self.peer_id,self.public_ip,self.listen_port,self.effective_register_ttl)
        return True

    def discover_peers(self,namespace=None):
        response=self.rendezvous.discover_all() if namespace=="*" else self.rendezvous.discover(namespace)
        if not response or response.get("status")!="OK":
            log.warning("DISCOVER falhou: %s",response);return None
        peers=[p for p in response.get("peers",[]) if not (p.get("name")==self.name and p.get("namespace")==self.namespace)]
        if namespace not in (None, self.namespace):
            return peers
        present=set()
        for p in peers:
            pid=f'{p["name"]}@{p["namespace"]}';present.add(pid)
            info=self.peer_table.get_peer(pid)
            if not info:
                info=self.peer_table.add_peer(pid,(p["ip"],p["port"]))
                info["rdv_present"]=True
            else:
                info["address"]=(p["ip"],int(p["port"]))
                if not info.get("rdv_present",True):
                    info["failures"]=0;info["next_attempt"]=0
                    self.peer_table.update_status(pid,"DISCOVERED")
                info["rdv_present"]=True
            info=self.peer_table.get_peer(pid)
            if info and info["status"] not in ("CONNECTED","CONNECTING","GONE") and time.monotonic()>=info.get("next_attempt",0):
                self._connect_peer(p)
        for info in self.peer_table.get_all_peers():
            if info["peer_id"] not in present and (info.get("rdv_present",True) or info.get("status")!="GONE"):
                info["rdv_present"]=False
                self.peer_table.update_status(info["peer_id"],"GONE")
                if info.get("connection"):
                    try:info["connection"].send_message(self._msg("BYE",info["peer_id"],reason="idle"))
                    except OSError:pass
                    info["connection"].close()
                if self.player.host_id==info["peer_id"] and self.player.status in ("JOINED","PLAYING"):
                    log.warning("[Game] Host %s unreachable — game aborted",info["peer_id"])
                    self.player.leave_game()
        return peers

    def _periodic(self):
        now=time.monotonic()
        next_discover=now
        last_register=now
        ttl=self.effective_register_ttl
        while self.running:
            now=time.monotonic()
            if now >= next_discover:
                try:self.discover_peers()
                except Exception:log.exception("Erro no DISCOVER periódico")
                next_discover=now+max(2,self.config["discover_interval"])
            if now-last_register >= max(1,ttl/2):
                try:self.register()
                except Exception:log.exception("Erro no refresh REGISTER")
                ttl=self.effective_register_ttl
                last_register=now
            self._check_turn_timeout()
            self._retry_due_peers()
            time.sleep(.25)

    def _check_turn_timeout(self):
        if self.host.status=="RUNNING":
            state=self.host.check_timeout()
            if state:
                log.info("[Host] Turn timeout for %s — skipping",state.get("last_guess",{}).get("player"))
                self._broadcast_state(state)

    def start(self):
        if self.running:return True
        self.running=True
        try:
            self.start_server()
            if not self.register():
                self.stop()
                return False
            self.keep_alive.start(self.peer_table.get_connected_peers)
            t=threading.Thread(target=self._periodic,daemon=True);t.start();self._threads.append(t)
            # First discovery immediately.
            threading.Thread(target=self.discover_peers,daemon=True).start()
            return True
        except Exception:
            self.stop();raise

    def _send(self,pid,msg):
        p=self.peer_table.get_peer(pid);c=p.get("connection") if p else None
        if not c or not c.is_connected():return False
        try:c.send_message(msg);return True
        except OSError:return False

    def _send_to(self,pid,typ,**fields):return self._send(pid,self._msg(typ,pid,**fields))
    def _broadcast(self,msg,players=None):
        for pid in list(players if players is not None else self.host.players):self._send(pid,self._msg(msg["type"],pid,**{k:v for k,v in msg.items() if k not in ("type","msg_id","ttl","src","dst")}))

    def create_game(self,word,max_players=None):
        if max_players is not None:
            maximum=int(max_players)
            if not 1 <= maximum <= 4:raise ValueError("max_players deve estar entre 1 e 4.")
            self.host.max_players=maximum
        ok=self.host.create_game(word)
        if ok:log.info("[Host] Game %s created (word_length=%s, lives=%s)",self.host.game_id,len(self.host.game.word),self.host.lives)
        return ok

    def start_game(self):
        msg=self.host.start_game()
        if not msg:return False
        log.info("[Host] Turn: %s (timeout %s s)",self.host.get_current_player(),self.host.turn_timeout)
        self._broadcast(msg)
        self._broadcast_state(self.host.get_state())
        return True

    def cancel_game(self):
        msg=self.host.cancel_game()
        if not msg:return False
        self._broadcast_game_over(msg);return True

    def list_games(self,timeout=2):
        peers=[p for p in self.peer_table.get_connected_peers()
               if "hangman" in getattr(p.get("connection"),"features",[])]
        self.games_cache=[]
        waiters=[]
        for p in peers:
            pid=p["peer_id"];ev=threading.Event();box={}
            mid=str(uuid.uuid4());self.pending[mid]=(ev,box)
            self._send(pid,{"type":"GAME_LIST","msg_id":mid,"src":self.peer_id,"dst":pid,"ttl":1})
            waiters.append((ev,box))
        # include locally hosted listing
        self._expire_finished_game()
        if self.host.status in ("OPEN","RUNNING","FINISHED"):
            self.games_cache.append(self._host_listing())
        deadline=time.monotonic()+timeout
        for ev,box in waiters:ev.wait(max(0,deadline-time.monotonic()))
        for ev,box in waiters:self.games_cache.extend(box.get("games",[]))
        for mid,(ev,box) in list(self.pending.items()):
            if ev in [w[0] for w in waiters]:self.pending.pop(mid,None)
        return self.games_cache

    def _expire_finished_game(self):
        if self.host.status=="FINISHED" and self.host.finished_at and time.monotonic()-self.host.finished_at>=60:
            self.host.status="IDLE"
    def _host_listing(self):
        self._expire_finished_game()
        return {"game_id":self.host.game_id,"host":self.peer_id,"status":self.host.status,
                "players":self.host.players.copy(),"max_players":self.host.max_players,
                "lives":self.host.lives,"word_length":len(self.host.game.word) if self.host.game else 0}

    def join_game(self,host_id,game_id=None):
        if host_id not in self.connections:return False,"peer_not_connected"
        if "hangman" not in getattr(self.connections[host_id],"features",[]):return False,"feature_not_supported"
        if self.player.status!="IDLE":return False,"already_in_game"
        if not game_id:
            games=self.list_games()
            found=[g for g in games if g.get("host")==host_id and g.get("status") in ("OPEN","RUNNING")]
            if not found:return False,"unknown_game"
            game_id=found[0]["game_id"]
        if not self.player.join_game(host_id,game_id):return False,"already_in_game"
        mid=str(uuid.uuid4());ev=threading.Event();box={};self.pending[mid]=(ev,box)
        self._send(host_id,{"type":"JOIN","msg_id":mid,"src":self.peer_id,"dst":host_id,"game_id":game_id,"ttl":1})
        ev.wait(5)
        self.pending.pop(mid,None)
        if not box.get("ok"):
            self.player.leave_game();return False,box.get("reason","timeout")
        return True,""

    def leave_game(self):
        if self.player.status in ("JOINED","PLAYING"):
            host=self.player.host_id;gid=self.player.game_id
            self._send_to(host,"LEAVE",game_id=gid);self.player.leave_game();return True
        if self.host.status=="OPEN":
            return self.cancel_game()
        return False

    def guess(self,letter=None,word=None):
        if self.player.status!="PLAYING":return False
        if not self.player.is_my_turn():return False
        fields={"game_id":self.player.game_id}
        if letter is not None:fields["letter"]=letter
        if word is not None:fields["word"]=word
        return self._send_to(self.player.host_id,"GUESS",**fields)

    def _broadcast_state(self,state):self._broadcast(state)
    def _broadcast_game_over(self,msg):self._broadcast(msg)

    def _handle_ping(self,c,m):
        c.send_message({"type":"PONG","msg_id":m.get("msg_id"),"timestamp":m.get("timestamp"),"ttl":1})
    def _handle_pong(self,c,m):
        if c.peer_id:self.keep_alive.receive_pong(c.peer_id,m)
    def _handle_bye(self,c,m):
        remote_id=c.peer_id or m.get("src")
        try:c.send_message({"type":"BYE_OK","msg_id":m.get("msg_id"),"src":self.peer_id,"dst":remote_id,"ttl":1})
        finally:c.close()
    def _handle_bye_ok(self,c,m):
        ev=self.pending.get(m.get("msg_id"))
        if ev:ev[0].set()
        c.close()
    def _handle_error(self,c,m):log.warning("Peer ERROR de %s: %s",c.peer_id,m)
    def _handle_game_list(self,c,m):
        if "hangman" not in getattr(c,"features",[]):return
        self._expire_finished_game()
        games=[self._host_listing()] if self.host.status in ("OPEN","RUNNING","FINISHED") else []
        c.send_message(self._msg("GAME_LIST_OK",c.peer_id,games=games,msg_id=m.get("msg_id")))
    def _handle_game_list_ok(self,c,m):
        item=self.pending.get(m.get("msg_id"))
        if item:item[1]["games"]=m.get("games",[]);item[0].set()
    def _handle_join(self,c,m):
        if "hangman" not in getattr(c,"features",[]):return
        gid=m.get("game_id");rejoin=False
        if gid!=self.host.game_id:reason="unknown_game"
        elif self.host.status=="RUNNING":
            if m.get("src") in self.host.scores:
                rejoin=self.host.rejoin_player(m["src"]);reason=None
            else:reason="game_running"
        elif self.host.status=="FINISHED":reason="game_finished"
        elif m.get("src") in self.host.players:reason="already_joined"
        elif len(self.host.players)>=self.host.max_players:reason="game_full"
        elif self.host.add_player(m["src"]):reason=None
        else:reason="game_full"
        if reason:self._send(m["src"],self._msg("JOIN_ERR",m["src"],game_id=gid,reason=reason,msg_id=m.get("msg_id")))
        else:
            join_fields={"game_id":gid,"players":self.host.players.copy(),"rejoin":rejoin}
            if rejoin:
                # Optional resume metadata lets a restarted player verify GAME_OVER too.
                join_fields.update({"word_hash":self.host.word_hash,"lives":self.host.game.lives,
                                    "turn_timeout":self.host.turn_timeout})
            self._send(m["src"],self._msg("JOIN_OK",m["src"],**join_fields,msg_id=m.get("msg_id")))
            if rejoin:
                log.info("[Host] %s rejoined (%s/%s)",m["src"],len(self.host.players),self.host.max_players)
                self._send(m["src"],self._msg("STATE",m["src"],**{k:v for k,v in self.host.snapshot_state().items() if k!="type"}))
            else:log.info("[Host] %s joined (%s/%s)",m["src"],len(self.host.players),self.host.max_players)
    def _handle_join_ok(self,c,m):
        if c.peer_id!=self.player.host_id:return
        item=self.pending.get(m.get("msg_id"))
        if item:item[1].update(ok=True);item[0].set()
        self.player.players=m.get("players",self.player.players)
        if m.get("rejoin"):
            self.player.status="PLAYING"
            self.player.word_hash=m.get("word_hash",self.player.word_hash)
            self.player.max_lives=m.get("lives",self.player.max_lives or self.player.lives)
            self.player.turn_timeout=m.get("turn_timeout",self.player.turn_timeout)
    def _handle_join_err(self,c,m):
        if c.peer_id!=self.player.host_id:return
        item=self.pending.get(m.get("msg_id"))
        if item:item[1].update(ok=False,reason=m.get("reason"));item[0].set()
    def _handle_leave(self,c,m):
        pid=m.get("src")
        if m.get("game_id")==self.host.game_id and self.host.remove_player(pid):
            self._send(pid,self._msg("LEAVE_OK",pid,game_id=self.host.game_id,msg_id=m.get("msg_id")))
            if self.host.status=="RUNNING":
                if self.host.players:self._broadcast_state(self.host.get_state())
                else:self._broadcast_game_over({"type":"GAME_OVER","game_id":self.host.game_id,"result":"ABORTED","word":self.host.game.word,"winner":None,"scores":self.host.scores.copy(),"reason":"no_players"})
            elif self.host.status=="FINISHED" and not self.host.players:
                self._broadcast_game_over({"type":"GAME_OVER","game_id":self.host.game_id,"result":"ABORTED","word":self.host.game.word,"winner":None,"scores":self.host.scores.copy(),"reason":"no_players"})
    def _handle_leave_ok(self,c,m):
        if c.peer_id==self.player.host_id:self.player.leave_game()
    def _handle_game_start(self,c,m):
        if c.peer_id==self.player.host_id and self.player.start_game(m):
            log.info("[Player] Game started: %s",self.player.game_id)
    def _handle_state(self,c,m):
        if c.peer_id!=self.player.host_id:return
        if self.player.apply_state(m):
            log.info("[Player] STATE seq=%s applied: %s lives=%s turn=%s",m["seq"],self.player.masked,self.player.lives,self.player.turn)
        else:log.debug("[Player] STATE seq=%s ignored (already at seq=%s)",m.get("seq"),self.player.seq)
    def _handle_guess(self,c,m):
        pid=m.get("src");gid=m.get("game_id")
        if gid!=self.host.game_id:
            result={"accepted":False,"reason":"game_not_running"}
        elif (("letter" in m)==("word" in m)):
            result={"accepted":False,"reason":"invalid_word"}
        elif "letter" in m:result=self.host.guess_letter(pid,m["letter"])
        else:result=self.host.guess_word(pid,m["word"])
        self._send(pid,self._msg("GUESS_RESULT",pid,game_id=gid,**{k:v for k,v in result.items() if k not in ("state","game_over")},msg_id=m.get("msg_id")))
        if result.get("accepted"):
            guess=m.get("letter",m.get("word"))
            log.info("[Host] GUESS %s %s=%s → %s",pid,"letter" if "letter" in m else "word",guess,"correct" if result.get("correct") else "incorrect")
        if result.get("state"):self._broadcast_state(result["state"])
        if result.get("game_over"):
            over=result["game_over"]
            log.info("[Host] Game %s finished: %s by %s",gid,over.get("result"),over.get("winner"))
            self._broadcast_game_over(over)
    def _handle_guess_result(self,c,m):
        if c.peer_id==self.player.host_id and not m.get("accepted"):
            log.warning("Palpite rejeitado: %s",m.get("reason"))
    def _handle_game_over(self,c,m):
        if (self.player.status in ("JOINED","PLAYING") and
                c.peer_id==self.player.host_id and m.get("game_id")==self.player.game_id):
            valid=self.player.verify_game_over(m)
            if valid is False:log.warning("[Player] host cheated: hash mismatch")
            elif valid is None:log.warning("[Player] word_hash unavailable; GAME_OVER could not be verified")
            else:log.info("[Player] GAME_OVER %s word=%s winner=%s — hash OK",m.get("result"),m.get("word"),m.get("winner"))
            self.player.leave_game()

    def status_text(self):
        if self.host.status in ("OPEN","RUNNING","FINISHED") and self.host.game:
            g=self.host
            remaining=max(0,int(g.turn_timeout-(time.monotonic()-g.last_activity))) if g.status=="RUNNING" else 0
            return (f"[Game] {g.game_id} (host: {self.peer_id}) status: {g.status}\\n"
                    f"[Game] Palavra: {g.game.get_hidden_word()} Vidas: {g.game.lives}/{g.game.max_lives} "
                    f"Erradas: {', '.join(sorted(g.game.wrong_letters))}\\n"
                    f"[Game] Jogadores: {', '.join(g.players)} Vez de: {g.get_current_player()} — {remaining}s restantes")
        p=self.player
        if p.status!="IDLE":
            wrong=(p.last_state or {}).get("wrong_letters",[])
            remaining=max(0,int(p.turn_timeout-(time.monotonic()-p.state_received_at))) if p.status=="PLAYING" else 0
            you=" (você)" if p.turn==p.peer_id else ""
            return (f"[Game] {p.game_id} (host: {p.host_id}) status: {p.status}\\n"
                    f"[Game] Palavra: {p.masked} Vidas: {p.lives}/{p.max_lives} Erradas: {', '.join(wrong)}\\n"
                    f"[Game] Jogadores: {', '.join(p.players)} Vez de: {p.turn}{you} — {remaining}s restantes")
        return "[Game] Nenhuma partida ativa."

    def stop(self):
        if not self.running:return
        # Required order: abort game / leave, BYE peers, then UNREGISTER.
        if self.host.status in ("OPEN","RUNNING"):
            over=self.host.cancel_game()
            if over:self._broadcast_game_over(over)
        if self.player.status in ("JOINED","PLAYING"):
            self.leave_game()
        bye_waiters=[]
        for pid,c in list(self.connections.items()):
            mid=str(uuid.uuid4());ev=threading.Event();self.pending[mid]=(ev,{})
            try:
                c.send_message({"type":"BYE","msg_id":mid,"src":self.peer_id,"dst":pid,"reason":"shutdown","ttl":1})
                bye_waiters.append(ev)
            except OSError:pass
        deadline=time.monotonic()+2
        for ev in bye_waiters:ev.wait(max(0,deadline-time.monotonic()))
        self.running=False;self.keep_alive.stop()
        for c in list(self.connections.values()):c.close()
        self.connections.clear()
        if self.server_socket:
            try:self.server_socket.close()
            except OSError:pass
        if self.registered:
            self.rendezvous.unregister(self.listen_port);self.registered=False
        log.info("Cliente P2P encerrado.")
