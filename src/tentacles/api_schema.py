"""Export API types without opening a database or reading user configuration."""
import json

from tentacles.config import AppConfig
from tentacles.server import create_app


def main() -> None:
    print(json.dumps(create_app(AppConfig()).openapi(), ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
