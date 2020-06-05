import os
import shutil
import subprocess
import tempfile
from collections import OrderedDict
import numpy
import operator
import ntpath
import stat
import platform
import re
import json

from PythonQt import QtGui
from PythonQt.CRIMSON import FaceType
from PythonQt.CRIMSON import Utils

from CRIMSONCore.SolutionStorage import SolutionStorage
from CRIMSONSolver.SolverStudies import PresolverExecutableName, PhastaSolverIO, PhastaConfig
from CRIMSONSolver.SolverSetupManagers.FlowProfileGenerator import FlowProfileGenerator
from CRIMSONSolver.SolverStudies.FileList import FileList
from CRIMSONSolver.SolverStudies.SolverInpData import SolverInpData
from CRIMSONSolver.SolverStudies.Timer import Timer
from CRIMSONSolver.BoundaryConditions import NoSlip, InitialPressure, RCR, ZeroPressure, PrescribedVelocities, \
    DeformableWall, Netlist, PCMRI, RAD
from CRIMSONSolver.Materials import MaterialData


# A helper class providing lazily-evaluated quantities for material computation
class MaterialFaceInfo(object):
    def __init__(self, vesselForestData, meshData, faceIdentifier, meshFaceInfoData):
        self.vesselForestData = vesselForestData
        self.meshData = meshData
        self.meshFaceInfoData = meshFaceInfoData
        self.faceIdentifier = faceIdentifier

    def getMeshFaceInfo(self):
        return self.meshFaceInfoData
                                                                                                
    def getFaceCenter(self):
        if 'center' not in self.__dict__:
            self.center = self.meshData.getNodeCoordinates(self.meshFaceInfoData[2])
            self.center = map(operator.add, self.center, self.meshData.getNodeCoordinates(self.meshFaceInfoData[3]))
            self.center = map(operator.add, self.center, self.meshData.getNodeCoordinates(self.meshFaceInfoData[4]))

            for i in xrange(3):
                self.center[i] /= 3

        return self.center

    #    This version is actually slower than getFaceCenter()
    #    def getFaceCenter2(self, faceInfo, meshData):
    #        center = numpy.array(meshData.getNodeCoordinates(faceInfo[2]))
    #        numpy.add(center, meshData.getNodeCoordinates(faceInfo[3]), center)
    #        numpy.add(center, meshData.getNodeCoordinates(faceInfo[4]), center)
    #
    #        return center / 3

    def getLocalRadius(self):
        if 'localRadius' not in self.__dict__:
            self._computeLocalRadiusAndArcLength()

        return self.localRadius

    def getArcLength(self):
        if 'arcLength' not in self.__dict__:
            self._computeLocalRadiusAndArcLength()

        return self.arcLength

    def getVesselPathCoordinateFrame(self):
        if 'vesselPathCoordinateFrame' not in self.__dict__:
            faceCenter = self.getFaceCenter()
            self.vesselPathCoordinateFrame = \
                self.vesselForestData.getVesselPathCoordinateFrame(self.faceIdentifier, faceCenter[0], faceCenter[1],
                                                                   faceCenter[2]) \
                    if self.vesselForestData is not None else []

        return self.vesselPathCoordinateFrame

    def _computeLocalRadiusAndArcLength(self):
        faceCenter = self.getFaceCenter()
        self.localRadius, self.arcLength = \
            self.vesselForestData.getClosestPoint(self.faceIdentifier, faceCenter[0], faceCenter[1], faceCenter[2]) \
                if self.vesselForestData is not None else (0, 0)


class SolverSetupType(object):
    FLUID_SIMULATION = 'fluid_simulation'
    PARTICLE_SIMULATION = 'particle_simulation'


def mkdirNoFailOnExist(path):
    try:
        os.mkdir(path)
    except OSError:
        pass

def sanitizeFileNameForWindows(filename):
    invalid_filename_characters='[ \t<>:\"\\\/\|?\*]'
    return re.sub(invalid_filename_characters, '_', filename)


