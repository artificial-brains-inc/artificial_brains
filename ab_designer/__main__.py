"""`python -m ab_designer` opens the standalone visual designer."""
import argparse
from .model import Brain
from .server import serve


def main():
    parser = argparse.ArgumentParser(prog="ab-designer", description="Artificial Brains visual and Python designer (standalone)")
    parser.add_argument("design", nargs="?", help="Optional .abdesign.json project to open")
    parser.add_argument("--port", type=int, default=0, help="Local port (default: choose free port)")
    parser.add_argument("--no-browser", action="store_true", help="Print URL without opening a browser")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("Port must be between 0 and 65535")
    brain = Brain.load(args.design) if args.design else Brain("Untitled Brain")
    serve(brain, port=args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
