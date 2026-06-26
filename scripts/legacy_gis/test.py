import os, numpy as np
from osgeo import gdal
from qgis.core import QgsVectorLayer, QgsGeometry
ROOT="/home/arash/Dropbox/Chloeta/NHA/"; SITE_DIR="WS3_GIS/AZ12-100"
OUT=os.path.join(ROOT,SITE_DIR,"outputs")
def load(t):
    L=QgsVectorLayer(os.path.join(OUT,f"wshed_{t}_clean.gpkg"),"w","ogr")
    return QgsGeometry.unaryUnion([f.geometry() for f in L.getFeatures()])
w4,w6=load("4"),load("6")
shared=w4.intersection(w6)
print("shared 4n6: %.3f km2  centroid %s"%(shared.area()/1e6, shared.centroid().asPoint()))
fd=gdal.Open(os.path.join(OUT,"flow_dir.tif")); gt=fd.GetGeoTransform(); arr=fd.ReadAsArray()
dirs={1:(0,1),2:(-1,1),3:(-1,0),4:(-1,-1),5:(0,-1),6:(1,-1),7:(1,0),8:(1,1)}
import random
def trace(px,py):
    c=int((px-gt[0])/gt[1]); r=int((py-gt[3])/gt[5]); pathlen=0
    for step in range(20000):
        if not(0<=r<arr.shape[0] and 0<=c<arr.shape[1]): return ("edge",pathlen,gt[0]+(c+0.5)*gt[1],gt[3]+(r+0.5)*gt[5])
        d=abs(int(arr[r,c]))
        if d not in dirs: return ("sink",pathlen,gt[0]+(c+0.5)*gt[1],gt[3]+(r+0.5)*gt[5])
        dr,dc=dirs[d]; r+=dr; c+=dc; pathlen+=1
    return ("maxiter",pathlen,0,0)
# trace from 8 random points inside the shared area
bb=shared.boundingBox()
done=0
while done<8:
    px=random.uniform(bb.xMinimum(),bb.xMaximum())
    py=random.uniform(bb.yMinimum(),bb.yMaximum())
    if shared.contains(QgsGeometry.fromPointXY(__import__('qgis.core',fromlist=['QgsPointXY']).QgsPointXY(px,py))):
        kind,n,ex,ey=trace(px,py)
        print(f"  from ({px:.0f},{py:.0f}) -> {kind} after {n} steps, exit ~({ex:.0f},{ey:.0f})")
        done+=1
print("pp4 ~ (599734,3992301)   pp6 ~ (599519,3992386)")