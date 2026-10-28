# <copyright>
# (c) Copyright 2026 Autumn Patterson
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
"""Unit tests for the Debian aspects after the Python 3 port.

The aspects used to build their control files in StringIO; they now write
encoded bytes into io.BytesIO, which tarfile.addfile() then reads.  The step
they talk to is core's DebianPackage (its _addFileObjToControl /
_addFileObjToArchive / _addInfoToArchive write into an in-memory tar), built
bare because a full csmake engine is out of reach in a unit test.
"""
import importlib
import io
import tarfile
import unittest

from . import csmake_alias
from .support import bare_instance


def load(name):
    return getattr(csmake_alias.import_plugin(name), name)


class DebianAspectTestCase(unittest.TestCase):

    def setUp(self):
        self.control_tar, self.data_tar = io.BytesIO(), io.BytesIO()
        self.step = bare_instance(
            load('DebianPackage'),
            # as DebianPackage._setupPackage leaves them
            controls={'md5sums': {'entries': []}},
            archiveRoot='.', packageMetadata={'Package': 'demo'},
            controlfile=tarfile.open(fileobj=self.control_tar, mode='w'),
            archive=tarfile.open(fileobj=self.data_tar, mode='w'))
        self.stepoptions = {}

    def make_aspect(self, name, **attributes):
        aspect = bare_instance(load(name), **attributes)
        # PackagerAspect gets its FileManager from the step's metadata;
        # hand it a real one instead.
        aspect._getFileManager = \
            importlib.import_module('Csmake.FileManager').FileManager
        return aspect

    def read_tar(self, buffer):
        """{name: (TarInfo, bytes or None)} for what the step has written."""
        self.step.controlfile.close()
        self.step.archive.close()
        tar = tarfile.open(fileobj=io.BytesIO(buffer.getvalue()))
        return {
            member.name: (member, tar.extractfile(member).read()
                          if member.isfile() else None)
            for member in tar.getmembers()}


