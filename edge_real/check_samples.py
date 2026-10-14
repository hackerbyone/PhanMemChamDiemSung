"""Run the exact v2 warp -> detect -> compare pipeline; output DEMO only."""
import json
from pathlib import Path
import cv2
from core import Detector,read_image,write_image,analyze,overlay
from template_geometry import load_or_extract
ROOT=Path(__file__).resolve().parents[1]
def main():
    out=ROOT/'sample_check';out.mkdir(exist_ok=True)
    t=read_image(ROOT/'assets/Biaso4.png');a=read_image(ROOT/'assets/2.Ket_thuc_New68.bmp')
    config=load_or_extract(ROOT/'assets/Biaso4.png',ROOT/'target_config.json')
    model=Detector(ROOT/'assets/best.pt');result=analyze(t,a,t,config,model)
    result['status']='DEMO';result['warnings'].append('Reference image is not the actual before-shot frame. No verified score or shot count.')
    write_image(out/'automatic_geometry.png',overlay(t,config))
    write_image(out/'automatic_result_demo.png',overlay(t,config,result['detections']))
    warped=cv2.warpPerspective(a,__import__('numpy').array(result['homography_after']),(t.shape[1],t.shape[0]))
    write_image(out/'alignment_overlay.png',cv2.addWeighted(t,.5,warped,.5,0))
    (out/'automatic_pipeline.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print('DEMO candidates:',len(result['detections']),'output:',out)
if __name__=='__main__':main()
