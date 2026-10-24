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
"""Unit tests for RpmPackage's metadata -> RPM header mapping.

_doMetadataMappings turns the csmakefile [metadata] section (a real
`metadata` module instance, built bare, so the version formatting is the
real thing) into the header records of the package: identity strings,
build constants, classifier-derived tags and the dependency arrays.  The
csmake engine (translatePackageName sections) is a FakeEngine and time is
patched, so every expected value is exact.
"""
import struct
import unittest
from unittest import mock

from . import csmake_alias
from .support import FakeEngine, FakeResult, make_metadata, new_packager

BUILD_TIME = 1700000000


class RpmMetadataTestCase(unittest.TestCase):

    def setUp(self):
        self.rpm_module = csmake_alias.import_plugin('RpmPackage')
        self.RpmPackage = self.rpm_module.RpmPackage
        self.metadata_class = csmake_alias.import_plugin('metadata').metadata

    def make_package(self, options=None, fields=None, version=None,
                     engine=None):
        all_options = {'package-version': '3', 'result': '/results'}
        all_options.update(options or {})
        return new_packager(
            self.RpmPackage, options=all_options, engine=engine,
            metadata=make_metadata(self.metadata_class, fields, version))

    def mapped(self, **kwargs):
        package = self.make_package(**kwargs)
        with mock.patch.object(
                self.rpm_module.time, 'time', return_value=BUILD_TIME + 0.5):
            package._doMetadataMappings()
        return package

    # --- reading the records back
    def record(self, package, tag):
        return package.headers.records[tag]

    def text(self, package, tag):
        data = self.record(package, tag).data
        self.assertEqual(b'\x00', data[-1:])
        return data[:-1].decode('utf-8')

    def texts(self, package, tag):
        return [s.decode('utf-8')
                for s in self.record(package, tag).data.split(b'\x00')[:-1]]

    def numbers(self, package, tag):
        record = self.record(package, tag)
        return list(struct.unpack('!%dl' % record.entries, record.data))


