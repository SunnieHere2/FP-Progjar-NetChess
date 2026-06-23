import socket
import threading
import json

HOST = "0.0.0.0"
PORT = 5555
START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind((HOST, PORT))
server.listen()

rooms = {}
rooms_lock = threading.Lock()

print(f"Server running on {HOST}:{PORT}")


def send_json(client, data):
    client.send(json.dumps(data).encode())


def broadcast_room_list(exclude=None):
    with rooms_lock:
        room_list = [
            {
                "id": rid,
                "name": r["name"],
                "players": len(r["clients"]),
                "spectators": len(r["spectators"]),
                "ongoing": r["started"]
            }
            for rid, r in rooms.items()
        ]

    msg = json.dumps({
        "type": "room_list",
        "rooms": room_list
    }).encode()

    with rooms_lock:
        all_clients = []

        for r in rooms.values():
            all_clients.extend(r["clients"])
            all_clients.extend(r["spectators"])

    for c in all_clients:
        if c != exclude:
            try:
                c.send(msg)
            except:
                pass

    if exclude:
        try:
            exclude.send(msg)
        except:
            pass


def handle_client(client):
    room_id = None

    while True:
        try:
            raw = b""
            while True:
                chunk = client.recv(4096)
                if not chunk:
                    raise ConnectionError
                raw += chunk
                try:
                    data = json.loads(raw.decode())
                    break
                except json.JSONDecodeError:
                    continue

            if data["type"] == "get_rooms":
                with rooms_lock:
                    room_list = [
                        {
                            "id": rid,
                            "name": r["name"],
                            "players": len(r["clients"]),
                            "spectators": len(r["spectators"]),
                            "ongoing": r["started"]
                        }
                        for rid, r in rooms.items()
                    ]
                send_json(client, {"type": "room_list", "rooms": room_list})

            elif data["type"] == "create_room":
                with rooms_lock:
                    rid = data["room_id"]
                    rooms[rid] = {
                        "name": data["name"],
                        "clients": [client],
                        "spectators": [],
                        "host": client,
                        "started": False,
                        "fen": None
                    }
                room_id = rid
                send_json(client, {"type": "color", "color": "white"})
                send_json(client, {"type": "waiting"})
                broadcast_room_list()

            elif data["type"] == "join_room":
                rid = data["room_id"]
                with rooms_lock:
                    room = rooms.get(rid)
                    if room and len(room["clients"]) < 2:
                        room["clients"].append(client)
                        room_id = rid
                        joined = True
                    else:
                        joined = False

                if joined:
                    rooms[rid]["started"] = True
                    rooms[rid]["fen"] = START_FEN
                    send_json(client, {"type": "color", "color": "black"})
                    host = rooms[rid]["host"]
                    send_json(host, {"type": "start"})
                    send_json(client, {"type": "start"})
                    broadcast_room_list()

            elif data["type"] == "spectate":
                    print("SPECTATE REQUEST", data["room_id"])
                    rid = data["room_id"]

                    with rooms_lock:
                        room = rooms.get(rid)
                        print("ROOM =", room)
                        if room:
                            print("STARTED =", room["started"])

                        if room and room["started"]:
                            room["spectators"].append(client)

                            room_id = rid

                            send_json(client, {
                                "type": "spectator_start",
                                "fen": room["fen"] or START_FEN
                            })

                            print("SENT spectator_start")

                        else:

                            send_json(client, {
                                "type": "error",
                                "message": "Match not available"
                            })

            elif data["type"] == "move":
                if room_id:

                    with rooms_lock:
                        room = rooms.get(room_id)

                    if room:

                        room["fen"] = data.get("fen")

                        for c in room["clients"]:
                            if c != client:
                                send_json(c, data)

                        for s in room["spectators"]:
                            send_json(s, data)

            elif data["type"] == "chat":

                if room_id:

                    with rooms_lock:
                        room = rooms.get(room_id)

                    if room:

                        recipients = (
                            room["clients"]
                            + room["spectators"]
                        )

                        for c in recipients:
                            if c != client:
                                send_json(c, data)

        except Exception as e:
            print("Client error:", e)
            break

    if room_id:
        with rooms_lock:
            room = rooms.get(room_id)

            if room:

                was_player = client in room["clients"]

                room["clients"] = [
                    c for c in room["clients"]
                    if c != client
                ]

                room["spectators"] = [
                    s for s in room["spectators"]
                    if s != client
                ]

                if not room["clients"] and not room["spectators"]:
                    del rooms[room_id]

                elif was_player:
                    for c in room["clients"]:
                        try:
                            send_json(c, {
                                "type": "opponent_left"
                            })
                        except:
                            pass

        broadcast_room_list()

    client.close()


while True:
    client, addr = server.accept()
    print("Connected:", addr)
    threading.Thread(target=handle_client, args=(client,), daemon=True).start()
