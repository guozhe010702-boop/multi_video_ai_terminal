"""Compare quantized RKNN simulator detections with ONNX on held-out COCO images."""
import argparse
import json
from pathlib import Path
import numpy as np
import onnxruntime as ort
from rknn.api import RKNN
from convert_model import preprocess, decode


def overlap(a, b):
    a, b = np.asarray(a), np.asarray(b)
    wh = np.maximum(0, np.minimum(a[2:], b[2:]) - np.maximum(a[:2], b[:2]))
    intersection = float(wh.prod())
    union = float(np.maximum(0, a[2:] - a[:2]).prod() + np.maximum(0, b[2:] - b[:2]).prod() - intersection)
    return intersection / max(union, 1e-9)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--work', type=Path, default=Path('/project/.deps/model-work'))
    parser.add_argument('--output', type=Path, default=Path('/project/data/validation/quantization.json'))
    args = parser.parse_args()
    bundle = args.work / 'bundle/coco80'
    reference = json.loads((bundle / 'reference.json').read_text())
    session = ort.InferenceSession(str(bundle / 'model.onnx'), providers=['CPUExecutionProvider'])
    model = RKNN(verbose=False)
    try:
        assert model.config(mean_values=[[0,0,0]], std_values=[[255,255,255]], target_platform='rk3588', optimization_level=3) in (None,0)
        assert model.load_onnx(model=str(bundle / 'model.onnx')) == 0
        assert model.build(do_quantization=True, dataset=str(bundle / 'calibration.txt')) == 0
        assert model.init_runtime() == 0
        records = []
        for row in reference:
            tensor, geometry = preprocess(args.work / row['file'])
            expected = decode(session.run(None, {'images':tensor}), geometry, .35)
            image = np.rint(tensor[0].transpose(1,2,0) * 255).astype(np.uint8)
            outputs = model.inference(inputs=[image[None]], data_format=['nhwc'])
            actual = decode(outputs, geometry, .35)
            used = set()
            matches = []
            for expected_box in expected:
                candidates = [(overlap(expected_box['box'],box['box']),i) for i,box in enumerate(actual)
                              if i not in used and box['class_id'] == expected_box['class_id']]
                if candidates:
                    iou,index = max(candidates)
                    if iou >= .5:
                        used.add(index)
                        matches.append(iou)
            records.append({'file':row['file'],'reference_count':len(expected),'quantized_count':len(actual),
                            'matched_count':len(matches),'matched_ious':matches,'reference':expected,'quantized':actual})
        expected_total = sum(row['reference_count'] for row in records)
        actual_total = sum(row['quantized_count'] for row in records)
        matches = [iou for row in records for iou in row['matched_ious']]
        report = {'kind':'RKNN simulator INT8 versus ONNX FP32','images':len(records),
                  'reference_count':expected_total,'quantized_count':actual_total,'matched_count':len(matches),
                  'reference_match_ratio':len(matches)/max(expected_total,1),
                  'quantized_match_ratio':len(matches)/max(actual_total,1),
                  'mean_matched_iou':float(np.mean(matches)) if matches else None,
                  'iou_threshold':.5,'confidence':.35,'records':records}
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2))
        print(json.dumps({k:v for k,v in report.items() if k!='records'},indent=2))
    finally:
        model.release()


if __name__ == '__main__':
    main()