class testDebianAspects(DebianAspectTestCase):

    def test_shlibs_aspect_writes_control_file_and_soname_links(self):
        aspect = self.make_aspect('IdentifyShlibsDebian')
        self.assertTrue(aspect.start(
            'package', {'file-types': '<(ELF-Shared)>'},
            self.step, self.stepoptions))
        # start() installed the control writer on the step
        self.assertIn('_control_shlibs', self.step.__dict__)

        self.assertTrue(aspect.begin_map(
            'package',
            {'from': {'location': '/build/libfoo.so.1.2.3'},
             'tos': [{'relLocation': 'usr/lib/libfoo.so.1.2.3'}]},
            self.step, self.stepoptions))
        shlibs = self.step._packageControl('shlibs')
        self.assertEqual('1', shlibs['libfoo'][0])
        shlibs['libplain'] = ('-1', 'demo', [], 'libplain.so')

        self.assertTrue(aspect.mapping_complete(
            'package', {}, self.step, self.stepoptions))
        self.step._control_shlibs(shlibs)

        control = self.read_tar(self.control_tar)
        self.assertEqual(
            b'libfoo 1 demo\nlibplain 0 demo\n', control['./shlibs'][1])
        links = self.read_tar(self.data_tar)
        for name in ('./usr/lib/libfoo.so.1', './usr/lib/libfoo.so'):
            member = links[name][0]
            self.assertTrue(member.issym(), name)
            self.assertEqual('libfoo.so.1.2.3', member.linkname)
        self.assertNotIn('./libplain.so', links)    # '-1' is never linked

    def test_conffiles_aspect_writes_utf8_paths(self):
        aspect = self.make_aspect('IdentifyConfFilesDebian')
        self.assertTrue(aspect.start(
            'package', {'file-types': '<(conf)>'}, self.step, self.stepoptions))
        aspect.begin_map(
            'package',
            {'from': {'location': '/build/foo.conf'},
             'tos': [{'relLocation': 'etc/foo.conf'},
                     {'relLocation': 'etc/café.conf'}]},
            self.step, self.stepoptions)
        self.step._control_conffiles(self.step._packageControl('conffiles'))

        self.assertEqual(
            b'/etc/foo.conf\n/etc/caf\xc3\xa9.conf\n',
            self.read_tar(self.control_tar)['./conffiles'][1])

    def test_conffiles_outside_the_archive_are_rejected(self):
        aspect = self.make_aspect('IdentifyConfFilesDebian', step=self.step)
        with self.assertRaises(ValueError):
            aspect._control_conffiles({'../escape.conf': None})

    def test_lintian_aspect_writes_overrides_file(self):
        aspect = self.make_aspect('DebianLintianOverrides')
        options = {'overrides': 'no-copyright\n\n  odd-café tag  '}
        self.assertTrue(aspect.start('package', options, self.step, self.stepoptions))
        self.assertTrue(aspect.mapping_complete(
            'package', options, self.step, self.stepoptions))
        self.step._control_lintian(self.step._packageControl('lintian'))

        member, content = self.read_tar(self.data_tar)[
            './usr/share/lintian/overrides/demo']
        self.assertEqual(
            'demo: no-copyright\ndemo: odd-café tag\n'.encode('utf-8'),
            content)
        self.assertEqual((0o644, 0, 'root'), (member.mode, member.uid, member.uname))

    def test_shlibs_begin_map_fails_for_a_library_without_a_name(self):
        aspect = self.make_aspect('IdentifyShlibsDebian')
        self.assertFalse(aspect.begin_map(
            'package',
            {'from': {'location': '/build/1.2.3'}, 'tos': []},
            self.step, self.stepoptions))
        self.assertTrue(aspect.log.called('failed'))
        self.assertFalse(aspect.log.called('passed'))
        self.assertEqual({}, self.step.controls['shlibs'])

    def test_shlibs_unversioned_library_is_recorded_as_minus_one(self):
        aspect = self.make_aspect('IdentifyShlibsDebian')
        self.assertTrue(aspect.begin_map(
            'package',
            {'from': {'location': '/build/libplain.so'}, 'tos': []},
            self.step, self.stepoptions))
        self.assertEqual(
            ('-1', 'demo', [], 'libplain.so'),
            self.step.controls['shlibs']['libplain'])

    def test_shlibs_control_encodes_non_ascii_package_names(self):
        aspect = self.make_aspect('IdentifyShlibsDebian')
        aspect.start('package', {}, self.step, self.stepoptions)
        self.step._control_shlibs(
            {'libfoo': ('2', 'd\u00e9mo', [], 'libfoo.so.2.0')})
        self.assertEqual(
            'libfoo 2 d\u00e9mo\n'.encode('utf-8'),
            self.read_tar(self.control_tar)['./shlibs'][1])

    def test_conffiles_begin_map_collects_every_destination(self):
        aspect = self.make_aspect('IdentifyConfFilesDebian')
        self.assertTrue(aspect.begin_map(
            'package',
            {'from': {'location': '/build/foo.conf'},
             'tos': [{'relLocation': 'etc/a.conf'}, {'relLocation': 'etc/b.conf'}]},
            self.step, self.stepoptions))
        self.assertEqual(['etc/a.conf', 'etc/b.conf'],
                         list(self.step.controls['conffiles']))
        self.assertTrue(aspect.log.called('passed'))

    def test_lintian_without_overrides_writes_an_empty_file(self):
        aspect = self.make_aspect('DebianLintianOverrides')
        aspect.start('package', {'overrides': 'x'}, self.step, self.stepoptions)
        self.step._control_lintian({})
        member, content = self.read_tar(self.data_tar)[
            './usr/share/lintian/overrides/demo']
        self.assertEqual((0, b''), (member.size, content))

    def test_lintian_overrides_accumulate_across_mappings(self):
        aspect = self.make_aspect('DebianLintianOverrides')
        aspect.mapping_complete(
            'package', {'overrides': 'one'}, self.step, self.stepoptions)
        aspect.mapping_complete(
            'package', {'overrides': 'two\nthree'}, self.step, self.stepoptions)
        self.assertEqual(
            ['demo: one', 'demo: two', 'demo: three'],
            self.step.controls['lintian']['overrides'])


if __name__ == '__main__':
    unittest.main()
