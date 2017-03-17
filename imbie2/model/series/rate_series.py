from .data_series import DataSeries
import numpy as np
import math

from imbie2.util.functions import match
from imbie2.util.combine import weighted_combine as ts_combine
from imbie2.const.basins import BasinGroup, Basin
import imbie2.model as model

from typing import Optional


class MassRateDataSeries(DataSeries):

    @property
    def min_rate(self) -> None:
        ok = np.isfinite(self.dmdt)
        return np.min(self.dmdt[ok])

    @property
    def max_rate(self) -> None:
        ok = np.isfinite(self.dmdt)
        return np.max(self.dmdt[ok])

    def __init__(self, user: Optional[str], user_group: Optional[str], data_group: Optional[str],
                 basin_group: BasinGroup, basin_id: Basin, basin_area: float, t_start: np.ndarray, t_end: np.ndarray,
                 area: np.ndarray, rate: np.ndarray, errs: np.ndarray, computed: bool=False, merged: bool=False,
                 aggregated: bool=False):
        super().__init__(
            user, user_group, data_group, basin_group, basin_id, basin_area,
            computed, merged, aggregated
        )
        self.t0 = t_start
        self.t1 = t_end
        self.dmdt = rate
        self.errs = errs
        self.a = area

    def _set_min_time(self, min_t: float) -> None:
        ok = np.ones(self.t0.shape, dtype=bool)

        for i, t0 in enumerate(self.t0):
            if t0 < min_t:
                self.t0[i] = min_t
            if self.t1[i] < min_t:
                ok[i] = False

        self.t0 = self.t0[ok]
        self.t1 = self.t1[ok]
        self.dmdt = self.dmdt[ok]
        self.errs = self.errs[ok]
        self.a = self.a[ok]

    def _set_max_time(self, max_t: float) -> None:
        ok = np.ones(self.t0.shape, dtype=bool)

        for i, t1 in enumerate(self.t1):
            if t1 > max_t:
                self.t1[i] = max_t
            if self.t0[i] > max_t:
                ok[i] = False

        self.t0 = self.t0[ok]
        self.t1 = self.t1[ok]
        self.dmdt = self.dmdt[ok]
        self.errs = self.errs[ok]
        self.a = self.a[ok]

    def _get_min_time(self) -> float:
        return min(np.min(self.t1), np.min(self.t0))

    def _get_max_time(self) -> float:
        return max(np.max(self.t1), np.max(self.t0))

    @property
    def t(self) -> np.ndarray:
        return (self.t0 + self.t1) / 2

    @classmethod
    def derive_rates(cls, mass_data: "model.series.MassChangeDataSeries") -> "MassRateDataSeries":
        t0 = mass_data.t[:-1]
        t1 = mass_data.t[1:]
        dmdt = np.diff(mass_data.mass)
        if mass_data.a is not None:
            area = (mass_data.a[:-1] + mass_data.a[1:]) / 2.
        else:
            area = None

        return cls(
            mass_data.user, mass_data.user_group, mass_data.data_group, mass_data.basin_group,
            mass_data.basin_id, mass_data.basin_area, t0, t1, area, dmdt, mass_data.errs,
            computed=True, aggregated=mass_data.aggregated
        )

    def __len__(self) -> int:
        return len(self.t0)

    @property
    def sigma(self) -> float:
        return math.sqrt(
            np.nanmean(np.square(self.errs))
        ) # / math.sqrt(len(self))

    @property
    def mean(self) -> float:
        return np.nanmean(self.dmdt)

    @classmethod
    def merge(cls, a: "MassRateDataSeries", b: "MassRateDataSeries") -> "MassRateDataSeries":
        ia, ib = match(a.t0, b.t0)

        if len(a) != len(b):
            return None
        if len(ia) != len(a) or len(ib) != len(b):
            return None

        t0 = a.t0[ia]
        t1 = a.t1[ia]
        m = (a.dmdt[ia] + b.dmdt[ib]) / 2.
        e = np.sqrt((np.square(a.errs[ia]) +
                     np.square(b.errs[ib])) / 2.)
        ar = (a.a[ia] + b.a[ib]) / 2.

        comp = a.computed or b.computed
        aggr = a.aggregated or b.aggregated

        return cls(
            a.user, a.user_group, a.data_group, BasinGroup.sheets,
            a.basin_id, a.basin_area, t0, t1, ar, m, e, comp, merged=True, aggregated=aggr
        )

    def chunk_rates(self) -> "WorkingMassRateDataSeries":
        ok = self.t0 == self.t1

        time_chunks = [self.t0[ok]]
        dmdt_chunks = [self.dmdt[ok]]
        errs_chunks = [self.errs[ok]]

        for i in range(len(self)):
            if ok[i]: continue

            time_chunks.append(
                np.asarray([self.t0[i], self.t1[i]])
            )
            dmdt_chunks.append(
                np.asarray([self.dmdt[i], self.dmdt[i]])
            )
            errs_chunks.append(
                np.asarray([self.errs[i], self.errs[i]])
            )

        t, dmdt = ts_combine(time_chunks, dmdt_chunks)
        _, errs = ts_combine(time_chunks, errs_chunks, error=True)

        return WorkingMassRateDataSeries(
            self.user, self.user_group, self.data_group, self.basin_group, self.basin_id, self.basin_area,
            t, self.a, dmdt, errs, aggregated=self.aggregated
        )

    def integrate(self, offset: float=None) -> "model.series.MassChangeDataSeries":
        return model.series.MassChangeDataSeries.accumulate_mass(self, offset=offset)


