##############################################################################
# Copyright 2016-2018 Rigetti Computing
#
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.
##############################################################################
"""
Module for working with Pauli algebras.
"""

import re
from itertools import product
import numpy as np
import copy

from typing import Callable, Dict, FrozenSet, Iterable, Iterator, List, Optional, Sequence, Tuple, Union

from pyquil.quilatom import QubitPlaceholder, FormalArgument, Expression, ExpressionDesignator

from .quil import Program
from .gates import H, RZ, RX, CNOT, X, PHASE, QUANTUM_GATES
from numbers import Number
from collections import OrderedDict
import warnings

PauliTargetDesignator = Union[int, FormalArgument]
PauliDesignator = Union['PauliTerm', 'PauliSum']

PAULI_OPS = ["X", "Y", "Z", "I"]
PAULI_PROD = {'ZZ': 'I', 'YY': 'I', 'XX': 'I', 'II': 'I',
              'XY': 'Z', 'XZ': 'Y', 'YX': 'Z', 'YZ': 'X', 'ZX': 'Y',
              'ZY': 'X', 'IX': 'X', 'IY': 'Y', 'IZ': 'Z',
              'ZI': 'Z', 'YI': 'Y', 'XI': 'X',
              'X': 'X', 'Y': 'Y', 'Z': 'Z', 'I': 'I'}
PAULI_COEFF = {'ZZ': 1.0, 'YY': 1.0, 'XX': 1.0, 'II': 1.0,
               'XY': 1.0j, 'XZ': -1.0j, 'YX': -1.0j, 'YZ': 1.0j, 'ZX': 1.0j,
               'ZY': -1.0j, 'IX': 1.0, 'IY': 1.0, 'IZ': 1.0, 'ZI': 1.0,
               'YI': 1.0, 'XI': 1.0,
               'X': 1.0, 'Y': 1.0, 'Z': 1.0, 'I': 1.0}


class UnequalLengthWarning(Warning):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


integer_types = (int, np.int64, np.int32, np.int16, np.int8)
"""Explicitly include numpy integer dtypes (for python 3)."""

HASH_PRECISION = 1e6
"""The precision used when hashing terms to check equality. The simplify() method
uses np.isclose() for coefficient comparisons to 0 which has its own default precision. We
can't use np.isclose() for hashing terms though.
"""


def _valid_qubit(index: int) -> bool:
    return ((isinstance(index, integer_types) and index >= 0)
            or isinstance(index, QubitPlaceholder)
            or isinstance(index, FormalArgument))


