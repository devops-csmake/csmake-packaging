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
"""Unit tests for RpmPackage's package() flow and its external boundaries.

The boundaries are all replaced by doubles from support.py: the csmake
engine (FakeEngine/FakeResult), the metadata module's FileManager
(FakeFileManager), the `dd` subprocess (FakeSubprocess, patched over the
module's `subprocess`) and the gpg signers (FakeSigner).  Real temp files
are used only where the file I/O and byte layout of the package are what is
being tested, and the finished .rpm is read back with the independent
parsers in rpmparse.py.
"""
import hashlib
import os
import struct
import subprocess
import tarfile
import tempfile
import unittest
import zlib
from unittest import mock

from . import csmake_alias
from .rpmparse import ParsedRpm, read_cpio
from .support import (
    BINARY, FakeEngine, FakeFileManager, FakeResult, FakeSigner,
    FakeSubprocess, make_metadata, new_packager)

MAPPING = {
    'permissions': '0755', 'owner': ('root', 0), 'group': ('root', 0),
    'rpmflags': 'config', 'copyright': []}


def close_archive_files(package):
    for name in ('package', 'archive', 'archiveHeaders'):
        handle = package.__dict__.get(name)
        if handle is not None:
            handle.close()


class RpmFlowTestCase(unittest.TestCase):

    def setUp(self):
        self.rpm_module = csmake_alias.import_plugin('RpmPackage')
        self.RpmPackage = self.rpm_module.RpmPackage
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = self.write_file('demo.bin', BINARY)

    def path(self, *parts):
        return os.path.join(self.tmp.name, *parts)

    def write_file(self, name, content):
        path = self.path(name)
        with open(path, 'wb') as out:
            out.write(content)
        return path

    def use_subprocess(self, fake):
        patcher = mock.patch.object(self.rpm_module, 'subprocess', fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake

    def archive_package(self, options=None, arch='noarch', engine=None):
        """A package past _setupArchive with /usr/bin/demo placed in it."""
        rpm_path = self.path('demo-1.0-1.%s.rpm' % arch)
        package = new_packager(
            self.RpmPackage, options=dict(options or {}), engine=engine,
            headers=self.RpmPackage.RpmRecordManager(63), arch=arch,
            fullLeadName='demo-1.0-1', fullPathToPackage=rpm_path,
            fullPathToArchive=rpm_path + '.tempcpio')
        package._setupArchive()
        self.addCleanup(close_archive_files, package)
        package._placeFileInArchive(
            dict(MAPPING), self.source, '/usr/bin/demo', None)
        return package


class testRpmPackageStages(RpmFlowTestCase):
    """package() itself: which stages run, in what order, and the verdict."""

    def stubbed(self, finish=True):
        package = new_packager(self.RpmPackage)
        self.stages = mock.Mock()
        for name in ('_doMetadataMappings', '_setupPackage', '_doMaps',
                     '_handleControls', '_finishPackage'):
            setattr(package, name, getattr(self.stages, name))
        self.stages._finishPackage.return_value = finish
        return package

    def test_package_runs_the_stages_in_order_and_passes(self):
        package = self.stubbed()
        self.assertTrue(package.package({'result': 'out'}))
        self.assertEqual(
            ['_doMetadataMappings', '_setupPackage', '_doMaps',
             '_handleControls', '_finishPackage'],
            [name for name, _, _ in self.stages.mock_calls])
        self.assertEqual({'result': 'out'}, package.options)
        self.assertTrue(package.log.called('passed'))
        self.assertFalse(package.log.called('failed'))

    def test_package_reports_failure_when_finishing_fails(self):
        package = self.stubbed(finish=False)
        self.assertFalse(package.package({}))
        self.assertTrue(package.log.called('failed'))
        self.assertFalse(package.log.called('passed'))

    def test_package_stops_at_the_first_stage_that_raises(self):
        package = self.stubbed()
        self.stages._doMaps.side_effect = ValueError("Mappings failed")
        with self.assertRaises(ValueError):
            package.package({})
        self.assertEqual(
            ['_doMetadataMappings', '_setupPackage', '_doMaps'],
            [name for name, _, _ in self.stages.mock_calls])


class testRpmPackageEndToEnd(RpmFlowTestCase):
    """package() with the real stages, the engine and the dd run mocked."""

    def setUp(self):
        super().setUp()
        self.fake_dd = self.use_subprocess(FakeSubprocess())
        self.map_definitions = {
            'path': {'root': 'rootdir'},
            'owner': {'root': 'rootuser'},
            'group': {'root': 'rootgroup'},
            'map': {'bin': {
                'map': 'demo.bin -(1-1)-> {rootdir}/usr/bin/demo',
                'owner': '{rootuser}', 'group': '{rootgroup}',
                'permissions': '0755', 'rpmflags': 'config',
                'copyright': 'demo-copyright'}}}
        self.steps = {
            'demo-map': FakeResult(True, self.map_definitions),
            'demo-copyright': FakeResult(True, ['the copyright'])}
        self.options = {
            'package-version': '1', 'maps': 'demo-map',
            'result': self.path('out')}

    def build(self, controls=None):
        metadata = make_metadata(
            csmake_alias.import_plugin('metadata').metadata)
        self.files = FakeFileManager([(
            [{'location': self.source}],
            [{'relLocation': './usr/bin/demo'}])])
        metadata.files = self.files
        self.engine = FakeEngine(steps=self.steps)
        package = new_packager(
            self.RpmPackage, options=self.options, metadata=metadata,
            engine=self.engine)
        package.controls.update(controls or {})
        self.addCleanup(close_archive_files, package)
        return package

    def test_package_builds_a_complete_rpm(self):
        package = self.build({'postinst': {'text': ['echo installed']}})
        self.assertTrue(package.package(self.options))

        self.assertTrue(package.log.called('passed'))
        # The installmap was resolved through the engine and substituted
        self.assertEqual(
            [('demo-map', 'package', {}), ('demo-copyright', 'package', {})],
            self.engine.launched)
        self.assertEqual(
            ['demo.bin -(1-1)-> ./usr/bin/demo'], self.files.maps)
        self.assertEqual(
            ['mapping_complete'],
            [joinpoint for _, joinpoint, _ in self.engine.aspects_launched])

        rpm = ParsedRpm.from_file(package.fullPathToPackage)
        self.assertEqual(b'demo-1.2.3-1', rpm.lead[10:22])
        self.assertEqual([b'demo'], rpm.strings(1000))
        self.assertEqual([b'echo installed'], rpm.strings(1024))      # %post
        self.assertEqual([b'demo'], rpm.strings(1117))                # basenames
        self.assertEqual([b'/usr/bin/'], rpm.strings(1118))           # dirnames
        self.assertEqual([1], rpm.ints(1037))                        # %config
        files, _ = read_cpio(zlib.decompress(rpm.payload, 31))
        self.assertEqual([(b'./usr/bin/demo', BINARY)],
                         [(f['name'], f['data']) for f in files])
        self.assertEqual(2, len(self.fake_dd.calls))

    def test_package_warns_about_controls_nothing_can_write(self):
        package = self.build({'mystery': {}})
        self.assertTrue(package.package(self.options))
        warnings = package.log.messages('warning')
        self.assertTrue(
            any("'mystery' control was defined" in w for w in warnings),
            warnings)

    def test_package_fails_when_the_mapping_step_fails(self):
        self.steps['demo-map'] = FakeResult(False)
        package = self.build()
        with self.assertRaises(ValueError):
            package.package(self.options)
        self.assertTrue(package.log.called('failed'))
        self.assertEqual([], self.fake_dd.calls)

    def test_package_fails_when_the_mapping_step_is_unknown(self):
        del self.steps['demo-map']
        package = self.build()
        with self.assertRaises(ValueError):
            package.package(self.options)
        self.assertEqual([], self.fake_dd.calls)

    def test_package_propagates_a_failing_dd(self):
        self.use_subprocess(FakeSubprocess(fail_call=2))
        package = self.build()
        with self.assertRaises(subprocess.CalledProcessError):
            package.package(self.options)
        self.assertFalse(package.log.called('passed'))


class testRpmFinishArchive(RpmFlowTestCase):
    """_finishArchive: the lead, signature block and the dd boundary."""

    def test_dd_appends_the_headers_then_the_payload_to_the_package(self):
        fake = self.use_subprocess(FakeSubprocess())
        package = self.archive_package()
        self.assertTrue(package._finishArchive())

        rpm_path = package.fullPathToPackage
        expected_kwargs = {'stdout': package.log.out_stream,
                           'stderr': package.log.err_stream}
        # GNU dd: 'oflag=append' is what makes the pieces land one after
        # the other; BSD dd (macOS) does not understand it.
        self.assertEqual(
            [(['dd', 'if=' + package.fullPathToArchiveHeaders,
               'of=' + rpm_path, 'oflag=append', 'conv=notrunc'],
              expected_kwargs),
             (['dd', 'if=' + package.fullPathToArchive,
               'of=' + rpm_path, 'oflag=append', 'conv=notrunc'],
              expected_kwargs)],
            fake.calls)

    def test_finished_package_is_internally_consistent(self):
        self.use_subprocess(FakeSubprocess())
        package = self.archive_package()
        self.assertTrue(package._finishArchive())

        rpm = ParsedRpm.from_file(package.fullPathToPackage)
        self.assertEqual(b'\xed\xab\xee\xdb', rpm.lead[:4])
        self.assertEqual(b'demo-1.0-1\x00', rpm.lead[10:21])
        # The metadata header starts on the next 8 byte boundary.
        self.assertEqual(b'\x00' * len(rpm.padding), rpm.padding)
        raw = zlib.decompress(rpm.payload, 31)

        self.assertEqual([len(rpm.header) + len(rpm.payload)],
                         rpm.sig_ints(1000))                        # SIZE
        self.assertEqual(
            hashlib.md5(rpm.header + rpm.payload).digest(),
            rpm.sig_binary(1004))                                   # MD5
        self.assertEqual([len(raw)], rpm.sig_ints(1007))             # PAYLOADSIZE
        self.assertEqual(
            [hashlib.sha1(rpm.header).hexdigest().encode()],
            rpm.sig_strings(269))                                   # SHA1HEADER
        self.assertEqual([len(BINARY)], rpm.ints(1009))              # SIZE
        self.assertEqual([len(raw)], rpm.ints(1046))                 # ARCHIVESIZE
        self.assertEqual([b'demo'], rpm.strings(1117))
        self.assertEqual([b'C'], rpm.strings(100))                   # I18N table
        files, _ = read_cpio(raw)
        self.assertEqual([(b'./usr/bin/demo', BINARY)],
                         [(f['name'], f['data']) for f in files])

    def test_lead_marks_the_architecture_and_source_packages(self):
        cases = [
            # (arch, is-source option, expected lead arch number, type)
            ('noarch', None, 0xff, 0),
            ('x86_64', None, 1, 0),
            ('noarch', 'True', 0xff, 1),
            ('noarch', 'False', 0xff, 0)]
        for arch, is_source, archnum, rpmtype in cases:
            with self.subTest(arch=arch, source=is_source):
                self.use_subprocess(FakeSubprocess())
                options = {} if is_source is None else {'is-source': is_source}
                package = self.archive_package(options, arch=arch)
                self.assertTrue(package._finishArchive())
                lead = ParsedRpm.from_file(package.fullPathToPackage).lead
                self.assertEqual(
                    (3, 0, rpmtype, archnum), struct.unpack_from('!2b2h', lead, 4))

    def test_finish_archive_raises_when_dd_fails_to_append_the_payload(self):
        fake = self.use_subprocess(FakeSubprocess(fail_call=2))
        package = self.archive_package()
        with self.assertRaises(subprocess.CalledProcessError):
            package._finishArchive()
        self.assertEqual(2, len(fake.calls))

    def test_finish_archive_raises_when_dd_rejects_oflag_append(self):
        fake = self.use_subprocess(FakeSubprocess(dd_flavour='bsd'))
        package = self.archive_package()
        with self.assertRaises(subprocess.CalledProcessError):
            package._finishArchive()
        # nothing after the failed header append was attempted
        self.assertEqual(1, len(fake.calls))
        self.assertIn('oflag=append', fake.calls[0][0])


class testRpmSigners(RpmFlowTestCase):
    """The gpg signers: launched by _setupArchive, fed while the package
    is finished, and recorded in the signature header."""

    def engine_with_signers(self, **signers):
        return FakeEngine(steps={
            name: FakeResult(True, signer) for name, signer in signers.items()})

    def finished(self, package):
        self.use_subprocess(FakeSubprocess())
        self.assertTrue(package._finishArchive())
        return ParsedRpm.from_file(package.fullPathToPackage)

    def test_setup_launches_every_signer_step_in_order(self):
        first, second = FakeSigner(), FakeSigner()
        engine = self.engine_with_signers(**{'sign-a': first, 'sign-b': second})
        package = self.archive_package(
            {'signers': 'sign-a,\n  sign-b'}, engine=engine)
        self.assertEqual(
            [('sign-a', 'package', {}), ('sign-b', 'package', {})],
            engine.launched)
        self.assertEqual([first, second], package.signers)

    def test_setup_fails_when_a_signer_step_fails(self):
        # e.g. gpg was given a --local-user key that is not on the keyring
        engine = FakeEngine(steps={'sign-a': FakeResult(False)})
        package = new_packager(
            self.RpmPackage, options={'signers': 'sign-a'}, engine=engine,
            headers=self.RpmPackage.RpmRecordManager(63),
            fullPathToPackage=self.path('x.rpm'),
            fullPathToArchive=self.path('x.rpm.tempcpio'))
        self.addCleanup(close_archive_files, package)
        with self.assertRaises(ValueError) as caught:
            package._setupArchive()
        self.assertIn('sign-a step failed', str(caught.exception))

    def test_setup_fails_when_a_signer_step_is_unknown(self):
        package = new_packager(
            self.RpmPackage, options={'signers': 'nope'},
            headers=self.RpmPackage.RpmRecordManager(63),
            fullPathToPackage=self.path('x.rpm'),
            fullPathToArchive=self.path('x.rpm.tempcpio'))
        self.addCleanup(close_archive_files, package)
        with self.assertRaises(ValueError):
            package._setupArchive()

    def test_is_source_option_only_true_for_the_string_True(self):
        for value, expected in (('True', True), ('False', False), ('1', False)):
            with self.subTest(value):
                package = new_packager(
                    self.RpmPackage, options={'is-source': value},
                    headers=self.RpmPackage.RpmRecordManager(63),
                    fullPathToPackage=self.path('x.rpm'),
                    fullPathToArchive=self.path('x.rpm.tempcpio'))
                package._setupArchive()
                close_archive_files(package)
                self.assertEqual(expected, package.isSource)

    def test_gpg_signature_is_recorded_and_covers_header_and_payload(self):
        signer = FakeSigner(signature=b'\x89\x01SIGNATURE-BYTES')
        package = self.archive_package(
            {'signers': 'sign-a'}, engine=self.engine_with_signers(**{'sign-a': signer}))
        rpm = self.finished(package)

        self.assertEqual(b'\x89\x01SIGNATURE-BYTES', rpm.sig_binary(1005))
        self.assertEqual(7, rpm.sig[1005][0])            # a binary blob
        # gpg signed exactly the metadata header followed by the payload
        self.assertEqual(rpm.header + rpm.payload, signer.fed_bytes())
        self.assertTrue(signer.finished)

    def test_non_gpg_signature_types_are_not_recorded(self):
        signer = FakeSigner(signtype='GPG Ascii')
        package = self.archive_package(
            {'signers': 'sign-a'}, engine=self.engine_with_signers(**{'sign-a': signer}))
        rpm = self.finished(package)
        self.assertNotIn(1005, rpm.sig)

    def test_a_failing_signer_aborts_packaging(self):
        class ExplodingSigner(FakeSigner):
            def digest(self):
                raise RuntimeError("gpg: signing failed")

        package = self.archive_package(
            {'signers': 'sign-a'},
            engine=self.engine_with_signers(**{'sign-a': ExplodingSigner()}))
        self.use_subprocess(FakeSubprocess())
        with self.assertRaises(RuntimeError):
            package._finishArchive()

    @unittest.skip("pre-existing gap: RpmPackage never asks the signer "
                   "whether gpg failed (didFail()), so a non-zero gpg exit "
                   "(e.g. an unknown --local-user key) yields an empty "
                   "signature record and a header whose index count includes "
                   "an unwritten entry instead of aborting the package")
    def test_gpg_non_zero_exit_aborts_packaging(self):
        signer = FakeSigner(exit_status=2)
        package = self.archive_package(
            {'signers': 'sign-a'}, engine=self.engine_with_signers(**{'sign-a': signer}))
        self.use_subprocess(FakeSubprocess())
        with self.assertRaises(Exception):
            package._finishArchive()

    @unittest.skip("pre-existing bug: the 'Multiple GPG Signatures' check in "
                   "_createSignatureBlock tests the never-filled list "
                   "'signkeys', so a second GPG signer is neither warned "
                   "about nor does the first get closed")
    def test_second_gpg_signer_is_rejected_with_a_warning(self):
        first, second = FakeSigner(), FakeSigner()
        package = self.archive_package(
            {'signers': 'a, b'},
            engine=self.engine_with_signers(**{'a': first, 'b': second}))
        self.finished(package)
        self.assertTrue(package.log.messages('warning'))


class FakeFlowControl:
    def __init__(self, advice=False):
        self.advised = advice
        self.issues = []

    def initFlowControlIssue(self, name, description):
        self.issues.append(name)

    def advice(self, name):
        return self.advised


class FakeTree:
    """os.listdir/isdir/islink/getsize over a scripted tree of paths."""

    def __init__(self, dirs, files, links=()):
        self.dirs, self.files, self.links = dirs, files, set(links)

    def patches(self):
        return [
            mock.patch('os.path.isdir', lambda p: p in self.dirs),
            mock.patch('os.path.islink', lambda p: p in self.links),
            mock.patch('os.listdir', lambda p: list(self.dirs[p])),
            mock.patch('os.path.getsize', lambda p: self.files[p])]


class testRpmPlaceFile(RpmFlowTestCase):
    """_placeFileInArchive / _placeDirectoryInArchive build RpmFileInfos."""

    def setUp(self):
        super().setUp()
        self.tree = FakeTree(
            dirs={'/src/pkg': ['a.txt', 'sub', 'link'], '/src/pkg/sub': ['b.bin']},
            files={'/src/tool': 123, '/src/pkg/a.txt': 5, '/src/pkg/sub/b.bin': 9},
            links=['/src/pkg/link'])
        for patcher in self.tree.patches():
            patcher.start()
            self.addCleanup(patcher.stop)

    def package(self, **attributes):
        return new_packager(
            self.RpmPackage, executedFileMappings=[], **attributes)

    def mapping(self, **overrides):
        mapping = {'permissions': '0755', 'owner': ('root', 0),
                   'group': ('wheel', 10)}
        mapping.update(overrides)
        return mapping

    def test_file_is_placed_with_an_rpmfileinfo(self):
        package = self.package()
        package._placeFileInArchive(
            self.mapping(), '/src/tool', '/usr/bin/tool', None)

        (_, source, archive_path, info), = package.executedFileMappings
        self.assertEqual(('/src/tool', '/usr/bin/tool'), (source, archive_path))
        self.assertIsInstance(info, self.RpmPackage.RpmFileInfo)
        self.assertEqual(0o755, info.mode)
        self.assertEqual(123, info.size)
        self.assertEqual(tarfile.REGTYPE, info.type)
        self.assertEqual(('root', 0, 'wheel', 10),
                         (info.uname, info.uid, info.gname, info.gid))
        self.assertEqual(0, info.rpmflags)

    def test_rpmflags_map_to_file_flag_bits(self):
        bits = {'config': 1, 'doc': 2, 'donotuse': 4, 'missingok': 8,
                'noreplace': 16, 'spec': 32, 'ghost': 64, 'license': 128,
                'readme': 256, 'pubkey': 2048}
        for flag, bit in bits.items():
            with self.subTest(flag):
                package = self.package()
                package._placeFileInArchive(
                    self.mapping(rpmflags=flag), '/src/tool', '/t', None)
                self.assertEqual(bit, package.executedFileMappings[0][3].rpmflags)

    def test_rpmflags_combine(self):
        package = self.package()
        package._placeFileInArchive(
            self.mapping(rpmflags=' config , noreplace'), '/src/tool', '/t', None)
        self.assertEqual(1 | 16, package.executedFileMappings[0][3].rpmflags)

    def test_unknown_rpmflag_is_ignored_with_a_warning(self):
        package = self.package()
        package._placeFileInArchive(
            self.mapping(rpmflags='config, bogus'), '/src/tool', '/t', None)
        self.assertEqual(1, package.executedFileMappings[0][3].rpmflags)
        self.assertIn("Flag 'bogus' has no definition",
                      package.log.messages('warning'))

    def test_directory_is_expanded_into_its_files(self):
        package = self.package()
        package._placeFileInArchive(
            self.mapping(rpmflags='doc'), '/src/pkg', './opt/pkg', None)

        placed = [(source, archive_path, info.size, info.rpmflags)
                  for _, source, archive_path, info
                  in package.executedFileMappings]
        self.assertEqual(
            [('/src/pkg/a.txt', './opt/pkg/a.txt', 5, 2),
             ('/src/pkg/sub/b.bin', './opt/pkg/sub/b.bin', 9, 2)],
            placed)
        self.assertEqual(
            ['RPM Ignoring link in directory listing'],
            package.log.messages('warning'))

    def test_aspects_are_told_about_the_file(self):
        engine = FakeEngine()
        package = self.package(
            engine=engine, flowcontrol=FakeFlowControl(advice=False),
            options={})
        mapping = self.mapping()
        package._placeFileInArchive(
            mapping, '/src/tool', '/t', [('aspect', {})])

        (aspects, joinpoint, extra), = engine.aspects_launched
        self.assertEqual([('aspect', {})], aspects)
        self.assertEqual('archive_file', joinpoint)
        self.assertEqual('/src/tool', extra['from'])
        self.assertEqual('/t', extra['to'])
        self.assertIs(mapping, extra['mapping'])
        self.assertEqual(1, len(package.executedFileMappings))

    @unittest.skip("pre-existing bug in core's Packager._doArchiveFileAspects: "
                   "when an aspect advises doNotMapFile it logs "
                   "str(source), an undefined name (NameError)")
    def test_aspect_advice_can_veto_a_file(self):
        package = self.package(
            engine=FakeEngine(), flowcontrol=FakeFlowControl(advice=True),
            options={})
        package._placeFileInArchive(
            self.mapping(), '/src/tool', '/t', [('aspect', {})])
        self.assertEqual([], package.executedFileMappings)


if __name__ == '__main__':
    unittest.main()
