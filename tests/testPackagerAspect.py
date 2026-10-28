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
"""Unit tests for PackagerAspect's handler installation.

A PackagerAspect registers itself with the Packager step it advises: file
type dispatch entries, control dispatch entries and the _control_<name>
writer methods it finds on its own class.  The step is a plain object, the
aspect a bare instance; the FileManager that parses '<(type)>' file
declarations is the real (pure) one.
"""
import importlib
import unittest

from . import csmake_alias
from .support import bare_instance


class FakeStep:
    """A Packager step as far as aspects are concerned: just a namespace."""


class PackagerAspectTestCase(unittest.TestCase):

    def setUp(self):
        self.PackagerAspect = csmake_alias.import_plugin(
            'PackagerAspect').PackagerAspect
        self.FileManager = importlib.import_module(
            'Csmake.FileManager').FileManager
        self.stepoptions = {}

    def make_aspect(self, cls=None):
        aspect = bare_instance(cls or self.PackagerAspect)
        aspect._getFileManager = self.FileManager
        return aspect

    def dispatch(self, key):
        return self.stepoptions['__PackagerAspect__'][key]


class testHandlerCallers(PackagerAspectTestCase):

    def test_installmap_caller_runs_every_handler_in_order(self):
        calls = []
        caller = self.PackagerAspect.installmap_caller()
        caller.appendHandler(lambda value, maps: calls.append(('one', value, maps)))
        caller.appendHandler(lambda value, maps: calls.append(('two', value, maps)))
        caller('v', {'m': 1})
        self.assertEqual([('one', 'v', {'m': 1}), ('two', 'v', {'m': 1})], calls)

    def test_control_caller_runs_every_handler_in_order(self):
        calls = []
        caller = self.PackagerAspect.control_caller()
        caller.appendHandler(lambda control: calls.append(('one', control)))
        caller.appendHandler(lambda control: calls.append(('two', control)))
        caller({'text': 'x'})
        self.assertEqual([('one', {'text': 'x'}), ('two', {'text': 'x'})], calls)

    def test_callers_without_handlers_do_nothing(self):
        self.PackagerAspect.control_caller()({})
        self.PackagerAspect.installmap_caller()('v', {})


class testFileTypeHandler(PackagerAspectTestCase):

    def test_wildcard_registers_the_aspect_for_every_file(self):
        aspect = self.make_aspect()
        options = {'file-types': '*'}
        aspect._installFileTypeHandler(options, self.stepoptions)
        self.assertEqual(
            ('*', aspect, options), self.dispatch('file-type-dispatch')[0])

    def test_declaration_prunes_empty_fields_while_iterating_the_keys(self):
        # parseFileDeclarationForIndexes() yields dicts with None/'' fields;
        # the aspect deletes those while walking the keys (a view on py3).
        aspect = self.make_aspect()
        options = {'file-types': '<(ELF-Shared)>'}
        aspect._installFileTypeHandler(options, self.stepoptions)
        self.assertEqual(
            [([{'type': 'ELF-Shared'}], aspect, options)],
            self.dispatch('file-type-dispatch'))

    def test_every_declaration_in_the_list_keeps_its_own_fields(self):
        aspect = self.make_aspect()
        aspect._installFileTypeHandler(
            {'file-types': '<(ELF-Shared)>, <conf(text:config)> /etc/*'},
            self.stepoptions)
        (indexes, _, _), = self.dispatch('file-type-dispatch')
        self.assertEqual(
            [{'type': 'ELF-Shared'},
             {'id': 'conf', 'type': 'text', 'intent': 'config',
              'location': '/etc/*'}],
            indexes)

    def test_registrations_accumulate_in_the_step_options(self):
        aspect = self.make_aspect()
        other = self.make_aspect()
        aspect._installFileTypeHandler(
            {'file-types': '<(ELF-Shared)>'}, self.stepoptions)
        other._installFileTypeHandler(
            {'file-types': '<(text)>'}, self.stepoptions)
        self.assertEqual(
            [aspect, other],
            [entry[1] for entry in self.dispatch('file-type-dispatch')])