class PauliTerm(object):
    """A term is a product of Pauli operators operating on different qubits.
    """

    def __init__(self, op: str, index: PauliTargetDesignator, coefficient: ExpressionDesignator = 1.0):
        """ Create a new Pauli Term with a Pauli operator at a particular index and a leading
        coefficient.

        :param op: The Pauli operator as a string "X", "Y", "Z", or "I"
        :param index: The qubit index that that operator is applied to.
        :param coefficient: The coefficient multiplying the operator, e.g. 1.5 * Z_1
        """
        if op not in PAULI_OPS:
            raise ValueError(f"{op} is not a valid Pauli operator")

        self._ops: Dict[int, str] = OrderedDict()
        if op != "I":
            if not _valid_qubit(index):
                raise ValueError(f"{index} is not a valid qubit")
            self._ops[index] = op

        self.coefficient: Union[complex, Expression]

        if isinstance(coefficient, Number):
            self.coefficient = complex(coefficient)
        else:
            self.coefficient = coefficient

    def id(self, sort_ops: bool = True) -> str:
        """
        Returns an identifier string for the PauliTerm (ignoring the coefficient).

        Don't use this to compare terms. This function will not work with qubits that
        aren't sortable.

        :param sort_ops: Whether to sort operations by qubit. This is True by default for
            backwards compatibility but will change in a future version. Callers should never rely
            on comparing id's for testing equality. See ``operations_as_set`` instead.
        :return: A string representation of this term's operations.
        :rtype: string
        """
        if len(self._ops) == 0 and not sort_ops:
            # This is nefariously backwards-compatibility breaking. There's potentially
            # lots of code floating around that says is_identity = term.id() == ''
            # Please use `is_identity(term)`!
            # Therefore, we only return 'I' when sort_ops is set to False, which is the newer
            # way of calling this function and implies the user knows what they're doing.
            return 'I'

        if sort_ops and len(self._ops) > 1:
            warnings.warn("`PauliTerm.id()` will not work on PauliTerms where the qubits are not "
                          "sortable and should be avoided in favor of `operations_as_set`.",
                          FutureWarning)
            return ''.join("{}{}".format(self._ops[q], q) for q in sorted(self._ops.keys()))
        else:
            return ''.join("{}{}".format(p, q) for q, p in self._ops.items())

    def operations_as_set(self) -> FrozenSet[Tuple[int, str]]:
        """
        Return a frozenset of operations in this term.

        Use this in place of :py:func:`id` if the order of operations in the term does not
        matter.

        :return: frozenset of (qubit, op_str) representing Pauli operations
        """
        return frozenset(self._ops.items())

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, (PauliTerm, PauliSum)):
            raise TypeError("Can't compare PauliTerm with object of type {}.".format(type(other)))
        elif isinstance(other, PauliSum):
            return other == self
        else:
            return (self.operations_as_set() == other.operations_as_set()
                    and np.isclose(self.coefficient, other.coefficient))

    def __hash__(self) -> int:
        return hash((
            round(self.coefficient.real * HASH_PRECISION),
            round(self.coefficient.imag * HASH_PRECISION),
            self.operations_as_set()
        ))

    def __len__(self) -> int:
        """
        The length of the PauliTerm is the number of Pauli operators in the term. A term that
        consists of only a scalar has a length of zero.
        """
        return len(self._ops)

    def copy(self) -> 'PauliTerm':
        """
        Properly creates a new PauliTerm, with a completely new dictionary
        of operators
        """
        new_term = PauliTerm("I", 0, 1.0)  # create new object
        # manually copy all attributes over
        for key in self.__dict__.keys():
            val = self.__dict__[key]
            if isinstance(val, (dict, list, set)):  # mutable types
                new_term.__dict__[key] = copy.copy(val)
            else:  # immutable types
                new_term.__dict__[key] = val

        return new_term

    @property
    def program(self) -> Program:
        return Program([QUANTUM_GATES[gate](q) for q, gate in self])

    def get_qubits(self) -> List[int]:
        """Gets all the qubits that this PauliTerm operates on.
        """
        return list(self._ops.keys())

    def __getitem__(self, i: int) -> str:
        return self._ops.get(i, "I")

    def __iter__(self) -> Iterator[Tuple[int, str]]:
        for i in self.get_qubits():
            yield i, self[i]

    def _multiply_factor(self, factor: str, index: int) -> 'PauliTerm':
        new_term = PauliTerm("I", 0)
        new_coeff = self.coefficient
        new_ops = self._ops.copy()

        ops = self[index] + factor
        new_op = PAULI_PROD[ops]
        if new_op != "I":
            new_ops[index] = new_op
        else:
            del new_ops[index]
        new_coeff *= PAULI_COEFF[ops]

        new_term._ops = new_ops
        new_term.coefficient = new_coeff

        return new_term

    def __mul__(self, term: Union[PauliDesignator, ExpressionDesignator]) -> PauliDesignator:
        """Multiplies this Pauli Term with another PauliTerm, PauliSum, or number according to the
        Pauli algebra rules.

        :param term: (PauliTerm or PauliSum or Number) A term to multiply by.
        :returns: The product of this PauliTerm and term.
        :rtype: PauliTerm or PauliSum
        """
        if isinstance(term, Number):
            return term_with_coeff(self, self.coefficient * term)
        elif isinstance(term, PauliSum):
            return (PauliSum([self]) * term).simplify()
        else:
            new_term = PauliTerm("I", 0, 1.0)
            new_term._ops = self._ops.copy()
            new_coeff = self.coefficient * term.coefficient
            for index, op in term:
                new_term = new_term._multiply_factor(op, index)

            return term_with_coeff(new_term, new_term.coefficient * new_coeff)

    def __rmul__(self, other: ExpressionDesignator) -> 'PauliTerm':
        """Multiplies this PauliTerm with another object, probably a number.

        :param other: A number or PauliTerm to multiply by
        :returns: A new PauliTerm
        :rtype: PauliTerm
        """
        assert isinstance(other, Number)
        return self * other

    def __pow__(self, power: int) -> 'PauliTerm':
        """Raises this PauliTerm to power.

        :param int power: The power to raise this PauliTerm to.
        :return: The power-fold product of power.
        :rtype: PauliTerm
        """
        if not isinstance(power, int) or power < 0:
            raise ValueError("The power must be a non-negative integer.")

        if len(self.get_qubits()) == 0:
            # There weren't any nontrivial operators
            return term_with_coeff(self, 1)

        result = ID()
        for _ in range(power):
            result *= self
        return result

    def __add__(self, other: Union[PauliDesignator, ExpressionDesignator]) -> 'PauliSum':
        """Adds this PauliTerm with another one.

        :param other: A PauliTerm object, a PauliSum object, or a Number
        :returns: A PauliSum object representing the sum of this PauliTerm and other
        :rtype: PauliSum
        """
        if isinstance(other, Number):
            return self + PauliTerm("I", 0, other)
        elif isinstance(other, PauliSum):
            return other + self
        else:
            new_sum = PauliSum([self, other])
            return new_sum.simplify()

    def __radd__(self, other: ExpressionDesignator) -> 'PauliTerm':
        """Adds this PauliTerm with a Number.

        :param other: A Number
        :returns: A new PauliTerm
        :rtype: PauliTerm
        """
        assert isinstance(other, Number)
        return PauliTerm("I", 0, other) + self

    def __sub__(self, other: Union['PauliTerm', Number]) -> 'PauliSum':
        """Subtracts a PauliTerm from this one.

        :param other: A PauliTerm object or a Number
        :returns: A PauliSum object representing the difference of this PauliTerm and term
        :rtype: PauliSum
        """
        return self + -1. * other

    def __rsub__(self, other: Union['PauliTerm', Number]) -> 'PauliSum':
        """Subtracts this PauliTerm from a Number or PauliTerm.

        :param other: A PauliTerm object or a Number
        :returns: A PauliSum object representing the difference of this PauliTerm and term
        :rtype: PauliSum
        """
        return other + -1. * self

    def __repr__(self) -> str:
        term_strs = []
        for index in self._ops.keys():
            term_strs.append("%s%s" % (self[index], index))

        if len(term_strs) == 0:
            term_strs.append("I")
        out = "%s*%s" % (self.coefficient, '*'.join(term_strs))
        return out

    def compact_str(self) -> str:
        """A string representation of the Pauli term that is more compact than ``str(term)``

        >>> term = 2.0 * sX(1)* sZ(2)
        >>> str(term)
        >>> '2.0*X1*X2'
        >>> term.compact_str()
        >>> '2.0*X1X2'
        """
        return f'{self.coefficient}*{self.id(sort_ops=False)}'

    @classmethod
    def from_list(cls, terms_list: List[Tuple[str, int]], coefficient: float = 1.0) -> 'PauliTerm':
        """
        Allocates a Pauli Term from a list of operators and indices. This is more efficient than
        multiplying together individual terms.

        :param list terms_list: A list of tuples, e.g. [("X", 0), ("Y", 1)]
        :return: PauliTerm
        """
        if not all([isinstance(op, tuple) for op in terms_list]):
            raise TypeError("The type of terms_list should be a list of (name, index) "
                            "tuples suitable for PauliTerm().")

        pterm = PauliTerm("I", 0)
        assert all([op[0] in PAULI_OPS for op in terms_list])

        indices = [op[1] for op in terms_list]
        assert all(_valid_qubit(index) for index in indices)

        # this is because from_list doesn't call simplify in order to be more efficient.
        if len(set(indices)) != len(indices):
            raise ValueError("Elements of PauliTerm that are allocated using from_list must "
                             "be on disjoint qubits. Use PauliTerm multiplication to simplify "
                             "terms instead.")

        for op, index in terms_list:
            if op != "I":
                pterm._ops[index] = op
        if isinstance(coefficient, Number):
            pterm.coefficient = complex(coefficient)
        else:
            pterm.coefficient = coefficient
        return pterm

    @classmethod
    def from_compact_str(cls, str_pauli_term: str) -> 'PauliTerm':
        """Construct a PauliTerm from the result of str(pauli_term)
        """
        # split into str_coef, str_op at first '*'' outside parenthesis
        try:
            str_coef, str_op = re.split(r'\*(?![^(]*\))', str_pauli_term,
                                        maxsplit=1)
        except ValueError:
            raise ValueError("Could not separate the pauli string into "
                             f"coefficient and operator. {str_pauli_term} does"
                             " not match <coefficient>*<operator>")

        # parse the coefficient into either a float or complex
        str_coef = str_coef.replace(' ', '')
        try:
            coef = float(str_coef)
        except ValueError:
            try:
                coef = complex(str_coef)
            except ValueError:
                raise ValueError("Could not parse the coefficient "
                                 f"{str_coef}")

        op = sI() * coef
        if str_op == 'I':
            return op

        # parse the operator
        str_op = re.sub(r'\*', '', str_op)
        if not re.match(r'^(([XYZ])(\d+))+$', str_op):
            raise ValueError(f"Could not parse operator string {str_op}. "
                             r"It should match ^(([XYZ])(\d+))+$")

        for factor in re.finditer(r'([XYZ])(\d+)', str_op):
            op *= cls(factor.group(1), int(factor.group(2)))

        return op

    def pauli_string(self, qubits: Optional[Iterable[int]] = None) -> str:
        """
        Return a string representation of this PauliTerm without its coefficient and with
        implicit qubit indices.

        If an iterable of qubits is provided, each character in the resulting string represents
        a Pauli operator on the corresponding qubit. If qubit indices are not provided as input,
        the returned string will be all non-identity operators in the order. This doesn't make
        much sense, so please provide a list of qubits. Not providing a list of qubits is
        deprecated.

        >>> p = PauliTerm("X", 0) * PauliTerm("Y", 1, 1.j)
        >>> p.pauli_string()
        "XY"
        >>> p.pauli_string(qubits=[0])
        "X"
        >>> p.pauli_string(qubits=[0, 2])
        "XI"

        :param iterable of qubits: The iterable of qubits to represent, given as ints. If None, defaults to
            all qubits in this PauliTerm.
        :return: The string representation of this PauliTerm, sans coefficient
        """
        if qubits is None:
            warnings.warn("Please provide a list of qubits when using PauliTerm.pauli_string",
                          DeprecationWarning)
            qubits = self.get_qubits()

        return ''.join(self[q] for q in qubits)