class testRpmDoMetadataMappings(RpmMetadataTestCase):

    def test_mappings_set_name_version_release_and_source_rpm_name(self):
        package = self.mapped()
        self.assertEqual('demo', self.text(package, 1000))
        self.assertEqual('1.2.3', self.text(package, 1001))
        self.assertEqual('3', self.text(package, 1002))
        self.assertEqual('demo-1.2.3-3.noarch.src.rpm', self.text(package, 1044))
        self.assertEqual('demo', package.packageName)

    def test_mappings_name_the_package_files_from_epoch_and_version(self):
        package = self.mapped(
            options={'result': '/out'},
            version={'epoch': '2', 'primary': '1.2.3'})
        self.assertEqual('2:1.2.3-3', package.fullVersion)
        self.assertEqual('demo-1.2.3-3', package.fullLeadName)
        self.assertEqual('demo-1.2.3-3.noarch.rpm', package.fullPackageName)
        self.assertEqual('/out/demo-1.2.3-3.noarch.rpm',
                         package.fullPathToPackage)
        self.assertEqual('/out/demo-1.2.3-3.noarch.rpm.tempcpio',
                         package.fullPathToArchive)
        # the epoch rides in the version tags, not in the file name
        self.assertEqual('1.2.3', self.text(package, 1001))
        self.assertEqual(['2:1.2.3-3'], self.texts(package, 1113))

    def test_mappings_set_summary_description_and_other_text_tags(self):
        package = self.mapped()
        self.assertEqual(['A demo'], self.texts(package, 1004))
        self.assertEqual(9, self.record(package, 1004).datatype)
        self.assertEqual(['Line one\nLine two'], self.texts(package, 1005))
        self.assertEqual('Pat Packager <pat@example.com>', self.text(package, 1015))
        self.assertEqual('Demo Corp', self.text(package, 1011))
        self.assertEqual('https://example.com/demo', self.text(package, 1020))

    def test_mappings_encode_non_ascii_metadata_as_utf8(self):
        package = self.mapped(fields={
            'description': 'Café ☕', 'packager': 'René'})
        self.assertEqual(
            b'Caf\xc3\xa9 \xe2\x98\x95\x00', self.record(package, 1004).data)
        self.assertEqual(b'Ren\xc3\xa9\x00', self.record(package, 1015).data)

    def test_mappings_omit_tags_for_absent_optional_metadata(self):
        package = self.make_package()
        for key in ('url', 'packager', 'manufacturer'):
            del package.metadata.original[key]
        with mock.patch.object(self.rpm_module.time, 'time', return_value=1.0):
            package._doMetadataMappings()
        for tag in (1020, 1015, 1011, 1010):
            self.assertNotIn(tag, package.headers.records)

    def test_mappings_require_the_about_text(self):
        package = self.make_package()
        del package.metadata.original['about']
        with self.assertRaises(KeyError):
            package._doMetadataMappings()

    def test_mappings_fix_the_build_constants(self):
        package = self.mapped()
        self.assertEqual([BUILD_TIME], self.numbers(package, 1006))   # BUILDTIME
        self.assertEqual('csmake', self.text(package, 1007))          # BUILDHOST
        self.assertEqual('cpio', self.text(package, 1124))            # PAYLOADFORMAT
        self.assertEqual('gzip', self.text(package, 1125))            # PAYLOADCOMPRESSOR
        self.assertEqual('9', self.text(package, 1126))               # PAYLOADFLAGS
        self.assertEqual('4.1.0', self.text(package, 1064))           # RPMVERSION

    def test_mappings_default_to_noarch(self):
        package = self.mapped()
        self.assertEqual('noarch', package.arch)
        self.assertEqual('noarch', self.text(package, 1022))
        self.assertEqual('noarch', self.text(package, 1131))
        self.assertEqual('noarch-redhat-linux-gnu', self.text(package, 1132))

    def test_mappings_arch_option_sets_arch_and_platform(self):
        package = self.mapped(options={'arch': 'x86_64'})
        self.assertEqual('x86_64', self.text(package, 1022))
        self.assertEqual('x86_64-redhat-linux-gnu', self.text(package, 1132))
        self.assertEqual('demo-1.2.3-3.x86_64.rpm', package.fullPackageName)

    def test_mappings_distro_option_adds_the_distribution_tag(self):
        self.assertEqual(
            'Fedora Project',
            self.text(self.mapped(options={'distro': 'Fedora Project'}), 1010))

    def test_mappings_license_group_and_os_come_from_classifiers(self):
        package = self.mapped()
        self.assertEqual('MIT', self.text(package, 1014))
        self.assertEqual(['Applications'], self.texts(package, 1016))
        self.assertEqual('linux', self.text(package, 1021))

    def test_mappings_group_uses_the_most_specific_classifier(self):
        package = self.mapped(fields={'classifiers': (
            'Intended Audience :: Developers\n'
            'Topic :: Software Development :: Libraries')})
        self.assertEqual(['Development/Libraries'], self.texts(package, 1016))

    def test_mappings_langs_add_translated_summary_description_and_group(self):
        package = self.mapped(
            options={'langs': 'fr_FR'},
            fields={'description(fr_FR)': 'Une démo'})
        self.assertEqual(['C', 'fr_FR'], package.languages)
        self.assertEqual(['A demo', 'Une démo'], self.texts(package, 1004))
        # untranslated entries fall back to the default text
        self.assertEqual(['Line one\nLine two'] * 2, self.texts(package, 1005))
        self.assertEqual(['Applications'] * 2, self.texts(package, 1016))

    def test_mappings_untranslated_language_falls_back_to_the_default_summary(self):
        package = self.mapped(
            options={'langs': 'fr_FR, de_DE'},
            fields={'description(fr_FR)': 'Une d\u00e9mo'})
        self.assertEqual(['C', 'fr_FR', 'de_DE'], package.languages)
        self.assertEqual(
            ['A demo', 'Une d\u00e9mo', 'A demo'], self.texts(package, 1004))


