"""Export a detector reference for the same decoded image used by the native benchmark."""
import argparse
import json
from pathlib import Path
import onnxruntime as ort
from convert_model import preprocess,decode

parser=argparse.ArgumentParser()
parser.add_argument('image',type=Path)
parser.add_argument('--model',type=Path,default=Path('/project/models/yolov8n/model.onnx'))
parser.add_argument('--output',type=Path,default=Path('/project/data/validation/native-reference.json'))
args=parser.parse_args()
tensor,geometry=preprocess(args.image)
session=ort.InferenceSession(str(args.model),providers=['CPUExecutionProvider'])
detections=decode(session.run(None,{'images':tensor}),geometry,.35)
result={'image':str(args.image),'geometry':geometry,'detections':detections}
args.output.parent.mkdir(parents=True,exist_ok=True)
args.output.write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