# For convenience, a shorthand for several operators.
def ID() -> PauliTerm:
    """
    The identity operator.
    """
    return PauliTerm("I", 0, 1)


def ZERO() -> PauliTerm:
    """
    The zero operator.
    """
    return PauliTerm("I", 0, 0)


def sI(q: Optional[int] = None) -> PauliTerm:
    """
    A function that returns the identity operator, optionally on a particular qubit.

    This can be specified without a qubit.

    :param int qubit_index: The optional index of a qubit.
    :returns: A PauliTerm object
    :rtype: PauliTerm
    """
    return PauliTerm("I", q)


def sX(q: int) -> PauliTerm:
    """
    A function that returns the sigma_X operator on a particular qubit.

    :param int qubit_index: The index of the qubit
    :returns: A PauliTerm object
    :rtype: PauliTerm
    """
    return PauliTerm("X", q)


def sY(q: int) -> PauliTerm:
    """
    A function that returns the sigma_Y operator on a particular qubit.

    :param int qubit_index: The index of the qubit
    :returns: A PauliTerm object
    :rtype: PauliTerm
    """
    return PauliTerm("Y", q)


def sZ(q: int) -> PauliTerm:
    """
    A function that returns the sigma_Z operator on a particular qubit.

    :param int qubit_index: The index of the qubit
    :returns: A PauliTerm object
    :rtype: PauliTerm
    """
    return PauliTerm("Z", q)


