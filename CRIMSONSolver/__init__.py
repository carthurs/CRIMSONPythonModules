import BoundaryConditions
import Materials
import BoundaryConditionSets
import SolverParameters
import SolverStudies
import SolverSetupManagers
import ScalarProblem

__all__ = ['BoundaryConditions', 'Materials', 'BoundaryConditionSets', 'SolverParameters', 'SolverStudies',
           'SolverSetupManagers', 'ScalarProblem']

import inspect


def getHumanReadableName(objClass):
    return objClass.humanReadableName if hasattr(objClass, 'humanReadableName') else objClass.__name__

def getSolverSetupManager():
    solverSetupManagerClass = SolverSetupManagers.CRIMSONSolverSolverSetupManager.CRIMSONSolverSolverSetupManager
    return (getHumanReadableName(solverSetupManagerClass), solverSetupManagerClass)

def reloadAll():
    for module_name in __all__:
        # [AJM] I am not sure if this line would be compatible with Python 3, so keep that in mind if we ever upgraded to Python3.
        exec ('{0} = reload({0})'.format(module_name))
