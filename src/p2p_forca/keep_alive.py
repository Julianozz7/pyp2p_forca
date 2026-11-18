import threading, time, uuid, logging
from datetime import datetime, timezone


class KeepAlive:
    def __init__(self, peer_table, ping_interval=30, miss_limit=3, on_stale=None):
        self.peer_table,self.ping_interval,self.miss_limit=peer_table,ping_interval,miss_limit
        self.on_stale=on_stale; self.running=False; self.pending_pings={}; self.consecutive_misses={}; self.lock=threading.RLock()
        self.thread=None

    def send_ping(self, connection):
        if not connection.peer_id:return
        pid=str(uuid.uuid4()); now=time.monotonic()
        stamp=datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
        with self.lock:self.pending_pings.setdefault(connection.peer_id,{})[pid]=(now,stamp)
        connection.send_message({"type":"PING","msg_id":pid,"timestamp":stamp})

    def receive_pong(self, peer_id, message):
        with self.lock:
            pending=self.pending_pings.get(peer_id,{})
            if isinstance(pending, list):
                ping_id=message.get("msg_id")
                if ping_id not in pending:return False
                pending.remove(ping_id)
                if not pending:self.pending_pings.pop(peer_id,None)
                item=None
            else:
                item=pending.pop(message.get("msg_id"),None)
                if not item:return False
                if not pending:self.pending_pings.pop(peer_id,None)
            self.consecutive_misses[peer_id]=0
        if item is not None:
            rtt=time.monotonic()-item[0]
            try:
                self.peer_table.set_rtt(peer_id,rtt)
                peer=self.peer_table.get_peer(peer_id)
                avg=peer.get("rtt") if isinstance(peer,dict) else rtt
                if not isinstance(avg,(int,float)):avg=rtt
                logging.getLogger(__name__).info("[KeepAlive] %s RTT=%.1f ms (avg %.1f ms)",peer_id,rtt*1000,avg*1000)
            except (AttributeError,TypeError):pass
        return True

    def reset_peer(self,peer_id):
        with self.lock:
            self.pending_pings.pop(peer_id,None)
            self.consecutive_misses.pop(peer_id,None)

    def check_peers(self):
        stale=[]
        now=time.monotonic()
        with self.lock:
            for peer_id,pending in list(self.pending_pings.items()):
                # Compatibility with older callers/tests that inject a list of ping IDs.
                if isinstance(pending,list):
                    if len(pending)>=self.miss_limit:
                        stale.append(peer_id);self.pending_pings.pop(peer_id,None)
                    continue
                expired=[pid for pid,(sent_at,_stamp) in pending.items()
                         if now-sent_at>=self.ping_interval]
                for pid in expired:
                    pending.pop(pid,None)
                    self.consecutive_misses[peer_id]=self.consecutive_misses.get(peer_id,0)+1
                if not pending:self.pending_pings.pop(peer_id,None)
                if self.consecutive_misses.get(peer_id,0)>=self.miss_limit:
                    stale.append(peer_id)
                    self.pending_pings.pop(peer_id,None)
                    self.consecutive_misses.pop(peer_id,None)
        for peer_id in stale:
            self.peer_table.update_status(peer_id,"STALE")
            try:
                p=self.peer_table.get_peer(peer_id)
                conn=p.get("connection") if isinstance(p,dict) else None
                if conn:conn.close()
            except (AttributeError,TypeError):pass
            if self.on_stale:self.on_stale(peer_id)

    def start(self,get_connections):
        if self.running:return
        self.running=True
        def loop():
            while self.running:
                for conn in get_connections():
                    try:self.send_ping(conn)
                    except OSError:pass
                self.check_peers()
                for _ in range(max(1,int(self.ping_interval*10))):
                    if not self.running:break
                    time.sleep(.1)
        self.thread=threading.Thread(target=loop,daemon=True);self.thread.start()
    def stop(self):
        self.running=False