class SolverStudy(object):
    def __init__(self):
        self.meshNodeUID = ""
        self.solverParametersNodeUID = ""
        self.boundaryConditionSetNodeUIDs = []
        self.materialNodeUIDs = []
        self.particleBolusMeshNodeUID = ""
        self.particleBinMeshNodeUIDs = []

    def getMeshNodeUID(self):
        return self.meshNodeUID

    def setMeshNodeUID(self, uid):
        self.meshNodeUID = uid

    def getParticleBolusMeshNodeUID(self):
        if 'particleBolusMeshNodeUID' not in self.__dict__:
            self.particleBolusMeshNodeUID = ""  # Support for old scenes
        return self.particleBolusMeshNodeUID

    def setParticleBolusMeshNodeUID(self, uid):
        self.particleBolusMeshNodeUID = uid

    def getParticleBinMeshNodeUIDs(self):
        if 'particleBinMeshNodeUIDs' not in self.__dict__:
            self.particleBinMeshNodeUIDs = []  # Support for old scenes
        return self.particleBinMeshNodeUIDs

    def setParticleBinMeshNodeUIDs(self, uids):
        self.particleBinMeshNodeUIDs = uids

    def getSolverParametersNodeUID(self):
        return self.solverParametersNodeUID

    def setSolverParametersNodeUID(self, uid):
        self.solverParametersNodeUID = uid

    def getBoundaryConditionSetNodeUIDs(self):
        return self.boundaryConditionSetNodeUIDs

    def setBoundaryConditionSetNodeUIDs(self, uids):
        self.boundaryConditionSetNodeUIDs = uids

    def getMaterialNodeUIDs(self):
        if 'materialNodeUIDs' not in self.__dict__:
            self.materialNodeUIDs = []  # Support for old scenes
        return self.materialNodeUIDs

    def setMaterialNodeUIDs(self, uids):
        self.materialNodeUIDs = uids

    def loadSolution(self):
        fullNames = QtGui.QFileDialog.getOpenFileNames(None, "Load solution")

        if not fullNames:
            return

        solutions = SolutionStorage()
        for fullName in fullNames:
            fileName = os.path.basename(fullName)
            if fileName.startswith('restart'):
                config = PhastaConfig.restartConfig
            elif fileName.startswith('ybar'):
                config = PhastaConfig.ybarConfig
            else:
                QtGui.QMessageBox.critical(None, "Solution loading failed",
                                           "File {0} was not recognized as a phasta solution file.\n"
                                           "Only 'restart.*' and 'ybar.*' files are supported.".format(
                                               fullName))
                continue
            try:
                with open(fullName, 'rb') as inFile:
                    fields = PhastaSolverIO.readPhastaFile(PhastaSolverIO.PhastaRawFileReader(inFile), config)

                for fieldName, fieldData in fields.iteritems():
                    solutions.arrays[fieldName] = SolutionStorage.ArrayInfo(fieldData.transpose())

            except Exception as e:
                QtGui.QMessageBox.critical(None, "Solution loading failed",
                                           "Failed to load solution from file {0}:\n{1}.".format(fullName, str(e)))
                continue

        return solutions


    # def runParticleTracking(self):
    #     simulationDirectory = QtGui.QFileDialog.getExistingDirectory(None, 'Set simulation directory')

    #     if not simulationDirectory:
    #         return

    #     Utils.logInformation("REACHED 0")
    #     self.setNProcsCaseFolderToReadForParticleSim(simulationDirectory)
    #     self._runParticleTracking(simulationDirectory)


    # def _runParticleTracking(self, trackingDirectory): #, numberOfProcessors):
    #     if platform.system() == "Windows":  # todo add linux case, below - in particular to avoid calling cmd.exe
    #         particleTrackingBatchFileName = "all_particles_run_windows.bat"
    #         particleTrackingScriptDirectory = os.path.normpath(os.path.join(os.path.realpath(__file__),
    #                                                              os.pardir, "particle_tracking",))

    #         particleTrackingBatchFileFullPath = os.path.join(particleTrackingScriptDirectory, particleTrackingBatchFileName)

    #         Utils.logInformation('Running particle tracking from ' + particleTrackingBatchFileFullPath)
    #         Utils.logInformation('Using working directory ' + trackingDirectory)

    #         # In case the paths both contain spaces, this pattern of double quotes is required
    #         # so that the Windows shell understands what we are asking it to do.
    #         #
    #         # Specifically, "" to start and end the whole argument set, and " around each path.
    #         command = "powershell.exe " + "\"\"" + particleTrackingBatchFileFullPath + "\" \'" + trackingDirectory + "\'\""
    #         # command = ["powershell.exe", particleTrackingBatchFileFullPath, trackingDirectory]

    #         # Launch in a new console so e.g. ctrl+c on the flowsolver console doesn't terminate CRIMSON
    #         subprocess.Popen(command,
    #                          cwd=trackingDirectory,
    #                          creationflags=subprocess.CREATE_NEW_CONSOLE)


    def _checkSystemForFlowsolver(self):
        invalid_hostname = True if re.search(r'[^a-zA-Z0-9\-\.]', platform.node()) else False

        if invalid_hostname:
                QtGui.QMessageBox.warning(None, "Hostname may be invalid",
                               "MPI may not work if your computer name contains non-standard characters.\n"
                               "It may only contain a-z, A-Z, 0-9, ., and -. It was {}\n"
                               "If flowsolver does not run, please change your computer name.".format(
                                   platform.node())
                                          )


    def runFlowsolver(self):
        self._checkSystemForFlowsolver()

        simulationDirectory = QtGui.QFileDialog.getExistingDirectory(None, 'Set simulation directory')

        if not simulationDirectory:
            return

        self._runFlowsolver(simulationDirectory)

    def _runFlowsolver(self, simulationDirectory): #, numberOfProcessors):
        if platform.system() == "Windows":  # todo add linux case, below - in particular to avoid calling cmd.exe
            flowsolverBatchFileName = "mysolver.bat"
            flowsolverDirectory = os.path.normpath(os.path.join(os.path.realpath(__file__),
                                                                 os.pardir, "flowsolver",))

            flowsolverBatchFileFullPath = os.path.join(flowsolverDirectory, flowsolverBatchFileName)

            Utils.logInformation('Running flowsolver from ' + flowsolverBatchFileFullPath)
            Utils.logInformation('Using working directory ' + simulationDirectory)

            # In case the paths both contain spaces, this pattern of double quotes is required
            # so that the Windows shell understands what we are asking it to do.
            #
            # Specifically, "" to start and end the whole argument set, and " around each path.
            command = "cmd.exe " + "/c \"\"" + flowsolverBatchFileFullPath + "\" \"" + simulationDirectory + "\"\""

            # Launch in a new console so e.g. ctrl+c on the flowsolver console doesn't terminate CRIMSON
            subprocess.Popen(command,
                             cwd=flowsolverDirectory,
                             creationflags=subprocess.CREATE_NEW_CONSOLE)

    def _writeMesh(self, meshData, fileList):
        with Timer('Written coordinates'):
            self._writeNodeCoordinates(meshData, fileList)
        with Timer('Written connectivity'):
            self._writeConnectivity(meshData, fileList)
        with Timer('Written adjacency'):
            self._writeAdjacency(meshData, fileList)

    # Called from Modules\PythonSolverSetupService\src\PythonSolverStudyData.cpp
    #                      _pyStudyObject.call("writeSolverSetup",...
    def writeSolverSetup(self, vesselForestData, solidModelData, meshData, solverParameters, boundaryConditions,
                         materials, vesselPathNames, solutionStorage):
        outputDir = QtGui.QFileDialog.getExistingDirectory(None, 'Select output folder')

        if not outputDir:
            return

        setupType = SolverSetupType.FLUID_SIMULATION
        self._writeSolverSetupWithDirectory(vesselForestData, solidModelData, meshData, solverParameters, boundaryConditions,
                         materials, vesselPathNames, solutionStorage, outputDir)

    # Writes the fluid simulation folder, too.
    def writeParticleSetup(self, vesselForestData, solidModelData, meshData, solverParameters, boundaryConditions,
                         materials, vesselPathNames, solutionStorage, bolusMeshData, binMeshDataList):
        outputDir_particles = QtGui.QFileDialog.getExistingDirectory(None, 'Select output folder')

        if not outputDir_particles:
            return

        setupTasks = []
        setupTasks.append({'type': SolverSetupType.FLUID_SIMULATION, 'subdirectory': 'fluid_sim', 'meshData': meshData})
        setupTasks.append({'type': SolverSetupType.PARTICLE_SIMULATION, 'subdirectory': 'particle_bolus', 'meshData': bolusMeshData})
        
        binNames = []

        for index, binMesh in enumerate(binMeshDataList):
            binName = sanitizeFileNameForWindows(binMesh.getDataNodeName())
            binNames.append('particle_bin_' + binName)
            subdirectory_name = "particle_bin_" + binName
            setupTasks.append({'type': SolverSetupType.PARTICLE_SIMULATION, 'subdirectory': subdirectory_name, 'meshData': binMesh})

        for task in setupTasks:
            outputDir = os.path.join(outputDir_particles, task['subdirectory'])
            mkdirNoFailOnExist(outputDir)
            
            if task['type'] == SolverSetupType.FLUID_SIMULATION:
                self._writeSolverSetupWithDirectory(vesselForestData, solidModelData, task['meshData'], solverParameters, boundaryConditions,
                                               materials, vesselPathNames, solutionStorage, outputDir)
            elif task['type'] == SolverSetupType.PARTICLE_SIMULATION:
                
                presolverDir = os.path.join(outputDir, 'presolver')
                mkdirNoFailOnExist(presolverDir)

                try:
                    fileList = FileList(outputDir)
                    self._writeMesh(task['meshData'], fileList)
                except Exception as e:
                    Utils.logError(str(e))
                    raise
                finally:
                    fileList.close()

            else:
                Utils.logError("Unknown task type in SolverStudy.py.")
                return

        self._writeParticleConfigJson(solverParameters, outputDir_particles, binNames)


    def _writeSolverSetupWithDirectory(self, vesselForestData, solidModelData, meshData, solverParameters, boundaryConditions,
                         materials, vesselPathNames, solutionStorage, outputDir):

        if not outputDir:
            return

        if solutionStorage is not None:
            if QtGui.QMessageBox.question(None, 'Write solution to the solver output?',
                                          'Would you like to use the solutions in the solver output?',
                                          QtGui.QMessageBox.Yes | QtGui.QMessageBox.No,
                                          QtGui.QMessageBox.Yes) != QtGui.QMessageBox.Yes:
                solutionStorage = None

        presolverDir = os.path.join(outputDir, 'presolver')
        if not os.path.exists(presolverDir):
            os.makedirs(presolverDir)

        fileList = FileList(outputDir)

        try:
            faceIndicesAndFileNames = self._computeFaceIndicesAndFileNames(solidModelData, vesselPathNames)
            solverInpData = SolverInpData(solverParameters, faceIndicesAndFileNames)

            supreFile = fileList[os.path.join('presolver', 'the.supre')]

            self._writeSupreHeader(meshData, supreFile)
            self._writeSupreSurfaceIDs(faceIndicesAndFileNames, supreFile)

            with Timer('Written nbc and ebc files'):
                faceIndicesInAllExteriorFaces = self._writeNbcEbc(solidModelData, meshData, faceIndicesAndFileNames,
                                                                  fileList)
            self._writeMesh(meshData, fileList)

            with Timer('Written boundary conditions'):
                self._writeBoundaryConditions(vesselForestData, solidModelData, meshData, boundaryConditions,
                                              materials, faceIndicesAndFileNames, solverInpData, fileList,
                                              faceIndicesInAllExteriorFaces)

            self._writeSolverSetup(solverInpData, fileList)

            supreFile.write('write_geombc  geombc.dat.1\n')
            supreFile.write('write_restart  restart.0.1\n')

            fileList['numstart.dat', 'wb'].write('0\n')
            fileList.close()

            with Timer('Ran presolver'):
                self._runPresolver(os.path.join(outputDir, 'presolver', 'the.supre'), outputDir,
                                   ['geombc.dat.1', 'restart.0.1'])

            if solutionStorage is not None:
                with Timer('Appended solutions'):
                    self._appendSolutionsToRestart(outputDir, solutionStorage)

                Utils.logInformation('Done')

        except Exception as e:
            Utils.logError(str(e))
            fileList.close()
            raise


    def _appendSolutionsToRestart(self, outputDir, solutionStorage):
        restartFileName = os.path.join(outputDir, 'restart.0.1')
        with open(restartFileName, 'rb') as restartFile:
            rawReader = PhastaSolverIO.PhastaRawFileReader(restartFile)
            dataBlocksToReplace = ['byteorder magic number']
            newFields = {}
            for name, dataInfo in solutionStorage.arrays.iteritems():
                arrayDesc, fieldDesc = PhastaConfig.restartConfig.findDescriptorAndField(name)
                if arrayDesc is None:
                    Utils.logWarning(
                        'Cannot write solution \'{0}\' to the restart file. Skipping.'.format(name))
                    continue
                Utils.logInformation('Appending solution data \'{0}\'...'.format(name))

                dataBlocksToReplace.append(arrayDesc.phastaDataBlockName)
                newFields[fieldDesc.name] = dataInfo.data.transpose()

            _, tempFileName = tempfile.mkstemp()
            with open(tempFileName, 'wb') as tempFile:
                rawWriter = PhastaSolverIO.PhastaRawFileWriter(tempFile)
                rawWriter.writeFileHeader()

                for blockName, blockDescriptor in rawReader.blockDescriptors.iteritems():
                    if blockDescriptor.totalBytes == -1:  # header only
                        rawWriter.writeHeader(blockName, 0, blockDescriptor.headerElements)
                    elif blockName not in dataBlocksToReplace:  # header and data
                        rawWriter.writeRawData(blockName, rawReader.getRawData(blockName),
                                               blockDescriptor.headerElements)

                PhastaSolverIO.writePhastaFile(rawWriter, PhastaConfig.restartConfig, newFields)

        shutil.copy(tempFileName, restartFileName)

    def _runPresolver(self, supreFile, outputDir, outputFiles):
        presolverExecutable = os.path.normpath(os.path.join(os.path.realpath(__file__), os.pardir,
                                                            PresolverExecutableName.getPresolverExecutableName()))
        Utils.logInformation('Running presolver from ' + presolverExecutable)

        if platform.system() != 'Windows':
            os.chmod(presolverExecutable, os.stat(presolverExecutable).st_mode | stat.S_IEXEC)

        supreDir, supreFileName = os.path.split(supreFile)
        p = subprocess.Popen([presolverExecutable, supreFileName], cwd=supreDir,
                             stderr=subprocess.STDOUT)  # stdout=subprocess.PIPE,

        out, _ = p.communicate()
        # self._logPresolverOutput(out)

        if p.returncode != 0:
            Utils.logError("Presolver run has failed.")
            return

        try:
            Utils.logInformation("Moving output files to output folder")
            for fName in outputFiles:
                fullName = os.path.normpath(os.path.join(supreFile, os.path.pardir, fName))
                shutil.copy(fullName, outputDir)
                os.remove(fullName)
        except Exception as e:
            Utils.logError("Failed to move output files: " + str(e))
            raise

    def _logPresolverOutput(self, out):
        for s in out.splitlines():
            if s.find('ERROR') != -1:
                Utils.logError(s)
            else:
                Utils.logInformation(s)

    # See https://crimsonpythonmodules.readthedocs.io/en/latest/concepts.html
    # for more documentation about functions that operate on meshData.
    def _writeAdjacency(self, meshData, fileList):
        # node and element indices are 0-based for presolver adjacency (!)
        outFileAdjacency = fileList[os.path.join('presolver', 'the.xadj')]

        xadjString = 'xadj: {0}\n'.format(meshData.getNElements() + 1)
        outFileAdjacency.write(xadjString)

        # reserve space in the beginning of file
        outFileAdjacency.write(' ' * 50 + '\n')

        # where the.xadj files are in the form
        # xadj: <numberOfElements>
        #   note that this is necessary to find the "second" section of the file
        # adjncy: <running total number of adjancent faces?>

        #Why is this named curIndex? looks more like the total number of faces with connections
        curIndex = 0

        # Part 1 of file: Write the running total number of faces with connections? Iterating through every single element
        outFileAdjacency.write('0\n')
        for i in xrange(meshData.getNElements()):
            #note: getAdjacentElements returns a list of element indexes that are adjacent to element index i
            curIndex += len(meshData.getAdjacentElements(i))
            outFileAdjacency.write('{0}\n'.format(curIndex))

        # Part 2 of file: write the adjacent element indexes for each element
        for i in xrange(meshData.getNElements()):
            for adjacentId in meshData.getAdjacentElements(i):
                outFileAdjacency.write('{0}\n'.format(adjacentId))

        outFileAdjacency.seek(len(xadjString) + len(os.linesep) - 1)  # +1 for potential \r
        outFileAdjacency.write('adjncy: {0}'.format(curIndex))

    def _writeConnectivity(self, meshData, fileList):
        outFileConnectivity = fileList[os.path.join('presolver', 'the.connectivity')]

        for i in xrange(meshData.getNElements()):
            # node and element indices are 1-based for presolver
            # every line in this file is in the form <elementIndex> <point1> <point2> <point3> <point4>,
            # where <elementIndex> identifies a tetrahedron in the mesh and point1-4 are the vertex indexes of the selected tetrahedron.
            outFileConnectivity.write(
                '{0} {1[0]} {1[1]} {1[2]} {1[3]}\n'.format(i + 1, [x + 1 for x in meshData.getElementNodeIds(i)]))

    def _writeNodeCoordinates(self, meshData, fileList):
        outFileCoordinates = fileList[os.path.join('presolver', 'the.coordinates')]

        for i in xrange(meshData.getNNodes()):
            # node indices are 1-based for presolver
            # every line in this file is in the form <nodeIndex> <nodeX> <nodeY> <nodeZ>,
            # where nodeIndex identifies a node (vertex) and nodeX/Y/Z are the coordinates of the node.
            outFileCoordinates.write('{0} {1[0]} {1[1]} {1[2]}\n'.format(i + 1, meshData.getNodeCoordinates(i)))

    def _writeNbc(self, meshData, faceIdentifiers, outputFile):
        nodeIndices = set()

        for faceIdentifier in faceIdentifiers:
            nodeIndices.update(meshData.getNodeIdsForFace(faceIdentifier))

        # where entries in nbc files are in the form <nodeIndex>,
        # where I think <nodeIndex> is a vertex index of a face in <faceIndentifiers>,
        # and the file in total is the set of all nodes (vertexes) in the set of faces.
        for i in sorted(nodeIndices):
            outputFile.write('{0}\n'.format(i + 1))  # Node indices are 1-based for presolver

    def _writeEbc(self, meshData, faceIdentifiers, outputFile):
        faceIndicesInFile = []
        for faceIdentifier in faceIdentifiers:
            for info in meshData.getMeshFaceInfoForFace(faceIdentifier):
                # where entries in ebc files are in the form <elementIndex> <faceIndex> <node1Index> <node2Index> <node3Index>
                # where <node1-3index> are the nodes of the (triangular) face <faceIndex> of (tetrahedral) element <elementIndex> 
                l = '{0}\n'.format(
                    ' '.join(str(x + 1) for x in info))  # element and node indices are 1-based for presolver
                outputFile.write(l)

                faceIndicesInFile.append(info[1])

        return faceIndicesInFile

    def _writeNbcEbc(self, solidModelData, meshData, faceIndicesAndFileNames, fileList):
        allFaceIdentifiers = [solidModelData.getFaceIdentifier(i) for i in
                              xrange(solidModelData.getNumberOfFaceIdentifiers())]

        # Write wall.nbc
        self._writeNbc(meshData, [id for id in allFaceIdentifiers if id.faceType == FaceType.ftWall],
                       fileList[os.path.join('presolver', 'wall.nbc')])

        # Write per-face-identifier ebc and nbc files
        for i in xrange(solidModelData.getNumberOfFaceIdentifiers()):
            faceIdentifier = solidModelData.getFaceIdentifier(i)

            baseFileName = os.path.join('presolver', faceIndicesAndFileNames[faceIdentifier][1])
            self._writeNbc(meshData, [faceIdentifier], fileList[baseFileName + '.nbc'])
            self._writeEbc(meshData, [faceIdentifier], fileList[baseFileName + '.ebc'])

        # Write all_eterior_faces.ebc
        return self._writeEbc(meshData, allFaceIdentifiers,
                              fileList[os.path.join('presolver', 'all_exterior_faces.ebc')])


    def _getNumberOfProcessorsUsedInFluidSim(self, simulation_directory):
        directoryContents = os.listdir(simulation_directory)
        procsCaseFolders = [item for item in directoryContents if '-procs-case' in item]
        if len(procsCaseFolders) != 1:
            errorMessage = "ERROR: exactly one fluid simulation case (N-procs-case) folder in the fluid_sim directory is required."
            print errorMessage
            raise RuntimeError(errorMessage)

        procsCaseFolder = procsCaseFolders[0]
        firstDashLocation = procsCaseFolder.find('-')
        numberOfFluidSimProcessors = int(procsCaseFolder[0:firstDashLocation])

        return numberOfFluidSimProcessors


    def _getParticleBinConfig(self, binName, binStartTime, binEndTime):
        binConfigTemplate = dict()
        binConfigTemplate["bin name"] = binName
        binConfigTemplate["bin data"] = u"plap"
        binConfigTemplate["bin time intervals"] = list()
        binConfigTemplate["bin time intervals"].append({"start": binStartTime, "end": binEndTime})
        binConfigTemplate["bin file names"] = list()
        binConfigTemplate["bin file names"].append(binName + u".vtu")

        return binConfigTemplate


    def setNProcsCaseFolderToReadForParticleSim(self, outputDir_particles):
        try:
            fileList_particleParentDir = FileList(outputDir_particles)
            particleConfigJsonFile = fileList_particleParentDir['particle_config.json', 'rb']

            particleConfig = json.load(particleConfigJsonFile)
            fluidSimFolderPath = outputDir_particles + os.sep + 'fluid_sim'
            particleConfig["simulation setup"]["fluid sim"]["n-procs-case n value"] = "{}".format(
                                              self._getNumberOfProcessorsUsedInFluidSim(fluidSimFolderPath)
                                              )
        finally:
            fileList_particleParentDir.close()
        # Now re-open the json file in write mode, and re-write the json
        try:
            fileList_particleParentDir_out = FileList(outputDir_particles)
            particleConfigJsonFile_out = fileList_particleParentDir_out['particle_config.json', 'wb']
            json.dump(particleConfig, particleConfigJsonFile_out)
        finally:
            fileList_particleParentDir.close()


    def _writeParticleConfigJson(self, solverParameters, outputDir_particles, binNames):
        try:
            fileList_particleParentDir = FileList(outputDir_particles)
            
            particleConfigJsonFile = fileList_particleParentDir['particle_config.json', 'wb']

            props = solverParameters.getProperties()

            particleConfig = dict()
            particleConfig["data file base name"] = props["Particle simulation nametag"]
            particleConfig["input data start timestep"] = props["Start at fluid problem timestep"]
            particleConfig["input data end timestep"] = props["Finish at fluid problem timestep"]
            particleConfig["timesteps between restarts in input data"] = props["Number of time steps between restarts"]
            particleConfig["real time between restarts in input data"] = props["Number of time steps between restarts"] * props["Time step size"]
            particleConfig["tracking simulation starting timestep"] = props["Start at fluid problem timestep"]
            particleConfig["number of cycles to track for"] = props["Repeats"]
            particleConfig["wall has displacement field"] = False
            particleConfig["steps between repartitioning particles and writing output"] = 20
            particleConfig["real time through cardiac cycle when simulation starts"] = 0.0
            particleConfig["real time of first systole start"] = 0.0
            particleConfig["real time of first systole end"] = 1.0
            particleConfig["cardiac cycle length"] = 1.0
            particleConfig["maximum particle reinjections"] = props["Maximum reinjections"]
            particleConfig["tracking steps between each reinjection"] = int(props["Reinject bolus every"] / props["Time step size"])
            particleConfig["steps before first reinjection"] = int(props["Initial injection time"] / props["Time step size"])

            particleConfig["simulation setup"] = dict()
            particleConfig["simulation setup"]["data root"] = outputDir_particles
            particleConfig["simulation setup"]["number of processors to use"] = "{}".format(props["Number of processors to use"])
            particleConfig["simulation setup"]["fluid sim"] = dict()

            particleConfig["simulation setup"]["fluid sim"]["n-procs-case n value"] = "SET_AT_RUNTIME"

            particleConfig["space time bins"] = list()

            for binName in binNames:
                particleConfig["space time bins"].append(self._getParticleBinConfig(binName, 0.0, particleConfig["cardiac cycle length"]))

            json.dump(particleConfig, particleConfigJsonFile)

        finally:
            fileList_particleParentDir.close()


    def _writeSolverSetup(self, solverInpData, fileList):
        solverInpFile = fileList['solver.inp', 'wb']

        # Where solver.inp is data separated by categories, where catagories are in the form:
        #   #<CATEGORY NAME>
        #   #{
        #       SomeEntry = value
        #   #}
        for category, values in sorted(solverInpData.data.iteritems()):
            solverInpFile.write('\n\n# {0}\n# {{\n'.format(category))
            for kv in values.iteritems():
                solverInpFile.write('    {0[0]} : {0[1]}\n'.format(kv))
            solverInpFile.write('# }\n')

    def _validateBoundaryConditions(self, boundaryConditions):
        if len(boundaryConditions) == 0:
            Utils.logError('Cannot write CRIMSON solver setup without any boundary conditions selected')
            return False

        # Check unique BC's
        bcByType = {}
        for bc in boundaryConditions:
            bcByType.setdefault(bc.__class__.__name__, []).append(bc)

        hadError = False

        for bcType, bcs in bcByType.iteritems():
            if bcs[0].unique and len(bcs) > 1:
                Utils.logError(
                    'Multiple instances of boundary condition {0} are not allowed in a single study'.format(bcType))
                hadError = True

        return not hadError

    def _writeBoundaryConditions(self, vesselForestData, solidModelData, meshData, boundaryConditions, materials,
                                 faceIndicesAndFileNames, solverInpData, fileList, faceIndicesInAllExteriorFaces):
        if not self._validateBoundaryConditions(boundaryConditions):
            raise RuntimeError('Invalid boundary conditions. Aborting.')

        supreFile = fileList[os.path.join('presolver', 'the.supre')]

        class RCRInfo(object):
            def __init__(self):
                self.first = True
                self.faceIds = []

        rcrInfo = RCRInfo()

        class NetlistInfo(object):
            def __init__(self):
                self.faceIds = []

        netlistInfo = NetlistInfo()

        class BCTInfo(object):
            def __init__(self):
                self.first = True
                self.totalPoints = 0
                self.maxNTimeSteps = 0
                self.faceIds = []
                self.period = 1.1  # for RCR

        bctInfo = BCTInfo()

        validFaceIdentifiers = lambda bc: (x for x in bc.faceIdentifiers if
                                           solidModelData.faceIdentifierIndex(x) != -1)

        is_boundary_condition_type = lambda bc, bcclass: bc.__class__.__name__ == bcclass.__name__

        initialPressure = None

        materialStorage = self.computeMaterials(materials, vesselForestData, solidModelData, meshData)

        # Processing priority for a particular BC type defines the order of processing the BCs
        # Default value is assumed to be 1. The higher the priority, the later the BC is processed
        bcProcessingPriorities = {
            RCR.RCR.__name__: 2,  # Process RCR after PrescribedVelocities
            DeformableWall.DeformableWall.__name__: 3  # Process deformable wall last
        }

        bcCompare = lambda l, r: \
            cmp([bcProcessingPriorities.get(l.__class__.__name__, 1), l.__class__.__name__],
                [bcProcessingPriorities.get(r.__class__.__name__, 1), r.__class__.__name__])

        # Where the the.supre file is in the form:
        # <presolver command> <parameter>
        #
        # For many command types, it's <command> <fileName>
        # the presolver has a lookup table `cmd_table` in <repositoryRoot>\Presolver\cmd.cxx 
        # that associates command strings with functions.
        #
        # the.supre serves as the top level configuration file for the presolver.
        # 
        # In addition to specifying input data, the.supre also 
        # defines all input and output operations for the presolver.
        # e.g., the end result of the presolver may be to write geombc.dat.1 and restart.0.1

        for bc in sorted(boundaryConditions, cmp=bcCompare):
            if is_boundary_condition_type(bc, NoSlip.NoSlip):
                for faceId in validFaceIdentifiers(bc):
                    supreFile.write('noslip {0}.nbc\n'.format(faceIndicesAndFileNames[faceId][1]))
                supreFile.write('\n')

            elif is_boundary_condition_type(bc, InitialPressure.InitialPressure):
                initialPressure = bc.getProperties()['Initial pressure']
                supreFile.write('initial_pressure {0}\n\n'.format(initialPressure))

            elif is_boundary_condition_type(bc, RCR.RCR):
                rcrtFile = fileList['rcrt.dat']
                faceInfoFile = fileList['faceInfo.dat']

                if rcrInfo.first:
                    rcrInfo.first = False
                    rcrtFile.write('2\n')

                for faceId in validFaceIdentifiers(bc):
                    supreFile.write('zero_pressure {0}.ebc\n'.format(faceIndicesAndFileNames[faceId][1]))
                    faceInfoFile.write('RCR {0[0]} {0[1]}\n'.format(faceIndicesAndFileNames[faceId]))

                    rcrInfo.faceIds.append(str(faceIndicesAndFileNames[faceId][0]))

                    rcrtFile.write('2\n'
                                   '{0[Proximal resistance]}\n'
                                   '{0[Capacitance]}\n'
                                   '{0[Distal resistance]}\n'
                                   '0 0.0\n'
                                   '{1} 0.0\n'.format(bc.getProperties(), bctInfo.period))
                supreFile.write('\n')

            elif is_boundary_condition_type(bc, Netlist.Netlist):
                faceInfoFile = fileList['faceInfo.dat']

                for faceId in validFaceIdentifiers(bc):

                    if bc.getProperties()['Heart model']:
                        supreFile.write('prescribed_velocities {0}.nbc\n'.format(faceIndicesAndFileNames[faceId][1]))
                        faceInfoFile.write('Netlist Heart {0[0]} {0[1]}\n'.format(faceIndicesAndFileNames[faceId]))
                    else:
                        faceInfoFile.write('Netlist {0[0]} {0[1]}\n'.format(faceIndicesAndFileNames[faceId]))

                    if not bc.netlistSurfacesDat == '':
                        Utils.logInformation('Writing to file \'{0}\''.format('netlist_surfaces.dat'))
                        fileList['netlist_surfaces.dat', 'wb'].write(bc.netlistSurfacesDat)
                    else:
                        Utils.logWarning('No circuit file was specified for the Netlist at surface  \'{0}\'.'.format(
                            faceIndicesAndFileNames[faceId][0]))

                    dynamicAdjustmentScriptFileNamesAndContents = bc.getCircuitDynamicAdjustmentFiles()
                    for dynamicAdjustmentScriptName in dynamicAdjustmentScriptFileNamesAndContents:
                        fileContentsToWrite = dynamicAdjustmentScriptFileNamesAndContents[dynamicAdjustmentScriptName]
                        nameOfFileToWrite = ntpath.basename(dynamicAdjustmentScriptName)
                        Utils.logInformation('Writing file \'{0}\''.format(nameOfFileToWrite))
                        if fileList.isOpen(nameOfFileToWrite):
                            Utils.logWarning(
                                'File with name \'{0}\' occurs multiple times in solver setup. Overwriting. This is ok if all copies should be identical'.format(
                                    nameOfFileToWrite))
                        fileList[nameOfFileToWrite, 'wb'].write(fileContentsToWrite)

                    additionalDataFileNamesAndContents = bc.getCircuitAdditionalDataFiles()
                    for additionalDataFileName in additionalDataFileNamesAndContents:
                        fileContentsToWrite = additionalDataFileNamesAndContents[additionalDataFileName]
                        nameOfFileToWrite = ntpath.basename(additionalDataFileName)
                        Utils.logInformation('Writing file \'{0}\''.format(nameOfFileToWrite))
                        if fileList.isOpen(nameOfFileToWrite):
                            Utils.logWarning(
                                'File with name \'{0}\' occurs multiple times in solver setup. Overwriting. This is ok if all copies should be identical'.format(
                                    nameOfFileToWrite))
                        fileList[nameOfFileToWrite, 'wb'].write(fileContentsToWrite)

                    supreFile.write('zero_pressure {0}.ebc\n'.format(faceIndicesAndFileNames[faceId][1]))

                    netlistInfo.faceIds.append(str(faceIndicesAndFileNames[faceId][0]))

                supreFile.write('\n')




            elif is_boundary_condition_type(bc, ZeroPressure.ZeroPressure):
                for faceId in validFaceIdentifiers(bc):
                    supreFile.write('zero_pressure {0}.ebc\n'.format(faceIndicesAndFileNames[faceId][1]))
                supreFile.write('\n')

            elif is_boundary_condition_type(bc, PrescribedVelocities.PrescribedVelocities):
                faceInfoFile = fileList['faceInfo.dat']

                bctFile = fileList['bct.dat']
                bctSteadyFile = fileList['bct_steady.dat']

                if bctInfo.first:
                    bctInfo.first = False
                    emptyLine = ' ' * 50 + '\n'
                    bctFile.write(emptyLine)
                    bctSteadyFile.write(emptyLine)
                    bctInfo.period = bc.originalWaveform[-1, 0]  # Last time point
                else:
                    if abs(bc.originalWaveform[-1, 0] - bctInfo.period) > 1e-5:
                        Utils.logWarning(
                            'Periods of waveforms used for prescribed velocities are different. RCR boundary conditions may be inconsistent - the period used is {0}'.format(
                                bctInfo.period))

                waveform = bc.smoothedWaveform
                steadyWaveformValue = numpy.trapz(waveform[:, 1], x=waveform[:, 0]) / (waveform[-1, 0] - waveform[0, 0])

                bctInfo.maxNTimeSteps = max(bctInfo.maxNTimeSteps, waveform.shape[0])

                for faceId in validFaceIdentifiers(bc):
                    supreFile.write('prescribed_velocities {0}.nbc\n'.format(faceIndicesAndFileNames[faceId][1]));
                    faceInfoFile.write('PrescribedVelocities {0[0]} {0[1]}\n'.format(faceIndicesAndFileNames[faceId]))
                    bctInfo.faceIds.append(str(faceIndicesAndFileNames[faceId][0]))

                supreFile.write('\n')

                def writeBctWaveforms(waveform, steadyWaveformValue):
                    smoothedFlowWaveformFile = fileList['bctFlowWaveform.dat']
                    numpy.savetxt(smoothedFlowWaveformFile, waveform)

                    steadyFlowWaveformFile = fileList['bctFlowWaveform_steady.dat']
                    numpy.savetxt(steadyFlowWaveformFile,
                                  numpy.array([[waveform[0, 0], steadyWaveformValue],
                                   [waveform[-1, 0], steadyWaveformValue]]) )

                writeBctWaveforms(waveform, steadyWaveformValue)


                def writeBctProfile(file, wave):
                    for faceId in validFaceIdentifiers(bc):
                        flowProfileGenerator = FlowProfileGenerator(bc.getProperties()['Profile type'], solidModelData,
                                                                    meshData, faceId)

                        for pointIndex, flowVectorList in flowProfileGenerator.generateProfile(wave[:, 1]):
                            bctInfo.totalPoints += 1
                            file.write('{0[0]} {0[1]} {0[2]} {1}\n'.format(meshData.getNodeCoordinates(pointIndex),
                                                                           wave.shape[0]))
                            for timeStep, flowVector in enumerate(flowVectorList):
                                file.write('{0[0]} {0[1]} {0[2]} {1}\n'.format(flowVector, wave[timeStep, 0]))

                writeBctProfile(bctFile, waveform)
                writeBctProfile(bctSteadyFile,
                                numpy.array([[waveform[0, 0], steadyWaveformValue],
                                             [waveform[-1, 0], steadyWaveformValue]]))

            elif is_boundary_condition_type(bc, PCMRI.PCMRI):
                faceInfoFile = fileList['faceInfo.dat']

                bctFile = fileList['bct.dat']
                bctSteadyFile = fileList['bct_steady.dat']

                if bctInfo.first:
                    bctInfo.first = False
                    emptyLine = ' ' * 50 + '\n'
                    bctFile.write(emptyLine)
                    bctSteadyFile.write(emptyLine)
                    bctInfo.period = bc.pcmriData.getTimepoints()[-1]  # Last time point
                else:
                    if abs(bc.pcmriData.getTimepoints()[-1] - bctInfo.period) > 1e-5:
                        Utils.logWarning(
                            'Periods of waveforms used for prescribed velocities are different. RCR boundary conditions may be inconsistent - the period used is {0}'.format(
                                bctInfo.period))

                waveform = bc.pcmriData.getFlowWaveform()
                # steadyWaveformValue = numpy.trapz(waveform[:, 1], x=waveform[:, 0]) / (waveform[-1, 0] - waveform[0, 0])
                steadyWaveformValue = numpy.trapz(waveform[:], x=bc.pcmriData.getTimepoints()) / \
                                      (bc.pcmriData.getTimepoints()[-1] - bc.pcmriData.getTimepoints()[0])

                bctInfo.maxNTimeSteps = max(bctInfo.maxNTimeSteps, len(bc.pcmriData.getTimepoints()))

                for faceId in validFaceIdentifiers(bc):
                    supreFile.write('prescribed_velocities {0}.nbc\n'.format(faceIndicesAndFileNames[faceId][1]));
                    faceInfoFile.write('PrescribedVelocities {0[0]} {0[1]}\n'.format(faceIndicesAndFileNames[faceId])) #PrescribedVelocities or PCMRI??
                    bctInfo.faceIds.append(str(faceIndicesAndFileNames[faceId][0]))

                supreFile.write('\n')

                def writeBctWaveforms(waveform, steadyWaveformValue):
                    smoothedFlowWaveformFile = fileList['bctFlowWaveform.dat']
                    numpy.savetxt(smoothedFlowWaveformFile, waveform)

                    steadyFlowWaveformFile = fileList['bctFlowWaveform_steady.dat']
                    numpy.savetxt(steadyFlowWaveformFile,
                                    numpy.array([[bc.pcmriData.getTimepoints()[0], steadyWaveformValue],
                                     [bc.pcmriData.getTimepoints()[-1], steadyWaveformValue]]) )

                writeBctWaveforms(waveform, steadyWaveformValue)


                def writeBctProfile(file):
                    for faceId in validFaceIdentifiers(bc):
                        for index, pointIndex in enumerate(meshData.getNodeIdsForFace(faceId)):
                            bctInfo.totalPoints += 1
                            file.write('{0[0]} {0[1]} {0[2]} {1}\n'.format(meshData.getNodeCoordinates(pointIndex),
                                                                           len(bc.pcmriData.getTimepoints())))

                            for timeIndex, timeStep in enumerate(bc.pcmriData.getTimepoints()):
                                file.write('{0[0]} {0[1]} {0[2]} {1}\n'.format(bc.pcmriData.getSingleMappedPCMRIvector(index,timeIndex),
                                                                               timeStep))
                def writeBctProfileSteady(file, wave):
                    for faceId in validFaceIdentifiers(bc):
                        flowProfileGenerator = FlowProfileGenerator(0, solidModelData,
                                                                    meshData, faceId)

                        for pointIndex, flowVectorList in flowProfileGenerator.generateProfile(wave[:, 1]):
                            bctInfo.totalPoints += 1
                            file.write('{0[0]} {0[1]} {0[2]} {1}\n'.format(meshData.getNodeCoordinates(pointIndex),
                                                                           wave.shape[0]))
                            for timeStep, flowVector in enumerate(flowVectorList):
                                file.write('{0[0]} {0[1]} {0[2]} {1}\n'.format(flowVector, wave[timeStep, 0]))


                writeBctProfile(bctFile)
                writeBctProfileSteady(bctSteadyFile,
                                numpy.array([[bc.pcmriData.getTimepoints()[0], steadyWaveformValue],
                                          [bc.pcmriData.getTimepoints()[-1], steadyWaveformValue]]))


            elif is_boundary_condition_type(bc, DeformableWall.DeformableWall):
                if initialPressure is None:
                    raise RuntimeError('Deformable wall boundary condition requires initial pressure to be defined.\n'
                                       'Please add the "Initial pressure" condition to the boundary condition set.')

                # Write the ebc for deformable wall
                self._writeEbc(meshData, validFaceIdentifiers(bc),
                               fileList[os.path.join('presolver', 'deformable_wall.ebc')])

                shearConstant = 0.8333333

                supreFile.write('deformable_wall deformable_wall.ebc\n')
                supreFile.write('fix_free_edge_nodes deformable_wall.ebc\n')
                supreFile.write('deformable_create_mesh deformable_wall.ebc\n')
                supreFile.write('deformable_write_feap inputdataformatlab.dat\n')
                supreFile.write('deformable_pressure {0}\n'.format(initialPressure))
                supreFile.write('deformable_Evw {0}\n'.format(bc.getProperties()["Young's modulus"]))
                supreFile.write('deformable_nuvw {0}\n'.format(bc.getProperties()["Poisson ratio"]))
                supreFile.write('deformable_thickness {0}\n'.format(bc.getProperties()["Thickness"]))
                supreFile.write('deformable_kcons {0}\n'.format(shearConstant))

                deformableGroup = solverInpData['DEFORMABLE WALL PARAMETERS']
                deformableGroup['Deformable Wall'] = True
                deformableGroup['Density of Vessel Wall'] = bc.getProperties()["Density"]
                deformableGroup['Thickness of Vessel Wall'] = bc.getProperties()["Thickness"]
                deformableGroup['Young Mod of Vessel Wall'] = bc.getProperties()["Young's modulus"]
                deformableGroup['Poisson Ratio of Vessel Wall'] = bc.getProperties()["Poisson ratio"]
                deformableGroup['Shear Constant of Vessel Wall'] = shearConstant

                deformableGroup['Use SWB File'] = False
                deformableGroup['Use TWB File'] = False
                deformableGroup['Use EWB File'] = False
                deformableGroup['Wall External Support Term'] = bc.getProperties()["Enable tissue support term"]
                deformableGroup['Stiffness Coefficient for Tissue Support'] = \
                    bc.getProperties()["Stiffness coefficient"]
                deformableGroup['Wall Damping Term'] = bc.getProperties()["Enable damping term"]
                deformableGroup['Damping Coefficient for Tissue Support'] = bc.getProperties()["Damping coefficient"]
                deformableGroup['Wall State Filter Term'] = False
                deformableGroup['Wall State Filter Coefficient'] = 0

                useSWB = False
                numberOfWallProps = 10
                readSWBCommand = None

                # Check if external material is present
                if 'Thickness' in materialStorage.arrays:
                    useSWB = True
                    numberOfWallProps = 21
                    swbFileName = 'SWB.dat'
                    swbFile = fileList[os.path.join('presolver', swbFileName)]
                    readSWBCommand = 'read_SWB_ORTHO ' + swbFileName
                    self._writeMaterial(bc, faceIndicesInAllExteriorFaces, swbFile, materialStorage,
                                        shearConstant, meshData, solidModelData, vesselForestData)

                deformableGroup['Use SWB File'] = useSWB
                deformableGroup['Number of Wall Properties per Node'] = numberOfWallProps
                supreFile.write('number_of_wall_Props {0}\n'.format(numberOfWallProps))
                if readSWBCommand is not None:
                    supreFile.write(readSWBCommand + '\n')
                supreFile.write('deformable_solve\n\n')

        # Finalize
        if len(rcrInfo.faceIds) > 0:
            rcrGroup = solverInpData['CARDIOVASCULAR MODELING PARAMETERS: RCR']

            rcrValuesFromFileKey = 'RCR Values From File'
            numberOfRCRSurfacesKey = 'Number of RCR Surfaces'
            listOfRCRSurfacesKey = 'List of RCR Surfaces'

            if len(netlistInfo.faceIds) > 0:
                rcrValuesFromFileKey = 'experimental RCR Values From File'
                numberOfRCRSurfacesKey = 'Number of experimental RCR Surfaces'
                listOfRCRSurfacesKey = 'List of experimental RCR Surfaces'

            rcrGroup[rcrValuesFromFileKey] = True
            rcrGroup[numberOfRCRSurfacesKey] = len(rcrInfo.faceIds)
            rcrGroup[listOfRCRSurfacesKey] = ' '.join(rcrInfo.faceIds)

        if len(netlistInfo.faceIds) > 0:
            netlistGroup = solverInpData['CARDIOVASCULAR MODELING PARAMETERS: NETLIST LPNs']
            netlistGroup['Number of Netlist LPN Surfaces'] = len(netlistInfo.faceIds)
            netlistGroup['List of Netlist LPN Surfaces'] = ' '.join(netlistInfo.faceIds)

            multidomainFile = fileList['multidomain.dat']
            multidomainFile.write('#\n{0}\n#\n0\n'.format(0 if len(rcrInfo.faceIds) == 0 else 1))

        if not bctInfo.first:
            bctInfo.totalPoints /= 2  # points counted twice for steady and non-steady output

            def writeBctInfo(file, maxNTimesteps):
                file.seek(0)
                file.write('{0} {1}'.format(bctInfo.totalPoints, maxNTimesteps))

            writeBctInfo(bctFile, bctInfo.maxNTimeSteps)
            writeBctInfo(bctSteadyFile, 2)

            presribedVelocititesGroup = solverInpData['CARDIOVASCULAR MODELING PARAMETERS: PRESCRIBED VELOCITIES']
            presribedVelocititesGroup['Time Varying Boundary Conditions From File'] = True
            presribedVelocititesGroup['BCT Time Scale Factor'] = 1.0
            presribedVelocititesGroup['Number of Dirichlet Surfaces Which Output Pressure and Flow'] = \
                len(bctInfo.faceIds)
            presribedVelocititesGroup['List of Dirichlet Surfaces'] = ' '.join(bctInfo.faceIds)

    def _writeMaterial(self, bc, faceIndicesInAllExteriorFaces, swbFile, materialStorage, shearConstant, meshData,
                       solidModelData, vesselForestData):
        thicknessArray = materialStorage.arrays['Thickness'].data
        isoStiffnessArray = materialStorage.arrays["Young's modulus"].data \
            if "Young's modulus" in materialStorage.arrays else None
        anisoStiffnessArray = materialStorage.arrays["Young's modulus (anisotropic)"].data \
            if "Young's modulus (anisotropic)" in materialStorage.arrays else None

        tConst = bc.getProperties()["Thickness"]
        Econst = bc.getProperties()["Young's modulus"]
        v = bc.getProperties()["Poisson ratio"]

        faceIndexToAllExteriorFacesIndex = {y: x for x, y in enumerate(faceIndicesInAllExteriorFaces)}

        # SWB file MUST contain information for all exterior faces
        for i in xrange(solidModelData.getNumberOfFaceIdentifiers()):
            faceIdentifier = solidModelData.getFaceIdentifier(i)
            for meshFaceInfo in meshData.getMeshFaceInfoForFace(faceIdentifier):
                globalFaceId = meshFaceInfo[1]
                t = thicknessArray[globalFaceId][0]

                if numpy.isnan(t):
                    t = tConst

                if anisoStiffnessArray is not None and not numpy.isnan(anisoStiffnessArray[globalFaceId][0]):
                    stiffnessMatrix = self._computeAnisotropicStiffnessMatrix(
                        MaterialFaceInfo(vesselForestData, meshData, faceIdentifier, meshFaceInfo),
                        anisoStiffnessArray[globalFaceId])
                else:
                    if isoStiffnessArray is not None and not numpy.isnan(isoStiffnessArray[globalFaceId][0]):
                        E = isoStiffnessArray[globalFaceId][0]
                    else:
                        if faceIdentifier.faceType != FaceType.ftWall:
                            continue # Ignore flow faces which have no material set
                        else:
                            E = Econst # Treat wall faces as having isotropic material with values from BC
                    stiffnessMatrix = self._computeIsotropicStiffnessMatrix(v, E, shearConstant)

                swbFile.write(
                    '{0} {1} 0 0 0 0 0 '
                    '{2[0][0]} {2[1][0]} {2[1][1]} '
                    '{2[2][0]} {2[2][1]} {2[2][2]} '
                    '{2[3][0]} {2[3][1]} {2[3][2]} '
                    '{2[3][3]} {2[4][0]} {2[4][1]} '
                    '{2[4][2]} {2[4][3]} {2[4][4]}\n'.format(
                        faceIndexToAllExteriorFacesIndex[globalFaceId] + 1, t, stiffnessMatrix))

    def _computeIsotropicStiffnessMatrix(self, poissonRatio, youngsModulus, shearConstant):
        Kmatrix = numpy.zeros([5, 5])

        C = youngsModulus / (1 - poissonRatio * poissonRatio)

        Kmatrix[0, 0] = Kmatrix[1, 1] = C
        Kmatrix[0, 1] = Kmatrix[1, 0] = C * poissonRatio
        Kmatrix[2, 2] = C * 0.5 * (1 - poissonRatio)
        Kmatrix[3, 3] = Kmatrix[4, 4] = C * 0.5 * shearConstant * (1 - poissonRatio)

        return Kmatrix

    def _computeAnisotropicStiffnessMatrix(self, materialFaceInfo, youngsModulusAniso):
        coordinateFrame = materialFaceInfo.getVesselPathCoordinateFrame()

        x1 = numpy.array(materialFaceInfo.meshData.getNodeCoordinates(materialFaceInfo.meshFaceInfoData[2]))
        x2 = numpy.array(materialFaceInfo.meshData.getNodeCoordinates(materialFaceInfo.meshFaceInfoData[3]))
        x3 = numpy.array(materialFaceInfo.meshData.getNodeCoordinates(materialFaceInfo.meshFaceInfoData[4]))

        # Face coordinate frame
        v1 = x2 - x1
        v1 /= numpy.linalg.norm(v1)

        v2 = x3 - x1
        v3 = numpy.cross(v1, v2)
        v3 /= numpy.linalg.norm(v3)

        v2 = numpy.cross(v3, v1)

        # 'Membrane' coordinate frame
        e3 = numpy.array(materialFaceInfo.getFaceCenter()) - numpy.array(coordinateFrame[0:3])
        e3 /= numpy.linalg.norm(e3)
        e2 = numpy.array(coordinateFrame[3:6])
        e1 = numpy.cross(e2, e3)
        e1 /= numpy.linalg.norm(e1)
        e3 = numpy.cross(e1, e2)

        # Transformation matrix
        Q = numpy.array([[numpy.dot(v1, e1), numpy.dot(v1, e2), numpy.dot(v1, e3)],
                         [numpy.dot(v2, e1), numpy.dot(v2, e2), numpy.dot(v2, e3)],
                         [numpy.dot(v3, e1), numpy.dot(v3, e2), numpy.dot(v3, e3)]])

        tempC = numpy.zeros([3, 3, 3, 3])

        tempC[0, 0, 0, 0] = youngsModulusAniso[0]  # C_qqqq
        tempC[0, 0, 1, 1] = youngsModulusAniso[1]  # C_qqzz
        tempC[1, 1, 0, 0] = tempC[0, 0, 1, 1]
        tempC[1, 1, 1, 1] = youngsModulusAniso[2]  # C_zzzz

        tempC[0, 1, 0, 1] = youngsModulusAniso[3]  # 0.25 * (C_qzqz+C_qzzq+C_zqzq+C_zqqz)

        tempC[0, 1, 1, 0] = tempC[0, 1, 0, 1]
        tempC[1, 0, 1, 0] = tempC[0, 1, 0, 1]
        tempC[1, 0, 0, 1] = tempC[0, 1, 0, 1]

        tempC[2, 0, 2, 0] = youngsModulusAniso[4]  # C_rqrq

        tempC[2, 1, 2, 1] = youngsModulusAniso[5]  # C_rzrz

        # http://stackoverflow.com/questions/4962606/fast-tensor-rotation-with-numpy
        def rotateTensor(tensor, transform):
            gg = numpy.outer(transform, transform)
            gggg = numpy.outer(gg, gg).reshape(4 * transform.shape)
            axes = ((0, 2, 4, 6), (0, 1, 2, 3))
            return numpy.tensordot(gggg, tensor, axes)

        tempCrot = rotateTensor(tempC, Q.T)

        Kmatrix = numpy.zeros([5, 5])

        Kmatrix[0, 0] = tempCrot[0, 0, 0, 0]
        Kmatrix[0, 1] = tempCrot[0, 0, 1, 1]
        Kmatrix[0, 2] = 0.5 * (tempCrot[0, 0, 0, 1] + tempCrot[0, 0, 1, 0])
        Kmatrix[0, 3] = tempCrot[0, 0, 2, 0]
        Kmatrix[0, 4] = tempCrot[0, 0, 2, 1]

        Kmatrix[1, 0] = tempCrot[1, 1, 0, 0]
        Kmatrix[1, 1] = tempCrot[1, 1, 1, 1]
        Kmatrix[1, 2] = 0.5 * (tempCrot[1, 1, 0, 1] + tempCrot[1, 1, 1, 0])
        Kmatrix[1, 3] = tempCrot[1, 1, 2, 0]
        Kmatrix[1, 4] = tempCrot[1, 1, 2, 1]

        Kmatrix[2, 0] = 0.5 * (tempCrot[0, 1, 0, 0] + tempCrot[1, 0, 0, 0])
        Kmatrix[2, 1] = 0.5 * (tempCrot[0, 1, 1, 1] + tempCrot[1, 0, 1, 1])
        Kmatrix[2, 2] = 0.25 * (tempCrot[0, 1, 0, 1] + tempCrot[0, 1, 1, 0] +
                                tempCrot[1, 0, 1, 0] + tempCrot[1, 0, 0, 1])
        Kmatrix[2, 3] = 0.5 * (tempCrot[0, 1, 2, 0] + tempCrot[1, 0, 2, 0])
        Kmatrix[2, 4] = 0.5 * (tempCrot[0, 1, 2, 1] + tempCrot[1, 0, 2, 1])

        Kmatrix[3, 0] = tempCrot[2, 0, 0, 0]
        Kmatrix[3, 1] = tempCrot[2, 0, 1, 1]
        Kmatrix[3, 2] = 0.5 * (tempCrot[2, 0, 0, 1] + tempCrot[2, 0, 1, 0])
        Kmatrix[3, 3] = tempCrot[2, 0, 2, 0]
        Kmatrix[3, 4] = tempCrot[2, 0, 2, 1]

        Kmatrix[4, 0] = tempCrot[2, 1, 0, 0]
        Kmatrix[4, 1] = tempCrot[2, 1, 1, 1]
        Kmatrix[4, 2] = 0.5 * (tempCrot[2, 1, 0, 1] + tempCrot[2, 1, 1, 0])
        Kmatrix[4, 3] = tempCrot[2, 1, 2, 0]
        Kmatrix[4, 4] = tempCrot[2, 1, 2, 1]

        return Kmatrix

    def _writeSupreSurfaceIDs(self, faceIndicesAndFileNames, supreFile):
        supreFile.write('set_surface_id all_exterior_faces.ebc 1\n')
        for idAndName in sorted(faceIndicesAndFileNames.viewvalues(), key=lambda x: x[0]):
            supreFile.write('set_surface_id {0[1]}.ebc {0[0]}\n'.format(idAndName))
        supreFile.write('\n')

    def _writeSupreHeader(self, meshData, supreFile):
        supreFile.write('number_of_variables 5\n')
        supreFile.write('number_of_nodes {0}\n'.format(meshData.getNNodes()))
        supreFile.write('number_of_elements {0}\n'.format(meshData.getNElements()))
        supreFile.write('number_of_mesh_edges {0}\n'.format(meshData.getNEdges()))
        supreFile.write('number_of_mesh_faces {0}\n'.format(meshData.getNFaces()))
        supreFile.write('\n')
        supreFile.write('phasta_node_order\n')
        supreFile.write('\n')
        supreFile.write('nodes the.coordinates\n')
        supreFile.write('elements the.connectivity\n')
        supreFile.write('boundary_faces all_exterior_faces.ebc\n')
        supreFile.write('adjacency the.xadj\n')
        supreFile.write('\n')

    def _computeFaceIndicesAndFileNames(self, solidModelData, vesselPathNames):
        faceTypePrefixes = {FaceType.ftCapInflow: 'inflow_',
                            FaceType.ftCapOutflow: 'outflow_',
                            FaceType.ftWall: 'wall_'}

        faceIndicesAndFileNames = {}
        for i in xrange(solidModelData.getNumberOfFaceIdentifiers()):
            faceIdentifier = solidModelData.getFaceIdentifier(i)

            faceIndicesAndFileNames[faceIdentifier] = [-1, faceTypePrefixes[faceIdentifier.faceType] + '_'.join(
                (vesselPathNames.get(vesselPathUID, vesselPathUID).replace(' ', '_') for vesselPathUID in
                 faceIdentifier.parentSolidIndices))]

        faceTypePriority = {FaceType.ftCapInflow: 1, FaceType.ftCapOutflow: 2, FaceType.ftWall: 3}

        def compareWithPriority(l, r):
            if l[0].faceType != r[0].faceType:
                return -1 if faceTypePriority[l[0].faceType] < faceTypePriority[r[0].faceType] else 1
            return -1 if l[1] < r[1] else 1

        for i, kv in enumerate(sorted(faceIndicesAndFileNames.iteritems(),
                                      cmp=compareWithPriority)):
            faceIndicesAndFileNames[kv[0]][0] = i + 2  # 1 is reserved for all_exterior_faces

        return OrderedDict(sorted(faceIndicesAndFileNames.items(), key=lambda t: t[1][0]))

    # Compute materials and return them in form of SolutionStorage
    def computeMaterials(self, materials, vesselForestData, solidModelData, meshData):
        with Timer('Compute materials'):
            solutionStorage = SolutionStorage()

            validFaceIdentifiers = lambda bc: (x for x in bc.faceIdentifiers if
                                               solidModelData.faceIdentifierIndex(x) != -1)

            def getMaterialConstantValue(materialData):
                if materialData.nComponents == 1:
                    return m.getProperties()[materialData.name]
                else:
                    return [m.getProperties()[materialData.name][materialData.componentNames[component]] for component
                            in xrange(materialData.nComponents)]

            for m in materials:
                for materialData in m.materialDatas:
                    if materialData.name not in solutionStorage.arrays:
                        newMat = numpy.zeros((meshData.getNFaces(), materialData.nComponents))
                        newMat[:] = numpy.NAN
                        solutionStorage.arrays[materialData.name] = SolutionStorage.ArrayInfo(newMat,
                                                                                              materialData.componentNames)

                    if materialData.representation == MaterialData.RepresentationType.Table:
                        # sort by argument value, see http://stackoverflow.com/questions/2828059/sorting-arrays-in-numpy-by-column
                        tableData = materialData.tableData.data.transpose()
                        tableData = tableData[tableData[:, 0].argsort()].transpose()
                    elif materialData.representation == MaterialData.RepresentationType.Script:
                        exec compile(materialData.scriptData, 'material {0}'.format(materialData.name),
                                     'exec') in globals(), globals()
                    for faceId in validFaceIdentifiers(m):
                        constantValue = getMaterialConstantValue(materialData)
                        for info in meshData.getMeshFaceInfoForFace(faceId):
                            materialFaceInfo = MaterialFaceInfo(vesselForestData, meshData, faceId, info)

                            if materialData.representation == MaterialData.RepresentationType.Constant:
                                value = constantValue
                            elif materialData.representation == MaterialData.RepresentationType.Table:
                                if materialData.tableData.inputVariableType == MaterialData.InputVariableType.DistanceAlongPath:
                                    x = materialFaceInfo.getArcLength()
                                elif materialData.tableData.inputVariableType == MaterialData.InputVariableType.LocalRadius:
                                    x = materialFaceInfo.getLocalRadius()
                                elif materialData.tableData.inputVariableType == MaterialData.InputVariableType.x:
                                    x = materialFaceInfo.getFaceCenter()[0]
                                elif materialData.tableData.inputVariableType == MaterialData.InputVariableType.y:
                                    x = materialFaceInfo.getFaceCenter()[1]
                                elif materialData.tableData.inputVariableType == MaterialData.InputVariableType.z:
                                    x = materialFaceInfo.getFaceCenter()[2]

                                value = [numpy.interp(x, tableData[0], tableData[component])
                                         for component in xrange(1, materialData.nComponents + 1)]
                            elif materialData.representation == MaterialData.RepresentationType.Script:
                                value = computeMaterialValue(materialFaceInfo)

                            solutionStorage.arrays[materialData.name].data[info[1]] = value
                            #
        return solutionStorage
