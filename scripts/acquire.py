from pathlib import Path
import argparse
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from jevmesh.data import download_market, prepare_market, download_news

parser = argparse.ArgumentParser()
parser.add_argument("kind", choices=["market", "news", "prepare"])
parser.add_argument("--end", default="2026-09-22")
args = parser.parse_args()
if args.kind == "market":
    download_market(args.end)
    prepare_market()
elif args.kind == "news":
    download_news()
else:
    prepare_market()