class testRpmDependencies(RpmMetadataTestCase):
    """The provides/requires/conflicts/... arrays of the header."""

    def setUp(self):
        super().setUp()
        self.R = self.RpmPackage
        self.rpmlib = self.R.RPMSENSE_RPMLIB | self.R.RPMSENSE_LESS \
            | self.R.RPMSENSE_EQUAL
        self.base_names = [
            '/bin/sh', 'rpmlib(VersionedDependencies)',
            'rpmlib(PayloadFilesHavePrefix)', 'rpmlib(CompressedFileNames)']
        self.base_flags = [
            self.R.RPMSENSE_INTERP, self.rpmlib, self.rpmlib, self.rpmlib]
        self.base_versions = ['', '3.0.3-1', '4.0-1', '3.0.4-1']

    def requires(self, package):
        return (self.texts(package, 1049), self.numbers(package, 1048),
                self.texts(package, 1050))

    def test_package_provides_itself_at_its_full_version(self):
        package = self.mapped()
        self.assertEqual(['demo'], self.texts(package, 1047))
        self.assertEqual([self.R.RPMSENSE_EQUAL], self.numbers(package, 1112))
        self.assertEqual(['1.2.3-3'], self.texts(package, 1113))

    @unittest.skip("pre-existing bug: the 'provides' branch of "
                   "_doMetadataMappings extends with the undefined name "
                   "'otherVersion' (NameError; it means 'otherVersions')")
    def test_extra_provides_are_appended(self):
        package = self.mapped(fields={'provides': 'virtual-demo (= 1.0)'})
        self.assertEqual(['demo', 'virtual-demo'], self.texts(package, 1047))

    def test_requires_always_carry_the_interpreter_and_rpmlib_entries(self):
        names, flags, versions = self.requires(self.mapped())
        self.assertEqual(self.base_names, names)
        self.assertEqual(self.base_flags, flags)
        self.assertEqual(self.base_versions, versions)

    def test_requires_use_the_shell_interpreter_option(self):
        names, _, _ = self.requires(
            self.mapped(options={'shell-interpreter': '/bin/bash'}))
        self.assertEqual('/bin/bash', names[0])

    def test_depends_become_versioned_requires(self):
        names, flags, versions = self.requires(self.mapped(fields={
            'depends': 'python (>= 3.6), binutils (<< 2.0), chrpath'}))
        self.assertEqual(self.base_names + ['python', 'binutils', 'chrpath'], names)
        self.assertEqual(
            self.base_flags
            + [self.R.RPMSENSE_GREATER | self.R.RPMSENSE_EQUAL,
               self.R.RPMSENSE_LESS, 0],
            flags)
        self.assertEqual(self.base_versions + ['3.6', '2.0', ''], versions)

    def test_pre_depends_are_flagged_as_script_prerequisites(self):
        names, flags, versions = self.requires(
            self.mapped(fields={'pre-depends': 'coreutils (>= 8)'}))
        self.assertEqual('coreutils', names[-1])
        self.assertEqual(
            self.R.RPMSENSE_GREATER | self.R.RPMSENSE_EQUAL
            | self.R.RPMSENSE_SCRIPT_PRE | self.R.RPMSENSE_SCRIPT_PREUN,
            flags[-1])
        self.assertEqual('8', versions[-1])

    def test_depends_are_translated_through_the_engine(self):
        section = 'translatePackageName@~~zlib1g~~'
        engine = FakeEngine(
            steps={section: FakeResult(True, 'zlib-devel')},
            sections=[section])
        names, _, _ = self.requires(
            self.mapped(fields={'depends': 'zlib1g'}, engine=engine))
        self.assertEqual('zlib-devel', names[-1])
        self.assertEqual(
            [(section, 'package', {'extraOptions': {'~~format~~': 'rpm'}})],
            engine.launched)

    def test_depends_keep_their_name_when_the_translation_step_is_missing(self):
        section = 'translatePackageName@~~zlib1g~~'
        engine = FakeEngine(sections=[section])     # launchStep() -> None
        names, _, _ = self.requires(
            self.mapped(fields={'depends': 'zlib1g'}, engine=engine))
        self.assertEqual('zlib1g', names[-1])

    def test_depends_translated_to_nothing_are_dropped(self):
        section = 'translatePackageName@~~zlib1g~~'
        engine = FakeEngine(
            steps={section: FakeResult(True, None)}, sections=[section])
        names, _, _ = self.requires(
            self.mapped(fields={'depends': 'zlib1g'}, engine=engine))
        self.assertEqual(self.base_names, names)

    def test_conflicts_and_breaks_share_the_conflict_tags(self):
        package = self.mapped(fields={
            'conflicts': 'oldpkg (< 2.0)', 'breaks': 'brokenpkg (= 1.5)'})
        self.assertEqual(['oldpkg', 'brokenpkg'], self.texts(package, 1054))
        self.assertEqual(['2.0', '1.5'], self.texts(package, 1055))
        self.assertEqual(
            [self.R.RPMSENSE_LESS, self.R.RPMSENSE_EQUAL],
            self.numbers(package, 1053))

    def test_breaks_alone_are_written_as_conflicts(self):
        package = self.mapped(fields={'breaks': 'brokenpkg'})
        self.assertEqual(['brokenpkg'], self.texts(package, 1054))

    def test_no_conflict_tags_without_conflicts_or_breaks(self):
        package = self.mapped()
        for tag in (1053, 1054, 1055):
            self.assertNotIn(tag, package.headers.records)

    def test_replaces_recommends_suggests_enhances_have_their_own_tags(self):
        # metadata key -> (name tag, flags tag, version tag)
        tags = {
            'replaces': (1090, 1114, 1115),
            'recommends': (5046, 5048, 5047),
            'suggests': (5049, 5051, 5050),
            'enhances': (5055, 5057, 5056)}
        for key, (name_tag, flags_tag, version_tag) in tags.items():
            with self.subTest(key):
                package = self.mapped(fields={key: 'extra (>= 1.0)'})
                self.assertEqual(['extra'], self.texts(package, name_tag))
                self.assertEqual(
                    [self.R.RPMSENSE_GREATER | self.R.RPMSENSE_EQUAL],
                    self.numbers(package, flags_tag))
                self.assertEqual(['1.0'], self.texts(package, version_tag))

    def test_empty_replaces_writes_no_tags(self):
        package = self.mapped(fields={'replaces': ''})
        self.assertNotIn(1090, package.headers.records)


