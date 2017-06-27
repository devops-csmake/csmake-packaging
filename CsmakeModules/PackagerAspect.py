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
from Csmake.CsmakeAspect import CsmakeAspect
from Csmake.FileManager import FileManager

class PackagerAspect(CsmakeAspect):
    """Purpose: To augment the Packager functionality.
                These aspects should only modify Packager based steps
       Type: Aspect   Library: csmake-packaging
       Description:
                A packager aspect can also add file handler types
                by naming methods _map_<installmaptype>_<map-definition>
                Packager aspects can also add control writers
                by naming methods _control_<control name>.
                Typically, you would extend this class to perform a
                specific augmentation that your particular package would
                need.
       Phases: *
       Joinpoints Added: See Packager documentation for details.
           begin_map, end_map, map_file, mapping_complete,
           control
       Options:
           file-types - (OPTIONAL) Will request advice for
                        file mapping join points when a file
                        of the given type(s) is processed from an
                        installmap.  (comma or newline delimited)
           controls - (OPTIONAL) Will request advice for
                        handling package control entries provided.
                        Multiple control names may be comma or
                        newline delimited.
      """

    class installmap_caller:
        def __init__(self):
            self.handlers = []

        def __call__(self, value, pathmaps):
            for handler in self.handlers:
                handler(value, pathmaps)

        def appendHandler(self, handler):
            self.handlers.append(handler)

    class control_caller:
        def __init__(self):
            self.handlers = []

        #TODO: We may want to simply restrict this to
        #      a single implementation here.
        def __call__(self, value):
            for handler in self.handlers:
                handler(value)

        def appendHandler(self, handler):
            self.handlers.append(handler)

    def _installMappingHandlers(self, pathhandlers, step):
        """Call this method to install aspect mapping handlers from
           the 'start' joinpoint handler"""
        for pathhandler in pathhandlers:
            handler = pathhandler.__name__
            if not handler.startswith('_map_'):
                self.log.error("'%s' is not a valid mapping handler", handler)
                self.log.error("  Mapping handler naming convention: _map_<installmaptype>_<map-definition>")
            if hasattr(step, handler):
                orig = getattr(step, handler)
                if not isinstance(step.__dict__[handler], PackageAspect.installmap_caller):
                    step.__dict__['____orig_%s'] = orig
                    step.__dict__[handler] = PackageAspect.installmap_caller()
                    step.__dict__[handler].appendHandler(orig)
            else:
                step.__dict__[handler] = PackageAspect.installmap_caller()
            step.__dict__[handler].appendHandler(pathhandler)

    def _installControlWriters(self, controlWriters, step):
        """Call this method to install aspect control writing handlers
           from the 'start' joinpoint handler"""
        for controlWriter in controlWriters:
            writer = controlWriter.__name__
            if not writer.startswith('_control_'):
                self.log.error("'%s' is not a valid control writer", controlWriter)
                self.log.error("   Control handler naming convention: _control_<controlname>")
            if hasattr(step, writer):
                self.log.warning("Overriding control writer '%s' in '%s'", writer, self.__class__.__name__)
            step.__dict__[writer] = PackagerAspect.control_caller()
            step.__dict__[writer].appendHandler(controlWriter)

    def _installFileTypeHandler(self, options, stepoptions):
        partName = "__PackagerAspect__"
        if partName not in stepoptions:
            stepoptions[partName] = {}
        if 'file-type-dispatch' not in stepoptions[partName]:
            stepoptions[partName]['file-type-dispatch'] = []

        dispatch = stepoptions[partName]['file-type-dispatch']
        self.typemaps = options['file-types']
        if '*' == self.typemaps:
            dispatch.append(('*', self, options))
        filemanager = self._getFileManager()
        indexes = filemanager.parseFileDeclarationForIndexes(self.typemaps)
        for index in indexes:
            keys = index.keys()
            for key in keys:
                if index[key] is None or len(index[key]) == 0:
                    del index[key]
        dispatch.append((indexes, self, options))

    def _installControlHandler(self, options, stepoptions):
        controls = options['controls']
        controls = [ c.strip() for c in ','.join(controls.split('\n')).split(',') ]
        partName = '__PackagerAspect__'
        if partName not in stepoptions:
            stepoptions[partName] = {}
        if 'control-dispatch' not in stepoptions[partName]:
            stepoptions[partName]['control-dispatch'] = []
        dispatch = stepoptions[partName]['control-dispatch']
        dispatch.append((controls, self, options))

    def start(self, phase, options, step, stepoptions):
        self.options = options
        if 'file-types' in options:
            self._installFileTypeHandler(options, stepoptions)
            self.log.devdebug("Installed '%s' for: %s", self.__class__.__name__, options['file-types'])
        if 'controls' in options:
            self._installControlHandler(options, stepoptions)
            self.log.devdebug("Installed '%s' for: %s", self.__class__.__name__, options['controls'])
        mappingHandlers = []
        controlWriters = []
        for key, value in self.__class__.__dict__.iteritems():
            if key.startswith('_map_'):
                mappingHandlers.append(getattr(self, key))
            elif key.startswith('_control_'):
                controlWriters.append(getattr(self, key))
        self._installMappingHandlers(mappingHandlers, step)
        self._installControlWriters(controlWriters, step)
        self.log.passed()
        return True
