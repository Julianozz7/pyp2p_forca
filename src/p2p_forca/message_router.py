class MessageRouter:
    """Recebe mensagens e encaminha para os handlers correspondentes."""

    def __init__(self):
        self.handlers = {}
        self.processed_messages = set()

    def register_handler(self, message_type, handler):
        """Registra uma função para processar determinado tipo de mensagem."""
        if not callable(handler):
            raise TypeError("O handler precisa ser uma função.")

        self.handlers[message_type] = handler

    def unregister_handler(self, message_type):
        """Remove um handler registrado."""
        return self.handlers.pop(message_type, None)

    def route(self, connection, message):
        """Identifica e encaminha uma mensagem recebida."""

        if not isinstance(message, dict):
            return False

        message_type = message.get("type")

        if not message_type:
            return False

        msg_id = message.get("msg_id")

        if msg_id:
            if msg_id in self.processed_messages:
                return False

            self.processed_messages.add(msg_id)

        handler = self.handlers.get(message_type)

        if handler is None:
            return False

        handler(connection, message)

        return True