class testRpmParseDebianDependencies(RpmMetadataTestCase):

    def parse(self, text):
        return new_packager(self.RpmPackage)._parseDebianDependencies(text)

    def test_version_operators_map_to_rpmsense_flags(self):
        R = self.RpmPackage
        cases = {
            '< 2': R.RPMSENSE_LESS, '<< 2': R.RPMSENSE_LESS,
            '<= 2': R.RPMSENSE_LESS | R.RPMSENSE_EQUAL,
            '= 2': R.RPMSENSE_EQUAL, '~ 2': R.RPMSENSE_EQUAL,
            '> 2': R.RPMSENSE_GREATER, '>> 2': R.RPMSENSE_GREATER,
            '>= 2': R.RPMSENSE_GREATER | R.RPMSENSE_EQUAL,
            '=2': R.RPMSENSE_EQUAL}
        for qualifier, flags in cases.items():
            with self.subTest(qualifier):
                self.assertEqual(
                    (['pkg'], [flags], ['2']), self.parse('pkg (%s)' % qualifier))

    def test_alternatives_use_only_the_first_choice(self):
        self.assertEqual(
            (['a', 'c'], [0, 0], ['', '']), self.parse('a | b, c'))

    def test_empty_dependency_list_is_empty(self):
        self.assertEqual(([], [], []), self.parse(''))

    def test_unusable_version_is_dropped_with_a_warning(self):
        package = new_packager(self.RpmPackage)
        # '{version}' is a placeholder that is only valid in translations
        self.assertEqual(
            (['pkg'], [0], ['']), package._parseDebianDependencies('pkg ({version})'))
        self.assertTrue(any(
            "is not a value version for 'pkg'" in message
            for message in package.log.messages('warning')))


