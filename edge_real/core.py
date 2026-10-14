"""Geometry in template pixels. No invented mm calibration."""
import hashlib
import json
from pathlib import Path
import cv2
import numpy as np
from scipy.optimize import linear_sum_assignment


def read_image(path):
    im = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
    if im is None:
        raise ValueError(f'Không đọc được ảnh: {path}')
    return im


def write_image(path, im):
    ok, data = cv2.imencode('.png', im)
    if not ok:
        raise ValueError('Không mã hóa được ảnh')
    data.tofile(str(path))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_config(c):
    pts = np.asarray(c['outline'], np.float32)
    if pts.ndim != 2 or pts.shape[1] != 2 or len(pts) < 3 or not np.isfinite(pts).all():
        raise ValueError('Đường bao cần ít nhất 3 điểm hợp lệ')
    if not cv2.isContourConvex(cv2.convexHull(pts)) or abs(cv2.contourArea(pts)) < 100:
        raise ValueError('Đường bao quá nhỏ')
    radii = [float(c['radii'][str(s)]) for s in range(10, 5, -1)]
    if not np.isfinite(radii).all() or radii[0] <= 0 or any(a >= b for a,b in zip(radii,radii[1:])):
        raise ValueError('Bán kính phải tăng dần từ vòng 10 đến vòng 6')
    center = np.asarray(c['center'], float)
    if center.shape != (2,) or not np.isfinite(center).all():
        raise ValueError('Tâm bia không hợp lệ')
    if cv2.pointPolygonTest(pts, tuple(center), False) < 0:
        raise ValueError('Tâm bia phải trong đường bao')


def score_point(point, c):
    x,y = map(float, point)
    if not np.isfinite([x,y]).all():
        raise ValueError('Tọa độ không hợp lệ')
    # Boundary belongs to silhouette; ring boundary takes the higher score.
    if cv2.pointPolygonTest(np.array(c['outline'],np.float32),(x,y),False) < 0:
        return 0
    d = np.linalg.norm(np.array([x,y])-c['center'])
    for s in range(10,5,-1):
        if d <= c['radii'][str(s)] + 1e-7:
            return s
    return 5


def transform(points, H):
    if not len(points):
        return []
    a = cv2.perspectiveTransform(np.array(points,np.float64).reshape(-1,1,2), H).reshape(-1,2)
    if not np.isfinite(a).all():
        raise ValueError('Ánh xạ không hợp lệ')
    return a.tolist()


def align(source, template, pairs=None):
    if pairs is not None:
        p,q = [np.array(v,np.float32) for v in pairs]
        if len(p) < 4 or len(p) != len(q):
            raise ValueError('Cần ít nhất 4 cặp điểm')
        H, mask = cv2.findHomography(p,q,cv2.RANSAC,4.0)
    else:
        sift = cv2.SIFT_create(nfeatures=5000)
        k1,d1 = sift.detectAndCompute(cv2.cvtColor(source,cv2.COLOR_BGR2GRAY),None)
        k2,d2 = sift.detectAndCompute(cv2.cvtColor(template,cv2.COLOR_BGR2GRAY),None)
        if d1 is None or d2 is None:
            raise ValueError('Không đủ đặc trưng. Dùng căn chỉnh thủ công.')
        matches = cv2.BFMatcher().knnMatch(d1,d2,k=2)
        good = [m[0] for m in matches if len(m)==2 and m[0].distance < .72*m[1].distance]
        if len(good)<12:
            raise ValueError('Ít điểm tương ứng. Dùng căn chỉnh thủ công.')
        p=np.float32([k1[m.queryIdx].pt for m in good]); q=np.float32([k2[m.trainIdx].pt for m in good])
        H,mask=cv2.findHomography(p,q,cv2.RANSAC,3.0)
    if H is None or mask is None or not np.isfinite(H).all() or abs(np.linalg.det(H))<1e-10:
        raise ValueError('Không tìm được homography hợp lệ')
    use=mask.ravel().astype(bool)
    if use.sum()<4 or (pairs is None and (use.sum()<10 or use.mean()<.35)):
        raise ValueError('Homography thiếu tin cậy. Cần căn chỉnh lại.')
    projected=np.array(transform(p[use],H))
    error=float(np.sqrt(np.mean(np.sum((projected-q[use])**2,axis=1))))
    if error>4:
        raise ValueError('Sai số căn chỉnh quá lớn')
    th,tw=template.shape[:2]
    coverage=abs(cv2.contourArea(cv2.convexHull(q[use])))/(tw*th)
    if coverage<.10:
        raise ValueError('Điểm tương ứng quá tập trung. Chọn điểm trải đều quanh bia.')
    corners=np.float32([[0,0],[source.shape[1]-1,0],[source.shape[1]-1,source.shape[0]-1],[0,source.shape[0]-1]])
    warped=np.array(transform(corners,H),np.float32)
    if not cv2.isContourConvex(warped) or abs(cv2.contourArea(warped))<.1*tw*th:
        raise ValueError('Phép chiếu bị suy biến')
    return H, {'inliers':int(use.sum()),'matches':len(p),'rmse_px':error,'coverage':coverage,'method':'manual' if pairs else 'SIFT/RANSAC'}


def match_holes(before, after, radius):
    """Gated one-to-one assignment with dummy columns for unmatched detections."""
    if not after:
        return [], list(range(len(before)))
    if not before:
        return [None]*len(after), []
    a=np.array(after); b=np.array(before)
    d=np.linalg.norm(a[:,None,:]-b[None,:,:],axis=2)
    costs=np.concatenate([np.where(d<=radius,d,1e9),np.full((len(a),len(a)),radius+1)],axis=1)
    rows,cols=linear_sum_assignment(costs)
    matches=[None]*len(after); used=set()
    for r,col in zip(rows,cols):
        if col<len(b) and d[r,col]<=radius:
            matches[r]=int(col); used.add(int(col))
    return matches,[i for i in range(len(b)) if i not in used]


