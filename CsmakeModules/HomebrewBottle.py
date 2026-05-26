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
from CsmakeModules.HomebrewFormula import HomebrewFormula
import os
import struct


class HomebrewBottle(HomebrewFormula):
    """Purpose: Generate a Homebrew bottle archive and its companion formula.
       Implements: Packager
       Type: Module   Library: csmake-packaging
       Package Name Format: homebrew-bottle
       Phases:
           package - Build the bottle archive and write the formula
           clean, package_clean - Delete the result directory
       Options:
           package-version - version suffix for the archive name
           maps            - installmap section(s) defining the payload
           result          - directory for output (archive + formula)
           os-tag          - Homebrew platform tag for this bottle, e.g.
                             arm64_sonoma, arm64_sequoia, sonoma, ventura,
                             monterey, or 'all' for arch-independent packages.
           cellar          - (OPTIONAL) Homebrew cellar relocation flag.
                             Default: :any_skip_relocation  (correct for
                             scripts and pure-Python packages that contain
                             no compiled binaries with embedded paths).
                             Use :any for packages that can live anywhere in
                             the cellar, or an absolute path string if the
                             package must be installed at a specific prefix.
           url             - (OPTIONAL) URL of the source tarball for
                             building from source when no bottle matches the
                             user's platform.  Embed the HomebrewFormula
                             tarball URL here.  If omitted the generated
                             formula is bottle-only (source build disabled).
           install-command - (OPTIONAL) Ruby install block body; same
                             semantics as HomebrewFormula.
       Notes:
           A Homebrew bottle is a pre-built binary archive that Homebrew
           unpacks directly into the cellar, bypassing the formula's install
           block.  The archive layout follows Homebrew's cellar convention:

               <name>/<version>/bin/...
               <name>/<version>/lib/...
               etc.

           The companion formula carries a 'bottle do' block whose sha256
           covers the finished archive.  Publish the bottle archive somewhere
           curl-accessible (GitHub Releases, OpenStack Swift, etc.) and
           update the formula's 'url' field in your tap accordingly.

           To add multiple platform bottles to a single formula, run this
           module once per platform (different os-tag values) and merge the
           resulting 'bottle do' blocks by hand, or with a subsequent
           csmake step.

       Install Map Definitions:  See Packager module
       See Also:
           csmake --list-type Packager
           HomebrewFormula
    """

    REQUIRED_OPTIONS = ['maps', 'result', 'package-version', 'os-tag']
    PACKAGER_NAME_FORMAT = 'homebrew-bottle'

    # ------------------------------------------------------------------ #
    # Naming / versioning                                                  #
    # ------------------------------------------------------------------ #

    def _calculateFileNameAndVersioning(self):
        HomebrewFormula._calculateFileNameAndVersioning(self)
        os_tag = self.options['os-tag']
        # Homebrew bottle naming: name--version.os_tag.bottle.tar.gz
        # (note the double dash between name and version)
        self.archiveFileName = '%s--%s.%s.bottle.tar.gz' % (
            self.packageName, self.version, os_tag)
        self.fullPathToArchive = os.path.join(self.resultdir, self.archiveFileName)
        self.formulaFileName   = '%s.rb' % self._formulaClassName().lower()
        self.fullPathToFormula = os.path.join(self.resultdir, self.formulaFileName)

    # ------------------------------------------------------------------ #
    # Path mapping                                                         #
    # ------------------------------------------------------------------ #

    def _map_path_root(self, value, pathmaps, pathkeymaps):
        # Bottle cellar root: <name>/<version>/ inside the archive.
        # python-lib and python-script are inherited from HomebrewFormula
        # and resolve relative to %(root)s, so they land correctly at
        # <name>/<version>/libexec/... and <name>/<version>/bin/...
        cellar_root = '%s/%s' % (self.packageName, self.version)
        pathmaps[value] = [cellar_root]
        self.archiveRoot = cellar_root
        pathkeymaps['root'] = [cellar_root]

    # ------------------------------------------------------------------ #
    # Package assembly                                                     #
    # ------------------------------------------------------------------ #

    def _finishPackage(self):
        if not self._finishArchive():
            return False
        self._writeBottleFormula()
        return True

    def _writeBottleFormula(self):
        class_name  = self._formulaClassName()
        os_tag      = self.options['os-tag']
        cellar      = self.options.get('cellar', ':any_skip_relocation')
        sha256_val  = self._sha256(self.fullPathToArchive)
        desc        = self.productMetadata.get('description', '').replace('\n', ' ').strip()
        homepage    = self.productMetadata.get('url', '').strip()
        source_url  = self.options.get('url', '').strip()
        dep_lines   = self._dependsOn()
        inst_lines  = self._installLines()

        parts = [
            'class %s < Formula' % class_name,
            '  desc "%s"'      % desc.replace('"', '\\"'),
            '  homepage "%s"'  % homepage,
        ]

        if source_url:
            parts += [
                '  url "%s"'     % source_url,
                '  version "%s"' % self.version,
            ]
        else:
            # Bottle-only: no source build available.
            parts += [
                '  # No source URL — install via bottle only.',
                '  version "%s"' % self.version,
            ]

        parts += [
            '',
            '  bottle do',
            '    sha256 cellar: %s, %s: "%s"' % (cellar, os_tag, sha256_val),
            '  end',
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
        self.log.info("Bottle formula written: %s", self.fullPathToFormula)
        self.log.info("Bottle archive:         %s", self.fullPathToArchive)
        self.log.info("  os-tag : %s", os_tag)
        self.log.info("  cellar : %s", cellar)
        self.log.info("  sha256 : %s", sha256_val)
