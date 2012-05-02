""" AreaServer supplies a tiny image server for use with TileStache providers
    that implement renderStaticMap() (http://staticstache.org/doc/#custom-providers).
    The built-in Mapnik provider (http://staticstache.org/doc/#mapnik-provider)
    is one example.
    
    There are no tiles here, just a quick & dirty way of getting variously-sized
    images out of a codebase that's ordinarily oriented toward tile generation.

    Example usage, with gunicorn (http://gunicorn.org):
    
      gunicorn --bind localhost:8888 "TileStache.Goodies.StaticMapServer:WSGIServer('staticstache.cfg')"
    

      http://localhost:8888/layer-name?size=600x300&center=12,-35
      
    Example: staticstache.cfg
    
    {
      "cache":
      {
        "name": "Disk",
        "path": "cache",
        "umask": "0000"
      },
      "cache":
      {
        "name": "Test",
    	"verbose": "True"
      },
      
      "layers": 
      {
        "markers":
        {
            "provider": {"name": "marker", "mapfile": "examples/staticmaps.xml"},
            "projection": "spherical mercator",
    		"png options": {"optimize": true, "palette": "examples/osm-palette.act"}		
        },
        "routes":
        {
            "provider": {"name": "path", "mapfile": "examples/style.xml"},
            "projection": "spherical mercator"
        } 
      }
    }
    
"""

from urlparse import parse_qsl
from datetime import timedelta
from datetime import datetime
from StringIO import StringIO

from TileStache import WSGITileServer, Core, Config
from TileStache.Core import KnownUnknown
from time import time
import logging

from Providers.StaticMap import StaticMaps


def getStaticMap(layer, extension='png', ignore_cached=False):
    """ Get a type string and binary for a given request layer.
    
        Arguments:
        - layer: instance of Core.Layer to render.
        - extension: filename extension to choose response type, e.g. "png" or "jpg".
        - ignore_cached: always re-render the tile, whether it's in the cache or not.
    
        This is the main entry point, after site configuration has been loaded
        and individual tiles need to be rendered.
    """
    start_time = time()
    
    mimetype, format = layer.getTypeByExtension(extension)
    cache = layer.config.cache
    
    if not ignore_cached:
        # Start by checking for a tile in the cache.
        body = cache.read(layer, layer.staticmap, format)
        tile_from = 'cache'

    else:
        # Then look in the bag of recent tiles.
        tile_from = 'recent tiles'
    
    # If no tile was found, dig deeper
    if body is None:
        try:
            lockStaticMap = None

            if layer.write_cache:
                # this is the coordinate that actually gets locked.
                lockStaticMap = layer.staticmap
                
                # We may need to write a new tile, so acquire a lock.
                cache.lock(layer, lockStaticMap, format)
            
            if not ignore_cached:
                # There's a chance that some other process has
                # written the tile while the lock was being acquired.
                body = cache.read(layer, layer.staticmap, format)
                tile_from = 'cache after all'
    
            if body is None:
                # No one else wrote the tile, do it here.
                buff = StringIO()
                
                try:
                    tile = layer.render(layer.staticmap.getCoord(), format)
                    save = True
                except Core.NoTileLeftBehind, e:
                    tile = e.tile
                    save = False

                if not layer.write_cache:
                    save = False
                
                if format.lower() == 'jpeg':
                    save_kwargs = layer.jpeg_options
                elif format.lower() == 'png':
                    save_kwargs = layer.png_options
                else:
                    save_kwargs = {}
                
                tile.save(buff, format, **save_kwargs)
                body = buff.getvalue()
                
                if save:
                    cache.save(body, layer, layer.staticmap, format)

                tile_from = 'layer.render()'

        finally:
            if lockStaticMap:
                # Always clean up a lock when it's no longer being used.
                cache.unlock(layer, lockStaticMap, format)
    
    logging.info('TileStache.getStaticMap() %s/%d/%d/%d.%s via %s in %.3f', layer.name(), layer.staticmap.zoom, layer.staticmap.center.lon, layer.staticmap.center.lat, extension, tile_from, time() - start_time)
    
    return mimetype, body


class WSGIServer (WSGITileServer):
    """ WSGI Application that can handle WMS-style requests for static images.
        
        Inherits the constructor from TileStache WSGI, which just loads
        a TileStache configuration file into self.config.
        
        WSGITileServer autoreload argument is ignored, though. For now.
    """
    def __call__(self, environ, start_response):
        """ Handle a request, using PATH_INFO and QUERY_STRING from environ.
        
            There are six required query string parameters: width, height,
            xmin, ymin, xmax and ymax. Layer name must be supplied in PATH_INFO.
        """
        try:
            for var in 'QUERY_STRING PATH_INFO'.split():
                if var not in environ:
                    raise KnownUnknown('Missing "%s" environment variable' % var)
            
            query = dict(parse_qsl(environ['QUERY_STRING']))
            
            for param in 'size zoom'.split():
                if param not in query:
                    raise KnownUnknown('Missing "%s" parameter' % param)
            
            layer = environ['PATH_INFO'].strip('/')
            layer = self.config.layers[layer]
            print layer
            provider = layer.provider
            
            if not hasattr(provider, 'renderStaticMap'):
                raise KnownUnknown('Layer "%s" provider %s has no renderStaticMap() method' % (layer.name(), provider.__class__))
            
            zoom = int(query['zoom'])
            w, h = [int(p) for p in query['size'].split('x')]
            layer.staticmap = StaticMaps(w, h, zoom)

            if 'markers' in query:
                for m in query['markers'].split('|'):
                    if m.startswith('icon:'):
                        icon = m[5:]
                    elif len(m.split(','))==2:
                        lat, lon = [float(p) for p in m.split(',')]
                layer.staticmap.addMarker(lat, lon, icon)
            
            if 'path' in query:
                params = [m.split(':') for m in query['path'].split('|') if m.find(':')>0]
                static_path = dict(params)
                static_path['points'] = [m.split(',') for m in query['path'].split('|') if m.find(',')>0]
                layer.staticmap.addPath(static_path)
                
            if layer.staticmap.path is None and layer.staticmap.marker is None:
                lat, lon = [float(p) for p in query['center'].split(',')]
                
            mimetype, content = getStaticMap(layer)
            headers = [('Content-Type', 'image/png')]
            
            if layer.allowed_origin:
                headers.append(('Access-Control-Allow-Origin', layer.allowed_origin))
            
            if layer.max_cache_age is not None:
                expires = datetime.utcnow() + timedelta(seconds=layer.max_cache_age)
                headers.append(('Expires', expires.strftime('%a %d %b %Y %H:%M:%S GMT')))
                headers.append(('Cache-Control', 'public, max-age=%d' % layer.max_cache_age))

            start_response('200 OK', headers)
            return content
        
        except KnownUnknown, e:
            start_response('400 Bad Request', [('Content-Type', 'text/plain')])
            return str(e)
