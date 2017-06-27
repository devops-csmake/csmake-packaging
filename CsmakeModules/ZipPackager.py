# <copyright>
# (c) Copyright 2017 Hewlett Packard Enterprise Development LP
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the
# Free Software Foundation, either version 3 of the License, or (at your
# option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General
# Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
# </copyright>
# <copyright>
# (c) Copyright 2017 Hewlett Packard Enterprise Development LP
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the
# Free Software Foundation, either version 3 of the License, or (at your
# option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General
# Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
# </copyright>
from CsmakeModules.Packager import Packager
import zipfile
import os.path

class ZipPackager(Packager):
    """Purpose: Implements a bare zip file format packaging
       Implements: Packager
       Type: Module   Library: csmake-packaging
       Phases:
           package - Will build the package
           clean, package_clean - will delete the package
       Options:
           format (not implemented) TODO: support PKZIP, others
           package-version - the version for the package
           maps - points to install map based sections that define
                  how files shoudl be mapped into the package
           result - directory to put the results
                  The package will be called
                      <name>-<version>-<package-version>.zip
       Joinpoints: (see Packaging module)
       Flowcontrol Advice: (see Packaging module)
       Install Map Definitions: (see Packaging module)
       See Also:
           csmake --list-type Packager
    """

    REQUIRED_OPTIONS = ['maps', 'result', 'package-version']

    def _calculateFileNameAndVersioning(self):
        Packager._calculateFileNameAndVersioning(self)
        self.archiveFileName = "%s.zip" % self.fullPackageName
        self.fullPathToArchive = os.path.join(
            self.resultdir,
            self.archiveFileName )

    def _setupArchive(self):
        #TODO: Allow multiple archive formats
        self._ensureDirectoryExists(self.fullPathToArchive)
        try:
            self.archive = zipfile.ZipFile(
                self.fullPathToArchive,
                'w',
                zipfile.ZIP_DEFLATED,
                True )
        except RuntimeError as e:
            self.log.warning("The zipfile will not be compressed because 'zlib' is not available: %s", str(e))
            self.archive = zipfile.ZipFile(
                self.fullPathToArchive,
                'w',
                zipfile.ZIP_STORED,
                True )

    def _placeDirectoryInArchive(self, mapping, sourcePath, archivePath):
        self.log.devdebug("Pushing '%s' to archive as a directory", sourcePath)
        if not os.path.isdir(sourcePath):
            self.log.warning("'%s' is not a directory", sourcePath)
            self._placeFileInArchive(mapping, sourcePath, archivePath)
        children = os.listdir(sourcePath)
        for child in children:
            childSource = os.path.join(
                sourcePath,
                child )
            childArchive = os.path.join(
                archivePath,
                child )
            self._placeFileInArchive(
                mapping,
                childSource,
                childArchive,
                None )

    def _placeFileInArchive(self, mapping, sourcePath, archivePath, aspects):
        if aspects is not None and not self._doArchiveFileAspects(
            mapping,
            sourcePath,
            archivePath,
            aspects ):
            return
        if os.path.isdir(sourcePath):
            self._placeDirectoryInArchive(mapping, sourcePath, archivePath)
        else:
            self._filePlacingInPackage('data',sourcePath,archivePath)
            self.archive.write(sourcePath, archivePath)
