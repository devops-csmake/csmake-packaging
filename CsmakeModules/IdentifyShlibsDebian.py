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
from CsmakeModules.PackagerAspect import PackagerAspect
import os.path
import StringIO
import tarfile

class IdentifyShlibsDebian(PackagerAspect):
    """Purpose: To identify and generate the proper control file for
                shlibs in Debian packages.
       Type: Aspect   Library: csmake-packaging
       Description: The files intended to be shared libraries must be
                listed in a shlibs control file.
                Assumes library files have been added of form:
                <path>/<library name>.so.<major>.<minor>.<patch>
                Will create links for:
                <path>/<library name>.so.<major>
                <path>/<library name>.so
       Phases: *
       Joinpoints: start - Creates the control file entry
                   begin_map - Collects information for an entry
                   mapping_complete - creates the necessary softlinks
       Flags:
           file-types - The types of files to identify as shlibs
                  example: file-types=<(ELF-Shared)>
                     Will identify all files of type ELF-Shared
           options for the aspect options are expected to include from
                 the aspect dispatcher (using "extraOptions" dictionary):
                 from: <file index>
       References:
           https://www.debian.org/doc/debian-policy/ch-sharedlibs.html#s-shlibs"""
    def start(self, phase, options, step, stepoptions):
        options['controls'] = 'shlibs'
        self.step = step
        return PackagerAspect.start(self, phase, options, step, stepoptions)

    def _control_shlibs(self, control):
        shlibsfile = StringIO.StringIO()
        for key, values in control.iteritems():
            ver = values[0]
            if ver == '-1':
                ver = '0'
            shlibsfile.write("%s %s %s\n" % (key, ver, values[1]))
        info = self.step._createControlFileInfo('shlibs')
        self.step._addFileObjToControl(shlibsfile, info)
        shlibsfile.close()

    def begin_map(self, phase, options, step, stepoptions):
        shlibs = step._packageControl('shlibs')
        _, lib = os.path.split(options['from']['location'])
        libpath = options['tos']
        libparts = lib.split('.')
        previous = ''
        vers = -1
        name = None
        libparts.reverse()
        self.log.devdebug("Libparts reversed: %s", str(libparts))
        for i, part in enumerate(libparts):
            try:
                previous = part
                self.log.devdebug("Processing part: %s", part)
                vers = int(part)
            except ValueError:
                if len(libparts) <= i:
                    self.log.error('Appropriate library name and version could not be recovered')
                    self.log.error("   %s", lib)
                    self.log.failed()
                    return False
                nameparts = libparts[i+1:]
                nameparts.reverse()
                name = '.'.join(nameparts)
                self.log.devdebug("Name of lib is: %s  Version is: %s", name, str(vers))
                break
        if name is None:
            self.log.error("Appropriate library name and version could not be recovered")
            self.log.error("    %s", lib)
            self.log.failed()
            return False
        shlibs[name] = (str(vers), step.packageMetadata['Package'], libpath, lib)
        self.log.passed()
        return True

    def mapping_complete(self, phase, options, step, stepoptions):
        #Create the soft links
        shlibs = step._packageControl('shlibs')
        for key, values in shlibs.iteritems():
            if values[0] =='-1':
                #Not a normal library, don't softlink it
                continue
            libraryName = "%s.so" % key
            for path in values[2]:
                fullLink = path['relLocation']
                linkpath, linkname = os.path.split(fullLink)
                linkpath = os.path.relpath(linkpath, step.archiveRoot)
                name = "%s.%s" % (
                    os.path.join(
                        linkpath,
                        libraryName),
                    values[0] )
                linkinfo = step._createArchiveFileInfo(name)
                linkinfo.linkname = values[3]
                linkinfo.type = tarfile.SYMTYPE
                linkinfo.uid = 0
                linkinfo.uname = 'root'
                linkinfo.gid = 0
                linkinfo.gname = 'root'
                step._addInfoToArchive(linkinfo)

                name = os.path.join(
                    linkpath,
                    libraryName)
                linkinfo = step._createArchiveFileInfo(name)
                linkinfo.linkname = values[3]
                linkinfo.type = tarfile.SYMTYPE
                linkinfo.uid = 0
                linkinfo.uname = 'root'
                linkinfo.gid = 0
                linkinfo.gname = 'root'
                step._addInfoToArchive(linkinfo)
        self.log.passed()
        return True
