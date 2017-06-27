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
import subprocess

class UndefineRpathForPackaging(PackagerAspect):
    """Purpose: To remove (in-place) rpath definitions from ELF files
       Type: Aspect   Library: csmake-packaging
       Phases: *
       Joinpoints: begin_map - removes rpath from matching files
       Flags:
           file-types - The types of mapped files to undef as specified in
                        an install map via file tracking
                  example: file-types=<(ELF-Shared)>
                     Will undef all files of type ELF-Shared
           options for the aspect options are expected to include from
                 the aspect dispatcher (using "extraOptions" dictionary):
                 from: <file index>
       Dependencies: chrpath
           sudo apt-get install chrpath
    """

    def begin_map(self, phase, options, step, stepoptions):
        stripTarget = options['from']['location']
        self.log.debug("Removing rpath from %s", stripTarget)
        result = subprocess.call(
            [ 'chrpath', '-d', stripTarget ],
            stdout=self.log.out(),
            stderr=self.log.err() )
        if result == 0:
            self.log.passed()
            return True
        else:
            self.log.failed()
            return False
