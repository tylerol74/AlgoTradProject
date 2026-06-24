"""Command-line entry point for AlgoTradProject."""

import argparse
import logging

from database.schema import initialize_database


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AlgoTradProject command-line tools")
    parser.add_argument(
        "command",
        nargs="?",
        default="init-db",
        choices=("init-db",),
        help="Command to run. Defaults to init-db.",
    )
    return parser.parse_args()


def main() -> None:
    configure_logging()
    args = parse_args()

    if args.command == "init-db":
        initialize_database()


if __name__ == "__main__":
    main()
