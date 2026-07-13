import threading

import uvicorn
import webview

from app.config import settings


def _start_server():
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    server = threading.Thread(target=_start_server, daemon=True)
    server.start()
    base_url = f"http://{settings.host}:{settings.port}"
    webview.create_window(settings.app_name, base_url, width=1920, height=1080, resizable=False, fullscreen=True)  # окно кассы
    # Второе окно — экран покупателя (этап 2.3). Открывается только по флагу конфига:
    # на одной машине с одним монитором fullscreen перекрыл бы кассу и мешал разработке.
    if settings.customer_display_window:
        webview.create_window("Покупатель", f"{base_url}/customer", width=1920, height=1200, fullscreen=True)
    webview.start()