def term_with_coeff(term: PauliTerm, coeff: ExpressionDesignator) -> PauliTerm:
    """
    Change the coefficient of a PauliTerm.

    :param PauliTerm term: A PauliTerm object
    :param Number coeff: The coefficient to set on the PauliTerm
    :returns: A new PauliTerm that duplicates term but sets coeff
    :rtype: PauliTerm
    """
    if not isinstance(coeff, Number):
        raise ValueError("coeff must be a Number")
    new_pauli = term.copy()
    # We cast to a complex number to ensure that internally the coefficients remain compatible.
    new_pauli.coefficient = complex(coeff)
    return new_pauli


class PauliSum(object):
    """A sum of one or more PauliTerms.
    """

    def __init__(self, terms: Sequence[PauliTerm]):
        """
        :param Sequence terms: A Sequence of PauliTerms.
        """
        if not (isinstance(terms, Sequence)
                and all([isinstance(term, PauliTerm) for term in terms])):
            raise ValueError("PauliSum's are currently constructed from Sequences of PauliTerms.")
        if len(terms) == 0:
            self.terms = [0.0 * ID()]
        else:
            self.terms = terms

    def __eq__(self, other: object) -> bool:
        """Equality testing to see if two PauliSum's are equivalent.

        :param PauliSum other: The PauliSum to compare this PauliSum with.
        :return: True if other is equivalent to this PauliSum, False otherwise.
        :rtype: bool
        """
        if not isinstance(other, (PauliTerm, PauliSum)):
            raise TypeError("Can't compare PauliSum with object of type {}.".format(type(other)))
        elif isinstance(other, PauliTerm):
            return self == PauliSum([other])
        elif len(self.terms) != len(other.terms):
            warnings.warn(UnequalLengthWarning("These PauliSums have a different number of terms."))
            return False

        return set(self.terms) == set(other.terms)

    def __hash__(self) -> int:
        return hash(frozenset(self.terms))

    def __repr__(self) -> str:
        return " + ".join([str(term) for term in self.terms])

    def __len__(self) -> int:
        """
        The length of the PauliSum is the number of PauliTerms in the sum.
        """
        return len(self.terms)

    def __getitem__(self, item: int) -> PauliTerm:
        """
        :param int item: The index of the term in the sum to return
        :return: The PauliTerm at the index-th position in the PauliSum
        :rtype: PauliTerm
        """
        return self.terms[item]

    def __iter__(self) -> Iterator[PauliTerm]:
        return self.terms.__iter__()

    def __mul__(self, other: Union[PauliDesignator, ExpressionDesignator]) -> 'PauliSum':
        """
        Multiplies together this PauliSum with PauliSum, PauliTerm or Number objects. The new term
        is then simplified according to the Pauli Algebra rules.

        :param other: a PauliSum, PauliTerm or Number object
        :return: A new PauliSum object given by the multiplication.
        :rtype: PauliSum
        """
        if not isinstance(other, (Number, PauliTerm, PauliSum)):
            raise ValueError("Cannot multiply PauliSum by term that is not a Number, PauliTerm, or"
                             "PauliSum")
        elif isinstance(other, PauliSum):
            other_terms = other.terms
        else:
            other_terms = [other]
        new_terms = [lterm * rterm for lterm, rterm in product(self.terms, other_terms)]
        new_sum = PauliSum(new_terms)
        return new_sum.simplify()

    def __rmul__(self, other: ExpressionDesignator) -> 'PauliSum':
        """
        Multiples together this PauliSum with PauliSum, PauliTerm or Number objects. The new term
        is then simplified according to the Pauli Algebra rules.

        :param other: a PauliSum, PauliTerm or Number object
        :return: A new PauliSum object given by the multiplication.
        :rtype: PauliSum
        """
        assert isinstance(other, Number)
        new_terms = [term.copy() for term in self.terms]
        for term in new_terms:
            term.coefficient *= other
        return PauliSum(new_terms).simplify()

    def __pow__(self, power: int) -> 'PauliSum':
        """Raises this PauliSum to power.

        :param int power: The power to raise this PauliSum to.
        :return: The power-th power of this PauliSum.
        :rtype: PauliSum
        """
        if not isinstance(power, int) or power < 0:
            raise ValueError("The power must be a non-negative integer.")
        result = PauliSum([ID()])

        if not self.get_qubits():
            # There aren't any nontrivial operators
            terms = [term_with_coeff(term, 1) for term in self.terms]
            for term in terms:
                result *= term
        else:
            for term in self.terms:
                for qubit_id in term.get_qubits():
                    result *= PauliTerm("I", qubit_id)

        for _ in range(power):
            result *= self
        return result

    def __add__(self, other: Union[PauliDesignator, ExpressionDesignator]) -> 'PauliSum':
        """
        Adds together this PauliSum with PauliSum, PauliTerm or Number objects. The new term
        is then simplified according to the Pauli Algebra rules.

        :param other: a PauliSum, PauliTerm or Number object
        :return: A new PauliSum object given by the addition.
        :rtype: PauliSum
        """
        if isinstance(other, PauliTerm):
            other = PauliSum([other])
        elif isinstance(other, Number):
            other = PauliSum([other * ID()])
        new_terms = [term.copy() for term in self.terms]
        new_terms.extend(other.terms)
        new_sum = PauliSum(new_terms)
        return new_sum.simplify()

    def __radd__(self, other: ExpressionDesignator) -> 'PauliSum':
        """
        Adds together this PauliSum with a Number object. The new term
        is then simplified according to the Pauli Algebra rules.

        :param other: A Number
        :return: A new PauliSum object given by the addition.
        :rtype: PauliSum
        """
        assert isinstance(other, Number)
        return self + other

    def __sub__(self, other: Union[PauliDesignator, ExpressionDesignator]) -> 'PauliSum':
        """
        Finds the difference of this PauliSum with PauliSum, PauliTerm or Number objects. The new
        term is then simplified according to the Pauli Algebra rules.

        :param other: a PauliSum, PauliTerm or Number object
        :return: A new PauliSum object given by the subtraction.
        :rtype: PauliSum
        """
        return self + -1. * other

    def __rsub__(self, other: Union[PauliDesignator, ExpressionDesignator]) -> 'PauliSum':
        """
        Finds the different of this PauliSum with PauliSum, PauliTerm or Number objects. The new
        term is then simplified according to the Pauli Algebra rules.

        :param other: a PauliSum, PauliTerm or Number object
        :return: A new PauliSum object given by the subtraction.
        :rtype: PauliSum
        """
        return other + -1. * self

    def get_qubits(self) -> List[int]:
        """
        The support of all the operators in the PauliSum object.

        :returns: A list of all the qubits in the sum of terms.
        :rtype: list
        """
        return list(set().union(*[term.get_qubits() for term in self.terms]))

    def simplify(self) -> 'PauliSum':
        """
        Simplifies the sum of Pauli operators according to Pauli algebra rules.
        """
        return simplify_pauli_sum(self)

    def get_programs(self) -> Tuple[List[Program], np.ndarray]:
        """
        Get a Pyquil Program corresponding to each term in the PauliSum and a coefficient
        for each program

        :return: (programs, coefficients)
        """
        programs = [term.program for term in self.terms]
        coefficients = np.array([term.coefficient for term in self.terms])
        return programs, coefficients

    def compact_str(self) -> str:
        """A string representation of the PauliSum that is more compact than ``str(pauli_sum)``

        >>> pauli_sum = 2.0 * sX(1)* sZ(2) + 1.5 * sY(2)
        >>> str(pauli_sum)
        >>> '2.0*X1*X2 + 1.5*Y2'
        >>> pauli_sum.compact_str()
        >>> '2.0*X1X2+1.5*Y2'
        """
        return "+".join([term.compact_str() for term in self.terms])

    @classmethod
    def from_compact_str(cls, str_pauli_sum: str) -> 'PauliSum':
        """Construct a PauliSum from the result of str(pauli_sum)
        """
        # split str_pauli_sum only at "+" outside of parenthesis to allow
        # e.g. "0.5*X0 + (0.5+0j)*Z2"
        str_terms = re.split(r'\+(?![^(]*\))', str_pauli_sum)
        str_terms = [s.strip() for s in str_terms]
        terms = [PauliTerm.from_compact_str(term) for term in str_terms]
        return cls(terms).simplify()


