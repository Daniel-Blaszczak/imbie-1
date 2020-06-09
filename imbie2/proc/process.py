from collections import OrderedDict
from itertools import product
import os
import shutil
from typing import Union, Sequence
from matplotlib.pyplot import cm
import matplotlib.pyplot as plt
import pandas as pd
import numpy as np

from imbie2.proc.sum_basins import sum_basins
from imbie2.conf import ImbieConfig
from imbie2.const.basins import IceSheet, BasinGroup
from imbie2.const.error_methods import ErrorMethod
from imbie2.model.collections import WorkingMassRateCollection, MassChangeCollection, \
                                     MassRateCollection
from imbie2.plot.plotter import Plotter
from imbie2.plot import style
from imbie2.table.tables import MeanErrorsTable, TimeCoverageTable, BasinsTable, \
                                RegionAveragesTable, RegionGroupAveragesTable
from imbie2.proc.compare_windows import compare_windows
from imbie2.util.count_tolerance import count_tolerance
from imbie2.util.functions import ts2m, match, move_av
from imbie2.util.discharge import calculate_discharge
from imbie2.model.series import WorkingMassRateDataSeries, MassChangeDataSeries

def prepare_collection(collection: Union[MassRateCollection, MassChangeCollection], config: ImbieConfig):
    if isinstance(collection, MassRateCollection):
        # normalise dM/dt data
        return collection.chunk_series()
    elif isinstance(collection, MassChangeCollection):
        return collection.to_dmdt(
                truncate=config.truncate_dmdt, window=config.dmdt_window, method=config.dmdt_method
        )
    else:
        raise TypeError("Expected dM or dM/dt collection")