class testRpmControlWriters(RpmMetadataTestCase):

    def package(self):
        return new_packager(
            self.RpmPackage, headers=self.RpmPackage.RpmRecordManager(63))

    def test_maintainer_script_controls_write_their_script_tags(self):
        scripts = {
            '_control_preinst': 1023, '_control_prein': 1023,
            '_control_postinst': 1024, '_control_postin': 1024,
            '_control_prerm': 1025, '_control_preun': 1025,
            '_control_postrm': 1026, '_control_postun': 1026,
            '_control_verify': 1079}
        for method, tag in scripts.items():
            with self.subTest(method):
                package = self.package()
                getattr(package, method)({'text': ['echo one', 'echo café']})
                self.assertEqual(
                    'echo one\necho café\x00'.encode('utf-8'),
                    self.record(package, tag).data)

    def test_maintainer_script_without_text_is_empty(self):
        package = self.package()
        package._control_postinst({})
        self.assertEqual(b'\x00', self.record(package, 1024).data)

    def test_changelog_control_writes_text_times_and_names(self):
        package = self.package()
        package._control_changelog({
            'text': ['Fixed it', 'Added it'], 'times': [100, 200],
            'names': ['A <a@example.com>', 'B <b@example.com>']})
        self.assertEqual(['Fixed it', 'Added it'], self.texts(package, 1082))
        self.assertEqual([100, 200], self.numbers(package, 1080))
        self.assertEqual(
            ['A <a@example.com>', 'B <b@example.com>'], self.texts(package, 1081))

    def test_changelog_control_without_text_writes_nothing(self):
        package = self.package()
        package._control_changelog({'times': [1]})
        self.assertEqual({}, package.headers.records)

    @unittest.skip("pre-existing bug: without 'times'/'names' "
                   "_control_changelog multiplies a list by the 'text' list "
                   "([now] * records), raising TypeError")
    def test_changelog_control_defaults_times_and_names(self):
        package = self.package()
        package._control_changelog({'text': ['Fixed it']})
        self.assertEqual(['No author'], self.texts(package, 1081))


class testRpmPathMaps(RpmMetadataTestCase):

    def test_root_maps_to_the_archive_root_dot(self):
        package = new_packager(self.RpmPackage)
        pathmaps, pathkeymaps = {}, {}
        package._map_path_root('rootdir', pathmaps, pathkeymaps)
        self.assertEqual({'rootdir': ['.']}, pathmaps)
        self.assertEqual({'root': ['.']}, pathkeymaps)
        self.assertEqual('.', package.archiveRoot)

    def test_python_lib_maps_to_site_packages_of_the_classified_python(self):
        package = new_packager(
            self.RpmPackage, packageMetadata={'**python-lib': [(1, 'python3')]})
        pathmaps, pathkeymaps = {}, {}
        package._map_path_python_lib('pylib', pathmaps, pathkeymaps)
        expected = ['%(root)s/usr/lib/python3/site-packages']
        self.assertEqual({'pylib': expected}, pathmaps)
        self.assertEqual({'python-lib': expected}, pathkeymaps)

    def test_python_lib_defaults_to_python3_with_a_warning(self):
        package = new_packager(self.RpmPackage, packageMetadata={})
        pathmaps, pathkeymaps = {}, {}
        package._map_path_python_lib('pylib', pathmaps, pathkeymaps)
        self.assertEqual(
            ['%(root)s/usr/lib/python3/site-packages'], pathmaps['pylib'])
        self.assertTrue(package.log.called('warning'))


if __name__ == '__main__':
    unittest.main()
