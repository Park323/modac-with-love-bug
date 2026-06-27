from manager.config import load_config


def test_parses_key_value(tmp_path):
    p = tmp_path / "qa.ini"
    p.write_text("server_url=ws://127.0.0.1:9000\npng_path=fixed.png\nfps=30\n")
    c = load_config(str(p))
    assert c.server_url == "ws://127.0.0.1:9000"
    assert c.png_path == "fixed.png"
    assert c.fps == 30


def test_missing_fps_defaults_to_30(tmp_path):
    p = tmp_path / "qa.ini"
    p.write_text("server_url=ws://x\npng_path=p.png\n")
    c = load_config(str(p))
    assert c.fps == 30