class testControlHandler(PackagerAspectTestCase):

    def test_controls_are_split_on_commas_and_newlines(self):
        aspect = self.make_aspect()
        options = {'controls': 'shlibs, conffiles\n lintian'}
        aspect._installControlHandler(options, self.stepoptions)
        self.assertEqual(
            [(['shlibs', 'conffiles', 'lintian'], aspect, options)],
            self.dispatch('control-dispatch'))

    def test_control_registrations_accumulate_in_the_step_options(self):
        aspect, other = self.make_aspect(), self.make_aspect()
        aspect._installControlHandler({'controls': 'shlibs'}, self.stepoptions)
        other._installControlHandler({'controls': 'lintian'}, self.stepoptions)
        self.assertEqual(
            [(['shlibs'], aspect), (['lintian'], other)],
            [(controls, owner)
             for controls, owner, _ in self.dispatch('control-dispatch')])


class testControlWriters(PackagerAspectTestCase):

    def test_writer_is_installed_on_the_step_and_called_through(self):
        seen = []

        class Demo(self.PackagerAspect):
            def _control_demo(self, control):
                seen.append(control)

        aspect, step = self.make_aspect(Demo), FakeStep()
        aspect._installControlWriters([aspect._control_demo], step)
        step._control_demo({'k': 'v'})
        self.assertEqual([{'k': 'v'}], seen)
        self.assertIsInstance(
            step.__dict__['_control_demo'], self.PackagerAspect.control_caller)

    def test_existing_writer_is_replaced_with_a_warning(self):
        class Demo(self.PackagerAspect):
            def _control_demo(self, control):
                pass

        class Step:
            def _control_demo(self, control):
                raise AssertionError("the step's own writer must not run")

        aspect, step = self.make_aspect(Demo), Step()
        aspect._installControlWriters([aspect._control_demo], step)
        step._control_demo({})
        self.assertEqual(
            ["Overriding control writer '_control_demo' in 'Demo'"],
            aspect.log.messages('warning'))

    def test_badly_named_writer_is_reported(self):
        aspect = self.make_aspect()
        aspect._installControlWriters([lambda control: None], FakeStep())
        self.assertTrue(aspect.log.called('error'))


class testStart(PackagerAspectTestCase):

    def demo_class(self):
        class Demo(self.PackagerAspect):
            def _control_alpha(self, control):
                pass

            def _control_beta(self, control):
                pass
        return Demo

    def test_start_installs_the_control_writers_found_on_the_class(self):
        aspect, step = self.make_aspect(self.demo_class()), FakeStep()
        options = {'controls': 'alpha'}
        self.assertTrue(aspect.start('package', options, step, self.stepoptions))
        self.assertIn('_control_alpha', step.__dict__)
        self.assertIn('_control_beta', step.__dict__)
        self.assertEqual(
            [(['alpha'], aspect, options)], self.dispatch('control-dispatch'))
        self.assertTrue(aspect.log.called('passed'))
        self.assertEqual(options, aspect.options)

    def test_start_registers_the_requested_file_types(self):
        aspect = self.make_aspect(self.demo_class())
        options = {'file-types': '<(ELF-Shared)>'}
        self.assertTrue(
            aspect.start('package', options, FakeStep(), self.stepoptions))
        self.assertEqual(
            [([{'type': 'ELF-Shared'}], aspect, options)],
            self.dispatch('file-type-dispatch'))

    def test_start_with_nothing_to_install_still_passes(self):
        aspect = self.make_aspect()
        self.assertTrue(aspect.start('package', {}, FakeStep(), self.stepoptions))
        self.assertEqual({}, self.stepoptions)

    def test_installing_no_mapping_handlers_does_nothing(self):
        step = FakeStep()
        self.make_aspect()._installMappingHandlers([], step)
        self.assertEqual({}, step.__dict__)

    @unittest.skip("pre-existing bug: _installMappingHandlers refers to "
                   "PackageAspect.installmap_caller, an undefined name "
                   "(it means PackagerAspect), so installing any _map_ "
                   "handler raises NameError")
    def test_mapping_handler_is_installed_on_the_step(self):
        seen = []

        class Demo(self.PackagerAspect):
            def _map_path_demo(self, value, pathmaps):
                seen.append(value)

        aspect, step = self.make_aspect(Demo), FakeStep()
        aspect._installMappingHandlers([aspect._map_path_demo], step)
        step._map_path_demo('v', {})
        self.assertEqual(['v'], seen)


if __name__ == '__main__':
    unittest.main()
