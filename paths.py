from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent

DATA_DIR = BASE_DIR / "data"
WORKSPACE_DIR = BASE_DIR / "workspace"

DATA_DIR.mkdir(exist_ok=True)
WORKSPACE_DIR.mkdir(exist_ok=True)

NOTES_FILE = DATA_DIR / "notes.txt"

DATABASE_FILE = DATA_DIR / "conversation_history.db"

CURRENT_SESSION_FILE = DATA_DIR / "current_session.txt"