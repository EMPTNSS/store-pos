import threading

import uvicorn
import webview

from app.config import settings


def _start_server():
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    server = threading.Thread(target=_start_server, daemon=True)
    server.start()
    webview.create_window(
        settings.app_name,
        f"http://{settings.host}:{settings.port}",
    )
    webview.start()
