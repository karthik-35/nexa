import subprocess

import os

ALLOWED_APPS = {
    "chrome": "Google Chrome",
    "safari": "Safari",
    "vscode": "Visual Studio Code",
    "spotify": "Spotify",
    "finder": "Finder",
    "zoom": "zoom.us",
    "photos": "Photos",
    "teams": "Microsoft Teams",
    "whatsapp": "WhatsApp",
}

def open_path(path: str):
    """
    Opens a local file or folder on macOS.

    Args:
        path: File or folder path, such as ~/Downloads.
    """
    path = os.path.expanduser(path.strip())

    if not os.path.exists(path):
        return {
            "success": False,
            "message": "File or folder not found."
        }

    subprocess.run(["open", path], check=True)

    return {
        "success": True,
        "message": f"Opened {path}."
    }


def open_app(app_name: str):
    """
    Opens an approved macOS application.

    Args:
        app_name: Name of the application to open.
    """
    app_name = app_name.lower().strip()

    if app_name not in ALLOWED_APPS:
        return {
            "success": False,
            "message": f"App '{app_name}' is not allowed."
        }

    actual_name = ALLOWED_APPS[app_name]

    subprocess.run(
        ["open", "-a", actual_name],
        check=True
    )

    return {
        "success": True,
        "message": f"Opened {actual_name}."
    }


def open_website(url: str):
    """
    Opens a website in the default browser.

    Args:
        url: Website address, such as youtube.com.
    """
    url = url.strip()

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    subprocess.run(
        ["open", url],
        check=True
    )

    return {
        "success": True,
        "message": f"Opened {url}."
    }