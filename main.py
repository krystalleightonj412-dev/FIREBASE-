"""Production entrypoint for Railway, Render, Docker, and local execution."""
from pathlib import Path
import runpy


if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).with_name("bot.py")), run_name="__main__")

