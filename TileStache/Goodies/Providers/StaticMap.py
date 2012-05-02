""" Mapnik StaticMap Provider.

Use Sperical Mercator projection

Sample configuration:

    "provider":
    {
      "class": "TileStache.Goodies.Providers.StaticMap:Marker",
      "kwargs":
      {
        "mapfile": "mymap.xml", 
      }
    }

mapfile: the mapnik xml file to load the map from
"""

import logging
from urlparse import urlparse, urljoin
from urllib import urlopen
from thread import allocate_lock
from time import time
from StringIO import StringIO
from os.path import dirname, exists, join as pathjoin
import math
import os

try:
    import mapnik2 as mapnik
except ImportError:
    try:
        import mapnik
    except ImportError:
        pass

try:
    from PIL import Image
except ImportError:
    # On some systems, PIL.Image is known as Image.
    import Image

import ModestMaps
from ModestMaps.Core import Point, Coordinate
from ModestMaps.Geo import Location
from TileStache import Geography


global_mapnik_lock = allocate_lock()

# Constants to calculate the deltas
# Source: gmerc.py
# http://blag.whit537.org/2007/07/how-to-hack-on-google-maps.html
CBK = [128, 256, 512, 1024, 2048, 4096, 8192, 16384, 32768, 65536, 131072, 262144, 524288, 1048576, 2097152, 4194304, 8388608, 16777216, 33554432, 67108864, 134217728, 268435456, 536870912, 1073741824, 2147483648, 4294967296, 8589934592, 17179869184, 34359738368, 68719476736, 137438953472]
CEK = [0.7111111111111111, 1.4222222222222223, 2.8444444444444446, 5.688888888888889, 11.377777777777778, 22.755555555555556, 45.51111111111111, 91.02222222222223, 182.04444444444445, 364.0888888888889, 728.1777777777778, 1456.3555555555556, 2912.711111111111, 5825.422222222222, 11650.844444444445, 23301.68888888889, 46603.37777777778, 93206.75555555556, 186413.51111111112, 372827.02222222224, 745654.0444444445, 1491308.088888889, 2982616.177777778, 5965232.355555556, 11930464.711111112, 23860929.422222223, 47721858.844444446, 95443717.68888889, 190887435.37777779, 381774870.75555557, 763549741.5111111]
CFK = [40.74366543152521, 81.48733086305042, 162.97466172610083, 325.94932345220167, 651.8986469044033, 1303.7972938088067, 2607.5945876176133, 5215.189175235227, 10430.378350470453, 20860.756700940907, 41721.51340188181, 83443.02680376363, 166886.05360752725, 333772.1072150545, 667544.214430109, 1335088.428860218, 2670176.857720436, 5340353.715440872, 10680707.430881744, 21361414.86176349, 42722829.72352698, 85445659.44705395, 170891318.8941079, 341782637.7882158, 683565275.5764316, 1367130551.1528633, 2734261102.3057265, 5468522204.611453, 10937044409.222906, 21874088818.445812, 43748177636.891624]


def ll2px(lat, lng, zoom):
    """Given two floats and an int, return a 2-tuple of ints.
    Note that the pixel coordinates are tied to the entire map, not to the map
    section currently in view.
    """
    assert isinstance(lat, (float, int, long)), \
        ValueError("lat must be a float")
    lat = float(lat)
    assert isinstance(lng, (float, int, long)), \
        ValueError("lng must be a float")
    lng = float(lng)
    assert isinstance(zoom, int), TypeError("zoom must be an int from 0 to 30")
    assert 0 <= zoom <= 30, ValueError("zoom must be an int from 0 to 30")

    cbk = CBK[zoom]

    x = int(round(cbk + (lng * CEK[zoom])))

    foo = math.sin(lat * math.pi / 180)
    if foo < -0.9999:
        foo = -0.9999
    elif foo > 0.9999:
        foo = 0.9999

    y = int(round(cbk + (0.5 * math.log((1+foo)/(1-foo)) * (-CFK[zoom]))))

    return (x, y)