def simplify_pauli_sum(pauli_sum: PauliSum) -> PauliSum:
    """Simplify the sum of Pauli operators according to Pauli algebra rules."""

    # You might want to use a defaultdict(list) here, but don't because
    # we want to do our best to preserve the order of terms.
    like_terms = OrderedDict()
    for term in pauli_sum.terms:
        key = term.operations_as_set()
        if key in like_terms:
            like_terms[key].append(term)
        else:
            like_terms[key] = [term]

    terms = []
    for term_list in like_terms.values():
        first_term = term_list[0]
        if len(term_list) == 1 and not np.isclose(first_term.coefficient, 0.0):
            terms.append(first_term)
        else:
            coeff = sum(t.coefficient for t in term_list)
            for t in term_list:
                if list(t._ops.items()) != list(first_term._ops.items()):
                    warnings.warn("The term {} will be combined with {}, but they have different "
                                  "orders of operations. This doesn't matter for QVM or "
                                  "wavefunction simulation but may be important when "
                                  "running on an actual device."
                                  .format(t.id(sort_ops=False), first_term.id(sort_ops=False)))

            if not np.isclose(coeff, 0.0):
                terms.append(term_with_coeff(term_list[0], coeff))
    return PauliSum(terms)


def check_commutation(pauli_list: Sequence[PauliTerm], pauli_two: PauliTerm) -> bool:
    """
    Check if commuting a PauliTerm commutes with a list of other terms by natural calculation.
    Uses the result in Section 3 of arXiv:1405.5749v2, modified slightly here to check for the
    number of anti-coincidences (which must always be even for commuting PauliTerms)
    instead of the no. of coincidences, as in the paper.

    :param list pauli_list: A list of PauliTerm objects
    :param PauliTerm pauli_two_term: A PauliTerm object
    :returns: True if pauli_two object commutes with pauli_list, False otherwise
    :rtype: bool
    """

    def coincident_parity(p1: PauliTerm, p2: PauliTerm) -> bool:
        non_similar = 0
        p1_indices = set(p1._ops.keys())
        p2_indices = set(p2._ops.keys())
        for idx in p1_indices.intersection(p2_indices):
            if p1[idx] != p2[idx]:
                non_similar += 1
        return non_similar % 2 == 0

    for term in pauli_list:
        if not coincident_parity(term, pauli_two):
            return False
    return True


