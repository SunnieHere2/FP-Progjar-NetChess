import socket
import json

class Network:
    def __init__(self, host="127.0.0.1", port=5555):
        self.client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.client.connect((host, port))
        self.buffer = ""

    def send(self, data):
        self.client.send(json.dumps(data).encode())

    def receive(self):
        while True:
            try:
                idx = self.buffer.index("}{")
                msg = self.buffer[:idx + 1]
                self.buffer = self.buffer[idx + 1:]
                return json.loads(msg)
            except ValueError:
                pass
            try:
                data = json.loads(self.buffer)
                self.buffer = ""
                return data
            except json.JSONDecodeError:
                pass
            chunk = self.client.recv(4096).decode()
            if not chunk:
                return None
            self.buffer += chunk