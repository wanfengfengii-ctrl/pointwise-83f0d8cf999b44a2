from collections.abc import Sequence
from fractions import Fraction


def solve_rational_system(
    coefficients: Sequence[Sequence[Fraction]], constants: Sequence[Fraction]
) -> list[Fraction]:
    size = len(constants)
    if len(coefficients) != size or any(len(row) != size for row in coefficients):
        raise ValueError('expected a square system with one constant per row')

    matrix = [
        [Fraction(value) for value in row] + [Fraction(constant)]
        for row, constant in zip(coefficients, constants)
    ]
    for column in range(size):
        pivot = next((row for row in range(column, size) if matrix[row][column]), None)
        if pivot is None:
            raise ValueError('singular rational system')
        matrix[column], matrix[pivot] = matrix[pivot], matrix[column]
        pivot_row = matrix[column]
        for index in range(column + 1, size):
            row = matrix[index]
            if row[column]:
                factor = row[column] / pivot_row[column]
                for entry in range(column, size + 1):
                    row[entry] -= factor * pivot_row[entry]

    solution = [Fraction(0)] * size
    for index in range(size - 1, -1, -1):
        row = matrix[index]
        known = sum(row[column] * solution[column] for column in range(index + 1, size))
        solution[index] = (row[-1] - known) / row[index]
    return solution
