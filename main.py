import os
import sys
import logging
import threading
import webview
from app.backend.api_bridge import ApiBridge

# Setup logging
log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "servdock.log")
logging.basicConfig(
    filename=log_path,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("ServDock")

def handle_uncaught_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logger.critical("Uncaught Exception occurred:", exc_info=(exc_type, exc_value, exc_traceback))

sys.excepthook = handle_uncaught_exception
if hasattr(threading, 'excepthook'):
    threading.excepthook = lambda args: handle_uncaught_exception(args.exc_type, args.exc_value, args.exc_traceback)

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    html_path = os.path.join(base_dir, "app", "ui", "index.html")

    if not os.path.exists(html_path):
        logger.error(f"UI file not found at {html_path}")
        print(f"Error: UI file not found at {html_path}")
        sys.exit(1)

    api = ApiBridge()

    window = webview.create_window(
        title="ServDock - Linux, Docker & Remote Workstation",
        url=f"file:///{html_path.replace(os.sep, '/')}",
        js_api=api,
        width=1280,
        height=840,
        min_size=(1040, 680),
        background_color="#080c14"
    )

    api.set_window(window)

    def on_closed():
        try:
            api.disconnect_server()
        except Exception:
            pass

    window.events.closed += on_closed

    try:
        webview.start(debug=False)
    except Exception as e:
        logger.critical(f"Webview runtime error: {e}", exc_info=True)

if __name__ == "__main__":
    main()
