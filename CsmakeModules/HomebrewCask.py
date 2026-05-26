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


class HomebrewCask(Packager):
    """Purpose: Generate a Homebrew cask (.rb) for a pre-built binary artifact.
       Implements: Packager
       Type: Module   Library: csmake-packaging
       Package Name Format: homebrew-cask
       Phases:
           package - Write the cask .rb file
           clean, package_clean - Delete the result directory
       Options:
           package-version - version suffix (used for naming consistency;
                             not embedded in the cask itself)
           result          - directory for the output .rb file
           url             - (REQUIRED) URL where the artifact will be
                             hosted and downloaded by Homebrew.
           artifact        - (OPTIONAL) Local path to the artifact file.
                             Used to compute the sha256 checksum.
                             If omitted, provide sha256 directly.
           sha256          - (OPTIONAL) Pre-computed sha256 of the artifact.
                             Ignored when 'artifact' is provided.
                             Defaults to a placeholder string when neither
                             artifact nor sha256 is given.
           artifact-type   - (OPTIONAL) How Homebrew should install the
                             artifact.  Choices:
                               pkg    - macOS .pkg installer (default)
                               app    - .app bundle (from DMG or ZIP)
                               binary - single command-line executable
           maps            - (OPTIONAL) installmap section(s).  Not used
                             for file staging but may be provided so that
                             a cask step can appear inline in a command
                             sequence that also runs other packagers.
       Notes:
           A Homebrew Cask distributes binary-only macOS software — .app
           bundles, .pkg installers, DMG images, and similar artifacts that
           are not built from source.  A cask is simply a Ruby stanza that
           tells Homebrew where to download the artifact and how to install
           it.  No compilation happens on the user's machine.

           The most natural workflow is to run ApplePackage first to produce
           a .pkg, then run HomebrewCask pointing 'artifact' at that .pkg
           and 'url' at wherever the .pkg will be published:

               [ApplePackage@apple-csmake]
               ...
               result=%(RESULTS)s/applepkg

               [HomebrewCask@cask-csmake]
               result=%(RESULTS)s/homebrew
               url=https://github.com/example/releases/download/%(version)s/csmake-%(version)s.pkg
               artifact=%(RESULTS)s/applepkg/csmake-%(version)s-1.0.pkg

           The generated cask lives at <result>/<name>.rb and is ready to
           publish to a Homebrew tap (a GitHub repo named homebrew-<tap>).

       Install Map Definitions:  See Packager module
       See Also:
           csmake --list-type Packager
           ApplePackage, HomebrewFormula, HomebrewBottle
    """

    REQUIRED_OPTIONS = ['result', 'package-version', 'url']
    PACKAGER_NAME_FORMAT = 'homebrew-cask'

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
    # Naming / versioning                                                  #
    # ------------------------------------------------------------------ #

    def _calculateFileNameAndVersioning(self):
        Packager._calculateFileNameAndVersioning(self)
        self.caskFileName  = '%s.rb' % self.packageName
        self.fullPathToCask = os.path.join(self.resultdir, self.caskFileName)

    # ------------------------------------------------------------------ #
    # Path mapping — minimal; cask does not stage files                   #
    # ------------------------------------------------------------------ #

    def _map_path_root(self, value, pathmaps, pathkeymaps):
        pathmaps[value] = [self.resultdir]
        self.archiveRoot = self.resultdir
        pathkeymaps['root'] = [self.resultdir]

    def _map_path_python_lib(self, value, pathmaps, pathkeymaps):
        pathmaps[value] = [self.resultdir]
        pathkeymaps['python-lib'] = pathmaps[value]

    def _map_path_python_script(self, value, pathmaps, pathkeymaps):
        pathmaps[value] = [self.resultdir]
        pathkeymaps['python-script'] = pathmaps[value]

    # ------------------------------------------------------------------ #
    # Packager overrides — no archive, no file staging                    #
    # ------------------------------------------------------------------ #

    def _setupArchive(self):
        self._ensureDirectoryExists(self.fullPathToCask)
        self.archive    = None
        self.archiveRoot = self.resultdir

    def _doMaps(self):
        # maps is optional for cask — skip if not provided.
        if 'maps' not in self.options or not self.options['maps'].strip():
            return
        Packager._doMaps(self)

    def _placeFileInArchive(self, mapping, sourcePath, archivePath, aspects):
        pass  # Cask describes where to download an artifact; no staging.

    def _finishArchive(self):
        return True  # No archive to close.

    def _finishPackage(self):
        self._writeCask()
        return True

    # ------------------------------------------------------------------ #
    # Checksum                                                             #
    # ------------------------------------------------------------------ #

    def _sha256(self, path):
        h = hashlib.sha256()
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                h.update(chunk)
        return h.hexdigest()

    def _resolveChecksum(self):
        artifact = self.options.get('artifact', '').strip()
        if artifact:
            if not os.path.exists(artifact):
                self.log.warning(
                    "HomebrewCask: artifact path not found: %s", artifact)
                self.log.warning(
                    "  sha256 will be a placeholder — update before publishing.")
                return 'PLACEHOLDER_SHA256_UPDATE_BEFORE_PUBLISHING'
            return self._sha256(artifact)
        provided = self.options.get('sha256', '').strip()
        if provided:
            return provided
        self.log.warning(
            "HomebrewCask: neither 'artifact' nor 'sha256' was provided.")
        self.log.warning(
            "  sha256 will be a placeholder — update before publishing.")
        return 'PLACEHOLDER_SHA256_UPDATE_BEFORE_PUBLISHING'

    # ------------------------------------------------------------------ #
    # Cask generation                                                      #
    # ------------------------------------------------------------------ #

    def _writeCask(self):
        name          = self.packageName
        version       = self.version
        desc          = self.productMetadata.get('description', '').replace('\n', ' ').strip()
        homepage      = self.productMetadata.get('url', '').strip()
        url           = self.options['url'].strip()
        sha256_val    = self._resolveChecksum()
        artifact_type = self.options.get('artifact-type', 'pkg').strip()

        # Derive artifact filename from the url (last path component).
        artifact_name = url.rstrip('/').split('/')[-1]

        lines = [
            'cask "%s" do' % name,
            '  version "%s"' % version,
            '  sha256 "%s"'  % sha256_val,
            '',
            '  url "%s"'      % url,
            '  name "%s"'     % name,
            '  desc "%s"'     % desc.replace('"', '\\"'),
            '  homepage "%s"' % homepage,
            '',
        ]

        if artifact_type == 'pkg':
            lines.append('  pkg "%s"' % artifact_name)
        elif artifact_type == 'app':
            lines.append('  app "%s"' % artifact_name)
        elif artifact_type == 'binary':
            lines.append('  binary "%s"' % artifact_name)
        else:
            self.log.warning(
                "HomebrewCask: unknown artifact-type %r — defaulting to pkg",
                artifact_type)
            lines.append('  pkg "%s"' % artifact_name)

        lines += ['end', '']

        with open(self.fullPathToCask, 'w') as fh:
            fh.write('\n'.join(lines))

        self.log.info("Cask written: %s", self.fullPathToCask)
        if sha256_val.startswith('PLACEHOLDER'):
            self.log.warning(
                "  Remember to update the sha256 before publishing the cask.")
