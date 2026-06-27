from dataclasses import dataclass


@dataclass
class Config:
    server_url: str = ""
    png_path: str = ""
    fps: int = 30


def load_config(path: str) -> Config:
    c = Config()
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            key, val = key.strip(), val.strip()
            if key == "server_url":
                c.server_url = val
            elif key == "png_path":
                c.png_path = val
            elif key == "fps":
                try:
                    c.fps = int(val)
                except ValueError:
                    pass
    if c.fps <= 0:
        c.fps = 30
    return c
