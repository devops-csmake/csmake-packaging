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
import tarfile

class CreateSymlinks(PackagerAspect):
    """Purpose: To generate extra, one-off symlinks for a package.
       Type: Aspect   Library: csmake-packaging
       Phases: *
       Joinpoints: mapping_complete - Creates symlinks in the package
       Flags: symlinks - newline delimited list of maps from one place
                         to another in the archive
                 Format: <link> -> <original>
              {root} will give the root of the archive
              <link> should always be a full path from root
                     ({root}/ is optional)
           (currently - uid/gid is root)"""

    REQUIRED_OPTIONS = ['symlinks']

    def mapping_complete(self, phase, options, step, stepoptions):
        symlinksList = options['symlinks'].split('\n')
        for link in symlinksList:
            link = link.strip()
            if len(link) == 0:
                continue
            parts = link.split('->')
            if len(parts) != 2:
                self.log.error("(%s) is an invalid symlink definition", link)
                self.log.failed()
                return False
            lhs = parts[0].strip()
            rhs = parts[1].strip()
            lhs = lhs.lstrip('{root}/')
            if rhs.startswith('{root}/'):
                rhs = os.path.join(
                    step.archiveRoot,
                    rhs.lstrip('{root}/') )
            linkinfo = step._createArchiveFileInfo(lhs)
            linkinfo.uid = 0
            linkinfo.uname = 'root'
            linkinfo.gid = 0
            linkinfo.gname = 'root'
            linkinfo.type = tarfile.SYMTYPE
            linkinfo.linkname = rhs
            step._addInfoToArchive(linkinfo)
        self.log.passed()
        return True
