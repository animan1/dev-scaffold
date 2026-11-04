"""Print the scaffold's profile selection contract without touching services."""

import argparse
import json

APPLICATIONS = ("react-vite", "server-rendered-django")
BACKUPS = ("none", "immutable-backup")


def metadata(application, backup, applications, backups):
    if application not in APPLICATIONS or backup not in BACKUPS:
        raise ValueError("unknown selected scaffold profile")
    if not applications or any(name not in APPLICATIONS for name in applications):
        raise ValueError("unknown or empty application validation matrix")
    if any(name not in BACKUPS for name in backups):
        raise ValueError("unknown backup validation profile")
    if application not in applications or (backup != "none" and backup not in backups):
        raise ValueError("selected profiles must be included in the validation matrix")
    return {
        "selected": {"application": application, "backup": backup},
        "validation": {"application": applications, "backup": backups},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application", required=True)
    parser.add_argument("--backup", required=True)
    parser.add_argument("--applications", nargs="+", required=True)
    parser.add_argument("--backups", nargs="*", default=[])
    options = parser.parse_args()
    try:
        result = metadata(
            options.application, options.backup, options.applications, options.backups
        )
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