def commuting_sets(pauli_terms: PauliSum) -> List[List[PauliTerm]]:
    """Gather the Pauli terms of pauli_terms variable into commuting sets

    Uses algorithm defined in (Raeisi, Wiebe, Sanders, arXiv:1108.4318, 2011)
    to find commuting sets. Except uses commutation check from arXiv:1405.5749v2

    :param PauliSum pauli_terms: A PauliSum object
    :returns: List of lists where each list contains a commuting set
    :rtype: list
    """

    m_terms = len(pauli_terms.terms)
    m_s = 1
    groups = []
    groups.append([pauli_terms.terms[0]])
    for j in range(1, m_terms):
        isAssigned_bool = False
        for p in range(m_s):  # check if it commutes with each group
            if isAssigned_bool is False:

                if check_commutation(groups[p], pauli_terms.terms[j]):
                    isAssigned_bool = True
                    groups[p].append(pauli_terms.terms[j])
        if isAssigned_bool is False:
            m_s += 1
            groups.append([pauli_terms.terms[j]])
    return groups


def is_identity(term: PauliDesignator) -> bool:
    """
    Tests to see if a PauliTerm or PauliSum is a scalar multiple of identity

    :param term: Either a PauliTerm or PauliSum
    :returns: True if the PauliTerm or PauliSum is a scalar multiple of identity, False otherwise
    :rtype: bool
    """
    if isinstance(term, PauliTerm):
        return (len(term) == 0) and (not np.isclose(term.coefficient, 0))
    elif isinstance(term, PauliSum):
        return (len(term.terms) == 1) and (len(term.terms[0]) == 0) and \
               (not np.isclose(term.terms[0].coefficient, 0))
    else:
        raise TypeError("is_identity only checks PauliTerms and PauliSum objects!")


