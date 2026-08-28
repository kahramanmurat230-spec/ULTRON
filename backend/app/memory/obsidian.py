from pathlib import Path
from datetime import datetime

class Vault:
    def __init__(self, root="data/vault"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def write_note(self, title, content, folder="outputs"):
        folder_path = self.root / folder
        folder_path.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in title).strip()
        path = folder_path / f"{safe or 'note'}.md"
        path.write_text(
            f"# {title}\n\n{content}\n\n"
            f"_Created: {datetime.now().isoformat(timespec='seconds')}_\n",
            encoding="utf-8"
        )
        return path
