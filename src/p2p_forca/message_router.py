"""Validated peer message dispatcher."""
class MessageRouter:
    CONNECTION_TYPES = {"HELLO", "HELLO_OK", "PING", "PONG"}
    def __init__(self, local_peer_id=None):
        self.handlers = {}
        self.processed_messages = set()
        self.local_peer_id = local_peer_id

    def register_handler(self, message_type, handler):
        if not callable(handler): raise TypeError("O handler precisa ser uma função.")
        self.handlers[message_type] = handler

    def unregister_handler(self, message_type):
        return self.handlers.pop(message_type, None)

    def route(self, connection, message):
        if not isinstance(message, dict) or not isinstance(message.get("type"), str):
            return False
        typ = message["type"]
        if message.get("ttl") != 1:
            return False
        msg_id=message.get("msg_id")
        if not isinstance(msg_id,str) or not msg_id:
            self._error(connection,message,"bad_request")
            return False
        if typ in ("PING","PONG") and not isinstance(message.get("timestamp"),str):
            self._error(connection,message,"bad_request")
            return False
        if typ not in self.CONNECTION_TYPES:
            if not isinstance(message.get("src"),str) or not isinstance(message.get("dst"),str):
                self._error(connection,message,"bad_request")
                return False
            if connection.peer_id and message["src"] != connection.peer_id:
                self._error(connection,message,"bad_request")
                return False
            if self.local_peer_id and message["dst"] != self.local_peer_id:
                self._error(connection, message, "not_for_me")
                return False
        if typ in ("HELLO","HELLO_OK"):
            if not isinstance(message.get("peer_id"),str) or not isinstance(message.get("version"),str):
                self._error(connection,message,"bad_request")
                return False
            if not isinstance(message.get("features"),list):
                self._error(connection,message,"bad_request")
                return False
        key=(id(connection),msg_id)
        if key in self.processed_messages:
            return False
        self.processed_messages.add(key)
        if len(self.processed_messages)>10000:self.processed_messages.clear()
        handler=self.handlers.get(typ)
        if handler is None:
            if typ!="ERROR":self._error(connection,message,"unknown_type")
            return False
        handler(connection,message)
        return True

    def _error(self,conn,msg,code):
        try:
            response={"type":"ERROR","msg_id":__import__("uuid").uuid4().hex,
                      "code":code,"message":code,"ttl":1}
            if msg.get("msg_id"):response["ref_msg_id"]=msg["msg_id"]
            local=getattr(conn,"local_peer_id",None)
            remote=getattr(conn,"peer_id",None) or msg.get("src")
            if local:response["src"]=local
            if remote:response["dst"]=remote
            conn.send_message(response)
        except (OSError,AttributeError):pass