def px2ll(x, y, zoom):
    """Given three ints, return a 2-tuple of floats.

    Note that the pixel coordinates are tied to the entire map, not to the map
    section currently in view.

    """
    assert isinstance(x, (int, long)), \
        ValueError("px must be a 2-tuple of ints")
    assert isinstance(y, (int, long)), \
        ValueError("px must be a 2-tuple of ints")
    assert isinstance(zoom, int), TypeError("zoom must be an int from 0 to 30")
    assert 0 <= zoom <= 30, ValueError("zoom must be an int from 0 to 30")

    foo = CBK[zoom]
    lng = (x - foo) / CEK[zoom]
    bar = (y - foo) / -CFK[zoom]
    blam = 2 * math.atan(math.exp(bar)) - math.pi / 2
    lat = blam / (math.pi / 180)

    return (lat, lng)

class _Loc:
    """ Local duck for (lat, lon) points.
    """
    def __init__(self, lat, lon):
        self.lat = lat
        self.lon = lon


class _Marker:
    """ Local duck for Markers
    """
    def __init__(self, lat, lon, icon):
        self.center = _Loc(lat, lon)
        self.icon = icon


class _Path:
    """ Local duck for Paths
    """
    def __init__(self, color="F00", weight=1):
        self.center = None
        self.color = color
        self.weight = weight
        self.polygon = []
    
    def addLoc(self, lat, lon):
        p = _Loc(float(lat), float(lon))
        self.polygon.append(p)
        # TODO: calculate path center
        self.center = p

class StaticMaps:

    def __init__(self, width, height, zoom):
        self.width = width
        self.height = height
        self.zoom = zoom
        self.center = None
        self.marker = None
        self.path = None
    
    def getSize(self):
        return self.width, self.height
        
    def getCoord(self):
        return Coordinate(self.center.lat, self.center.lon, self.zoom)
        
    def addMarker(self, lat, lon, icon):
        # move the center latitude some pixels down, to match with the middle-bottom point of the icon
        # TODO: make it work for more than one marker
        # TODO: calculate the center if more than one marker
        lat, lon = float(lat), float(lon)
        x, y = ll2px(lat, lon, self.zoom)
        _lat, _lon = px2ll(x, y-22, self.zoom)
        self.center = _Loc(_lat, lon)
        self.marker = _Marker(_lat, lon, icon)
    
    def addPath(self, path):
        self.path = _Path()
        print path['points']
        for p in path['points']:
            self.path.addLoc(p[0], p[1])
        minLat = min([float(p[0]) for p in path['points']])
        maxLat = max([float(p[0]) for p in path['points']])
        minLon = min([float(p[1]) for p in path['points']])
        maxLon = max([float(p[1]) for p in path['points']])

        self.center = _Loc((minLat + maxLat)/2, (minLon + maxLon)/2)
        logging.debug('TileStache.Providers.StaticMaps.addPath() center @ lat:%.4f, lon:%.4f', self.center.lat, self.center.lon)

        # Calculate the zoom level
        merc_radius = 85445659.44705395
        x = ((maxLat-minLat)*merc_radius*math.pi) / (180*self.height)
        zoomLon = 21 - int(math.log(x, 2))
        y = ((maxLon-minLon)*merc_radius*math.pi) / (180*self.width)
        zoomLat = 21 - int(math.log(y, 2))
        self.zoom = min(zoomLat, zoomLon)-180
        logging.debug('TileStache.Providers.StaticMaps.addPath() zoom:%d', self.zoom)
        
    
    def getWKT(self):
        line = "LINESTRING(%s)" % ",".join(["%s %s" % (p.lon, p.lat) for p in self.path.polygon])
        logging.debug('TileStache.Providers.StaticMaps.getWKT() wkt: %s', line)
        #return line
        poly = "POLYGON((%s))" % ",".join(["%s %s" % (p.lon, p.lat) for p in self.path.polygon])
        logging.debug('TileStache.Providers.StaticMaps.getWKT() wkt: %s', poly)
        return poly
        
