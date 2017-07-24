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
from distutils.command.install import install

#class csmakeinstaller(install):
#    pass

class PythonPackage(Packager):
    """Purpose: ++++FUTURE++++
                To define a python module for delivery
                NOTE --CURRENTLY NOT IMPLEMENTED--
       Implements: Packager
       Type: Module   Library: csmake-packaging
       Package Name Format: python
       Phases:
           package - Will build a python package based on
                   passing the usual flags as parameters to setup
           clean - will delete the package
       Options:
           Use the normal python setup.py keywords.
           Common keywords:
               name - name of the package
               version - version of the package
               description - description of the package
               author - author of the package
               keywords - keywords used (space delimited)
               install_requires - list of packages required
               packages -
       See Also:
           csmake --list-type Packager
    """

    PACKAGER_NAME_FORMAT = 'python'

    def package(self, options):
        savedArgv = sys.argv.copy()
        try:
            sys.argv = ['setup.py']
            sys.argv.extend(options['command'].strip().split())

            #Get the package information
            setupoptions = {}

            package = options['package'].strip()
            result = self.engine.launchStep(package)
            if result is None or not result._didPass():
                self.log.failed()
                self.log.error("The package definition '%s' failed to execute")
                return None

            import distutils.core
            #Ensure we get a fresh run at setup
            reload(distutils.core)

            #Do the command
            distutils.core.setup(**setupoptions)
        finally:
            sys.argv = savedArgv
