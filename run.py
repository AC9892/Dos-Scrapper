from __future__ import annotations

import sys


def main() -> int:
    try:
        from dos_app.app.main import main as app_main
    except ModuleNotFoundError as exc:
        missing = exc.name or "a required package"
        print(f"Missing dependency: {missing}", file=sys.stderr)
        print("Install dependencies with:", file=sys.stderr)
        print("  python -m pip install -r requirements.txt", file=sys.stderr)
        return 1
    return int(app_main() or 0)


if __name__ == "__main__":
    raise SystemExit(main())