class Marker:

    def __init__(self, layer, mapfile, iconspath=None):
        self.mapnik = None
        self.layer = layer
        self.iconspath = iconspath
        maphref = urljoin(layer.config.dirpath, mapfile)
        scheme, h, path, q, p, f = urlparse(maphref)
        if scheme in ('file', ''):
            self.mapfile = path
        else:
            self.mapfile = maphref
        
        iconhref = urljoin(layer.config.dirpath, iconspath)
        scheme, h, path, q, p, f = urlparse(iconhref)
        if scheme in ('file', ''):
            self.iconsfullpath = path
        else:
            self.iconsfullpath = iconhref
    
    def renderStaticMap(self, staticmap):
        logging.debug('TileStache.Providers.StaticMaps.renderStaticMap() Map center @ lat:%.4f, lon:%.4f, zoom:%d', staticmap.center.lat, staticmap.center.lon, staticmap.zoom)
        start_time = time()
        
        if self.mapnik is None:
            self.mapnik = mapnik.Map(0, 0)
            #mapnik.load_map(self.mapnik, str(self.mapfile))
            logging.debug('TileStache.Providers.StaticMaps.renderStaticMap() %.3f to load %s', time() - start_time, self.mapfile)
        
        # Download the icon if doesn't exists
        if staticmap.marker:
            scheme, h, path, q, p, f = urlparse(staticmap.marker.icon)
            fullpath = pathjoin(self.iconsfullpath, h, path[1:])
            relativepath = pathjoin(self.iconspath, h, path[1:])
            print fullpath
            if not exists(fullpath):
                logging.debug('TileStache.Providers.StaticMaps.renderStaticMap() - Downloading icon %s', staticmap.marker.icon)
                url = staticmap.marker.icon
                icon = Image.open(StringIO(urlopen(url).read()))
                try:
                    os.makedirs(dirname(fullpath))
                except:
                    pass
                        
                icon.save(fullpath)
            
        # Mapnik can behave strangely when run in threads, so place a lock on the instance.
        if global_mapnik_lock.acquire():
            #  Remove all Mapnik Styles and layers from the Map.
            self.mapnik.remove_all()
            mapnik.load_map(self.mapnik, str(self.mapfile))
            if staticmap.marker:
                logging.debug('TileStache.Providers.StaticMaps.renderStaticMap() Marker center @ lat:%.4f, lon:%.4f', 
                    staticmap.marker.center.lat, staticmap.marker.center.lon)
                    
                pds = mapnik.PointDatasource()
                pds.add_point(staticmap.marker.center.lon, staticmap.marker.center.lat, 'Name', 'infopoint')

                infopoint = mapnik.PointSymbolizer(mapnik.PathExpression(str(relativepath)))
                infopoint.transform="translate(200 0)"
                infopoint.allow_overlap = True
            
                s = mapnik.Style()
                r = mapnik.Rule()
                r.symbols.append(infopoint)
                r.filter = mapnik.Filter("[Name] = 'infopoint'")
                s.rules.append(r)
            
                lyr = mapnik.Layer('Infopoints')
                lyr.datasource = pds
                lyr.styles.append('Infopoint Style')
                self.mapnik.layers.append(lyr)
            
                self.mapnik.append_style('Infopoint Style', s)

            # transform the centre point into the target coord sys
            centre = Location(staticmap.center.lat, staticmap.center.lon)
            merc = Geography.SphericalMercator()
            merc_centre = merc.locationProj(centre)
            
            # 360/(2**zoom) degrees = 256 px
            # so in merc 1px = (20037508.34*2) / (256 * 2**zoom)
            # hence to find the bounds of our rectangle in projected coordinates + and - half the image width worth of projected coord units
            mercator_offset = 256*(2 ** (staticmap.zoom))
            dx = (20037508.34*2*(staticmap.width/2))/mercator_offset
            
            minx = merc_centre.x - dx
            maxx = merc_centre.x + dx
            
            # grow the height bbox, as we only accurately set the width bbox
            self.mapnik.aspect_fix_mode = mapnik.aspect_fix_mode.ADJUST_BBOX_HEIGHT
            bounds = mapnik.Box2d(minx, merc_centre.y-10, maxx, merc_centre.y+10) # the y bounds will be fixed by mapnik due to ADJUST_BBOX_HEIGHT

            self.mapnik.zoom_to_box(bounds)
            self.mapnik.width = staticmap.width
            self.mapnik.height = staticmap.height
            
            img = mapnik.Image(staticmap.width, staticmap.height)
            mapnik.render(self.mapnik, img)
            watermark = mapnik.Image.open('examples/staticmaps/watermark.png')
            x_offset = 5
            y_offset = staticmap.height-20
            opacity = 0.5
            img.blend(x_offset, y_offset, watermark, opacity)
            global_mapnik_lock.release()
        
        img = Image.fromstring('RGBA', (staticmap.width, staticmap.height), img.tostring())
        logging.debug('TileStache.Providers.StaticMaps.renderStaticMap() %dx%d in %.3f from %s', staticmap.width, staticmap.height, time() - start_time, self.mapfile)
        return img
        

