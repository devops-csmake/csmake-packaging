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

class AddPackageScript(PackagerAspect):
    """Purpose: Generate a maintainer script for a package
       Type: Aspect   Library: csmake-packaging
       Phases: *
       Joinpoints: mapping_complete - Creates a maintainer file
       Flags: name - name of the maintainer script
              script - the body of the script
       Notes: Intended to crosscut Packager implementations.
    """

    REQUIRED_OPTIONS = ['name', 'script']

    def mapping_complete(self, phase, options, step, stepoptions):
        maint = step._packageControl(options['name'])
        if 'text' not in maint:
            maint['text'] = []
        maint['text'].append(options['script'])
        self.log.passed()
        return True
