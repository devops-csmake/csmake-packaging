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

class IdentifyConfFilesDebian(PackagerAspect):
    """Purpose: To identify and generate the proper control file for
                conffiles (files generally in /etc) in Debian packages.
       Type: Aspect   Library: csmake-packaging
       Phases: *
       Joinpoints: start - creates the 'conffiles' control entry
                   begin_map - Capture files identified by the
                               file-types option
       Options:
           file-types - The types of files to identify as conffiles
                  example: file-types=<(ELF-Shared)>
                     Will identify all files of type ELF-Shared
           options for the aspect options are expected to include from
                 the aspect dispatcher (using "extraOptions" dictionary):
                 from: <file index>
       References:
         https://www.debian.org/doc/manuals/maint-guide/dother.en.html#conffiles
    """
    def start(self, phase, options, step, stepoptions):
        options['controls'] = 'conffiles'
        self.step = step
        return PackagerAspect.start(self, phase, options, step, stepoptions)

    def _control_conffiles(self, control):
        conffilefile = StringIO.StringIO()
        for key in control.keys():
            relpath = os.path.relpath(key, self.step.archiveRoot)
            if relpath.startswith('../'):
                raise ValueError("Path to conffile didn't land in archive: %s" % key)
            path = '/' + relpath
            conffilefile.write("%s\n" % path)
        info = self.step._createControlFileInfo('conffiles')
        self.step._addFileObjToControl(conffilefile, info)
        conffilefile.close()

    def begin_map(self, phase, options, step, stepoptions):
        conffiles = step._packageControl('conffiles')
        _, lib = os.path.split(options['from']['location'])
        libpaths = options['tos']
        for path in libpaths:
            conffiles[path['relLocation']] = None
        self.log.passed()
        return True
