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

class WheelPackage(ZipPackager):
    """Purpose: Implements a PEP-427 compliant wheel packaging
       Implements: ZipPackager
       Type: Module   Library: csmake-packaging
       Package Name Format: python
       Description: The implementation for WheelPackage crates a wheel using
           the ZipPackager implementation.
                 - see: --list-type=ZipPackager  and  --list-type=Packager
                   for more information
       Phases:
           package - Will build the package
           clean, package_clean - will delete the package
       Options:
           top-level - (OPTIONAL) Directory of the top level package directory
                 Default is to use the package name
           package-version - the version for the package
           use-package-version - The package version will be used
                  as a build number (see PEP-0427)
           maps - points to install map based sections that define
                  how files shoudl be mapped into the package
           arch - (OPTIONAL) Specify the architecture (default is any)
             follow the PEP-425 Spec for values
           abi - (OPTIONAL) Specify the python abi (default is none)
             follow the PEP-425 Spec for values
           result - directory to put the results
             The package will be called:
               <name>-<version>-<python tag>-<abi>-<platform>.whl
             If use-package-version is True:
               <name>-<version>-<package-version>-<python tag>-<abi>-<arch>.whl
             NOTE: The "python tag" will be derived from the metadata classifiers.

       Joinpoints: (see Packaging module)
       Flowcontrol Advice: (see Packaging module)
       Install Map Definitions: (see Packaging module)

       Notes:
           Use of this module only makes sense with a python distribution
           Metadata classifiers have to include some flavor of:
           Programming Language :: Python

             ":: 2" specified with ":: 2.7" will be taken to mean py2 compatible
             ":: 2 :: Only" specified with ":: 3 :: Only" will ignore "Only"
                 and specify a py2.py3 package, as the actual meaning is void
                 i.e., multiple uses of 'Only' are invalid.
        See Also:
            csmake --list-type ZipPackager
            csmake --list-type Packager
    """

    REQUIRED_OPTIONS = ['maps', 'result', 'package-version']
    PACKAGER_NAME_FORMAT = 'python'

    WHEEL_GENERATOR = "csmake (WheelPackage 0.1.0)"

    METAMAP_METHODS = {
        'Name' : Packager.PackageNameMapper,
        'Summary' : Packager.MetadataMapper,
        'License' : Packager.METAMAP_METHODS['License'],
        'Keywords' : Packager.MetadataMapper,
        'Home-page' : Packager.MetadataMapper,
        '**python-tag' : Packager.AppendingClassifierMapper
    }

    METAMAP = {
        'Name' : 'name',
        'Summary' : 'description',
        'Keywords' : 'keywords',
        'Home-page' : 'homepage'
    }

    CLASSIFIER_MAPS = {
        '**python-tag' : {
            '' : (sys.maxint, None),
            'Programming Language :: Python' : (9, 'py2.py3'),
            'Programming Language :: Python :: 2' : (4, 'py2'),
            'Programming Language :: Python :: 2.3' : (5, 'py23'),
            'Programming Language :: Python :: 2.4' : (5, 'py24'),
            'Programming Language :: Python :: 2.5' : (5, 'py25'),
            'Programming Language :: Python :: 2.6' : (5, 'py26'),
            'Programming Language :: Python :: 2.7' : (5, 'py27'),
            'Programming Language :: Python :: 2 :: Only' : (1, 'py2'),
            'Programming Language :: Python :: 3' : (4, 'py3'),
            'Programming Language :: Python :: 3.0' : (5, 'py30'),
            'Programming Language :: Python :: 3.1' : (5, 'py31'),
            'Programming Language :: Python :: 3.2' : (5, 'py32'),
            'Programming Language :: Python :: 3.3' : (5, 'py33'),
            'Programming Language :: Python :: 3.4' : (5, 'py34'),
            'Programming Language :: Python :: 3 :: Only' : (1, 'py3')
        },
        'License' : Packager.CLASSIFIER_MAPS['License']
    }

    def _map_path_root(self, value, pathmaps, pathkeymaps):
        pathmaps[value] = ['%s.data/data/system/' % self.packageName]
        self.archiveRoot = ''
        pathkeymaps['root'] = []

    def _map_path_python_lib(self, value, pathmaps, pathkeymaps):
        pathmaps[value] = ['']
        pathkeymaps['python-lib'] = ['']

    def _map_path_python_script(self, value, pathmaps, pathkeymaps):
        pathmaps[value] = ['%s.data/scripts' % self.packageName]
        pathkeymaps['python-script'] = ['%s.data/scripts' % self.packageName]

    def _calculateFileNameAndVersioning(self):
        ZipPackager._calculateFileNameAndVersioning(self)
        self.version = self.metadata._getVersionWithFormat(
            ['%(epoch)s!', '%(primary)s' ],
            True,
            ['epoch', 'primary'],
            '+' )
        version = self.version
        self.filenameVersion = version
        self.packageVersion = self.options['package-version']
        self.usePackageVersion = 'use-package-version' in self.options \
                    and self.options['use-package-version'] == 'True'
        if self.usePackageVersion:
            self.filenameFullVersion = "%s-%s" % (
                version,
                self.packageVersion )
        else:
            self.filenameFullVersion = version
        self.fullVersion = self.filenameFullVersion
        if '**python-tag' not in self.packageMetadata:
            self.log.error("One or more flavors of 'Programming Language :: Python' must be in the metadata's classifiers")
            self.log.failed()
            raise ValueError("Python packaging required")

        pytags = self.packageMetadata['**python-tag']
        pytags.sort()
        initprio, _ = pytags[0]
        tags = []
        for priority, tag in pytags:
            if priority != initprio:
                break
            tags.append(tag)
        tags.sort()
        pythonTag = '.'.join(tags)
        abi = 'none'
        arch = 'any'
        if 'abi' in self.options:
            abi = self.options['abi']
        if 'arch' in self.options:
            arch = self.options['arch']
        self.wheelTag = '%s-%s-%s' % (
            pythonTag,
            abi,
            arch )
        self.fullPackageName = "%s-%s-%s.whl" % (
            self.packageName,
            self.filenameFullVersion,
            self.wheelTag )
        self.distinfoName = "%s-%s.dist-info" % (
            self.packageName,
            self.version )
        self.archiveFileName =  self.fullPackageName
        self.fullPathToArchive = os.path.join(
            self.resultdir,
            self.archiveFileName )

    def _filePlacingInPackage(self, archive, sourcePath, archivePath, contents=None):
        if sourcePath is not None:
            with open(sourcePath) as content:
                size = self._filesize(content)
                shasum = self._PEP427Encode(self._fileSHA256(content))
                self.contents.append("%s,sha256=%s,%s" % (
                    archivePath.lstrip('/'),
                    shasum,
                    size))
        elif contents is not None:
            size = len(contents)
            shacalc = hashlib.sha256()
            shacalc.update(contents)
            shasum = self._PEP427Encode(shacalc.hexdigest())
            self.contents.append("%s,sha256=%s,%s" % (
                archivePath,
                shasum,
                size))

    def _setupPackage(self):
        self.contents = []
        ZipPackager._setupPackage(self)

    def _generateMetadataContents(self):
        result = ['Metadata-Version: 2.0']
        jsonResult = {'metadata_version':'2.0'}
        for key, value in self.packageMetadata.iteritems():
            if key.startswith('**'):
                continue
            result.append('%s: %s' % (key, value))
            jsonResult[key.lower()] = value
        if 'keywords' in jsonResult:
            jsonResult['keywords'] = jsonResult['keywords'].split()
        result.append('Version: %s' % self.version)
        jsonResult['version'] = self.version
        jsonResult['generator'] = self.WHEEL_GENERATOR
        authors = []
        jsonResult['extensions'] = {}
        if 'copyrights' in self.productMetadata:
            for copy in self.productMetadata['copyrights']:
                holder = (copy['holder'],None)
                if '<' in holder:
                    holderparts = holder.split('<')
                    holderName = holderparts[0]
                    email = holderparts[1].rstrip().rstrip('>')
                    holder = (holderName, email)
                authors.append(holder)
            result.append("Author: %s" % ', '.join([x[0] for x in authors]))
            emails = [x[1] for x in authors if x[1] is not None]
            if len(emails) > 0:
                result.append('Author-email: %s' % ', '.join(emails))
            if 'python.details' not in jsonResult['extensions']:
                jsonResult['extensions']['python.details'] = {}
            if 'contacts' not in jsonResult['extensions']['python.details']:
                jsonResult['extensions']['python.details']['contacts'] = []
            jsonContacts = jsonResult['extensions']['python.details']['contacts']
            for author in authors:
                if author[1] is None:
                    jsonContacts.append({'name':author[0], 'role':'author'})
                else:
                    jsonContacts.append({'name':author[0], 'email':author[1], 'role':'author'})
        #TODO: Add python.details/document_names: {'description' : 'DESCRIPTION.rst'}
        #TODO: Add python.exports/console_scripts
        #TODO: Add python.commands/wrap_console
        for classifier in self.classifiers:
            result.append('Classifier: %s' % classifier)
        jsonResult['classifiers'] = self.classifiers
        arch = 'any'
        if 'arch' in self.options:
            arch = self.options['arch']
        archivePath = os.path.join(
            self.distinfoName,
            "METADATA" )
        content = '\n'.join(result)
        #TODO: Append DESCRIPTION.rst
        self._filePlacingInPackage("data", None, archivePath, content)
        self.archive.writestr(archivePath, content)
        archivePath = os.path.join(
            self.distinfoName,
            "metadata.json" )
        content = json.dumps(jsonResult)
        self._filePlacingInPackage("data", None, archivePath, content)
        self.archive.writestr(archivePath, content)

    def _generateWheelFileContents(self):
        result = ['Wheel-Version: 1.0', 'Generator: %s' % self.WHEEL_GENERATOR]
        purelib = 'true'
        if ('arch' in self.options and self.options['arch'] != 'any') \
           or ('abi' in self.options and self.options['abi'] != 'none'):
            purelib = 'false'
        result.append('Root-Is-Purelib: %s' % purelib)
        result.append("Tag: %s" % self.wheelTag)
        if self.usePackageVersion:
            result.append("Build: %s", self.options['package-version'])
        archivePath = os.path.join(
            self.distinfoName,
            "WHEEL" )
        content = '\n'.join(result)
        self._filePlacingInPackage("data", None, archivePath, content)
        self.archive.writestr(archivePath, content)

    def _generateTopLevelFile(self):
        toplevel = "%s\n" % self.packageName
        if 'top-level' in self.options:
            toplevel = "%s\n" % self.options['top-level']
        archivePath = os.path.join(
            self.distinfoName,
            "top_level.txt" )
        self._filePlacingInPackage("data", None, archivePath, toplevel)
        self.archive.writestr(archivePath, toplevel)

    def _generateRecord(self):
        #In order to ensure all other files are captured,
        #This needs to be the last file generated.
        record = '\n'.join(self.contents)
        archivePath = os.path.join(
            self.distinfoName,
            "RECORD" )
        #Do not call filePlacingInPackage -it will try to add this file to the
        #    contents of this file.
        self.archive.writestr(archivePath, record)

    def _finishPackage(self):
        #Write out all metafiles
        #TODO: Place or generate a DESCRIPTION.rst
        self._generateMetadataContents()
        self._generateWheelFileContents()
        self._generateTopLevelFile()

        #Write out RECORD file
        #Last step
        self._generateRecord()
        return ZipPackager._finishPackage(self)