class WorkingMassRateDataSeries(DataSeries):
    def __init__(self, user: Optional[str], user_group: Optional[str], data_group: Optional[str],
                 basin_group: BasinGroup, basin_id: Basin, basin_area: float, time: np.ndarray, area: np.ndarray,
                 dmdt: np.ndarray, errs: np.ndarray, computed: bool=False, merged: bool=False, aggregated: bool=False):
        super().__init__(
            user, user_group, data_group, basin_group, basin_id, basin_area,
            computed, merged, aggregated
        )
        self.t = time
        self.a = area
        self.dmdt = dmdt
        self.errs = errs

    @property
    def min_rate(self) -> float:
        ok = np.isfinite(self.dmdt)
        return np.min(self.dmdt[ok])

    @property
    def max_rate(self) -> float:
        ok = np.isfinite(self.dmdt)
        return np.max(self.dmdt[ok])

    @property
    def sigma(self) -> float:
        return math.sqrt(
            np.nanmean(np.square(self.errs))
        )  # / math.sqrt(len(self))

    @property
    def mean(self) -> float:
        return np.nanmean(self.dmdt)

    def _get_min_time(self) -> float:
        return np.min(self.t)

    def _get_max_time(self) -> float:
        return np.max(self.t)

    def _set_min_time(self, min_t: float) -> None:
        ok = self.t >= min_t

        self.t = self.t[ok]
        self.dmdt = self.dmdt[ok]
        self.a = self.a[ok]
        self.errs = self.errs[ok]

    def _set_max_time(self, max_t: float) -> None:
        ok = self.t <= max_t

        self.t = self.t[ok]
        self.dmdt = self.dmdt[ok]
        self.a = self.a[ok]
        self.errs = self.errs[ok]

    def integrate(self, offset: float=None) -> "model.series.MassChangeDataSeries":
        return model.series.MassChangeDataSeries.accumulate_mass(self, offset=offset)

    @classmethod
    def merge(cls, a: "WorkingMassRateDataSeries", b: "WorkingMassRateDataSeries") -> "WorkingMassRateDataSeries":
        try:
            ia, ib = match(a.t, b.t)
        except IndexError:
            print(a.user, a.t, b.t)
        if a.user.lower() == "helm":
            print(a.t, b.t)
            print(a.t, b.t)
            print(ia, ib)

        if len(a) != len(b):
            return None
        if len(ia) != len(a) or len(ib) != len(b):
            return None

        t = a.t[ia]
        m = (a.dmdt[ia] + b.dmdt[ib]) / 2.
        e = np.sqrt((np.square(a.errs[ia]) +
                     np.square(b.errs[ib])) / 2.)
        # ar = (a.a[ia] + b.a[ib]) / 2.
        ar = None

        comp = a.computed or b.computed
        aggr = a.aggregated or b.aggregated

        return cls(
            a.user, a.user_group, a.data_group, BasinGroup.sheets,
            a.basin_id, a.basin_area, t, ar, m, e, comp, merged=True, aggregated=aggr
        )

    def __len__(self) -> int:
        return len(self.t)

    def chunk_rates(self):
        return self