def process(input_data: Sequence[Union[MassRateCollection, MassChangeCollection]], config: ImbieConfig):

    groups = ["RA", "GMB", "IOM"]
    if config.include_la:
        groups.append("LA")
    for g in config.methods_skip:
        groups.remove(g)

    # find output directory
    output_path = os.path.expanduser(config.output_path)

    # check if it exists, clear it if not empty (or abort)
    if not os.path.exists(output_path):
        os.makedirs(output_path)
    if os.listdir(output_path):
        msg = "WARNING: directory \"%s\" is not empty, contents will be deleted. Proceed? (Y/n): " % output_path
        choice = input(msg)
        if (not choice.lower() == 'y') and choice:
            print("Processor cancelled by user.")
            return
        shutil.rmtree(output_path)

    sheets = [IceSheet.apis, IceSheet.eais, IceSheet.wais, IceSheet.gris]
    regions = OrderedDict([
        (IceSheet.eais, [IceSheet.eais]),
        (IceSheet.apis, [IceSheet.apis]),
        (IceSheet.wais, [IceSheet.wais]),
        (IceSheet.ais, [IceSheet.apis, IceSheet.eais, IceSheet.wais]),
        (IceSheet.gris, [IceSheet.gris]),
        (IceSheet.all, [IceSheet.apis, IceSheet.eais, IceSheet.wais, IceSheet.gris])
    ])
    offset = config.align_date

    rate_data = WorkingMassRateCollection()
    for collection in input_data:
        collection = prepare_collection(collection, config)
        for series in collection:
            # check if there's already a series for this user & location
            existing = rate_data.filter(
                user_group=series.user_group, user=series.user, basin_id=series.basin_id, basin_group=series.basin_group
            )
            if not existing:
                rate_data.add_series(series)

    for series in rate_data.filter(user_group='RA'):
        print(series.user, series.basin_id, series.t.max(), 'dM' if series.computed else 'dM/dt')

    # find users who have provided a full ice sheet of basin data, but no ice sheet series.
    sum_basins(rate_data, sheets)

    # keep copies of zwally/rignot data before merging them
    zwally_data = rate_data.filter(basin_group=BasinGroup.zwally)
    rignot_data = rate_data.filter(basin_group=BasinGroup.rignot)

    # merge zwally/rignot
    rate_data.merge()

    mass_data = rate_data.integrate(offset=offset)

    # create empty collections for storing outputs
    groups_sheets_rate = WorkingMassRateCollection()
    groups_sheets_mass = MassChangeCollection()

    groups_regions_rate = WorkingMassRateCollection()
    groups_regions_mass = MassChangeCollection()

    sheets_rate = WorkingMassRateCollection()
    sheets_mass = MassChangeCollection()

    regions_rate = WorkingMassRateCollection()
    regions_mass = MassChangeCollection()

    for outlier in config.users_mark:
        data = rate_data.filter(user=outlier)
        for series in data:
            for t, dmdt, e in zip(series.t, series.dmdt, series.errs):
                print(outlier, series.basin_id, t, dmdt, e)

    if config.reduce_window is not None:
        assert config.reduce_window > 0
        rate_data = rate_data.reduce(config.reduce_window)

    for group in groups:
        for sheet in sheets:
            print("computing", group, "average for", sheet.value, end="... ")

            new_series = rate_data.filter(
                user_group=group, basin_id=sheet
            ).average(
                mode=config.combine_method,
                error_mode=config.group_avg_errors_method,
                nsigma=config.average_nsigma
            )
            if new_series is None:
                continue

            groups_sheets_rate.add_series(new_series)
            groups_sheets_mass.add_series(
                new_series.integrate(offset=offset)
            )
            print("done.")
        for region, sheets in regions.items():
            print("computing", group, "average for", region.value, end="... ")

            region_rate = groups_sheets_rate.filter(
                user_group=group, basin_id=sheets
            ).sum(error_method=config.sum_errors_method)
            if region_rate is None:
                continue

            region_rate.basin_id = region
            region_mass = region_rate.integrate(offset=offset)

            groups_regions_rate.add_series(region_rate)
            groups_regions_mass.add_series(region_mass)
            print("done.")

    for sheet in sheets:
        print("computing inter-group average for", sheet.value, end="... ")

        sheet_rate_avg = groups_sheets_rate.filter(
            basin_id=sheet
        ).average(
            mode=config.combine_method,
            error_mode=config.sheet_avg_errors_method,
            nsigma=config.average_nsigma
        )
        if sheet_rate_avg is None:
            continue

        sheets_rate.add_series(sheet_rate_avg)
        sheets_mass.add_series(
            sheet_rate_avg.integrate(offset=offset)
        )
        print("done.")

    # compute region figures
    for region, sheets in regions.items():
        print("computing inter-group average for", region.value, end="... ")

        region_rate = sheets_rate.filter(
            basin_id=sheets
        ).sum(error_method=config.sum_errors_method)
        if region_rate is None:
            continue

        region_rate.basin_id = region

        regions_rate.add_series(region_rate)
        regions_mass.add_series(
            region_rate.integrate(offset=offset)
        )
        print("done.")

    # calculate dicharge
    gris_rate = regions_rate.filter(basin_id=IceSheet.gris).first()
    gris_mass = regions_mass.filter(basin_id=IceSheet.gris).first()
    # - read SMB data
    smb_fname = 'smb_anom_1980_2019_v2.csv'
    smb_fpath = os.path.join('/home/mark/imbie', smb_fname) # '/media/mark/isardSAT/imbie'
    smb_data = pd.read_csv(
        smb_fpath,
        names=['year', 'smb', 'err'],
        index_col='year'
    )
    smb_t = smb_data.index.values
    smb_rate = smb_data.smb.values * 12.
    smb_rate_err = smb_data.err.values * 12.

    smb_rate_series = WorkingMassRateDataSeries(
        'SMB', 'SMB', 'SMB', BasinGroup.sheets, IceSheet.gris,
        np.nan, smb_t, np.ones(smb_t.shape) * np.nan, smb_rate,
        smb_rate_err
    ).reduce(1, .45, backfill=True)
    smb_t12, smb_dmdt12 = ts2m(smb_rate_series.t, smb_rate_series.dmdt)
    _, smb_errs12 = ts2m(smb_rate_series.t, smb_rate_series.errs)

    smb_rate_series = WorkingMassRateDataSeries(
        'SMB', 'SMB', 'SMB', BasinGroup.sheets, IceSheet.gris,
        np.nan, smb_t12, np.ones(smb_t.shape) * np.nan, smb_dmdt12,
        smb_errs12
    )
    smb_mass_series = smb_rate_series.integrate()
    smb_mass_smooth = smb_rate_series.smooth(3.083333).integrate()

    groups_discharge_rate = WorkingMassRateCollection()
    mean_discharge_rate = calculate_discharge(gris_rate, smb_rate_series)
    mean_discharge_rate.user_group = 'all'

    for group in groups:
        series = groups_regions_rate.filter(
            basin_id=IceSheet.gris, user_group=group
        ).first()

        print(group, series)

        if series is None:
            continue

        groups_discharge_rate.add_series(
            calculate_discharge(series, smb_rate_series)
        )    
    mouginot_data = pd.read_csv(
        '~/imbie/mouginot_discharge.tsv',
        names=['year', 'discharge', 'error'],
        index_col='year'
    )
    mouginot_t = np.asarray(
        mouginot_data.index.values, dtype=np.float)
    mouginot_mass = np.asarray(
        mouginot_data.discharge.values, dtype=np.float)
    mouginot_errs = np.asarray(
        mouginot_data.error.values, dtype=np.float)

    users_discharge_mass = MassChangeCollection(
        MassChangeDataSeries(
            'Mouginot', 'IOM', 'IOM', BasinGroup.sheets, IceSheet.gris, np.nan,
            mouginot_t, mouginot_t*np.nan, mouginot_mass, mouginot_errs
        )
    )
    mean_discharge_mass = mean_discharge_rate.integrate() # smooth(3.083333)
    groups_discharge_mass = groups_discharge_rate.integrate( # smooth(3.083333)
        align=mean_discharge_mass
    )

    print('group discharge:', len(groups_discharge_rate), len(groups_discharge_mass))



    # SMB + Dynamics

    if config.output_timestep is not None:
        mean_discharge_mass = mean_discharge_rate.reduce(
            config.output_timestep, config.output_offset, backfill=True
        ).integrate().reduce(config.output_timestep, config.output_offset)

        smb_mass_series = smb_rate_series.reduce(
            config.output_timestep, config.output_offset, backfill=True
        ).integrate().reduce(config.output_timestep, config.output_offset)

        gris_mass = gris_rate.reduce(
            config.output_timestep, config.output_offset, backfill=True
        ).integrate().reduce(config.output_timestep, config.output_offset)

        mean_discharge_rate = mean_discharge_rate.reduce(config.output_timestep, config.output_offset)
        smb_rate_series = smb_rate_series.reduce(config.output_timestep, config.output_offset)
        gris_rate = gris_rate.reduce(config.output_timestep, config.output_offset)
        # mean_discharge_mass = mean_discharge_mass.reduce(config.output_timestep, config.output_offset)
        # smb_mass_series = smb_mass_series.reduce(config.output_timestep, config.output_offset)
        # gris_mass = gris_mass.reduce(config.output_timestep, config.output_offset)
    # write CSV data
    dyn_df = pd.DataFrame(
        data={
            'dynamics_dmdt': pd.Series(
                mean_discharge_rate.dmdt, index=mean_discharge_rate.t),
            'dynamics_dmdt_sd': pd.Series(
                mean_discharge_rate.errs, index=mean_discharge_rate.t)
        }
    )
    smb_df = pd.DataFrame(
        data={
            'smb_dmdt': pd.Series(
                smb_rate_series.dmdt, index=smb_rate_series.t),
            'smb_dmdt_sd': pd.Series(
                smb_rate_series.errs, index=smb_rate_series.t)
        }
    )
    imb_df = pd.DataFrame(
        data={
            'imbie_dmdt': pd.Series(
                gris_rate.dmdt, index=gris_rate.t),
            'imbie_dmdt_sd': pd.Series(
                gris_rate.errs, index=gris_rate.t)
        }
    )
    # dyn_df = pd.DataFrame(
    #     data={
    #         'dynamics_dmdt': pd.Series(dyn_rate, index=dyn_t),
    #         'dynamics_dmdt_sd': pd.Series(dyn_rate_err, index=dyn_t)
    #     }
    # )
    # smb_df = pd.DataFrame(
    #     data={
    #         'smb_dmdt': pd.Series(smb_rate, index=smb_t),
    #         'smb_dmdt_sd': pd.Series(smb_rate_err, index=smb_t)
    #     }
    # )
    # imb_df = pd.DataFrame(
    #     data={
    #         'imbie_dmdt': pd.Series(imbie_rate, index=imbie_t),
    #         'imbie_dmdt_sd': pd.Series(imbie_rate_err, index=imbie_t)
    #     }
    # )
    md = dyn_df.reindex(smb_df.index, method='nearest', tolerance=1./24)
    mi = imb_df.reindex(smb_df.index, method='nearest', tolerance=1./24)
    
    df = smb_df.merge(mi, left_index=True, right_index=True).merge(md, left_index=True, right_index=True)
    df.to_csv(os.path.join(output_path, 'imbie_smb_dynamics_dmdt.csv'))
    # write CSV dM
    # dyn_df = pd.DataFrame(
    #     data={
    #         'dyn_dm': pd.Series(dyn_mass, index=dyn_t),
    #         'dyn_dm_sd': pd.Series(dyn_mass_err, index=dyn_t)
    #     }
    # )
    # smb_df = pd.DataFrame(
    #     data={
    #         'smb_dm': pd.Series(smb_mass, index=smb_t),
    #         'smb_dm_sd': pd.Series(smb_mass_err, index=smb_t)
    #     }
    # )
    # imb_df = pd.DataFrame(
    #     data={
    #         'imbie_dm': pd.Series(gris_mass.mass, index=gris_mass.t),
    #         'imbie_dm_sd': pd.Series(gris_mass.errs, index=gris_mass.t)
    #     }
    # )
        

    dyn_df = pd.DataFrame(
        data={
            'dyn_dm': pd.Series(
                mean_discharge_mass.mass, index=mean_discharge_mass.t),
            'dyn_dm_sd': pd.Series(
                mean_discharge_mass.errs, index=mean_discharge_mass.t)
        }
    )
    smb_df = pd.DataFrame(
        data={
            'smb_dm': pd.Series(
                smb_mass_series.mass, index=smb_mass_series.t),
            'smb_dm_sd': pd.Series(
                smb_mass_series.errs, index=smb_mass_series.t)
        }
    )
    imb_df = pd.DataFrame(
        data={
            'imbie_dm': pd.Series(
                gris_mass.mass, index=gris_mass.t),
            'imbie_dm_sd': pd.Series(
                gris_mass.errs, index=gris_mass.t)
        }
    )
    md = dyn_df.reindex(smb_df.index, method='nearest', tolerance=1./24)
    mi = imb_df.reindex(smb_df.index, method='nearest', tolerance=1./24)
    
    df = smb_df.merge(mi, left_index=True, right_index=True).merge(md, left_index=True, right_index=True)
    df.to_csv(os.path.join(output_path, 'accumulated_dm.csv'))

    windows = [
        (1992, 1997), (1997, 2002), (2002, 2007), (2007, 2012), (2012, 2017), (2005, 2015), (1992, 2011), (1992, 2018)
    ]
    imb_tab = []
    smb_tab = []
    dyn_tab = []
    headers = []

    for w0, w1 in windows:
        smb_w = smb_rate_series.truncate(w0, w1, interp=False)
        dyn_w = mean_discharge_rate.truncate(w0, w1, interp=False)
        imb_w = gris_rate.truncate(w0, w1, interp=False)

        # smb_w = np.logical_and(smb_t >= w0, smb_t <= w1)
        # dyn_w = np.logical_and(dyn_t >= w0, dyn_t <= w1)
        # imb_w = np.logical_and(
        #     imbie_t >= w0, imbie_t < w1 + 1
        # )
        # mean_smb_w = np.nanmean(smb_rate[smb_w])
        # sdev_smb_w = np.sqrt(np.sum((smb_rate_err[smb_w]) ** 2)) / (w1 - w0)
        # # sdev_smb_w = np.nanstd(smb_rate_err[smb_w] * 12) / (w1 - w0)
        # # sdev_smb_w = np.sqrt(np.nanmean((smb_rate_err[smb_w]) ** 2)) / (w1 - w0)
        # # sdev_smb_w = np.sum(smb_rate_err[smb_w]) / (w1 - w0) # ** 2

        # mean_dyn_w = np.nanmean(dyn_rate[dyn_w])
        # sdev_dyn_w = np.sqrt(np.sum((dyn_rate_err[dyn_w]) ** 2)) / (w1 - w0)
        # # sdev_dyn_w = np.sqrt(np.nanmean((dyn_rate_err[dyn_w]) ** 2)) / (w1 - w0)
        # # sdev_dyn_w = np.sum(dyn_rate_err[dyn_w]) / (w1 - w0) # ** 2

        # mean_imb_w = np.nanmean(imbie_rate[imb_w])
        # sdev_imb_w = np.sqrt(np.sum((imbie_rate_err[imb_w]) ** 2)) / (w1 - w0)
        # # sdev_imb_w = np.sqrt(np.nanmean((imbie_rate_err[imb_w]) ** 2)) / (w1 - w0)
        # # sdev_imb_w = np.sum(imbie_rate_err[imb_w]) / (w1 - w0) # ** 2

        smb_tab.append('%.1f\u00B1%.1f' % (smb_w.mean, smb_w.sigma))
        dyn_tab.append('%.1f\u00B1%.1f' % (dyn_w.mean, dyn_w.sigma))
        imb_tab.append('%.1f\u00B1%.1f' % (imb_w.mean, imb_w.sigma))
        headers.append('%i-%i' % (w0, w1))

    fpath = os.path.join(output_path, 'smb_dynamics_table.csv')
    with open(fpath, 'w') as f:
        line = ','.join([''] + headers)
        f.write(line+'\n')
        line = ','.join(['Total'] + imb_tab)
        f.write(line+'\n')
        line = ','.join(['SMB'] + smb_tab)
        f.write(line+'\n')
        line = ','.join(['Dynamics'] + dyn_tab)
        f.write(line+'\n')
    
    print('     ', *headers, sep='\t')
    print('Total', *imb_tab, sep='\t')
    print('SMB  ', *smb_tab, sep='\t')
    print('Dynam', *dyn_tab, sep='\t')


    t_start = int(
        sheets_rate.filter(basin_id=IceSheet.gris).min_time()
    )
    t_final = int(
        sheets_rate.filter(basin_id=IceSheet.gris).max_time()
    )
    for group in (*groups, 'ALL'):
        year = pd.Series(np.arange(t_start, t_final), name='year')
        mean = pd.Series(np.zeros(year.size)*np.nan, name='mean', index=year)
        stdev = pd.Series(np.zeros(year.size)*np.nan, name='stdev', index=year)
        min_ = pd.Series(np.zeros(year.size)*np.nan, name='min', index=year)
        max_ = pd.Series(np.zeros(year.size)*np.nan, name='max', index=year)
        count = pd.Series(np.zeros(year.size, dtype=np.int), name='contributions', index=year)

        if group == 'ALL':
            group_ind = rate_data.filter(basin_id=IceSheet.gris)
        else:
            group_ind = rate_data.filter(
                basin_id=IceSheet.gris,
                user_group=group
            )

        for y0 in year:
            y1 = y0 + 1

            group_ind_year = group_ind.get_window(y0, y1)
            group_ind_year_avgs = np.array(
                [s.mean for s in group_ind_year if s.min_time < y1 and s.max_time > y0]
            )

            mean[y0] = np.nanmean(group_ind_year_avgs) if group_ind_year_avgs.size else np.nan
            min_[y0] = np.nanmin(group_ind_year_avgs) if group_ind_year_avgs.size else np.nan
            max_[y0] = np.nanmax(group_ind_year_avgs) if group_ind_year_avgs.size else np.nan
            stdev[y0] = np.nanstd(group_ind_year_avgs) if group_ind_year_avgs.size else np.nan
            count[y0] = group_ind_year_avgs.size
        
        fname = os.path.join(output_path, '%s_annual_stats.csv' % group)
        df = pd.DataFrame(
            {mean.name: mean,
             stdev.name: stdev,
             min_.name: min_,
             max_.name: max_,
             count.name: count}
        )
        print(group)
        print(df)
        # plt.title(group)
        # plt.errorbar(year, mean, yerr=stdev, color='b', ls='-')
        # plt.plot(min_, 'b--')
        # plt.plot(max_, 'b--')
        # plt.legend()
        # plt.show()

        df.to_csv(fname)

    c0, c1 = groups_sheets_rate.filter(
        basin_id=IceSheet.gris
    ).common_period()

    cdata = sheets_rate.filter(
        basin_id=IceSheet.gris
    ).first().truncate(c0, c1)

    print('greenland xgroup common:', c0, c1)
    print('greenland xgroup stdev range:', cdata.errs.min(), cdata.errs.max())

    for group in groups:
        group_data = rate_data.filter(
            user_group=group,
            basin_id=IceSheet.gris
        )
        t0, t1 = group_data.common_period()
        print('Greenland/%s common period:' % group, t0, '-', t1)

        group_data_global_common = group_data.get_window(c0, c1)
        print('Greenland/%s stdev in x-group common:' % group, group_data_global_common.stdev())

        series = groups_sheets_rate.filter(
            user_group=group,
            basin_id=IceSheet.gris
        ).first()

        series_common = series.truncate(t0, t1)
        print('Greenland/%s common range:' % group, series_common.min_rate, series_common.max_rate)
        print('Greenland/%s common stdev:' % group, np.nanstd(series_common.dmdt))

        if group == 'RA':
            la_data = group_data.filter(
                user=names_la
            )
            tt0, tt1 = la_data.common_period()
            la_common = la_data.get_window(tt0, tt1)
            print('LA common period:', tt0, tt1)
            print('LA common range:', la_common.min_rate(), la_common.max_rate())

        if t0 is None or t1 is None:
            continue

        others = [g for g in groups if g != group]

        xwindow = groups_sheets_rate.filter(
            basin_id=IceSheet.gris,
            user_group=others
        ).get_window(t0, t1)
        intergroup = sheets_rate.filter(
            basin_id=IceSheet.gris
        ).first().truncate(t0, t1)

        print('groups %.2f-%.2f range:' % (t0, t1), xwindow.min_rate(), xwindow.max_rate())
        print('xgroup %.2f-%.2f range:' % (t0, t1), intergroup.min_rate, intergroup.max_rate)
    
    # TODO: RESTORE THIS
    # supl_data = pd.read_csv(
    #     '~/Downloads/ais_data_out.txt',
    #     header=None,
    #     names=["year_ais",
    #         "dm_eais",
    #         "dm_eais_sd",
    #         "dm_wais",
    #         "dm_wais_sd",
    #         "dm_apis",
    #         "dm_apis_sd",
    #         "dm_ais",
    #         "dm_ais_sd"],
    #     delim_whitespace=True
    # )

    # EXT_PLOT_DEBUG = False
    # if EXT_PLOT_DEBUG:
    #     _, axs = plt.subplots(2, 2, sharex=True, sharey=True)
    #     plot_sheets = [IceSheet.apis, IceSheet.eais, IceSheet.wais, IceSheet.ais]

    #     for sheet in ais_sheets:
    #         for series in rate_data.filter(basin_id=sheet):
    #             extent = "{:.2f}-{:.2f}".format(series.min_time, series.max_time)
    #             print(series.basin_id.value, extent, series.user_group, series.user)
    #     sheets_sd = {}
    #     sheets_dm = {}
    #     for ax, sheet in zip(np.ravel(axs), plot_sheets):

    #         year_sheet = supl_data.year_ais
    #         dm_sheet = getattr(supl_data, 'dm_%s' % sheet.value)
    #         dm_sheet_sd = getattr(supl_data, 'dm_%s_sd' % sheet.value)

    #         ax.fill_between(
    #             year_sheet,
    #             dm_sheet-dm_sheet_sd,
    #             dm_sheet+dm_sheet_sd,
    #             color='g', alpha=.5
    #         )
    #         ax.plot(
    #             year_sheet,
    #             dm_sheet,
    #             'g-', label='Andy'
    #         )
    #         data = regions_mass.filter(basin_id=sheet).first()

    #         # for series in rate_data.filter(basin_id=sheet):
    #         # #     ax.plot(series.t, series.dmdt, '-m')
    #         #     ax.text(series.t[0], series.dmdt[0],
    #         #             "{} ({})".format(series.user, series.user_group),
    #         #             ha='right', va='top')
    #         #     ax.text(series.t[-1], series.dmdt[-1],
    #         #             "{} ({})".format(series.user, series.user_group))
    #         # ax.errorbar(
    #         #     data.t, data.mass, yerr=data.errs, color='grey', label='IMBIE'
    #         # )
    #         # n = data.t - data.t[0]; n[0] = 1.
    #         # corr_errs = data.errs/np.sqrt(n)
    #         # corr_errs[0] = data.errs[0]

    #         sheets_sd[sheet] = data.errs #corr_errs
    #         sheets_dm[sheet] = data.mass
    #         # corr_errs = data.errs/np.sqrt((np.arange(len(data.t))+1)/12.)
    #         ax.errorbar(
    #             data.t, data.mass, yerr=data.errs,
    #             label=r'$\frac{1}{\sqrt{N}}$', color='b'
    #         )
    #         avg_diff = np.sqrt(np.nanmean(np.square(data.mass-dm_sheet)))
    #         err_diff = np.sqrt(np.nanmean(np.square(data.errs-dm_sheet_sd)))

    #         info = "RMS Avg: {:.3f} $Gt$\nRMS Err: {:.3f} $Gt$".format(avg_diff, err_diff)
    #         ax.text(0.1, 0.1, info, transform=ax.transAxes)

    #         ok = np.isclose(data.mass, dm_sheet)
    #         ax.scatter(data.t[ok], data.mass[ok], label='exact d$M$ matches')

    #         # last = np.argwhere(ok).max()
    #         # ax.text(data.t[last], data.mass[last]+data.errs[last], "{:.2f}".format(data.t[last]), ha='center')

    #         # if sheet == IceSheet.ais:
    #         #     sum_dm = sheets_dm[IceSheet.apis]+sheets_dm[IceSheet.wais]+sheets_dm[IceSheet.eais]
    #         #     sum_sd = np.sqrt(sheets_sd[IceSheet.apis]**2.+sheets_sd[IceSheet.wais]**2.+sheets_sd[IceSheet.eais]**2.)

    #         #     ax.plot(data.t, sum_dm+sum_sd, '-r')
    #         #     ax.plot(data.t, sum_dm-sum_sd, '-r')
    #         #     ax.plot(data.t, sum_dm, '--r', label='Reconstructed')

    #         # ax2 = ax.twinx()
    #         # ax.plot(data.t, np.abs(data.mass-dm_sheet), '--r', label='data RMS')
    #         # ax.plot(data.t, np.abs(corr_errs-dm_sheet_sd), '-r', label='error RMS')
    #         ax.axhline(0, color='k')

    #         # ax2.legend(loc='upper right', frameon=False)
    #         # ax2.set_ylabel(r'd$M$ error (Gt)', color='r')
    #         ax.legend(loc='upper left', frameon=False)

    #         ax.set_ylabel(r'd$M$ (Gt)')
    #         ax.set_xlabel(r'$t$ (year)')
    #         ax.set_title(sheet.value.upper())

    #         df = pd.DataFrame({'dm': data.mass, 'dm_sd': data.errs}, index=data.t)
    #         filename = os.path.join(output_path, "test_{}.csv".format(sheet.value))
    #         df.to_csv(filename, index_label='year')
    #     plt.show()

    #     sys.exit(0)

    # print tables
    output_path = os.path.expanduser(config.output_path)

    met = MeanErrorsTable(rate_data, style=config.table_format)
    filename = os.path.join(output_path, "mean_errors."+met.default_extension())

    print("writing table:", filename)
    met.write(filename)

    btz = BasinsTable(zwally_data, BasinGroup.zwally, style=config.table_format)
    filename = os.path.join(output_path, "zwally_basins."+btz.default_extension())

    print("writing table:", filename)
    btz.write(filename)

    btr = BasinsTable(rignot_data, BasinGroup.rignot, style=config.table_format)
    filename = os.path.join(output_path, "rignot_basins." + btr.default_extension())

    print("writing table:", filename)
    btr.write(filename)

    rat = RegionAveragesTable(
        regions_rate, list(regions.keys()),
        (1992, 2011), (1992, 2000), (1993, 2003), (2000, 2011), (2005, 2010),
        (2010, 2017), (1992, 2017), (1992, 1997), (1997, 2002), (2002, 2007),
        (2007, 2012), (2012, 2017), (2005, 2015),
        style=config.table_format
    )
    filename = os.path.join(output_path, "region_window_averages." + rat.default_extension())

    for region in regions:
        series = regions_rate.filter(basin_id=region).first()
        print(region.value,
              "{:.1f}-{:.1f}".format(series.min_time, series.max_time),
              "({:.1f})".format(series.max_time-series.min_time))

    print("writing table:", filename)
    rat.write(filename)

    rat = RegionAveragesTable(
        regions_rate, [IceSheet.eais, IceSheet.wais, IceSheet.apis, IceSheet.ais],
        (1992, 2011), (1992, 2000), (1993, 2003), (2000, 2011), (2005, 2010),
        (2010, 2017), (1992, 2017), (1992, 1997), (1997, 2002), (2002, 2007),
        (2007, 2012), (2012, 2017), (2005, 2015),
        style=config.table_format
    )
    filename = os.path.join(output_path, "region_window_averages_ais." + rat.default_extension())

    print("writing table:", filename)
    rat.write(filename)

    rgt = RegionGroupAveragesTable(
        groups_regions_rate.window_cropped(), regions_rate.window_cropped(),
        list(regions.keys()), config.bar_plot_min_time, config.bar_plot_max_time, groups,
        style=config.table_format
    )
    filename = os.path.join(output_path, "region_group_window_averages."+rgt.default_extension())

    print("writing table:", filename)
    rgt.write(filename)

    rgt = RegionGroupAveragesTable(
        groups_regions_rate.window_cropped(), regions_rate.window_cropped(),
        [IceSheet.eais, IceSheet.wais, IceSheet.apis, IceSheet.ais],
        config.bar_plot_min_time, config.bar_plot_max_time, groups,
        style=config.table_format
    )
    filename = os.path.join(output_path, "region_group_window_averages_ais."+rgt.default_extension())

    print("writing table:", filename)
    rgt.write(filename)

    for group in groups:
        tct = TimeCoverageTable(rate_data.filter(user_group=group), style=config.table_format)
        filename = os.path.join(
            output_path, "time_coverage_" + group + "." + tct.default_extension()
        )

        print("writing table:", filename)
        tct.write(filename)

    # draw plots
    plotter = Plotter(
        filetype=config.plot_format,
        path=output_path,
        limits=True
    )
    if len(input_data) == 2:
        from functools import partial
        prepare = partial(prepare_collection, config=config)
        data_a, data_b = map(prepare, input_data)
        for sheet in sheets:
            for group in groups:
                data_a_sel = data_a.filter(user_group=group, basin_id=sheet, user='Shepherd').window_cropped()
                data_b_sel = data_b.filter(user_group=group, basin_id=sheet, user='Shepherd').window_cropped()

                name = "%s_%s" % (group, sheet.value)
                plotter.named_dmdt_comparison_plot(data_a_sel, data_b_sel, name)

    # rignot/zwally comparison
    for sheet in sheets:
        plotter.rignot_zwally_comparison(
            rignot_data+zwally_data, [sheet]
        )
    # error bars (IMBIE1 style plot)
    window = config.bar_plot_min_time, config.bar_plot_max_time
    plotter.sheets_error_bars(
        groups_regions_rate.window_cropped(), regions_rate, groups, regions, window=window
    )
    plotter.sheets_error_bars(
        groups_regions_rate.window_cropped(), regions_rate, groups, regions,
        window=window, ylabels=True, suffix="labeled"
    )

    align_dm = offset is None
    # intracomparisons
    for group in groups:
        plotter.group_rate_boxes(
            rate_data.filter(user_group=group), {s: s for s in sheets}, suffix=group
        )
        plotter.group_rate_intracomparison(
            groups_regions_rate.filter(user_group=group).window_cropped().smooth(config.plot_smooth_window),
            rate_data.filter(user_group=group).window_cropped().smooth(config.plot_smooth_window),
            regions, suffix=group, mark=config.users_mark
        )
        plotter.group_mass_intracomparison(
            groups_regions_mass.filter(user_group=group),
            mass_data.filter(user_group=group), regions, suffix=group,
            mark=config.users_mark, align=align_dm
        )
        for sheet in sheets:
            plotter.named_dmdt_group_plot(
                sheet, group, rate_data.filter(user_group=group, basin_id=sheet).window_cropped(),
                groups_regions_rate.filter(user_group=group, basin_id=sheet).window_cropped().first()
            )
            plotter.named_dm_group_plot(
                sheet, group, mass_data.filter(user_group=group, basin_id=sheet),
                basis=groups_regions_mass.filter(user_group=group, basin_id=sheet).first()
            )
    # intercomparisons
    for _id, region in regions.items():
        reg = {_id: region}

        plotter.groups_rate_intercomparison(
            regions_rate.window_cropped().smooth(config.plot_smooth_window),
            groups_regions_rate.smooth(config.plot_smooth_window), reg
        )
        plotter.groups_mass_intercomparison(
            regions_mass, groups_regions_mass, reg, align=align_dm
        )
    # region comparisons
    ais_regions = [IceSheet.eais, IceSheet.wais, IceSheet.apis]
    all_regions = [IceSheet.ais, IceSheet.gris, IceSheet.all]

    plotter.regions_mass_intercomparison(
        regions_mass, *sheets
    )
    plotter.regions_mass_intercomparison(
        regions_mass, *ais_regions
    )
    plotter.regions_mass_intercomparison(
        regions_mass, *all_regions
    )

    if not config.export_data:
        return

    # write data to files
    for region in regions:
        data = regions_rate.filter(basin_id=region).first()
        fname = os.path.join(output_path, region.value+".csv")

        print("exporting data:", fname, end="... ")
        with open(fname, 'w') as f:
            for line in zip(data.t, data.dmdt, data.errs):
                line = ", ".join(map(str, line)) + "\n"
                f.write(line)
        print("done.")
