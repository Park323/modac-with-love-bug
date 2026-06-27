import threading

from websockets.sync.server import serve

from manager.frame import Frame
from manager.serializer import serialize
from manager.transport import WebSocketTransport


def test_send_transmits_serialized_binary_frame():
    received = []

    def handler(ws):
        for message in ws:
            received.append(message)
            ws.send(message)

    server = serve(handler, "127.0.0.1", 0)
    port = server.socket.getsockname()[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    try:
        tx = WebSocketTransport()
        assert tx.connect(f"ws://127.0.0.1:{port}") is True

        f = Frame(timestamp_ms=0x1122334455667788, png=bytes([0xDE, 0xAD, 0xBE, 0xEF]))
        assert tx.send(f) is True

        import time
        deadline = time.time() + 2.0
        while not received and time.time() < deadline:
            time.sleep(0.01)

        assert received, "server received nothing"
        assert received[0] == serialize(f)

        tx.close()
    finally:
        server.shutdown()


def test_send_without_connect_returns_false():
    tx = WebSocketTransport()
    f = Frame(timestamp_ms=1, png=b"\x00")
    assert tx.send(f) is False
