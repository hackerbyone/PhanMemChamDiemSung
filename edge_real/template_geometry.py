"""Automatic template geometry for a front-facing Bia so 4, dark print on light paper.
No preselected center/radii, and no assumption that ring gaps are equal.
The silhouette is horizontally filled; this is specific to this target family.
"""
import json
from pathlib import Path
import cv2
import numpy as np
from scipy.signal import find_peaks
from scipy.optimize import least_squares
from core import sha,validate_config,read_image
from storage import atomic_json
VERSION='bia4-auto-2.0'


def extract_geometry(image):
    height,width=image.shape[:2];scale=min(1.,900/max(height,width))
    work=cv2.resize(image,None,fx=scale,fy=scale) if scale<1 else image
    h,w=work.shape[:2];unit=min(h,w)
    gray=cv2.cvtColor(work,cv2.COLOR_BGR2GRAY)
    if np.std(gray)<8:raise ValueError('Ảnh mẫu thiếu tương phản để nhận dạng bia.')
    _,dark=cv2.threshold(gray,0,255,cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)
    k=max(3,round(unit*.012))|1
    closed=cv2.morphologyEx(dark,cv2.MORPH_CLOSE,np.ones((k,k),np.uint8))
    contours,_=cv2.findContours(closed,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    if not contours:raise ValueError('Không tìm được đường bao bia.')
    largest=max(contours,key=cv2.contourArea);mask=np.zeros_like(gray);cv2.drawContours(mask,[largest],-1,255,-1)
    # Bia 4 is row-convex. Include bright interior, even where printing joins paper at bottom.
    for row in mask:
        xs=np.flatnonzero(row)
        if len(xs):row[xs[0]:xs[-1]+1]=255
    area=float(np.mean(mask>0))
    if not .25<area<.93:raise ValueError('Không tách được hình người rõ ràng khỏi nền.')
    contour=max(cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)[0],key=cv2.contourArea)
    outline=cv2.approxPolyDP(contour,1.5,True).reshape(-1,2).astype(float)/scale
    blur=cv2.GaussianBlur(gray,(5,5),1)
    guesses=[]
    # Independent search intervals to expose concentric circles (Hough minDist suppresses shared centers).
    edges=np.array([.065,.16,.28,.39,.49])*unit
    for lo,hi in zip(edges,edges[1:]):
        circles=cv2.HoughCircles(blur,cv2.HOUGH_GRADIENT,1.5,max(15,unit*.035),param1=60,param2=70,minRadius=round(lo),maxRadius=round(hi))
        if circles is not None:guesses.extend(circles[0,:12].tolist())
    if len(guesses)<3:raise ValueError('Không đủ cung tròn để xác định tâm bia.')
    points=np.array(guesses)
    counts=(np.linalg.norm(points[:,None,:2]-points[None,:,:2],axis=2)<unit*.015).sum(axis=1)
    best=points[counts.argmax(),:2];cluster=points[np.linalg.norm(points[:,:2]-best,axis=1)<unit*.015]
    if len(cluster)<3:raise ValueError('Các đường tròn phát hiện không có cùng tâm.')
    center=np.median(cluster[:,:2],axis=0)
    theta=np.arange(1440,dtype=np.float32)*2*np.pi/1440
    radii=np.arange(max(12,round(unit*.06)),round(np.hypot(h,w)*.52),dtype=np.float32)
    safe=cv2.erode(mask,np.ones((max(5,round(unit*.016))|1,)*2,np.uint8))
    g=gray.astype(np.float32);offset=max(3,unit*.0055)
    def contrast(center,rs):
        rs=np.asarray(rs,np.float32).reshape(-1,1)
        x=(center[0]+rs*np.cos(theta)).astype(np.float32);y=(center[1]+rs*np.sin(theta)).astype(np.float32)
        mid=cv2.remap(g,x,y,cv2.INTER_LINEAR)
        left=cv2.remap(g,(center[0]+(rs-offset)*np.cos(theta)).astype(np.float32),(center[1]+(rs-offset)*np.sin(theta)).astype(np.float32),cv2.INTER_LINEAR)
        right=cv2.remap(g,(center[0]+(rs+offset)*np.cos(theta)).astype(np.float32),(center[1]+(rs+offset)*np.sin(theta)).astype(np.float32),cv2.INTER_LINEAR)
        valid=cv2.remap(safe,x,y,cv2.INTER_NEAREST)>0
        return abs(mid-(left+right)/2),valid
    response,valid=contrast(center,radii)
    profile=(response*valid).sum(axis=1)/np.maximum(valid.sum(axis=1),1)
    peaks,_=find_peaks(profile,distance=max(15,round(unit*.033)),prominence=unit*.004)
    peaks=[i for i in peaks if valid[i].sum()>55 and profile[i]>18]
    if len(peaks)<5:raise ValueError('Không tìm đủ 5 biên vòng 10–6; hãy dùng mẫu rõ và chụp thẳng.')
    chosen=sorted(sorted(peaks,key=lambda i:-profile[i])[:5]);rs=radii[chosen].astype(float)
    if min(profile[chosen])<max(profile[chosen])*.35:raise ValueError('Một số vòng không đủ rõ để chấm tự động.')
    samples=[];labels=[];supports=[]
    for i,r in enumerate(rs):
        options=np.arange(r-7,r+7.01,.5,dtype=np.float32)
        resp,ok=contrast(center,options);resp[~ok]=0;idx=resp.argmax(axis=0);val=resp[idx,np.arange(len(theta))]
        take=val>max(25,float(np.max(profile[chosen]))*.3)
        pts=np.column_stack([center[0]+options[idx[take]]*np.cos(theta[take]),center[1]+options[idx[take]]*np.sin(theta[take])])
        if len(pts)<45:raise ValueError('Cung vòng quá ngắn hoặc mờ.')
        samples.append(pts);labels.extend([i]*len(pts));supports.append(round(float(take.mean()*360),1))
    pts=np.vstack(samples);labels=np.array(labels)
    def residual(v):return np.linalg.norm(pts-v[:2],axis=1)-v[2:][labels]
    fit=least_squares(residual,np.r_[center,rs],loss='soft_l1',f_scale=1.5,max_nfev=80)
    errors=residual(fit.x)
    per_ring=[float(np.median(abs(errors[labels==i]))) for i in range(5)]
    if max(per_ring)>3 or np.linalg.norm(fit.x[:2]-center)>unit*.025:raise ValueError('Sai số khớp vòng điểm quá lớn.')
    config={'algorithm':VERSION,'confirmed':False,'center':(fit.x[:2]/scale).tolist(),
            'radii':{str(10-i):float(r/scale) for i,r in enumerate(fit.x[2:])},'outline':outline.tolist(),
            'quality':{'silhouette_area_fraction':area,'arc_support_degrees':supports,'median_ring_residual_px':[e/scale for e in per_ring]},
            'image_size':[width,height],'geometry_method':'otsu_row_filled_outline+hough+radial_contrast+joint_robust_circle_fit'}
    validate_config(config);return config


def load_or_extract(template_path,config_path):
    digest=sha(template_path);config_path=Path(config_path)
    if config_path.exists():
        try:
            c=json.loads(config_path.read_text(encoding='utf8'));validate_config(c)
            if c.get('template_sha256')==digest and (c.get('algorithm')==VERSION or c.get('geometry_method')=='manual'):
                return c
        except (ValueError,KeyError,TypeError):pass
    c=extract_geometry(read_image(template_path));c['template_sha256']=digest;atomic_json(config_path,c);return c