class Path:
    def __init__(self, layer, mapfile):
        self.mapnik = None
        self.layer = layer
        maphref = urljoin(layer.config.dirpath, mapfile)
        scheme, h, path, q, p, f = urlparse(maphref)
        if scheme in ('file', ''):
            self.mapfile = path
        else:
            self.mapfile = maphref
        
    def renderStaticMap(self, staticmap):
        logging.debug('TileStache.Providers.StaticMaps.renderStaticMap() Map center @ lat:%.4f, lon:%.4f, zoom:%d', staticmap.center.lat, staticmap.center.lon, staticmap.zoom)
        start_time = time()
        
        if self.mapnik is None:
            self.mapnik = mapnik.Map(200, 200)
            #mapnik.load_map(self.mapnik, str(self.mapfile))
            logging.debug('TileStache.Providers.StaticMaps.renderStaticMap() %.3f to load %s', time() - start_time, self.mapfile)

        # Mapnik can behave strangely when run in threads, so place a lock on the instance.
        if global_mapnik_lock.acquire():
            #  Remove all Mapnik Styles and layers from the Map.
            self.mapnik.remove_all()
            mapnik.load_map(self.mapnik, str(self.mapfile))
            if staticmap.path:
                logging.debug('TileStache.Providers.StaticMaps.renderStaticMap() Path center @ lat:%.4f, lon:%.4f', 
                    staticmap.path.center.lat, staticmap.path.center.lon)
                    
                #wkt = 'POLYGON((39.0234375 62.578125,20.0390625 58.359375,15.1171875 45.703125,15.8203125 35.15625,24.2578125 30.9375,35.5078125 30.234375,55.1953125 38.671875,58.7109375 46.40625,54.4921875 53.4375,51.6796875 58.359375,49.5703125 61.171875,39.0234375 62.578125))'
                # Features
                f = mapnik.Feature(1)
                f.add_geometries_from_wkt(staticmap.getWKT())
                f['Name'] = 'route'
                ds = mapnik.MemoryDatasource()
                ds.add_feature(f)

                poly = mapnik.PolygonSymbolizer()
                line = mapnik.LineSymbolizer()
                
                s = mapnik.Style()
                r = mapnik.Rule()
                #r.symbols.append(poly)
                r.symbols.extend([poly, line])
                #r.filter = mapnik.Filter("[Name] = 'route'")
                s.rules.append(r)
                self.mapnik.append_style('Route Style', s)
            
                lyr = mapnik.Layer('Routes')
                lyr.datasource = ds
                lyr.styles.append('Route Style')
                self.mapnik.layers.append(lyr)
            

            # transform the centre point into the target coord sys
            centre = Location(staticmap.center.lat, staticmap.center.lon)
            #centre = Location(50, 20)
            merc = Geography.SphericalMercator()
            merc_centre = merc.locationProj(centre)
            
            # 360/(2**zoom) degrees = 256 px
            # so in merc 1px = (20037508.34*2) / (256 * 2**zoom)
            # hence to find the bounds of our rectangle in projected coordinates + and - half the image width worth of projected coord units
            mercator_offset = 256*(2 ** (staticmap.zoom))
            dx = (20037508.34*2*(staticmap.width/2))/mercator_offset
            
            minx = merc_centre.x - dx
            maxx = merc_centre.x + dx
            
            # grow the height bbox, as we only accurately set the width bbox
            self.mapnik.aspect_fix_mode = mapnik.aspect_fix_mode.ADJUST_BBOX_HEIGHT
            bounds = mapnik.Box2d(minx, merc_centre.y-10, maxx, merc_centre.y+10) # the y bounds will be fixed by mapnik due to ADJUST_BBOX_HEIGHT

            self.mapnik.zoom_to_box(bounds)
            self.mapnik.width = staticmap.width
            self.mapnik.height = staticmap.height
            
            img = mapnik.Image(staticmap.width, staticmap.height)
            mapnik.render(self.mapnik, img)
            watermark = mapnik.Image.open('examples/staticmaps/watermark.png')
            x_offset = 5
            y_offset = staticmap.height-20
            opacity = 0.5
            img.blend(x_offset, y_offset, watermark, opacity)
            global_mapnik_lock.release()
        
        img = Image.fromstring('RGBA', (staticmap.width, staticmap.height), img.tostring())
        logging.debug('TileStache.Providers.StaticMaps.renderStaticMap() %dx%d in %.3f from %s', staticmap.width, staticmap.height, time() - start_time, self.mapfile)
        return img
