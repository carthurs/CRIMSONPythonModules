from PythonQt.CRIMSON import FaceType
from collections import OrderedDict
from CRIMSONSolver.SolverParameters.SolverParameters3D import CouplingType, SolverType
from PythonQt.CRIMSON import Utils

__author__ = 'rk13'


class SolverInpData():
    """
        if numberOfScalars is 0, no scalar related solver.inp fields will be emitted
    """
    def __init__(self, solverParametersData, faceIndicesAndFileNames, numberOfScalars):
        self.data = OrderedDict()

        getFaceIds = lambda cond: [str(faceIdAndName[0]) for faceIdentifier, faceIdAndName in
                                   faceIndicesAndFileNames.iteritems() if
                                   cond(faceIdentifier.faceType)]
        coupledSurfaceIds = getFaceIds(lambda faceType: faceType == FaceType.ftCapOutflow)
        outputSurfaceIds = getFaceIds(lambda faceType: faceType != FaceType.ftWall)
        wallShearStressSurfaceIds = getFaceIds(lambda faceType: faceType == FaceType.ftWall)

        props = solverParametersData.getProperties()

        #############################################################################
        #NOTE: this class overrides __getitem__ (see below)
        # Each OrderedDict section like this is a section like
        """
        # <Control Group>
        # {
            <Some Property> : <Some Value>
            ...
        # }

        """

        # For example:
        """
        # SOLUTION CONTROL
        # {
            Equation of State : Incompressible
            Number of Timesteps : 200
            Time Step Size : 0.01
        # }
        """
        solutionControlGroup = self['SOLUTION CONTROL']
        solutionControlGroup['Equation of State'] = 'Incompressible'
        solutionControlGroup['Number of Timesteps'] = props['Number of time steps']
        solutionControlGroup['Time Step Size'] = props['Time step size']

        #############################################################################
        outputControlGroup = self['OUTPUT CONTROL']
        outputControlGroup['Print Error Indicators'] = True
        outputControlGroup['Number of Timesteps between Restarts'] = \
            props['Number of time steps between restarts']
        outputControlGroup['Print ybar'] = props['Output error indicator']

        if props['Output wall shear stress']:
            outputControlGroup['Number of Force Surfaces'] = len(wallShearStressSurfaceIds)
            outputControlGroup["Surface ID's for Force Calculation"] = ' '.join(wallShearStressSurfaceIds)

        #############################################################################
        materialPropertiesGroup = self['MATERIAL PROPERTIES']
        materialPropertiesGroup['Viscosity'] = props['Viscosity']
        materialPropertiesGroup['Density'] = props['Density']

        #############################################################################
        cardiovascularModelingGroup = self['CARDIOVASCULAR MODELING PARAMETERS']
        cardiovascularModelingGroup['Global Node Numbering'] = True

        # NOTE: Fluid influx coefficient
        cardiovascularModelingGroup['Influx Coefficient'] = props['Influx coefficient']

        cardiovascularModelingGroup['Residual Control'] = props['Residual control']
        cardiovascularModelingGroup['Residual Criteria'] = props['Residual criteria']
        cardiovascularModelingGroup['Minimum Required Iterations'] = props['Minimum required iterations']

        cardiovascularModelingGroup['Number of Coupled Surfaces'] = len(coupledSurfaceIds)

        cardiovascularModelingGroup['Pressure Coupling'] = CouplingType.enumNames[props['Pressure coupling']]

        cardiovascularModelingGroup['Number of Surfaces which Output Pressure and Flow'] = len(outputSurfaceIds)
        cardiovascularModelingGroup['List of Output Surfaces'] = ' '.join(outputSurfaceIds)

        if(numberOfScalars > 0):
            cardiovascularModelingGroup['Scalar Influx Coefficient'] = props['Scalar Influx Coefficient']
            
            capturingValue = props['Scalar Discontinuity Capturing']
            enableDiscontinuityCapturing = False

            # Just out of extra caution, because a string is always truey, there should be no way for this to happen though
            if(isinstance(capturingValue, str)):
                print('An unexpected error occurred: Scalar discontinuity capturing was a string. This value should have been upgraded to a bool.')
                if(capturingValue == u'1 0'):
                    enableDiscontinuityCapturing = True
                else:
                    enableDiscontinuityCapturing = False
            
            else:
                if(capturingValue):
                    enableDiscontinuityCapturing = True
                else:
                    enableDiscontinuityCapturing = False


            # We can omit this line completely if discontinuity capturing isn't enabled
            if(enableDiscontinuityCapturing):
                cardiovascularModelingGroup['Scalar Discontinuity Capturing'] = '1 0'
            
            cardiovascularModelingGroup['Scalar Start Time'] = props['Start scalar simulation at timestep']
            cardiovascularModelingGroup['End Solve Flow'] = props["End Flow Simulation Early Enable"]
            cardiovascularModelingGroup['End Flow Time'] = props['End Flow Simulation at Timestep']


        try:
            cardiovascularModelingGroup['Simulate in Purely Zero Dimensions'] = props[
                'Simulate in Purely Zero Dimensions']
        except KeyError:
            # Catch case where old scene does not contain Simulate in Purely Zero Dimensions; just set False
            # until the user explicitly changes this.
            Utils.logWarning('This is an old scene; Simulate\n'
                             'in Purely Zero Dimensions was missing; setting it to '
                             'False. Delete and recreate your Solver\nParameters '
                             'in CRIMSON to avoid this warning.')
            cardiovascularModelingGroup['Simulate in Purely Zero Dimensions'] = False

        #############################################################################
        # NOTE: This is NOT for scalar iterations. This is for fluid iterations.
        linearSolverGroup = self['LINEAR SOLVER']
        nSteps = props['Step construction']
        linearSolverGroup['Step Construction'] = '{0} # this is the standard {1} iteration'.format('0 1 ' * nSteps,
                                                                                                   nSteps)
        linearSolverGroup['Solver Type'] = SolverType.enumNames[props['Solver type']]

        if(numberOfScalars > 0):
            linearSolverGroup['Solve Scalars'] = numberOfScalars

        #############################################################################
        discretizationControlGroup = self['DISCRETIZATION CONTROL']
        discretizationControlGroup['Basis Function Order'] = 1
        discretizationControlGroup['Quadrature Rule on Interior'] = 2
        discretizationControlGroup['Quadrature Rule on Boundary'] = 2
        discretizationControlGroup['Include Viscous Correction in Stabilization'] = True
        discretizationControlGroup['Lumped Mass Fraction on Left-hand-side'] = 0.0
        discretizationControlGroup['Lumped Mass Fraction on Right-hand-side'] = 0.0
        discretizationControlGroup['Time Integration Rule'] = 'Second Order'
        discretizationControlGroup['Time Integration Rho Infinity'] = 0.0
        discretizationControlGroup['Flow Advection Form'] = 'Convective'

    def __getitem__(self, item):
        return self.data.setdefault(item, OrderedDict())