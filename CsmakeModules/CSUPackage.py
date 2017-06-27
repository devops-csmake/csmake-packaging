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
from CsmakeModules.ZipPackager import ZipPackager
from CsmakeModules.Packager import Packager
import zipfile
import os.path
import StringIO
import hashlib
import json
import sys
import datetime

class CSUPackage(ZipPackager):
    """Purpose: Implements an HP CSU packager
       Implements: ZipPackager
       Type: Module   Library: csmake-packaging
       Phases:
           package - Will build the package
           clean, package_clean - will delete the package
       Options:
           package-version - the version for the package, not the contents
           use-package-version - (OPTIONAL) Set to true to add the package version
                             to the name, would go after version...
           priority - (OPTIONAL) Package's priority , e.g., normal...
                 Default is 'normal'
           maps - points to install map based sections that define
                  how files should be mapped into the package
           result - directory to put the results
             The package will be called:
               <name>_<version>.csu
           Other versioning fields may be used and will be added as:
               +<version text>
             after version.

       Joinpoints Defined: (see Packaging module)
       Flowcontrol Advice: (see Packaging module)
       Install Map Definitions: (see Packaging module)
             NOTE: Top level directory in the mapping will determine  package type:
                 e.g. myfile.tar -(1-1)-> {ROOT}/component/{~~file~~}}
                 would place the file in the "component" contents.
       Install Map Extensions:
            :: map ::
               type - heat, etc
               version - Specific version of component (would be subsumed by metadata)
                   package-version will be used if unspecified
                       even if use-package-version is False)
               TODO: metadata - allow a specific metadata object
                   For now, some of the data will be hard coded:
                       dependencies, created_by, name
       Notes:
           The format supports components in a shallow directory
                 If the installmap gives paths that are not in a shallow
                 format, the packager will truncate the path to force it to
                 conform.
           The first level directory is used as the 'component'
           Allow a metadata to be specified on the installmap entry
           This format was reverse engineered based on examples
                 Testing along with a review of the CSU specifiaction
                 is required to ensure this packager will properly generate
                 CSU's for all given scenarios.
       Phases:
       See Also:
           csmake --list-type Packager
           csmake --list-type ZipPackager
    """

    REQUIRED_OPTIONS = ['maps', 'result', 'package-version']

    METAMAP_METHODS = {
        'name' : Packager.MetadataMapper,
        'title' : Packager.MetadataMapper,
        'created_by' : Packager.MetadataMapper
    }

    METAMAP = {
        'name' : 'name',
        'title' : 'description',
        'created_by' : 'manufacturer'
    }

    CLASSIFIER_MAPS = {
    }

    def _map_path_root(self, value, pathmaps, pathkeymaps):
        pathmaps[value] = ['']
        self.archiveRoot = ''
        pathkeymaps['root'] = ['']

    def _calculateFileNameAndVersioning(self):
        ZipPackager._calculateFileNameAndVersioning(self)
        self.version = self.metadata._getVersionWithFormat(
            ['%(primary)s' ],
            True,
            ['primary'],
            '+' )
        version = self.version
        self.filenameVersion = version
        self.packageVersion = self.options['package-version']
        if 'use-package-version' in self.options \
            and self.options['use-package-version'] == 'True':
                self.filenameFullVersion = "%s-%s" % (
                    version,
                    self.packageVersion )
        else:
            self.filenameFullVersion = version
        self.fullVersion = self.filenameFullVersion
        self.fullPackageName = "%s_%s" % (
            self.packageName,
            self.filenameFullVersion)
        self.archiveFileName =  self.fullPackageName + ".csu"
        self.fullPathToArchive = os.path.join(
            self.resultdir,
            self.archiveFileName )

    def _placeFileInArchive(self, mapping, sourcePath, archivePath, aspects):
        archiveDir, filename = os.path.split(archivePath)
        filenamepart, ext = os.path.splitext(filename)
        componentparts = archiveDir.lstrip('/').split('/',1)
        if len(componentparts) == 2 and len(componentparts[0]) > 0:
            component = componentparts[0]
            if component not in self.contents:
                self.contents[component] = []
        else:
            self.log.warning("File '%s' cannot be stored in the csu", archivePath)
            return None
        archivePath = "%s/%s" % (component, filename)
        ZipPackager._placeFileInArchive(self, mapping, sourcePath, archivePath, aspects)
        createdBy = self.packageMetadata['created_by']
        if 'created_by' in mapping:
            createdBy = mapping['created_by']
        dependencies = []
        if 'dependencies' in mapping:
            dependencies = [ x.strip() for x in mapping['dependencies'].split(',') ]
        #XXX: Defaulting to 'heat' for now....not sure what a good default is
        componentType = 'heat'
        if 'type' in mapping:
            componentType = mapping['type']
        version = self.packageVersion
        if 'version' in mapping:
            version = mapping['version']
        objectname = filename
        if 'object' in mapping:
            objectname = mapping['object']
        componentName = filenamepart
        if 'name' in mapping:
            componentName = mapping['name']
        publication = datetime.datetime.now().strftime("%A, %b %d, %Y %I:%M%p")
        if 'publication_date' in mapping:
            publication = mapping['publication_date']
        self.contents[component].append( {
            'created_by' : createdBy,
            'dependencies' : dependencies,
            'type' : componentType,
            'version' : version,
            'repo_type' : 'file',
            'object' : objectname,
            'file_name' : filename,
            'name' : componentName,
            'publication_date' : publication } )

    def _setupPackage(self):
        self.contents = {}
        ZipPackager._setupPackage(self)

    def _generateMetadataContents(self):
        specedLanguages = self.metadata._getSpecifiedLanguages()
        self.contents['meta_file_version'] = "1.0"
        if 'details' not in self.contents:
            self.contents['details'] = {}
        if 'package' not in self.contents['details']:
            self.contents['details']['package'] = {}
        package = self.contents['details']['package']
        package['name'] = self.fullPackageName
        if 'notes' in self.productMetadata:
            if 'notes' not in package:
                package['notes'] = {}
            for lang in specedLanguages:
                try:
                    local = self.metadata.getDefinitionInLanguage('notes', lang)
                    package['notes'][lang] = local
                except:
                    pass
        priority = 'normal'
        if 'priority' in self.options:
            priority = self.options['priority']
        package['priority'] = priority
        #TODO: Release notes package['release_notes"]
        if 'title' in self.productMetadata:
            if 'title' not in package:
                package['title'] = {}
            for lang in specedLanguages:
                try:
                    local = self.metadata.getDefinitionInLanguage('title', lang)
                    package['title'][lang] = local
                except:
                    pass
        package['version'] = self.packageVersion

        archivePath = "manifest"
        content = json.dumps(self.contents,indent=4, separators=(',', ': '))
        self._filePlacingInPackage("data", None, archivePath, content)
        self.archive.writestr(archivePath, content)

    def _finishPackage(self):
        #Write out all metafiles
        #TODO: Place or generate a DESCRIPTION.rst
        self._generateMetadataContents()
        return ZipPackager._finishPackage(self)
