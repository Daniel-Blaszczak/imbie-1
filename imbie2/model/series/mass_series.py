from .data_series import DataSeries
import numpy as np

from imbie2.util.functions import ts2m, match
from imbie2.const.basins import BasinGroup


class MassChangeDataSeries(DataSeries):

    @property
    def min_mass(self):
        ok = np.isfinite(self.dM)
        return np.min(self.dM[ok])

    @property
    def max_mass(self):
        ok = np.isfinite(self.dM)
        return np.max(self.dM[ok])

    def __init__(self, user, user_group, data_group, basin_group, basin_id,
                 basin_area, time, area, mass, errs, computed=False, merged=False):
        super().__init__(
            user, user_group, data_group, basin_group, basin_id, basin_area,
            computed, merged
        )
        self.t, self.dM = ts2m(time, mass)
        _, self.a = ts2m(time, area)
        _, self.dM_err = ts2m(time, errs)

    def _get_min_time(self):
        return np.min(self.t)

    def _get_max_time(self):
        return np.max(self.t)

    def _set_min_time(self, min_t):
        ok = self.t >= min_t

        self.t = self.t[ok]
        self.dM = self.dM[ok]
        self.a = self.a[ok]
        self.dM_err = self.dM_err[ok]

    def _set_max_time(self, max_t):
        ok = self.t <= max_t

        self.t = self.t[ok]
        self.dM = self.dM[ok]
        self.a = self.a[ok]
        self.dM_err = self.dM_err[ok]

    @classmethod
    def accumulate_mass(cls, rate_data):
        t = (rate_data.t0 + rate_data.t1) / 2.
        dM = np.cumsum(rate_data.dMdt) # / 12?

        return cls(
            rate_data.user, rate_data.user_group, rate_data.data_group, rate_data.basin_group,
            rate_data.basin_id, rate_data.basin_area, t, rate_data.a, dM, rate_data.dMdt_err,
            computed=True
        )

    def __len__(self):
        return len(self.t)

    @classmethod
    def merge(cls, a, b):
        ia, ib = match(a.t, b.t)
        if len(a) != len(b):
            return None
        if len(ia) != len(a) or len(ib) != len(b):
            return None
        if len(ia) == 0:
            return None

        t = a.t[ia]
        m = (a.dM[ia] + b.dM[ib]) / 2.
        e = np.sqrt((np.square(a.dM_err[ia]) +
                     np.square(b.dM_err[ib])) / 2.)
        ar = (a.a[ia] + b.a[ib]) / 2.

        comp = a.computed or b.computed

        return cls(
            a.user, a.user_group, a.data_group, BasinGroup.sheets,
            a.basin_id, a.basin_area, t, ar, m, e, comp, merged=True
        )
