# <copyright>
# (c) Copyright 2025 Autumn Patterson
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
import hashlib
import os
import os.path
import re
import sys
import tarfile


class HomebrewFormula(Packager):
    """Purpose: Generate a Homebrew formula (.rb) and its source tarball.
       Implements: Packager
       Type: Module   Library: csmake-packaging
       Package Name Format: homebrew
       Phases:
           package - Build the tarball and write the formula
           clean, package_clean - Delete the result directory
       Options:
           package-version - version suffix for the archive name
           maps            - installmap section(s) defining the payload
           result          - directory for output (archive + formula)
           url             - (OPTIONAL) URL where the tarball will be hosted.
                             Embedded in the formula's 'url' field.
                             Default: file://<absolute-path-to-archive>
           install-command - (OPTIONAL) Ruby code for the formula's install
                             block.  May be multi-line; leading/trailing
                             whitespace is preserved.
                             Default: prefix.install Dir["*"]
       Notes:
           The generated formula class name is derived from the package name
           by converting hyphens/underscores to CamelCase.  The formula is
           written to <result>/<name>.rb alongside the archive.

           The 'depends' field in the metadata section is translated into
           'depends_on' lines.  Version constraints are stripped; only the
           package name is preserved.

           For local testing, install with:
               brew install --formula ./<name>.rb

       Install Map Definitions:  See Packager module
       See Also:
           csmake --list-type Packager
    """

    REQUIRED_OPTIONS = ['maps', 'result', 'package-version']
    PACKAGER_NAME_FORMAT = 'homebrew'

    METAMAP_METHODS = {
        'Package'      : Packager.PackageNameMapper,
        '**python-lib' : Packager.AppendingClassifierMapper,
        'License'      : Packager.ClassifierMapper,
    }

    METAMAP = {
        'Package': 'name',
    }

    CLASSIFIER_MAPS = {
        '**python-lib' : Packager.CLASSIFIER_MAPS['**python-lib'],
        'License'      : Packager.CLASSIFIER_MAPS['License'],
    }

    # ------------------------------------------------------------------ #
    # Path mapping                                                         #
    # ------------------------------------------------------------------ #

    def _map_path_python_lib(self, value, pathmaps, pathkeymaps):
        # Homebrew convention: private Python packages live in libexec so
        # they do not pollute the system Python path.
        # Override via default_python-lib option if needed.
        if 'default_python-lib' in self.options:
            path = self.options['default_python-lib'].replace(
                '{', '%(').replace('}', ')s')
            pathmaps[value] = [path]
        else:
            pathmaps[value] = ['%(root)s/libexec']
        pathkeymaps['python-lib'] = pathmaps[value]

    def _map_path_python_script(self, value, pathmaps, pathkeymaps):
        # Homebrew scripts live in bin/ under the formula prefix.
        if 'default_python-script' in self.options:
            path = self.options['default_python-script'].replace(
                '{', '%(').replace('}', ')s')
            pathmaps[value] = [path]
        else:
            pathmaps[value] = ['%(root)s/bin']
        pathkeymaps['python-script'] = pathmaps[value]

    # ------------------------------------------------------------------ #
    # Naming                                                               #
    # ------------------------------------------------------------------ #

    def _formulaClassName(self):
        return ''.join(w.capitalize() for w in re.split(r'[-_]', self.packageName))

    # ------------------------------------------------------------------ #
    # Packager overrides                                                   #
    # ------------------------------------------------------------------ #

    def _calculateFileNameAndVersioning(self):
        Packager._calculateFileNameAndVersioning(self)
        self.archiveFileName = '%s.tar.gz' % self.fullPackageName
        self.fullPathToArchive = os.path.join(self.resultdir, self.archiveFileName)
        self.formulaFileName = '%s.rb' % self._formulaClassName().lower()
        self.fullPathToFormula = os.path.join(self.resultdir, self.formulaFileName)

    def _setupArchive(self):
        self._ensureDirectoryExists(self.fullPathToArchive)
        self.archive = tarfile.open(self.fullPathToArchive, 'w:gz')

    def _map_path_root(self, value, pathmaps, pathkeymaps):
        pathmaps[value] = [self.fullPackageName]
        self.archiveRoot = self.fullPackageName
        pathkeymaps['root'] = [self.archiveRoot]

    def _finishPackage(self):
        if not self._finishArchive():
            return False
        self._writeFormula()
        return True

    # ------------------------------------------------------------------ #
    # Formula generation                                                   #
    # ------------------------------------------------------------------ #

    def _sha256(self, path):
        h = hashlib.sha256()
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                h.update(chunk)
        return h.hexdigest()

    def _dependsOn(self):
        depends_raw = self.productMetadata.get('depends', '')
        lines = []
        for entry in re.split(r'[,\n]', depends_raw):
            entry = entry.strip()
            if not entry:
                continue
            name = re.split(r'\s+', entry)[0].strip('()')
            if name:
                lines.append('  depends_on "%s"' % name)
        return lines

    def _installLines(self):
        custom = self.options.get('install-command', '')
        if custom:
            return ['    ' + line for line in custom.splitlines()]
        return ['    prefix.install Dir["*"]']

    def _writeFormula(self):
        class_name = self._formulaClassName()
        desc      = self.productMetadata.get('description', '').replace('\n', ' ').strip()
        homepage  = self.productMetadata.get('url', '').strip()
        url       = self.options.get(
            'url', 'file://%s' % os.path.abspath(self.fullPathToArchive))
        sha256    = self._sha256(self.fullPathToArchive)
        dep_lines = self._dependsOn()
        inst_lines = self._installLines()

        parts = [
            'class %s < Formula' % class_name,
            '  desc "%s"'       % desc.replace('"', '\\"'),
            '  homepage "%s"'   % homepage,
            '  url "%s"'        % url,
            '  sha256 "%s"'     % sha256,
            '  version "%s"'    % self.version,
            '',
        ]
        if dep_lines:
            parts.extend(dep_lines)
            parts.append('')
        parts += [
            '  def install',
        ] + inst_lines + [
            '  end',
            'end',
            '',
        ]

        with open(self.fullPathToFormula, 'w') as fh:
            fh.write('\n'.join(parts))
        self.log.info("Formula written: %s", self.fullPathToFormula)