def exponentiate(term: PauliTerm) -> Program:
    """
    Creates a pyQuil program that simulates the unitary evolution exp(-1j * term)

    :param term: A pauli term to exponentiate
    :returns: A Program object
    :rtype: Program
    """
    return exponential_map(term)(1.0)


def exponential_map(term: PauliTerm) -> Callable[[float], Program]:
    """
    Returns a function f(alpha) that constructs the Program corresponding to exp(-1j*alpha*term).

    :param term: A pauli term to exponentiate
    :returns: A function that takes an angle parameter and returns a program.
    :rtype: Function
    """
    if not np.isclose(np.imag(term.coefficient), 0.0):
        raise TypeError("PauliTerm coefficient must be real")

    coeff = term.coefficient.real
    term.coefficient = term.coefficient.real

    def exp_wrap(param: float) -> Program:
        prog = Program()
        if is_identity(term):
            prog.inst(X(0))
            prog.inst(PHASE(-param * coeff, 0))
            prog.inst(X(0))
            prog.inst(PHASE(-param * coeff, 0))
        elif is_zero(term):
            pass
        else:
            prog += _exponentiate_general_case(term, param)
        return prog

    return exp_wrap


def exponentiate_commuting_pauli_sum(pauli_sum: PauliSum) -> Callable[[float], Program]:
    """
    Returns a function that maps all substituent PauliTerms and sums them into a program. NOTE: Use
    this function with care. Substituent PauliTerms should commute.

    :param PauliSum pauli_sum: PauliSum to exponentiate.
    :returns: A function that parametrizes the exponential.
    :rtype: function
    """
    if not isinstance(pauli_sum, PauliSum):
        raise TypeError("Argument 'pauli_sum' must be a PauliSum.")

    fns = [exponential_map(term) for term in pauli_sum]

    def combined_exp_wrap(param: float) -> Program:
        return Program([f(param) for f in fns])

    return combined_exp_wrap


def _exponentiate_general_case(pauli_term: PauliTerm, param: float) -> Program:
    """
    Returns a Quil (Program()) object corresponding to the exponential of
    the pauli_term object, i.e. exp[-1.0j * param * pauli_term]

    :param PauliTerm pauli_term: A PauliTerm to exponentiate
    :param float param: scalar, non-complex, value
    :returns: A Quil program object
    :rtype: Program
    """

    def reverse_hack(p: Program) -> Program:
        # A hack to produce a *temporary* program which reverses p.
        revp = Program()
        revp.inst(list(reversed(p.instructions)))
        return revp

    quil_prog = Program()
    change_to_z_basis = Program()
    change_to_original_basis = Program()
    cnot_seq = Program()
    prev_index = None
    highest_target_index = None

    for index, op in pauli_term:
        if 'X' == op:
            change_to_z_basis.inst(H(index))
            change_to_original_basis.inst(H(index))

        elif 'Y' == op:
            change_to_z_basis.inst(RX(np.pi / 2.0, index))
            change_to_original_basis.inst(RX(-np.pi / 2.0, index))

        elif 'I' == op:
            continue

        if prev_index is not None:
            cnot_seq.inst(CNOT(prev_index, index))

        prev_index = index
        highest_target_index = index

    # building rotation circuit
    quil_prog += change_to_z_basis
    quil_prog += cnot_seq
    quil_prog.inst(RZ(2.0 * pauli_term.coefficient * param, highest_target_index))
    quil_prog += reverse_hack(cnot_seq)
    quil_prog += change_to_original_basis

    return quil_prog


