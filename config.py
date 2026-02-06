import os

# Telegram Bot Token from @BotFather
BOT_TOKEN = os.getenv("BOT_TOKEN", "7916887113:AAHT8O_nw25UouvI-Hy5n05yDVg_z-B4c9E")

# Download path for temporary files
DOWNLOAD_PATH = os.path.join(os.path.dirname(__file__), "downloads")

# Create downloads folder if not exists
os.makedirs(DOWNLOAD_PATH, exist_ok=True)
