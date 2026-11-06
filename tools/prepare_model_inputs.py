"""Copy the existing checkpoint and COCO128 assets into an isolated converter workspace."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

root = Path(__file__).resolve().parents[1]
edge = root.parent / 'edgevision -yolov8'
parser = argparse.ArgumentParser()
parser.add_argument('--checkpoint',type=Path,default=edge/'训练/ultralytics_yolov8-main/yolov8n.pt')
parser.add_argument('--coco128',type=Path,default=edge/'.cache/indoor-upgrade/datasets/coco128')
parser.add_argument('--labels',type=Path,default=edge/'.cache/indoor-upgrade/source/coco80.txt')
args = parser.parse_args()
if len(list((args.coco128/'images/train2017').glob('*.jpg'))) < 128:
    raise SystemExit('COCO128 images are incomplete')
labels=args.labels.read_text().splitlines()
if len(labels)!=80 or labels[0]!='person': raise SystemExit('Expected COCO80 labels')
work=root/'.deps/model-work'
(work/'source').mkdir(parents=True,exist_ok=True)
shutil.copy2(args.checkpoint,work/'source/yolov8n.pt')
shutil.copy2(args.labels,work/'source/coco80.txt')
shutil.copytree(args.coco128,work/'datasets/coco128',dirs_exist_ok=True)
(work/'inputs.json').write_text(json.dumps({'checkpoint':str(args.checkpoint.resolve()),
    'checkpoint_sha256':hashlib.sha256(args.checkpoint.read_bytes()).hexdigest(),
    'coco128':str(args.coco128.resolve()),'images':128},indent=2))
print('Model conversion inputs prepared in',work)
