from __future__ import annotations

import getpass

from werkzeug.security import generate_password_hash


def main() -> None:
    password = getpass.getpass("Admin password: ")
    if len(password) < 12:
        raise SystemExit("Admin password must be at least 12 characters.")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match.")
    print(generate_password_hash(password))


if __name__ == "__main__":
    main()