class Detector:
    def __init__(self,path):
        from ultralytics import YOLO
        self.model=YOLO(str(path))
    def detect(self, image, conf=.25):
        r=self.model.predict(image,device='cpu',imgsz=640,conf=conf,verbose=False)[0]
        return [{'box':b[:4].tolist(),'point':[(float(b[0])+float(b[2]))/2,(float(b[1])+float(b[3]))/2],
                 'confidence':float(b[4]),'class_id':int(b[5])} for b in r.boxes.data.cpu().numpy()]


def analyze(before,after,template,config,detector,confidence=.25,radius=12,pairs=None):
    """Warp both images first, then run YOLO in the common template coordinates."""
    validate_config(config)
    hb,qb=align(before,template,None if pairs is None else pairs[0])
    ha,qa=align(after,template,None if pairs is None else pairs[1])
    height,width=template.shape[:2]
    aligned=[];valid=[]
    for im,H in [(before,hb),(after,ha)]:
        aligned.append(cv2.warpPerspective(im,H,(width,height),flags=cv2.INTER_LINEAR))
        m=cv2.warpPerspective(np.full(im.shape[:2],255,np.uint8),H,(width,height),flags=cv2.INTER_NEAREST)
        valid.append(m)
    target_mask=np.zeros((height,width),np.uint8)
    cv2.fillPoly(target_mask,[np.array(config['outline'],np.int32)],255)
    required=target_mask>0
    for m in valid:
        if np.mean(m[required]>0)<.985:
            raise ValueError('Camera không bao phủ đủ hình người. Chụp lại toàn bộ bia.')
    old=detector.detect(aligned[0],confidence);new=detector.detect(aligned[1],confidence)
    # Detection centers are now already in template coordinates.
    op=[h['point'] for h in old];np_=[h['point'] for h in new]
    matches,missing=match_holes(op,np_,radius)
    warnings=[]
    tol=max(2.,qb['rmse_px']+qa['rmse_px'])
    for i,h in enumerate(new):
        point=np_[i];x,y=point;issues=[]
        h.update(template_point=point,matched_before=matches[i],selected=matches[i] is None,
                 score=score_point(point,config),source='model')
        h['original_point']=transform([point],np.linalg.inv(ha))[0]
        if matches[i] is None:
            if h['confidence']<.65:issues.append('Độ tin cậy nhận diện thấp')
            px,py=int(round(x)),int(round(y))
            if not (0<=px<width and 0<=py<height and valid[0][py,px] and valid[1][py,px]):issues.append('Ngoài vùng chung của hai ảnh')
            if min(x,y,width-1-x,height-1-y)<12:issues.append('Sát mép ảnh; có thể là vết rách')
            d=float(np.linalg.norm(np.array(point)-config['center']))
            edge=abs(cv2.pointPolygonTest(np.array(config['outline'],np.float32),(float(x),float(y)),True))
            if edge<=tol or (h['score']>0 and min(abs(d-r) for r in config['radii'].values())<=tol):issues.append('Gần ranh giới điểm')
            if op and min(np.linalg.norm(np.array(point)-p) for p in op)<=radius*1.8:issues.append('Gần lỗ cũ; cần kiểm tra ghép lỗ')
        h['issues']=issues
        if issues:warnings.append(f'Lỗ {i+1}: '+ '; '.join(issues))
    for i,h in enumerate(old):
        h['template_point']=op[i];h['original_point']=transform([op[i]],np.linalg.inv(hb))[0]
    if missing:warnings.append(f'{len(missing)} lỗ trước không thấy lại ở ảnh sau')
    return {'schema_version':2,'before_detections':old,'detections':new,'unmatched_before':missing,
            'homography_before':hb.tolist(),'homography_after':ha.tolist(),'alignment_before':qb,'alignment_after':qa,
            'confidence_threshold':confidence,'match_radius_px':radius,'config':config,
            'coordinate_unit':'template_pixel','point_estimator':'bounding_box_center',
            'pipeline':'homography_images_then_yolo_then_match_then_score','warnings':warnings,
            'status':'NEEDS_REVIEW' if warnings else 'AUTO',
            'warning':'Kết quả tự động, không xác định thứ tự phát bắn hoặc phát trúng chồng hoàn toàn.'}


def overlay(template,config,holes=()):
    out=template.copy(); center=tuple(int(round(v)) for v in config['center'])
    mask=np.zeros(template.shape[:2],np.uint8)
    cv2.fillPoly(mask,[np.array(config['outline'],np.int32)],255)
    rings=out.copy()
    for s,r in config['radii'].items():
        cv2.circle(rings,center,round(r),(0,210,255),1)
    out[mask>0]=rings[mask>0]
    cv2.polylines(out,[np.array(config['outline'],np.int32)],True,(255,160,0),2)
    cv2.drawMarker(out,center,(0,0,255))
    for i,h in enumerate(holes):
        p=tuple(round(v) for v in h['template_point']); color=(0,200,0) if h['selected'] else (100,100,255)
        cv2.circle(out,p,7,color,2);cv2.putText(out,f"{i+1}:{h['score']}",(p[0]+8,p[1]),cv2.FONT_HERSHEY_SIMPLEX,.5,color,1)
    return out
