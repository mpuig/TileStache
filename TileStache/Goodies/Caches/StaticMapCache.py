""" Cache that stores static maps to disk

Example StaticMaps cache configuration:

"cache":
{
    "class": "TileStache.Goodies.Caches.StaticMapCache:Disk",
    "kwargs": {
        "path": "cache-staticmaps"
    }
}
"""

import os
import sys
import time
from tempfile import mkstemp
from os.path import isdir, exists, dirname, basename, join as pathjoin


class Disk:
    """ Caches static maps files to disk.
    """
    def __init__(self, path, umask=0022):
        self.cachepath = path
        self.umask = umask

    def _filepath(self, layer, staticmap, format):
        """
        """
        l = layer.name()
        z = '%d' % staticmap.zoom
        e = format.lower()
        
        x = str(int((float(staticmap.center.lon)+180)*10000))
        y = str(int((float(staticmap.center.lat)+90)*10000))
        size = "%dx%d" % (staticmap.width, staticmap.height)
        
        if staticmap.marker:
            icon = basename(staticmap.marker.icon).replace('.','-')
            return os.sep.join( (l, z, x, y + '_' + size + '_' + icon + '.' + e) )
        elif staticmap.path:
            return os.sep.join( (l, z, x, y + '_' + size + '.' + e) )
        else:
            return os.sep.join( (l, z, x, y + '_' + size + '.' + e) )

    def _fullpath(self, layer, staticmap, format):
        """
        """
        filepath = self._filepath(layer, staticmap, format)
        fullpath = pathjoin(self.cachepath, filepath)

        return fullpath

    def _lockpath(self, layer, staticmap, format):
        """
        """
        return self._fullpath(layer, staticmap, format) + '.lock'
    
    def lock(self, layer, staticmap, format):
        """ Acquire a cache lock for this tile.
        
            Returns nothing, but blocks until the lock has been acquired.
            Lock is implemented as an empty directory next to the tile file.
        """
        lockpath = self._lockpath(layer, staticmap, format)
        due = time.time() + layer.stale_lock_timeout
        
        while True:
            # try to acquire a directory lock, repeating if necessary.
            try:
                umask_old = os.umask(self.umask)
                
                if time.time() > due:
                    # someone left the door locked.
                    try:
                        os.rmdir(lockpath)
                    except OSError:
                        # Oh - no they didn't.
                        pass
                os.makedirs(lockpath, 0777&~self.umask)
                break
            except OSError, e:
                if e.errno != 17:
                    raise
                time.sleep(.2)
            finally:
                os.umask(umask_old)
    
    def unlock(self, layer, staticmap, format):
        """ Release a cache lock for this tile.

            Lock is implemented as an empty directory next to the tile file.
        """
        lockpath = self._lockpath(layer, staticmap, format)

        try:
            os.rmdir(lockpath)
        except OSError:
            # Ok, someone else deleted it already
            pass
        
    def remove(self, layer, staticmap, format):
        """ Remove a cached tile.
        """
        fullpath = self._fullpath(layer, staticmap, format)
        
        try:
            os.remove(fullpath)
        except OSError, e:
            # errno=2 means that the file does not exist, which is fine
            if e.errno != 2:
                raise
        
    def read(self, layer, staticmap, format):
        """ Read a cached tile.
        """
        fullpath = self._fullpath(layer, staticmap, format)
        
        if not exists(fullpath):
            return None

        age = time.time() - os.stat(fullpath).st_mtime
        
        if layer.cache_lifespan and age > layer.cache_lifespan:
            return None
    
        else:
            body = open(fullpath, 'rb').read()
            return body
    
    def save(self, body, layer, staticmap, format):
        """ Save a cached tile.
        """
        fullpath = self._fullpath(layer, staticmap, format)
        
        try:
            umask_old = os.umask(self.umask)
            os.makedirs(dirname(fullpath), 0777&~self.umask)
        except OSError, e:
            if e.errno != 17:
                raise
        finally:
            os.umask(umask_old)

        suffix = '.' + format.lower()

        fh, tmp_path = mkstemp(dir=self.cachepath, suffix=suffix)
        
        os.write(fh, body)
        os.close(fh)
        
        try:
            os.rename(tmp_path, fullpath)
        except OSError:
            os.unlink(fullpath)
            os.rename(tmp_path, fullpath)

        os.chmod(fullpath, 0666&~self.umask)
