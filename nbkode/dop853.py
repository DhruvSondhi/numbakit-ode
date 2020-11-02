"""
    nbkode.dop853
    ~~~~~~~~~~~~~

    Methods of Runge-Kutta family:
    - DOP853

    :copyright: 2020 by nbkode Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from typing import Callable

import numba
import numpy as np

from nbkode import corevs, dop853_coefficients
from nbkode.corevs import rk_step, MAX_FACTOR, SAFETY, MIN_FACTOR
from nbkode.nbcompat import clip


def step_builder(A, B, C, E5, E3, error_exponent):
    """Perform a single fixed step.

    This outer function should only contains attributes
    associated with the solver class not with the solver instance.

    Parameters
    ----------
    A : ndarray, shape (n_stages, )
        Coefficients for combining previous stages to compute the next
        stage.

    Returns
    -------
    callable

    """

    @numba.njit
    def _step(t_bound, rhs, t, y, f, h, K, rtol, atol, max_step):
        """Perform a single fixed step.

        This inner function should only contains attributes
        associated with the solver instance not with the solver class.

        Parameters
        ----------
        t_bound : float
            Boundary time - the integration won’t continue beyond it.
            It also determines the direction of the integration.
        rhs : callable
            Right-hand side of the system.
        ts : ndarray, shape (LEN_HISTORY, )
            Last ORDER + 1 times
        ys : ndarray, shape (LEN_HISTORY, n)
            Last ORDER + 1 values, for each n dimension
        fs : ndarray, shape (LEN_HISTORY, n)
            Last ORDER + 1 value of the derivative, for each n dimension
        h : ndarray, shape (1, )
            Step to use.
        K : ndarray, shape (n_stages + 1, n)
            Storage array for putting RK stages here. Stages are stored in rows.
            The last row is a linear combination of the previous rows with
            coefficients
        rtol : float
            Tolerance (relative) for termination.
        atol : float
            The allowable error of the zero value. If `func` is complex-valued,
            a larger `tol` is recommended as both the real and imaginary parts
            of `x` contribute to ``|x - x0|``.
        max_step : float
            Maximum allowed step size.

        ts, ts and fs are modified in place.
        h and K is modified in place.
        """

        t_cur = t[-1]
        y_cur = y[-1]
        f_cur = f[-1]

        min_step = 10 * (np.nextafter(t_cur, np.inf) - t_cur)
        _h = clip(h[0], min_step, max_step)
        step_rejected = False
        while True:
            if _h < min_step:
                raise RuntimeError("Required step is too small.")

            t_new = min(t_cur + _h, t_bound)
            _h = t_new - t_cur

            y_new, f_new = rk_step(rhs, t_cur, y_cur, f_cur, _h, A, B, C, K)

            # Estimate norm of scaled error
            scale = atol + np.maximum(np.abs(y_cur), np.abs(y_new)) * rtol

            err5 = np.dot(K.T, E5) / scale
            err3 = np.dot(K.T, E3) / scale
            err5_norm_2 = np.linalg.norm(err5) ** 2
            err3_norm_2 = np.linalg.norm(err3) ** 2
            if err5_norm_2 == 0 and err3_norm_2 == 0:
                error_norm = 0.0
            else:
                denom = err5_norm_2 + 0.01 * err3_norm_2
                error_norm = np.abs(_h) * err5_norm_2 / np.sqrt(denom * len(scale))

            if error_norm < 1:
                if error_norm == 0:
                    factor = MAX_FACTOR
                else:
                    factor = min(MAX_FACTOR, SAFETY * error_norm ** error_exponent)

                if step_rejected:
                    factor = min(1, factor)

                _h *= factor

                break
            else:
                _h *= max(MIN_FACTOR, SAFETY * error_norm ** error_exponent)
                step_rejected = True

        f[:-1] = f[1:]
        f[-1] = f_new

        t[:-1] = t[1:]
        t[-1] = t_new

        y[:-1] = y[1:]
        y[-1] = y_new

        h[0] = _h

    return _step


class DOP853(corevs.VariableStepRungeKutta):
    """Explicit Runge-Kutta method of order 8.

    This is a Python implementation of "DOP853" algorithm originally written
    in Fortran [1]_, [2]_. Note that this is not a literate translation, but
    the algorithmic core and coefficients are the same.
    Can be applied in the complex domain.


    References
    ----------
    .. [1] E. Hairer, S. P. Norsett G. Wanner, "Solving Ordinary Differential
           Equations I: Nonstiff Problems", Sec. II.
    .. [2] `Page with original Fortran code of DOP853
            <http://www.unige.ch/~hairer/software.html>`_.
    """

    GROUP = "Runge-Kutta"
    IMPLICIT = False

    n_stages = dop853_coefficients.N_STAGES
    order = 8
    error_estimator_order = 7
    A = np.ascontiguousarray(dop853_coefficients.A[:n_stages, :n_stages])
    B = dop853_coefficients.B
    C = np.ascontiguousarray(dop853_coefficients.C[:n_stages])
    E3 = dop853_coefficients.E3
    E5 = dop853_coefficients.E5
    D = dop853_coefficients.D

    A_EXTRA = np.ascontiguousarray(dop853_coefficients.A[n_stages + 1 :])
    C_EXTRA = np.ascontiguousarray(dop853_coefficients.C[n_stages + 1 :])

    def __init__(
        self,
        rhs: Callable,
        t0: float,
        y0: np.ndarray,
        args: tuple = (),
        *,
        max_step=np.inf,
        rtol=1e-3,
        atol=1e-6,
    ):
        super().__init__(rhs, t0, y0, args, max_step=max_step, rtol=rtol, atol=atol)

        self.K_extended = np.empty(
            (dop853_coefficients.N_STAGES_EXTENDED, y0.size), dtype=y0.dtype
        )
        self.K = self.K_extended[: self.n_stages + 1]

        self._step = step_builder(
            self.A,
            self.B,
            self.C,
            self.E5,
            self.E3,
            self.error_exponent
        )