def suzuki_trotter(trotter_order: int, trotter_steps: int) -> List[Tuple[float, int]]:
    """
    Generate trotterization coefficients for a given number of Trotter steps.

    U = exp(A + B) is approximated as exp(w1*o1)exp(w2*o2)... This method returns
    a list [(w1, o1), (w2, o2), ... , (wm, om)] of tuples where o=0 corresponds
    to the A operator, o=1 corresponds to the B operator, and w is the
    coefficient in the exponential. For example, a second order Suzuki-Trotter
    approximation to exp(A + B) results in the following
    [(0.5/trotter_steps, 0), (1/trotter_steps, 1),
    (0.5/trotter_steps, 0)] * trotter_steps.

    :param int trotter_order: order of Suzuki-Trotter approximation
    :param int trotter_steps: number of steps in the approximation
    :returns: List of tuples corresponding to the coefficient and operator
              type: o=0 is A and o=1 is B.
    :rtype: list
    """
    p1 = p2 = p4 = p5 = 1.0 / (4 - (4 ** (1. / 3)))
    p3 = 1 - 4 * p1
    trotter_dict = {1: [(1, 0), (1, 1)],
                    2: [(0.5, 0), (1, 1), (0.5, 0)],
                    3: [(7.0 / 24, 0), (2.0 / 3.0, 1), (3.0 / 4.0, 0), (-2.0 / 3.0, 1),
                        (-1.0 / 24, 0), (1.0, 1)],
                    4: [(p5 / 2, 0), (p5, 1), (p5 / 2, 0),
                        (p4 / 2, 0), (p4, 1), (p4 / 2, 0),
                        (p3 / 2, 0), (p3, 1), (p3 / 2, 0),
                        (p2 / 2, 0), (p2, 1), (p2 / 2, 0),
                        (p1 / 2, 0), (p1, 1), (p1 / 2, 0)]}

    order_slices = [(x0 / trotter_steps, x1) for x0, x1 in trotter_dict[trotter_order]]
    order_slices = order_slices * trotter_steps
    return order_slices


def is_zero(pauli_object: PauliDesignator) -> bool:
    """
    Tests to see if a PauliTerm or PauliSum is zero.

    :param pauli_object: Either a PauliTerm or PauliSum
    :returns: True if PauliTerm is zero, False otherwise
    :rtype: bool
    """
    if isinstance(pauli_object, PauliTerm):
        return np.isclose(pauli_object.coefficient, 0)
    elif isinstance(pauli_object, PauliSum):
        return len(pauli_object.terms) == 1 and np.isclose(pauli_object.terms[0].coefficient, 0)
    else:
        raise TypeError("is_zero only checks PauliTerms and PauliSum objects!")


def trotterize(first_pauli_term: PauliTerm, second_pauli_term: PauliTerm, trotter_order: int = 1,
               trotter_steps: int = 1) -> Program:
    """
    Create a Quil program that approximates exp( (A + B)t) where A and B are
    PauliTerm operators.

    :param PauliTerm first_pauli_term: PauliTerm denoted `A`
    :param PauliTerm second_pauli_term: PauliTerm denoted `B`
    :param int trotter_order: Optional argument indicating the Suzuki-Trotter
                          approximation order--only accepts orders 1, 2, 3, 4.
    :param int trotter_steps: Optional argument indicating the number of products
                          to decompose the exponential into.

    :return: Quil program
    :rtype: Program
    """

    if not (1 <= trotter_order < 5):
        raise ValueError("trotterize only accepts trotter_order in {1, 2, 3, 4}.")

    commutator = (first_pauli_term * second_pauli_term) + \
                 (-1 * second_pauli_term * first_pauli_term)

    prog = Program()
    if is_zero(commutator):
        param_exp_prog_one = exponential_map(first_pauli_term)
        exp_prog = param_exp_prog_one(1)
        prog += exp_prog
        param_exp_prog_two = exponential_map(second_pauli_term)
        exp_prog = param_exp_prog_two(1)
        prog += exp_prog
        return prog

    order_slices = suzuki_trotter(trotter_order, trotter_steps)
    for coeff, operator in order_slices:
        if operator == 0:
            param_prog = exponential_map(coeff * first_pauli_term)
            exp_prog = param_prog(1)
            prog += exp_prog
        else:
            param_prog = exponential_map(coeff * second_pauli_term)
            exp_prog = param_prog(1)
            prog += exp_prog
    return prog
