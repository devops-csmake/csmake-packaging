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

class DebianLintianOverrides(PackagerAspect):
    """Purpose: To generate a lintian override file for a debian package
       Type: Aspect   Library: csmake-packaging
       Phases: *
       Joinpoints: start - sets up the 'lintian' control file
                   mapping_complete - writes the lintian file.
       Options: overrides - newline delimited list of overrides.
       [[<package>][ <archlist>][ <type>]: ]<lintian-tag>[ [*]<lintian-info>[*]]
           <package> is the package name
           <archlist> is an architecture list
             (see Architecture specific overrides for more info)
           <type> is one of binary, udeb and source,
           <lintian-info> is all additional information provided by Lintian except for the tag.
       Notes:
           Some errors are not overridable without changing a profile as well
           https://lintian.debian.org/manual/section-2.5.html
       References:
           https://lintian.debian.org/manual/section-2.4.html
    """

    REQUIRED_OPTIONS = ['overrides']

    def start(self, phase, options, step, stepoptions):
        options['controls'] = 'lintian'
        self.step = step
        return PackagerAspect.start(self, phase, options, step, stepoptions)

    def _control_lintian(self, control):
        lintianfile = StringIO.StringIO()
        if 'overrides' in control:
            for override in control['overrides']:
                lintianfile.write("%s\n" %  override)
        lintianFilename = os.path.join(
            'usr',
            'share',
            'lintian',
            'overrides',
            self.step.packageMetadata['Package'] )

        info = self.step._createArchiveFileInfo(lintianFilename)
        info.uid = 0
        info.uname = 'root'
        info.gid = 0
        info.gname = 'root'
        info.mode = self.step._modeInt('0644')
        self.step._addFileObjToArchive(lintianfile, info)
        lintianfile.close()

    def mapping_complete(self, phase, options, step, stepoptions):
        lintians = step._packageControl('lintian')
        overrideList = options['overrides'].split('\n')
        if 'overrides' not in lintians:
            lintians['overrides'] = []
        for override in overrideList:
            override = override.strip()
            if len(override) == 0:
                continue
            lintians['overrides'].append("%s: %s" % (
                step.packageMetadata['Package'],
                override ) )
        self.log.passed()
        return True
